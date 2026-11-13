#include "..\track_time_weather.h"
#include <cassert>
#include <cstring>
#include <vector>

template<typename T>
static void Put(void* address, const T& value) {
    memcpy(address, &value, sizeof value);
}

template<typename T>
static T Get(const void* address) {
    T value = {};
    memcpy(&value, address, sizeof value);
    return value;
}

int main() {
    TrackTimeWeather::Preset preset;
    std::string error;
    assert(TrackTimeWeather::ParsePreset(L"0", nullptr, nullptr, nullptr, nullptr, nullptr, preset, error));
    assert(!preset.enabled);
    assert(TrackTimeWeather::ParsePreset(L"1", L"13.5", L"1", L"85", L"0", L"0", preset, error));
    assert(preset.enabled && preset.hours == 13.5f && preset.freeze);
    assert(preset.atmosphere == 0.85f && preset.rain == 0 && preset.environment == 0);
    assert(!TrackTimeWeather::ParsePreset(L"1", L"24", L"1", L"85", L"0", L"0", preset, error));
    assert(!TrackTimeWeather::ParsePreset(L"1", L"13.5", L"2", L"85", L"0", L"0", preset, error));
    assert(!TrackTimeWeather::ParsePreset(L"1", L"13.5", L"1", L"101", L"0", L"0", preset, error));

    std::vector<unsigned char> image(0x34aba60 + 0x100);
    uintptr_t base = reinterpret_cast<uintptr_t>(image.data());
    std::vector<unsigned char> root(0x50), module(0xa8), context(0x500),
        clock(0x400), weather(0x110);
    const unsigned char timeGetter[] = {0x48,0x8b,0x81,0x98,0,0,0,0xc3};
    const unsigned char weatherGetter[] = {0x48,0x8b,0x81,0x80,4,0,0,0xc3};
    const unsigned char environmentGetter[] = {0x48,0x8b,0x81,0x88,4,0,0,0xc3};
    memcpy(image.data() + 0x2e1520, timeGetter, sizeof timeGetter);
    memcpy(image.data() + 0x2e1f90, weatherGetter, sizeof weatherGetter);
    memcpy(image.data() + 0x2e2e90, environmentGetter, sizeof environmentGetter);
    uintptr_t rootAddress = reinterpret_cast<uintptr_t>(root.data());
    uintptr_t moduleAddress = reinterpret_cast<uintptr_t>(module.data());
    uintptr_t contextAddress = reinterpret_cast<uintptr_t>(context.data());
    uintptr_t clockAddress = reinterpret_cast<uintptr_t>(clock.data());
    uintptr_t weatherAddress = reinterpret_cast<uintptr_t>(weather.data());
    Put(image.data() + 0x34aba60, rootAddress);
    Put(root.data() + 0x48, moduleAddress);
    Put(module.data() + 0x98, contextAddress);
    Put(context.data() + 0x480, clockAddress);
    Put(context.data() + 0x488, weatherAddress);

    const float originalSeconds = 45000.0f, originalAtmosphere = 0.2f,
                originalScalar = 0.7f, originalEnvironment = 0.3f, originalRain = 0.1f,
                originalAtmosphereOverride = 0.4f, originalEnvironmentOverride = 0.5f,
                originalRainOverride = 0.6f;
    const unsigned char originalSample = 7, originalFrozen = 1;
    const unsigned char originalFlags[3] = {1,0,1};
    Put(clock.data() + 0x10, originalSeconds);
    Put(clock.data() + 0xc, originalSeconds);
    Put(clock.data() + 0x361, originalSample);
    Put(clock.data() + 0x24, originalFrozen);
    Put(weather.data() + 8, originalAtmosphere);
    Put(weather.data() + 0x10, originalScalar);
    Put(weather.data() + 0x18, originalEnvironment);
    Put(weather.data() + 0x5c, originalRain);
    Put(weather.data() + 0xf4, originalAtmosphereOverride);
    Put(weather.data() + 0xfc, originalEnvironmentOverride);
    Put(weather.data() + 0x104, originalRainOverride);
    memcpy(weather.data() + 0xf0, originalFlags, 1);
    memcpy(weather.data() + 0xf8, originalFlags + 1, 1);
    memcpy(weather.data() + 0x100, originalFlags + 2, 1);

    TrackTimeWeather::Snapshot snapshot;
    assert(TrackTimeWeather::Capture(base, snapshot, error));
    assert(snapshot.valid && snapshot.seconds == originalSeconds && snapshot.frozen == originalFrozen);
    assert(TrackTimeWeather::Apply(base, preset, error));
    assert(Get<float>(clock.data() + 0x10) == 48600.0f);
    assert(Get<float>(clock.data() + 0xc) == 48600.0f);
    assert(Get<unsigned char>(clock.data() + 0x361) == 0);
    assert(Get<unsigned char>(clock.data() + 0x24) == 1);
    assert(Get<float>(weather.data() + 8) == 0.85f);
    assert(Get<float>(weather.data() + 0x18) == 0);
    assert(Get<float>(weather.data() + 0x5c) == 0);
    assert(Get<unsigned char>(weather.data() + 0xf0) == 1);
    assert(Get<unsigned char>(weather.data() + 0xf8) == 1);
    assert(Get<unsigned char>(weather.data() + 0x100) == 1);
    assert(TrackTimeWeather::HoldWeather(base, preset, error));
    assert(TrackTimeWeather::ReleaseForReturn(base, error));
    assert(Get<unsigned char>(clock.data() + 0x24) == 0);
    assert(Get<unsigned char>(weather.data() + 0xf0) == 0);
    assert(Get<unsigned char>(weather.data() + 0xf8) == 0);
    assert(Get<unsigned char>(weather.data() + 0x100) == 0);
    assert(TrackTimeWeather::Restore(base, snapshot, error));
    assert(Get<float>(clock.data() + 0x10) == originalSeconds);
    assert(Get<float>(clock.data() + 0xc) == originalSeconds);
    assert(Get<unsigned char>(clock.data() + 0x361) == originalSample);
    assert(Get<unsigned char>(clock.data() + 0x24) == originalFrozen);
    assert(Get<float>(weather.data() + 8) == originalAtmosphere);
    assert(Get<float>(weather.data() + 0x10) == originalScalar);
    assert(Get<float>(weather.data() + 0x18) == originalEnvironment);
    assert(Get<float>(weather.data() + 0x5c) == originalRain);
    assert(Get<float>(weather.data() + 0xf4) == originalAtmosphereOverride);
    assert(Get<float>(weather.data() + 0xfc) == originalEnvironmentOverride);
    assert(Get<float>(weather.data() + 0x104) == originalRainOverride);
    assert(Get<unsigned char>(weather.data() + 0xf0) == originalFlags[0]);
    assert(Get<unsigned char>(weather.data() + 0xf8) == originalFlags[1]);
    assert(Get<unsigned char>(weather.data() + 0x100) == originalFlags[2]);

    image[0x2e1520] ^= 1;
    TrackTimeWeather::Snapshot rejected;
    assert(!TrackTimeWeather::Capture(base, rejected, error));
    assert(error.find("unsupported game build") != std::string::npos);
}
