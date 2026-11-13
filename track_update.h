#pragma once
#include <windows.h>
#include <winhttp.h>
#include <winsqlite\winsqlite3.h>
#include <string>
#include <vector>
#include <algorithm>
#include <cctype>
#include <climits>
#pragma comment(lib, "winhttp.lib")

namespace TrackUpdate {
struct Version {
    int core[3] = {};
    std::vector<std::string> prerelease;
};
inline bool ParseVersion(const std::string& value, Version& out) {
    Version result;
    size_t i = 0;
    if (i < value.size() && (value[i] == 'v' || value[i] == 'V')) ++i;
    for (int part = 0; part < 3; ++part) {
        if (i == value.size() || !std::isdigit(static_cast<unsigned char>(value[i]))) return false;
        int number = 0;
        while (i < value.size() && std::isdigit(static_cast<unsigned char>(value[i]))) {
            int digit = value[i++] - '0';
            if (number > (INT_MAX - digit) / 10) return false;
            number = number * 10 + digit;
        }
        result.core[part] = number;
        if (part < 2 && (i == value.size() || value[i++] != '.')) return false;
    }
    if (i < value.size()) {
        if (value[i++] != '-' || i == value.size()) return false;
        while (i < value.size()) {
            if (value[i] == '.' || value[i] == '-') { ++i; continue; }
            if (!std::isalnum(static_cast<unsigned char>(value[i]))) return false;
            const bool number = std::isdigit(static_cast<unsigned char>(value[i])) != 0;
            size_t start = i++;
            while (i < value.size() && std::isalnum(static_cast<unsigned char>(value[i])) &&
                   (std::isdigit(static_cast<unsigned char>(value[i])) != 0) == number) ++i;
            result.prerelease.push_back(value.substr(start, i - start));
        }
        if (result.prerelease.empty()) return false;
    }
    out = result;
    return true;
}
inline bool Compare(const std::string& a, const std::string& b, int& result) {
    Version left, right;
    if (!ParseVersion(a, left) || !ParseVersion(b, right)) return false;
    for (int i = 0; i < 3; ++i) {
        if (left.core[i] != right.core[i]) { result = left.core[i] < right.core[i] ? -1 : 1; return true; }
    }
    if (left.prerelease.empty() != right.prerelease.empty()) {
        result = left.prerelease.empty() ? 1 : -1;
        return true;
    }
    for (size_t i = 0; i < left.prerelease.size() && i < right.prerelease.size(); ++i) {
        auto x = left.prerelease[i], y = right.prerelease[i];
        const bool xn = std::isdigit(static_cast<unsigned char>(x[0])) != 0;
        const bool yn = std::isdigit(static_cast<unsigned char>(y[0])) != 0;
        if (xn && yn) {
            x.erase(0, (std::min)(x.find_first_not_of('0'), x.size() - 1));
            y.erase(0, (std::min)(y.find_first_not_of('0'), y.size() - 1));
            if (x.size() != y.size()) { result = x.size() < y.size() ? -1 : 1; return true; }
        } else if (xn != yn) { result = xn ? -1 : 1; return true; }
        if (x != y) { result = x < y ? -1 : 1; return true; }
    }
    result = left.prerelease.size() == right.prerelease.size() ? 0 :
        left.prerelease.size() < right.prerelease.size() ? -1 : 1;
    return true;
}
inline bool LatestTag(const std::string& json, std::string& tag, std::string& error) {
    sqlite3* db = nullptr;
    sqlite3_stmt* statement = nullptr;
    if (sqlite3_open(":memory:", &db) != SQLITE_OK) {
        error = "cannot open release parser";
        if (db) sqlite3_close(db);
        return false;
    }
    const char* query =
        "SELECT json_extract(value,'$.tag_name') FROM json_each(?1) "
        "WHERE json_type(?1)='array' AND "
        "json_extract(value,'$.draft')=0 AND json_type(value,'$.tag_name')='text' LIMIT 1";
    bool ok = sqlite3_prepare_v2(db, query, -1, &statement, nullptr) == SQLITE_OK &&
        sqlite3_bind_text(statement, 1, json.c_str(), static_cast<int>(json.size()), SQLITE_TRANSIENT) == SQLITE_OK &&
        sqlite3_step(statement) == SQLITE_ROW;
    if (ok) {
        const auto* value = sqlite3_column_text(statement, 0);
        const int length = sqlite3_column_bytes(statement, 0);
        if (value && length > 0 && length <= 80)
            tag.assign(reinterpret_cast<const char*>(value), length);
        else ok = false;
    }
    if (!ok) error = std::string("invalid or empty GitHub releases response: ") + sqlite3_errmsg(db);
    if (statement) sqlite3_finalize(statement);
    sqlite3_close(db);
    return ok;
}

inline bool Fetch(std::string& tag, std::string& error) {
    HINTERNET session = WinHttpOpen(L"TrackLoader/0.7.0-beta.4", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                                    WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!session) { error = "WinHTTP session failed: " + std::to_string(GetLastError()); return false; }
    WinHttpSetTimeouts(session, 5000, 5000, 8000, 8000);
    HINTERNET connection = WinHttpConnect(session, L"api.github.com", INTERNET_DEFAULT_HTTPS_PORT, 0);
    HINTERNET request = connection ? WinHttpOpenRequest(connection, L"GET",
        L"/repos/suderek22/FH2_TopGearDLC/releases?per_page=10", nullptr, WINHTTP_NO_REFERER,
        WINHTTP_DEFAULT_ACCEPT_TYPES, WINHTTP_FLAG_SECURE) : nullptr;
    DWORD status = 0, length = sizeof status;
    bool ok = request && WinHttpSendRequest(request, L"Accept: application/vnd.github+json\r\n",
        static_cast<DWORD>(-1), WINHTTP_NO_REQUEST_DATA, 0, 0, 0) &&
        WinHttpReceiveResponse(request, nullptr) &&
        WinHttpQueryHeaders(request, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                            WINHTTP_HEADER_NAME_BY_INDEX, &status, &length, WINHTTP_NO_HEADER_INDEX);
    std::string body;
    if (!ok) error = "GitHub request failed: " + std::to_string(GetLastError());
    else if (status != 200) { error = "GitHub returned HTTP " + std::to_string(status); ok = false; }
    while (ok) {
        char buffer[8192];
        DWORD received = 0;
        if (!WinHttpReadData(request, buffer, sizeof buffer, &received)) {
            error = "GitHub response read failed: " + std::to_string(GetLastError());
            ok = false;
            break;
        }
        if (!received) break;
        if (body.size() + received > 1024 * 1024) {
            error = "GitHub releases response exceeds 1 MiB";
            ok = false;
            break;
        }
        body.append(buffer, received);
    }
    if (request) WinHttpCloseHandle(request);
    if (connection) WinHttpCloseHandle(connection);
    WinHttpCloseHandle(session);
    return ok && LatestTag(body, tag, error);
}
}
