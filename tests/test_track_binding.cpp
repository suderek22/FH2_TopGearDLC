#include "..\track_binding.h"
#include <cassert>
#include <cstdio>
#include <map>
#include "native_group_fixture.h"

static std::map<uintptr_t, void*> detours;
static unsigned registered, enabled, disabled, logs, swaps, restores;
static int failRegistration = -1;
static bool failEnable = false, runSwap = false, runRestore = false;
static bool targetApplied, poseApplied, nested;
static bool launchStartup;
static float restoredX;
static TrackBinding::Runtime* runtime;
static uintptr_t gameBase;
static unsigned char otherComponent[0x150] = {};
static unsigned char startupComponent[0x150] = {};
static GroupFixture* parsedGroup;
static bool deferredEntry, deferredStartup;
static void ParseGroup(GroupFixture& group, const char* filename) {
    parsedGroup = &group;
    GroupFixture::Pair output{};
    reinterpret_cast<void*(*)(void*, void*, const char*)>(detours.at(TrackBinding::GroupParse))(
        nullptr, &output, filename);
}
static void* Parse(void*, void* output, const char*) {
    assert(parsedGroup);
    memcpy(output, &parsedGroup->shared, sizeof parsedGroup->shared);
    return output;
}
static void* ConstructProfile(void* profile) { return profile; }
static GroupFixture startupGroup(startupComponent);

