#include "..\track_player.h"
#include "native_group_fixture.h"
#include <vector>
#include <cstdio>

static void* primary;
static void* currentCar;
static GroupFixture* global;
static int playerRefs = 1, carRefs = 1;
static bool noGlobal = false, noSystem = false;
static bool noLookupContext, noLookupSystem;
static void* Context(void*) { return noLookupContext ? nullptr : global; }
static TrackNative::Shared* EntitySystem(void* context, TrackNative::Shared* output) {
    assert(context == global);
    if (!noLookupSystem) output->Retain(global->shared.object, global->shared.control);
    return output;
}
static TrackNative::Shared* Global(void* system, TrackNative::Shared* output, const void* value) {
    assert(system == global->shared.object);
    auto name = static_cast<const TrackPlayer::GlobalName*>(value);
    assert(!strcmp(name->text, "global") && name->length == 6 && name->capacity == 15);
    if (!noGlobal) output->Retain(global->shared.object, global->shared.control);
    return output;
}
static void* Primary(void*, void** output) {
    *output = primary;
    if (primary) ++playerRefs;
    return output;
}
static void* System(void*, void*) { return noSystem ? nullptr : global; }
static void* PlayerCar(void* system, void** output) {
    assert(system == global);
    *output = currentCar;
    if (currentCar) ++carRefs;
    return output;
}
static void DropPlayer(void* value) { assert(value); --playerRefs; }
static void DropCar(void* value) { assert(value); --carRefs; }
static void Put(void* value, size_t offset, uintptr_t field) {
    memcpy(static_cast<unsigned char*>(value) + offset, &field, sizeof field);
}
int main() {
    std::vector<unsigned char> image(0x365ad78);
    uintptr_t base = reinterpret_cast<uintptr_t>(image.data());
    unsigned char player[0x340] = {}, profile[0x20] = {}, replacement[0x20] = {}, module[0x110] = {};
    Put(player, 0, base + TrackPlayer::PlayerVtable);
    Put(player, 0x328, reinterpret_cast<uintptr_t>(profile));
    Put(profile, 0, base + TrackPlayer::ProfileVtable);
    Put(replacement, 0, base + TrackPlayer::ProfileVtable);
    Put(module, 0, base + TrackLive::MicroSaveVtable);
    Put(image.data(), TrackPlayer::PlayerVtable + 0xc8, base + 0x324060);
    primary = player;
    GroupFixture fixture(module, TrackLive::MicroSaveVtable);
    global = &fixture;
    TrackPlayer::Functions functions{Context, EntitySystem, Global, Primary, System, PlayerCar, DropPlayer, DropCar};
    std::string error;
    TrackPlayer::Owners owners;
    assert(!owners.Capture(error));
    assert(!owners.Initialize(0, error));
    assert(!owners.Initialize(0, functions, error));
    assert(owners.Initialize(base, functions, error));
    assert(!owners.Initialize(base, functions, error));
    noLookupContext = true;
    assert(!owners.Capture(error) && error.find("context unavailable") != std::string::npos);
    noLookupContext = false;
    noLookupSystem = true;
    assert(!owners.Capture(error) && error.find("entity system unavailable") != std::string::npos);
    noLookupSystem = false;
    noGlobal = true;
    assert(!owners.Capture(error));
    assert(error == "native named global group is unavailable");
    noGlobal = false;
    primary = nullptr;
    assert(!owners.Capture(error) && playerRefs == 1 && fixture.groupControl.strong == 1);
    primary = player;
    assert(owners.Capture(error));
    assert(playerRefs == 2 && fixture.groupControl.strong == 2 && fixture.componentControl.strong == 2);
    assert(owners.Module() == reinterpret_cast<uintptr_t>(module));
    assert(owners.ProfileAddress() == reinterpret_cast<uintptr_t>(profile));
    assert(!owners.Capture(error));
    assert(owners.Validate(error) && playerRefs == 2);
    noGlobal = true;
    assert(!owners.Validate(error) && error == "native named global group is unavailable");
    noGlobal = false;
    Put(player, 0x328, reinterpret_cast<uintptr_t>(replacement));
    assert(!owners.Validate(error) && playerRefs == 2);
    Put(player, 0x328, reinterpret_cast<uintptr_t>(profile));
    primary = nullptr;
    assert(!owners.Validate(error) && playerRefs == 2);
    primary = player;
    GroupFixture changed(module, TrackLive::MicroSaveVtable);
    global = &changed;
    assert(owners.Validate(error));
    unsigned char newModule[0x110] = {};
    Put(newModule, 0, base + TrackLive::MicroSaveVtable);
    GroupFixture changedModule(newModule, TrackLive::MicroSaveVtable);
    global = &changedModule;
    assert(!owners.Validate(error));
    global = &fixture;

    std::vector<unsigned char> car(0x4d10), physics(0x400);
    currentCar = car.data();
    Put(car.data(), 0, base + TrackLive::CarVtable);
    Put(car.data(), 0x4c08, reinterpret_cast<uintptr_t>(physics.data() + 0x390));
    Put(car.data(), 0x4d00, reinterpret_cast<uintptr_t>(player));
    Put(physics.data(), 0x390, base + TrackLive::PhysicsVtable);
    Put(image.data(), TrackLive::PhysicsVtable + 0x270, base + 0x12cd150);
    Put(image.data(), TrackLive::PhysicsVtable + 0x970, base + 0x12cbed0);
    const unsigned char position[] = {0xc5,0xf8,0x28,0x81,0x70,0xfc,0xff,0xff};
    const unsigned char facing[] = {0x49,0x63,0xc0,0x48,0x83,0xe8,0x38,0x48,0x03,0xc0};
    memcpy(image.data() + 0x12cd150, position, sizeof position);
    memcpy(image.data() + 0x12cbed0, facing, sizeof facing);
    TrackPose::Pose pose{{202,0,31,1},{0,0,1,0}};
    memcpy(physics.data(), pose.position, sizeof pose.position);
    memcpy(physics.data() + 0x30, pose.facing, sizeof pose.facing);
    Put(image.data(), 0x34aba60, reinterpret_cast<uintptr_t>(&fixture));
    TrackLive::CarState state;
    {
        TrackPlayer::Car ownedCar;
        assert(owners.CaptureCar(ownedCar, state, error));
        assert(ownedCar.Address() == reinterpret_cast<uintptr_t>(currentCar) && carRefs == 2);
        auto previous = state;
        noSystem = true;
        assert(!owners.CaptureCar(ownedCar, state, error) && carRefs == 2);
        assert(!memcmp(&previous, &state, sizeof state));
        noSystem = false;
        currentCar = nullptr;
        assert(!owners.CaptureCar(ownedCar, state, error) && carRefs == 2);
        currentCar = car.data();
        Put(car.data(), 0x4d00, 1);
        assert(!owners.CaptureCar(ownedCar, state, error) && carRefs == 2);
        Put(car.data(), 0x4d00, reinterpret_cast<uintptr_t>(player));
        car[0] ^= 1;
        assert(!owners.CaptureCar(ownedCar, state, error) && carRefs == 2);
        car[0] ^= 1;
        assert(owners.CaptureCar(ownedCar, state, error) && carRefs == 2);
        ownedCar.Reset();
        ownedCar.Reset();
        assert(carRefs == 1);
    }
    owners.Reset();
    owners.Reset();
    assert(!owners.Validate(error) && playerRefs == 1 && carRefs == 1);
    assert(fixture.groupControl.strong == 1 && fixture.componentControl.strong == 1);
    profile[0] ^= 1;
    assert(!owners.Capture(error) && playerRefs == 1);
    assert(fixture.groupControl.strong == 1 && fixture.componentControl.strong == 1);
    puts("Native primary player/global microsave ownership, borrowed profile revalidation and balanced car lifetime passed.");
}
