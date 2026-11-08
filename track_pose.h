#pragma once
#include "..\CarPackStudio\native\include\fh2mod.h"
#include <cmath>
#include <cstring>
#include <string>

namespace TrackPose {
constexpr uintptr_t RestoreBegin = 0x8fdff0;
constexpr uintptr_t FacingSite = 0x8fe26f;
constexpr unsigned char FacingBytes[] = {0xc5, 0xf8, 0x29, 0x45, 0xf0};
constexpr uintptr_t PositionSite = 0x8fe286;
constexpr unsigned char PositionBytes[] = {0xc5, 0xf8, 0x29, 0x45, 0xe0};

struct Pose {
    float position[4];
    float facing[4];
};
inline bool Valid(const Pose& pose) {
    for (float value : pose.position) if (!std::isfinite(value)) return false;
    for (float value : pose.facing) if (!std::isfinite(value)) return false;
    double length = 0;
    for (int i = 0; i < 3; ++i) length += double(pose.facing[i]) * pose.facing[i];
    return std::abs(length - 1.0) <= 0.001 && pose.facing[3] == 0;
}
struct Restoration {
    Pose pose;
    uintptr_t frame = 0;
    unsigned facingApplied = 0;
    unsigned positionApplied = 0;
    bool rejected = false;
};
inline Restoration*& Current() {
    static thread_local Restoration* active = nullptr;
    return active;
}
inline void Facing(FH2Context* context, void*) {
    auto active = Current();
    if (!active) return;
    if (!context || !context->rbp || active->rejected || active->facingApplied ||
        active->positionApplied) {
        active->rejected = true;
        return;
    }
    active->frame = context->rbp;
    std::memcpy(context->xmm[0].f, active->pose.facing, sizeof active->pose.facing);
    ++active->facingApplied;
}
inline void Position(FH2Context* context, void*) {
    auto active = Current();
    if (!active) return;
    if (!context || active->rejected || active->facingApplied != 1 ||
        active->positionApplied || context->rbp != active->frame) {
        active->rejected = true;
        return;
    }
    std::memcpy(context->xmm[0].f, active->pose.position, sizeof active->pose.position);
    ++active->positionApplied;
}
class Scope {
    Restoration state;
    bool entered = false;
public:
    explicit Scope(const Pose& pose) : state{pose} {
        if (!Current() && Valid(pose)) {
            Current() = &state;
            entered = true;
        }
    }
    ~Scope() { if (entered) Current() = nullptr; }
    bool Entered() const { return entered; }
    bool Complete(std::string& error) const {
        if (!entered) error = "invalid or nested native car restoration";
        else if (state.rejected) error = "unexpected native car restore frame/callback order";
        else if (state.facingApplied != 1 || state.positionApplied != 1)
            error = "native car restore pose hooks were not both reached";
        else return true;
        return false;
    }
    Scope(const Scope&) = delete;
    Scope& operator=(const Scope&) = delete;
};
}
