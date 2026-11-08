#include "..\CarPackStudio\native\include\fh2mod.h"
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
#include "track_mount.h"
#include <winsqlite\winsqlite3.h>
#include <atomic>
#pragma comment(lib, "winsqlite3.lib")

struct Track {
    int id;
    std::string name;
    std::string reason;
    int environmentId;
    std::string mediaName;
    std::unique_ptr<TrackMedia::Package> media;
    std::unique_ptr<TrackMedia::Package> visit;
    bool experimental = false;
};
static const FH2ModApi* g_api;
static FH2Mod* g_mod;
static std::vector<Track> g_tracks;
static std::atomic<TrackSession::Driver*> g_session{nullptr};
static TrackPose::Pose g_arrival = {};
static int g_experimentalId = 0;
static std::atomic<bool> g_registrationVerified{false};
static std::mutex g_startupMutex;
static std::string g_startup = "diagnostic packs only";
static HANDLE g_protectionThread = nullptr;
static uintptr_t g_protectedBase = 0;
static std::string g_protectionError;

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
static bool VerifyDatabase(std::string& error) {
    std::wstring path = std::wstring(g_api->game_dir) + L"\\mediaoverride\\stripped\\gamedbRC.slt";
    std::string filename = Utf8(path.c_str());
    sqlite3* db = nullptr;
    sqlite3_stmt* statement = nullptr;
    int rc = sqlite3_open_v2(filename.c_str(), &db, SQLITE_OPEN_READONLY, nullptr);
    const char* sql =
        "SELECT COUNT(*) FROM Tracks t JOIN Environments e ON t.EnvironmentId=e.Id "
        "WHERE t.id=10000 AND t.MediaTrack=10000 AND t.EnvironmentId=10000 "
        "AND t.MediaName='TopGearCPS' AND e.MediaName='TopGearCPS' "
        "AND t.RibbonIndex=0 AND t.Route=0 AND t.ContentId=0 AND t.Active=1 "
        "AND t.Renderable=1 AND t.IsInstalled=1 AND t.IsPurchased=1 AND e.ShowOnMap=0";
    if (rc == SQLITE_OK) rc = sqlite3_prepare_v2(db, sql, -1, &statement, nullptr);
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
    if (!WritePrivateProfileStringW(L"Experimental", L"SessionOnly", L"0", manifest->c_str())) {
        g_protectionError = "cannot disarm early one-shot protection: " + std::to_string(GetLastError());
        StartupFailure(g_protectionError);
        return 0;
    }
    // Protection and ownership hooks must precede slow media publication.
    auto driver = new TrackSession::Driver;
    g_session.store(driver);
    if (!driver->Protect(g_api, g_mod, g_protectedBase, g_protectionError))
        StartupFailure(g_protectionError);
    else g_api->set_notice(g_mod, "Top Gear test: SAVING DISABLED\nNext restart restores saving");
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
    if (!VerifyDatabase(error)) { StartupFailure(error); return 0; }
    g_registrationVerified = true;
    {
        TrackMount::Mounter mounter;
        if (!mounter.Initialize(g_protectedBase, error)) { StartupFailure(error); return 0; }
        size_t mounted = 0;
        for (const auto& track : g_tracks) {
            if (track.id != g_experimentalId || !track.media) continue;
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
    auto cooperation = static_cast<const CPSTrackVisitApi*>(
        g_api->get_interface(CPS_TRACK_VISIT_INTERFACE, CPS_TRACK_VISIT_VERSION));
    if (!cooperation) {
        StartupFailure("updated CPS TrackVisit cooperation DLL required");
        return 0;
    }
    auto driver = g_session.load();
    if (!driver || !driver->Initialize(g_api, g_mod, g_protectedBase, cooperation, g_experimentalId, g_arrival, error)) {
        if (!driver) error = "early native protection driver unavailable";
        Log("error: " + error);
        return 0;
    }
    g_api->set_notice(g_mod, "Top Gear test: SAVING DISABLED\nNext restart restores saving");
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
        result = "TrackLoaderCPS catalog (in-game map validation still required):";
        for (const auto& track : g_tracks) {
            result += "\n" + std::to_string(track.id) + " " + track.name +
                (track.experimental ? " [session-only test]: " : " [blocked]: ") + track.reason;
            if (track.environmentId)
                result += " [local DLC; media=" + track.mediaName + "]";
        }
        if (g_tracks.empty()) result += "\nNo staged tracks found.";
    } else if (!strcmp(command, "status")) {
        size_t dlc = 0;
        for (const auto& track : g_tracks) if (track.environmentId) ++dlc;
        result = "TrackLoaderCPS reserved_tracks=" + std::to_string(g_tracks.size()) +
                 " local_dlc_packs=" + std::to_string(dlc) +
                 " registered_tracks=" + std::to_string(g_registrationVerified ? 1 : 0) +
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
            result = session->Queue(true, error) ? "mainland return queued on native CPS tick" : "error: " + error;
        }
        Log(result);
    } else {
        char* end = nullptr;
        errno = 0;
        long id = strtol(args, &end, 10);
        const Track* found = nullptr;
        if (errno != ERANGE && end != args && *end == '\0')
            for (const auto& track : g_tracks) if (track.id == id) found = &track;
        if (found && !strcmp(command, "pack"))
            result = found->environmentId ?
                "pack id=" + std::to_string(found->id) + " environment=" +
                std::to_string(found->environmentId) + " delivery=mod-managed media=game:\\media\\tracks\\" +
                found->mediaName + " physical_media=" + PhysicalMedia(*found) +
                " verified_media_files=" + std::to_string(found->media ? found->media->Count() : 0) +
                (found->experimental ? std::string(" file_mount=enabled state=session-only-test registration=") +
                                       (g_registrationVerified ? "verified; " : "pending-or-failed; ") :
                                       " file_mount=disabled state=blocked registration=disabled; ") + found->reason :
                "pack id=" + std::to_string(found->id) + " state=blocked delivery=diagnostic; " + found->reason;
        else if (found && found->experimental && !strcmp(command, "travel")) {
            auto session = g_session.load();
            std::string error;
            result = !session ? "error: experimental native startup is not available" :
                session->Queue(false, error) ? "session-only native entry queued; saving disabled until restart" :
                                              "error: " + error;
        } else result = found ? "error: " + found->name + " (" + std::to_string(found->id) +
                          ") is not playable: " + found->reason :
                         "error: unknown reserved track ID; use TrackLoaderCPS list";
        Log(result);
    }
    strncpy_s(reply, size, result.c_str(), _TRUNCATE);
}

