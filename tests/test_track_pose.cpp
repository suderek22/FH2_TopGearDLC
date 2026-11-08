#include "..\track_pose.h"
#include <cassert>
#include <cstdio>
#include <limits>
#include <thread>

int main() {
    TrackPose::Pose arrival = {{202.15f, 0.8f, 31.53f, 1}, {1, 0, 0, 0}};
    assert(TrackPose::Valid(arrival));
    FH2Context original = {};
    original.rbp = 0x1234;
    original.rax = 567;
    original.rflags = 0x202;
    original.xmm[1].u64[0] = 987;
    FH2Context context = original;
    std::string error;
    TrackPose::Facing(&context, nullptr);
    TrackPose::Position(&context, nullptr);
    assert(!memcmp(&context, &original, sizeof context));
    {
        TrackPose::Scope scope(arrival);
        assert(scope.Entered());
        TrackPose::Scope nested(arrival);
        assert(!nested.Entered());
        assert(!nested.Complete(error));
        std::thread worker([&] {
            FH2Context other = original;
            TrackPose::Facing(&other, nullptr);
            TrackPose::Position(&other, nullptr);
            assert(!memcmp(&other, &original, sizeof other));
        });
        worker.join();
        assert(!scope.Complete(error));
        TrackPose::Facing(&context, nullptr);
        auto expected = original;
        memcpy(expected.xmm[0].f, arrival.facing, sizeof arrival.facing);
        assert(!memcmp(&context, &expected, sizeof context));
        assert(!scope.Complete(error));
        TrackPose::Position(&context, nullptr);
        memcpy(expected.xmm[0].f, arrival.position, sizeof arrival.position);
        assert(!memcmp(&context, &expected, sizeof context));
        assert(scope.Complete(error));
        auto duplicate = original;
        TrackPose::Position(&duplicate, nullptr);
        assert(!scope.Complete(error));
        assert(!memcmp(&duplicate, &original, sizeof duplicate));
    }
    assert(!TrackPose::Current());
    for (int test = 0; test < 5; ++test) {
        TrackPose::Scope scope(arrival);
        context = original;
        if (test == 0) {
            TrackPose::Position(&context, nullptr);
        } else if (test == 1) {
            TrackPose::Facing(nullptr, nullptr);
        } else if (test == 2) {
            context.rbp = 0;
            TrackPose::Facing(&context, nullptr);
        } else {
            TrackPose::Facing(&context, nullptr);
            context = original;
            if (test == 3) ++context.rbp;
            if (test == 3) TrackPose::Position(&context, nullptr);
            else TrackPose::Facing(&context, nullptr);
        }
        auto before = context;
        TrackPose::Position(&context, nullptr);
        assert(!memcmp(&context, &before, sizeof context));
        assert(!scope.Complete(error));
    }
    for (int i = 0; i < 4; ++i) {
        auto invalid = arrival;
        invalid.position[i] = std::numeric_limits<float>::quiet_NaN();
        assert(!TrackPose::Valid(invalid));
        invalid = arrival;
        invalid.facing[i] = std::numeric_limits<float>::infinity();
        assert(!TrackPose::Valid(invalid));
    }
    auto invalid = arrival;
    invalid.facing[0] = 0;
    assert(!TrackPose::Valid(invalid));
    invalid = arrival;
    invalid.facing[3] = 1;
    TrackPose::Scope rejected(invalid);
    assert(!rejected.Entered());
    assert(!rejected.Complete(error));
    auto returned = arrival;
    returned.position[0] = 5787;
    returned.position[3] = 0;
    returned.facing[0] = 0;
    returned.facing[2] = -1;
    {
        TrackPose::Scope scope(returned);
        context = original;
        TrackPose::Facing(&context, nullptr);
        TrackPose::Position(&context, nullptr);
        assert(scope.Complete(error));
        assert(context.xmm[0].f[0] == 5787);
        assert(!memcmp(context.xmm[0].f, returned.position, sizeof returned.position));
    }
    puts("Scoped native car position/facing restoration tests passed; no profile writes.");
}
