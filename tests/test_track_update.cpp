#include "../track_update.h"
#include <cassert>

int main(int argc, char**) {
    std::string tag, error;
    assert(TrackUpdate::LatestTag(
        "[{\"draft\":true,\"tag_name\":\"9.0.0\"},"
        "{\"draft\":false,\"prerelease\":true,\"tag_name\":\"0.6.0-beta2-track\"}]", tag, error));
    assert(tag == "0.6.0-beta2-track");
    int comparison = 0;
    assert(TrackUpdate::Compare(tag, "0.7.0-beta.4", comparison) && comparison < 0);
    assert(TrackUpdate::Compare("0.7.0-beta.5", "0.7.0-beta.4", comparison) && comparison > 0);
    assert(TrackUpdate::Compare("0.7.0", "0.7.0-beta.4", comparison) && comparison > 0);
    assert(TrackUpdate::Compare("v0.7.0-beta4", "0.7.0-beta.4", comparison) && comparison == 0);
    assert(!TrackUpdate::Compare("garbage", "0.7.0-beta.4", comparison));
    assert(!TrackUpdate::LatestTag("[]", tag, error));
    assert(!TrackUpdate::LatestTag("not json", tag, error));
    if (argc > 1) {
        assert(TrackUpdate::Fetch(tag, error));
        assert(!tag.empty());
    }
}
