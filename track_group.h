#pragma once
#include "track_binding.h"
#include <cstddef>

namespace TrackGroup {
using Shared = TrackNative::Shared;
struct NativeString {
    char bytes[16] = {};
    size_t length = 0;
    size_t capacity = 15;
};
static_assert(sizeof(NativeString) == 32, "Native string ABI");

struct Functions {
    void* (*context)(void*);
    Shared* (*entitySystem)(void*, Shared*);
    Shared* (*groupManager)(void*, Shared*);
    void* (*stringAssign)(NativeString*, const char*, size_t);
    void (*freeMemory)(void*);
    Shared* (*load)(void*, Shared*, NativeString*, Shared*, bool, bool);
};

class Loader {
    Functions functions = {};
    Shared receipt;
    bool ready = false;
public:
    bool Initialize(uintptr_t base, std::string& error) {
        const struct { uintptr_t rva; unsigned char bytes[6]; } checks[] = {
            {0x19242b0, {0x8b,0x0d,0x22,0xa9,0xc2,0x01}},
            {0x867dc0, {0x40,0x53,0x48,0x83,0xec,0x30}},
            {0x52bab0, {0x40,0x53,0x48,0x83,0xec,0x30}},
            {0x22c300, {0x48,0x89,0x5c,0x24,0x08,0x48}},
            {0x31c7b0, {0x48,0x85,0xc9,0x74,0x1d,0x53}},
            {0x50a6b0, {0x4c,0x89,0x4c,0x24,0x20,0x4c}},
        };
        for (const auto& check : checks) {
            unsigned char actual[6] = {};
            if (!base || !TrackBinding::Read(base + check.rva, actual, sizeof actual) ||
                memcmp(actual, check.bytes, sizeof actual)) {
                error = "unsupported native group loader signature at RVA " + std::to_string(check.rva);
                return false;
            }
        }
        return Initialize({
            reinterpret_cast<decltype(functions.context)>(base + 0x19242b0),
            reinterpret_cast<decltype(functions.entitySystem)>(base + 0x867dc0),
            reinterpret_cast<decltype(functions.groupManager)>(base + 0x52bab0),
            reinterpret_cast<decltype(functions.stringAssign)>(base + 0x22c300),
            reinterpret_cast<decltype(functions.freeMemory)>(base + 0x31c7b0),
            reinterpret_cast<decltype(functions.load)>(base + 0x50a6b0),
        }, error);
    }
    bool Initialize(const Functions& native, std::string& error) {
        if (ready || !native.context || !native.entitySystem || !native.groupManager ||
            !native.stringAssign || !native.freeMemory || !native.load) {
            error = "native group loader callbacks unavailable or already initialized";
            return false;
        }
        functions = native;
        ready = true;
        return true;
    }
    bool Submit(bool returning, std::string& error) {
        if (!ready || receipt.control) {
            error = "native group loader unavailable or previous receipt is still held";
            return false;
        }
        void* context = functions.context(nullptr);
        if (!context) {
            error = "native stateflow thread context unavailable";
            return false;
        }
        Shared system, manager, parent;
        functions.entitySystem(context, &system);
        if (!system.object || !system.control) {
            error = "native entity system unavailable";
            return false;
        }
        functions.groupManager(system.object, &manager);
        if (!manager.object || !manager.control) {
            error = "native entity group manager unavailable";
            return false;
        }
        const char* filename = returning ? "entities_trackloadercps_return" : "entities_trackloadercps_enter";
        struct StringOwner {
            NativeString value;
            void (*freeMemory)(void*);
            ~StringOwner() {
                if (value.capacity >= 16) {
                    void* memory = nullptr;
                    memcpy(&memory, value.bytes, sizeof memory);
                    freeMemory(memory);
                }
            }
        } name{{}, functions.freeMemory};
        functions.stringAssign(&name.value, filename, strlen(filename));
        // The native loader consumes/reset its filename and parent shared reference.
        functions.load(manager.object, &receipt, &name.value, &parent, true, false);
        if (!receipt.control) {
            error = "native group loader produced no ownership receipt";
            return false;
        }
        return true;
    }
    void ReleaseReceipt() { receipt.Reset(); }
    bool HasReceipt() const { return receipt.control != nullptr; }
    Loader() = default;
    Loader(const Loader&) = delete;
    Loader& operator=(const Loader&) = delete;
};
}
