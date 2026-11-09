#include "..\track_flow.h"
#include <cassert>
#include <cstdlib>
#include "native_group_fixture.h"

static unsigned loads, resolves, frees;
static bool present, failLoad, failVerification, failCompilation, failParse;
static std::string expectedGroup = "trackloadercps";
static std::string expectedPath = "game:\\media\\StateFlow\\trackloadercps.xml";
static unsigned char manager[0x40] = {};
static unsigned char machine[0x128] = {};
static uintptr_t module[0x58 / sizeof(uintptr_t)] = {};
static bool moduleAlive;
static GroupFixture::Control moduleControl{GroupFixture::Table()};
static TrackNative::Shared* MachineModule(void* owner, TrackNative::Shared* output) {
    assert(owner == machine + 0x100);
    if (moduleAlive) output->Retain(module, &moduleControl);
    return output;
}
static void* Construct(void* value) {
    auto builder = static_cast<TrackFlow::Builder*>(value);
    assert(builder->length == 0 && builder->capacity == 15);
    return value;
}
static void* Find(void* registry, const char* name) {
    assert(registry == manager + 0x10 && expectedGroup == name);
    return present ? reinterpret_cast<void*>(123) : nullptr;
}
static void* Load(void* owner, const char* name, void* value) {
    assert(owner == manager && expectedPath == name);
    auto builder = static_cast<TrackFlow::Builder*>(value);
    assert(builder->fields[0] == 456);
    void* memory = malloc(32);
    assert(memory);
    memcpy(memory, "test parse error", 17);
    memcpy(builder->source, &memory, sizeof memory);
    builder->length = 16;
    builder->capacity = 31;
    if (failParse) builder->errors = 1;
    ++loads;
    present = !failLoad && !failVerification;
    return failLoad ? nullptr : reinterpret_cast<void*>(123);
}
static void Resolve(void* groupDefinition, void* value) {
    assert(groupDefinition == reinterpret_cast<void*>(123));
    assert(static_cast<TrackFlow::Builder*>(value)->fields[2] == 123);
    if (failCompilation) static_cast<TrackFlow::Builder*>(value)->errors = 2;
    ++resolves;
}
static void Free(void* memory) { ++frees; free(memory); }

int main() {
    TrackFlow::Registry registry;
    std::string error;
    void* group = machine;
    assert(!registry.Ensure(group, error));
    assert(!registry.Initialize(TrackFlow::Functions{}, error));
    assert(!registry.Initialize({MachineModule, Construct, Load, Resolve, Find, Free, 456, 0}, error));
    assert(registry.Initialize({MachineModule, Construct, Load, Resolve, Find, Free, 456, 789}, error));
    assert(!registry.Initialize({MachineModule, Construct, Load, Resolve, Find, Free, 456, 789}, error));
    assert(!registry.Ensure(nullptr, error));
    assert(!registry.Ensure(group, error));
    assert(error.find("has expired") != std::string::npos);
    moduleAlive = true;
    module[0] = 788;
    assert(!registry.Ensure(group, error));
    assert(error.find("unsupported vtable 788") != std::string::npos);
    module[0] = 789;
    assert(!registry.Ensure(group, error));
    assert(error.find("registry is unavailable") != std::string::npos);
    module[0x50 / sizeof(uintptr_t)] = reinterpret_cast<uintptr_t>(manager);
    assert(registry.Ensure(group, error));
    assert(loads == 1 && resolves == 1 && frees == 1);
    assert(registry.Ensure(group, error));
    assert(loads == 1 && resolves == 2 && frees == 1);
    failCompilation = true;
    assert(!registry.Ensure(group, error));
    assert(error.find("compiler reported 2 errors") != std::string::npos);
    assert(loads == 1 && resolves == 3 && frees == 1);
    failCompilation = false;
    present = false;
    failLoad = true;
    assert(!registry.Ensure(group, error));
    assert(error.find("parser rejected") != std::string::npos);
    assert(loads == 2 && resolves == 3 && frees == 2);
    failLoad = false;
    failVerification = true;
    assert(!registry.Ensure(group, error));
    assert(error.find("could not be verified") != std::string::npos);
    assert(loads == 3 && resolves == 4 && frees == 3);
    present = false;
    failVerification = false;
    failParse = true;
    assert(!registry.Ensure(group, error));
    assert(error.find("compiler reported 1 errors: test parse error") != std::string::npos);
    assert(loads == 4 && resolves == 4 && frees == 4);
    present = failParse = false;
    TrackLifecycle::Names second{"trackloadercps_10001"};
    expectedGroup = second.group;
    expectedPath = "game:\\media\\StateFlow\\" + second.group + ".xml";
    assert(registry.Ensure(group, error, second));
    assert(loads == 5 && resolves == 5 && frees == 5);
    assert(registry.Ensure(group, error, second));
    assert(loads == 5 && resolves == 6 && frees == 5);
    assert(!registry.Ensure(group, error, TrackLifecycle::Names{"global"}));
    assert(moduleControl.strong == 1);
}
