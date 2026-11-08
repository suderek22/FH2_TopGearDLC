#include "..\TrackLoaderCPS.cpp"
#include <cassert>

static FH2ModInfo g_info;
static FH2Mod* Register(const FH2ModInfo* info) {
    g_info = *info;
    return reinterpret_cast<FH2Mod*>(&g_info);
}
static void TestLog(FH2Mod*, const char*) {}
static int AddCommand(FH2Mod*, const char*, const char*, FH2CommandFn, void*) { return 1; }

int wmain(int argc, wchar_t** argv) {
    int id = 0;
    assert(CustomId(L"10000", &id) && id == 10000);
    assert(!CustomId(L"549", &id));
    assert(!CustomId(L"2147483648", &id));
    assert(!CustomId(L"10000tail", &id));
    assert(MediaName(L"TopGearCPS"));
    assert(!MediaName(L"..\\Anthem"));
    assert(!MediaName(L"1TopGear"));
    assert(!MediaName(L""));
    float vector[3] = {};
    assert(Vector(L"1,2,3", vector) && vector[0] == 1 && vector[2] == 3);
    assert(!Vector(L"1,2", vector));
    assert(!Vector(L"1,2,3tail", vector));
    assert(!Vector(L"nan,2,3", vector));
    assert(!Vector(L"1,inf,3", vector));
    FH2ModApi api = {};
    api.size = sizeof api;
    api.api_version = 9;
    assert(argc == 2 || argc == 3);
    api.azimuth_dir = argv[1];
    api.game_dir = L"C:\\Test\\Mount";
    api.register_mod = Register;
    api.log = TestLog;
    api.add_command = AddCommand;
    assert(fh2mod_init(&api) == 0);
    assert(g_info.offline_only == 1);
    assert(g_tracks.size() == 1);
    assert(g_tracks[0].id == 10000 && g_tracks[0].environmentId == 10000);
    assert(g_tracks[0].mediaName == "TopGearCPS");
    if (argc == 3) {
        assert(g_tracks[0].media);
        assert(g_tracks[0].media->Count() == static_cast<size_t>(_wtoi(argv[2])));
    }
    g_tracks.push_back({10001, "Legacy", "unfinished", 0, "", nullptr, nullptr, false});
    char reply[1024] = {};
    Commands(nullptr, "pack", "10000", reply, sizeof reply);
    assert(strstr(reply, "delivery=mod-managed"));
    assert(strstr(reply, "registration=disabled"));
    assert(strstr(reply, "physical_media=C:\\Test\\Mount\\mediaoverride\\tracks\\TopGearCPS"));
    assert(strstr(reply, "file_mount=disabled"));
    Commands(nullptr, "pack", "10001", reply, sizeof reply);
    assert(strstr(reply, "delivery=diagnostic"));
    Commands(nullptr, "status", "", reply, sizeof reply);
    assert(strstr(reply, "local_dlc_packs=1"));
    assert(strstr(reply, "registered_tracks=0"));
    Commands(nullptr, "travel", "10000", reply, sizeof reply);
    assert(!strncmp(reply, "error:", 6));
    Commands(nullptr, "travel", "9999999999999999999999", reply, sizeof reply);
    assert(!strncmp(reply, "error:", 6));
    puts("TrackLoaderCPS local DLC catalog tests passed; no SQL/files/hooks applied.");
}
