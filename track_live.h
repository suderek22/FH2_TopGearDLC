#pragma once
#include "track_ownership.h"
#include "track_pose.h"

namespace TrackLive {
constexpr uintptr_t CarVtable = 0x2c5cb80;
constexpr uintptr_t PhysicsVtable = 0x2eb5e18;
constexpr uintptr_t TrackManager = 0x365ad70;
constexpr uintptr_t MicroSaveVtable = 0x2caa7e8;
constexpr uintptr_t NullJobControlVtable = 0x2c60c68;
constexpr double ReturnPositionTolerance = 0.25;
constexpr double ReturnFacingDistance = 0.01;

struct MapState {
    uintptr_t manager = 0;
    int current = -1;
    int target = -1;
    uint32_t pendingJob = 0;
    bool Settled() const { return manager && current > 0 && current == target && !pendingJob; }
};
struct CarState {
    uintptr_t car = 0;
    uintptr_t physics = 0;
    TrackPose::Pose pose = {};
};
struct MicroSaveState {
    uintptr_t module = 0;
    uintptr_t job = 0;
    uintptr_t control = 0;
    bool enabled = false;
    bool nullJobControl = false;
    bool Idle() const { return module && !job && (!control || nullJobControl); }
};
inline bool CaptureMicroSave(uintptr_t base, uintptr_t module, MicroSaveState& output,
                             std::string& error) {
    MicroSaveState state;
    state.module = module;
    uintptr_t table = 0, after = 0;
    uintptr_t job[2] = {}, confirm[2] = {};
    unsigned char enabled = 0, confirmEnabled = 0;
    if (!base || !module || !TrackOwnership::Read(module, &table, sizeof table) ||
        table != base + MicroSaveVtable ||
        !TrackOwnership::Read(module + 0xb0, &enabled, sizeof enabled) || enabled > 1 ||
        !TrackOwnership::Read(module + 0x100, job, sizeof job) ||
        !TrackOwnership::Read(module + 0x100, confirm, sizeof confirm) ||
        memcmp(job, confirm, sizeof job) ||
        !TrackOwnership::Read(module + 0xb0, &confirmEnabled, sizeof confirmEnabled) ||
        confirmEnabled != enabled ||
        !TrackOwnership::Read(module, &after, sizeof after) || after != table) {
        error = "native microsave owner/job layout is unavailable, unsupported or changed";
        return false;
    }
    state.job = job[0];
    state.control = job[1];
    state.enabled = enabled != 0;
    if (state.job && !state.control) {
        error = "native microsave job has no shared owner";
        return false;
    }
    if (!state.job && state.control) {
        uintptr_t ownerTable = 0, owned = 0;
        LONG strong = 0;
        if (!TrackOwnership::Read(state.control, &ownerTable, sizeof ownerTable) ||
            ownerTable != base + NullJobControlVtable ||
            !TrackOwnership::Read(state.control + 8, &strong, sizeof strong) || strong <= 0 ||
            !TrackOwnership::Read(state.control + 0x10, &owned, sizeof owned) || owned ||
            !TrackOwnership::Read(module + 0x100, confirm, sizeof confirm) ||
            memcmp(job, confirm, sizeof job)) {
            error = "native null microsave job control is unsupported, invalid or changed";
            return false;
        }
        state.nullJobControl = true;
    }
    output = state;
    return true;
}
inline bool CaptureMap(uintptr_t base, MapState& output, std::string& error) {
    MapState state;
    int fields[4] = {};
    uintptr_t after = 0;
    if (!base || !TrackOwnership::Pointer(base + TrackManager, state.manager) ||
        !TrackOwnership::Read(state.manager + 8, fields, sizeof fields) ||
        !TrackOwnership::Pointer(base + TrackManager, after) || after != state.manager) {
        error = "native track manager is unavailable or changed during observation";
        return false;
    }
    state.current = fields[0];
    state.target = fields[1];
    memcpy(&state.pendingJob, &fields[3], sizeof state.pendingJob);
    if (state.current <= 0 || state.target <= 0) {
        error = "native track manager has invalid current/target IDs";
        return false;
    }
    output = state;
    return true;
}
// Read-only timeout evidence: TrackSwapManager state (+0x14) and the ForzaSimulation load phase
// (+0x2c8) plus its display-readiness gates checked by the simulation load step (0xfa17d0).
inline std::string DescribeLoad(uintptr_t base) {
    uintptr_t manager = 0, simulation = 0;
    int fields[4] = {}, phase = 0;
    unsigned char gates[0x14] = {};
    if (!base || !TrackOwnership::Pointer(base + TrackManager, manager) ||
        !TrackOwnership::Read(manager + 8, fields, sizeof fields))
        return "track manager unavailable";
    std::string text = "manager current=" + std::to_string(fields[0]) + " target=" +
        std::to_string(fields[1]) + " state=" + std::to_string(fields[3]);
    if (!TrackOwnership::Pointer(manager + 0x18, simulation) ||
        !TrackOwnership::Read(simulation + 0x2c8, &phase, sizeof phase) ||
        !TrackOwnership::Read(simulation + 0x3c2, gates, sizeof gates))
        return text + " simulation unavailable";
    return text + " simulation phase=" + std::to_string(phase) +
        " loaded=" + std::to_string(gates[1]) + std::to_string(gates[2]) + std::to_string(gates[3]) +
        " track_display=" + std::to_string(gates[4]) + " cars_display=" + std::to_string(gates[0x13]);
}
inline bool CaptureCar(uintptr_t base, uintptr_t car, CarState& output, std::string& error) {
    CarState state;
    uintptr_t table = 0, positionGetter = 0, facingGetter = 0, after = 0;
    state.car = car;
    if (!base || !car || !TrackOwnership::Read(car, &table, sizeof table) ||
        table != base + CarVtable || !TrackOwnership::Pointer(car + 0x4c08, state.physics) ||
        state.physics < 0x390 || !TrackOwnership::Read(state.physics, &table, sizeof table) ||
        table != base + PhysicsVtable ||
        !TrackOwnership::Read(table + 0x270, &positionGetter, sizeof positionGetter) ||
        !TrackOwnership::Read(table + 0x970, &facingGetter, sizeof facingGetter) ||
        positionGetter != base + 0x12cd150 || facingGetter != base + 0x12cbed0) {
        error = "native player car/physics layout or pose getters are unsupported";
        return false;
    }
    const unsigned char positionBytes[] = {0xc5,0xf8,0x28,0x81,0x70,0xfc,0xff,0xff};
    const unsigned char facingBytes[] = {0x49,0x63,0xc0,0x48,0x83,0xe8,0x38,0x48,0x03,0xc0};
    unsigned char actual[sizeof facingBytes] = {};
    if (!TrackOwnership::Read(positionGetter, actual, sizeof positionBytes) ||
        memcmp(actual, positionBytes, sizeof positionBytes) ||
        !TrackOwnership::Read(facingGetter, actual, sizeof facingBytes) ||
        memcmp(actual, facingBytes, sizeof facingBytes) ||
        !TrackOwnership::Read(state.physics - 0x390, state.pose.position, sizeof state.pose.position) ||
        !TrackOwnership::Read(state.physics - 0x360, state.pose.facing, sizeof state.pose.facing) ||
        !TrackOwnership::Read(car + 0x4c08, &after, sizeof after) || after != state.physics) {
        error = "native live pose cannot be read consistently or getter signatures changed";
        return false;
    }
    if (!TrackPose::Valid(state.pose)) {
        error = "native live pose has invalid finite/unit coordinates; position=(" +
            std::to_string(state.pose.position[0]) + "," + std::to_string(state.pose.position[1]) + "," +
            std::to_string(state.pose.position[2]) + "," + std::to_string(state.pose.position[3]) +
            "); facing=(" + std::to_string(state.pose.facing[0]) + "," +
            std::to_string(state.pose.facing[1]) + "," + std::to_string(state.pose.facing[2]) + "," +
            std::to_string(state.pose.facing[3]) + ")";
        return false;
    }
    output = state;
    return true;
}
inline bool VerifyReturn(const CarState& departure, const CarState& current,
                         const MapState& map, std::string& error) {
    if (!departure.car || !departure.physics || !current.car || !current.physics ||
        !TrackPose::Valid(departure.pose) || !TrackPose::Valid(current.pose) ||
        !map.Settled() || map.current != 542) {
        error = "native mainland map and live return pose are not valid/settled";
        return false;
    }
    double positionDistance = 0, facingDistance = 0;
    for (int i = 0; i < 3; ++i) {
        double position = double(current.pose.position[i]) - departure.pose.position[i];
        double facing = double(current.pose.facing[i]) - departure.pose.facing[i];
        positionDistance += position * position;
        facingDistance += facing * facing;
    }
    if (positionDistance > ReturnPositionTolerance * ReturnPositionTolerance ||
        facingDistance > ReturnFacingDistance * ReturnFacingDistance) {
        error = "native mainland car has not restored the captured departure position/facing";
        return false;
    }
    return true;
}
}
