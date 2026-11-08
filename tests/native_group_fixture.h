#pragma once
#include <windows.h>
#include <cstring>
#include <cassert>

struct GroupFixture {
    struct Pair { void* object; void* control; };
    struct Control {
        void (**table)(void*);
        LONG strong = 1;
        LONG weak = 1;
    };
    static void UnexpectedRelease(void*) { assert(false); }
    static void (**Table())(void*) {
        static void (*table[])(void*) = {UnexpectedRelease, UnexpectedRelease};
        return table;
    }
    unsigned char group[0x68] = {}, head[0x40] = {}, node[0x40] = {};
    unsigned char entity[0x80] = {}, types[0x50] = {}, type[0x50] = {};
    unsigned char components[0x20] = {}, componentNode[0x20] = {};
    Control groupControl{Table()}, componentControl{Table()};
    Pair shared{group, &groupControl};
    static void Put(unsigned char* memory, size_t offset, const void* pointer) {
        memcpy(memory + offset, &pointer, sizeof pointer);
    }
    explicit GroupFixture(void* component, uintptr_t componentRva = 0x2c98cf0) {
        uintptr_t componentTable = 0;
        memcpy(&componentTable, component, sizeof componentTable);
        uintptr_t groupTable = componentTable - componentRva + 0x2f5f6b8;
        memcpy(group, &groupTable, sizeof groupTable);
        Put(group, 0x28, head);
        head[0x19] = 1;
        Put(head, 8, node);
        Put(node, 0, head); Put(node, 0x10, head); Put(node, 0x30, entity);
        Put(entity, 0x68, types);
        Put(types, 0, type); Put(types, 8, type);
        Put(type, 0, types); Put(type, 8, types);
        Put(type, 0x18, components);
        Put(components, 0, componentNode); Put(components, 8, componentNode);
        Put(componentNode, 0, components); Put(componentNode, 8, components);
        Put(componentNode, 0x10, component);
        Put(componentNode, 0x18, &componentControl);
    }
};