FH2MOD_EXPORT int fh2mod_init(const FH2ModApi* api) {
    if (!api || api->api_version < 9 || api->size < sizeof(FH2ModApi)) return 0;
    g_api = api;
    FH2ModInfo info = {};
    info.size = sizeof info;
    info.name = "TrackLoaderCPS";
    info.version = "0.2.0-test";
    info.platform_version = "2.8.0";
    info.description = "Experimental session-only custom-map travel; saving disabled until restart.";
    info.offline_only = 1;
    g_mod = api->register_mod(&info);
    if (!g_mod) return 0;
    Log("runtime=" + Utf8(api->azimuth_dir) +
        "; custom track and visit files publish through Mount\\mediaoverride; track archives mount natively at runtime");

    std::wstring root = std::wstring(api->azimuth_dir) + L"\\fh2-mods\\TrackPacks";
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
            if (invalidId || invalidDlc || duplicate || label.empty() || detail.empty() ||
                (wcscmp(state, L"blocked") && !experimental)) {
                Log("error: invalid or duplicate diagnostic manifest: " + Utf8(entry.cFileName));
                continue;
            }
            std::unique_ptr<TrackMedia::Package> package;
            wchar_t armed[16] = {};
            GetPrivateProfileStringW(L"Experimental", L"SessionOnly", L"", armed, 16, path.c_str());
            if (experimental && id == 10000 && environmentId == 10000 && mediaName == "TopGearCPS" &&
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
                    detail = "One-shot test consumed; normal saving is not suppressed. Set SessionOnly=1 before another test launch.";
                    g_tracks.push_back({id, label, detail, environmentId, mediaName, std::move(package), nullptr, false});
                    Log("track " + std::to_string(id) + ": " + detail);
                    continue;
                }
                TrackPose::Pose arrival = {};
                arrival.position[3] = 1;
                std::string error;
                bool valid = id == 10000 && environmentId == 10000 && mediaName == "TopGearCPS" &&
                    !g_experimentalId && package && !wcscmp(sessionOnly, L"1") &&
                    !wcscmp(visitDirectory, L"visit") && Vector(position, arrival.position) &&
                    Vector(facing, arrival.facing) && TrackPose::Valid(arrival) &&
                    g_api->wait_for_mods && g_api->get_interface && g_api->wait_for_code &&
                    g_api->add_sql && g_api->apply_database && g_api->set_notice;
                if (valid) {
                    visit = std::make_unique<TrackMedia::Package>();
                    valid = visit->Prepare(root + L"\\" + entry.cFileName + L"\\visit",
                                           mediaName, Utf8(visitHash), error, true);
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
                g_experimentalId = id;
                g_arrival = arrival;
            }
            g_tracks.push_back({id, label, detail, environmentId, mediaName, std::move(package), std::move(visit), experimental});
            Log("reserved track " + std::to_string(id) + ": " + label +
                (experimental ? "; experimental: " : "; blocked: ") + detail);
        } while (FindNextFileW(search, &entry));
        DWORD error = GetLastError();
        FindClose(search);
        if (error != ERROR_NO_MORE_FILES) Log("error: track enumeration failed: " + std::to_string(error));
    } else if (GetLastError() != ERROR_FILE_NOT_FOUND && GetLastError() != ERROR_PATH_NOT_FOUND) {
        Log("error: cannot enumerate TrackPacks: " + std::to_string(GetLastError()));
    }
    if (!api->add_command(g_mod, "list", "staged custom tracks and compatibility blockers", Commands, nullptr))
        Log("error: could not register list command");
    if (!api->add_command(g_mod, "status", "native test state and registration; in-game validation pending", Commands, nullptr))
        Log("error: could not register status command");
    if (!api->add_command(g_mod, "travel", "travel <ID>: session-only native entry for experimental packs", Commands, nullptr))
        Log("error: could not register travel command");
    if (!api->add_command(g_mod, "pack", "pack <ID>: local DLC namespace and registration state", Commands, nullptr))
        Log("error: could not register pack command");
    if (!api->add_command(g_mod, "return", "return: session-only return to captured mainland departure pose", Commands, nullptr))
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
