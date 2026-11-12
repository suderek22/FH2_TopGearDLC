#pragma once
#include "track_ownership.h"
#include <atomic>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <vector>
#ifdef TRACKLOADER_LOD_DIAGNOSTICS
#include "track_lod_diag.h"
#endif

// Log-only probes for the track presentation load/stream path. They never alter
// registers; a refused site is skipped so the visit hooks are unaffected.
namespace TrackDiag {
constexpr uintptr_t BeginLoadSite = 0x133BB25;   // rdi = TrackPresentation
constexpr uintptr_t LoadedSite = 0x133DE61;      // rsi = TrackPresentation
constexpr uintptr_t SkyResultSite = 0x138C862;   // rdi = TrackPresentation, al = sky request result
constexpr uintptr_t ProbeResultSite = 0x138CB18; // rbp = TrackPresentation, al = probe request result
constexpr uintptr_t UnloadSite = 0x13B0CA6;      // rcx = TrackPresentation
constexpr uintptr_t TextureRequestSite = 0x1391072;
constexpr uintptr_t ModelRequestSite = 0x13907F0;
constexpr uintptr_t ModelReleaseSite = 0x13B1EFB;
constexpr uintptr_t ReadyExitSite = 0x133AD86;    // r15 = TrackPresentation, eax = readiness code 0 or 5..10
constexpr uintptr_t PresentationCountsSite = 0x13379D6; // r15 = TrackPresentation, before per-zone vector resize

struct State {
    const FH2ModApi* api = nullptr;
    FH2Mod* mod = nullptr;
    std::atomic<unsigned> textures{0}, models{0}, releases{0}, events{0}, customFailures{0};
    std::atomic<bool> custom{false};
    uintptr_t base = 0;
};
inline State& Shared() { static State state; return state; }

inline void Log(const char* format, ...) {
    auto& state = Shared();
    if (!state.api || !state.api->log || state.events.fetch_add(1) >= 400) return;
    char text[512];
    va_list args;
    va_start(args, format);
    vsnprintf(text, sizeof text, format, args);
    va_end(args);
    state.api->log(state.mod, text);
}
template <typename T> inline T Value(uintptr_t address) {
    T value{};
    TrackOwnership::Read(address, &value, sizeof value);
    return value;
}
inline void Describe(const char* event, uintptr_t presentation, int result = -1) {
    char name[17] = {};
    TrackOwnership::Read(presentation + 0xAAB0, name, 16);
    if (Value<uint64_t>(presentation + 0xAAB0 + 0x18) >= 16) {
        uintptr_t pointer = Value<uintptr_t>(presentation + 0xAAB0);
        memset(name, 0, sizeof name);
        TrackOwnership::Read(pointer, name, 16);
    }
    for (char& c : name) if (c && (c < 32 || c > 126)) c = '?';
    if (!strcmp(name, "TopGearCPS")) Shared().custom = true;
#ifdef TRACKLOADER_LOD_DIAGNOSTICS
    if (!strcmp(name, "TopGearCPS") && strcmp(event, "unload") &&
        TrackLodDiag::Shared().presentation.load() != presentation)
        TrackLodDiag::Activate(presentation);
#endif
    uint64_t models = (Value<uint64_t>(presentation + 0xF620) - Value<uint64_t>(presentation + 0xF618)) / 8;
    uint64_t textureBegin = Value<uint64_t>(presentation + 0x1AA68);
    uint64_t textures = textureBegin ? (Value<uint64_t>(presentation + 0x1AA70) - textureBegin) / 0x2B8 : 0;
    uintptr_t skyBegin = Value<uintptr_t>(presentation + 0xFC08);
    uint64_t skies = skyBegin ? (Value<uint64_t>(presentation + 0xFC10) - skyBegin) / 4 : 0;
    unsigned sky = Value<uint16_t>(presentation + 0x82A4);
    int skyModel = sky < skies ? Value<int>(skyBegin + 4ull * sky) : -2;
    auto& state = Shared();
    Log("diag %s p=%p name=%s result=%d models=%llu textures=%llu sky=%u/%llu->%d loaded=%u stream=%u "
        "requests tex=%u model=%u release=%u",
        event, reinterpret_cast<void*>(presentation), name, result, static_cast<unsigned long long>(models),
        static_cast<unsigned long long>(textures), sky, static_cast<unsigned long long>(skies), skyModel,
        Value<uint8_t>(presentation + 0xF370), Value<uint8_t>(presentation + 0x198A8),
        state.textures.load(), state.models.load(), state.releases.load());
}
inline void Collections(const char* when);
inline void Count(std::atomic<unsigned>& counter, const char* name) {
    unsigned value = counter.fetch_add(1) + 1;
    if ((value & (value - 1)) == 0) {
        Log("diag %s count=%u", name, value);
        if (value >= 256) Collections(name);
    }
}
// Live resource vectors of the "Instance Resources" (0x36E6858) and "Procedural Resources" (0x36E6938)
// collections (see their count virtuals 0xF9D660 / 0xF9D840).
inline void Collections(const char* when) {
    uintptr_t base = Shared().base;
    auto size = [base](uintptr_t rva) {
        return (Value<uint64_t>(base + rva + 8) - Value<uint64_t>(base + rva)) / 8;
    };
    Log("diag collections %s instance=%llu procedural=%llu", when, static_cast<unsigned long long>(size(0x36E6858)),
        static_cast<unsigned long long>(size(0x36E6938)));
}
inline void ReadyCode(FH2Context* c, void* user) {
    static std::atomic<int> last{-1};
    static std::atomic<unsigned> repeats{0};
    int code = user ? static_cast<int>(reinterpret_cast<uintptr_t>(user)) : static_cast<int>(c->rax);
    unsigned count = repeats.fetch_add(1) + 1;
    if (last.exchange(code) != code || (count & (count - 1)) == 0) {
        Log("diag ready-code %d t=%p evaluations=%u", code, reinterpret_cast<void*>(c->r15), count);
        Collections("ready");
    }
}
inline void Counts(FH2Context* c, void*) {
    uintptr_t p = c->r15;
    Log("diag counts p=%p fd88=%d fd8c=%d fd90=%d fd94=%d fd98=%d fb2c=%d", reinterpret_cast<void*>(p),
        Value<int>(p + 0xFD88), Value<int>(p + 0xFD8C), Value<int>(p + 0xFD90), Value<int>(p + 0xFD94),
        Value<int>(p + 0xFD98), Value<int>(p + 0xFB2C));
}
inline void BeginLoad(FH2Context* c, void*) { Describe("begin-load", c->rdi); }
inline void Loaded(FH2Context* c, void*) { Describe("load-callback", c->rsi); }
inline void Sky(FH2Context* c, void*) { Describe("sky-request", c->rdi, static_cast<uint8_t>(c->rax)); }
inline void Probe(FH2Context* c, void*) { Describe("probe-request", c->rbp, static_cast<uint8_t>(c->rax)); }
inline void Unload(FH2Context* c, void*) {
    Describe("unload", c->rcx);
#ifdef TRACKLOADER_LOD_DIAGNOSTICS
    TrackLodDiag::Deactivate(c->rcx);
#endif
}
inline void Texture(FH2Context*, void*) { Count(Shared().textures, "texture-request"); }
inline void Model(FH2Context*, void*) { Count(Shared().models, "model-request"); }
inline void Release(FH2Context*, void*) { Count(Shared().releases, "model-release"); }

// Modelbin parser 0x13F1D70 (rdi = model data, name std::string at +0x30): log failing stages only.
constexpr uintptr_t ParseMeshCountSite = 0x13F1FD7; // r15 = parsed meshes, [rbp+0x67] = header count
constexpr uintptr_t ParseLayoutSite = 0x13F2010;    // al = VLay result
constexpr uintptr_t ParseMaterialSite = 0x13F2089;  // al = MatI result
constexpr uintptr_t ParseResultSite = 0x13F2121;    // bl = parser result
inline std::atomic<unsigned>& ParseFailures() { static std::atomic<unsigned> value{0}; return value; }
inline std::atomic<unsigned>& ParseSuccesses() { static std::atomic<unsigned> value{0}; return value; }
inline void ParseFailure(const char* stage, uintptr_t model, unsigned long long a, unsigned long long b) {
    unsigned count = ParseFailures().fetch_add(1) + 1;
    if (count > 24 && (count & (count - 1))) return;
    char name[64] = {};
    uintptr_t text = model + 0x30;
    if (Value<uint64_t>(model + 0x48) >= 16) text = Value<uintptr_t>(model + 0x30);
    TrackOwnership::Read(text, name, sizeof name - 1);
    for (char& c : name) if (c && (c < 32 || c > 126)) c = '?';
    Log("diag modelbin %s failed #%u model=%s a=%llu b=%llu ok=%u", stage, count, name, a, b, ParseSuccesses().load());
}
inline void ParseMeshCount(FH2Context* c, void*) {
    uint64_t expected = Value<uint64_t>(c->rbp + 0x67);
    if (c->r15 != expected) ParseFailure("mesh-count", c->rdi, c->r15, expected);
}
inline void ParseLayout(FH2Context* c, void*) {
    if (!static_cast<uint8_t>(c->rax)) ParseFailure("VLay", c->rdi, 0, 0);
}
inline void ParseMaterial(FH2Context* c, void*) {
    if (!static_cast<uint8_t>(c->rax)) ParseFailure("MatI", c->rdi, 0, 0);
}
inline void ParseResult(FH2Context* c, void*) {
    if (static_cast<uint8_t>(c->rbx)) ParseSuccesses().fetch_add(1);
    else ParseFailure("result", c->rdi, 0, 0);
}

// Resource failure writers (state byte +0x34 = 6); [rbp+0x38] holds "Failed to load <name> (<reason>)".
constexpr uintptr_t ResourceFailSiteA = 0x13D5163;
constexpr uintptr_t ResourceFailSiteB = 0x13D699E;
constexpr uintptr_t ResourceFailSiteC = 0x13D70FB; // after SetState(6); message at [rbp+0x28]
constexpr uintptr_t ResourceFailSiteD = 0x13D044E; // request path after SetState(6); message at [rbp+0xB0]
inline void ResourceFailed(FH2Context* c, void* user) {
    static std::atomic<unsigned> failures{0};
    static std::atomic<unsigned> named{0};
    unsigned count = failures.fetch_add(1) + 1;
    auto& shared = Shared();
    char site = *static_cast<const char*>(user);
    uintptr_t message0 = c->rbp + (site == 'C' ? 0x28 : site == 'D' ? 0xB0 : 0x38);
    uintptr_t text = message0;
    uint64_t size = Value<uint64_t>(message0 + 0x10);
    if (Value<uint64_t>(message0 + 0x18) >= 16) text = Value<uintptr_t>(message0);
    char message[300] = {};
    TrackOwnership::Read(text, message, size < sizeof message - 1 ? static_cast<size_t>(size) : sizeof message - 1);
    for (char& ch : message) if (ch && (ch < 32 || ch > 126)) ch = '?';
    // Nameless "File Resources" failures are known noise; anything else after a custom load is always logged.
    bool noise = !strcmp(message, "Failed to load  (File Resources)");
    bool always = shared.custom && !noise && named.fetch_add(1) < 200;
    bool customFailure = false;
    if (shared.custom && !noise && shared.customFailures.fetch_add(1) < 48) {
        customFailure = true;
        uintptr_t res = c->rsi;
        uint64_t q[16] = {};
        TrackOwnership::Read(res + 0x20, q, sizeof q);
        Log("diag custom-fail #%u res=%p vt=+%llx f20=%llx %llx %llx %llx f40=%llx %llx %llx %llx",
            count, reinterpret_cast<void*>(res),
            static_cast<unsigned long long>(Value<uintptr_t>(res) - shared.base), q[0], q[1], q[2], q[3], q[4], q[5], q[6], q[7]);
        Log("diag custom-fail #%u f60=%llx %llx %llx %llx %llx %llx %llx %llx", count, q[8], q[9], q[10], q[11], q[12],
            q[13], q[14], q[15]);
    }
    if (!customFailure && !always && count > 24 && (count & (count - 1))) return;
    Log("diag resource-failed%s #%u res=%p: %s", static_cast<const char*>(user), count,
        reinterpret_cast<void*>(c->rsi), message);
}

// HEXY grid (Ribbon_00\<name>out_00.hex) readiness and the resulting t+0xF7E0 spatial query object.
constexpr uintptr_t HexReadySite = 0x13396D9;  // rsi = hex file resource, al = ready result
constexpr uintptr_t SpatialSetSite = 0x1339823; // r14 = TrackPresentation, rax = t+0xF7E0
inline void HexReady(FH2Context* c, void*) {
    uintptr_t res = c->rsi;
    Log("diag hex-ready res=%p ok=%u state=%u/%u error=%d refs=%d", reinterpret_cast<void*>(res),
        static_cast<unsigned>(static_cast<uint8_t>(c->rax)), Value<uint8_t>(res + 0x34), Value<uint8_t>(res + 0x35),
        Value<int>(res + 0x44), Value<int>(res + 0x40));
}
inline void SpatialSet(FH2Context* c, void*) {
    uintptr_t p = c->r14, object = c->rax;
    Log("diag spatial t=%p object=%p vt=+%llx a968=%p", reinterpret_cast<void*>(p), reinterpret_cast<void*>(object),
        static_cast<unsigned long long>(object ? Value<uintptr_t>(object) - Shared().base : 0),
        reinterpret_cast<void*>(Value<uintptr_t>(p + 0xA968)));
}

// InstanceTemplates loader 0x186BDF0: enumerates Objects\*.plib (rsi dir) then Placement\*.plib (r15 dir)
// and requests each file. Logged without the shared event budget so late track loads stay visible.
constexpr uintptr_t PlibObjectsCountSite = 0x186BE89;   // [rsp+0x78] = enumerated file count
constexpr uintptr_t PlibObjectsRequestSite = 0x186BF5A; // std::string path at rbp+0xC0
constexpr uintptr_t PlibPlacementCountSite = 0x186C02B; // [rsp+0x38] = enumerated file count
constexpr uintptr_t PlibPlacementRequestSite = 0x186C0E7; // std::string path at rbp+0xE8
inline void ReadText(uintptr_t address, bool stdString, char (&out)[260]) {
    memset(out, 0, sizeof out);
    uintptr_t text = address;
    size_t size = sizeof out - 1;
    if (stdString) {
        uint64_t length = Value<uint64_t>(address + 0x10);
        if (Value<uint64_t>(address + 0x18) >= 16) text = Value<uintptr_t>(address);
        if (length < size) size = static_cast<size_t>(length);
    }
    TrackOwnership::Read(text, out, size);
    for (char& ch : out) if (ch && (ch < 32 || ch > 126)) ch = '?';
}
inline void PlibLog(const char* format, ...) {
    static std::atomic<unsigned> lines{0};
    auto& state = Shared();
    if (!state.api || !state.api->log || lines.fetch_add(1) >= 200) return;
    char text[512];
    va_list args;
    va_start(args, format);
    vsnprintf(text, sizeof text, format, args);
    va_end(args);
    state.api->log(state.mod, text);
}
inline void PlibCount(FH2Context* c, void* user) {
    bool objects = *static_cast<const char*>(user) == 'O';
    char dir[260];
    ReadText(objects ? c->rsi : c->r15, true, dir);
    PlibLog("diag plib %s dir=%s files=%llu", objects ? "objects" : "placement", dir,
            static_cast<unsigned long long>(Value<uint64_t>(c->rsp + (objects ? 0x78 : 0x38))));
    Collections(objects ? "plib-objects" : "plib-placement");
}
inline void PlibRequest(FH2Context* c, void* user) {
    bool objects = *static_cast<const char*>(user) == 'O';
    char path[260];
    ReadText(c->rbp + (objects ? 0xC0 : 0xE8), true, path);
    PlibLog("diag plib request %s", path);
}

// ChunkMap key -> archive index lookup 0x1330940 (rcx = TrackPresentation, edx = key) and the FileChunk
// resource index writers (eax = archive index). Logs type-0/flagged key lookups and FileChunk creations
// for index 0 or the appended smashable cell (>= 6600) after a custom track load.
constexpr uintptr_t ChunkLookupSite = 0x1330940;
constexpr uintptr_t FileChunkIndexSiteA = 0x13D359F;
constexpr uintptr_t FileChunkIndexSiteB = 0x13D56EF;
inline void Callers(uintptr_t rsp, char (&out)[200]) {
    uintptr_t base = Shared().base;
    int used = 0, found = 0;
    out[0] = 0;
    for (int i = 0; i < 96 && found < 8; ++i) {
        uintptr_t value = Value<uintptr_t>(rsp + 8ull * i);
        if (value > base + 0x1000 && value < base + 0x2200000) {
            used += snprintf(out + used, sizeof out - used, " %llx", static_cast<unsigned long long>(value - base));
            ++found;
            if (used >= static_cast<int>(sizeof out) - 12) break;
        }
    }
}
inline void ChunkLookup(FH2Context* c, void*) {
    static std::atomic<unsigned> lines{0}, reference{0};
    uint32_t key = static_cast<uint32_t>(c->rdx);
    if ((key & 0x7F000000) != 0) return;
    if (Shared().custom ? lines.fetch_add(1) >= 60 : reference.fetch_add(1) >= 15) return;
    char callers[200];
    Callers(c->rsp, callers);
    PlibLog("diag chunk-lookup key=%08x t=%p callers:%s", key, reinterpret_cast<void*>(c->rcx), callers);
}
inline void FileChunkIndex(FH2Context* c, void* user) {
    static std::atomic<unsigned> lines{0};
    int index = static_cast<int>(c->rax);
    if (!Shared().custom || (index != 0 && index < 6600) || lines.fetch_add(1) >= 60) return;
    char callers[200];
    Callers(c->rsp, callers);
    PlibLog("diag filechunk-%s index=%d bc=%08x res=%p callers:%s", static_cast<const char*>(user), index,
            Value<uint32_t>(c->rbp - 0x4D), reinterpret_cast<void*>(c->rsi), callers);
}

inline void Install(const FH2ModApi* api, FH2Mod* mod, uintptr_t base) {
    auto& state = Shared();
    state.api = api;
    state.mod = mod;
    state.base = base;
    struct Site { uintptr_t rva; const char* name; FH2InstructionFn fn; const char* user = nullptr; };
    const Site sites[] = {
        {BeginLoadSite, "TrackLoaderCPS diag presentation begin-load", BeginLoad},
        {LoadedSite, "TrackLoaderCPS diag presentation load-callback", Loaded},
        {SkyResultSite, "TrackLoaderCPS diag sky request", Sky},
        {ProbeResultSite, "TrackLoaderCPS diag probe request", Probe},
        {UnloadSite, "TrackLoaderCPS diag presentation unload", Unload},
        {TextureRequestSite, "TrackLoaderCPS diag texture request", Texture},
        {ModelRequestSite, "TrackLoaderCPS diag model request", Model},
        {ModelReleaseSite, "TrackLoaderCPS diag model release", Release},
        {PresentationCountsSite, "TrackLoaderCPS diag presentation counts", Counts},
        {ReadyExitSite, "TrackLoaderCPS diag ready exit", ReadyCode},
        {ParseMeshCountSite, "TrackLoaderCPS diag modelbin mesh count", ParseMeshCount},
        {ParseLayoutSite, "TrackLoaderCPS diag modelbin layouts", ParseLayout},
        {ParseMaterialSite, "TrackLoaderCPS diag modelbin materials", ParseMaterial},
        {ParseResultSite, "TrackLoaderCPS diag modelbin result", ParseResult},
        {ResourceFailSiteA, "TrackLoaderCPS diag resource failure A", ResourceFailed, "A"},
        {ResourceFailSiteB, "TrackLoaderCPS diag resource failure B", ResourceFailed, "B"},
        {ResourceFailSiteC, "TrackLoaderCPS diag resource failure C", ResourceFailed, "C"},
        {ResourceFailSiteD, "TrackLoaderCPS diag resource failure D", ResourceFailed, "D"},
        {HexReadySite, "TrackLoaderCPS diag hex grid ready", HexReady},
        {SpatialSetSite, "TrackLoaderCPS diag spatial query object", SpatialSet},
        {PlibObjectsCountSite, "TrackLoaderCPS diag plib objects count", PlibCount, "O"},
        {PlibObjectsRequestSite, "TrackLoaderCPS diag plib objects request", PlibRequest, "O"},
        {PlibPlacementCountSite, "TrackLoaderCPS diag plib placement count", PlibCount, "P"},
        {PlibPlacementRequestSite, "TrackLoaderCPS diag plib placement request", PlibRequest, "P"},
        {ChunkLookupSite, "TrackLoaderCPS diag chunk key lookup", ChunkLookup},
        {FileChunkIndexSiteA, "TrackLoaderCPS diag filechunk index A", FileChunkIndex, "A"},
        {FileChunkIndexSiteB, "TrackLoaderCPS diag filechunk index B", FileChunkIndex, "B"},
    };
    unsigned installed = 0;
    for (const auto& site : sites)
        if (api->hook_instruction(mod, site.name, reinterpret_cast<void*>(base + site.rva), site.fn,
                                  const_cast<char*>(site.user)))
            ++installed;
        else
            Log("diag site refused: %s", site.name);
    Log("diag presentation probes registered: %u/%u", installed, static_cast<unsigned>(sizeof sites / sizeof *sites));
#ifdef TRACKLOADER_LOD_DIAGNOSTICS
    TrackLodDiag::Install(api, mod, base);
#endif
}
}
