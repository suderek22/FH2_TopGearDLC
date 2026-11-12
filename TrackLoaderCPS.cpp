#include "..\FH2-Mod-Platform\include\fh2mod.h"
#include <windows.h>
#include <cstdio>
#include <cstdlib>
#include <cerrno>
#include <climits>
#include <cwchar>
#include <cstring>
#include <string>
#include <vector>
#include "track_media.h"
#include "track_session.h"
#include "track_time_weather.h"
#ifdef TRACKLOADER_STANDALONE
#include "track_standalone.h"
#endif
#include "track_mount.h"
#include "track_browser.h"
#include "track_artwork.h"
#include <winsqlite\winsqlite3.h>
#include <atomic>
#pragma comment(lib, "winsqlite3.lib")

#ifdef TRACKLOADER_STANDALONE
static constexpr const char* ModName = "TrackLoader";
static TrackStandalone::Runtime g_standalone;
#else
static constexpr const char* ModName = "TrackLoaderCPS";
#endif
static constexpr const char* RuntimeInterface = "TrackLoader.RuntimeOwner";

struct Track {
    int id;
    std::string name;
    std::string reason;
    int environmentId;
    std::string mediaName;
    std::unique_ptr<TrackMedia::Package> media;
    std::unique_ptr<TrackMedia::Package> visit;
    bool experimental = false;
    TrackPose::Pose arrival = {};
    TrackLifecycle::Names lifecycle;
    bool restoreSavingOnReturn = false;
    bool saveDuringVisit = false;
    TrackTimeWeather::Preset timeWeather;
    TrackPauseMap::Preset pauseMap;
    std::wstring manifest;
};
static const FH2ModApi* g_api;
static FH2Mod* g_mod;
static std::vector<Track> g_tracks;
static std::atomic<TrackSession::Driver*> g_session{nullptr};
static int g_experimentalId = 0;
static std::atomic<bool> g_registrationVerified{false};
static std::mutex g_startupMutex;
static std::string g_startup = "diagnostic packs only";
static HANDLE g_protectionThread = nullptr;
static uintptr_t g_protectedBase = 0;
static std::string g_protectionError;
static std::vector<TrackBrowser::Item> g_browser;

