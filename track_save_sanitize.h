#pragma once
#include "track_ownership.h"
#include "track_pose.h"
#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <string>

// Keeps native saves taken on a custom track loadable without the mod: the SaveState reflection
// of PlayerState reports the mainland (LastTrackVisited 542, Airport position/facing) so the next
// boot never asks for track 10000 or spawns at track coordinates.
//   A: track-swap handler (0x9393b0) right after `mov [PlayerState+0x7c], r14d` records the
//      PlayerState object and the swapped-in track; while armed on a custom track the field is
//      rewritten to 542 (its only reader is the boot track request, 0x563520).
//   B: the vec4 reflection getter thunk (std::function _Do_call 0xa6cac0, unique to vec4
//      getters) is redirected to an aligned copy carrying the mainland pose when the object is
//      PlayerState and the getter is PlayerPosition (+0x10) or PlayerFacing (+0x20).
namespace TrackSaveSanitize {
constexpr uintptr_t SwapSite = 0x9394f2;
constexpr uintptr_t GetterSite = 0xa6cacd;
constexpr uintptr_t PositionGetter = 0x82b110;
constexpr uintptr_t FacingGetter = 0x18ddca0;
constexpr int Mainland = 542;
constexpr int FirstCustom = 10000;

struct State {
    uintptr_t base = 0;
    bool installed = false;
    std::atomic<uintptr_t> playerState{0};
    std::atomic<int> track{0};
    std::atomic<bool> armed{false};
    std::atomic<uint64_t> rewrites{0};
    std::atomic<uint64_t> poses{0};
    std::mutex poseMutex;
    float position[3] = {425.574f, 110.821f, -1478.082f};
    float facing[3] = {0, 0, 1};
};
inline State& Get() {
    static State state;
    return state;
}

inline void Swap(FH2Context* context, void*) {
    State& state = Get();
    const int track = static_cast<int>(static_cast<uint32_t>(context->r14));
    if (context->rbx) state.playerState.store(context->rbx);
    state.track.store(track);
    if (context->rbx && track >= FirstCustom && state.armed.load()) {
        *reinterpret_cast<int*>(context->rbx + 0x7c) = Mainland;
        ++state.rewrites;
    }
}

inline void Getter(FH2Context* context, void*) {
    State& state = Get();
    if (!state.armed.load() || state.track.load() < FirstCustom) return;
    const uintptr_t object = context->r8;
    if (!object || object != state.playerState.load()) return;
    const uintptr_t getter = context->rax;
    const bool position = getter == state.base + PositionGetter;
    if (!position && getter != state.base + FacingGetter) return;
    // The getters read with aligned vmovaps: give them a 16-byte aligned stand-in object.
    alignas(16) static thread_local float fake[12];
    float* value = fake + (position ? 4 : 8);
    memcpy(value, reinterpret_cast<const void*>(object + (position ? 0x10 : 0x20)), 16);
    {
        std::lock_guard<std::mutex> lock(state.poseMutex);
        memcpy(value, position ? state.position : state.facing, 12);
    }
    context->r8 = reinterpret_cast<uintptr_t>(fake);
    ++state.poses;
}

inline bool Supported(uintptr_t base) {
    static const unsigned char swap[] = {0x48,0x85,0xdb,0x74,0x04,0x44,0x89,0x73,0x7c,
                                         0x48,0xc7,0x45,0xdf,0x0f,0x00,0x00,0x00};
    static const unsigned char track[] = {0x44,0x8b,0xb0,0xb0,0x00,0x00,0x00};
    static const unsigned char thunk[] = {0x40,0x53,0x48,0x83,0xec,0x30,0x48,0x8b,0x41,0x08,0x48,0x8b,0xda,
                                          0x48,0x8d,0x54,0x24,0x20,0x49,0x8b,0xc8,0xff,0xd0};
    static const unsigned char position[] = {0xc5,0xf8,0x28,0x41,0x10,0x48,0x8b,0xc2,0xc5,0xf8,0x29,0x02,0xc3};
    static const unsigned char facing[] = {0xc5,0xf8,0x28,0x41,0x20,0x48,0x8b,0xc2,0xc5,0xf8,0x29,0x02,0xc3};
    unsigned char actual[32] = {};
    return base &&
        TrackOwnership::Read(base + SwapSite - 9, actual, sizeof swap) && !memcmp(actual, swap, sizeof swap) &&
        TrackOwnership::Read(base + 0x9393f9, actual, sizeof track) && !memcmp(actual, track, sizeof track) &&
        TrackOwnership::Read(base + 0xa6cac0, actual, sizeof thunk) && !memcmp(actual, thunk, sizeof thunk) &&
        TrackOwnership::Read(base + PositionGetter, actual, sizeof position) &&
        !memcmp(actual, position, sizeof position) &&
        TrackOwnership::Read(base + FacingGetter, actual, sizeof facing) && !memcmp(actual, facing, sizeof facing);
}

// Hooks are passive until Arm; Installed() is false when either hook was refused.
inline bool Install(const FH2ModApi* api, FH2Mod* mod, uintptr_t base) {
    State& state = Get();
    if (state.installed) return true;
    if (!api || api->api_version < 7 || !api->hook_instruction || !base) return false;
    if (!Supported(base)) {
        api->log(mod, "error: save sanitizer unavailable: unsupported game build; saving stays blocked on custom tracks");
        return false;
    }
    state.base = base;
    if (!api->hook_instruction(mod, "TrackLoader save sanitizer track swap",
                               reinterpret_cast<void*>(base + SwapSite), Swap, nullptr)) {
        api->log(mod, "error: save sanitizer track-swap hook refused; saving stays blocked on custom tracks");
        return false;
    }
    if (!api->hook_instruction(mod, "TrackLoader save sanitizer player pose",
                               reinterpret_cast<void*>(base + GetterSite), Getter, nullptr)) {
        api->log(mod, "error: save sanitizer player-pose hook refused; saving stays blocked on custom tracks");
        return false;
    }
    state.installed = true;
    api->log(mod, "save sanitizer installed: saves on custom tracks resume at the mainland Airport");
    return true;
}
inline bool Installed() { return Get().installed; }

inline bool Arm(const TrackPose::Pose& pose) {
    State& state = Get();
    if (!state.installed || !TrackPose::Valid(pose)) return false;
    float facing[3] = {pose.facing[0], 0, pose.facing[2]};
    float length = std::sqrt(facing[0] * facing[0] + facing[2] * facing[2]);
    if (!(length > 0.001f)) {
        facing[0] = 0;
        facing[2] = 1;
        length = 1;
    }
    {
        std::lock_guard<std::mutex> lock(state.poseMutex);
        memcpy(state.position, pose.position, sizeof state.position);
        for (int i = 0; i < 3; ++i) state.facing[i] = facing[i] / length;
    }
    state.armed.store(true);
    return true;
}
inline void Disarm() {
    State& state = Get();
    state.armed.store(false);
}
inline int Track() { return Get().track.load(); }
inline bool PlayerStateSeen() { return Get().playerState.load() != 0; }
inline uint64_t Rewrites() { return Get().rewrites.load(); }
inline uint64_t Poses() { return Get().poses.load(); }
}
