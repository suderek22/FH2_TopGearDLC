#include "..\track_save.h"
#include "..\track_target.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <thread>

int main() {
    constexpr uintptr_t module = 0x12345678;
    constexpr uintptr_t profile = 0x87654321;
    TrackSave::Gate gate;
    std::string error;
    FH2Context original = {};
    original.rbp = module;
    original.rax = 123;
    original.rcx = profile;
    original.rflags = 0x202;
    original.xmm[6].u64[0] = 789;
    FH2Context context = original;
    TrackSave::Override(&context, &gate);
    TrackSave::OverrideProfile(&context, &gate);
    assert(!memcmp(&context, &original, sizeof context));
    assert(!gate.Hold(0, profile, 0, 0, error));
    assert(!gate.Hold(module, 0, 0, 0, error));
    assert(!gate.Hold(module, profile, 1, 0, error));
    assert(!gate.Hold(module, profile, 0, 1, error));
    assert(!gate.Held());
    assert(gate.Hold(module, profile, 0, 0, error));
    assert(!gate.Hold(module, profile, 0, 0, error));
    {
        TrackTarget::Request request = {542, 542, 10000, {202, 1, 31, 1}, 0};
        TrackTarget::Scope preparation(request);
        assert(preparation.Entered());
        assert(!preparation.Complete(error));
    }
    assert(gate.Held()); // A failed/aborted preparation cannot release visit protection.
    TrackSave::Override(&context, &gate);
    FH2Context expected = original;
    expected.rflags |= TrackSave::ZeroFlag;
    assert(!memcmp(&context, &expected, sizeof context));
    assert(gate.Blocked() == 1);
    context = expected;
    TrackSave::Override(&context, &gate);
    assert(!memcmp(&context, &expected, sizeof context));
    assert(gate.Blocked() == 2);

    context = original;
    TrackSave::OverrideProfile(&context, &gate);
    assert(!memcmp(&context, &expected, sizeof context));
    assert(gate.BlockedProfiles() == 1);
    TrackSave::ReturnState returned = {module, profile, 542, 542, 0, 0, 0, true, true, true};
    for (int test = 0; test < 10; ++test) {
        auto incomplete = returned;
        switch (test) {
        case 0: incomplete.module = 0; break;
        case 1: incomplete.currentTrack = 10000; break;
        case 2: incomplete.targetTrack = 10000; break;
        case 3: incomplete.pendingTrackJob = 1; break;
        case 4: incomplete.pendingSaveJob = 1; break;
        case 5: incomplete.gameplayReady = false; break;
        case 6: incomplete.originalPoseRestored = false; break;
        case 7: incomplete.careerUnchanged = false; break;
        case 8: incomplete.profile = 0; break;
        case 9: incomplete.pendingProfileJob = 1; break;
        }
        assert(!gate.Release(incomplete, error));
        assert(gate.Held());
    }
    std::thread worker([&] {
        FH2Context callback = original;
        TrackSave::Override(&callback, &gate);
        TrackSave::OverrideProfile(&callback, &gate);
        assert(!memcmp(&callback, &expected, sizeof callback));
        std::string workerError;
        assert(!gate.Release(returned, workerError));
    });
    worker.join();
    assert(gate.Held());
    assert(gate.Blocked() == 3);
    assert(gate.BlockedProfiles() == 2);
    assert(gate.Release(returned, error));
    assert(!gate.Held());
    context = original;
    TrackSave::Override(&context, &gate);
    TrackSave::OverrideProfile(&context, &gate);
    assert(!memcmp(&context, &original, sizeof context));
    assert(!gate.Release(returned, error));

    assert(gate.Hold(module, profile, 0, 0, error));
    context = original;
    context.rbp = module + 1;
    auto foreign = context;
    foreign.rflags |= TrackSave::ZeroFlag;
    TrackSave::Override(&context, &gate);
    assert(!memcmp(&context, &foreign, sizeof context));
    assert(gate.UnexpectedModule());
    assert(!gate.Release(returned, error));
    assert(gate.Held());
    TrackSave::Override(nullptr, &gate);
    TrackSave::Override(&context, nullptr);
    assert(gate.Blocked() == 1);
    TrackSave::Gate foreignProfileGate;
    assert(foreignProfileGate.Hold(module, profile, 0, 0, error));
    context = original;
    context.rcx = profile + 1;
    foreign = context;
    foreign.rflags |= TrackSave::ZeroFlag;
    TrackSave::OverrideProfile(&context, &foreignProfileGate);
    assert(!memcmp(&context, &foreign, sizeof context));
    assert(foreignProfileGate.UnexpectedModule());
    assert(!foreignProfileGate.Release(returned, error));
    TrackSave::Gate session;
    assert(session.ProtectSession(error));
    assert(session.Held() && session.SessionOnly());
    assert(!session.ProtectSession(error));
    context = original;
    context.rbp = 0;
    context.rcx = 0;
    expected = context;
    expected.rflags |= TrackSave::ZeroFlag;
    TrackSave::Override(&context, &session);
    TrackSave::OverrideProfile(&context, &session);
    assert(!memcmp(&context, &expected, sizeof context));
    assert(!session.UnexpectedModule());
    assert(!session.Release(returned, error) && session.Held());
    assert(session.Hold(module, profile, 0, 0, error));
    assert(!session.Release(returned, error) && session.Held() && session.SessionOnly());
    context = original;
    TrackSave::Override(&context, &session);
    TrackSave::OverrideProfile(&context, &session);
    expected = original;
    expected.rflags |= TrackSave::ZeroFlag;
    assert(!memcmp(&context, &expected, sizeof context));
    auto optedIn = returned;
    optedIn.careerUnchanged = false;
    auto pending = optedIn;
    pending.pendingProfileJob = 1;
    assert(!session.Release(pending, error, true) && session.Held());
    pending = optedIn;
    pending.originalPoseRestored = false;
    assert(!session.Release(pending, error, true) && session.Held());
    assert(session.Release(optedIn, error, true));
    assert(!session.Held() && !session.SessionOnly());
    context = original;
    TrackSave::Override(&context, &session);
    TrackSave::OverrideProfile(&context, &session);
    assert(!memcmp(&context, &original, sizeof context));
    assert(session.Hold(module, profile, 0, 0, error));
    assert(!session.Release(optedIn, error));
    assert(session.Release(optedIn, error, true));
    assert(!gate.ProtectSession(error));
    puts("Visit-held native microsave/profile gate tests passed; no save/career memory writes.");
}