static std::string Utf8(const wchar_t* text) {
    int count = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, text, -1, nullptr, 0, nullptr, nullptr);
    if (!count) return {};
    std::string result(count, '\0');
    if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, text, -1, &result[0], count, nullptr, nullptr))
        return {};
    result.pop_back();
    return result;
}
static void Log(const std::string& message) { g_api->log(g_mod, message.c_str()); }
static std::string ManifestText(const std::wstring& manifest, const wchar_t* section, const wchar_t* key) {
    wchar_t text[512] = {};
    const DWORD count = GetPrivateProfileStringW(section, key, L"", text, 512, manifest.c_str());
    if (count >= 511) {
        Log("error: browser metadata exceeds 510 characters: " + Utf8(key));
        return {};
    }
    return Utf8(text);
}
static void BrowserArtwork(TrackBrowser::Item& item, const std::wstring& manifest) {
    const auto posterHash = ManifestText(manifest, L"Browser", L"PosterSha256");
    const auto logoHash = ManifestText(manifest, L"Browser", L"LogoSha256");
    if (posterHash.empty() && logoHash.empty()) return;
    const auto slash = manifest.find_last_of(L'\\');
    std::string error;
    if (slash == std::wstring::npos ||
        !TrackArtwork::Publish(g_api, g_mod, item.id, manifest.substr(0, slash) + L"\\browser",
                              posterHash, logoHash, item.poster, item.logo, error))
        Log("error: track " + std::to_string(item.id) + " artwork unavailable: " + error);
}
static void BrowserOpen(void*, const char*) {
    auto session = g_session.load();
    if (!g_api->set_page_slot(g_mod, "Tracks", 1, session && session->InCustomTrack() ? 1 : 0))
        Log("error: could not refresh browser Airport action");
}
static bool BrowserMap(TrackLive::MapState& map, std::string& error) {
    if (!TrackLive::CaptureMap(g_protectedBase, map, error)) return false;
    if (!map.Settled()) {
        error = "wait for settled free roam before map travel";
        return false;
    }
    return true;
}
static const char* BrowserTileTitle(void*, const char*) {
    TrackLive::MapState map;
    std::string error;
    if (!BrowserMap(map, error)) {
        Log("error: Tracks tile map observation: " + error);
        return "Tracks";
    }
    return map.current == 542 ? "Tracks" : "Return to mainland";
}
static int BrowserReturn(int current, std::string& error) {
    if (current == 549) return FH2_PAGE_MAINLAND;
    auto session = g_session.load();
    if (session && session->InCustomTrack() && session->Queue(true, error)) return FH2_PAGE_RESUME;
    if (error.empty()) error = "return refused: no owned custom-track visit";
    return FH2_PAGE_HANDLED;
}
static int BrowserTile(void*, const char*, int) {
    TrackLive::MapState map;
    std::string error;
    if (BrowserMap(map, error)) {
        if (map.current == 542) return FH2_PAGE_IGNORED;
        const int result = BrowserReturn(map.current, error);
        if (result != FH2_PAGE_HANDLED) return result;
    }
    Log("error: mainland return refused: " + error);
    return FH2_PAGE_HANDLED;
}
static int BrowserButton(void*, const char*, int button) {
    if (button == FH2_BUTTON_LB || button == FH2_BUTTON_RB) {
        int selected = g_api->get_page_slot(g_mod, "Tracks", 0);
        const int count = static_cast<int>(g_browser.size());
        selected = (selected + (button == FH2_BUTTON_LB ? count - 1 : 1)) % count;
        if (!g_api->set_page_slot(g_mod, "Tracks", 0, selected) ||
            !g_api->set_page_slot(g_mod, "Tracks", 2, 0))
            Log("error: could not change browser selection");
        return FH2_PAGE_HANDLED_SELECT;
    }
    if (button != FH2_BUTTON_A && button != FH2_BUTTON_Y) return FH2_PAGE_IGNORED;
    auto session = g_session.load();
    std::string error;
    bool queued = false;
    int status = 2;
    TrackLive::MapState map;
    if (!BrowserMap(map, error)) {
        Log("error: browser travel refused: " + error);
        if (!g_api->set_page_slot(g_mod, "Tracks", 2, 2))
            Log("error: could not show browser travel refusal");
        return FH2_PAGE_HANDLED;
    }
    if (map.current != 542) {
        const int result = BrowserReturn(map.current, error);
        if (result != FH2_PAGE_HANDLED) return result;
        Log("error: browser return refused: " + error);
        return FH2_PAGE_HANDLED;
    }
    if (button == FH2_BUTTON_Y) {
        queued = session && session->Queue(true, error);
        if (!session) error = "native track runtime is unavailable";
    } else {
        const int selected = g_api->get_page_slot(g_mod, "Tracks", 0);
        if (selected < 0 || static_cast<size_t>(selected) >= g_browser.size()) {
            error = "invalid track browser selection";
        } else {
            const auto& item = g_browser[selected];
            if (item.id == 549 && item.playable) {
                Log("browser: native Storm Island travel requested; entitlement/install checks retained");
                return FH2_PAGE_ISLAND;
            } else if (!item.playable) {
                error = item.reason;
                status = 3;
            } else {
                for (const auto& track : g_tracks)
                    if (track.id == item.id && track.experimental) {
                        queued = session && session->QueueEntry(track.id, track.arrival, track.lifecycle,
                                                               track.restoreSavingOnReturn, track.timeWeather,
                                                               track.pauseMap, error, track.saveDuringVisit);
                        if (!session) error = "native track runtime is unavailable";
                        break;
                    }
            }
        }
    }
    if (!queued) Log("error: browser travel refused: " + error);
    else Log("browser travel queued; waiting for resumed free roam");
    if (!g_api->set_page_slot(g_mod, "Tracks", 2, queued ? 1 : status))
        Log("error: could not show browser travel result");
    return queued ? FH2_PAGE_RESUME : FH2_PAGE_HANDLED;
}
static void RegisterBrowser(const std::wstring& mods) {
    if (!g_api->add_page || !g_api->set_page_xaml || !g_api->get_page_slot ||
        !g_api->set_page_slot || !g_api->set_page_tile_text) {
        Log("error: Tracks browser requires platform page services");
        return;
    }
    TrackBrowser::Item island;
    island.id = 549;
    island.name = "Storm Island";
    island.author = "Playground Games";
    island.version = "Native DLC";
    island.playable = true;
    BrowserArtwork(island, mods + L"\\TrackLoaderAssets\\StormIsland\\track.ini");
    g_browser.push_back(std::move(island));
    for (const auto& track : g_tracks) {
        TrackBrowser::Item item;
        item.id = track.id;
        item.name = track.name;
        item.playable = track.experimental;
        item.reason = track.reason;
        item.author = ManifestText(track.manifest, L"Track", L"Author");
        item.version = ManifestText(track.manifest, L"Track", L"Version");
        item.repository = ManifestText(track.manifest, L"Track", L"GitHubRepo");
        BrowserArtwork(item, track.manifest);
        g_browser.push_back(std::move(item));
    }
    TrackBrowser::Order(g_browser);
    FH2Page page = {};
    page.size = sizeof page;
    page.key = "Tracks";
    page.title = "Tracks";
    page.description = "Storm Island and installed custom tracks.";
    page.tile = "IslandFeatured";
    page.tab_title = "DLC";
    page.on_open = BrowserOpen;
    page.on_button = BrowserButton;
    page.tile_title = BrowserTileTitle;
    page.on_tile = BrowserTile;
    if (!g_api->add_page(g_mod, &page) ||
        !g_api->set_page_xaml(g_mod, "Tracks", TrackBrowser::Resources().c_str(),
                              TrackBrowser::Content(g_browser).c_str(), TrackBrowser::Help().c_str())) {
        Log("error: could not register Tracks browser; native page unavailable");
        return;
    }
    if (!g_api->set_page_tile_text(g_mod, "Tracks", (std::to_string(g_browser.size()) + " tracks").c_str()))
        Log("error: Tracks browser tile text unavailable; use the local IslandFeatured platform build");
    Log("Tracks browser registered; native UI/texture validation pending");
}
static void StartupFailure(const std::string& error) {
    std::lock_guard<std::mutex> lock(g_startupMutex);
    g_startup = "experimental native startup failed: " + error;
    Log("error: " + g_startup);
}
static bool Vector(const wchar_t* text, float* output) {
    for (int i = 0; i < 3; ++i) {
        wchar_t* end = nullptr;
        errno = 0;
        float value = wcstof(text, &end);
        if (errno == ERANGE || end == text || !std::isfinite(value) ||
            (i == 2 ? *end != 0 : *end != L',')) return false;
        output[i] = value;
        text = end + 1;
    }
    return true;
}
static bool ReadTimeWeather(const std::wstring& manifest, TrackTimeWeather::Preset& preset,
                            std::string& error) {
    wchar_t enabled[16] = L"0", hours[32] = {}, freeze[16] = {},
            atmosphere[16] = {}, rain[16] = {}, environment[16] = {};
    GetPrivateProfileStringW(L"TimeWeather", L"Enabled", L"0", enabled, 16, manifest.c_str());
    GetPrivateProfileStringW(L"TimeWeather", L"TimeHours", L"", hours, 32, manifest.c_str());
    GetPrivateProfileStringW(L"TimeWeather", L"Freeze", L"", freeze, 16, manifest.c_str());
    GetPrivateProfileStringW(L"TimeWeather", L"Atmosphere", L"", atmosphere, 16, manifest.c_str());
    GetPrivateProfileStringW(L"TimeWeather", L"Rain", L"", rain, 16, manifest.c_str());
    GetPrivateProfileStringW(L"TimeWeather", L"Environment", L"", environment, 16, manifest.c_str());
    return TrackTimeWeather::ParsePreset(enabled, hours, freeze, atmosphere, rain, environment,
                                         preset, error);
}
// Optional [PauseMap]: <pack>\map\pausemap.swatchbin (low tile) and map\pausemap-high.bin (4x7 high
// tiles, concatenated row-major) replace the pause and full-screen map backgrounds while on track.
static bool g_pauseMapPublished = false;
static void ReadPauseMap(const std::wstring& manifest, int id, TrackPauseMap::Preset& preset) {
    preset = {};
    wchar_t enabled[16] = {}, size[32] = {}, x[32] = {}, z[32] = {};
    GetPrivateProfileStringW(L"PauseMap", L"Enabled", L"0", enabled, 16, manifest.c_str());
    if (!wcscmp(enabled, L"0")) return;
    GetPrivateProfileStringW(L"PauseMap", L"Size", L"", size, 32, manifest.c_str());
    GetPrivateProfileStringW(L"PauseMap", L"OffsetX", L"", x, 32, manifest.c_str());
    GetPrivateProfileStringW(L"PauseMap", L"OffsetZ", L"", z, 32, manifest.c_str());
    TrackPauseMap::Preset parsed;
    parsed.enabled = true;
    std::string error;
    const auto slash = manifest.find_last_of(L'\\');
    if (wcscmp(enabled, L"1") || !TrackTimeWeather::ParseFloat(size, parsed.size) ||
        !TrackTimeWeather::ParseFloat(x, parsed.offsetX) || !TrackTimeWeather::ParseFloat(z, parsed.offsetZ) ||
        !parsed.Valid()) {
        error = "PauseMap needs Enabled=1 and Size (100-12000), OffsetX, OffsetZ";
    } else if (g_pauseMapPublished) {
        error = "only one track may replace the pause map background";
    } else if (slash == std::wstring::npos ||
               !TrackPauseMap::Publish(g_api, g_mod, manifest.substr(0, slash) + L"\\map\\pausemap.swatchbin",
                                       ManifestText(manifest, L"PauseMap", L"Sha256"),
                                       manifest.substr(0, slash) + L"\\map\\pausemap-high.bin",
                                       ManifestText(manifest, L"PauseMap", L"HighSha256"), error)) {
        if (error.empty()) error = "pause map publication failed";
    } else {
        g_pauseMapPublished = true;
        preset = parsed;
        return;
    }
    Log("error: track " + std::to_string(id) + " pause map disabled: " + error);
}
static bool VerifyDatabase(const Track& track, std::string& error) {
    std::wstring path = std::wstring(g_api->game_dir) + L"\\mediaoverride\\stripped\\gamedbRC.slt";
    std::string filename = Utf8(path.c_str());
    sqlite3* db = nullptr;
    sqlite3_stmt* statement = nullptr;
    int rc = sqlite3_open_v2(filename.c_str(), &db, SQLITE_OPEN_READONLY, nullptr);
    const char* sql =
        "SELECT COUNT(*) FROM Tracks t JOIN Environments e ON t.EnvironmentId=e.Id "
        "WHERE t.id=?1 AND t.MediaTrack=?1 AND t.EnvironmentId=?2 "
        "AND t.MediaName=?3 AND e.MediaName=?3 "
        "AND t.RibbonIndex=0 AND t.Route=0 AND t.ContentId=0 AND t.Active=1 "
        "AND t.Renderable=1 AND t.IsInstalled=1 AND t.IsPurchased=1 AND e.ShowOnMap=0";
    if (rc == SQLITE_OK) rc = sqlite3_prepare_v2(db, sql, -1, &statement, nullptr);
    if (rc == SQLITE_OK) rc = sqlite3_bind_int(statement, 1, track.id);
    if (rc == SQLITE_OK) rc = sqlite3_bind_int(statement, 2, track.environmentId);
    if (rc == SQLITE_OK) rc = sqlite3_bind_text(statement, 3, track.mediaName.c_str(), -1, SQLITE_TRANSIENT);
    if (rc == SQLITE_OK) rc = sqlite3_step(statement);
    bool verified = rc == SQLITE_ROW && sqlite3_column_int(statement, 0) == 1;
    if (!verified)
        error = "experimental registration not present in the actual override database: " +
            std::string(db ? sqlite3_errmsg(db) : "cannot open database");
    if (statement) sqlite3_finalize(statement);
    if (db) sqlite3_close(db);
    return verified;
}
static DWORD WINAPI ProtectSession(void* argument) {
    std::unique_ptr<std::wstring> manifest(static_cast<std::wstring*>(argument));
    g_protectedBase = reinterpret_cast<uintptr_t>(g_api->wait_for_code(120000));
    if (!g_protectedBase) {
        g_protectionError = "timed out waiting for early native code protection";
        StartupFailure(g_protectionError);
        return 0;
    }
    // Protection and ownership hooks must precede slow media publication.
#ifdef TRACKLOADER_STANDALONE
    if (!g_standalone.Initialize(g_api, g_mod, g_protectedBase, g_protectionError)) {
        StartupFailure(g_protectionError);
        return 0;
    }
#endif
    auto driver = new TrackSession::Driver;
    g_session.store(driver);
    if (!driver->Protect(g_api, g_mod, g_protectedBase, g_protectionError))
        StartupFailure(g_protectionError);
    else g_api->set_notice(g_mod, "Custom tracks: SAVING DISABLED\nSaving resumes only after a verified mainland return");
    return 0;
}
static DWORD WINAPI StartSession(void*) {
    std::string error;
    if (!g_protectionThread || WaitForSingleObject(g_protectionThread, 125000) != WAIT_OBJECT_0) {
        StartupFailure("early native protection worker unavailable or timed out");
        return 0;
    }
    CloseHandle(g_protectionThread);
    g_protectionThread = nullptr;
    if (!g_protectionError.empty()) { StartupFailure(g_protectionError); return 0; }
    if (!g_api->wait_for_mods(120000)) {
        StartupFailure("timed out waiting for mods");
        return 0;
    }
    g_api->apply_database(g_mod);
    const Track* first = nullptr;
    for (const auto& track : g_tracks) {
        if (!track.experimental) continue;
        if (!VerifyDatabase(track, error)) {
            StartupFailure("track " + std::to_string(track.id) + ": " + error);
            return 0;
        }
        if (!first) first = &track;
    }
    if (!first) { StartupFailure("no enabled custom tracks"); return 0; }
    g_registrationVerified = true;
    {
        TrackMount::Mounter mounter;
        if (!mounter.Initialize(g_protectedBase, error)) { StartupFailure(error); return 0; }
        size_t mounted = 0;
        for (const auto& track : g_tracks) {
            if (!track.experimental || !track.media) continue;
            for (const auto& path : track.media->ArchivePaths()) {
                std::string resolved;
                if (!mounter.Mount(path, g_api->game_dir ? g_api->game_dir : L"", resolved, error)) {
                    StartupFailure("archive mount failed: " + error +
                                   (resolved.empty() ? std::string() : " (resolved " + resolved + ")"));
                    return 0;
                }
                Log("mounted " + path + " from " + resolved);
                ++mounted;
            }
        }
        if (!mounted) { StartupFailure("experimental pack has no archives to mount"); return 0; }
    }
#ifdef TRACKLOADER_STANDALONE
    const auto cooperation = g_standalone.Services();
#else
    auto cooperation = static_cast<const TrackVisit::Api*>(
        g_api->get_interface(CPS_TRACK_VISIT_INTERFACE, CPS_TRACK_VISIT_VERSION));
    if (!cooperation) {
        StartupFailure("updated CPS TrackVisit cooperation DLL required");
        return 0;
    }
#endif
    auto driver = g_session.load();
    if (!driver || !driver->Initialize(g_api, g_mod, g_protectedBase, cooperation, first->id, first->arrival, error,
                                      first->restoreSavingOnReturn, first->lifecycle, first->saveDuringVisit)) {
        if (!driver) error = "early native protection driver unavailable";
        Log("error: " + error);
        return 0;
    }
    if (!first->saveDuringVisit) g_api->set_notice(g_mod, first->restoreSavingOnReturn ?
        "Custom tracks: SAVING DISABLED\nSaving resumes after verified mainland return" :
        "Custom tracks: SAVING DISABLED\nDisable track activation and restart to restore saving");
    return 0;
}
static std::string PhysicalMedia(const Track& track) {
    if (!g_api->game_dir || !*g_api->game_dir) return "unavailable (game Mount not supplied by platform)";
    std::wstring path = std::wstring(g_api->game_dir) + L"\\mediaoverride\\tracks\\";
    std::string directory = Utf8(path.c_str());
    if (directory.empty()) return "unavailable (game Mount is not valid UTF-16)";
    return directory + track.mediaName;
}
static bool CustomId(const wchar_t* text, int* value) {
    wchar_t* end = nullptr;
    errno = 0;
    long parsed = wcstol(text, &end, 10);
    if (errno == ERANGE || end == text || *end || parsed < 10000 || parsed > INT_MAX) return false;
    *value = static_cast<int>(parsed);
    return true;
}
static bool MediaName(const wchar_t* text) {
    size_t length = wcslen(text);
    if (!length || length > 48) return false;
    for (size_t i = 0; i < length; ++i) {
        wchar_t c = text[i];
        bool letter = (c >= L'A' && c <= L'Z') || (c >= L'a' && c <= L'z');
        if (!letter && (i == 0 || !((c >= L'0' && c <= L'9') || c == L'_'))) return false;
    }
    return true;
}
static void Commands(void*, const char* command, const char* args, char* reply, size_t size) {
    std::string result;
    if (!strcmp(command, "list")) {
        result = std::string(ModName) + " catalog (in-game map validation still required):";
        for (const auto& track : g_tracks) {
            result += "\n" + std::to_string(track.id) + " " + track.name +
                (track.experimental ? " [experimental]: " : " [blocked]: ") + track.reason;
            if (track.environmentId)
                result += " [local DLC; media=" + track.mediaName + "]";
        }
        if (g_tracks.empty()) result += "\nNo staged tracks found.";
    } else if (!strcmp(command, "status")) {
        size_t dlc = 0, enabled = 0;
        for (const auto& track : g_tracks) {
            if (track.environmentId) ++dlc;
            if (track.experimental) ++enabled;
        }
        result = std::string(ModName) + " reserved_tracks=" + std::to_string(g_tracks.size()) +
                 " local_dlc_packs=" + std::to_string(dlc) +
                 " registered_tracks=" + std::to_string(g_registrationVerified ? enabled : 0) +
                 " in_game_validated_tracks=0";
        auto session = g_session.load();
        if (session) result += "\n" + session->Status();
        else {
            std::lock_guard<std::mutex> lock(g_startupMutex);
            result += "\n" + g_startup;
        }
    } else if (!strcmp(command, "return")) {
        auto session = g_session.load();
        if (*args) result = "error: return takes no arguments";
        else if (!session) result = "error: experimental native runtime is unavailable";
        else {
            std::string error;
            result = session->Queue(true, error) ? "mainland return queued on native free-roam tick" : "error: " + error;
        }
        Log(result);
    } else {
        char* end = nullptr;
        errno = 0;
        long id = strtol(args, &end, 10);
        const Track* found = nullptr;
        if (errno != ERANGE && end != args && *end == '\0')
            for (const auto& track : g_tracks) if (track.id == id) found = &track;
        if (found && !strcmp(command, "pack")) {
            result = found->environmentId ?
                "pack id=" + std::to_string(found->id) + " environment=" +
                std::to_string(found->environmentId) + " delivery=mod-managed media=game:\\media\\tracks\\" +
                found->mediaName + " physical_media=" + PhysicalMedia(*found) +
                " verified_media_files=" + std::to_string(found->media ? found->media->Count() : 0) +
                (found->experimental ? std::string(" file_mount=enabled state=experimental registration=") +
                                       (g_registrationVerified ? "verified; " : "pending-or-failed; ") :
                                       " file_mount=disabled state=blocked registration=disabled; ") + found->reason :
                "pack id=" + std::to_string(found->id) + " state=blocked delivery=diagnostic; " + found->reason;
            if (found->experimental) {
                result += " lifecycle=" + found->lifecycle.group + " arrival=";
                for (int i = 0; i < 3; ++i)
                    result += (i ? "," : "") + std::to_string(found->arrival.position[i]);
            }
        } else if (found && found->experimental && !strcmp(command, "travel")) {
            auto session = g_session.load();
            std::string error;
            result = !session ? "error: experimental native startup is not available" :
                session->QueueEntry(found->id, found->arrival, found->lifecycle,
                                    found->restoreSavingOnReturn, found->timeWeather, found->pauseMap, error,
                                    found->saveDuringVisit) ?
                    "native entry queued; saving protected during custom visit" :
                                              "error: " + error;
        } else result = found ? "error: " + found->name + " (" + std::to_string(found->id) +
                          ") is not playable: " + found->reason :
                         std::string("error: unknown reserved track ID; use ") + ModName + " list";
        Log(result);
    }
    strncpy_s(reply, size, result.c_str(), _TRUNCATE);
}

