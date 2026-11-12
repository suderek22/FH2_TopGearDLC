#pragma once
#include "track_ownership.h"
#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <cwchar>
#include <string>

namespace TrackTimeWeather {
struct Preset {
    bool enabled = false;
    float hours = 0;
    bool freeze = false;
    float atmosphere = 0;
    float rain = 0;
    float environment = 0;
    bool Valid() const {
        return !enabled || (std::isfinite(hours) && hours >= 0 && hours < 24 &&
            std::isfinite(atmosphere) && atmosphere >= 0 && atmosphere <= 1 &&
            std::isfinite(rain) && rain >= 0 && rain <= 1 &&
            std::isfinite(environment) && environment >= 0 && environment <= 1);
    }
};

struct Snapshot {
    float seconds = 0;
    float interpolationSeconds = 0;
    unsigned char timeSample = 0;
    unsigned char frozen = 0;
    float atmosphere = 0;
    float weatherScalar = 0;
    float environment = 0;
    float rain = 0;
    float atmosphereOverride = 0;
    float environmentOverride = 0;
    float rainOverride = 0;
    unsigned char overrideFlags[3] = {};
    bool valid = false;
};

struct World {
    uintptr_t clock = 0;
    uintptr_t weather = 0;
};

inline bool ParseFloat(const wchar_t* text, float& output) {
    if (!text || !*text) return false;
    wchar_t* end = nullptr;
    errno = 0;
    float value = wcstof(text, &end);
    if (errno == ERANGE || end == text || *end || !std::isfinite(value)) return false;
    output = value;
    return true;
}

inline bool ParsePercent(const wchar_t* text, float& output) {
    if (!text || !*text) return false;
    wchar_t* end = nullptr;
    errno = 0;
    long value = wcstol(text, &end, 10);
    if (errno == ERANGE || end == text || *end || value < 0 || value > 100) return false;
    output = static_cast<float>(value) / 100.0f;
    return true;
}

inline bool ParsePreset(const wchar_t* enabled, const wchar_t* hours, const wchar_t* freeze,
                        const wchar_t* atmosphere, const wchar_t* rain, const wchar_t* environment,
                        Preset& output, std::string& error) {
    if (!enabled || !wcscmp(enabled, L"0")) {
        output = {};
        return true;
    }
    if (wcscmp(enabled, L"1")) {
        error = "TimeWeather.Enabled must be 0 or 1";
        return false;
    }
    Preset preset;
    preset.enabled = true;
    if (!ParseFloat(hours, preset.hours) || !hours || preset.hours >= 24 || preset.hours < 0) {
        error = "TimeWeather.TimeHours must be in [0, 24)";
        return false;
    }
    if (!freeze || (wcscmp(freeze, L"0") && wcscmp(freeze, L"1"))) {
        error = "TimeWeather.Freeze must be 0 or 1";
        return false;
    }
    preset.freeze = !wcscmp(freeze, L"1");
    if (!ParsePercent(atmosphere, preset.atmosphere) ||
        !ParsePercent(rain, preset.rain) ||
        !ParsePercent(environment, preset.environment)) {
        error = "TimeWeather atmosphere, rain and environment must be integers from 0 to 100";
        return false;
    }
    if (!preset.Valid()) {
        error = "TimeWeather preset is outside supported ranges";
        return false;
    }
    output = preset;
    return true;
}

inline bool ReadPointer(uintptr_t address, uintptr_t& output) {
    uintptr_t value = 0;
    if (!TrackOwnership::Read(address, &value, sizeof value) ||
        value < 0x10000 || value > 0x7fffffffffffULL) return false;
    output = value;
    return true;
}

inline bool Supported(uintptr_t base, std::string& error) {
    static const unsigned char timeGetter[] = {0x48,0x8b,0x81,0x98,0,0,0,0xc3};
    static const unsigned char weatherGetter[] = {0x48,0x8b,0x81,0x80,4,0,0,0xc3};
    static const unsigned char environmentGetter[] = {0x48,0x8b,0x81,0x88,4,0,0,0xc3};
    unsigned char actual[sizeof timeGetter] = {};
    if (!base ||
        !TrackOwnership::Read(base + 0x2e1520, actual, sizeof timeGetter) ||
        memcmp(actual, timeGetter, sizeof timeGetter) ||
        !TrackOwnership::Read(base + 0x2e1f90, actual, sizeof weatherGetter) ||
        memcmp(actual, weatherGetter, sizeof weatherGetter) ||
        !TrackOwnership::Read(base + 0x2e2e90, actual, sizeof environmentGetter) ||
        memcmp(actual, environmentGetter, sizeof environmentGetter)) {
        error = "unsupported game build for time/weather preset";
        return false;
    }
    return true;
}

inline bool Resolve(uintptr_t base, World& output, std::string& error) {
    if (!Supported(base, error)) return false;
    uintptr_t root = 0, module = 0, context = 0;
    World world;
    float seconds = 0, atmosphere = 0, environment = 0, rain = 0;
    unsigned char frozen = 0;
    if (!ReadPointer(base + 0x34aba60, root) ||
        !ReadPointer(root + 0x48, module) ||
        !ReadPointer(module + 0x98, context) ||
        !ReadPointer(context + 0x480, world.clock) ||
        !ReadPointer(context + 0x488, world.weather) ||
        !TrackOwnership::Read(world.clock + 0x10, &seconds, sizeof seconds) ||
        !TrackOwnership::Read(world.clock + 0x24, &frozen, sizeof frozen) ||
        !TrackOwnership::Read(world.weather + 8, &atmosphere, sizeof atmosphere) ||
        !TrackOwnership::Read(world.weather + 0x18, &environment, sizeof environment) ||
        !TrackOwnership::Read(world.weather + 0x5c, &rain, sizeof rain) ||
        !std::isfinite(seconds) || seconds < -1 || seconds > 86401 || frozen > 1 ||
        !std::isfinite(atmosphere) || atmosphere < -0.01f || atmosphere > 1.01f ||
        !std::isfinite(environment) || environment < -0.01f || environment > 1.01f ||
        !std::isfinite(rain) || rain < -0.01f || rain > 1.01f) {
        error = "time/weather world is unavailable or has an unexpected layout";
        return false;
    }
    uintptr_t rootAgain = 0, moduleAgain = 0, contextAgain = 0, clockAgain = 0, weatherAgain = 0;
    if (!ReadPointer(base + 0x34aba60, rootAgain) ||
        !ReadPointer(rootAgain + 0x48, moduleAgain) ||
        !ReadPointer(moduleAgain + 0x98, contextAgain) ||
        !ReadPointer(contextAgain + 0x480, clockAgain) ||
        !ReadPointer(contextAgain + 0x488, weatherAgain) ||
        rootAgain != root || moduleAgain != module || contextAgain != context ||
        clockAgain != world.clock || weatherAgain != world.weather) {
        error = "time/weather world changed during resolution";
        return false;
    }
    output = world;
    return true;
}

inline bool Write(uintptr_t address, const void* data, size_t size) {
    SIZE_T written = 0;
    return address && WriteProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(address),
                                         data, size, &written) && written == size;
}

