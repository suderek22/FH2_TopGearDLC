#pragma once
#include <windows.h>
#include <cwchar>
#include <string>
#include <utility>
#include <vector>

// Mod-owned per-user save, kept apart from the player's native save:
//   <azimuth>\fh2-saves\User_<id>\TrackLoader.dat   (UTF-8 "Key=Value" lines)
// The user is resolved the way fh2-achievements does: the hex User_<id> save folder whose
// revisions folder was written most recently.
namespace TrackModSave {
constexpr wchar_t FileName[] = L"TrackLoader.dat";

inline std::string Narrow(const std::wstring& text) {
    if (text.empty()) return {};
    int size = WideCharToMultiByte(CP_UTF8, 0, text.c_str(), static_cast<int>(text.size()), nullptr, 0, nullptr, nullptr);
    std::string result(size > 0 ? size : 0, '\0');
    if (size > 0)
        WideCharToMultiByte(CP_UTF8, 0, text.c_str(), static_cast<int>(text.size()), &result[0], size, nullptr, nullptr);
    return result;
}

inline bool FindUser(const std::wstring& azimuth, std::wstring& user, std::string& why) {
    const std::wstring base = azimuth + L"\\data\\titles\\1A96545B\\saves\\player-1";
    WIN32_FIND_DATAW entry;
    HANDLE search = FindFirstFileW((base + L"\\User_*").c_str(), &entry);
    if (search == INVALID_HANDLE_VALUE) {
        why = "no native save found in " + Narrow(base);
        return false;
    }
    FILETIME best = {};
    bool found = false;
    do {
        if (!(entry.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) continue;
        const wchar_t* id = entry.cFileName + 5;
        if (!*id || wcsspn(id, L"0123456789ABCDEFabcdef") != wcslen(id)) continue;
        WIN32_FILE_ATTRIBUTE_DATA attributes;
        if (!GetFileAttributesExW((base + L"\\" + entry.cFileName + L"\\revisions").c_str(),
                                  GetFileExInfoStandard, &attributes)) continue;
        if (!found || CompareFileTime(&attributes.ftLastWriteTime, &best) > 0) {
            best = attributes.ftLastWriteTime;
            user = entry.cFileName;
            found = true;
        }
    } while (FindNextFileW(search, &entry));
    FindClose(search);
    if (!found) why = "no User_ folder with revisions in " + Narrow(base);
    return found;
}

class Store {
    std::wstring path;
    std::string user;
    std::vector<std::pair<std::string, std::string>> values;
    bool Load() {
        values.clear();
        HANDLE file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                                  FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file == INVALID_HANDLE_VALUE) return GetLastError() == ERROR_FILE_NOT_FOUND;
        LARGE_INTEGER size = {};
        std::string text;
        bool ok = GetFileSizeEx(file, &size) && size.QuadPart < 1024 * 1024;
        if (ok) {
            text.resize(static_cast<size_t>(size.QuadPart));
            DWORD read = 0;
            ok = text.empty() || (ReadFile(file, &text[0], static_cast<DWORD>(text.size()), &read, nullptr) &&
                                  read == text.size());
        }
        CloseHandle(file);
        if (!ok) return false;
        size_t start = 0;
        while (start < text.size()) {
            size_t end = text.find('\n', start);
            if (end == std::string::npos) end = text.size();
            std::string line = text.substr(start, end - start);
            if (!line.empty() && line.back() == '\r') line.pop_back();
            size_t equals = line.find('=');
            if (equals != std::string::npos && equals) values.emplace_back(line.substr(0, equals), line.substr(equals + 1));
            start = end + 1;
        }
        return true;
    }
    bool Write(std::string& error) {
        std::string text;
        for (const auto& value : values) text += value.first + "=" + value.second + "\r\n";
        const std::wstring temporary = path + L".tmp";
        HANDLE file = CreateFileW(temporary.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS,
                                  FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file == INVALID_HANDLE_VALUE) {
            error = "cannot create " + Narrow(temporary) + " (" + std::to_string(GetLastError()) + ")";
            return false;
        }
        DWORD written = 0;
        bool ok = WriteFile(file, text.data(), static_cast<DWORD>(text.size()), &written, nullptr) &&
                  written == text.size() && FlushFileBuffers(file);
        CloseHandle(file);
        if (!ok || !MoveFileExW(temporary.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
            error = "cannot write " + Narrow(path) + " (" + std::to_string(GetLastError()) + ")";
            DeleteFileW(temporary.c_str());
            return false;
        }
        return true;
    }
public:
    bool Open(const std::wstring& azimuth, std::string& error) {
        std::wstring id;
        if (!FindUser(azimuth, id, error)) return false;
        const std::wstring root = azimuth + L"\\fh2-saves", folder = root + L"\\" + id;
        if ((!CreateDirectoryW(root.c_str(), nullptr) && GetLastError() != ERROR_ALREADY_EXISTS) ||
            (!CreateDirectoryW(folder.c_str(), nullptr) && GetLastError() != ERROR_ALREADY_EXISTS)) {
            error = "cannot create " + Narrow(folder) + " (" + std::to_string(GetLastError()) + ")";
            return false;
        }
        path = folder + L"\\" + FileName;
        user = Narrow(id);
        if (!Load()) {
            error = "cannot read " + Narrow(path);
            path.clear();
            return false;
        }
        return true;
    }
    bool Ready() const { return !path.empty(); }
    const std::string& User() const { return user; }
    std::string Path() const { return Narrow(path); }
    std::string Get(const std::string& key, const std::string& fallback = {}) const {
        for (const auto& value : values) if (value.first == key) return value.second;
        return fallback;
    }
    bool Has(const std::string& key) const {
        for (const auto& value : values) if (value.first == key) return true;
        return false;
    }
    bool Set(const std::string& key, const std::string& value, std::string& error) {
        if (!Ready()) {
            error = "mod save is not open";
            return false;
        }
        for (auto& entry : values) {
            if (entry.first != key) continue;
            if (entry.second == value) return true;
            const std::string previous = entry.second;
            entry.second = value;
            if (Write(error)) return true;
            entry.second = previous;
            return false;
        }
        values.emplace_back(key, value);
        if (Write(error)) return true;
        values.pop_back();
        return false;
    }
};
}
