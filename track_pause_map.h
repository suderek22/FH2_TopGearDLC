#pragma once
#include "track_media.h"
#include "track_ownership.h"
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>

// Per-track pause-map background. Map profiles are parsed once at boot, so while a custom track is
// driven the background tiles symbolizers are retargeted in place (texture/Size/Offset): the
// PauseMenu home-tab preview (low tile) and the full-screen "test" map (low tile plus 7x4 high
// tiles). Loaded profiles are refreshed through the game's own unload/load helpers; mainland values
// are restored before the return transition.
namespace TrackPauseMap {
// Track names match the stock lengths, so the heap-backed std::strings are rewritten in place.
constexpr char StockLow[] = "Data_Bound\\Anthem_Map\\low.png";
constexpr char TrackLow[] = "Data_Bound\\TrackLoader\\pm.png";
constexpr char StockHigh[] = "Data_Bound\\Anthem_Map\\high.png";
constexpr char TrackHigh[] = "Data_Bound\\TrackLoader\\pmh.png";
static_assert(sizeof StockLow == sizeof TrackLow && sizeof StockHigh == sizeof TrackHigh,
              "pause-map texture names must match in length");
constexpr const char* LowMediaPath = "UI\\textures\\data_bound\\TrackLoader\\pm-0-0.swatchbin";
constexpr const char* HighMediaFormat = "UI\\textures\\data_bound\\TrackLoader\\pmh-%u-%u.swatchbin";
constexpr unsigned HighRows = 4, HighColumns = 7;
constexpr size_t HeaderBytes = 83, LowBytes = HeaderBytes + 3584 * 2048, HighBytes = HeaderBytes + 1024 * 1024;
constexpr float StockSize = 12000.0f;
constexpr float StockOffset[3] = {-7500.0f, 0.0f, -11000.0f};
constexpr uintptr_t ProfileManager = 0x36ea128;
constexpr uintptr_t LookupProfile = 0x193be70;
constexpr uintptr_t LoadProfile = 0x8ad1f0;
constexpr uintptr_t UnloadProfile = 0x8ad2c0;
constexpr uintptr_t TilesVtable = 0x2f61d48;
constexpr uintptr_t TilesLoad = 0x193e200;
constexpr uintptr_t ProfileLoaded = 0x48;
constexpr uintptr_t SizeField = 0x2c, CurrentSizeField = 0x38, OffsetField = 0x70, TextureField = 0xd0;

struct Preset {
    bool enabled = false;
    float size = 0;
    float offsetX = 0;
    float offsetZ = 0;
    bool Valid() const {
        return !enabled || (std::isfinite(size) && size >= 100 && size <= StockSize &&
            std::isfinite(offsetX) && std::fabs(offsetX) <= 20000 &&
            std::isfinite(offsetZ) && std::fabs(offsetZ) <= 20000);
    }
};

// Durango BC7 swatchbin with one mip, as in Anthem_Map.zip.
inline bool Native(const unsigned char* data, size_t size, uint16_t width, uint16_t height) {
    if (size != HeaderBytes + size_t(width) * height) return false;
    uint16_t w = 0, h = 0;
    memcpy(&w, data + 64, 2);
    memcpy(&h, data + 66, 2);
    const unsigned char format[] = {0x2e,0x01,0x00,0xb8};
    return !memcmp(data, "burG", 4) && !memcmp(data + 16, "BCXT", 4) &&
        !memcmp(data + 40, "HCXT", 4) && w == width && h == height && data[76] == 1 &&
        !memcmp(data + 79, format, sizeof format);
}

// lowPath: 3584x2048 swatchbin; highPath: the 4x7 grid of 1024x1024 swatchbins concatenated row-major.
inline bool Publish(const FH2ModApi* api, FH2Mod* mod, const std::wstring& lowPath, const std::string& lowHash,
                    const std::wstring& highPath, const std::string& highHash, std::string& error) {
    TrackMedia::MappedFile low, high;
    std::string digest;
    if (!api->set_file || !api->clear_file || lowHash.size() != 64 || !TrackMedia::PlainPath(lowPath, error) ||
        !low.Open(lowPath, error) || !Native(static_cast<const unsigned char*>(low.data), low.size, 3584, 2048) ||
        !TrackMedia::Sha256(low, digest, error) || digest != lowHash) {
        if (error.empty()) error = "pause map low tile must be a 3584x2048 BC7 swatchbin matching PauseMap.Sha256";
        return false;
    }
    if (highHash.size() != 64 || !TrackMedia::PlainPath(highPath, error) || !high.Open(highPath, error) ||
        high.size != HighBytes * HighRows * HighColumns || !TrackMedia::Sha256(high, digest, error) ||
        digest != highHash) {
        if (error.empty()) error = "pause map high tiles must be 28 concatenated 1024x1024 BC7 swatchbins matching PauseMap.HighSha256";
        return false;
    }
    const auto* tiles = static_cast<const unsigned char*>(high.data);
    for (unsigned i = 0; i < HighRows * HighColumns; ++i) {
        if (!Native(tiles + i * HighBytes, HighBytes, 1024, 1024)) {
            error = "pause map high tile " + std::to_string(i) + " is not a 1024x1024 BC7 swatchbin";
            return false;
        }
    }
    std::string published[1 + HighRows * HighColumns];
    unsigned count = 0;
    bool ok = api->set_file(mod, LowMediaPath, low.data, low.size);
    if (ok) published[count++] = LowMediaPath;
    for (unsigned row = 0; ok && row < HighRows; ++row) {
        for (unsigned column = 0; ok && column < HighColumns; ++column) {
            char path[96] = {};
            snprintf(path, sizeof path, HighMediaFormat, row, column);
            ok = api->set_file(mod, path, tiles + (row * HighColumns + column) * HighBytes, HighBytes);
            if (ok) published[count++] = path;
        }
    }
    if (!ok) {
        while (count) api->clear_file(mod, published[--count].c_str());
        error = "could not publish pause map textures";
    }
    return ok;
}

inline bool Write(uintptr_t address, const void* data, size_t size) {
    SIZE_T written = 0;
    return address && WriteProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(address),
                                         data, size, &written) && written == size;
}