static void Swap(void*) {
    ++swaps;
    FH2Context context = {};
    context.r14 = 542;
    reinterpret_cast<FH2InstructionFn>(detours.at(TrackTarget::PreparationSite))(&context, nullptr);
    targetApplied = context.r14 == 10000;
}
static void Restore(void*) {
    ++restores;
    FH2Context context = {};
    context.rbp = 0x1000;
    reinterpret_cast<FH2InstructionFn>(detours.at(TrackPose::FacingSite))(&context, nullptr);
    reinterpret_cast<FH2InstructionFn>(detours.at(TrackPose::PositionSite))(&context, nullptr);
    restoredX = context.xmm[0].f[0];
    poseApplied = context.xmm[0].f[0] == 202;
}
static bool rejectLaunch, rejectPreparation;
static unsigned launches;
static bool PrepareGroup(void* user, void* group, std::string& error) {
    assert(user == reinterpret_cast<void*>(1) && group);
    if (rejectPreparation) {
        error = "test custom lifecycle registration failure";
        return false;
    }
    return true;
}
static bool Launch(void*, const char* flow) {
    ++launches;
    if (rejectLaunch) return false;
    if (!strcmp(flow, "trackloadercps.resume_mainland")) runtime->Restore(nullptr);
    else if (runSwap) runtime->Swap(nullptr);
    return true;
}
static void Update(void*, float) {
    if (launchStartup) {
        launchStartup = false;
        ParseGroup(startupGroup, "entities_trackloadercps_mainland.xml");
        if (!deferredStartup) runtime->Launch(startupComponent, "trackloadercps.resume_mainland");
        return;
    }
    if (nested) {
        nested = false;
        runtime->Update(otherComponent, 0);
        assert(!targetApplied && !poseApplied);
        nested = true;
    } else {
        if (runSwap) runtime->Swap(nullptr);
        if (runRestore) runtime->Restore(nullptr);
    }
}
static void Destroy(void*) {}
static void CompleteJob(void*) {}
static void* SubmitJob(void* service, void* output, void* filename, void* data,
                       void* resource, void* callback, bool asynchronous) {
    assert(service == reinterpret_cast<void*>(1) && filename == reinterpret_cast<void*>(2));
    assert(data == reinterpret_cast<void*>(3) && resource == reinterpret_cast<void*>(4));
    assert(callback == reinterpret_cast<void*>(5) && asynchronous);
    std::string error;
    assert(!runtime->PersistenceIdle(error));
    return output;
}
static void Log(FH2Mod*, const char*) { ++logs; }
static FH2Hook* Register(uintptr_t address, void* detour) {
    auto index = registered++;
    detours[address - gameBase] = detour;
    if (static_cast<int>(index) == failRegistration) return nullptr;
    return reinterpret_cast<FH2Hook*>(static_cast<uintptr_t>(index + 1));
}
static FH2Hook* Function(FH2Mod*, const char*, void* target, void* detour, void** original) {
    uintptr_t rva = reinterpret_cast<uintptr_t>(target) - gameBase;
    if (rva == TrackBinding::MachineLaunch) *original = reinterpret_cast<void*>(&Launch);
    else if (rva == TrackJobs::ProfileConstructor) *original = reinterpret_cast<void*>(&ConstructProfile);
    else if (rva == TrackBinding::GroupParse) *original = reinterpret_cast<void*>(&Parse);
    else if (rva == TrackJobs::Completion) *original = reinterpret_cast<void*>(&CompleteJob);
    else if (rva == TrackJobs::Submit) *original = reinterpret_cast<void*>(&SubmitJob);
    else if (rva == TrackBinding::MachineUpdate) *original = reinterpret_cast<void*>(&Update);
    else if (rva == TrackBinding::SwapBegin) *original = reinterpret_cast<void*>(&Swap);
    else if (rva == TrackPose::RestoreBegin) *original = reinterpret_cast<void*>(&Restore);
    else if (rva == TrackBinding::MachineDestroy) *original = reinterpret_cast<void*>(&Destroy);
    else assert(false);
    return Register(reinterpret_cast<uintptr_t>(target), detour);
}
static FH2Hook* Instruction(FH2Mod*, const char*, void* target, FH2InstructionFn fn, void*) {
    return Register(reinterpret_cast<uintptr_t>(target), reinterpret_cast<void*>(fn));
}
static int Enable(FH2Mod*, FH2Hook* hook) { assert(!hook); ++enabled; return !failEnable; }
static int Disable(FH2Mod*, FH2Hook* hook) { assert(!hook); ++disabled; return 1; }
static void Component(unsigned char* component, const char* target) {
    uintptr_t vtable = gameBase + TrackBinding::MachineVtable;
    memcpy(component, &vtable, sizeof vtable);
    memcpy(component + 0xb0, &target, sizeof target);
    uint64_t length = strlen(target), capacity = 95;
    memcpy(component + 0xc0, &length, sizeof length);
    memcpy(component + 0xc8, &capacity, sizeof capacity);
}
static void GroupType(GroupFixture& group) {
    uintptr_t table = gameBase + TrackOwnership::GroupVtable;
    memcpy(group.group, &table, sizeof table);
}
static void Load(void* user) {
    auto group = static_cast<GroupFixture*>(user);
    ParseGroup(*group, "entities_trackloadercps_enter.xml");
    if (deferredEntry) return;
    reinterpret_cast<bool(*)(void*, const char*)>(detours.at(TrackBinding::MachineLaunch))(
        *reinterpret_cast<void**>(group->componentNode + 0x10), "trackloadercps.enter");
}
static void LoadReturn(void* user) {
    auto group = static_cast<GroupFixture*>(user);
    ParseGroup(*group, "entities_trackloadercps_return.xml");
    reinterpret_cast<bool(*)(void*, const char*)>(detours.at(TrackBinding::MachineLaunch))(
        *reinterpret_cast<void**>(group->componentNode + 0x10), "trackloadercps.return_mainland");
}
static void LoadNothing(void*) {}

