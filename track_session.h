#pragma once
#include "track_player.h"
#include "track_group.h"
#include "track_flow.h"
#include "track_diag.h"
#include "track_visit.h"
#include <mutex>

namespace TrackSession {
class Driver {
    enum class State { Unavailable, Ready, Entering, Driving, Returning, Failed };
    mutable std::mutex mutex;
    State state = State::Unavailable;
    const FH2ModApi* api = nullptr;
    FH2Mod* mod = nullptr;
    const TrackVisit::Api* cooperation = nullptr;
    uintptr_t base = 0;
    int track = 0;
    int request = 0;
    ULONGLONG deadline = 0;
    TrackPose::Pose arrival = {};
    TrackLifecycle::Names lifecycle;
    bool restoreSaving = false;
    TrackLive::CarState departure;
    TrackLive::CarState mainlandArrival;
    TrackSave::Gate saves;
    TrackBinding::Runtime bridge{saves};
    TrackPlayer::Owners owners;
    TrackGroup::Loader loader;
    TrackFlow::Registry flows;
    TrackBinding::Visit entry, returned;
    std::string detail = "experimental runtime not initialized";
    std::string submissionError;
    static void ResetVisit(TrackBinding::Visit& visit) {
        visit.target = {};
        visit.pose = {};
        visit.profile = 0;
        visit.component = nullptr;
        visit.mainlandStartup = nullptr;
        visit.swaps = 0;
        visit.restores = 0;
        visit.failed = false;
        visit.destroyed = false;
        visit.launched = false;
        visit.startupLaunched = false;
        visit.transitionOwner.Reset();
        visit.startupOwner.Reset();
        visit.error.clear();
    }
    static void TickCallback(void* user, void* pause, int canEnter) {
        static_cast<Driver*>(user)->Tick(pause, canEnter != 0);
    }
    static void LoadCallback(void* user) {
        auto self = static_cast<Driver*>(user);
        self->loader.Submit(self->state == State::Returning, self->submissionError, self->lifecycle);
    }
    static bool PrepareGroupCallback(void* user, void* group, std::string& error) {
        auto self = static_cast<Driver*>(user);
        return self->flows.Ensure(group, error, self->lifecycle);
    }
    void Fail(const std::string& error) {
        state = State::Failed;
        detail = error;
        api->log(mod, ("error: " + error +
            (saves.Held() ? "; experimental saving remains disabled; restart required" :
                                   "; no custom visit started")).c_str());
    }
    bool GameplayReady(void* pause) const {
        return pause && reinterpret_cast<bool(*)(void*)>(base + 0x963560)(pause);
    }
    bool Dispatch(TrackBinding::Visit& visit) {
        std::string error;
        submissionError.clear();
        bool submitted = bridge.Dispatch(visit, LoadCallback, this, error);
        if (!submitted || !submissionError.empty()) {
            Fail(submissionError.empty() ? error : submissionError);
            return false;
        }
        deadline = GetTickCount64() + 180000;
        return true;
    }
    void Tick(void* pause, bool canEnter) {
        std::lock_guard<std::mutex> lock(mutex);
        if (state == State::Unavailable || state == State::Failed) return;
        std::string error;
        if (state == State::Ready) {
            if (!request) return;
            request = 0;
            if (!canEnter) {
                detail = "entry refused: native mainland free roam is not idle/eligible";
                api->log(mod, ("error: " + detail).c_str());
                return;
            }
            TrackLive::MicroSaveState micro;
            TrackLive::MapState map;
            TrackPlayer::Car car;
            if (!owners.Capture(error) || !owners.CaptureCar(car, departure, error) ||
                !TrackLive::CaptureMap(base, map, error) ||
                !TrackLive::CaptureMicroSave(base, owners.Module(), micro, error)) {
                Fail(error);
                return;
            }
            if (!map.Settled() || map.current != 542) {
                Fail("native departure map is not settled mainland; current=" +
                    std::to_string(map.current) + " target=" + std::to_string(map.target) +
                    " pending=" + std::to_string(map.pendingJob));
                return;
            }
            if (!micro.Idle()) {
                Fail("native departure microsave job remains pending; job=" +
                    std::to_string(micro.job) + " control=" + std::to_string(micro.control));
                return;
            }
            if (!bridge.SessionProfileReady(owners.ProfileAddress(), error)) {
                Fail(error);
                return;
            }
            char message[512] = {};
            if (!cooperation->acquire(message, sizeof message)) {
                detail = message;
                api->log(mod, message);
                owners.Reset();
                return;
            }
            if (!saves.Hold(owners.Module(), owners.ProfileAddress(), 0, 0, error)) {
                Fail(error);
                return;
            }
            api->set_notice(mod, restoreSaving ?
                "Custom tracks: SAVING DISABLED\nSaving resumes after verified mainland return" :
                "Custom tracks: SAVING DISABLED\nDisable track activation and restart to restore saving");
            mainlandArrival = TrackLive::AirportReturn(departure);
            entry.target = {542, 542, track,
                {arrival.position[0], arrival.position[1], arrival.position[2], 1}, 0};
            entry.pose = arrival;
            entry.lifecycle = lifecycle;
            entry.profile = owners.ProfileAddress();
            state = State::Entering;
            detail = "native entry and streaming pending";
            Dispatch(entry);
            return;
        }
        if (!owners.Validate(error)) { Fail(error); return; }
        auto& visit = state == State::Returning ? returned : entry;
        if (visit.failed) { Fail(visit.error); return; }
        TrackLive::MapState map;
        if (!TrackLive::CaptureMap(base, map, error)) { Fail(error); return; }
        if (state == State::Driving) {
            if (!map.Settled() || map.current != track) {
                Fail("native custom map changed outside the owned visit");
                return;
            }
            if (!request) return;
            request = 0;
            returned.target = {track, 542, 542,
                {mainlandArrival.pose.position[0], mainlandArrival.pose.position[1], mainlandArrival.pose.position[2], 1}, 0};
            returned.pose = mainlandArrival.pose;
            returned.lifecycle = lifecycle;
            returned.profile = owners.ProfileAddress();
            state = State::Returning;
            detail = "native mainland transition and pose restoration pending";
            Dispatch(returned);
            return;
        }
        if (GetTickCount64() >= deadline) {
            Fail("native transition timed out: " + detail + " (" + TrackLive::DescribeLoad(base) +
                 " launched=" + std::to_string(visit.launched) + " swaps=" + std::to_string(visit.swaps) +
                 " restores=" + std::to_string(visit.restores) + ")");
            return;
        }
        if (!map.Settled() || map.current != visit.target.targetTrack ||
            !visit.launched || visit.swaps != 1 || visit.restores != 1 || !GameplayReady(pause)) return;
        TrackPlayer::Car car;
        TrackLive::CarState current;
        if (!owners.CaptureCar(car, current, error)) { detail = error; return; }
        if (state == State::Entering) {
            if (!bridge.FinishEntry(entry, map.current, true, true, error)) { detail = error; return; }
            loader.ReleaseReceipt();
            state = State::Driving;
            detail = "native custom map/car/flow ready; rendering and driving require in-game observation";
            api->log(mod, ("track " + std::to_string(track) + ": " + detail).c_str());
        } else {
            bool finished = false;
            if (restoreSaving) {
                TrackSave::ReturnState observed = {owners.Module(), owners.ProfileAddress(),
                    0, 0, 0, 0, 0, true, false, false};
                finished = bridge.FinishReturnObserved(returned, mainlandArrival, car.Address(),
                                                       observed, error, true);
            } else {
                finished = bridge.FinishSessionReturnObserved(returned, mainlandArrival, car.Address(),
                                                              owners.Module(), true, error);
            }
            if (!finished) {
                detail = error;
                return;
            }
            loader.ReleaseReceipt();
            char message[512] = {};
            if (!cooperation->release(message, sizeof message)) { Fail(message); return; }
            owners.Reset();
            ResetVisit(entry);
            ResetVisit(returned);
            departure = {};
            mainlandArrival = {};
            state = State::Ready;
            detail = restoreSaving ?
                "mainland Airport arrival verified; saving enabled; repeat entry ready; career changes not audited" :
                "mainland Airport arrival verified; repeat entry ready; saving disabled until game restart";
            api->set_notice(mod, restoreSaving ? nullptr : "Custom tracks: SAVING DISABLED\nDisable track activation and restart to restore saving");
            api->log(mod, detail.c_str());
        }
    }
public:
    bool Protect(const FH2ModApi* platform, FH2Mod* owner, uintptr_t image, std::string& error) {
        std::lock_guard<std::mutex> lock(mutex);
        if (state != State::Unavailable || api || !platform || !owner || !image) {
            error = "experimental native protection services unavailable or already initialized";
            return false;
        }
        api = platform;
        mod = owner;
        base = image;
        const unsigned char signature[] = {0x48,0x89,0x5c,0x24,0x08,0x48,0x89,0x74,0x24,0x10};
        unsigned char actual[sizeof signature] = {};
        if (!TrackOwnership::Read(base + 0x963560, actual, sizeof actual) ||
            memcmp(actual, signature, sizeof actual)) {
            error = "unsupported native gameplay readiness signature";
            Fail(error);
            return false;
        }
        if (!owners.Initialize(base, error) || !loader.Initialize(base, error) ||
            !flows.Initialize(base, error) ||
            !bridge.SetGroupPreparation(PrepareGroupCallback, this, error) ||
            !bridge.Install(api, mod, base, error) ||
            (TrackDiag::Install(api, mod, base), !bridge.Enable(error, true))) {
            Fail(error);
            return false;
        }
        if (!bridge.SessionProtectionActive()) {
            error = "native session protection did not become active";
            Fail(error);
            return false;
        }
        detail = "early native protection active; pack validation/publication pending; saving disabled until restart";
        api->log(mod, detail.c_str());
        return true;
    }
    bool Initialize(const FH2ModApi* platform, FH2Mod* owner, uintptr_t image,
                    const TrackVisit::Api* services, int id, const TrackPose::Pose& pose,
                    std::string& error, bool restoreSavingOnReturn = false,
                    const TrackLifecycle::Names& names = {}) {
        std::lock_guard<std::mutex> lock(mutex);
        if (state != State::Unavailable || api != platform || mod != owner || base != image ||
            !bridge.SessionProtectionActive() ||
            !services || services->size < sizeof *services || !services->attach ||
            !services->acquire || !services->release || !services->held ||
            id < 10000 || !TrackPose::Valid(pose) || !TrackLifecycle::Valid(names.group)) {
            error = "experimental native protection/visit cooperation is unavailable or invalid";
            return false;
        }
        cooperation = services;
        track = id;
        arrival = pose;
        lifecycle = names;
        restoreSaving = restoreSavingOnReturn;
        TrackVisit::Client client{sizeof client, this, TickCallback};
        char message[512] = {};
        if (!cooperation->attach(&client, message, sizeof message)) {
            error = message;
            Fail(error);
            return false;
        }
        state = State::Ready;
        detail = restoreSaving ?
            "native entry ready; saving disabled until verified mainland return; career changes not audited" :
            "native entry ready; all profile saving disabled until restart";
        api->log(mod, detail.c_str());
        return true;
    }
    bool Queue(bool returning, std::string& error) {
        std::lock_guard<std::mutex> lock(mutex);
        if (request || state != (returning ? State::Driving : State::Ready)) {
            error = "experimental command refused: " + detail;
            return false;
        }
        request = returning ? 2 : 1;
        return true;
    }
    bool QueueEntry(int id, const TrackPose::Pose& pose, const TrackLifecycle::Names& names,
                    bool restoreSavingOnReturn, std::string& error) {
        std::lock_guard<std::mutex> lock(mutex);
        if (request || state != State::Ready || id < 10000 ||
            !TrackPose::Valid(pose) || !TrackLifecycle::Valid(names.group)) {
            error = "track entry refused: " + detail;
            return false;
        }
        track = id;
        arrival = pose;
        lifecycle = names;
        restoreSaving = restoreSavingOnReturn;
        request = 1;
        return true;
    }
    std::string Status() const {
        std::lock_guard<std::mutex> lock(mutex);
        return detail;
    }
    bool InCustomTrack() const {
        std::lock_guard<std::mutex> lock(mutex);
        return state == State::Driving;
    }
};
}
