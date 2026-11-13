#include "..\..\FH2-Mod-Platform\include\fh2mod.h"
#include <windows.h>
#include <cassert>
#include <cstdio>
#include <map>
#include <string>

static std::map<std::string, FH2CommandFn> commands;
static int handle;
static unsigned files, sqlCalls, clears;
static std::map<std::string, std::string> published;
static bool catalogTest;
static const void* runtimeOwner;
static unsigned registrations;
static HANDLE startupFailed;
static HANDLE codeRequested;
static FH2Page browserPage = {};
static std::map<int, int> browserSlots;
static std::string browserContent;
static int AddPage(FH2Mod*, const FH2Page* page) {
    assert(!strcmp(page->key, "Tracks") && !strcmp(page->tile, "IslandFeatured"));
    assert(!strcmp(page->tab_title, "DLC") && page->size == sizeof *page);
    browserPage = *page;
    return 1;
}
static int SetXaml(FH2Mod*, const char* key, const char* resources, const char* content, const char*) {
    assert(!strcmp(key, "Tracks") && strstr(resources, "TrackLoader_Thumbnail"));
    browserContent = content;
    assert(browserContent.find("Storm Island") < browserContent.find("Top Gear"));
    assert(strstr(content, "x:Name=\"FirstItem\"") && strstr(content, "Mode=TwoWay"));
    return 1;
}
static int SetSlot(FH2Mod*, const char*, int slot, int value) { browserSlots[slot] = value; return 1; }
static int GetSlot(FH2Mod*, const char*, int slot) { return browserSlots[slot]; }
static int TileText(FH2Mod*, const char*, const char*) { return 1; }
static FH2Mod* Register(const FH2ModInfo* info) {
    assert(!strcmp(info->name, "TrackLoaderCPS") || !strcmp(info->name, "TrackLoader"));
    assert(info->offline_only == 1);
    ++registrations;
    return reinterpret_cast<FH2Mod*>(&handle);
}
static void Log(FH2Mod*, const char* line) {
    printf("%s\n", line);
    if (startupFailed && strstr(line, "experimental native startup failed")) SetEvent(startupFailed);
}
static int SetFile(FH2Mod*, const char* path, const void* data, size_t size) {
    assert(WaitForSingleObject(codeRequested, 10000) == WAIT_OBJECT_0);
    assert(data && size);
    assert(catalogTest || !strncmp(path, "tracks\\TopGearCPS\\", 18) ||
           !strcmp(path, "stateflow\\trackloadercps.xml") ||
           !strncmp(path, "EntityModel\\entities_trackloadercps_", 35));
    ++files;
    assert(published.emplace(path, std::string(static_cast<const char*>(data), size)).second);
    return 1;
}
static void ClearFile(FH2Mod*, const char* path) {
    assert(catalogTest || !strncmp(path, "tracks\\TopGearCPS\\", 18));
    published.erase(path);
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
static const void* Interface(const char* name, uint32_t version) {
    assert(!strcmp(name, "TrackLoader.RuntimeOwner") && version == 1);
    return runtimeOwner;
}
static void PublishInterface(FH2Mod*, const char* name, uint32_t version, const void* value) {
    assert(!strcmp(name, "TrackLoader.RuntimeOwner") && version == 1 && value && !runtimeOwner);
    runtimeOwner = value;
}
static void* WaitCode(uint32_t timeout) {
    assert(timeout == INFINITE);  // Launcher time must not exhaust the initialization window.
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
    setvbuf(stdout, nullptr, _IONBF, 0);
    catalogTest = argc >= 5 && !wcscmp(argv[3], L"catalog");
    bool experimental = catalogTest || (argc == 4 && !wcscmp(argv[3], L"experimental"));
    bool disarmed = argc == 4 && !wcscmp(argv[3], L"disarmed");
    assert(argc == 3 || experimental || disarmed);
    if (disarmed) {
        std::wstring manifest = std::wstring(argv[2]) + L"\\fh2-mods\\TrackPacks\\TopGear\\track.ini";
        assert(WritePrivateProfileStringW(L"Experimental", L"Enabled", L"0", manifest.c_str()));
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
    api.add_page = AddPage;
    api.set_page_xaml = SetXaml;
    api.get_page_slot = GetSlot;
    api.set_page_slot = SetSlot;
    api.set_page_tile_text = TileText;
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
        api.publish_interface = PublishInterface;
        api.wait_for_code = WaitCode;
        api.set_notice = Notice;
    }
    assert(init(&api) == 0);
    assert(commands.size() == 5);
    assert(browserPage.on_open && browserPage.on_button && browserPage.tile_title && browserPage.on_tile);
    browserPage.on_open(nullptr, "Tracks");
    assert(browserSlots[1] == 0);
    assert(browserPage.on_button(nullptr, "Tracks", FH2_BUTTON_B) == FH2_PAGE_IGNORED);
    browserPage.on_button(nullptr, "Tracks", FH2_BUTTON_A);
    assert(browserSlots[2] == 2);  // No native map in this host: fail closed rather than travel.
    browserPage.on_button(nullptr, "Tracks", FH2_BUTTON_RB);
    assert(browserSlots[0] == (catalogTest && _wtoi(argv[4]) == 0 ? 0 : 1));
    browserPage.on_button(nullptr, "Tracks", FH2_BUTTON_LB);
    assert(browserSlots[0] == 0);
    if (disarmed) {
        assert(files == 0 && sqlCalls == 0);
        assert(WaitForSingleObject(codeRequested, 0) == WAIT_TIMEOUT);
        assert(Call("list", "").find("Track activation disabled") != std::string::npos);
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
        if (catalogTest) {
            unsigned expected = static_cast<unsigned>(_wtoi(argv[4]));
            assert(files == expected * 8 && sqlCalls == expected);
            assert(published.size() == files);
            assert(Call("status", "").find("reserved_tracks=" + std::to_string(expected)) != std::string::npos);
            if (expected == 2) {
                assert(published.count("stateflow\\trackloadercps.xml"));
                assert(published.count("stateflow\\trackloadercps_10001.xml"));
                assert(Call("pack", "10000").find("lifecycle=trackloadercps arrival=1.000000,2.000000,3.000000")
                       != std::string::npos);
                assert(Call("pack", "10001").find("lifecycle=trackloadercps_10001 arrival=10.000000,20.000000,30.000000")
                       != std::string::npos);
            }
            assert(Call("travel", "10000").find("error:") == 0);
            assert(Call("travel", "10001").find("error:") == 0);
            if (argc == 6) {
                HMODULE second = LoadLibraryW(argv[5]);
                assert(second);
                auto secondInit = reinterpret_cast<Init>(GetProcAddress(second, "fh2mod_init"));
                assert(secondInit && secondInit(&api) == 0);
                assert(registrations == 2 && files == expected * 8 && sqlCalls == expected);
                assert(commands.size() == 5 && published.size() == files);
                puts("Mixed variants refused before a second file/SQL/hook publication.");
                FreeLibrary(second);
            }
            puts("Catalog host passed: isolated overrides/SQL/arrival; failed native startup refuses travel.");
            return 0;
        }
        assert(files == 22 && sqlCalls == 1);
        assert(GetFileAttributesW((std::wstring(api.game_dir) +
            L"\\media\\tracks\\TopGearCPS").c_str()) == INVALID_FILE_ATTRIBUTES);
        assert(Call("list", "").find("10000 Dunsfold Aerodrome [experimental]") != std::string::npos);
        assert(Call("pack", "10000").find("registration=pending-or-failed") != std::string::npos);
        assert(Call("status", "").find("experimental native startup failed") != std::string::npos);
        assert(Call("travel", "10000").find("error:") == 0);
        assert(Call("return", "").find("error:") == 0);
        puts("Experimental DLL host: protection code wait precedes publication; 17 track overrides (no zipmanifest.xml), 5 visit overrides and additive SQL requested; code timeout stays explicit and travel is refused.");
        return 0;
    }
    assert(Call("list", "").find("10000 Dunsfold Aerodrome [blocked]") != std::string::npos);
    std::string status = Call("status", "");
    assert(status.find("reserved_tracks=1") != std::string::npos);
    assert(status.find("registered_tracks=0") != std::string::npos);
    assert(status.find("in_game_validated_tracks=0") != std::string::npos);
    assert(status.find("local_dlc_packs=1") != std::string::npos);
    std::string pack = Call("pack", "10000");
    assert(pack.find("registration=disabled") != std::string::npos);
    assert(pack.find("physical_media=unavailable (game Mount not supplied by platform)") != std::string::npos);
    assert(pack.find("file_mount=disabled") != std::string::npos);
    assert(Call("travel", "10000").find("error: Dunsfold Aerodrome (10000) is not playable:") == 0);
    assert(Call("travel", "542").find("error: unknown reserved track ID") == 0);
    assert(Call("travel", "10000junk").find("error: unknown reserved track ID") == 0);
    assert(Call("return", "").find("error: experimental native runtime is unavailable") == 0);
    FreeLibrary(module);
    return 0;
}