int main(int argc, char** argv) {
    std::vector<unsigned char> image(0x365ad78);
    gameBase = reinterpret_cast<uintptr_t>(image.data());
    const std::pair<uintptr_t, std::vector<unsigned char>> code[] = {
        {TrackJobs::ProfileConstructor, {0x48,0x8b,0xc4,0x48,0x89,0x48,0x08}},
        {TrackBinding::GroupParse, {0x48,0x8b,0xc4,0x48,0x89,0x50,0x10}},
        {TrackJobs::BeginSite, {0x4c,0x8b,0x64,0x24,0x30}},
        {TrackJobs::Completion, {0x48,0x83,0xec,0x28,0x48,0x83,0x79,0x20,0x00}},
        {TrackJobs::Submit, {0x48,0x8b,0xc4,0x4c,0x89,0x48,0x20}},
        {TrackBinding::MachineLaunch, {0x48,0x8b,0xc4,0x55,0x48,0x8d,0x68,0xa1}},
        {TrackBinding::MachineUpdate, {0x40,0x53,0x48,0x83,0xec,0x30}},
        {TrackBinding::SwapBegin, {0x40,0x57,0x48,0x83,0xec,0x70}},
        {TrackPose::RestoreBegin, {0x48,0x8b,0xc4,0x55,0x48,0x8d,0x68,0xb8}},
        {TrackBinding::MachineDestroy, {0x48,0x89,0x5c,0x24,0x08,0x57,0x48,0x83,0xec,0x20}},
        {TrackTarget::PreparationSite, {0x48,0x8b,0x0d,0x49,0x26,0xb7,0x02}},
        {TrackPose::FacingSite, {0xc5,0xf8,0x29,0x45,0xf0}},
        {TrackPose::PositionSite, {0xc5,0xf8,0x29,0x45,0xe0}},
        {TrackSave::MicroSaveBranch, {0x0f,0x84,0xcb,0x01,0x00,0x00}},
        {TrackSave::ProfileSaveBranch, {0x0f,0x84,0x6e,0x04,0x00,0x00}},
        {TrackSave::ProfileCleanupBranch, {0x74,0x3a,0xc6,0x81,0x90,0x08,0x00,0x00,0x00}},
        {TrackSave::DirectMicroSaveBranch, {0x0f,0x84,0x5d,0x03,0x00,0x00}},
    };
    for (const auto& entry : code) memcpy(image.data() + entry.first, entry.second.data(), entry.second.size());
    FH2ModApi api = {};
    api.hook_function = Function; api.hook_instruction = Instruction;
    api.enable_hooks = Enable; api.disable_hooks = Disable; api.log = Log;
    TrackSave::Gate gate;
    TrackBinding::Runtime bridge(gate);
    runtime = &bridge;
    auto mod = reinterpret_cast<FH2Mod*>(1);
    std::string error;
    const char* mode = argc > 1 ? argv[1] : "";
    bool session = !strcmp(mode, "session") || !strcmp(mode, "session-enable");
    if (!strcmp(mode, "signature") || !strcmp(mode, "cleanup-signature") ||
        !strcmp(mode, "parser-signature") || !strcmp(mode, "direct-save-signature") ||
        !strcmp(mode, "job-signature") || !strcmp(mode, "profile-signature")) {
        uintptr_t site = !strcmp(mode, "signature") ? TrackBinding::MachineLaunch :
            !strcmp(mode, "parser-signature") ? TrackBinding::GroupParse :
            !strcmp(mode, "job-signature") ? TrackJobs::Submit :
            !strcmp(mode, "profile-signature") ? TrackJobs::ProfileConstructor :
            !strcmp(mode, "direct-save-signature") ? TrackSave::DirectMicroSaveBranch : TrackSave::ProfileCleanupBranch;
        image[site] ^= 1;
        assert(!bridge.Install(&api, mod, gameBase, error));
        assert(registered == 0 && enabled == 0);
        return 0;
    }
    if (!strcmp(mode, "registration")) failRegistration = 4;
    bool installed = bridge.Install(&api, mod, gameBase, error);
    assert(registered == 17 && enabled == 0);
    if (!strcmp(mode, "registration")) {
        assert(!installed && !bridge.Enable(error) && logs == 1 && enabled == 0);
        return 0;
    }
    assert(installed);
    assert(!bridge.Install(&api, mod, gameBase, error));
    if (strcmp(mode, "preparation-missing"))
        assert(bridge.SetGroupPreparation(PrepareGroup, reinterpret_cast<void*>(1), error));
    if (!strcmp(mode, "enable") || !strcmp(mode, "session-enable")) {
        failEnable = true;
        assert(!bridge.Enable(error, session) && enabled == 1 && disabled == 1 && logs == 1);
        assert(gate.SessionOnly() == session && gate.Held() == session);
        return 0;
    }
    assert(bridge.Enable(error, session));
    unsigned char nativeProfile[0x20] = {};
    uintptr_t profileAddress = 456;
    if (session) {
        uintptr_t profileTable = gameBase + 0x2c5f870;
        memcpy(nativeProfile, &profileTable, sizeof profileTable);
        profileAddress = reinterpret_cast<uintptr_t>(nativeProfile);
        assert(!bridge.SessionProfileReady(profileAddress, error));
        auto constructor = reinterpret_cast<void*(*)(void*)>(detours.at(TrackJobs::ProfileConstructor));
        assert(constructor(nativeProfile) == nativeProfile);
        assert(bridge.SessionProfileReady(profileAddress, error));
        assert(!bridge.SessionProfileReady(profileAddress + 1, error));
    }
    auto submit = reinterpret_cast<void*(*)(void*,void*,void*,void*,void*,void*,bool)>(detours.at(TrackJobs::Submit));
    assert(submit(reinterpret_cast<void*>(1), reinterpret_cast<void*>(6), reinterpret_cast<void*>(2),
                  reinterpret_cast<void*>(3), reinterpret_cast<void*>(4), reinterpret_cast<void*>(5), true) ==
           reinterpret_cast<void*>(6));
    assert(bridge.PersistenceIdle(error));
    unsigned char operation[0x30] = {}, operationStack[0x40] = {};
    GroupFixture::Control operationOwner{GroupFixture::Table()};
    uintptr_t operationVtable = gameBase + TrackJobs::OperationVtable;
    memcpy(operation, &operationVtable, sizeof operationVtable);
    GroupFixture::Put(operationStack, 0x28, operation);
    GroupFixture::Put(operationStack, 0x30, &operationOwner);
    FH2Context operationContext = {};
    operationContext.rsp = reinterpret_cast<uintptr_t>(operationStack);
    auto originalOperationContext = operationContext;
    reinterpret_cast<FH2InstructionFn>(detours.at(TrackJobs::BeginSite))(&operationContext, nullptr);
    assert(!memcmp(&operationContext, &originalOperationContext, sizeof operationContext));
    assert(operationOwner.strong == 2 && !bridge.PersistenceIdle(error));
    reinterpret_cast<void(*)(void*)>(detours.at(TrackJobs::Completion))(operation);
    assert(operationOwner.strong == 1 && bridge.PersistenceIdle(error));
    FH2Context directSave = {};
    directSave.rcx = profileAddress;
    directSave.rdx = 123;
    directSave.r8 = 789;
    directSave.rflags = 0x202;
    auto directOriginal = directSave;
    reinterpret_cast<FH2InstructionFn>(detours.at(TrackSave::DirectMicroSaveBranch))(&directSave, &gate);
    if (session) directOriginal.rflags |= TrackSave::ZeroFlag;
    assert(!memcmp(&directSave, &directOriginal, sizeof directSave));
    unsigned char component[0x150] = {};
    GroupFixture entryGroup(component);
    GroupType(entryGroup);
    GroupType(startupGroup);
    TrackBinding::Visit visit = {};
    visit.target = {542, 542, 10000, {202, 1, 31, 1}, 0};
    visit.pose = {{202, 1, 31, 1}, {1, 0, 0, 0}};
    visit.profile = session ? profileAddress : 0;
    if (!session) assert(!bridge.Dispatch(visit, Load, nullptr, error));
    unsigned char saveModule[0x110] = {};
    uintptr_t saveVtable = gameBase + TrackLive::MicroSaveVtable;
    memcpy(saveModule, &saveVtable, sizeof saveVtable);
    uintptr_t saveAddress = reinterpret_cast<uintptr_t>(saveModule);
    assert(gate.Hold(saveAddress, profileAddress, 0, 0, error));
    FH2Context cleanup = {};
    cleanup.rcx = profileAddress;
    cleanup.rflags = 0x202;
    cleanup.rax = 987;
    cleanup.xmm[6].u64[0] = 654;
    auto expectedCleanup = cleanup;
    expectedCleanup.rflags |= TrackSave::ZeroFlag;
    reinterpret_cast<FH2InstructionFn>(detours.at(TrackSave::ProfileCleanupBranch))(&cleanup, &gate);
    assert(!memcmp(&cleanup, &expectedCleanup, sizeof cleanup));
    assert(gate.BlockedProfiles() == 1 && !gate.UnexpectedModule());
    reinterpret_cast<FH2InstructionFn>(detours.at(TrackSave::DirectMicroSaveBranch))(&directSave, &gate);
    directOriginal.rflags |= TrackSave::ZeroFlag;
    assert(!memcmp(&directSave, &directOriginal, sizeof directSave));
    assert(gate.BlockedProfiles() == 2 && !gate.UnexpectedModule());
    Component(component, "trackloadercps.enter");
    Component(otherComponent, "pause.root");
    assert(TrackBinding::Flow(component, gameBase, "trackloadercps.enter"));
    assert(!TrackBinding::Flow(component, gameBase, "trackloadercps.return_mainland"));
    assert(!TrackBinding::Flow(reinterpret_cast<void*>(1), gameBase, "trackloadercps.enter"));
    if (session) {
        visit.profile = profileAddress + 1;
        assert(!bridge.Dispatch(visit, Load, &entryGroup, error));
        assert(!visit.component && !visit.failed && gate.Held());
        visit.profile = profileAddress;
    }
    {
        TrackTarget::Scope arguments(visit.target);
        TrackPose::Scope pose(visit.pose);
        runSwap = runRestore = true;
        bridge.Update(otherComponent, 0);
        assert(!targetApplied && !poseApplied);
        assert(!arguments.Complete(error) && !pose.Complete(error));
        runSwap = runRestore = false;
    }
    if (!strcmp(mode, "missing")) {
        assert(!bridge.Dispatch(visit, LoadNothing, nullptr, error));
        assert(visit.failed && gate.Held() && logs == 1);
        return 0;
    }
    if (!strcmp(mode, "ownership-failure")) {
        entryGroup.groupControl.strong = 0;
        assert(!bridge.Dispatch(visit, Load, &entryGroup, error));
        assert(visit.failed && gate.Held() && !visit.component && !visit.launched && logs == 1);
        assert(error.find("native group ownership failed") != std::string::npos);
        return 0;
    }
    runSwap = true;
    assert(bridge.Launch(component, "trackloadercps.enter"));
    assert(!targetApplied && !visit.component);
    rejectLaunch = !strcmp(mode, "reject-launch");
    rejectPreparation = !strcmp(mode, "preparation-failure");
    if (rejectLaunch || rejectPreparation || !strcmp(mode, "preparation-missing")) {
        auto previousLaunches = launches;
        assert(!bridge.Dispatch(visit, Load, &entryGroup, error));
        assert(visit.failed && gate.Held() && !targetApplied && logs == 1);
        assert(launches == previousLaunches + (rejectLaunch ? 1 : 0));
        assert(error.find(rejectLaunch ? "native engine rejected" :
            rejectPreparation ? "registration failure" : "preparation unavailable") != std::string::npos);
        return 0;
    }
    deferredEntry = !strcmp(mode, "deferred") || !strcmp(mode, "prelaunch-update");
    assert(bridge.Dispatch(visit, Load, &entryGroup, error));
    if (deferredEntry) {
        assert(!visit.launched && visit.swaps == 0);
        assert(!bridge.FinishEntry(visit, 10000, true, true, error));
        if (!strcmp(mode, "prelaunch-update")) {
            bridge.Update(component, 0);
            assert(visit.failed && gate.Held() && !visit.swaps && logs == 1);
            return 0;
        }
        Component(otherComponent, "trackloadercps.enter");
        targetApplied = false;
        bridge.Launch(otherComponent, "trackloadercps.enter");
        assert(!targetApplied && !visit.launched);
        Component(otherComponent, "pause.root");
        bridge.Launch(component, "trackloadercps.enter");
    }
    assert(visit.component == component && visit.swaps == 1 && targetApplied);
    assert(logs == 1);  // successful SwapTrack binding reports the unstaged descriptor track
    logs = 0;
    assert(entryGroup.groupControl.strong == 2 && entryGroup.componentControl.strong == 2);
    if (!strcmp(mode, "duplicate-launch")) {
        targetApplied = false;
        bridge.Launch(component, "trackloadercps.enter");
        assert(visit.failed && gate.Held() && !targetApplied && logs == 1);
        return 0;
    }
    assert(!TrackBinding::Executing() && !TrackTarget::Current());
    runSwap = false; runRestore = true;
    bridge.Update(component, 0);
    assert(visit.restores == 1 && poseApplied && !visit.failed);
    if (!strcmp(mode, "thread")) {
        std::thread worker([&] { bridge.Update(component, 0); });
        worker.join();
        assert(visit.failed && gate.Held() && logs == 1);
        return 0;
    }
    if (!strcmp(mode, "duplicate")) {
        runSwap = true; runRestore = false;
        targetApplied = false;
        bridge.Update(component, 0);
        assert(visit.failed && gate.Held() && logs == 1 && !targetApplied);
        return 0;
    }
    if (!strcmp(mode, "flow")) {
        Component(component, "pause.root");
        poseApplied = false;
        bridge.Update(component, 0);
        assert(visit.failed && gate.Held() && logs == 1 && !poseApplied);
        return 0;
    }
    runSwap = true; targetApplied = poseApplied = false;
    nested = true;
    bridge.Update(component, 0);
    nested = false;
    assert(!targetApplied && !poseApplied && !visit.failed);
    runSwap = runRestore = false;
    assert(!bridge.FinishEntry(visit, 10000, true, false, error));
    uintptr_t execution = 1;
    memcpy(component + 0xf0, &execution, sizeof execution);
    assert(!bridge.FinishEntry(visit, 10000, true, true, error));
    execution = 0;
    memcpy(component + 0xf0, &execution, sizeof execution);
    if (!strcmp(mode, "destroy")) {
        bridge.Destroy(component);
        assert(visit.destroyed && visit.failed && gate.Held());
        assert(!bridge.FinishEntry(visit, 10000, true, true, error));
        return 0;
    }
    assert(bridge.FinishEntry(visit, 10000, true, true, error));
    assert(entryGroup.groupControl.strong == 1 && entryGroup.componentControl.strong == 1);
    assert(gate.Held());
    runSwap = runRestore = true;
    targetApplied = poseApplied = false;
    bridge.Update(component, 0);
    assert(!targetApplied && !poseApplied);
    assert(swaps >= 3 && restores >= 3);
    runSwap = runRestore = false;
    unsigned char returnComponent[0x150] = {};
    GroupFixture returnGroup(returnComponent);
    GroupType(returnGroup);
    TrackBinding::Visit returned = {};
    returned.target = {10000, 542, 542, {5787, 124, -5929, 1}, 0};
    returned.pose = {{5787, 124, -5929, 1}, {0, 0, -1, 0}};
    returned.profile = session ? profileAddress : 0;
    Component(returnComponent, "trackloadercps.return_mainland");
    Component(startupComponent, "trackloadercps.resume_mainland");
    runSwap = true;
    assert(bridge.Dispatch(returned, LoadReturn, &returnGroup, error));
    assert(returned.swaps == 1 && returned.restores == 0);
    TrackSave::ReturnState mainland = {saveAddress, profileAddress, 542, 542, 0, 0, 0, true, true, true};
    assert(!bridge.FinishReturn(returned, mainland, error));
    assert(gate.Held());
    TrackLive::CarState missingDeparture;
    assert(!bridge.FinishReturnObserved(returned, missingDeparture, 0, mainland, error));
    assert(gate.Held());
    runSwap = false;
    runtime->Launch(startupComponent, "free_roam_start.enter_offline");
    assert(!returned.mainlandStartup && restoredX == 0);
    launchStartup = true;
    deferredStartup = deferredEntry;
    bridge.Update(returnComponent, 0);
    if (deferredStartup) {
        assert(returned.mainlandStartup == startupComponent && !returned.startupLaunched && !returned.restores);
        assert(!bridge.FinishReturn(returned, mainland, error));
        bridge.Launch(startupComponent, "trackloadercps.resume_mainland");
    }
    assert(returned.mainlandStartup == startupComponent && returned.restores == 1);
    assert(restoredX == 5787 && !returned.failed);
    execution = 1;
    memcpy(startupComponent + 0xf0, &execution, sizeof execution);
    assert(!bridge.FinishReturn(returned, mainland, error));
    execution = 0;
    memcpy(startupComponent + 0xf0, &execution, sizeof execution);
    mainland.originalPoseRestored = false;
    assert(!bridge.FinishReturn(returned, mainland, error));
    assert(gate.Held());
    std::vector<unsigned char> liveCar(0x4c10), livePhysics(0x400);
    uintptr_t carAddress = reinterpret_cast<uintptr_t>(liveCar.data());
    uintptr_t physicsAddress = reinterpret_cast<uintptr_t>(livePhysics.data() + 0x390);
    uintptr_t carTable = gameBase + TrackLive::CarVtable, physicsTable = gameBase + TrackLive::PhysicsVtable;
    memcpy(liveCar.data(), &carTable, sizeof carTable);
    memcpy(liveCar.data() + 0x4c08, &physicsAddress, sizeof physicsAddress);
    memcpy(livePhysics.data() + 0x390, &physicsTable, sizeof physicsTable);
    uintptr_t positionGetter = gameBase + 0x12cd150, facingGetter = gameBase + 0x12cbed0;
    memcpy(reinterpret_cast<void*>(physicsTable + 0x270), &positionGetter, sizeof positionGetter);
    memcpy(reinterpret_cast<void*>(physicsTable + 0x970), &facingGetter, sizeof facingGetter);
    const unsigned char positionBytes[] = {0xc5,0xf8,0x28,0x81,0x70,0xfc,0xff,0xff};
    const unsigned char facingBytes[] = {0x49,0x63,0xc0,0x48,0x83,0xe8,0x38,0x48,0x03,0xc0};
    memcpy(reinterpret_cast<void*>(positionGetter), positionBytes, sizeof positionBytes);
    memcpy(reinterpret_cast<void*>(facingGetter), facingBytes, sizeof facingBytes);
    memcpy(livePhysics.data(), returned.pose.position, sizeof returned.pose.position);
    memcpy(livePhysics.data() + 0x30, returned.pose.facing, sizeof returned.pose.facing);
    int mapFields[6] = {0,0,542,542,0,0};
    uintptr_t mapAddress = reinterpret_cast<uintptr_t>(mapFields);
    memcpy(image.data() + TrackLive::TrackManager, &mapAddress, sizeof mapAddress);
    TrackLive::CarState departure;
    assert(TrackLive::CaptureCar(gameBase, carAddress, departure, error));
    float wrongX = returned.pose.position[0] + 1;
    memcpy(livePhysics.data(), &wrongX, sizeof wrongX);
    assert(!bridge.FinishReturnObserved(returned, departure, carAddress, mainland, error));
    assert(gate.Held() && returnGroup.groupControl.strong == 2);
    memcpy(livePhysics.data(), returned.pose.position, sizeof returned.pose.position);
    mainland.module = 123;
    assert(!bridge.FinishReturnObserved(returned, departure, carAddress, mainland, error));
    assert(gate.Held());
    mainland.module = saveAddress;
    uintptr_t job = 1;
    memcpy(saveModule + 0x108, &job, sizeof job);
    assert(!bridge.FinishReturnObserved(returned, departure, carAddress, mainland, error));
    unsigned char emptyJobControl[0x18] = {};
    uintptr_t emptyJobTable = gameBase + TrackLive::NullJobControlVtable;
    LONG emptyJobStrong = 1;
    memcpy(emptyJobControl, &emptyJobTable, sizeof emptyJobTable);
    memcpy(emptyJobControl + 8, &emptyJobStrong, sizeof emptyJobStrong);
    job = reinterpret_cast<uintptr_t>(emptyJobControl);
    memcpy(saveModule + 0x108, &job, sizeof job);
    mainland.currentTrack = mainland.targetTrack = 10000;
    mainland.pendingTrackJob = 1;
    mainland.pendingSaveJob = 1;
    mainland.pendingProfileJob = 1;
    reinterpret_cast<FH2InstructionFn>(detours.at(TrackJobs::BeginSite))(&operationContext, nullptr);
    assert(!bridge.FinishReturnObserved(returned, departure, carAddress, mainland, error));
    assert(gate.Held() && operationOwner.strong == 2);
    reinterpret_cast<void(*)(void*)>(detours.at(TrackJobs::Completion))(operation);
    assert(!bridge.FinishReturnObserved(returned, departure, carAddress, mainland, error));
    assert(gate.Held());
    mainland.pendingProfileJob = 0;
    if (session) {
        assert(!bridge.FinishReturnObserved(returned, departure, carAddress, mainland, error));
        assert(!bridge.FinishSessionReturnObserved(returned, departure, carAddress, saveAddress, false, error));
        wrongX = returned.pose.position[0] + 1;
        memcpy(livePhysics.data(), &wrongX, sizeof wrongX);
        assert(!bridge.FinishSessionReturnObserved(returned, departure, carAddress, saveAddress, true, error));
        memcpy(livePhysics.data(), returned.pose.position, sizeof returned.pose.position);
        reinterpret_cast<FH2InstructionFn>(detours.at(TrackJobs::BeginSite))(&operationContext, nullptr);
        assert(!bridge.FinishSessionReturnObserved(returned, departure, carAddress, saveAddress, true, error));
        reinterpret_cast<void(*)(void*)>(detours.at(TrackJobs::Completion))(operation);
        assert(bridge.FinishSessionReturnObserved(returned, departure, carAddress, saveAddress, true, error));
        assert(gate.Held() && gate.SessionOnly());
        assert(!gate.Release(mainland, error));
        assert(returnGroup.groupControl.strong == 1 && returnGroup.componentControl.strong == 1);
        assert(startupGroup.groupControl.strong == 1 && startupGroup.componentControl.strong == 1);
        puts("Native session-only entry/return releases machines, never save protection.");
        return 0;
    }
    assert(bridge.FinishReturnObserved(returned, departure, carAddress, mainland, error));
    assert(!gate.Held());
    assert(returnGroup.groupControl.strong == 1 && returnGroup.componentControl.strong == 1);
    assert(startupGroup.groupControl.strong == 1 && startupGroup.componentControl.strong == 1);
    assert(!TrackBinding::Executing() && !TrackBinding::ExecutingComponent());
    puts("Native hook registration, scoped machine dispatch and callback isolation tests passed.");
}
