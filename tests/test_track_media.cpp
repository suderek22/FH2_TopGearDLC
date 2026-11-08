#include "..\track_media.h"
#include <shlobj.h>
#include <shellapi.h>
#pragma comment(lib, "shell32.lib")
#include <cassert>
#include <cstdio>
#include <map>

static std::map<std::string, std::string> overrides;
static int writes = 0, failAt = 0;
static int SetFile(FH2Mod*, const char* path, const void* data, size_t size) {
    ++writes;
    if (failAt == writes) return 0;
    overrides[path] = std::string(static_cast<const char*>(data), size);
    return 1;
}
static void ClearFile(FH2Mod*, const char* path) { overrides.erase(path); }
static void Log(FH2Mod*, const char* text) { puts(text); }

int wmain(int argc, wchar_t** argv) {
    bool visit = argc == 5 && !wcscmp(argv[4], L"visit");
    assert(argc == 3 || argc == 4 || visit);
    std::string expected;
    for (const wchar_t* p = argv[2]; *p; ++p) {
        assert((*p >= L'0' && *p <= L'9') || (*p >= L'a' && *p <= L'f'));
        expected += static_cast<char>(*p);
    }
    std::string error;
    TrackMedia::Package package;
    assert(!package.Prepare(argv[1], "TopGearCPS", std::string(64, '0'), error, visit));
    assert(error == "media catalog checksum mismatch");
    assert(package.Count() == 0);
    assert(package.Prepare(argv[1], "TopGearCPS", expected, error, visit));
    assert(package.Count() >= 2);
    if (argc == 3) {
        printf("Native preflight verified and pinned %zu media files; no overrides published.\n", package.Count());
        return 0;
    }
    assert(!package.Prepare(argv[1], "TopGearCPS", expected, error, visit));
    FH2ModApi api = {};
    api.set_file = SetFile;
    api.clear_file = ClearFile;
    api.log = Log;
    int handle = 1;
    FH2Mod* owner = reinterpret_cast<FH2Mod*>(&handle);
    assert(!package.Publish(nullptr, owner, error));
    failAt = 2;
    overrides["tracks\\Anthem\\unchanged"] = "stock namespace";
    assert(!package.Publish(&api, owner, error));
    assert(overrides.size() == 1);
    assert(overrides.at("tracks\\Anthem\\unchanged") == "stock namespace");
    writes = failAt = 0;
    assert(package.Publish(&api, owner, error));
    assert(overrides.size() == package.Count() + 1);
    assert(!package.Publish(&api, owner, error));
    for (const auto& row : overrides)
        assert(row.first == "tracks\\Anthem\\unchanged" ||
               (visit ? TrackMedia::VisitPath(row.first) : row.first.find("tracks\\TopGearCPS\\") == 0));
    package.Release();
    package.Release();
    assert(overrides.size() == 1);
    assert(!TrackMedia::MediaPath("tracks\\TopGearCPS\\..\\Anthem", "TopGearCPS"));
    assert(!TrackMedia::MediaPath("tracks\\TopGearCPS\\file:stream", "TopGearCPS"));
    assert(!TrackMedia::MediaPath("tracks\\TopGearCPS\\file.", "TopGearCPS"));
    assert(!TrackMedia::MediaPath("tracks\\Anthem\\file", "TopGearCPS"));
    assert(!TrackMedia::VisitPath("stateflow\\pause.xml"));
    assert(!TrackMedia::VisitPath("EntityModel\\entities_offline_freeroam.xml"));
    assert(!TrackMedia::VisitPath("EntityModel\\..\\stock.xml"));
    TrackMedia::Package corrupt;
    assert(!corrupt.Prepare(argv[3], "TopGearCPS", expected, error, visit));
    assert(corrupt.Count() == 0);
    assert(writes == static_cast<int>(package.Count()));
    if (!visit) {
        wchar_t temp[MAX_PATH] = {};
        assert(GetModuleFileNameW(nullptr, temp, MAX_PATH));
        *(wcsrchr(temp, L'\\') + 1) = 0;
        std::wstring mount = std::wstring(temp) + L"tlcps_media_test_" + std::to_wstring(GetCurrentProcessId());
        assert(CreateDirectoryW(mount.c_str(), nullptr));
        auto exists = [](const std::wstring& path) { return GetFileAttributesW(path.c_str()) != INVALID_FILE_ATTRIBUTES; };
        auto write = [](const std::wstring& path, const std::string& text) {
            HANDLE created = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, 0, nullptr);
            assert(created != INVALID_HANDLE_VALUE);
            DWORD done = 0;
            assert(WriteFile(created, text.data(), static_cast<DWORD>(text.size()), &done, nullptr) && done == text.size());
            CloseHandle(created);
        };
        assert(SHCreateDirectoryExW(nullptr, (mount + L"\\media\\tracks").c_str(), nullptr) == ERROR_SUCCESS);
        // An owned direct copy from an earlier build is removed; foreign folders are untouched.
        std::wstring direct = mount + L"\\media\\tracks\\TopGearCPS", foreign = mount + L"\\media\\tracks\\Other";
        assert(CreateDirectoryW(direct.c_str(), nullptr) && CreateDirectoryW(foreign.c_str(), nullptr));
        write(direct + L"\\.trackloadercps", "");
        write(direct + L"\\bin.zip", "stale");
        write(foreign + L"\\bin.zip", "foreign");
        overrides.clear();
        writes = 0;
        TrackMedia::Package first;
        assert(first.Prepare(argv[1], "TopGearCPS", expected, error));
        const std::vector<std::string> archives = first.ArchivePaths();
        assert(archives.size() == 3 && archives.front() == "game:\\media\\tracks\\topgearcps\\bin.zip" &&
               archives.back() == "game:\\media\\tracks\\topgearcps\\geochunk1.minizip");
        assert(first.PublishTo(&api, owner, mount, error));
        assert(!exists(direct) && exists(foreign + L"\\bin.zip"));
        // Archives mount at runtime; a boot-time zipmanifest.xml override would always be one launch stale.
        assert(overrides.size() == first.Count() && !overrides.count("zipmanifest.xml"));
        assert(!first.PublishTo(&api, owner, mount, error));
        first.Release();
        assert(overrides.empty());
        // A failed file publication rolls the whole track back.
        TrackMedia::Package second;
        assert(second.Prepare(argv[1], "TopGearCPS", expected, error));
        writes = 0;
        failAt = static_cast<int>(second.Count());
        assert(!second.PublishTo(&api, owner, mount, error));
        assert(overrides.empty());
        failAt = 0;
        second.Release();
        printf("Published %zu track overrides for %zu runtime-mounted archives.\n", first.Count(), archives.size());
        SHFILEOPSTRUCTW remove = {};
        std::wstring from = mount + L'\0';
        remove.wFunc = FO_DELETE;
        remove.pFrom = from.c_str();
        remove.fFlags = FOF_NO_UI;
        SHFileOperationW(&remove);
    }
    puts("Native media catalog pinning, SHA256, publication, rollback and namespace tests passed.");
}
