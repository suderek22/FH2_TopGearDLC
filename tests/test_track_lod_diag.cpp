#include "..\track_lod_diag.h"
#include <cassert>
#include <string>
#include <vector>

static std::vector<std::string> logs;
static unsigned hooks = 0;
static void Log(FH2Mod*, const char* text) { logs.emplace_back(text); }
static FH2Hook* Hook(FH2Mod*, const char*, void*, FH2InstructionFn callback, void*) {
    assert(callback == TrackLodDiag::Entry || callback == TrackLodDiag::Selection);
    return reinterpret_cast<FH2Hook*>(static_cast<uintptr_t>(++hooks));
}

int main() {
    FH2ModApi api{};
    api.log = Log;
    TrackLodDiag::Shared().api = &api;
    std::atomic<ULONGLONG> next{0};
    assert(TrackLodDiag::Due(next, 100));
    assert(!TrackLodDiag::Due(next, 100));
    assert(!TrackLodDiag::Due(next, 5099));
    assert(TrackLodDiag::Due(next, 5100));

    std::vector<unsigned char> presentation(0xFB80);
    std::vector<unsigned char> draws((TrackLodDiag::Targets[0].draw + 1) * 0x70);
    uintptr_t drawPointer = reinterpret_cast<uintptr_t>(draws.data());
    std::memcpy(presentation.data() + 0xFB78, &drawPointer, sizeof drawPointer);
    unsigned char descriptor[0x100]{};
    unsigned char stack[0x80]{};
    unsigned index = TrackLodDiag::Targets[0].draw;
    unsigned model = TrackLodDiag::Targets[0].model;
    unsigned short bands[] = {100, 200, 350, 5000};
    float bounds[] = {0.02f, 0.41f, 0.11f, 0};
    float center[] = {-204.09f, 1.65f, 271.69f};
    uint64_t flags = 1ull << 50;
    std::memcpy(descriptor + 0xC0, &index, sizeof index);
    std::memcpy(descriptor + 0xB8, &flags, sizeof flags);
    std::memcpy(descriptor + 0xF0, bands, sizeof bands);
    std::memcpy(descriptor + 0x70, bounds, sizeof bounds);
    std::memcpy(descriptor + 0x60, center, sizeof center);
    std::memcpy(draws.data() + index * 0x70ull + 0x10, &model, sizeof model);
    TrackLodDiag::Activate(reinterpret_cast<uintptr_t>(presentation.data()));

    FH2Context context{};
    context.rdx = context.r15 = reinterpret_cast<uintptr_t>(descriptor);
    context.rsp = reinterpret_cast<uintptr_t>(stack);
    context.rbp = reinterpret_cast<uintptr_t>(stack) + 0x40;
    context.rsi = 0;
    context.rax = 1;
    context.xmm[0].f[0] = 900 * 900;
    context.xmm[9].f[0] = 1;
    context.xmm[9].f[1] = 2;
    context.xmm[9].f[2] = 3;
    context.xmm[10].f[0] = 1;
    int band = 3;
    std::memcpy(stack + 0x68, &band, sizeof band);
    FH2Context original = context;
    auto originalDescriptor = std::vector<unsigned char>(descriptor, descriptor + sizeof descriptor);
    auto originalDraws = draws;
    TrackLodDiag::Entry(&context, nullptr);
    TrackLodDiag::Selection(&context, nullptr);
    assert(logs.size() == 2);
    assert(logs[0].find("draw=3516 model=26") != std::string::npos);
    assert(logs[0].find("bands=100/200/350/5000") != std::string::npos);
    assert(logs[0].find("obb-center=-204.09/1.65/271.69") != std::string::npos);
    assert(logs[1].find("distance=900.000 band=3 rejected=1 view=1.0/2.0/3.0 scale=1.0000") != std::string::npos);
    assert(!std::memcmp(&context, &original, sizeof context));
    assert(!std::memcmp(descriptor, originalDescriptor.data(), sizeof descriptor));
    assert(draws == originalDraws);
    TrackLodDiag::Entry(&context, nullptr);
    TrackLodDiag::Selection(&context, nullptr);
    assert(logs.size() == 2);
    TrackLodDiag::Selection(&context, reinterpret_cast<void*>(1));
    assert(logs.size() == 3 && logs[2].find("selection-single draw=3516") != std::string::npos);
    logs.pop_back();
    TrackLodDiag::Activate(reinterpret_cast<uintptr_t>(presentation.data()));
    unsigned wrongModel = 27;
    std::memcpy(draws.data() + index * 0x70ull + 0x10, &wrongModel, sizeof wrongModel);
    TrackLodDiag::Selection(&context, nullptr);
    assert(logs.size() == 2);
    std::memcpy(draws.data() + index * 0x70ull + 0x10, &model, sizeof model);
    index = 1;
    std::memcpy(descriptor + 0xC0, &index, sizeof index);
    TrackLodDiag::Selection(&context, nullptr);
    assert(logs.size() == 2);
    TrackLodDiag::Deactivate(reinterpret_cast<uintptr_t>(presentation.data()));
    assert(!TrackLodDiag::Shared().presentation);
    assert(!TrackLodDiag::Install(&api, nullptr, 0));
    assert(logs.back().find("signatures differ") != std::string::npos);
    std::vector<unsigned char> image(TrackLodDiag::SelectionSite + 16);
    const unsigned char entry[] = {0x49, 0x8B, 0x45, 0x00, 0x32, 0xD2};
    const unsigned char selection[] = {0x84, 0xC0, 0x40, 0x0F, 0x94, 0xC7};
    std::memcpy(image.data() + TrackLodDiag::EntrySite, entry, sizeof entry);
    std::memcpy(image.data() + TrackLodDiag::SelectionSite, selection, sizeof selection);
    std::memcpy(image.data() + TrackLodDiag::SingleSelectionSite, selection, sizeof selection);
    api.hook_instruction = Hook;
    assert(TrackLodDiag::Install(&api, nullptr, reinterpret_cast<uintptr_t>(image.data())));
    assert(hooks == 3);
    image[TrackLodDiag::EntrySite] = 0;
    assert(!TrackLodDiag::Install(&api, nullptr, reinterpret_cast<uintptr_t>(image.data())));
    assert(hooks == 3);
    puts("Prop LOD diagnostics: throttled, target-only, context and scene data unchanged.");
}
