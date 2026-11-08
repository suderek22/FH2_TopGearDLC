#pragma once
#include "track_target.h"
#include "track_pose.h"
#include "track_save.h"
#include "track_ownership.h"
#include "track_live.h"
#include "track_jobs.h"
#include <windows.h>
#include <vector>

namespace TrackBinding {
constexpr uintptr_t MachineLaunch = 0x5641b0;
constexpr uintptr_t MachineUpdate = 0x55d070;
constexpr uintptr_t MachineDestroy = 0x5415c0;
constexpr uintptr_t SwapBegin = 0x93cfd0;
constexpr uintptr_t MachineVtable = 0x2c98cf0;
constexpr uintptr_t GroupParse = 0x50a2e0;

struct Visit {
    TrackTarget::Request target;
    TrackPose::Pose pose;
    uintptr_t profile = 0;
    void* component = nullptr;
    void* mainlandStartup = nullptr;
    unsigned swaps = 0;
    unsigned restores = 0;
    bool failed = false;
    bool destroyed = false;
    bool launched = false;
    bool startupLaunched = false;
    TrackOwnership::Binding transitionOwner;
    TrackOwnership::Binding startupOwner;
    std::string error;
};
inline Visit*& Executing() {
    static thread_local Visit* visit = nullptr;
    return visit;
}
inline void*& ExecutingComponent() {
    static thread_local void* component = nullptr;
    return component;
}
class Execution {
    Visit* previous;
    void* previousComponent;
public:
    explicit Execution(Visit* visit, void* component = nullptr)
        : previous(Executing()), previousComponent(ExecutingComponent()) {
        Executing() = visit;
        ExecutingComponent() = component;
    }
    ~Execution() {
        Executing() = previous;
        ExecutingComponent() = previousComponent;
    }
    Execution(const Execution&) = delete;
    Execution& operator=(const Execution&) = delete;
};
inline bool Read(uintptr_t address, void* data, size_t size) {
    return TrackOwnership::Read(address, data, size);
}
inline bool Flow(void* component, uintptr_t base, const char* expected) {
    return TrackOwnership::Flow(component, base, expected);
}

class Runtime;
inline Runtime*& Instance() {
    static Runtime* instance = nullptr;
    return instance;
}

class Runtime {
    const FH2ModApi* api = nullptr;
    FH2Mod* mod = nullptr;
    uintptr_t base = 0;
    TrackSave::Gate& saves;
    TrackJobs::Ledger jobs;
    TrackJobs::ProfileBoundary profiles;
    Visit* dispatching = nullptr;
    Visit* bound = nullptr;
    std::thread::id gameThread;
    std::vector<FH2Hook*> hooks;
    bool installed = false;
    bool enabled = false;
    bool attempted = false;
    bool (*prepareGroup)(void*, void*, std::string&) = nullptr;
    void* preparationUser = nullptr;

    void Fail(Visit& visit, const std::string& error) {
        visit.failed = true;
        visit.error = error;
        if (api && api->log) api->log(mod, ("error: " + error + "; visit save gate retained").c_str());
    }
    static bool LaunchHook(void* component, const char* flow) { return Instance()->Launch(component, flow); }
    static void* ParseHook(void* parser, void* output, const char* filename) {
        return Instance()->Parse(parser, output, filename);
    }
    static void JobBeginHook(FH2Context* context, void*) {
        auto runtime = Instance();
        std::string error;
        FH2Context missing = {};
        if (!runtime->jobs.Begin(context ? *context : missing, runtime->base, error))
            runtime->api->log(runtime->mod, error.c_str());
    }
    static void* JobSubmitHook(void* service, void* output, void* filename, void* data,
                               void* resource, void* callback, bool asynchronous) {
        auto runtime = Instance();
        TrackJobs::Call call(runtime->jobs);
        return runtime->originalJobSubmit(service, output, filename, data, resource, callback, asynchronous);
    }
    static void JobCompleteHook(void* operation) {
        auto runtime = Instance();
        runtime->originalJobComplete(operation);
        runtime->jobs.Complete(operation);
    }
    static void* ProfileConstructorHook(void* profile) {
        auto runtime = Instance();
        void* result = runtime->originalProfileConstructor(profile);
        runtime->profiles.Constructed(reinterpret_cast<uintptr_t>(profile),
                                      reinterpret_cast<uintptr_t>(result), runtime->base);
        return result;
    }
    static void UpdateHook(void* component, float elapsed) { Instance()->Update(component, elapsed); }
    static void SwapHook(void* behavior) { Instance()->Swap(behavior); }
    static void RestoreHook(void* behavior) { Instance()->Restore(behavior); }
    static void DestroyHook(void* component) { Instance()->Destroy(component); }
    static void TargetHook(FH2Context* context, void*) {
        if (Executing() && !Executing()->failed && ExecutingComponent() == Executing()->component)
            TrackTarget::Override(context, nullptr);
    }
    static bool RestoringOwner() {
        auto visit = Executing();
        return visit && !visit->failed && ExecutingComponent() ==
            (visit->target.targetTrack == 542 ? visit->mainlandStartup : visit->component);
    }
    static void FacingHook(FH2Context* context, void*) {
        if (RestoringOwner()) TrackPose::Facing(context, nullptr);
    }
    static void PositionHook(FH2Context* context, void*) {
        if (RestoringOwner()) TrackPose::Position(context, nullptr);
    }
public:
    bool (*originalLaunch)(void*, const char*) = nullptr;
    void* (*originalParse)(void*, void*, const char*) = nullptr;
    void (*originalJobComplete)(void*) = nullptr;
    void* (*originalJobSubmit)(void*, void*, void*, void*, void*, void*, bool) = nullptr;
    void* (*originalProfileConstructor)(void*) = nullptr;
    void (*originalUpdate)(void*, float) = nullptr;
    void (*originalSwap)(void*) = nullptr;
    void (*originalRestore)(void*) = nullptr;
    void (*originalDestroy)(void*) = nullptr;

