#include "..\..\CarPackStudio\native\include\fh2mod.h"
#include <windows.h>
#include <cassert>
#include <cstdio>
#include <map>
#include <string>

static std::map<std::string, FH2CommandFn> commands;
static int handle;
static unsigned files, sqlCalls, clears;
static HANDLE startupFailed;
static HANDLE codeRequested;
static FH2Mod* Register(const FH2ModInfo* info) {
    assert(!strcmp(info->name, "TrackLoaderCPS"));
    assert(info->offline_only == 1);
    return reinterpret_cast<FH2Mod*>(&handle);
}
static void Log(FH2Mod*, const char* line) {
    printf("%s\n", line);
    if (startupFailed && strstr(line, "experimental native startup failed")) SetEvent(startupFailed);
}
static int SetFile(FH2Mod*, const char* path, const void* data, size_t size) {
    assert(WaitForSingleObject(codeRequested, 10000) == WAIT_OBJECT_0);
    assert(data && size);
    assert(!strncmp(path, "tracks\\TopGearCPS\\", 18) ||
           !strcmp(path, "stateflow\\trackloadercps.xml") ||
           !strncmp(path, "EntityModel\\entities_trackloadercps_", 35));
    ++files;
    return 1;
}
static void ClearFile(FH2Mod*, const char* path) {
    assert(!strncmp(path, "tracks\\TopGearCPS\\", 18));
    ++clears;
}
static void Sql(FH2Mod*, const char* text) {
    assert(strstr(text, "INSERT INTO \"Environments\""));
    assert(strstr(text, "INSERT INTO \"Tracks\""));
    assert(!strstr(text, "UPDATE "));
    ++sqlCalls;
}
static int WaitMods(uint32_t) { return 0; }
static void ApplyDatabase(FH2Mod*) { assert(false); }
static const void* Interface(const char*, uint32_t) { assert(false); return nullptr; }
static void* WaitCode(uint32_t) {
    assert(codeRequested);
    SetEvent(codeRequested);
    return nullptr;
}
static void Notice(FH2Mod*, const char* text) { assert(!text); }
static int AddCommand(FH2Mod*, const char* name, const char*, FH2CommandFn callback, void*) {
    commands[name] = callback;
    return 1;
}
static std::string Call(const char* command, const char* args) {
    char reply[4096] = {};
    commands.at(command)(nullptr, command, args, reply, sizeof reply);
    printf("%s: %s\n", command, reply);
    return reply;
}
int wmain(int argc, wchar_t** argv) {
    bool experimental = argc == 4 && !wcscmp(argv[3], L"experimental");
    bool disarmed = argc == 4 && !wcscmp(argv[3], L"disarmed");
    assert(argc == 3 || experimental || disarmed);
    if (disarmed) {
        std::wstring manifest = std::wstring(argv[2]) + L"\\fh2-mods\\TrackPacks\\TopGear\\track.ini";
        assert(WritePrivateProfileStringW(L"Experimental", L"SessionOnly", L"0", manifest.c_str()));
    }
    HMODULE module = LoadLibraryW(argv[1]);
    assert(module);
    typedef int (*Init)(const FH2ModApi*);
    Init init = reinterpret_cast<Init>(GetProcAddress(module, "fh2mod_init"));
    assert(init);
    static FH2ModApi api = {};
    api.size = sizeof api;
    api.api_version = 9;
    api.azimuth_dir = argv[2];
    api.register_mod = Register;
    api.log = Log;
    api.add_command = AddCommand;
    if (experimental || disarmed) {
        startupFailed = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        assert(startupFailed);
        codeRequested = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        assert(codeRequested);
        static wchar_t mount[MAX_PATH] = {};
        assert(GetFullPathNameW(L"bin\\host_mount", MAX_PATH, mount, nullptr));
        CreateDirectoryW(mount, nullptr);
        CreateDirectoryW((std::wstring(mount) + L"\\media").c_str(), nullptr);
        CreateDirectoryW((std::wstring(mount) + L"\\media\\tracks").c_str(), nullptr);
        HANDLE stock = CreateFileW((std::wstring(mount) + L"\\media\\zipmanifest.xml").c_str(), GENERIC_WRITE, 0,
                                   nullptr, CREATE_ALWAYS, 0, nullptr);
        assert(stock != INVALID_HANDLE_VALUE);
        const char stockText[] = "<ZipFiles version=\"3\">\r\n</ZipFiles>\r\n";
        DWORD written = 0;
        assert(WriteFile(stock, stockText, sizeof stockText - 1, &written, nullptr));
        CloseHandle(stock);
        api.game_dir = experimental ? mount : L"C:\\Test\\Mount";
        api.set_file = SetFile;
        api.clear_file = ClearFile;
        api.add_sql = Sql;
        api.wait_for_mods = WaitMods;
        api.apply_database = ApplyDatabase;
        api.get_interface = Interface;
        api.wait_for_code = WaitCode;
        api.set_notice = Notice;
    }
    assert(init(&api) == 0);
    assert(commands.size() == 5);
    if (disarmed) {
        assert(files == 0 && sqlCalls == 0);
        assert(WaitForSingleObject(codeRequested, 0) == WAIT_TIMEOUT);
        assert(Call("list", "").find("One-shot test consumed") != std::string::npos);
        assert(Call("status", "").find("registered_tracks=0") != std::string::npos);
        assert(Call("travel", "10000").find("error:") == 0);
        assert(Call("return", "").find("error:") == 0);
        FreeLibrary(module);
        CloseHandle(startupFailed);
        CloseHandle(codeRequested);
        puts("Disarmed next-launch DLL: no file/SQL publication, no native activation or saving suppression.");
        return 0;
    }
    if (experimental) {
        assert(WaitForSingleObject(startupFailed, 10000) == WAIT_OBJECT_0);
        assert(WaitForSingleObject(codeRequested, 0) == WAIT_OBJECT_0);
        printf("host: files=%u clears=%u\n", files, clears);
        assert(files == 22 && sqlCalls == 1);
        assert(GetFileAttributesW((std::wstring(api.game_dir) +
            L"\\media\\tracks\\TopGearCPS").c_str()) == INVALID_FILE_ATTRIBUTES);
        assert(Call("list", "").find("10000 Top Gear (CPS) [session-only test]") != std::string::npos);
        assert(Call("pack", "10000").find("registration=pending-or-failed") != std::string::npos);
        assert(Call("status", "").find("experimental native startup failed") != std::string::npos);
        assert(Call("travel", "10000").find("error:") == 0);
        assert(Call("return", "").find("error:") == 0);
        puts("Experimental DLL host: protection code wait precedes publication; 17 track overrides (no zipmanifest.xml), 5 visit overrides and additive SQL requested; code timeout stays explicit and travel is refused.");
        return 0;
    }
    assert(Call("list", "").find("10000 Top Gear (CPS) [blocked]") != std::string::npos);
    std::string status = Call("status", "");
    assert(status.find("reserved_tracks=1") != std::string::npos);
    assert(status.find("registered_tracks=0") != std::string::npos);
    assert(status.find("in_game_validated_tracks=0") != std::string::npos);
    assert(status.find("local_dlc_packs=1") != std::string::npos);
    std::string pack = Call("pack", "10000");
    assert(pack.find("registration=disabled") != std::string::npos);
    assert(pack.find("physical_media=unavailable (game Mount not supplied by platform)") != std::string::npos);
    assert(pack.find("file_mount=disabled") != std::string::npos);
    assert(Call("travel", "10000").find("error: Top Gear (CPS) (10000) is not playable:") == 0);
    assert(Call("travel", "542").find("error: unknown reserved track ID") == 0);
    assert(Call("travel", "10000junk").find("error: unknown reserved track ID") == 0);
    assert(Call("return", "").find("error: experimental native runtime is unavailable") == 0);
    FreeLibrary(module);
    return 0;
}
