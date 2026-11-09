#pragma once
#include "track_ownership.h"
#include "track_lifecycle.h"

namespace TrackFlow {
constexpr uintptr_t BuilderConstructor = 0x191fdf0;
constexpr uintptr_t BuilderVtable = 0x2ca16f8;
constexpr uintptr_t LoadFile = 0x867e50;
constexpr uintptr_t ValidateGroup = 0x1928b10;
constexpr uintptr_t FindGroup = 0x1922e20;
constexpr uintptr_t MachineModule = 0x3eb640;
constexpr uintptr_t MachineModuleVtable = 0x2ca1740;

struct Builder {
    uintptr_t fields[14] = {};
    uint32_t errors = 0;
    uint32_t reserved = 0;
    char source[16] = {};
    size_t length = 0;
    size_t capacity = 15;
};
static_assert(offsetof(Builder, source) == 0x78 && sizeof(Builder) == 0x98,
              "Native stateflow builder ABI");

struct Functions {
    TrackNative::Shared* (*machineModule)(void*, TrackNative::Shared*);
    void* (*construct)(void*);
    void* (*load)(void*, const char*, void*);
    void (*validate)(void*, void*);
    void* (*find)(void*, const char*);
    void (*freeMemory)(void*);
    uintptr_t builderVtable;
    uintptr_t moduleVtable;
};

class Registry {
    Functions functions = {};
    bool ready = false;
    static bool CompilerFailed(const Builder& builder, std::string& error) {
        if (!builder.errors) return false;
        error = "native custom lifecycle compiler reported " + std::to_string(builder.errors) + " errors";
        if (builder.length) {
            uintptr_t address = reinterpret_cast<uintptr_t>(builder.source);
            if (builder.capacity >= 16) memcpy(&address, builder.source, sizeof address);
            char text[513] = {};
            size_t length = builder.length > 512 ? 512 : builder.length;
            if (builder.length > builder.capacity || !TrackOwnership::Read(address, text, length))
                error += "; compiler error text unreadable";
            else error += ": " + std::string(text, length);
        }
        return true;
    }
public:
    bool Initialize(uintptr_t base, std::string& error) {
        const struct { uintptr_t rva; unsigned char bytes[6]; } checks[] = {
            {BuilderConstructor, {0x48,0x8d,0x05,0xe9,0x18,0x38}},
            {LoadFile, {0x48,0x8b,0xc4,0x55,0x41,0x56}},
            {ValidateGroup, {0x48,0x89,0x5c,0x24,0x10,0x48}},
            {0x1928bc0, {0x48,0x89,0x5c,0x24,0x10,0x57}},
            {0x1924d8a, {0xff,0x41,0x70,0x4d,0x8b,0xc8}},
            {MachineModule, {0x40,0x53,0x48,0x83,0xec,0x20}},
            {0x3eb646, {0x48,0x8b,0xda,0xe8,0x82,0x54}},
            {0x400ad0, {0x40,0x53,0x48,0x83,0xec,0x20}},
            {0x400ad6, {0x48,0x83,0xc1,0x18,0x48,0x8b}},
            {0x563fe6, {0x48,0x8d,0x8e,0x00,0x01,0x00}},
            {0x563ff2, {0x48,0x8b,0x08,0x4c,0x8b,0x71}},
            {FindGroup, {0x4c,0x8b,0x49,0x10,0x4c,0x8b}},
            {0x5a651f, {0x48,0x8d,0x05,0xd2,0xb1,0x6f}},
            {0x5a6596, {0x48,0x8b,0x4b,0x50,0x48,0x83}},
            {0x31c7b0, {0x48,0x85,0xc9,0x74,0x1d,0x53}},
        };
        for (const auto& check : checks) {
            unsigned char actual[6] = {};
            if (!base || !TrackOwnership::Read(base + check.rva, actual, sizeof actual) ||
                memcmp(actual, check.bytes, sizeof actual)) {
                error = "unsupported native stateflow registration signature at RVA " + std::to_string(check.rva);
                return false;
            }
        }
        return Initialize({
            reinterpret_cast<decltype(functions.machineModule)>(base + MachineModule),
            reinterpret_cast<decltype(functions.construct)>(base + BuilderConstructor),
            reinterpret_cast<decltype(functions.load)>(base + LoadFile),
            reinterpret_cast<decltype(functions.validate)>(base + ValidateGroup),
            reinterpret_cast<decltype(functions.find)>(base + FindGroup),
            reinterpret_cast<decltype(functions.freeMemory)>(base + 0x31c7b0),
            base + BuilderVtable,
            base + MachineModuleVtable,
        }, error);
    }
    bool Initialize(const Functions& native, std::string& error) {
        if (ready || !native.machineModule || !native.construct || !native.load || !native.validate ||
            !native.find || !native.freeMemory || !native.builderVtable || !native.moduleVtable) {
            error = "native stateflow registration services unavailable or already initialized";
            return false;
        }
        functions = native;
        ready = true;
        return true;
    }
    bool Ensure(void* machine, std::string& error, const TrackLifecycle::Names& lifecycle = {}) {
        uintptr_t manager = 0, table = 0;
        uintptr_t registry[3] = {};
        if (!ready || !machine || !TrackLifecycle::Valid(lifecycle.group)) {
            error = "owned state machine component is unavailable";
            return false;
        }
        TrackNative::Shared module;
        // Mirror native launch 0x563F90: machine+0x100 -> GameStateMachineModule weak owner.
        functions.machineModule(reinterpret_cast<void*>(reinterpret_cast<uintptr_t>(machine) + 0x100), &module);
        if (!module.object || !module.control) {
            error = "owned state machine's GameStateMachineModule owner has expired";
            return false;
        }
        uintptr_t object = reinterpret_cast<uintptr_t>(module.object);
        if (!TrackOwnership::Read(object, &table, sizeof table) || table != functions.moduleVtable) {
            error = "owned state machine module has unsupported vtable " + std::to_string(table);
            return false;
        }
        if (!TrackOwnership::Read(object + 0x50, &manager, sizeof manager) || !manager ||
            !TrackOwnership::Read(manager + 0x10, registry, sizeof registry)) {
            error = "GameStateMachineModule stateflow registry is unavailable";
            return false;
        }
        void* groups = reinterpret_cast<void*>(manager + 0x10);
        void* loaded = functions.find(groups, lifecycle.group.c_str());
        struct BuilderOwner {
            Builder value;
            void (*freeMemory)(void*);
            ~BuilderOwner() {
                if (value.capacity >= 16) {
                    void* memory = nullptr;
                    memcpy(&memory, value.source, sizeof memory);
                    freeMemory(memory);
                }
            }
        } builder{{}, functions.freeMemory};
        functions.construct(&builder.value);
        builder.value.fields[0] = functions.builderVtable;
        // Mirror native group initialization, but load only our pinned additive file.
        std::string path = "game:\\media\\StateFlow\\" + lifecycle.group + ".xml";
        if (!loaded) loaded = functions.load(reinterpret_cast<void*>(manager),
            path.c_str(), &builder.value);
        if (CompilerFailed(builder.value, error)) return false;
        if (!loaded) {
            error = "native stateflow parser rejected the custom lifecycle resource";
            return false;
        }
        // Native validation sets builder+0x10 around this per-group visitor.
        builder.value.fields[2] = reinterpret_cast<uintptr_t>(loaded);
        functions.validate(loaded, &builder.value);
        builder.value.fields[2] = 0;
        if (CompilerFailed(builder.value, error)) return false;
        if (functions.find(groups, lifecycle.group.c_str()) != loaded) {
            error = "native custom stateflow registration could not be verified";
            return false;
        }
        return true;
    }
};
}
