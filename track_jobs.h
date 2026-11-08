#pragma once
#include "track_ownership.h"
#include "..\CarPackStudio\native\include\fh2mod.h"
#include <memory>
#include <mutex>

namespace TrackJobs {
constexpr uintptr_t BeginSite = 0x11ff03d;
constexpr unsigned char BeginBytes[] = {0x4c,0x8b,0x64,0x24,0x30};
constexpr uintptr_t Completion = 0x11ee260;
constexpr unsigned char CompletionBytes[] = {0x48,0x83,0xec,0x28,0x48,0x83,0x79,0x20,0x00};
constexpr uintptr_t OperationVtable = 0x2eae2b0;
constexpr uintptr_t Submit = 0x11fefd0;
constexpr unsigned char SubmitBytes[] = {0x48,0x8b,0xc4,0x4c,0x89,0x48,0x20};
constexpr uintptr_t ProfileConstructor = 0x2bf410;
constexpr unsigned char ProfileConstructorBytes[] = {0x48,0x8b,0xc4,0x48,0x89,0x48,0x08};

class ProfileBoundary {
    mutable std::mutex mutex;
    std::unordered_set<uintptr_t> created;
    std::string failure;
public:
    void Constructed(uintptr_t value, uintptr_t result, uintptr_t base) {
        uintptr_t table = 0;
        bool valid = base && value && result == value &&
            TrackOwnership::Read(value, &table, sizeof table) && table == base + 0x2c5f870;
        std::lock_guard<std::mutex> lock(mutex);
        if (!failure.empty()) return;
        if (!valid) failure = "native observed profile construction has an unsupported result";
        else if (created.count(value)) return;
        else if (created.size() >= 64) failure = "native observed profile construction limit exceeded";
        else created.insert(value);
    }
    bool Contains(uintptr_t profile, std::string& error) const {
        std::lock_guard<std::mutex> lock(mutex);
        if (!failure.empty()) { error = failure; return false; }
        if (!profile || !created.count(profile)) {
            error = "primary profile predates persistence hooks; restart required for session-only travel";
            return false;
        }
        return true;
    }
};

class Ledger {
    mutable std::mutex mutex;
    std::vector<std::unique_ptr<TrackNative::Shared>> pending;
    std::string failure;
    size_t calls = 0;
public:
    void Enter() {
        std::lock_guard<std::mutex> lock(mutex);
        ++calls;
    }
    void Leave() {
        std::lock_guard<std::mutex> lock(mutex);
        if (calls) --calls;
        else failure = "native persistence submission lifetime is unbalanced";
    }
    bool Begin(const FH2Context& context, uintptr_t base, std::string& error) {
        uintptr_t object = 0, control = 0, table = 0;
        LONG strong = 0;
        bool valid = context.rsp &&
            TrackOwnership::Pointer(context.rsp + 0x28, object) &&
            TrackOwnership::Pointer(context.rsp + 0x30, control) &&
            TrackOwnership::Pointer(object, table) && table == base + OperationVtable &&
            TrackOwnership::Read(control + 8, &strong, sizeof strong) && strong > 0;
        std::lock_guard<std::mutex> lock(mutex);
        if (!failure.empty()) { error = failure; return false; }
        if (!valid) {
            error = failure = "native persistence operation owner/layout could not be captured";
            return false;
        }
        for (const auto& job : pending) if (job->object == reinterpret_cast<void*>(object)) {
            error = failure = "native persistence operation was captured twice";
            return false;
        }
        if (pending.size() >= 64) {
            error = failure = "native persistence operation limit exceeded; idle cannot be verified";
            return false;
        }
        auto job = std::make_unique<TrackNative::Shared>();
        job->Retain(reinterpret_cast<void*>(object), reinterpret_cast<void*>(control));
        pending.push_back(std::move(job));
        return true;
    }
    void Complete(void* object) {
        std::unique_ptr<TrackNative::Shared> completed;
        {
            std::lock_guard<std::mutex> lock(mutex);
            for (auto it = pending.begin(); it != pending.end(); ++it) if ((*it)->object == object) {
                completed = std::move(*it);
                pending.erase(it);
                break;
            }
        }
        // Release outside the lock: native destruction can invoke other callbacks.
    }
    bool Idle(std::string& error) const {
        std::lock_guard<std::mutex> lock(mutex);
        if (!failure.empty()) { error = failure; return false; }
        if (!pending.empty() || calls) {
            error = "native persistence operations/submissions remain pending: " +
                std::to_string(pending.size()) + "/" + std::to_string(calls);
            return false;
        }
        return true;
    }
    size_t Pending() const {
        std::lock_guard<std::mutex> lock(mutex);
        return pending.size();
    }
};
class Call {
    Ledger& jobs;
public:
    explicit Call(Ledger& ledger) : jobs(ledger) { jobs.Enter(); }
    ~Call() { jobs.Leave(); }
    Call(const Call&) = delete;
    Call& operator=(const Call&) = delete;
};
}
