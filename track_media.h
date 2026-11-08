#pragma once
#include "..\CarPackStudio\native\include\fh2mod.h"
#include <windows.h>
#include <bcrypt.h>
#include <algorithm>
#include <array>
#include <cerrno>
#include <climits>
#include <cstdlib>
#include <memory>
#include <set>
#include <string>
#include <vector>
#pragma comment(lib, "bcrypt.lib")

namespace TrackMedia {
struct MappedFile {
    HANDLE file = INVALID_HANDLE_VALUE, mapping = nullptr;
    const void* data = nullptr;
    size_t size = 0;
    ~MappedFile() {
        if (data) UnmapViewOfFile(data);
        if (mapping) CloseHandle(mapping);
        if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    }
    bool Open(const std::wstring& path, std::string& error) {
        // Pin verified bytes: no write/delete sharing until publication finishes.
        file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                           FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
        LARGE_INTEGER length = {};
        if (file == INVALID_HANDLE_VALUE || !GetFileSizeEx(file, &length)) {
            error = "cannot open media file (Windows error " + std::to_string(GetLastError()) + ")";
            return false;
        }
        if (length.QuadPart <= 0 || length.QuadPart > INT_MAX) {
            error = "media file size is outside the supported range";
            return false;
        }
        size = static_cast<size_t>(length.QuadPart);
        mapping = CreateFileMappingW(file, nullptr, PAGE_READONLY, 0, 0, nullptr);
        if (mapping) data = MapViewOfFile(mapping, FILE_MAP_READ, 0, 0, 0);
        if (!data) {
            error = "cannot map media file (Windows error " + std::to_string(GetLastError()) + ")";
            return false;
        }
        return true;
    }
};

inline bool Sha256(const MappedFile& file, std::string& value, std::string& error) {
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    NTSTATUS status = BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0);
    if (status >= 0) status = BCryptCreateHash(algorithm, &hash, nullptr, 0, nullptr, 0, 0);
    const auto* bytes = static_cast<const unsigned char*>(file.data);
    for (size_t offset = 0; status >= 0 && offset < file.size;) {
        ULONG count = static_cast<ULONG>(std::min<size_t>(file.size - offset, 1024 * 1024));
        status = BCryptHashData(hash, const_cast<PUCHAR>(bytes + offset), count, 0);
        offset += count;
    }
    std::array<unsigned char, 32> digest = {};
    if (status >= 0) status = BCryptFinishHash(hash, digest.data(), static_cast<ULONG>(digest.size()), 0);
    if (hash) BCryptDestroyHash(hash);
    if (algorithm) BCryptCloseAlgorithmProvider(algorithm, 0);
    if (status < 0) {
        error = "SHA256 failed (NTSTATUS " + std::to_string(status) + ")";
        return false;
    }
    const char* hex = "0123456789abcdef";
    value.clear();
    for (unsigned char byte : digest) {
        value += hex[byte >> 4];
        value += hex[byte & 15];
    }
    return true;
}

inline bool PlainPath(const std::wstring& path, std::string& error) {
    if (path.size() < 3 || path[1] != L':' || path[2] != L'\\') {
        error = "media directory must be an absolute drive path";
        return false;
    }
    for (size_t end = 3; end <= path.size(); ++end) {
        if (end != path.size() && path[end] != L'\\') continue;
        DWORD attributes = GetFileAttributesW(path.substr(0, end).c_str());
        if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_REPARSE_POINT)) {
            error = "missing or redirected media path";
            return false;
        }
    }
    return true;
}

inline bool MediaPath(const std::string& name, const std::string& media) {
    if (name.compare(0, media.size() + 8, "tracks\\" + media + "\\") != 0 || name.size() > 240)
        return false;
    size_t start = 0;
    while (start < name.size()) {
        size_t end = name.find('\\', start);
        if (end == std::string::npos) end = name.size();
        std::string part = name.substr(start, end - start);
        if (part.empty() || part == "." || part == ".." || part.back() == '.' || part.back() == ' ')
            return false;
        for (char c : part)
            if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
                  c == '_' || c == '-' || c == '.' || c == '#' || c == ' ')) return false;
        start = end + 1;
    }
    return name.back() != '\\';
}
inline bool VisitPath(const std::string& name) {
    return name == "stateflow\\trackloadercps.xml" ||
        name == "EntityModel\\entities_trackloadercps_enter.xml" ||
        name == "EntityModel\\entities_trackloadercps_return.xml" ||
        name == "EntityModel\\entities_trackloadercps_static_route.xml" ||
        name == "EntityModel\\entities_trackloadercps_mainland.xml";
}

