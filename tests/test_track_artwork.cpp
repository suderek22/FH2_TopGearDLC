#include "../track_artwork.h"
#include <cassert>
#include <map>

static std::map<std::string, size_t> files;
static int failAt = 0, calls = 0;
static int SetFile(FH2Mod*, const char* path, const void* data, size_t size) {
    assert(data && size);
    if (++calls == failAt) return 0;
    assert(files.emplace(path, size).second);
    return 1;
}
static void Clear(FH2Mod*, const char* path) {
    assert(files.erase(path) == 1);
}
int wmain(int argc, wchar_t** argv) {
    assert(argc == 2);
    std::wstring directory = argv[1];
    TrackMedia::MappedFile posterFile, logoFile;
    std::string posterHash, logoHash, error;
    assert(posterFile.Open(directory + L"\\poster.swatchbin", error));
    assert(logoFile.Open(directory + L"\\logo.swatchbin", error));
    assert(TrackMedia::Sha256(posterFile, posterHash, error));
    assert(TrackMedia::Sha256(logoFile, logoHash, error));
    assert(TrackArtwork::Native(posterFile, true) && TrackArtwork::Native(logoFile, false));
    assert(!TrackArtwork::Native(posterFile, false) && !TrackArtwork::Native(logoFile, true));
    FH2ModApi api = {};
    api.set_file = SetFile;
    api.clear_file = Clear;
    std::string poster, logo;
    auto publish = [&] {
        return TrackArtwork::Publish(&api, nullptr, 10000, directory, posterHash, logoHash, poster, logo, error);
    };
    const auto correctHash = logoHash;
    logoHash[0] = logoHash[0] == '0' ? '1' : '0';
    assert(!publish() && files.empty() && calls == 0);
    logoHash = correctHash;
    error.clear();
    failAt = 2;
    assert(!publish() && files.empty() && error.find("rolled back") != std::string::npos);
    calls = failAt = 0;
    error.clear();
    assert(publish() && files.size() == 2);
    assert(poster == "/ForzaUI;component/Textures/Data_Bound/TrackLoader/track10000_large.png");
    assert(logo == "/ForzaUI;component/Textures/Data_Bound/TrackLoader/track10000_small.png");
    puts("Native artwork format/hash/publication/rollback checks passed.");
}