inline bool Capture(uintptr_t base, Snapshot& output, std::string& error) {
    World world;
    Snapshot snapshot;
    if (!Resolve(base, world, error) ||
        !TrackOwnership::Read(world.clock + 0x10, &snapshot.seconds, sizeof snapshot.seconds) ||
        !TrackOwnership::Read(world.clock + 0xc, &snapshot.interpolationSeconds,
                              sizeof snapshot.interpolationSeconds) ||
        !TrackOwnership::Read(world.clock + 0x361, &snapshot.timeSample, sizeof snapshot.timeSample) ||
        !TrackOwnership::Read(world.clock + 0x24, &snapshot.frozen, sizeof snapshot.frozen) ||
        !TrackOwnership::Read(world.weather + 8, &snapshot.atmosphere, sizeof snapshot.atmosphere) ||
        !TrackOwnership::Read(world.weather + 0x10, &snapshot.weatherScalar, sizeof snapshot.weatherScalar) ||
        !TrackOwnership::Read(world.weather + 0x18, &snapshot.environment, sizeof snapshot.environment) ||
        !TrackOwnership::Read(world.weather + 0x5c, &snapshot.rain, sizeof snapshot.rain) ||
        !TrackOwnership::Read(world.weather + 0xf4, &snapshot.atmosphereOverride,
                              sizeof snapshot.atmosphereOverride) ||
        !TrackOwnership::Read(world.weather + 0xfc, &snapshot.environmentOverride,
                              sizeof snapshot.environmentOverride) ||
        !TrackOwnership::Read(world.weather + 0x104, &snapshot.rainOverride,
                              sizeof snapshot.rainOverride) ||
        !TrackOwnership::Read(world.weather + 0xf0, &snapshot.overrideFlags[0], 1) ||
        !TrackOwnership::Read(world.weather + 0xf8, &snapshot.overrideFlags[1], 1) ||
        !TrackOwnership::Read(world.weather + 0x100, &snapshot.overrideFlags[2], 1)) {
        if (error.empty()) error = "could not capture the original time/weather state";
        return false;
    }
    if (!std::isfinite(snapshot.interpolationSeconds) ||
        snapshot.interpolationSeconds < -1 || snapshot.interpolationSeconds > 86401 ||
        !std::isfinite(snapshot.weatherScalar) ||
        snapshot.frozen > 1 || snapshot.overrideFlags[0] > 1 ||
        snapshot.overrideFlags[1] > 1 || snapshot.overrideFlags[2] > 1 ||
        (snapshot.overrideFlags[0] && (!std::isfinite(snapshot.atmosphereOverride) ||
                                      snapshot.atmosphereOverride < -0.01f ||
                                      snapshot.atmosphereOverride > 1.01f)) ||
        (snapshot.overrideFlags[1] && (!std::isfinite(snapshot.environmentOverride) ||
                                      snapshot.environmentOverride < -0.01f ||
                                      snapshot.environmentOverride > 1.01f)) ||
        (snapshot.overrideFlags[2] && (!std::isfinite(snapshot.rainOverride) ||
                                      snapshot.rainOverride < -0.01f ||
                                      snapshot.rainOverride > 1.01f))) {
        error = "original time/weather state is outside supported ranges";
        return false;
    }
    snapshot.valid = true;
    output = snapshot;
    return true;
}