class Package {
    struct Entry {
        std::string path;
        std::unique_ptr<MappedFile> file;
        std::string digest;
    };
    std::vector<Entry> entries;
    size_t published = 0;
    const FH2ModApi* ownerApi = nullptr;
    FH2Mod* owner = nullptr;
public:
    size_t Count() const { return entries.size(); }
    // Lower-case game paths of the zip/minizip archives that need a native mount.
    std::vector<std::string> ArchivePaths() const {
        std::vector<std::string> paths;
        for (const auto& entry : entries) {
            std::string path = "game:\\media\\" + entry.path;
            std::transform(path.begin(), path.end(), path.begin(), [](unsigned char c) {
                return c >= 'A' && c <= 'Z' ? static_cast<char>(c + ('a' - 'A')) : static_cast<char>(c);
            });
            if ((path.size() > 4 && !path.compare(path.size() - 4, 4, ".zip")) ||
                (path.size() > 8 && !path.compare(path.size() - 8, 8, ".minizip")))
                paths.push_back(path);
        }
        return paths;
    }
    bool Prepare(const std::wstring& directory, const std::string& media,
                 const std::string& catalogHash, std::string& error, bool visit = false) {
        if (!entries.empty() || published) {
            error = "media package was already prepared";
            return false;
        }
        if (media.empty() || media.size() > 48 ||
            !((media[0] >= 'a' && media[0] <= 'z') || (media[0] >= 'A' && media[0] <= 'Z')) ||
            media.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_") != std::string::npos) {
            error = "invalid custom media namespace";
            return false;
        }
        std::wstring catalogPath = directory + L"\\media.catalog";
        if (!PlainPath(catalogPath, error)) return false;
        MappedFile catalog;
        if (!catalog.Open(catalogPath, error)) return false;
        if (catalog.size > 2 * 1024 * 1024) {
            error = "media catalog exceeds the supported size";
            return false;
        }
        std::string digest;
        if (!Sha256(catalog, digest, error)) return false;
        if (digest != catalogHash) {
            error = "media catalog checksum mismatch";
            return false;
        }
        std::string text(static_cast<const char*>(catalog.data), catalog.size);
        std::string header = std::string(visit ? "TLCPS-VISIT-1\t" : "TLCPS-MEDIA-1\t") + media + "\n";
        if (text.compare(0, header.size(), header) || text.back() != '\n') {
            error = "invalid media catalog envelope";
            return false;
        }
        std::vector<Entry> candidate;
        std::set<std::string> names;
        size_t start = header.size();
        while (start < text.size()) {
            size_t end = text.find('\n', start);
            std::string line = text.substr(start, end - start);
            start = end + 1;
            size_t tab = line.find('\t', 65);
            if (line.size() < 68 || line[64] != '\t' || tab == std::string::npos ||
                line.substr(0, 64).find_first_not_of("0123456789abcdef") != std::string::npos) {
                error = "invalid media catalog record";
                return false;
            }
            std::string sizeText = line.substr(65, tab - 65), name = line.substr(tab + 1);
            std::string key = name;
            std::transform(key.begin(), key.end(), key.begin(), [](unsigned char c) {
                return c >= 'A' && c <= 'Z' ? static_cast<char>(c + ('a' - 'A')) : static_cast<char>(c);
            });
            if (sizeText.empty() || sizeText.find_first_not_of("0123456789") != std::string::npos ||
                !(visit ? VisitPath(name) : MediaPath(name, media)) ||
                !names.insert(key).second || candidate.size() >= 4096) {
                error = "invalid or duplicate media catalog path/size";
                return false;
            }
            errno = 0;
            char* tail = nullptr;
            unsigned long long size = strtoull(sizeText.c_str(), &tail, 10);
            if (errno || *tail || !size || size > INT_MAX) {
                error = "media catalog size is outside the supported range";
                return false;
            }
            std::wstring path = directory + L"\\mediaoverride\\" + std::wstring(name.begin(), name.end());
            if (!PlainPath(path, error)) { error += ": " + name; return false; }
            auto file = std::make_unique<MappedFile>();
            if (!file->Open(path, error) || !Sha256(*file, digest, error)) {
                error += ": " + name;
                return false;
            }
            if (file->size != size || digest != line.substr(0, 64)) {
                error = "media checksum/size mismatch: " + name;
                return false;
            }
            candidate.push_back({name, std::move(file), digest});
        }
        if (candidate.empty() || (visit && candidate.size() != 5)) {
            error = visit ? "native visit catalog must contain exactly five custom resources" : "empty media catalog";
            return false;
        }
        entries = std::move(candidate);
        return true;
    }
    bool Publish(const FH2ModApi* api, FH2Mod* mod, std::string& error) {
        if (entries.empty() || published || !api || !mod || !api->set_file || !api->clear_file || !api->log) {
            error = "media publisher is unavailable or already active";
            return false;
        }
        ownerApi = api;
        owner = mod;
        for (const auto& entry : entries) {
            if (!api->set_file(mod, entry.path.c_str(), entry.file->data, entry.file->size)) {
                error = "failed publishing media override: " + entry.path;
                api->log(mod, ("error: " + error + "; rolling back this package").c_str());
                api->clear_file(mod, entry.path.c_str());
                Release();
                return false;
            }
            ++published;
        }
        api->log(mod, ("published " + std::to_string(published) + " verified files through Mount\\mediaoverride").c_str());
        return true;
    }
    // Track files are served through Extra Options' mediaoverride. The game reads
    // zipmanifest.xml once at boot (before mods load), so archives (bin.zip,
    // GeoChunk*.minizip) are mounted at runtime by TrackMount instead; a manifest
    // override would always be one launch stale. Builds that copied the track into
    // <Mount>\media\tracks\<name> left an ownership marker; such folders are removed.
    static constexpr const wchar_t* Marker = L".trackloadercps";
    static bool RemoveTree(const std::wstring& folder, size_t& removed, std::string& error) {
        WIN32_FIND_DATAW data;
        HANDLE find = FindFirstFileW((folder + L"\\*").c_str(), &data);
        if (find == INVALID_HANDLE_VALUE) return true;
        bool ok = true;
        do {
            std::wstring name = data.cFileName;
            if (name == L"." || name == L"..") continue;
            std::wstring path = folder + L"\\" + name;
            if ((data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) && !(data.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) {
                ok = RemoveTree(path, removed, error) && RemoveDirectoryW(path.c_str());
            } else if (data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
                ok = RemoveDirectoryW(path.c_str()) != 0;
            } else {
                SetFileAttributesW(path.c_str(), FILE_ATTRIBUTE_NORMAL);
                ok = DeleteFileW(path.c_str()) != 0;
                if (ok) ++removed;
            }
            if (!ok && error.empty())
                error = "cannot remove stale track media (Windows error " + std::to_string(GetLastError()) + ")";
        } while (ok && FindNextFileW(find, &data));
        FindClose(find);
        return ok;
    }
    // Validates a stock-format archive's directory (the record mirrors zipmanifest.xml syntax).
    static bool ArchiveRecord(const std::string& path, const MappedFile& file, std::string& record, std::string& error) {
        const auto* bytes = static_cast<const unsigned char*>(file.data);
        auto u16 = [&](size_t at) { return static_cast<unsigned>(bytes[at] | bytes[at + 1] << 8); };
        auto u32 = [&](size_t at) { return static_cast<unsigned long long>(u16(at)) | static_cast<unsigned long long>(u16(at + 2)) << 16; };
        unsigned long long start = 0, size = 0, count = 0;
        std::string key = path;
        std::transform(key.begin(), key.end(), key.begin(), [](unsigned char c) {
            return c >= 'A' && c <= 'Z' ? static_cast<char>(c + ('a' - 'A')) : static_cast<char>(c);
        });
        if (key.size() > 8 && key.compare(key.size() - 8, 8, ".minizip") == 0) {
            if (file.size < 32 || u32(0) != 0x505A4750 || (u32(4) != 100 && u32(4) != 101)) {
                error = "invalid MiniZip header: " + path;
                return false;
            }
            unsigned long long directory = u32(8), entries = u32(12), folders = u32(16), perChunk = u32(20), chunks = u32(24);
            if (directory < 32 || !perChunk || perChunk > 65536 || entries > 1000000 || folders > 65535 ||
                chunks != (entries + perChunk - 1) / perChunk || !chunks || u32(28) != 1) {
                error = "invalid MiniZip directory header: " + path;
                return false;
            }
            size = directory + ((4 * (folders + 1) + 7) & ~7ull) + 8 * chunks + 12 * (entries + 1);
            if (size > file.size) {
                error = "truncated MiniZip directory: " + path;
                return false;
            }
            start = 999;
            count = entries;
        } else if (key.size() > 4 && key.compare(key.size() - 4, 4, ".zip") == 0) {
            if (file.size < 22 || u32(file.size - 22) != 0x06054b50 || u16(file.size - 2) != 0 ||
                u16(file.size - 18) || u16(file.size - 16) || u16(file.size - 14) != u16(file.size - 12)) {
                error = "zip has a comment, spans disks or lacks an end record: " + path;
                return false;
            }
            count = u16(file.size - 12);
            start = u32(file.size - 6);
            if (start + u32(file.size - 10) != file.size - 22 || !count) {
                error = "zip central directory is not contiguous with its end record: " + path;
                return false;
            }
            size = file.size - start;
        } else {
            return true;
        }
        record = "  <Zip version=\"1\" path=\"game:\\media\\" + key + "\" priority=\"80\" dirstart=\"" +
            std::to_string(start) + "\" dirsize=\"" + std::to_string(size) + "\" direntries=\"" +
            std::to_string(count) + "\" />";
        return true;
    }
    bool PublishTo(const FH2ModApi* api, FH2Mod* mod, const std::wstring& mount, std::string& error) {
        if (entries.empty() || published || !api || !mod || !api->set_file || !api->clear_file || !api->log) {
            error = "media publisher is unavailable or already active";
            return false;
        }
        std::string media = entries.front().path.substr(7, entries.front().path.find('\\', 7) - 7);
        std::vector<std::string> records;
        bool archive = false;
        for (const auto& entry : entries) {
            std::string key = entry.path.substr(8 + media.size());
            std::transform(key.begin(), key.end(), key.begin(), [](unsigned char c) {
                return c >= 'A' && c <= 'Z' ? static_cast<char>(c + ('a' - 'A')) : static_cast<char>(c);
            });
            if (key.compare(0, 4, "bin\\") == 0) {
                error = "track pack ships an unpacked bin\\ folder; rebuild its media with build_media.py --native-bin-zip";
                return false;
            }
            archive = archive || key == "bin.zip";
            std::string record;
            if (!ArchiveRecord(entry.path, *entry.file, record, error)) return false;
            if (!record.empty()) records.push_back(record);
        }
        if (!archive) {
            error = "track pack media has no bin.zip";
            return false;
        }
        std::wstring name(media.begin(), media.end()), direct = mount + L"\\media\\tracks\\" + name;
        size_t stale = 0;
        DWORD attributes = GetFileAttributesW(direct.c_str());
        if (attributes != INVALID_FILE_ATTRIBUTES && !(attributes & FILE_ATTRIBUTE_REPARSE_POINT) &&
            (attributes & FILE_ATTRIBUTE_DIRECTORY) &&
            GetFileAttributesW((direct + L"\\" + Marker).c_str()) != INVALID_FILE_ATTRIBUTES) {
            if (!RemoveTree(direct, stale, error) || !RemoveDirectoryW(direct.c_str())) {
                if (error.empty()) error = "cannot remove the earlier direct copy of the track";
                return false;
            }
        }
        if (!Publish(api, mod, error)) return false;
        api->log(mod, ("published " + std::to_string(records.size()) + " " + media +
                       " archives for runtime mounting; removed " + std::to_string(stale) + " stale direct files").c_str());
        return true;
    }
    // The native session must be unloaded before releasing its overrides.
    void Release() {
        while (published) ownerApi->clear_file(owner, entries[--published].path.c_str());
    }
};
}