inline bool Supported(uintptr_t base, std::string& error) {
    static const unsigned char head[] = {0x40,0x57,0x48,0x83,0xec,0x50,0x48,0xc7,0x44,0x24,0x20,0xfe,0xff,0xff,0xff,
        0x48,0x89,0x5c,0x24,0x60,0x48,0x89,0x6c,0x24,0x68,0x48,0x89,0x74,0x24,0x70,0x48,0x8b,0xea,0x4c,0x8b,0xc2,
        0x48,0x8d,0x54,0x24,0x28,0x48,0x8b,0x0d};
    static const unsigned char load[] = {0x08,0xcf,0xe3,0x02,0xe8,0x4b,0xec,0x08,0x01,0x90,0x48,0x8b,0x08,0xe8,0x02,0x4f,0x08,0x01};
    static const unsigned char unload[] = {0x38,0xce,0xe3,0x02,0xe8,0x7b,0xeb,0x08,0x01,0x90,0x48,0x8b,0x08,0xe8,0x32,0x4e,0x08,0x01};
    static const unsigned char lookup[] = {0x40,0x57,0x41,0x56,0x41,0x57,0x48,0x83,0xec,0x30,0x48,0xc7};
    static const unsigned char loaded[] = {0x0f,0xb6,0x41,0x48,0xc3};
    unsigned char actual[sizeof head] = {};
    uintptr_t tilesLoad = 0;
    if (!base ||
        !TrackOwnership::Read(base + LoadProfile, actual, sizeof head) || memcmp(actual, head, sizeof head) ||
        !TrackOwnership::Read(base + LoadProfile + sizeof head, actual, sizeof load) || memcmp(actual, load, sizeof load) ||
        !TrackOwnership::Read(base + UnloadProfile, actual, sizeof head) || memcmp(actual, head, sizeof head) ||
        !TrackOwnership::Read(base + UnloadProfile + sizeof head, actual, sizeof unload) ||
        memcmp(actual, unload, sizeof unload) ||
        !TrackOwnership::Read(base + LookupProfile, actual, sizeof lookup) || memcmp(actual, lookup, sizeof lookup) ||
        // the game's own loaded-flag accessor (0x1932130) reads profile+0x48
        !TrackOwnership::Read(base + 0x1932130, actual, sizeof loaded) || memcmp(actual, loaded, sizeof loaded) ||
        !TrackOwnership::Read(base + TilesVtable + 0x20, &tilesLoad, sizeof tilesLoad) ||
        tilesLoad != base + TilesLoad) {
        error = "unsupported game build for pause map background";
        return false;
    }
    return true;
}

