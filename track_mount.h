#pragma once
#include <windows.h>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

// Mounts track archives at runtime like the DLC folder mounter (game+0xFD24A0).
// FH2 only reads Mount\media\zipmanifest.xml at boot, before mod overrides exist.
namespace TrackMount {
constexpr uintptr_t FileSystem = 0x365ab28;
constexpr uintptr_t FileSystemVtable = 0x2e6ed18;
constexpr uintptr_t ArchiveConstructor = 0x11dfda0;
constexpr uintptr_t ArchiveVtable = 0x2ead838;
constexpr uintptr_t Allocate = 0x3229c0;
constexpr uintptr_t Free = 0x31c7b0;
constexpr uintptr_t Assign = 0x22c300;
constexpr uintptr_t Qualify = 0x11fb470;
constexpr uintptr_t Config = 0x295940;
constexpr uintptr_t PriorityBase = 0x24a4;
constexpr uint32_t ArchiveFlags = 0x8000001b;
constexpr int ManifestPriority = 80;

struct NativeString {
    union { char text[16]; char* heap; };
    size_t size;
    size_t capacity;
    const char* c_str() const { return capacity >= 16 ? heap : text; }
};
static_assert(sizeof(NativeString) == 32, "MSVC std::string ABI");

struct Functions {
    void* (*allocate)(size_t);
    void (*free)(void*);
    void* (*assign)(NativeString*, const char*, size_t);
    void* (*construct)(void*, NativeString*, NativeString*, uint32_t, uint32_t, uint32_t, uint32_t);
    void (*qualify)(void*, NativeString*, int);
    uint8_t* (*config)();
};

inline bool Readable(uintptr_t address, void* out, size_t size) {
    SIZE_T got = 0;
    return address && ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(address), out, size, &got) &&
        got == size;
}

class Mounter {
    Functions fn = {};
    uintptr_t base = 0;
    void Init(NativeString& s, const std::string& value) {
        memset(&s, 0, sizeof s);
        s.capacity = 15;
        if (!value.empty()) fn.assign(&s, value.data(), value.size());
    }
    void Destroy(NativeString& s) {
        if (s.capacity >= 16) fn.free(s.heap);
        memset(&s, 0, sizeof s);
        s.capacity = 15;
    }
    static void* Slot(void* object, size_t offset) {
        return (*reinterpret_cast<void***>(object))[offset / sizeof(void*)];
    }
    static void AddRef(void* object) { reinterpret_cast<void (*)(void*)>(Slot(object, 0x10))(object); }
    static void Release(void* object) { reinterpret_cast<void (*)(void*)>(Slot(object, 0x18))(object); }

public:
    bool Initialize(uintptr_t gameBase, std::string& error) {
        const struct { uintptr_t rva; unsigned char bytes[6]; } checks[] = {
            {0xfd0690, {0x48,0x8d,0x05,0x91,0xa4,0x68}},  // file system singleton getter
            {ArchiveConstructor, {0x48,0x8b,0xc4,0x48,0x89,0x48}},
            {0xfd220d, {0xe8,0x8e,0xdb,0x20,0x00,0x48}},  // DLC/boot archive construction
            {0xfd22ba, {0xff,0x90,0xe8,0x01,0x00,0x00}},  // boot archive mount slot
            {0xfd1923, {0x48,0x8b,0x4d,0x98,0x48,0x8b}},  // boot lookup result
            {0xfd192a, {0xff,0x90,0xc0,0x01,0x00,0x00}},
            {0xfd193a, {0xe8,0x31,0x9b,0x22,0x00,0x4c}},  // boot path qualification
            {Allocate, {0x48,0x89,0x5c,0x24,0x08,0x57}},
            {Free, {0x48,0x85,0xc9,0x74,0x1d,0x53}},
            {Assign, {0x48,0x89,0x5c,0x24,0x08,0x48}},
            {Qualify, {0x48,0x89,0x5c,0x24,0x08,0x48}},
            {Config, {0x40,0x53,0x48,0x83,0xec,0x30}},
        };
        for (const auto& check : checks) {
            unsigned char actual[6] = {};
            if (!Readable(gameBase + check.rva, actual, sizeof actual) || memcmp(actual, check.bytes, sizeof actual)) {
                error = "unsupported native archive mount signature at RVA " + std::to_string(check.rva);
                return false;
            }
        }
        base = gameBase;
        fn.allocate = reinterpret_cast<decltype(fn.allocate)>(base + Allocate);
        fn.free = reinterpret_cast<decltype(fn.free)>(base + Free);
        fn.assign = reinterpret_cast<decltype(fn.assign)>(base + Assign);
        fn.construct = reinterpret_cast<decltype(fn.construct)>(base + ArchiveConstructor);
        fn.qualify = reinterpret_cast<decltype(fn.qualify)>(base + Qualify);
        fn.config = reinterpret_cast<decltype(fn.config)>(base + Config);
        return true;
    }