static void DiscoverTracks(const std::wstring& root) {
    WIN32_FIND_DATAW entry = {};
    HANDLE search = FindFirstFileW((root + L"\\*").c_str(), &entry);
    if (search != INVALID_HANDLE_VALUE) {
        do {
            if (!(entry.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || entry.cFileName[0] == L'.') continue;
            if (entry.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) {
                Log("error: skipping redirected track pack directory");
                continue;
            }
            std::wstring path = root + L"\\" + entry.cFileName + L"\\track.ini";
            wchar_t idText[64] = {}, name[128] = {}, reason[512] = {}, state[32] = {};
            GetPrivateProfileStringW(L"Track", L"Id", L"", idText, 64, path.c_str());
            GetPrivateProfileStringW(L"Track", L"Name", L"", name, 128, path.c_str());
            GetPrivateProfileStringW(L"Track", L"State", L"", state, 32, path.c_str());
            GetPrivateProfileStringW(L"Track", L"Reason", L"", reason, 512, path.c_str());
            int id = 0;
            bool invalidId = !CustomId(idText, &id);
            wchar_t format[32] = {}, environmentText[64] = {}, media[64] = {},
                    delivery[32] = {}, registration[64] = {};
            GetPrivateProfileStringW(L"LocalDLC", L"Format", L"", format, 32, path.c_str());
            GetPrivateProfileStringW(L"LocalDLC", L"EnvironmentId", L"", environmentText, 64, path.c_str());
            GetPrivateProfileStringW(L"LocalDLC", L"MediaName", L"", media, 64, path.c_str());
            GetPrivateProfileStringW(L"LocalDLC", L"Delivery", L"", delivery, 32, path.c_str());
            GetPrivateProfileStringW(L"LocalDLC", L"Registration", L"", registration, 64, path.c_str());
            bool localDlc = *format || *environmentText || *media || *delivery || *registration;
            int environmentId = 0;
            bool invalidDlc = localDlc && (wcscmp(format, L"1") ||
                !CustomId(environmentText, &environmentId) || !MediaName(media) ||
                wcscmp(delivery, L"mod-managed") || wcscmp(registration, L"registration.sql"));
            bool duplicate = false;
            std::string mediaName = Utf8(media);
            for (const auto& track : g_tracks) {
                if (track.id == id || (localDlc && track.environmentId &&
                    (track.environmentId == environmentId ||
                     !_stricmp(track.mediaName.c_str(), mediaName.c_str())))) duplicate = true;
            }
            std::string label = Utf8(name), detail = Utf8(reason);
            bool experimental = !wcscmp(state, L"experimental");
            TrackTimeWeather::Preset timeWeather;
            if (invalidId || invalidDlc || duplicate || label.empty() || detail.empty() ||
                (wcscmp(state, L"blocked") && !experimental)) {
                Log("error: invalid or duplicate diagnostic manifest: " + Utf8(entry.cFileName));
                continue;
            }
            std::string timeWeatherError;
            if (experimental && !ReadTimeWeather(path, timeWeather, timeWeatherError)) {
                detail = "invalid time/weather preset: " + timeWeatherError;
                g_tracks.push_back({id, label, detail, environmentId, mediaName, nullptr, nullptr, false});
                g_tracks.back().manifest = path;
                Log("error: track " + std::to_string(id) + ": " + detail);
                continue;
            }
            std::unique_ptr<TrackMedia::Package> package;
            TrackLifecycle::Names lifecycle;
            wchar_t lifecycleText[64] = {};
            GetPrivateProfileStringW(L"Experimental", L"LifecycleNamespace", L"trackloadercps",
                                     lifecycleText, 64, path.c_str());
            lifecycle.group = Utf8(lifecycleText);
            if (!TrackLifecycle::Valid(lifecycle.group)) {
                Log("error: invalid lifecycle namespace for track " + std::to_string(id));
                continue;
            }
            wchar_t armed[16] = {}, legacyArmed[16] = {};
            GetPrivateProfileStringW(L"Experimental", L"SessionOnly", L"", legacyArmed, 16, path.c_str());
            GetPrivateProfileStringW(L"Experimental", L"Enabled", legacyArmed, armed, 16, path.c_str());
            if (experimental && localDlc &&
                !wcscmp(armed, L"1") && !g_protectionThread && g_api->wait_for_code &&
                g_api->wait_for_mods && g_api->get_interface && g_api->add_sql &&
                g_api->apply_database && g_api->set_notice) {
                auto manifest = std::make_unique<std::wstring>(path);
                g_protectionThread = CreateThread(nullptr, 0, ProtectSession, manifest.get(), 0, nullptr);
                if (!g_protectionThread) {
                    StartupFailure("cannot start early protection worker: " + std::to_string(GetLastError()));
                    continue;
                }
                manifest.release();
            }
            wchar_t directory[80] = {}, hash[80] = {};
            GetPrivateProfileStringW(L"Media", L"Directory", L"", directory, 80, path.c_str());
            GetPrivateProfileStringW(L"Media", L"CatalogSha256", L"", hash, 80, path.c_str());
            if (*directory || *hash) {
                bool valid = localDlc && *directory && wcslen(directory) <= 64 && wcslen(hash) == 64;
                for (const wchar_t* p = directory; *p; ++p)
                    if (!((*p >= L'A' && *p <= L'Z') || (*p >= L'a' && *p <= L'z') ||
                          (*p >= L'0' && *p <= L'9') || *p == L'-' || *p == L'_')) valid = false;
                for (const wchar_t* p = hash; *p; ++p)
                    if (!((*p >= L'0' && *p <= L'9') || (*p >= L'a' && *p <= L'f'))) valid = false;
                std::string error;
                if (valid) {
                    package = std::make_unique<TrackMedia::Package>();
                    valid = package->Prepare(root + L"\\" + entry.cFileName + L"\\" + directory,
                                             mediaName, Utf8(hash), error);
                } else error = "invalid media directory/catalog hash";
                if (!valid) {
                    package.reset();
                    detail = "Media preflight failed: " + error + "; custom travel remains disabled.";
                    Log("error: track " + std::to_string(id) + ": " + detail);
                } else Log("track " + std::to_string(id) + ": verified and pinned " +
                           std::to_string(package->Count()) + " custom media files; not published");
            }
            std::unique_ptr<TrackMedia::Package> visit;
            TrackPose::Pose arrival = {};
            bool restoreSavingOnReturn = false;
            bool saveDuringVisit = false;
            std::string visitDigest;
            if (experimental) {
                wchar_t sessionOnly[16] = {}, visitDirectory[80] = {}, visitHash[80] = {},
                    sqlHash[80] = {}, position[128] = {}, facing[128] = {};
                wcscpy_s(sessionOnly, armed);
                GetPrivateProfileStringW(L"Experimental", L"VisitDirectory", L"", visitDirectory, 80, path.c_str());
                GetPrivateProfileStringW(L"Experimental", L"VisitCatalogSha256", L"", visitHash, 80, path.c_str());
                GetPrivateProfileStringW(L"Experimental", L"RegistrationSha256", L"", sqlHash, 80, path.c_str());
                GetPrivateProfileStringW(L"Experimental", L"ArrivalPosition", L"", position, 128, path.c_str());
                GetPrivateProfileStringW(L"Experimental", L"ArrivalFacing", L"", facing, 128, path.c_str());
                if (!wcscmp(sessionOnly, L"0")) {
                    experimental = false;
                    detail = "Track activation disabled; set Experimental.Enabled=1 and restart to enable.";
                    g_tracks.push_back({id, label, detail, environmentId, mediaName, std::move(package), nullptr, false});
                    g_tracks.back().manifest = path;
                    Log("track " + std::to_string(id) + ": " + detail);
                    continue;
                }
                arrival.position[3] = 1;
                wchar_t restore[16] = {}, duringVisit[16] = {};
                GetPrivateProfileStringW(L"Experimental", L"RestoreSavingOnReturn", L"0", restore, 16, path.c_str());
                GetPrivateProfileStringW(L"Experimental", L"SaveDuringVisit", L"0", duringVisit, 16, path.c_str());
                std::string error;
                bool valid = localDlc && package && !wcscmp(sessionOnly, L"1") &&
                    !wcscmp(visitDirectory, L"visit") && Vector(position, arrival.position) &&
                    Vector(facing, arrival.facing) && TrackPose::Valid(arrival) &&
                    g_api->wait_for_mods && g_api->get_interface && g_api->wait_for_code &&
                    g_api->add_sql && g_api->apply_database && g_api->set_notice;
                valid = valid && (!wcscmp(restore, L"0") || !wcscmp(restore, L"1")) &&
                    (!wcscmp(duringVisit, L"0") || !wcscmp(duringVisit, L"1"));
                visitDigest = Utf8(visitHash);
                for (const auto& track : g_tracks) {
                    if (track.experimental && track.lifecycle.group == lifecycle.group) {
                        valid = false;
                        error = "lifecycle namespace is already used by track " + std::to_string(track.id);
                    }
                }
                if (valid) {
                    visit = std::make_unique<TrackMedia::Package>();
                    valid = visit->Prepare(root + L"\\" + entry.cFileName + L"\\visit",
                                           mediaName, visitDigest, error, true, lifecycle);
                }
                TrackMedia::MappedFile sql;
                std::string digest;
                std::wstring sqlPath = root + L"\\" + entry.cFileName + L"\\registration.sql";
                if (valid) valid = TrackMedia::PlainPath(sqlPath, error) && sql.Open(sqlPath, error) &&
                    sql.size < 1024 * 1024 && TrackMedia::Sha256(sql, digest, error) && digest == Utf8(sqlHash);
                if (valid) valid = package->PublishTo(g_api, g_mod, g_api->game_dir ? g_api->game_dir : L"", error) &&
                    visit->Publish(g_api, g_mod, error);
                if (!valid) {
                    if (error.empty()) error = "invalid experimental manifest, registration hash or unavailable services";
                    Log("error: experimental pack refused: " + error);
                    if (package) package->Release();
                    if (visit) visit->Release();
                    continue;
                }
                std::string statements(static_cast<const char*>(sql.data), sql.size);
                g_api->add_sql(g_mod, statements.c_str());
                if (!g_experimentalId) g_experimentalId = id;
                saveDuringVisit = !wcscmp(duringVisit, L"1");
                restoreSavingOnReturn = saveDuringVisit || !wcscmp(restore, L"1");
                if (saveDuringVisit)
                    Log("native saving stays enabled, including on this track; track saves are rewritten to resume at the mainland Airport");
                else if (restoreSavingOnReturn)
                    Log("warning: saving resumes after verified mainland return; career/profile changes are NOT audited (explicit pack opt-in)");
            }
            g_tracks.push_back({id, label, detail, environmentId, mediaName, std::move(package), std::move(visit), experimental});
            auto& added = g_tracks.back();
            added.arrival = arrival;
            added.lifecycle = lifecycle;
            added.restoreSavingOnReturn = restoreSavingOnReturn;
            added.saveDuringVisit = saveDuringVisit;
            added.timeWeather = timeWeather;
            added.manifest = path;
            if (experimental && added.media) ReadPauseMap(path, id, added.pauseMap);
            Log("reserved track " + std::to_string(id) + ": " + label +
                (experimental ? "; experimental: " : "; blocked: ") + detail);
        } while (FindNextFileW(search, &entry));
        DWORD error = GetLastError();
        FindClose(search);
        if (error != ERROR_NO_MORE_FILES) Log("error: track enumeration failed: " + std::to_string(error));
    } else if (GetLastError() != ERROR_FILE_NOT_FOUND && GetLastError() != ERROR_PATH_NOT_FOUND) {
        Log("error: cannot enumerate custom track directory: " + std::to_string(GetLastError()));
    }
}

FH2MOD_EXPORT int fh2mod_init(const FH2ModApi* api) {
    if (!api || api->api_version < 9 || api->size < sizeof(FH2ModApi)) return 0;
    g_api = api;
    FH2ModInfo info = {};
    info.size = sizeof info;
    info.name = ModName;
    info.version = "0.6.0-dev-beta3";
    info.platform_version = "2.8.0";
    info.description = "Persistent experimental multi-map travel with verified mainland return.";
    info.offline_only = 1;
    g_mod = api->register_mod(&info);
    if (!g_mod) return 0;
    if (api->get_interface && api->publish_interface) {
        if (api->get_interface(RuntimeInterface, 1)) {
            Log("error: another TrackLoader variant owns this process; install only TrackLoader or TrackLoaderCPS");
            return 0;
        }
        api->publish_interface(g_mod, RuntimeInterface, 1, ModName);
        if (api->get_interface(RuntimeInterface, 1) != ModName) {
            Log("error: could not acquire exclusive TrackLoader runtime ownership");
            return 0;
        }
    }
    Log("runtime=" + Utf8(api->azimuth_dir) +
        "; custom track and visit files publish through Mount\\mediaoverride; track archives mount natively at runtime");
    const std::wstring mods = std::wstring(api->azimuth_dir) + L"\\fh2-mods";
    DiscoverTracks(mods + L"\\CustomTracks");
    DiscoverTracks(mods + L"\\TrackPacks");
    for (const auto& track : g_tracks) {
        if (!track.experimental || !track.saveDuringVisit) continue;
        if (!api->request_save_snapshot ||
            !api->request_save_snapshot(g_mod, "TrackLoader keeps native saving on during custom track visits", 3))
            Log("warning: no save snapshot will be taken this session (see platform log)");
        break;
    }
    RegisterBrowser(mods);
    if (!api->add_command(g_mod, "list", "staged custom tracks and compatibility blockers", Commands, nullptr))
        Log("error: could not register list command");
    if (!api->add_command(g_mod, "status", "native test state and registration; in-game validation pending", Commands, nullptr))
        Log("error: could not register status command");
    if (!api->add_command(g_mod, "travel", "travel <ID>: native entry for experimental packs", Commands, nullptr))
        Log("error: could not register travel command");
    if (!api->add_command(g_mod, "pack", "pack <ID>: local DLC namespace and registration state", Commands, nullptr))
        Log("error: could not register pack command");
    if (!api->add_command(g_mod, "return", "return: verified mainland Airport arrival", Commands, nullptr))
        Log("error: could not register return command");
    if (g_experimentalId) {
        {
            std::lock_guard<std::mutex> lock(g_startupMutex);
            if (g_startup == "diagnostic packs only") g_startup = "experimental native startup pending";
        }
        HANDLE worker = CreateThread(nullptr, 0, StartSession, nullptr, 0, nullptr);
        if (worker) CloseHandle(worker);
        else StartupFailure("could not create native runtime thread: " + std::to_string(GetLastError()));
    } else {
        if (g_protectionThread) {
            Log("error: armed pack preflight failed; early protection may be active; restart restores saving");
        } else {
            if (api->set_notice) api->set_notice(g_mod, nullptr);
            Log("diagnostic/disarmed packs only; no database, files, saves or native code changed");
        }
    }
    return 0;
}
