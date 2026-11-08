#include "..\track_group.h"
#include <cassert>
#include <cstdlib>
#include <cstdio>

struct Control {
    void (**table)(void*);
    LONG strong = 1;
    LONG weak = 1;
};
static unsigned objectReleases, controlReleases, allocations, frees, loads;
static int failure = 0;
static bool consumeString = true;
static const char* expected = "entities_trackloadercps_enter";
static void ObjectRelease(void*) { ++objectReleases; }
static void ControlRelease(void* object) { ++controlReleases; delete static_cast<Control*>(object); }
static void (*table[])(void*) = {ObjectRelease, ControlRelease};
static void Make(TrackGroup::Shared* shared, void* object) {
    auto control = new Control;
    control->table = table;
    shared->object = object;
    shared->control = control;
}
static void* Context(void* self) { assert(!self); return failure == 1 ? nullptr : reinterpret_cast<void*>(1); }
static TrackGroup::Shared* System(void* context, TrackGroup::Shared* output) {
    assert(context == reinterpret_cast<void*>(1));
    if (failure != 2) Make(output, reinterpret_cast<void*>(2));
    return output;
}
static TrackGroup::Shared* Manager(void* system, TrackGroup::Shared* output) {
    assert(system == reinterpret_cast<void*>(2));
    if (failure != 3) Make(output, reinterpret_cast<void*>(3));
    return output;
}
static void* Assign(TrackGroup::NativeString* string, const char* text, size_t length) {
    char* memory = static_cast<char*>(malloc(length + 1));
    assert(memory);
    ++allocations;
    memcpy(memory, text, length + 1);
    memcpy(string->bytes, &memory, sizeof memory);
    string->length = string->capacity = length;
    return string;
}
static void Free(void* memory) { assert(memory); ++frees; free(memory); }
static TrackGroup::Shared* Load(void* manager, TrackGroup::Shared* receipt,
                               TrackGroup::NativeString* string, TrackGroup::Shared* parent,
                               bool replace, bool option) {
    ++loads;
    assert(manager == reinterpret_cast<void*>(3));
    assert(replace && !option);
    assert(!parent->object && !parent->control);
    char* text = nullptr;
    memcpy(&text, string->bytes, sizeof text);
    assert(!strcmp(text, expected));
    assert(string->length == strlen(expected));
    if (consumeString) {
        Free(text);
        *string = {};
    }
    // The native load receipt can own a control block without a group object.
    if (failure != 4) Make(receipt, nullptr);
    return receipt;
}
int main() {
    TrackGroup::Functions callbacks = {Context, System, Manager, Assign, Free, Load};
    TrackGroup::Loader loader;
    std::string error;
    assert(!loader.Submit(false, error));
    assert(!loader.Initialize(0, error));
    auto missing = callbacks;
    missing.load = nullptr;
    assert(!loader.Initialize(missing, error));
    assert(loader.Initialize(callbacks, error));
    assert(!loader.Initialize(callbacks, error));
    for (failure = 1; failure <= 4; ++failure) {
        assert(!loader.Submit(false, error));
        assert(!loader.HasReceipt());
        assert(objectReleases == controlReleases);
        assert(allocations == frees);
    }
    failure = 0;
    assert(loader.Submit(false, error));
    assert(loader.HasReceipt());
    assert(allocations == frees);
    unsigned before = loads;
    assert(!loader.Submit(true, error));
    assert(loads == before);
    loader.ReleaseReceipt();
    assert(!loader.HasReceipt());
    assert(objectReleases == controlReleases);
    loader.ReleaseReceipt();
    consumeString = false;
    expected = "entities_trackloadercps_return";
    assert(loader.Submit(true, error));
    assert(allocations == frees);
    loader.ReleaseReceipt();
    assert(objectReleases == controlReleases);
    {
        TrackGroup::Shared retained;
        Make(&retained, reinterpret_cast<void*>(10));
        auto control = static_cast<Control*>(retained.control);
        InterlockedIncrement(&control->strong);
        unsigned prior = objectReleases;
        retained.Reset();
        assert(objectReleases == prior && control->strong == 1);
        retained.control = control;
        retained.Reset();
        assert(objectReleases == prior + 1);
    }
    assert(objectReleases == controlReleases);
    assert(allocations == frees);
    puts("Native group dispatch filename consumption, receipts and shared ownership tests passed.");
}
