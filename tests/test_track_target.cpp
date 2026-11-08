#include "..\track_target.h"
#include <cassert>
#include <cstdio>
#include <limits>
#include <thread>

int main() {
    FH2Context original = {};
    original.r14 = 542;
    original.rax = 123;
    original.rcx = 456;
    original.xmm[7].f[1] = 9;
    FH2Context context = original;
    std::string error;
    TrackTarget::Request request = {542, 542, 10000, {202, 1, 31, 1}, 0.5f};
    TrackTarget::Override(&context, nullptr);
    assert(!memcmp(&context, &original, sizeof context));
    {
        TrackTarget::Scope scope(request);
        assert(scope.Entered());
        std::thread unrelated([&] {
            FH2Context untouched = original;
            TrackTarget::Override(&untouched, nullptr);
            assert(!memcmp(&untouched, &original, sizeof untouched));
        });
        unrelated.join();
        TrackTarget::Override(&context, nullptr);
        assert(scope.Complete(error));
        FH2Context expected = original;
        expected.r14 = 10000;
        memcpy(expected.xmm[6].f, request.position, sizeof request.position);
        expected.xmm[7].f[0] = 0.5f;
        assert(!memcmp(&context, &expected, sizeof context));
        FH2Context duplicate = original;
        TrackTarget::Override(&duplicate, nullptr);
        assert(!scope.Complete(error));
        assert(!memcmp(&duplicate, &original, sizeof duplicate));
    }
    assert(!TrackTarget::Current());
    {
        TrackTarget::Scope scope(request);
        assert(!scope.Complete(error));
        TrackTarget::Scope nested(request);
        assert(!nested.Entered());
        assert(!nested.Complete(error));
        context = original;
        context.r14 = 549;
        TrackTarget::Override(&context, nullptr);
        assert(scope.Complete(error) && scope.ObservedTrack() == 549);
        assert(context.r14 == 10000 && context.xmm[7].f[0] == 0.5f);
    }
    request.heading = std::numeric_limits<float>::quiet_NaN();
    assert(!TrackTarget::Valid(request));
    request.heading = 0;
    request.targetTrack = 549;
    assert(!TrackTarget::Valid(request));
    TrackTarget::Request back = {10000, 542, 542, {1, 2, 3, 1}, 0};
    assert(TrackTarget::Valid(back));
    {
        TrackTarget::Scope scope(back);
        context = original;
        TrackTarget::Override(&context, nullptr);
        assert(scope.Complete(error));
        assert(context.r14 == 542);
    }
    puts("Scoped native target preparation tests passed; no manager/career fields written.");
}
