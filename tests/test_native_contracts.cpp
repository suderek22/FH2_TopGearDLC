#include "..\track_media.h"
#include "..\track_group.h"
#include "..\track_player.h"
#include "..\track_flow.h"
#include "..\track_diag.h"
#include "..\track_mount.h"
#include <cassert>
#include <cstdio>

static unsigned hooks;
static FH2Hook* HookFunction(FH2Mod*, const char*, void* target, void*, void** original) {
    *original = target;
    return reinterpret_cast<FH2Hook*>(static_cast<uintptr_t>(++hooks));
}
static FH2Hook* HookInstruction(FH2Mod*, const char*, void*, FH2InstructionFn, void*) {
    return reinterpret_cast<FH2Hook*>(static_cast<uintptr_t>(++hooks));
}
static int Toggle(FH2Mod*, FH2Hook*) { assert(false); return 0; }
static void Log(FH2Mod*, const char*) {}

int wmain(int argc, wchar_t** argv) {
    assert(argc == 2);
    TrackMedia::MappedFile file;
    std::string error;
    assert(file.Open(argv[1], error));
    auto data = static_cast<const unsigned char*>(file.data);
    assert(file.size >= sizeof(IMAGE_DOS_HEADER));
    IMAGE_DOS_HEADER dos;
    memcpy(&dos, data, sizeof dos);
    assert(dos.e_magic == IMAGE_DOS_SIGNATURE && dos.e_lfanew > 0);
    size_t offset = static_cast<size_t>(dos.e_lfanew);
    assert(offset + sizeof(IMAGE_NT_HEADERS64) <= file.size);
    IMAGE_NT_HEADERS64 pe;
    memcpy(&pe, data + offset, sizeof pe);
    assert(pe.Signature == IMAGE_NT_SIGNATURE && pe.OptionalHeader.Magic == IMAGE_NT_OPTIONAL_HDR64_MAGIC);
    assert(pe.FileHeader.NumberOfSections < 96 && pe.OptionalHeader.SizeOfImage < 512 * 1024 * 1024);
    assert(pe.OptionalHeader.SizeOfHeaders <= file.size);
    auto image = static_cast<unsigned char*>(
        VirtualAlloc(nullptr, pe.OptionalHeader.SizeOfImage, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE));
    assert(image);
    memcpy(image, data, pe.OptionalHeader.SizeOfHeaders);
    offset += sizeof(DWORD) + sizeof(IMAGE_FILE_HEADER) + pe.FileHeader.SizeOfOptionalHeader;
    for (unsigned i = 0; i < pe.FileHeader.NumberOfSections; ++i) {
        IMAGE_SECTION_HEADER section;
        assert(offset + sizeof section <= file.size);
        memcpy(&section, data + offset, sizeof section);
        offset += sizeof section;
        assert(static_cast<uint64_t>(section.PointerToRawData) + section.SizeOfRawData <= file.size);
        assert(static_cast<uint64_t>(section.VirtualAddress) + section.SizeOfRawData <= pe.OptionalHeader.SizeOfImage);
        memcpy(image + section.VirtualAddress, data + section.PointerToRawData, section.SizeOfRawData);
    }
    uintptr_t base = reinterpret_cast<uintptr_t>(image);
    const struct { uintptr_t rva; unsigned char bytes[7]; size_t size; } graphChecks[] = {
        {0x18fe2a7, {0x48,0x8d,0x4f,0x28}, 4},
        {0x18fe2bc, {0x48,0x89,0x47,0x28}, 4},
        {0x867dc6, {0x48,0x8b,0x49,0x10}, 4},
        {0x867dd5, {0x48,0x83,0xc1,0x28}, 4},
        {0x1906819, {0x4c,0x89,0x3b}, 3},
        {0x1906820, {0x4c,0x8b,0x86,0x98,0x01,0x00,0x00}, 7},
        {0x1906827, {0x48,0x8b,0x96,0x90,0x01,0x00,0x00}, 7},
        {0x18fe1e2, {0x48,0x8d,0x4e,0x68}, 4},
        {0x551827, {0x48,0x8b,0x38}, 3},
        {0x551840, {0x48,0x8b,0x47,0x18}, 4},
        {0x551850, {0x48,0x8b,0x6b,0x18}, 4},
        {0x551854, {0x4c,0x8b,0x73,0x10}, 4},
    };
    for (const auto& check : graphChecks)
        assert(!memcmp(image + check.rva, check.bytes, check.size));
    const unsigned char clearNullJob[] = {0x33,0xd2,0xc5,0xfa,0x7f,0x44,0x24,0x20,0xe8,0x6b,0x56,0xcd,0xff};
    const unsigned char nullJobOwner[] = {
        0xc7,0x40,0x08,0x01,0x00,0x00,0x00,0xc7,0x40,0x0c,0x01,0x00,0x00,0x00,
        0x48,0x8d,0x0d,0xc6,0x6a,0x9a,0x02,0x48,0x89,0x08,0x48,0x89,0x58,0x10
    };
    assert(!memcmp(image + 0x5e4ae8, clearNullJob, sizeof clearNullJob));
    assert(!memcmp(image + 0x2ba18d, nullJobOwner, sizeof nullJobOwner));
    int32_t nullJobDisplacement = 0;
    memcpy(&nullJobDisplacement, image + 0x2ba19e, sizeof nullJobDisplacement);
    assert(0x2ba1a2 + nullJobDisplacement == TrackLive::NullJobControlVtable);
    const unsigned char positionGetter[] = {
        0xc5,0xf8,0x28,0x81,0x70,0xfc,0xff,0xff,0x48,0x8b,0xc2,0xc5,0xf8,0x29,0x02,0xc3
    };
    const unsigned char positionSetter[] = {
        0xc5,0xf8,0x28,0x02,0xc5,0xf8,0x29,0x81,0x70,0xfc,0xff,0xff,0xc3
    };
    uintptr_t positionSlot = 0;
    memcpy(&positionSlot, image + TrackLive::PhysicsVtable + 0x270, sizeof positionSlot);
    assert(positionSlot == pe.OptionalHeader.ImageBase + 0x12cd150);
    memcpy(&positionSlot, image + TrackLive::PhysicsVtable + 0x280, sizeof positionSlot);
    assert(positionSlot == pe.OptionalHeader.ImageBase + 0x12e27d0);
    assert(!memcmp(image + 0x12cd150, positionGetter, sizeof positionGetter));
    assert(!memcmp(image + 0x12e27d0, positionSetter, sizeof positionSetter));
    int32_t groupDisplacement = 0;
    memcpy(&groupDisplacement, image + 0x18fe274, sizeof groupDisplacement);
    assert(0x18fe278 + groupDisplacement == TrackOwnership::GroupVtable);
    uintptr_t release = 0;
    memcpy(&release, image + TrackFlow::MachineModuleVtable + 0x10, sizeof release);
    assert(release == pe.OptionalHeader.ImageBase + 0x5a5ab0);
    memcpy(&release, image + TrackLive::CarVtable + 0x18, sizeof release);
    assert(release == pe.OptionalHeader.ImageBase + 0x2904d0);
    release = base + 0x2904d0;
    memcpy(image + TrackLive::CarVtable + 0x18, &release, sizeof release);
    {
        TrackGroup::Loader loader;
        TrackPlayer::Owners owners;
        TrackFlow::Registry flows;
        assert(loader.Initialize(base, error));
        assert(owners.Initialize(base, error));
        assert(flows.Initialize(base, error));
        TrackMount::Mounter mounter;
        assert(mounter.Initialize(base, error));
        memcpy(&release, image + TrackMount::FileSystemVtable + 0x1e8, sizeof release);
        assert(release == pe.OptionalHeader.ImageBase + 0x11f82b0);
        TrackSave::Gate gate;
        TrackBinding::Runtime bridge(gate);
        FH2ModApi api = {};
        api.hook_function = HookFunction;
        api.hook_instruction = HookInstruction;
        api.enable_hooks = api.disable_hooks = Toggle;
        api.log = Log;
        assert(bridge.Install(&api, reinterpret_cast<FH2Mod*>(1), base, error));
        assert(hooks == 17 && !gate.Held());
        TrackDiag::Install(&api, reinterpret_cast<FH2Mod*>(1), base);
        assert(hooks == 17 + 8);
    }
    assert(VirtualFree(image, 0, MEM_RELEASE));
    puts("Owned FH2 executable: all 17 binding signatures, native loader/player/stateflow helpers and car release slot match; no game code executed or hooked.");
}
