#include "..\..\CarPackStudio\native\studio_track_visit.h"
#include <cassert>
#include <cstdio>
#include <thread>

struct Client {
    StudioTrackVisit::Coordinator& service;
    int action = 0;
    unsigned ticks = 0;
    void* pause = reinterpret_cast<void*>(42);
    static void Tick(void* user, void* pause, int canEnter) {
        auto& self = *static_cast<Client*>(user);
        assert(pause == self.pause);
        ++self.ticks;
        char error[256] = {};
        if (self.action == 1) {
            assert(canEnter);
            assert(self.service.Acquire(error, sizeof error));
            assert(self.service.Held());
            assert(!self.service.Acquire(error, sizeof error));
        } else if (self.action == 2) {
            assert(!canEnter);
            assert(!self.service.Acquire(error, sizeof error));
        } else if (self.action == 3) {
            std::thread foreign([&] {
                char message[256];
                assert(!self.service.Release(message, sizeof message));
                assert(self.service.Held());
            });
            foreign.join();
            assert(self.service.Release(error, sizeof error));
            assert(!self.service.Release(error, sizeof error));
        }
        unsigned count = self.ticks;
        self.service.Tick(pause, true);
        assert(self.ticks == count);
    }
};
int main() {
    StudioTrackVisit::Coordinator service;
    Client client{service};
    char error[256] = {};
    assert(!service.Attached() && !service.Held());
    assert(!service.Acquire(error, sizeof error));
    assert(!service.Release(error, sizeof error));
    assert(!service.Attach(nullptr, error, sizeof error));
    CPSTrackVisitClient api{sizeof api, &client, Client::Tick};
    auto invalid = api;
    invalid.size = 0;
    assert(!service.Attach(&invalid, error, sizeof error));
    assert(service.Attach(&api, error, sizeof error));
    assert(!service.Attach(&api, error, sizeof error));
    client.action = 2;
    service.Tick(client.pause, false);
    assert(!service.Held());
    client.action = 1;
    service.Tick(client.pause, true);
    assert(service.Held());
    assert(!service.Release(error, sizeof error));
    using StudioTrackVisit::Coordinator;
    const char* commands[] = {"car", "track", "teleport", "event", "spawn", "drivatar", "pad", "recover", "input"};
    for (const auto* command : commands) assert(Coordinator::Mutating(command, "542"));
    assert(Coordinator::Mutating("pad", "status"));
    assert(Coordinator::Mutating("recover", "status"));
    assert(Coordinator::Mutating("car", "capture"));
    assert(Coordinator::Mutating("track", "details"));
    assert(!Coordinator::Mutating("car", "details"));
    assert(!Coordinator::Mutating("track", "status"));
    assert(!Coordinator::Mutating("spawn", "list 2"));
    assert(!Coordinator::Mutating("teleport", "capture"));
    assert(!Coordinator::Mutating("status", ""));
    assert(!Coordinator::Mutating("pipelines", "off"));
    client.action = 3;
    service.Tick(client.pause, false);
    assert(!service.Held());
    puts("CPS custom-track native tick, exclusive ownership and command-isolation tests passed.");
}
