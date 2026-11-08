#pragma once
#include "track_live.h"

namespace TrackPlayer {
constexpr uintptr_t PlayerVtable = 0x2c62bf0;
constexpr uintptr_t ProfileVtable = 0x2c5f870;
inline void ReleaseIntrusive(void* object) {
    auto table = *reinterpret_cast<void(***)(void*)>(object);
    table[3](object);
}

struct Functions {
    void* (*context)(void*);
    TrackNative::Shared* (*entitySystem)(void*, TrackNative::Shared*);
    TrackNative::Shared* (*globalGroup)(void*, TrackNative::Shared*, const void*);
    void* (*primary)(void*, void**);
    void* (*resolveCars)(void*, void*);
    void* (*playerCar)(void*, void**);
    void (*releasePlayer)(void*);
    void (*releaseCar)(void*);
};
struct GlobalName {
    char text[16] = "global";
    size_t length = 6;
    size_t capacity = 15;
};
static_assert(sizeof(GlobalName) == 32, "Native global group name ABI");

class Car {
    void* object = nullptr;
    void (*release)(void*) = nullptr;
public:
    ~Car() { Reset(); }
    void Reset() {
        void* old = object;
        object = nullptr;
        if (old) release(old);
    }
    void Adopt(void* value, void (*drop)(void*)) {
        Reset();
        object = value;
        release = drop;
    }
    void* Detach() {
        void* value = object;
        object = nullptr;
        return value;
    }
    uintptr_t Address() const { return reinterpret_cast<uintptr_t>(object); }
    Car() = default;
    Car(const Car&) = delete;
    Car& operator=(const Car&) = delete;
};

class Owners {
    uintptr_t base = 0;
    Functions functions = {};
    void* player = nullptr;
    uintptr_t profile = 0;
    TrackOwnership::Binding microSave;