inline bool WriteWeather(const World& world, float atmosphere, float rain, float environment,
                         std::string& error) {
    const unsigned char enabled = 1;
    const float zero = 0;
    bool ok = true;
    if (!Write(world.weather + 0xf4, &atmosphere, sizeof atmosphere)) ok = false;
    if (!Write(world.weather + 0xfc, &environment, sizeof environment)) ok = false;
    if (!Write(world.weather + 0x104, &rain, sizeof rain)) ok = false;
    if (!Write(world.weather + 8, &atmosphere, sizeof atmosphere)) ok = false;
    if (!Write(world.weather + 0x10, &zero, sizeof zero)) ok = false;
    if (!Write(world.weather + 0x18, &environment, sizeof environment)) ok = false;
    if (!Write(world.weather + 0x5c, &rain, sizeof rain)) ok = false;
    if (!Write(world.weather + 0xf0, &enabled, sizeof enabled)) ok = false;
    if (!Write(world.weather + 0xf8, &enabled, sizeof enabled)) ok = false;
    if (!Write(world.weather + 0x100, &enabled, sizeof enabled)) ok = false;
    if (!ok) error = "one or more weather writes failed";
    return ok;
}

inline bool Apply(uintptr_t base, const Preset& preset, std::string& error) {
    World world;
    if (!preset.enabled || !preset.Valid() || !Resolve(base, world, error)) {
        if (error.empty()) error = "invalid or disabled time/weather preset";
        return false;
    }
    const float seconds = preset.hours * 3600.0f;
    const unsigned char sample = 0;
    const unsigned char frozen = preset.freeze ? 1 : 0;
    bool ok = true;
    if (!Write(world.clock + 0x10, &seconds, sizeof seconds)) ok = false;
    if (!Write(world.clock + 0xc, &seconds, sizeof seconds)) ok = false;
    if (!Write(world.clock + 0x361, &sample, sizeof sample)) ok = false;
    if (!WriteWeather(world, preset.atmosphere, preset.rain, preset.environment, error)) ok = false;
    if (!Write(world.clock + 0x24, &frozen, sizeof frozen)) ok = false;
    if (!ok && error.empty()) error = "one or more time/weather preset writes failed";
    return ok;
}

