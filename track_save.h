#pragma once
#include "..\CarPackStudio\native\include\fh2mod.h"
#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>

namespace TrackSave {
constexpr uintptr_t MicroSaveBranch = 0x5e640a;
constexpr unsigned char MicroSaveBytes[] = {0x0f, 0x84, 0xcb, 0x01, 0x00, 0x00};
constexpr uintptr_t ProfileSaveBranch = 0x2f3aaa;
constexpr unsigned char ProfileSaveBytes[] = {0x0f, 0x84, 0x6e, 0x04, 0x00, 0x00};
constexpr uintptr_t ProfileCleanupBranch = 0x2f3ee2;
constexpr unsigned char ProfileCleanupBytes[] = {0x74, 0x3a, 0xc6, 0x81, 0x90, 0x08, 0x00, 0x00, 0x00};
constexpr uintptr_t DirectMicroSaveBranch = 0x2fad8e;
constexpr unsigned char DirectMicroSaveBytes[] = {0x0f, 0x84, 0x5d, 0x03, 0x00, 0x00};
constexpr uint64_t ZeroFlag = 0x40;

struct ReturnState {
    uintptr_t module;
    uintptr_t profile;
    int currentTrack;
    int targetTrack;
    uintptr_t pendingTrackJob;
    uintptr_t pendingSaveJob;
    uintptr_t pendingProfileJob;
    bool gameplayReady;
    bool originalPoseRestored;
    bool careerUnchanged;
};

class Gate {
    mutable std::mutex mutex;
    uintptr_t module = 0;
    uintptr_t profile = 0;
    std::thread::id owner;
    uint64_t blocked = 0;
    uint64_t blockedProfiles = 0;
    bool unexpectedModule = false;
    bool sessionOnly = false;
    // SaveDuringVisit: ownership bookkeeping stays intact, but native saves are not suppressed.
    std::atomic<bool> passThrough{false};
    std::atomic<uint64_t> passed{0};
public:
    void PassThrough(bool enabled) { passThrough.store(enabled); }
    bool PassingThrough() const { return passThrough.load(); }
    uint64_t Passed() const { return passed.load(); }
    bool ProtectSession(std::string& error) {
        std::lock_guard<std::mutex> lock(mutex);
        if (module || sessionOnly) {
            error = "native session save protection is already held";
            return false;
        }
        sessionOnly = true;
        return true;
    }
    bool Hold(uintptr_t saveModule, uintptr_t playerProfile, uintptr_t pendingSaveJob,
              uintptr_t pendingProfileJob, std::string& error) {
        std::lock_guard<std::mutex> lock(mutex);
        if (module) {
            error = "native save gate is already held";
            return false;
        }
        if (!saveModule || !playerProfile || pendingSaveJob || pendingProfileJob) {
            error = "native save owner is unavailable or a save job is still pending";
            return false;
        }
        module = saveModule;
        profile = playerProfile;
        owner = std::this_thread::get_id();
        blocked = 0;
        blockedProfiles = 0;
        unexpectedModule = false;
        return true;
    }
    void Suppress(FH2Context& context) {
        std::lock_guard<std::mutex> lock(mutex);
        if (!module && !sessionOnly) return;
        if (module && context.rbp != module) unexpectedModule = true;
        if (passThrough.load()) {
            ++passed;
            return;
        }
        // Force only the native JE to its normal no-submission epilogue.
        context.rflags |= ZeroFlag;
        ++blocked;
    }
    void SuppressProfile(FH2Context& context) {
        std::lock_guard<std::mutex> lock(mutex);
        if (!module && !sessionOnly) return;
        if (module && context.rcx != profile) unexpectedModule = true;
        if (passThrough.load()) {
            ++passed;
            return;
        }
        // Both save and early cleanup branches lead to the native false-return epilogue.
        context.rflags |= ZeroFlag;
        ++blockedProfiles;
    }
    bool Release(const ReturnState& state, std::string& error, bool allowCareerChanges = false) {
        std::lock_guard<std::mutex> lock(mutex);
        if (sessionOnly && !allowCareerChanges) {
            error = "experimental session saving stays disabled until game restart";
            return false;
        }
        if (!module) {
            error = "native save gate is not held";
            return false;
        }
        if (owner != std::this_thread::get_id() || state.module != module ||
            state.profile != profile || unexpectedModule) {
            error = "native save owner changed; gate retained until process exit";
            return false;
        }
        if (state.currentTrack != 542 || state.targetTrack != 542 || state.pendingTrackJob ||
            state.pendingSaveJob || state.pendingProfileJob || !state.gameplayReady || !state.originalPoseRestored ||
            (!state.careerUnchanged && !allowCareerChanges)) {
            error = "mainland return is not verified; native save gate remains held";
            return false;
        }
        module = 0;
        profile = 0;
        owner = {};
        sessionOnly = false;
        return true;
    }
    bool Held() const {
        std::lock_guard<std::mutex> lock(mutex);
        return module != 0 || sessionOnly;
    }
    bool SessionOnly() const {
        std::lock_guard<std::mutex> lock(mutex);
        return sessionOnly;
    }
    uintptr_t ProfileAddress() const {
        std::lock_guard<std::mutex> lock(mutex);
        return profile;
    }
    uint64_t Blocked() const {
        std::lock_guard<std::mutex> lock(mutex);
        return blocked;
    }
    uint64_t BlockedProfiles() const {
        std::lock_guard<std::mutex> lock(mutex);
        return blockedProfiles;
    }
    bool UnexpectedModule() const {
        std::lock_guard<std::mutex> lock(mutex);
        return unexpectedModule;
    }
    Gate() = default;
    Gate(const Gate&) = delete;
    Gate& operator=(const Gate&) = delete;
};

inline void Override(FH2Context* context, void* user) {
    if (context && user) static_cast<Gate*>(user)->Suppress(*context);
}
inline void OverrideProfile(FH2Context* context, void* user) {
    if (context && user) static_cast<Gate*>(user)->SuppressProfile(*context);
}
}