    explicit Runtime(TrackSave::Gate& gate) : saves(gate) {}
    bool SetGroupPreparation(bool (*prepare)(void*, void*, std::string&), void* user, std::string& error) {
        if (enabled || prepareGroup || !prepare || !user) {
            error = "native group preparation unavailable or already configured";
            return false;
        }
        prepareGroup = prepare;
        preparationUser = user;
        return true;
    }
    bool Install(const FH2ModApi* platform, FH2Mod* owner, uintptr_t gameBase, std::string& error) {
        if (attempted || Instance() || !platform || !owner || !gameBase ||
            !platform->hook_function || !platform->hook_instruction ||
            !platform->enable_hooks || !platform->disable_hooks || !platform->log) {
            error = "native visit binding services unavailable or already installed";
            return false;
        }
        attempted = true;
        struct Check { uintptr_t rva; const unsigned char* bytes; size_t size; };
        const unsigned char launch[] = {0x48, 0x8b, 0xc4, 0x55, 0x48, 0x8d, 0x68, 0xa1};
        const unsigned char update[] = {0x40, 0x53, 0x48, 0x83, 0xec, 0x30};
        const unsigned char swap[] = {0x40, 0x57, 0x48, 0x83, 0xec, 0x70};
        const unsigned char restore[] = {0x48, 0x8b, 0xc4, 0x55, 0x48, 0x8d, 0x68, 0xb8};
        const unsigned char destroy[] = {0x48, 0x89, 0x5c, 0x24, 0x08, 0x57, 0x48, 0x83, 0xec, 0x20};
        const unsigned char parse[] = {0x48,0x8b,0xc4,0x48,0x89,0x50,0x10};
        const Check checks[] = {
            {MachineLaunch, launch, sizeof launch}, {MachineUpdate, update, sizeof update},
            {SwapBegin, swap, sizeof swap}, {TrackPose::RestoreBegin, restore, sizeof restore},
            {MachineDestroy, destroy, sizeof destroy},
            {GroupParse, parse, sizeof parse},
            {TrackJobs::BeginSite, TrackJobs::BeginBytes, sizeof TrackJobs::BeginBytes},
            {TrackJobs::Completion, TrackJobs::CompletionBytes, sizeof TrackJobs::CompletionBytes},
            {TrackJobs::Submit, TrackJobs::SubmitBytes, sizeof TrackJobs::SubmitBytes},
            {TrackJobs::ProfileConstructor, TrackJobs::ProfileConstructorBytes, sizeof TrackJobs::ProfileConstructorBytes},
            {TrackTarget::PreparationSite, TrackTarget::PreparationBytes, sizeof TrackTarget::PreparationBytes},
            {TrackPose::FacingSite, TrackPose::FacingBytes, sizeof TrackPose::FacingBytes},
            {TrackPose::PositionSite, TrackPose::PositionBytes, sizeof TrackPose::PositionBytes},
            {TrackSave::MicroSaveBranch, TrackSave::MicroSaveBytes, sizeof TrackSave::MicroSaveBytes},
            {TrackSave::ProfileSaveBranch, TrackSave::ProfileSaveBytes, sizeof TrackSave::ProfileSaveBytes},
            {TrackSave::ProfileCleanupBranch, TrackSave::ProfileCleanupBytes, sizeof TrackSave::ProfileCleanupBytes},
            {TrackSave::DirectMicroSaveBranch, TrackSave::DirectMicroSaveBytes, sizeof TrackSave::DirectMicroSaveBytes},
        };
        for (const auto& check : checks) {
            unsigned char actual[16] = {};
            if (!Read(gameBase + check.rva, actual, check.size) || memcmp(actual, check.bytes, check.size)) {
                error = "unsupported native visit binding signature at RVA " + std::to_string(check.rva);
                return false;
            }
        }
        api = platform; mod = owner; base = gameBase;
        Instance() = this;
        hooks = {
            api->hook_function(mod, "TrackLoaderCPS initial profile observation boundary",
                               reinterpret_cast<void*>(base + TrackJobs::ProfileConstructor),
                               reinterpret_cast<void*>(&ProfileConstructorHook),
                               reinterpret_cast<void**>(&originalProfileConstructor)),
            api->hook_function(mod, "TrackLoaderCPS persistence submission lifetime",
                               reinterpret_cast<void*>(base + TrackJobs::Submit),
                               reinterpret_cast<void*>(&JobSubmitHook), reinterpret_cast<void**>(&originalJobSubmit)),
            api->hook_instruction(mod, "TrackLoaderCPS persistence operation capture",
                                  reinterpret_cast<void*>(base + TrackJobs::BeginSite), JobBeginHook, nullptr),
            api->hook_function(mod, "TrackLoaderCPS persistence operation completion",
                               reinterpret_cast<void*>(base + TrackJobs::Completion),
                               reinterpret_cast<void*>(&JobCompleteHook), reinterpret_cast<void**>(&originalJobComplete)),
            api->hook_function(mod, "TrackLoaderCPS parsed group ownership",
                               reinterpret_cast<void*>(base + GroupParse),
                               reinterpret_cast<void*>(&ParseHook), reinterpret_cast<void**>(&originalParse)),
            api->hook_function(mod, "TrackLoaderCPS machine launch", reinterpret_cast<void*>(base + MachineLaunch),
                               reinterpret_cast<void*>(&LaunchHook), reinterpret_cast<void**>(&originalLaunch)),
            api->hook_function(mod, "TrackLoaderCPS machine update", reinterpret_cast<void*>(base + MachineUpdate),
                               reinterpret_cast<void*>(&UpdateHook), reinterpret_cast<void**>(&originalUpdate)),
            api->hook_function(mod, "TrackLoaderCPS scoped SwapTrack", reinterpret_cast<void*>(base + SwapBegin),
                               reinterpret_cast<void*>(&SwapHook), reinterpret_cast<void**>(&originalSwap)),
            api->hook_function(mod, "TrackLoaderCPS scoped RestoreCarPosition",
                               reinterpret_cast<void*>(base + TrackPose::RestoreBegin),
                               reinterpret_cast<void*>(&RestoreHook), reinterpret_cast<void**>(&originalRestore)),
            api->hook_function(mod, "TrackLoaderCPS machine destruction",
                               reinterpret_cast<void*>(base + MachineDestroy),
                               reinterpret_cast<void*>(&DestroyHook), reinterpret_cast<void**>(&originalDestroy)),
            api->hook_instruction(mod, "TrackLoaderCPS target arguments",
                                  reinterpret_cast<void*>(base + TrackTarget::PreparationSite), TargetHook, nullptr),
            api->hook_instruction(mod, "TrackLoaderCPS restore facing",
                                  reinterpret_cast<void*>(base + TrackPose::FacingSite), FacingHook, nullptr),
            api->hook_instruction(mod, "TrackLoaderCPS restore position",
                                  reinterpret_cast<void*>(base + TrackPose::PositionSite), PositionHook, nullptr),
            api->hook_instruction(mod, "TrackLoaderCPS visit microsave gate",
                                  reinterpret_cast<void*>(base + TrackSave::MicroSaveBranch), TrackSave::Override, &saves),
            api->hook_instruction(mod, "TrackLoaderCPS visit profile save gate",
                                  reinterpret_cast<void*>(base + TrackSave::ProfileSaveBranch), TrackSave::OverrideProfile, &saves),
            api->hook_instruction(mod, "TrackLoaderCPS visit profile cleanup gate",
                                  reinterpret_cast<void*>(base + TrackSave::ProfileCleanupBranch), TrackSave::OverrideProfile, &saves),
            api->hook_instruction(mod, "TrackLoaderCPS visit direct microsave gate",
                                  reinterpret_cast<void*>(base + TrackSave::DirectMicroSaveBranch), TrackSave::OverrideProfile, &saves),
        };
        for (auto hook : hooks) if (!hook) {
            error = "native visit hook registration failed; all registered hooks remain disabled";
            api->log(mod, error.c_str());
            return false;
        }
        if (!originalProfileConstructor || !originalJobSubmit || !originalJobComplete || !originalParse || !originalLaunch || !originalUpdate ||
            !originalSwap || !originalRestore || !originalDestroy) {
            error = "native visit hook original callbacks unavailable; hooks remain disabled";
            api->log(mod, error.c_str());
            return false;
        }
        installed = true;
        return true;
    }
    bool Enable(std::string& error, bool sessionOnly = false) {
        if (!installed || enabled || saves.Held()) {
            error = "native visit hooks cannot be enabled in the current lifecycle";
            return false;
        }
        if (sessionOnly && !saves.ProtectSession(error)) return false;
        if (!api->enable_hooks(mod, nullptr)) {
            bool rolledBack = api->disable_hooks(mod, nullptr) != 0;
            if (!rolledBack) installed = false;
            error = rolledBack ? "native visit hooks failed to enable; disabled again" :
                                 "native visit hook enable/rollback failed; restart required";
            api->log(mod, error.c_str());
            return false;
        }
        enabled = true;
        return true;
    }
    bool Dispatch(Visit& visit, void (*load)(void*), void* user, std::string& error) {
        if (!enabled || !load || dispatching || bound || !saves.Held() ||
            !TrackTarget::Valid(visit.target) || !TrackPose::Valid(visit.pose) ||
            visit.component || visit.mainlandStartup || visit.failed || visit.swaps || visit.restores ||
            visit.launched || visit.startupLaunched || visit.transitionOwner.Component() || visit.startupOwner.Component() ||
            ((visit.target.sourceTrack == 542) == (visit.target.targetTrack == 542))) {
            error = "native visit dispatch requires enabled hooks, held save gate and a fresh valid visit";
            return false;
        }
        gameThread = std::this_thread::get_id();
        if (!jobs.Idle(error)) return false;
        if (saves.SessionOnly() &&
            (visit.profile != saves.ProfileAddress() || !SessionProfileReady(visit.profile, error))) {
            if (visit.profile != saves.ProfileAddress())
                error = "native session visit profile does not match the held save owner";
            return false;
        }
        dispatching = &visit;
        struct Reset {
            Visit*& pending;
            ~Reset() { pending = nullptr; }
        } reset{dispatching};
        load(user);
        if (!visit.component && !visit.failed)
            Fail(visit, "custom dispatch did not produce a verified owned state machine");
        if (visit.failed) { error = visit.error; return false; }
        return true;
    }
    void* Parse(void* parser, void* output, const char* filename) {
        Visit* visit = dispatching;
        bool startup = false;
        const char* expectedFile = visit && visit->target.targetTrack == 542 ?
            "entities_trackloadercps_return.xml" : "entities_trackloadercps_enter.xml";
        if (!visit && bound && Executing() == bound && ExecutingComponent() == bound->component &&
            bound->target.targetTrack == 542) {
            visit = bound;
            startup = true;
            expectedFile = "entities_trackloadercps_mainland.xml";
        }
        bool matching = visit && filename && !strcmp(filename, expectedFile);
        void* result = originalParse(parser, output, filename);
        if (!matching) return result;
        if (visit->failed) return result;
        if (gameThread != std::this_thread::get_id()) {
            Fail(*visit, "native group parsing moved off the visit dispatch thread");
            return result;
        }
        std::string error;
        const char* expectedFlow = startup ? "trackloadercps.resume_mainland" :
            visit->target.targetTrack == 542 ? "trackloadercps.return_mainland" : "trackloadercps.enter";
        auto& ownership = startup ? visit->startupOwner : visit->transitionOwner;
        if (!ownership.Capture(output, base, expectedFlow, error)) {
            Fail(*visit, "native group ownership failed: " + error);
            return result;
        }
        if (startup) visit->mainlandStartup = ownership.Component();
        else {
            visit->component = ownership.Component();
            bound = visit;
        }
        return result;
    }
    bool Launch(void* component, const char* flow) {
        Visit* visit = bound && (component == bound->component || component == bound->mainlandStartup) ? bound : nullptr;
        if (visit) {
            bool startup = component == visit->mainlandStartup;
            const char* expected = startup ? "trackloadercps.resume_mainland" :
                visit->target.targetTrack == 542 ? "trackloadercps.return_mainland" : "trackloadercps.enter";
            bool& launched = startup ? visit->startupLaunched : visit->launched;
            if (gameThread != std::this_thread::get_id() || !flow || strcmp(flow, expected) ||
                !Flow(component, base, expected) || launched) {
                Fail(*visit, "owned native state machine launch has wrong thread/flow or is repeated");
            }
            if (visit->failed) return false;
            std::string error;
            void* machine = (startup ? visit->startupOwner : visit->transitionOwner).Component();
            if (!prepareGroup || !prepareGroup(preparationUser, machine, error)) {
                Fail(*visit, error.empty() ? "custom lifecycle registry preparation unavailable" : error);
                return false;
            }
            launched = true;
        }
        Execution scope(visit && !visit->failed ? visit : nullptr, component);
        bool result = originalLaunch(component, flow);
        if (visit && !result) Fail(*visit, "native engine rejected the owned custom state machine launch");
        return result;
    }
    void Update(void* component, float elapsed) {
        Visit* visit = bound && (bound->component == component || bound->mainlandStartup == component) ? bound : nullptr;
        if (visit && gameThread != std::this_thread::get_id()) {
            Fail(*visit, "custom state machine moved off its dispatch thread");
            visit = nullptr;
        }
        if (visit && !(component == visit->mainlandStartup ? visit->startupLaunched : visit->launched)) {
            Fail(*visit, "owned native state machine updated before its launch");
            visit = nullptr;
        }
        const char* expected = visit && visit->mainlandStartup == component ? "trackloadercps.resume_mainland" :
            visit && visit->target.targetTrack == 542 ? "trackloadercps.return_mainland" : "trackloadercps.enter";
        if (visit && !Flow(component, base, expected)) {
            Fail(*visit, "bound native state machine changed its target flow");
            visit = nullptr;
        }
        Execution scope(visit && !visit->failed ? visit : nullptr, component);
        originalUpdate(component, elapsed);
    }
    void Swap(void* behavior) {
        Visit* visit = Executing();
        if (!visit || visit->failed) { originalSwap(behavior); return; }
        if (ExecutingComponent() != visit->component) {
            Fail(*visit, "native SwapTrack appeared in mainland startup rather than its transition");
            originalSwap(behavior);
            return;
        }
        if (visit->swaps) {
            Fail(*visit, "duplicate native SwapTrack in the custom transition");
            originalSwap(behavior);
            return;
        }
        TrackTarget::Scope scope(visit->target);
        originalSwap(behavior);
        std::string error;
        if (!scope.Complete(error) || ++visit->swaps != 1) Fail(*visit, "native visit target binding failed: " + error);
        else api->log(mod, ("native SwapTrack bound to track " + std::to_string(visit->target.targetTrack) +
                            " (unstaged descriptor track " + std::to_string(scope.ObservedTrack()) + ")").c_str());
    }
    void Restore(void* behavior) {
        Visit* visit = Executing();
        if (!visit || visit->failed) { originalRestore(behavior); return; }
        void* expected = visit->target.targetTrack == 542 ? visit->mainlandStartup : visit->component;
        if (!expected || ExecutingComponent() != expected) {
            Fail(*visit, "native car restoration appeared outside its bound arrival machine");
            originalRestore(behavior);
            return;
        }
        if (visit->restores) {
            Fail(*visit, "duplicate native car restoration in the custom transition");
            originalRestore(behavior);
            return;
        }
        TrackPose::Scope scope(visit->pose);
        originalRestore(behavior);
        std::string error;
        if (!scope.Complete(error) || ++visit->restores != 1) Fail(*visit, "native visit car restoration failed: " + error);
    }
    void Destroy(void* component) {
        if (bound && (bound->component == component || bound->mainlandStartup == component)) {
            bound->destroyed = true;
            Fail(*bound, "bound custom state machine was destroyed before verified completion");
        }
        Execution scope(nullptr);
        originalDestroy(component);
    }
    bool FinishEntry(Visit& visit, int currentTrack, bool settled, bool gameplayReady, std::string& error) {
        uintptr_t execution = 0;
        if (bound != &visit || visit.failed || visit.destroyed ||
            gameThread != std::this_thread::get_id() || !saves.Held() ||
            visit.target.targetTrack < 10000 || currentTrack != visit.target.targetTrack ||
            !settled || !gameplayReady || visit.swaps != 1 || visit.restores != 1 ||
            !visit.launched ||
            !Read(reinterpret_cast<uintptr_t>(visit.component) + 0xf0, &execution, sizeof execution) ||
            execution) {
            error = "custom entry has not completed with verified native gameplay; binding retained";
            return false;
        }
        bound = nullptr;
        visit.transitionOwner.Reset();
        return true;
    }
    bool ReturnComplete(Visit& visit, std::string& error) const {
        uintptr_t transition = 0, startup = 0;
        if (bound != &visit || visit.failed || visit.destroyed ||
            gameThread != std::this_thread::get_id() || visit.target.targetTrack != 542 ||
            visit.swaps != 1 || visit.restores != 1 || !visit.mainlandStartup ||
            !visit.launched || !visit.startupLaunched ||
            !Read(reinterpret_cast<uintptr_t>(visit.component) + 0xf0, &transition, sizeof transition) ||
            !Read(reinterpret_cast<uintptr_t>(visit.mainlandStartup) + 0xf0, &startup, sizeof startup) ||
            transition || startup) {
            error = "native mainland transition/startup is incomplete; binding and save gate retained";
            return false;
        }
        return true;
    }
    bool FinishReturn(Visit& visit, const TrackSave::ReturnState& state, std::string& error) {
        if (!ReturnComplete(visit, error) || !saves.Release(state, error)) return false;
        bound = nullptr;
        visit.startupOwner.Reset();
        visit.transitionOwner.Reset();
        return true;
    }
    bool FinishSessionReturnObserved(Visit& visit, const TrackLive::CarState& departure,
                                     uintptr_t activePlayerCar, uintptr_t module,
                                     bool gameplayReady, std::string& error) {
        if (!saves.SessionOnly() || !ReturnComplete(visit, error)) {
            if (!saves.SessionOnly()) error = "session-only return requires permanent save protection";
            return false;
        }
        TrackLive::MapState map;
        TrackLive::CarState current;
        TrackLive::MicroSaveState microSave;
        if (!TrackLive::CaptureMap(base, map, error) ||
            !TrackLive::CaptureCar(base, activePlayerCar, current, error) ||
            !TrackLive::VerifyReturn(departure, current, map, error) ||
            !TrackLive::CaptureMicroSave(base, module, microSave, error) ||
            !jobs.Idle(error)) return false;
        if (!microSave.Idle() || !gameplayReady) {
            error = "native session mainland return is not idle/gameplay-ready";
            return false;
        }
        bound = nullptr;
        visit.startupOwner.Reset();
        visit.transitionOwner.Reset();
        return true;
    }
    bool FinishReturnObserved(Visit& visit, const TrackLive::CarState& departure,
                              uintptr_t activePlayerCar, TrackSave::ReturnState state,
                              std::string& error) {
        if (gameThread != std::this_thread::get_id()) {
            error = "native return observation must run on the visit dispatch thread";
            return false;
        }
        TrackLive::MapState map;
        TrackLive::CarState current;
        TrackLive::MicroSaveState microSave;
        if (!TrackLive::CaptureMap(base, map, error) ||
            !TrackLive::CaptureCar(base, activePlayerCar, current, error) ||
            !TrackLive::VerifyReturn(departure, current, map, error) ||
            !TrackLive::CaptureMicroSave(base, state.module, microSave, error)) return false;
        if (!microSave.Idle()) {
            error = "native microsave job remains owned; return save gate retained";
            return false;
        }
        if (!jobs.Idle(error)) return false;
        state.currentTrack = map.current;
        state.targetTrack = map.target;
        state.pendingTrackJob = map.pendingJob;
        state.originalPoseRestored = true;
        state.pendingSaveJob = 0;
        return FinishReturn(visit, state, error);
    }
    bool PersistenceIdle(std::string& error) const { return jobs.Idle(error); }
    bool SessionProtectionActive() const { return enabled && saves.SessionOnly(); }
    bool SessionProfileReady(uintptr_t profile, std::string& error) const {
        if (!enabled || !saves.SessionOnly()) {
            error = "native session profile observation requires active permanent save protection";
            return false;
        }
        return profiles.Contains(profile, error) && jobs.Idle(error);
    }
    Runtime(const Runtime&) = delete;
    Runtime& operator=(const Runtime&) = delete;
};
}