inline bool HoldWeather(uintptr_t base, const Preset& preset, std::string& error) {
    World world;
    return preset.enabled && preset.Valid() && Resolve(base, world, error) &&
        WriteWeather(world, preset.atmosphere, preset.rain, preset.environment, error);
}

inline bool ReleaseForReturn(uintptr_t base, std::string& error) {
    World world;
    if (!Resolve(base, world, error)) return false;
    const unsigned char disabled = 0;
    bool ok = true;
    if (!Write(world.clock + 0x24, &disabled, sizeof disabled)) ok = false;
    if (!Write(world.weather + 0xf0, &disabled, sizeof disabled)) ok = false;
    if (!Write(world.weather + 0xf8, &disabled, sizeof disabled)) ok = false;
    if (!Write(world.weather + 0x100, &disabled, sizeof disabled)) ok = false;
    if (!ok) error = "could not release time/weather overrides for mainland return";
    return ok;
}

inline bool Restore(uintptr_t base, const Snapshot& snapshot, std::string& error) {
    World world;
    if (!snapshot.valid || !Resolve(base, world, error)) {
        if (error.empty()) error = "original time/weather snapshot is unavailable";
        return false;
    }
    bool ok = true;
    if (!Write(world.clock + 0x10, &snapshot.seconds, sizeof snapshot.seconds)) ok = false;
    if (!Write(world.clock + 0xc, &snapshot.interpolationSeconds, sizeof snapshot.interpolationSeconds)) ok = false;
    if (!Write(world.clock + 0x361, &snapshot.timeSample, sizeof snapshot.timeSample)) ok = false;
    if (!Write(world.weather + 0xf4, &snapshot.atmosphereOverride, sizeof snapshot.atmosphereOverride)) ok = false;
    if (!Write(world.weather + 0xfc, &snapshot.environmentOverride, sizeof snapshot.environmentOverride)) ok = false;
    if (!Write(world.weather + 0x104, &snapshot.rainOverride, sizeof snapshot.rainOverride)) ok = false;
    if (!Write(world.weather + 8, &snapshot.atmosphere, sizeof snapshot.atmosphere)) ok = false;
    if (!Write(world.weather + 0x10, &snapshot.weatherScalar, sizeof snapshot.weatherScalar)) ok = false;
    if (!Write(world.weather + 0x18, &snapshot.environment, sizeof snapshot.environment)) ok = false;
    if (!Write(world.weather + 0x5c, &snapshot.rain, sizeof snapshot.rain)) ok = false;
    if (!Write(world.weather + 0xf0, &snapshot.overrideFlags[0], 1)) ok = false;
    if (!Write(world.weather + 0xf8, &snapshot.overrideFlags[1], 1)) ok = false;
    if (!Write(world.weather + 0x100, &snapshot.overrideFlags[2], 1)) ok = false;
    if (!Write(world.clock + 0x24, &snapshot.frozen, sizeof snapshot.frozen)) ok = false;
    if (!ok) error = "one or more original time/weather values could not be restored";
    return ok;
}
}