    bool Global(TrackNative::Shared& group, std::string& error) const {
        void* context = functions.context(nullptr);
        if (!context) {
            error = "native global group lookup context unavailable";
            return false;
        }
        TrackNative::Shared system;
        functions.entitySystem(context, &system);
        if (!system.object || !system.control) {
            error = "native global group lookup entity system unavailable";
            return false;
        }
        GlobalName name;
        functions.globalGroup(system.object, &group, &name);
        if (!group.object || !group.control) {
            error = "native named global group is unavailable";
            return false;
        }
        return true;
    }
    bool Profile(uintptr_t owner, uintptr_t& value, std::string& error) const {
        uintptr_t table = 0, getter = 0, after = 0, confirm = 0;
        if (!TrackOwnership::Read(owner, &table, sizeof table) || table != base + PlayerVtable ||
            !TrackOwnership::Read(table + 0xc8, &getter, sizeof getter) || getter != base + 0x324060 ||
            !TrackOwnership::Pointer(owner + 0x328, value) ||
            !TrackOwnership::Read(value, &table, sizeof table) || table != base + ProfileVtable ||
            !TrackOwnership::Read(owner + 0x328, &confirm, sizeof confirm) || confirm != value ||
            !TrackOwnership::Read(owner, &after, sizeof after) || after != base + PlayerVtable) {
            error = "native primary player/profile ownership is unavailable or changed";
            return false;
        }
        return true;
    }
public:
    ~Owners() { Reset(); }
    bool Initialize(uintptr_t image, std::string& error) {
        const struct { uintptr_t rva; unsigned char bytes[8]; size_t size; } checks[] = {
            {0x19242b0, {0x8b,0x0d,0x22,0xa9,0xc2,0x01}, 6},
            {0x867dc0, {0x40,0x53,0x48,0x83,0xec,0x30}, 6},
            {0x19065e0, {0x48,0x89,0x5c,0x24,0x10,0x48}, 6},
            {0x2da000, {0x48,0x89,0x5c,0x24,0x08,0x57}, 6},
            {0x21e0c0, {0x48,0x8b,0x49,0x48,0x48,0x85,0xc9}, 7},
            {0x2e1520, {0x48,0x8b,0x81,0x98,0x00,0x00,0x00,0xc3}, 8},
            {0x2da060, {0x48,0x89,0x5c,0x24,0x08,0x48}, 6},
            {0x324060, {0x48,0x8b,0x81,0x28,0x03,0x00,0x00,0xc3}, 8},
            {0x322430, {0x48,0x89,0x5c,0x24,0x10,0x57}, 6},
            {0x2904d0, {0x48,0x89,0x5c,0x24,0x10,0x57}, 6},
        };
        for (const auto& check : checks) {
            unsigned char actual[8] = {};
            if (!image || !TrackOwnership::Read(image + check.rva, actual, check.size) ||
                memcmp(actual, check.bytes, check.size)) {
                error = "unsupported native player owner signature at RVA " + std::to_string(check.rva);
                return false;
            }
        }
        uintptr_t dropCar = 0;
        if (!TrackOwnership::Pointer(image + TrackLive::CarVtable + 0x18, dropCar) ||
            dropCar != image + 0x2904d0) {
            error = "native player car release slot is unavailable";
            return false;
        }
        return Initialize(image, {
            reinterpret_cast<decltype(functions.context)>(image + 0x19242b0),
            reinterpret_cast<decltype(functions.entitySystem)>(image + 0x867dc0),
            reinterpret_cast<decltype(functions.globalGroup)>(image + 0x19065e0),
            reinterpret_cast<decltype(functions.primary)>(image + 0x2da000),
            reinterpret_cast<decltype(functions.resolveCars)>(image + 0x21e0c0),
            reinterpret_cast<decltype(functions.playerCar)>(image + 0x2da060),
            ReleaseIntrusive,
            ReleaseIntrusive,
        }, error);
    }
    bool Initialize(uintptr_t image, const Functions& native, std::string& error) {
        if (base || !image || !native.context || !native.entitySystem || !native.globalGroup || !native.primary ||
            !native.resolveCars || !native.playerCar || !native.releasePlayer || !native.releaseCar) {
            error = "native player owner callbacks unavailable or already initialized";
            return false;
        }
        base = image;
        functions = native;
        return true;
    }
    bool Capture(std::string& error) {
        if (!base || player || microSave.Component()) {
            error = "native player owners unavailable or already captured";
            return false;
        }
        TrackNative::Shared group;
        if (!Global(group, error)) return false;
        if (!microSave.CaptureType(&group, base, TrackLive::MicroSaveVtable, error)) return false;
        functions.primary(nullptr, &player);
        if (!player || !Profile(reinterpret_cast<uintptr_t>(player), profile, error)) {
            if (!player) error = "native primary player is unavailable";
            Reset();
            return false;
        }
        return true;
    }
    bool Validate(std::string& error) const {
        if (!base || !player || !microSave.Component() || !profile) {
            error = "native player owners have not been captured";
            return false;
        }
        Car primary;
        void* current = nullptr;
        functions.primary(nullptr, &current);
        primary.Adopt(current, functions.releasePlayer);
        uintptr_t currentProfile = 0;
        if (current != player || !Profile(reinterpret_cast<uintptr_t>(player), currentProfile, error) ||
            currentProfile != profile) {
            error = "native primary player or borrowed profile was replaced";
            return false;
        }
        TrackNative::Shared group;
        TrackOwnership::Binding currentSave;
        if (!Global(group, error)) return false;
        if (!currentSave.CaptureType(&group, base, TrackLive::MicroSaveVtable, error)) return false;
        if (currentSave.Component() != microSave.Component()) {
            error = "native global microsave module was replaced";
            return false;
        }
        return true;
    }
    bool CaptureCar(Car& output, TrackLive::CarState& state, std::string& error) const {
        if (!Validate(error)) return false;
        uintptr_t holder = 0;
        if (!TrackOwnership::Pointer(base + 0x34aba60, holder)) {
            error = "native player car holder is unavailable";
            return false;
        }
        void* system = functions.resolveCars(reinterpret_cast<void*>(holder), nullptr);
        if (!system) {
            error = "native player car system is unavailable";
            return false;
        }
        void* value = nullptr;
        functions.playerCar(system, &value);
        Car car;
        car.Adopt(value, functions.releaseCar);
        TrackLive::CarState captured;
        uintptr_t owner = 0;
        if (!value) {
            error = "native primary player has no loaded car";
            return false;
        }
        if (!TrackLive::CaptureCar(base, car.Address(), captured, error)) return false;
        if (!TrackOwnership::Read(car.Address() + 0x4d00, &owner, sizeof owner) ||
            owner != reinterpret_cast<uintptr_t>(player)) {
            error = "native car no longer belongs to the captured primary player";
            return false;
        }
        if (!Validate(error)) return false;
        output.Adopt(car.Detach(), functions.releaseCar);
        state = captured;
        return true;
    }
    uintptr_t Module() const { return reinterpret_cast<uintptr_t>(microSave.Component()); }
    uintptr_t ProfileAddress() const { return profile; }
    void Reset() {
        void* old = player;
        player = nullptr;
        profile = 0;
        microSave.Reset();
        if (old) functions.releasePlayer(old);
    }
    Owners() = default;
    Owners(const Owners&) = delete;
    Owners& operator=(const Owners&) = delete;
};
}
