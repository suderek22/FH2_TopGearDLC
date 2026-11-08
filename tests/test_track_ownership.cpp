#include "..\track_ownership.h"
#include "native_group_fixture.h"
#include <cstdio>
#include <array>

int main() {
    constexpr uintptr_t base = 0x140000000;
    unsigned char component[0x150] = {};
    uintptr_t table = base + 0x2c98cf0;
    memcpy(component, &table, sizeof table);
    const char* flow = "trackloadercps.enter";
    GroupFixture::Put(component, 0xb0, flow);
    size_t length = strlen(flow), capacity = 95;
    memcpy(component + 0xc0, &length, sizeof length);
    memcpy(component + 0xc8, &capacity, sizeof capacity);
    std::string error;
    for (int mode = 0; mode < 10; ++mode) {
        GroupFixture group(component);
        TrackOwnership::Binding ownership;
        unsigned char duplicate[0x20] = {};
        switch (mode) {
        case 1: GroupFixture::Put(group.head, 8, group.head); break;
        case 2: GroupFixture::Put(group.node, 0, group.node); break;
        case 3: GroupFixture::Put(group.type, 0, group.type); break;
        case 4: GroupFixture::Put(group.componentNode, 0, group.componentNode); break;
        case 5:
            GroupFixture::Put(group.componentNode, 0, duplicate);
            GroupFixture::Put(duplicate, 0, group.components);
            GroupFixture::Put(duplicate, 0x10, component);
            GroupFixture::Put(duplicate, 0x18, &group.componentControl);
            break;
        case 6: group.groupControl.strong = 0; break;
        case 7: group.componentControl.strong = 0; break;
        case 8: GroupFixture::Put(group.node, 0x30, reinterpret_cast<void*>(1)); break;
        case 9: GroupFixture::Put(group.group, 0x28, nullptr); break;
        }
        bool captured = ownership.Capture(&group.shared, base, flow, error);
        assert(captured == (mode == 0));
        if (captured) {
            assert(ownership.Component() == component);
            assert(group.groupControl.strong == 2 && group.componentControl.strong == 2);
            assert(!ownership.Capture(&group.shared, base, flow, error));
            ownership.Reset();
            assert(!ownership.Component());
            ownership.Reset();
            assert(group.groupControl.strong == 1 && group.componentControl.strong == 1);
        } else {
            assert(!error.empty() && !ownership.Component());
            assert(group.groupControl.strong == (mode == 6 ? 0 : 1));
            assert(group.componentControl.strong == (mode == 7 ? 0 : 1));
        }
    }
    {
        GroupFixture group(component);
        TrackOwnership::Binding ownership;
        assert(!ownership.Capture(nullptr, base, flow, error));
        assert(!ownership.Capture(&group.shared, base, "trackloadercps.return_mainland", error));
        assert(ownership.Capture(&group.shared, base, flow, error));
        assert(group.groupControl.strong == 2 && group.componentControl.strong == 2);
    }
    for (size_t count : {size_t(4096), size_t(4097)}) {
        GroupFixture group(component);
        TrackOwnership::Binding ownership;
        unsigned char unrelated[0x150] = {};
        std::vector<std::array<unsigned char, 0x20>> nodes(count);
        GroupFixture::Put(group.components, 0, nodes.front().data());
        for (size_t i = 0; i < count; ++i) {
            auto node = nodes[i].data();
            GroupFixture::Put(node, 0, i + 1 == count ? group.components : nodes[i + 1].data());
            GroupFixture::Put(node, 0x10, i + 1 == count ? component : unrelated);
            GroupFixture::Put(node, 0x18, &group.componentControl);
        }
        assert(ownership.Capture(&group.shared, base, flow, error) == (count == 4096));
        ownership.Reset();
        assert(group.groupControl.strong == 1 && group.componentControl.strong == 1);
    }
    for (int mode = 0; mode < 4; ++mode) {
        GroupFixture group(component);
        unsigned char extraType[0x28] = {}, emptyComponents[0x20] = {};
        GroupFixture::Put(extraType, 0, group.types);
        GroupFixture::Put(extraType, 0x18, emptyComponents);
        GroupFixture::Put(emptyComponents, 0, emptyComponents);
        GroupFixture::Put(group.type, 0, extraType);
        if (mode == 1) GroupFixture::Put(extraType, 0, extraType);
        if (mode == 2) {
            GroupFixture::Put(group.components, 0, group.components);
        }
        if (mode == 3) GroupFixture::Put(group.type, 0x18, nullptr);
        TrackOwnership::Binding ownership;
        assert(ownership.Capture(&group.shared, base, flow, error) == (mode == 0));
        if (mode == 1 || mode == 3)
            assert(error.find("component-type list node") != std::string::npos);
        if (mode == 2) assert(error == "native parsed group has no matching component");
        ownership.Reset();
        assert(group.groupControl.strong == 1 && group.componentControl.strong == 1);
    }
    {
        GroupFixture group(component);
        TrackOwnership::Binding ownership;
        assert(ownership.Capture(&group.shared, base, flow, error));
        assert(ownership.Group() == group.group && ownership.Entity() == group.entity);
        ownership.Reset();
        assert(!ownership.Entity());
        GroupFixture::Put(group.group, 0x28, nullptr);
        assert(!ownership.Capture(&group.shared, base, flow, error));
        assert(error.find("Group entity-map header") != std::string::npos);
    }
    {
        GroupFixture group(component);
        uintptr_t unsupported = base + 0x5ee0a0;
        memcpy(group.group, &unsupported, sizeof unsupported);
        TrackOwnership::Binding ownership;
        assert(!ownership.Capture(&group.shared, base, flow, error));
        assert(error.find("unsupported object type") != std::string::npos);
        assert(group.groupControl.strong == 1);
    }
    puts("Native parsed group graph, unique component ownership, malformed graphs and pin/release tests passed.");
}
