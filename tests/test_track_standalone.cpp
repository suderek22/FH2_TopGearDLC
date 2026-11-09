#define TRACKLOADER_STANDALONE
#include "..\track_standalone.h"
#include <cassert>
#include <thread>

static TrackStandalone::Coordinator coordinator;
static bool acquire, release, wrongThread;
static unsigned ticks;
static void Tick(void* user, void* pause, int canEnter) {
    assert(user == reinterpret_cast<void*>(1) && pause == reinterpret_cast<void*>(2));
    ++ticks;
    char error[256] = {};
    if (wrongThread) {
        std::thread worker([&] {
            assert(!coordinator.Acquire(error, sizeof error));
            assert(!coordinator.Release(error, sizeof error));
        });
        worker.join();
    }
    if (acquire) {
        assert(coordinator.Acquire(error, sizeof error) == canEnter);
        if (canEnter) {
            assert(coordinator.Held());
            assert(!coordinator.Acquire(error, sizeof error));
        }
    }
    if (release) {
        assert(coordinator.Release(error, sizeof error));
        assert(!coordinator.Held());
        assert(!coordinator.Release(error, sizeof error));
    }
}
int main() {
    char error[256] = {};
    assert(!coordinator.Tick(nullptr, false));
    assert(!coordinator.Attach(nullptr, error, sizeof error));
    TrackVisit::Client client{sizeof client, reinterpret_cast<void*>(1), Tick};
    auto invalid = client;
    invalid.tick = nullptr;
    assert(!coordinator.Attach(&invalid, error, sizeof error));
    assert(coordinator.Attach(&client, error, sizeof error));
    assert(!coordinator.Attach(&client, error, sizeof error));
    assert(!coordinator.Acquire(error, sizeof error));
    assert(!coordinator.Release(error, sizeof error));
    acquire = wrongThread = true;
    assert(coordinator.Tick(reinterpret_cast<void*>(2), false));
    assert(!coordinator.Held());
    assert(coordinator.Tick(reinterpret_cast<void*>(2), true));
    assert(coordinator.Held());
    acquire = false;
    assert(!coordinator.Release(error, sizeof error));
    release = true;
    assert(coordinator.Tick(reinterpret_cast<void*>(2), false));
    assert(!coordinator.Held());
    acquire = true;
    assert(coordinator.Tick(reinterpret_cast<void*>(2), true));
    assert(!coordinator.Held());
    assert(ticks == 4);
    puts("Standalone lease validation, game-thread ownership, release and repeat-acquisition passed.");
}