// Full-screen map framing and icon filtering while a custom track is driven.
// Map screen init (0xd7fd80) copies the profile's view_area/cursor_area into its
// MapViewControllerInteractive (screen+0x410) on every open; the hook just after those writes
// replaces them with the track image rectangle and shrinks the XAML zoom range (MapDistance is half
// the visible map height) so the image always fills the screen. The map filter predicate (0xc56ae0)
// hides every typed element except the player's vehicle and route, removing mainland events/POIs.
namespace Hooks {
constexpr uintptr_t MapOpenedSite = 0xd80196;
constexpr uintptr_t ElementFilter = 0xc56ae0;
constexpr uintptr_t ElementTag = 0x193ac50;
constexpr uintptr_t ControllerVtable = 0x2d9cd48, ControllerType = 0xb90fb0;
constexpr uintptr_t ScreenController = 0x410, ScreenProfile = 0x420;
// Pause-menu tile camera (MapViewControllerTracking, A_PauseMenu.xaml): 0xbabb90 lerps
// MinDistance/MaxDistance by speed into +0xb8 and puts the look-at point at +0x90; the caller then
// rebuilds the view from those fields. The HUD minimap's subclass uses another vtable (0x2d9dc28).
constexpr uintptr_t TileCamera = 0xbabb90;
constexpr uintptr_t TileVtable = 0x2d9db50, TileType = 0xbaeb60;
constexpr uintptr_t TileTarget = 0x90, TileDistance = 0xb8;
// Measured in game (d = 133 and 248): the tile spans 2.14 x distance metres across, is 2.19 times
// wider than tall, and the look-at point sits 2% of its height below the centre. Image row 0 is max z.
constexpr float TileWidthPerDistance = 2.14f, TileAspect = 2.19f, TileTargetDrop = 0.02f;
// MinMapDistance, MaxMapDistance, MinCursorSpeed, MaxCursorSpeed (A_Map.xaml).
constexpr uintptr_t ZoomFields[4] = {0x370, 0x374, 0x37c, 0x380};
constexpr uintptr_t ViewMinX = 0x3bc, ViewMaxX = 0x3c0, ViewMaxZ = 0x3c4, ViewMinZ = 0x3c8;
constexpr uintptr_t CursorMinX = 0x2f0, CursorMinZ = 0x2f8, CursorMaxX = 0x300, CursorMaxZ = 0x308;

struct State {
    uintptr_t base = 0;
    bool installed = false;
    std::atomic<bool> active{false};
    float view[4] = {};    // min x, min z, max x, max z
    float cursor[4] = {};
    float zoom[4] = {};
    uintptr_t touched = 0;  // controller whose XAML zoom values were replaced
    float original[4] = {};
    bool (*originalFilter)(void*, void*) = nullptr;
    float tileCentre[2] = {};  // x, z
    float tileDistance = 0;
    void (*originalTile)(void*, const float*, const float*, float, float) = nullptr;
};
inline State& Get() {
    static State state;
    return state;
}
inline bool Kept(const char* type) {
    return !strncmp(type, "vehicle", 7) || !strcmp(type, "route") || !strcmp(type, "group_route") ||
        !strcmp(type, "track");
}
inline bool FilterHook(void* self, void* element) {
    State& state = Get();
    if (state.active.load(std::memory_order_acquire) && element) {
        const char* type = reinterpret_cast<const char*(*)(void*, const char*)>(state.base + ElementTag)(element, "type");
        if (type && !Kept(type)) return false;
    }
    return state.originalFilter(self, element);
}
// The tile normally follows the car; on the track it is held over the whole image instead.
inline void TileHook(void* self, const float* position, const float* direction, float speed, float floor) {
    State& state = Get();
    state.originalTile(self, position, direction, speed, floor);
    uintptr_t table = 0, controller = reinterpret_cast<uintptr_t>(self);
    if (!state.active.load(std::memory_order_acquire) || !TrackOwnership::Pointer(controller, table) ||
        table != state.base + TileVtable)
        return;
    auto target = reinterpret_cast<float*>(controller + TileTarget);
    target[0] = state.tileCentre[0];
    target[2] = state.tileCentre[1];
    *reinterpret_cast<float*>(controller + TileDistance) = state.tileDistance;
}
inline bool FullScreenProfile(uintptr_t screen) {
    uint64_t length = 0, capacity = 0;
    uintptr_t text = screen + ScreenProfile;
    char name[5] = {};
    return TrackOwnership::Read(screen + ScreenProfile + 0x10, &length, sizeof length) &&
        TrackOwnership::Read(screen + ScreenProfile + 0x18, &capacity, sizeof capacity) && length == 4 &&
        (capacity < 16 || TrackOwnership::Pointer(screen + ScreenProfile, text)) &&
        TrackOwnership::Read(text, name, 4) && !memcmp(name, "test", 4);
}
inline void MapOpened(FH2Context* context, void*) {
    State& state = Get();
    uintptr_t screen = context->r14, controller = 0, table = 0;
    if (!FullScreenProfile(screen) || !TrackOwnership::Pointer(screen + ScreenController, controller) ||
        !TrackOwnership::Pointer(controller, table) || table != state.base + ControllerVtable)
        return;
    auto field = [controller](uintptr_t offset) { return reinterpret_cast<float*>(controller + offset); };
    if (!state.active.load(std::memory_order_acquire)) {
        // A cached scene keeps its controller: put the XAML zoom values back once the visit ends.
        if (controller == state.touched) {
            bool ours = true;
            for (unsigned i = 0; i < 4; ++i) ours = ours && *field(ZoomFields[i]) == state.zoom[i];
            if (ours) for (unsigned i = 0; i < 4; ++i) *field(ZoomFields[i]) = state.original[i];
            state.touched = 0;
        }
        return;
    }
    bool ours = true;
    for (unsigned i = 0; i < 4; ++i) ours = ours && *field(ZoomFields[i]) == state.zoom[i];
    if (!ours) {
        for (unsigned i = 0; i < 4; ++i) state.original[i] = *field(ZoomFields[i]);
        state.touched = controller;
    }
    for (unsigned i = 0; i < 4; ++i) *field(ZoomFields[i]) = state.zoom[i];
    *field(ViewMinX) = state.view[0];
    *field(ViewMinZ) = state.view[1];
    *field(ViewMaxX) = state.view[2];
    *field(ViewMaxZ) = state.view[3];
    *field(CursorMinX) = state.cursor[0];
    *field(CursorMinZ) = state.cursor[1];
    *field(CursorMaxX) = state.cursor[2];
    *field(CursorMaxZ) = state.cursor[3];
}
inline bool TileSupported(uintptr_t base) {
    static const unsigned char camera[] = {0x48,0x8b,0xc4,0x55,0x53,0x56,0x57,0x41,0x56,0x41,0x57,0x48,0x8d,0x68,0xa8};
    unsigned char actual[sizeof camera] = {};
    uintptr_t type = 0;
    return TrackOwnership::Read(base + TileCamera, actual, sizeof camera) && !memcmp(actual, camera, sizeof camera) &&
        TrackOwnership::Read(base + TileVtable + 3 * 8, &type, sizeof type) && type == base + TileType;
}
inline bool Supported(uintptr_t base) {
    static const unsigned char site[] = {0x4c,0x89,0x7c,0x24,0x40};
    static const unsigned char filter[] = {0x48,0x89,0x4c,0x24,0x08,0x55,0x56,0x57,0x41,0x54,0x41,0x55,0x41,0x56};
    static const unsigned char tag[] = {0x48,0x89,0x5c,0x24,0x08,0x48,0x89,0x6c,0x24,0x10,0x48,0x89,0x74,0x24,0x18};
    unsigned char actual[16] = {};
    uintptr_t type = 0;
    return TileSupported(base) &&
        TrackOwnership::Read(base + MapOpenedSite, actual, sizeof site) && !memcmp(actual, site, sizeof site) &&
        TrackOwnership::Read(base + ElementFilter, actual, sizeof filter) && !memcmp(actual, filter, sizeof filter) &&
        TrackOwnership::Read(base + ElementTag, actual, sizeof tag) && !memcmp(actual, tag, sizeof tag) &&
        TrackOwnership::Read(base + ControllerVtable + 3 * 8, &type, sizeof type) && type == base + ControllerType;
}
// Registered with the session hooks (enabled together); inert until Activate.
inline void Install(const FH2ModApi* api, FH2Mod* mod, uintptr_t base) {
    State& state = Get();
    if (state.installed || !api || api->api_version < 7 || !api->hook_function || !api->hook_instruction || !base)
        return;
    if (!Supported(base)) {
        api->log(mod, "error: pause map framing/icon hooks unavailable: unsupported game build");
        return;
    }
    state.base = base;
    void* original = nullptr;
    FH2Hook* filter = api->hook_function(mod, "TrackLoader pause map element filter",
                                         reinterpret_cast<void*>(base + ElementFilter),
                                         reinterpret_cast<void*>(&FilterHook), &original);
    if (filter && original) state.originalFilter = reinterpret_cast<bool(*)(void*, void*)>(original);
    else api->log(mod, "error: pause map element filter hook refused");
    original = nullptr;
    FH2Hook* tile = api->hook_function(mod, "TrackLoader pause menu map tile camera",
                                       reinterpret_cast<void*>(base + TileCamera),
                                       reinterpret_cast<void*>(&TileHook), &original);
    if (tile && original)
        state.originalTile = reinterpret_cast<void(*)(void*, const float*, const float*, float, float)>(original);
    else api->log(mod, "error: pause menu map tile camera hook refused");
    if (!api->hook_instruction(mod, "TrackLoader pause map framing", reinterpret_cast<void*>(base + MapOpenedSite),
                               MapOpened, nullptr))
        api->log(mod, "error: pause map framing hook refused");
    state.installed = true;
}
// The image covers x in [offsetX, offsetX + 1.75 size], z in [offsetZ, offsetZ + size].
inline void Activate(float size, float offsetX, float offsetZ) {
    State& state = Get();
    if (!state.installed) return;
    const float width = size * 1.75f, inset = size * 0.05f;
    // Visible height is 2 x distance; keep a 16:9 view inside the image with a small margin.
    const float farthest = 0.95f * (std::min)(size * 0.5f, width * 0.5f / (16.0f / 9.0f)), nearest = farthest * 0.4f;
    const float view[4] = {offsetX, offsetZ, offsetX + width, offsetZ + size};
    const float cursor[4] = {view[0] + inset, view[1] + inset, view[2] - inset, view[3] - inset};
    const float zoom[4] = {nearest, farthest, nearest * 1.6f, farthest * 1.6f};
    memcpy(state.view, view, sizeof view);
    memcpy(state.cursor, cursor, sizeof cursor);
    memcpy(state.zoom, zoom, sizeof zoom);
    // Image width fills the tile (slightly overscanned so no background shows at the slanted edges),
    // its top edge just under the tile's top edge; the bottom of the image is cropped.
    const float tileHeight = 0.98f * width / TileAspect;
    state.tileDistance = tileHeight * TileAspect / TileWidthPerDistance;
    state.tileCentre[0] = offsetX + width * 0.5f;
    state.tileCentre[1] = offsetZ + size + size * 0.01f - (0.5f + TileTargetDrop) * tileHeight;
    state.active.store(true, std::memory_order_release);
}
inline void Deactivate() { Get().active.store(false, std::memory_order_release); }
}

