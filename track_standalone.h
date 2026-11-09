#pragma once
#include "track_visit.h"
#include "track_live.h"
#include <atomic>
#include <mutex>

namespace TrackStandalone {
class Coordinator {
    std::mutex mutex;
    TrackVisit::Client client = {};
    bool attached = false;
    bool ticking = false;
    bool allowed = false;
    DWORD thread = 0;
    std::atomic<bool> lease{false};
    static int Error(char* error, size_t size, const char* message) {
        if (error && size) strncpy_s(error, size, message, _TRUNCATE);
        return 0;
    }
public:
    int Attach(const TrackVisit::Client* value, char* error, size_t size) {
        std::lock_guard<std::mutex> lock(mutex);
        if (!value || value->size < sizeof client || !value->tick)
            return Error(error, size, "error: invalid standalone track client");
        if (attached) return Error(error, size, "error: standalone track client already attached");
        client = *value;
        attached = true;
        return 1;
    }
    int Acquire(char* error, size_t size) {
        std::lock_guard<std::mutex> lock(mutex);
        if (!ticking || thread != GetCurrentThreadId() || !allowed || lease)
            return Error(error, size, "error: standalone track ownership requires an idle mainland tick");
        lease = true;
        allowed = false;
        return 1;
    }
    int Release(char* error, size_t size) {
        std::lock_guard<std::mutex> lock(mutex);
        if (!ticking || thread != GetCurrentThreadId() || !lease)
            return Error(error, size, "error: standalone track ownership requires its native tick");
        lease = false;
        return 1;
    }
    bool Tick(void* pause, bool canEnter) {
        TrackVisit::Client callback;
        {
            std::lock_guard<std::mutex> lock(mutex);
            if (!attached || ticking) return false;
            ticking = true;
            allowed = canEnter;
            thread = GetCurrentThreadId();
            callback = client;
        }
        struct Exit {
            Coordinator& owner;
            ~Exit() {
                std::lock_guard<std::mutex> lock(owner.mutex);
                owner.ticking = owner.allowed = false;
            }
        } exit{*this};
        callback.tick(callback.user, pause, canEnter);
        return true;
    }
    bool Held() const { return lease; }
    bool Attached() {
        std::lock_guard<std::mutex> lock(mutex);
        return attached;
    }
};

class Runtime {
    Coordinator coordinator;
    uintptr_t base = 0;
    const FH2ModApi* api = nullptr;
    FH2Mod* mod = nullptr;
    uintptr_t (*original)(void*) = nullptr;
    std::string lastError;
    static Runtime*& Instance() {
        static Runtime* runtime = nullptr;
        return runtime;
    }
    static uintptr_t Poll(void* pause) { return Instance()->Tick(pause); }
    static int Attach(const TrackVisit::Client* client, char* error, size_t size) {
        return Instance()->coordinator.Attach(client, error, size);
    }
    static int Acquire(char* error, size_t size) {
        return Instance()->coordinator.Acquire(error, size);
    }
    static int Release(char* error, size_t size) {
        return Instance()->coordinator.Release(error, size);
    }
    static int Held() { return Instance()->coordinator.Held(); }
    TrackVisit::Api services{sizeof(TrackVisit::Api), Attach, Acquire, Release, Held};
    uintptr_t Tick(void* pause) {
        if (!coordinator.Attached()) return original(pause);
        TrackLive::MapState map;
        std::string error;
        bool settled = TrackLive::CaptureMap(base, map, error) && map.Settled();
        if (!error.empty() && error != lastError)
            api->log(mod, ("error: standalone native tick: " + error).c_str());
        lastError = error;
        bool playable = pause && reinterpret_cast<bool(*)(void*)>(base + 0x963560)(pause);
        coordinator.Tick(pause, settled && map.current == 542 && playable);
        if (coordinator.Held() && !(settled && map.current != 542 && playable)) return 0;
        return original(pause);
    }
public:
    bool Initialize(const FH2ModApi* platform, FH2Mod* owner, uintptr_t image, std::string& error) {
        const unsigned char tick[] = {0x48,0x89,0x5c,0x24,0x08,0x57,0x48,0x83,0xec,0x70};
        const unsigned char readiness[] = {0x48,0x89,0x5c,0x24,0x08,0x48,0x89,0x74,0x24,0x10};
        unsigned char actual[sizeof tick] = {};
        if (Instance() || !platform || !platform->hook_function || !owner || !image ||
            !TrackOwnership::Read(image + 0x964810, actual, sizeof actual) ||
            memcmp(actual, tick, sizeof tick) ||
            !TrackOwnership::Read(image + 0x963560, actual, sizeof actual) ||
            memcmp(actual, readiness, sizeof readiness)) {
            error = "standalone native tick services unavailable or signatures unsupported";
            return false;
        }
        api = platform;
        mod = owner;
        base = image;
        if (!api->hook_function(mod, "TrackLoader native free-roam dispatch",
                reinterpret_cast<void*>(base + 0x964810), reinterpret_cast<void*>(Poll),
                reinterpret_cast<void**>(&original)) || !original) {
            error = "standalone native free-roam hook registration failed";
            return false;
        }
        Instance() = this;
        return true;
    }
    const TrackVisit::Api* Services() const { return &services; }
};
}
