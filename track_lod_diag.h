#pragma once
#include "..\FH2-Mod-Platform\include\fh2mod.h"
#include "track_ownership.h"
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>

namespace TrackLodDiag {
constexpr uintptr_t EntrySite = 0x1353A50;
constexpr uintptr_t SelectionSite = 0x135464F;
// Single-threshold selector call (r12b path: lower bands forced to 0, final band only).
constexpr uintptr_t SingleSelectionSite = 0x13545B8;
// Prop pop-in probes: three H posts, one KA board, and the non-popping sign 1508 as control.
struct Target { unsigned draw; unsigned model; };
constexpr Target Targets[] = {{3516, 26}, {3517, 26}, {3518, 26}, {3550, 50}, {1508, 412}};
constexpr unsigned TargetCount = sizeof Targets / sizeof Targets[0];

struct State {
    const FH2ModApi* api = nullptr;
    FH2Mod* mod = nullptr;
    std::atomic<uintptr_t> presentation{0};
    std::atomic<unsigned> events{0};
    std::atomic<bool> readFailure{false};
    std::atomic<ULONGLONG> entryNext[TargetCount]{};
    std::atomic<ULONGLONG> selectionNext[TargetCount][44]{};
};
inline State& Shared() { static State state; return state; }

inline bool Due(std::atomic<ULONGLONG>& next, ULONGLONG now) {
    auto expected = next.load(std::memory_order_relaxed);
    return now >= expected && next.compare_exchange_strong(expected, now + 5000,
        std::memory_order_relaxed);
}
inline void Log(const char* message) {
    auto& state = Shared();
    if (state.api && state.api->log && state.events.fetch_add(1) < 30000)
        state.api->log(state.mod, message);
}
inline void ReadFailure() {
    if (!Shared().readFailure.exchange(true))
        Log("lod-trace error: native target data could not be read; refusing this sample");
}
inline void Activate(uintptr_t presentation) {
    auto& state = Shared();
    for (auto& next : state.entryNext) next.store(0);
    for (auto& target : state.selectionNext)
        for (auto& next : target) next.store(0);
    state.presentation = presentation;
}
inline void Deactivate(uintptr_t presentation) {
    Shared().presentation.compare_exchange_strong(presentation, 0);
}

inline bool Target(uintptr_t descriptor, unsigned& slot) {
    const uintptr_t presentation = Shared().presentation.load(std::memory_order_relaxed);
    if (!presentation) return false;
    // At these verified sites the native renderer owns a live 0x100-byte descriptor.
    unsigned draw;
    std::memcpy(&draw, reinterpret_cast<const void*>(descriptor + 0xC0), sizeof draw);
    for (slot = 0; slot < TargetCount && Targets[slot].draw != draw; ++slot) {}
    if (slot == TargetCount) return false;
    uintptr_t draws = 0;
    unsigned model = 0;
    if (!TrackOwnership::Read(presentation + 0xFB78, &draws, sizeof draws) || !draws ||
        !TrackOwnership::Read(draws + draw * 0x70ull + 0x10, &model, sizeof model)) {
        ReadFailure();
        return false;
    }
    return model == Targets[slot].model;
}
inline void Entry(FH2Context* context, void*) {
    unsigned slot;
    if (!Target(context->rdx, slot) || !Due(Shared().entryNext[slot], GetTickCount64())) return;
    unsigned char descriptor[0x100];
    if (!TrackOwnership::Read(context->rdx, descriptor, sizeof descriptor)) {
        ReadFailure();
        return;
    }
    uint64_t flags = 0;
    unsigned short bands[4];
    float bounds[4], center[3];
    std::memcpy(&flags, descriptor + 0xB8, sizeof flags);
    std::memcpy(bands, descriptor + 0xF0, sizeof bands);
    std::memcpy(bounds, descriptor + 0x70, sizeof bounds);
    std::memcpy(center, descriptor + 0x60, sizeof center);
    char text[512];
    std::snprintf(text, sizeof text,
        "lod-trace entry draw=%u model=%u flags=%llx bypass=%u bands=%u/%u/%u/%u "
        "obb-center=%.2f/%.2f/%.2f obb-extents=%.3f/%.3f/%.3f descriptor-flags=%02x",
        Targets[slot].draw, Targets[slot].model, static_cast<unsigned long long>(flags),
        static_cast<unsigned>((flags >> 37) & 1), bands[0], bands[1], bands[2], bands[3],
        center[0], center[1], center[2], bounds[0], bounds[1], bounds[2], descriptor[0xC4]);
    Log(text);
}
inline void Selection(FH2Context* context, void* user) {
    unsigned slot;
    if (!Target(context->r15, slot)) return;
    const auto pass = static_cast<unsigned>(context->rsi);
    const bool single = user != nullptr;
    if (pass >= 22 || !Due(Shared().selectionNext[slot][pass + (single ? 22 : 0)], GetTickCount64()))
        return;
    int band = -1;
    if (!TrackOwnership::Read(context->rsp + 0x68, &band, sizeof band)) {
        ReadFailure();
        return;
    }
    const float squaredDistance = context->xmm[0].f[0];
    if (!std::isfinite(squaredDistance) || squaredDistance < 0 || band < -1 || band > 4) {
        Log("lod-trace error: native selection sample is outside its verified contract");
        return;
    }
    // xmm9 holds the pass view position and xmm10 the squared-distance scale (verified at 0x135455F).
    const auto& view = context->xmm[9].f;
    char text[320];
    std::snprintf(text, sizeof text,
        "lod-trace selection%s draw=%u model=%u pass=%u distance=%.3f band=%d rejected=%u "
        "view=%.1f/%.1f/%.1f scale=%.4f",
        single ? "-single" : "", Targets[slot].draw, Targets[slot].model, pass,
        std::sqrt(squaredDistance), band, static_cast<unsigned>(context->rax & 0xFF),
        view[0], view[1], view[2], context->xmm[10].f[0]);
    Log(text);
}

inline bool Install(const FH2ModApi* api, FH2Mod* mod, uintptr_t base) {
    auto& state = Shared();
    state.api = api;
    state.mod = mod;
    const unsigned char entry[] = {0x49, 0x8B, 0x45, 0x00, 0x32, 0xD2};
    const unsigned char selection[] = {0x84, 0xC0, 0x40, 0x0F, 0x94, 0xC7};
    unsigned char actual[sizeof entry];
    if (!TrackOwnership::Read(base + EntrySite, actual, sizeof actual) ||
        std::memcmp(actual, entry, sizeof actual) ||
        !TrackOwnership::Read(base + SelectionSite, actual, sizeof actual) ||
        std::memcmp(actual, selection, sizeof actual) ||
        !TrackOwnership::Read(base + SingleSelectionSite, actual, sizeof actual) ||
        std::memcmp(actual, selection, sizeof actual)) {
        Log("lod-trace error: executable signatures differ; diagnostic hooks not installed");
        return false;
    }
    if (!api->hook_instruction(mod, "TrackLoader prop LOD entry diagnostic",
                              reinterpret_cast<void*>(base + EntrySite), Entry, nullptr) ||
        !api->hook_instruction(mod, "TrackLoader prop LOD selection diagnostic",
                              reinterpret_cast<void*>(base + SelectionSite), Selection, nullptr) ||
        !api->hook_instruction(mod, "TrackLoader prop LOD single-band diagnostic",
                              reinterpret_cast<void*>(base + SingleSelectionSite), Selection,
                              reinterpret_cast<void*>(1))) {
        Log("lod-trace error: diagnostic hook registration refused");
        return false;
    }
    Log("lod-trace enabled: log-only prop LOD probes (H posts, KA board, control sign), 5-second per-pass sampling; not an FPS benchmark");
    return true;
}
}