struct Values {
    const char* texture = nullptr;
    size_t length = 0;  // characters, without the terminator
    float size = 0;
    float offset[3] = {};
};

class Swap {
    struct SharedRef { uintptr_t object; uintptr_t control; };
    struct Layer { const char* stock; const char* track; size_t length; };
    struct Profile { const char* name; Layer layers[2]; unsigned count; };
    static constexpr Layer Low = {StockLow, TrackLow, sizeof StockLow - 1};
    static constexpr Layer High = {StockHigh, TrackHigh, sizeof StockHigh - 1};
    static constexpr Profile Profiles[] = {{"PauseMenu", {Low, Low}, 1}, {"test", {Low, High}, 2}};
    static constexpr unsigned ProfileCount = sizeof Profiles / sizeof Profiles[0];

    uintptr_t base = 0;
    bool applied[ProfileCount] = {};
    Preset active;

    static void Release(uintptr_t control) {
        if (!control) return;
        using Destroy = void(*)(void*);
        auto self = reinterpret_cast<void*>(control);
        auto table = *reinterpret_cast<Destroy**>(control);
        if (!InterlockedDecrement(reinterpret_cast<volatile LONG*>(control + 8))) {
            table[0](self);
            if (!InterlockedDecrement(reinterpret_cast<volatile LONG*>(control + 0xc))) table[1](self);
        }
    }
    static bool TextureAddress(uintptr_t symbolizer, uint64_t length, uintptr_t& text) {
        uint64_t actual = 0, capacity = 0;
        text = symbolizer + TextureField;
        return TrackOwnership::Read(symbolizer + TextureField + 0x10, &actual, sizeof actual) &&
            TrackOwnership::Read(symbolizer + TextureField + 0x18, &capacity, sizeof capacity) &&
            actual == length && capacity >= length &&
            (capacity < 16 || TrackOwnership::Pointer(symbolizer + TextureField, text));
    }
    static bool Matches(uintptr_t symbolizer, const Values& expected) {
        uintptr_t text = 0;
        float size = 0, offset[3] = {};
        char texture[64] = {};
        if (expected.length >= sizeof texture || !TextureAddress(symbolizer, expected.length, text) ||
            !TrackOwnership::Read(text, texture, expected.length + 1) ||
            !TrackOwnership::Read(symbolizer + SizeField, &size, sizeof size) ||
            !TrackOwnership::Read(symbolizer + OffsetField, offset, sizeof offset))
            return false;
        return !memcmp(texture, expected.texture, expected.length + 1) && size == expected.size &&
            !memcmp(offset, expected.offset, sizeof offset);
    }
    static Values Make(const char* texture, size_t length, float size, float x, float z) {
        Values values;
        values.texture = texture;
        values.length = length;
        values.size = size;
        values.offset[0] = x;
        values.offset[2] = z;
        return values;
    }
    Values Side(const Layer& layer, bool track) const {
        return track ? Make(layer.track, layer.length, active.size, active.offsetX, active.offsetZ) :
                       Make(layer.stock, layer.length, StockSize, StockOffset[0], StockOffset[2]);
    }
    static bool Put(uintptr_t symbolizer, uintptr_t text, const Values& values) {
        return Write(text, values.texture, values.length + 1) &&
            Write(symbolizer + SizeField, &values.size, sizeof values.size) &&
            Write(symbolizer + CurrentSizeField, &values.size, sizeof values.size) &&
            Write(symbolizer + OffsetField, values.offset, sizeof values.offset);
    }
    // Moves every background layer of one profile from stock to track values (or back).
    bool Retarget(const Profile& profile, bool toTrack, std::string& error) {
        uintptr_t manager = 0;
        if (!TrackOwnership::Pointer(base + ProfileManager, manager)) {
            error = "map profile manager is unavailable";
            return false;
        }
        SharedRef ref = {};
        reinterpret_cast<SharedRef*(*)(uintptr_t, SharedRef*, const char*)>(base + LookupProfile)(
            manager, &ref, profile.name);
        uintptr_t found[2] = {}, text[2] = {}, begin = 0, end = 0;
        unsigned matches[2] = {};
        unsigned char loaded = 0;
        if (ref.object && TrackOwnership::Read(ref.object + 8, &begin, sizeof begin) &&
            TrackOwnership::Read(ref.object + 0x10, &end, sizeof end) &&
            TrackOwnership::Read(ref.object + ProfileLoaded, &loaded, sizeof loaded) && begin && end >= begin &&
            (end - begin) % 16 == 0 && (end - begin) / 16 <= 512) {
            for (uintptr_t slot = begin; slot < end; slot += 16) {
                uintptr_t symbolizer = 0, table = 0;
                if (!TrackOwnership::Pointer(slot, symbolizer) || !TrackOwnership::Pointer(symbolizer, table) ||
                    table != base + TilesVtable)
                    continue;
                for (unsigned i = 0; i < profile.count; ++i) {
                    if (!Matches(symbolizer, Side(profile.layers[i], !toTrack))) continue;
                    found[i] = symbolizer;
                    ++matches[i];
                }
            }
        }
        Release(ref.control);
        for (unsigned i = 0; i < profile.count; ++i) {
            if (matches[i] != 1 || !TextureAddress(found[i], profile.layers[i].length, text[i])) {
                error = std::string(profile.name) + " map profile has " + std::to_string(matches[i]) +
                    " tiles symbolizers with the expected " + Side(profile.layers[i], !toTrack).texture;
                return false;
            }
        }
        using Resources = void(*)(void*, const char*);
        if (loaded) reinterpret_cast<Resources>(base + UnloadProfile)(nullptr, profile.name);
        bool written = true;
        for (unsigned i = 0; written && i < profile.count; ++i)
            written = Put(found[i], text[i], Side(profile.layers[i], toTrack));
        if (!written) {
            for (unsigned i = 0; i < profile.count; ++i) Put(found[i], text[i], Side(profile.layers[i], !toTrack));
            error = std::string("could not write ") + profile.name + " map background fields";
        }
        if (loaded) reinterpret_cast<Resources>(base + LoadProfile)(nullptr, profile.name);
        return written;
    }

public:
    bool Applied() const {
        for (bool value : applied) if (value) return true;
        return false;
    }
    // Retargets every profile it can; returns false (with the first error) if any could not be moved.
    bool Apply(uintptr_t image, const Preset& preset, std::string& error) {
        if (Applied() || !preset.enabled) return true;
        if (!preset.Valid() || !Supported(image, error)) {
            if (error.empty()) error = "invalid pause map preset";
            return false;
        }
        base = image;
        active = preset;
        bool ok = true;
        for (unsigned i = 0; i < ProfileCount; ++i) {
            std::string failure;
            applied[i] = Retarget(Profiles[i], true, failure);
            if (!applied[i] && ok) {
                error = failure;
                ok = false;
            }
        }
        return ok;
    }
    bool Restore(std::string& error) {
        bool ok = true;
        for (unsigned i = 0; i < ProfileCount; ++i) {
            if (!applied[i]) continue;
            std::string failure;
            if (Retarget(Profiles[i], false, failure)) applied[i] = false;
            else if (ok) {
                error = failure;
                ok = false;
            }
        }
        if (!Applied()) active = {};
        return ok;
    }
};
}
