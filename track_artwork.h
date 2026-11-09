#pragma once
#include "track_media.h"

namespace TrackArtwork {
inline bool Digest(const std::string& value) {
    return value.size() == 64 && value.find_first_not_of("0123456789abcdef") == std::string::npos;
}
inline bool Native(const TrackMedia::MappedFile& file, bool large) {
    const size_t size = large ? 983123 : 131155;
    if (file.size != size) return false;
    const auto* data = static_cast<const unsigned char*>(file.data);
    uint16_t width = 0, height = 0;
    memcpy(&width, data + 64, 2);
    memcpy(&height, data + 66, 2);
    const unsigned char format[] = {0x2e,0x01,0x00,0xb8};
    return !memcmp(data, "burG", 4) && !memcmp(data + 16, "BCXT", 4) &&
        !memcmp(data + 40, "HCXT", 4) && width == (large ? 1390 : 290) &&
        height == (large ? 516 : 179) && data[76] == 1 && !memcmp(data + 79, format, sizeof format);
}
inline bool Publish(const FH2ModApi* api, FH2Mod* mod, int id, const std::wstring& directory,
                    const std::string& posterHash, const std::string& logoHash,
                    std::string& poster, std::string& logo, std::string& error) {
    if (!api->set_file || !api->clear_file || !Digest(posterHash) || !Digest(logoHash) ||
        !TrackMedia::PlainPath(directory, error)) {
        if (error.empty()) error = "browser artwork requires both SHA256 digests and file publication services";
        return false;
    }
    TrackMedia::MappedFile posterFile, logoFile;
    std::string digest;
    if (!TrackMedia::PlainPath(directory + L"\\poster.swatchbin", error) ||
        !TrackMedia::PlainPath(directory + L"\\logo.swatchbin", error) ||
        !posterFile.Open(directory + L"\\poster.swatchbin", error) ||
        !logoFile.Open(directory + L"\\logo.swatchbin", error) ||
        !Native(posterFile, true) || !Native(logoFile, false) ||
        !TrackMedia::Sha256(posterFile, digest, error) || digest != posterHash ||
        !TrackMedia::Sha256(logoFile, digest, error) || digest != logoHash) {
        if (error.empty()) error = "browser artwork format, size or SHA256 mismatch";
        return false;
    }
    const std::string root = "UI\\textures\\data_bound\\TrackLoader\\track" + std::to_string(id);
    const std::string largePath = root + "_large.swatchbin", smallPath = root + "_small.swatchbin";
    if (!api->set_file(mod, largePath.c_str(), posterFile.data, posterFile.size)) {
        error = "could not publish browser poster";
        return false;
    }
    if (!api->set_file(mod, smallPath.c_str(), logoFile.data, logoFile.size)) {
        api->clear_file(mod, largePath.c_str());
        error = "could not publish browser logo; poster rolled back";
        return false;
    }
    const std::string uri = "/ForzaUI;component/Textures/Data_Bound/TrackLoader/track" + std::to_string(id);
    poster = uri + "_large.png";
    logo = uri + "_small.png";
    return true;
}
}