    // path: lower-case "game:\media\tracks\<media>\<name>.zip|.minizip"; resolved receives the device path.
    // The native lookup only indexes files present (or zipmanifest-listed) at boot; runtime-published
    // overrides are therefore opened directly from game:\mediaoverride when present under gameDir.
    bool Mount(const std::string& path, const std::wstring& gameDir, std::string& resolvedOut, std::string& error) {
        size_t dot = path.rfind('.'), slash = path.rfind('\\');
        if (!base || dot == std::string::npos || slash == std::string::npos || dot < slash ||
            path.compare(0, 6, "game:\\") != 0) {
            error = "invalid archive mount path " + path;
            return false;
        }
        void* fs = nullptr;
        uintptr_t vtable = 0;
        if (!Readable(base + FileSystem, &fs, sizeof fs) || !fs || !Readable(reinterpret_cast<uintptr_t>(fs), &vtable, sizeof vtable) ||
            vtable != base + FileSystemVtable) {
            error = "native file system is unavailable";
            return false;
        }
        NativeString gamePath, resolved, mountPath, empty;
        Init(gamePath, path);
        Init(resolved, "");
        Init(mountPath, path.substr(0, dot) + "\\");
        Init(empty, "");
        bool ok = false;
        void* found = nullptr;
        void* archive = nullptr;
        using Lookup = void (*)(void*, void**, NativeString*, NativeString*, char, char, char, void*, char);
        reinterpret_cast<Lookup>(Slot(fs, 0x210))(fs, &found, &resolved, &gamePath, 0, 0, 0, nullptr, 0);
        bool device = false;
        const std::string media = "game:\\media\\";
        if (found) {
            void* nativeDevice = reinterpret_cast<void* (*)(void*)>(Slot(found, 0x1c0))(found);
            if (nativeDevice) fn.qualify(nativeDevice, &resolved, 0);
            Release(found);
            device = nativeDevice != nullptr;
        } else if (!gameDir.empty() && path.compare(0, media.size(), media) == 0) {
            std::string relative = path.substr(media.size());
            std::wstring disk = gameDir + L"\\mediaoverride\\" + std::wstring(relative.begin(), relative.end());
            DWORD attributes = GetFileAttributesW(disk.c_str());
            if (attributes != INVALID_FILE_ATTRIBUTES && !(attributes & FILE_ATTRIBUTE_DIRECTORY)) {
                Destroy(resolved);
                Init(resolved, "game:\\mediaoverride\\" + relative);
                device = true;
            }
        }
        if (!found && !device) {
            error = "native file system cannot find " + path;
        } else {
            resolvedOut.assign(resolved.c_str(), resolved.size);
            if (!device || !resolved.size) {
                error = "native file system cannot resolve the device for " + path;
            } else if (!(archive = fn.allocate(0x1e8))) {
                error = "native archive allocation failed";
            } else {
                archive = fn.construct(archive, &resolved, &gamePath, ArchiveFlags, 0, 0, 0);
                uintptr_t archiveVtable = 0;
                if (!archive || !Readable(reinterpret_cast<uintptr_t>(archive), &archiveVtable, sizeof archiveVtable) ||
                    archiveVtable != base + ArchiveVtable) {
                    error = "native archive construction failed for " + path;
                    archive = nullptr;
                } else {
                    AddRef(archive);
                    void* holder = archive;
                    int priority = *reinterpret_cast<int*>(fn.config() + PriorityBase) + ManifestPriority;
                    using MountFn = bool (*)(void*, NativeString*, void**, int, char, char, NativeString*);
                    ok = reinterpret_cast<MountFn>(Slot(fs, 0x1e8))(fs, &mountPath, &holder, priority, 0, 0, &empty);
                    Release(archive);
                    if (!ok) error = "native file system refused to mount " + path;
                }
            }
        }
        Destroy(gamePath);
        Destroy(resolved);
        Destroy(mountPath);
        Destroy(empty);
        return ok;
    }
};
}
