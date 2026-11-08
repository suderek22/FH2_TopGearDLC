#include "..\track_jobs.h"
#include "native_group_fixture.h"
#include <array>
#include <thread>
#include <cstdio>

int main() {
    constexpr uintptr_t base = 0x140000000;
    std::string error;
    struct Operation {
        unsigned char object[0x30] = {}, stack[0x40] = {};
        GroupFixture::Control owner{GroupFixture::Table()};
        FH2Context context = {};
        Operation() {
            uintptr_t table = base + TrackJobs::OperationVtable;
            memcpy(object, &table, sizeof table);
            GroupFixture::Put(stack, 0x28, object);
            GroupFixture::Put(stack, 0x30, &owner);
            context.rsp = reinterpret_cast<uintptr_t>(stack);
        }
    };
    {
        Operation operation;
        TrackJobs::Ledger jobs;
        assert(jobs.Idle(error));
        {
            TrackJobs::Call call(jobs);
            assert(!jobs.Idle(error) && jobs.Pending() == 0);
        }
        assert(jobs.Idle(error));
        assert(jobs.Begin(operation.context, base, error));
        assert(jobs.Pending() == 1 && !jobs.Idle(error) && operation.owner.strong == 2);
        jobs.Complete(reinterpret_cast<void*>(1));
        assert(jobs.Pending() == 1);
        {
            TrackJobs::Call call(jobs);
            std::thread worker([&] { jobs.Complete(operation.object); });
            worker.join();
            assert(jobs.Pending() == 0 && !jobs.Idle(error));
        }
        assert(jobs.Idle(error) && jobs.Pending() == 0 && operation.owner.strong == 1);
        jobs.Complete(operation.object);
        assert(jobs.Idle(error));
    }
    {
        TrackJobs::Ledger jobs;
        jobs.Leave();
        assert(!jobs.Idle(error));
    }
    {
        TrackJobs::Ledger jobs;
        try {
            TrackJobs::Call call(jobs);
            throw 17;
        } catch (int value) {
            assert(value == 17);
        }
        assert(jobs.Idle(error));
    }
    {
        Operation operation;
        TrackJobs::Ledger jobs;
        assert(jobs.Begin(operation.context, base, error));
        assert(!jobs.Begin(operation.context, base, error));
        jobs.Complete(operation.object);
        assert(!jobs.Idle(error));
        assert(operation.owner.strong == 1);
    }
    for (int mode = 0; mode < 4; ++mode) {
        Operation operation;
        TrackJobs::Ledger jobs;
        if (mode == 0) operation.context.rsp = 1;
        if (mode == 1) operation.object[0] ^= 1;
        if (mode == 2) operation.owner.strong = 0;
        if (mode == 3) GroupFixture::Put(operation.stack, 0x30, nullptr);
        assert(!jobs.Begin(operation.context, base, error));
        assert(!jobs.Idle(error) && jobs.Pending() == 0 && !error.empty());
    }
    {
        std::array<Operation,65> operations;
        TrackJobs::Ledger jobs;
        for (size_t i = 0; i < 64; ++i) assert(jobs.Begin(operations[i].context, base, error));
        assert(jobs.Pending() == 64);
        assert(!jobs.Begin(operations[64].context, base, error));
        assert(operations[64].owner.strong == 1);
        for (size_t i = 0; i < 64; ++i) {
            jobs.Complete(operations[i].object);
            assert(operations[i].owner.strong == 1);
        }
        assert(jobs.Pending() == 0 && !jobs.Idle(error));
    }
    {
        TrackJobs::ProfileBoundary boundary;
        std::array<uintptr_t,65> profiles;
        profiles.fill(base + 0x2c5f870);
        assert(!boundary.Contains(reinterpret_cast<uintptr_t>(&profiles[0]), error));
        for (size_t i = 0; i < 64; ++i) {
            auto address = reinterpret_cast<uintptr_t>(&profiles[i]);
            std::thread worker([&] { boundary.Constructed(address, address, base); });
            worker.join();
            assert(boundary.Contains(address, error));
            boundary.Constructed(address, address, base);
        }
        auto extra = reinterpret_cast<uintptr_t>(&profiles[64]);
        boundary.Constructed(extra, extra, base);
        assert(!boundary.Contains(reinterpret_cast<uintptr_t>(&profiles[0]), error));
    }
    for (int mode = 0; mode < 3; ++mode) {
        TrackJobs::ProfileBoundary boundary;
        uintptr_t profile = base + 0x2c5f870;
        auto address = reinterpret_cast<uintptr_t>(&profile);
        boundary.Constructed(mode == 0 ? 1 : address,
                             mode == 1 ? address + 1 : address,
                             mode == 2 ? 0 : base);
        assert(!boundary.Contains(address, error));
        boundary.Constructed(address, address, base);
        assert(!boundary.Contains(address, error));
    }
    puts("Native persistence operation capture/completion, ownership, worker thread and exact 64-job bound passed.");
}
