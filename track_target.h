#pragma once
#include "..\CarPackStudio\native\include\fh2mod.h"
#include <cmath>
#include <cstdint>
#include <cstring>
#include <string>

namespace TrackTarget {
constexpr uintptr_t PreparationSite = 0x939410;
constexpr unsigned char PreparationBytes[] = {0x48, 0x8b, 0x0d, 0x49, 0x26, 0xb7, 0x02};
struct Request {
    int sourceTrack;
    int descriptorTrack;
    int targetTrack;
    float position[4];
    float heading;
};
struct Preparation {
    Request request;
    unsigned applied = 0;
    bool rejected = false;
    int32_t observedTrack = 0;
};
inline Preparation*& Current() {
    static thread_local Preparation* active = nullptr;
    return active;
}

inline bool Valid(const Request& request) {
    if ((request.sourceTrack != 542 && request.sourceTrack < 10000) ||
        (request.descriptorTrack != 542 && request.descriptorTrack < 10000) ||
        (request.targetTrack != 542 && request.targetTrack < 10000) ||
        request.sourceTrack == request.targetTrack || !std::isfinite(request.heading)) return false;
    for (float value : request.position) if (!std::isfinite(value)) return false;
    return true;
}

inline void Override(FH2Context* context, void*) {
    Preparation* active = Current();
    if (!active) return;
    if (!context || active->applied || active->rejected) {
        active->rejected = true;
        return;
    }
    // SwapTrack copies descriptor+B0/E0/108 into these locals and never reads the descriptor
    // again; custom flows do not stage that descriptor, so its stale track is only recorded.
    active->observedTrack = static_cast<int32_t>(context->r14);
    // Only replace the callback's local arguments. Shared travel/career data is untouched.
    context->r14 = static_cast<uint32_t>(active->request.targetTrack);
    std::memcpy(context->xmm[6].f, active->request.position, sizeof(active->request.position));
    context->xmm[7].f[0] = active->request.heading;
    ++active->applied;
}

class Scope {
    Preparation state;
    bool entered = false;
public:
    explicit Scope(const Request& request) : state{request} {
        if (!Current() && Valid(request)) {
            Current() = &state;
            entered = true;
        }
    }
    ~Scope() { if (entered) Current() = nullptr; }
    bool Entered() const { return entered; }
    int32_t ObservedTrack() const { return state.observedTrack; }
    bool Complete(std::string& error) const {
        if (!entered) error = "invalid or nested native track preparation";
        else if (state.rejected) error = "duplicate native travel preparation callback";
        else if (state.applied != 1) error = "native track preparation hook was not reached";
        else return true;
        return false;
    }
    Scope(const Scope&) = delete;
    Scope& operator=(const Scope&) = delete;
};
}
