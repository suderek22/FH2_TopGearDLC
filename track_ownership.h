#pragma once
#include "track_shared.h"
#include <cstdint>
#include <cstring>
#include <string>
#include <unordered_set>
#include <vector>

namespace TrackOwnership {
constexpr uintptr_t EntityTree = 0x28;
constexpr uintptr_t GroupVtable = 0x2f5f6b8;
inline bool Read(uintptr_t address, void* output, size_t size) {
    SIZE_T count = 0;
    return address && ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(address),
                                       output, size, &count) && count == size;
}
inline bool Pointer(uintptr_t address, uintptr_t& value) {
    return Read(address, &value, sizeof value) && value;
}
inline bool Flow(void* component, uintptr_t base, const char* expected) {
    uintptr_t table = 0, text = reinterpret_cast<uintptr_t>(component) + 0xb0;
    uint64_t length = 0, capacity = 0;
    if (!component || !Read(reinterpret_cast<uintptr_t>(component), &table, sizeof table) ||
        table != base + 0x2c98cf0 || !Read(text + 0x10, &length, sizeof length) ||
        !Read(text + 0x18, &capacity, sizeof capacity) ||
        length != strlen(expected) || capacity < length || capacity > 4096) return false;
    if (capacity >= 16 && !Read(text, &text, sizeof text)) return false;
    char buffer[96] = {};
    return length < sizeof buffer && Read(text, buffer, static_cast<size_t>(length + 1)) &&
           !strcmp(buffer, expected);
}

class Binding {
    TrackNative::Shared group;
    TrackNative::Shared machine;
    uintptr_t entityOwner = 0;
    static bool SharedValue(uintptr_t address, uintptr_t& value, uintptr_t& control) {
        LONG count = 0;
        uintptr_t table = 0;
        return Pointer(address, value) && Pointer(address + 8, control) &&
            Pointer(control, table) && Read(control + 8, &count, sizeof count) && count > 0;
    }
    bool CaptureMatching(const void* shared, uintptr_t base, uintptr_t vtable,
                         const char* flow, std::string& error) {
        if (group.control || machine.control) {
            error = "native group ownership has already been captured";
            return false;
        }
        uintptr_t object = 0, owner = 0, head = 0, root = 0;
        if (!SharedValue(reinterpret_cast<uintptr_t>(shared), object, owner)) {
            error = "native group shared ownership is unavailable";
            return false;
        }
        uintptr_t groupTable = 0;
        if (!Read(object, &groupTable, sizeof groupTable) || groupTable != base + GroupVtable) {
            error = "native group lookup returned an unsupported object type (vtable " +
                std::to_string(groupTable) + "); refusing entity-map traversal";
            return false;
        }
        if (!Pointer(object + EntityTree, head)) {
            error = "native Group entity-map header is unavailable at group " + std::to_string(object);
            return false;
        }
        if (!Pointer(head + 8, root)) {
            error = "native Group entity-map root is unavailable at header " + std::to_string(head);
            return false;
        }
        uintptr_t found = 0, foundOwner = 0, foundEntity = 0;
        std::vector<uintptr_t> pending{root};
        std::unordered_set<uintptr_t> visited;
        size_t entries = 0;
        bool valid = true;
        while (!pending.empty() && valid) {
            uintptr_t node = pending.back();
            pending.pop_back();
            if (node == head) continue;
            unsigned char nil = 0;
            uintptr_t left = 0, right = 0, entity = 0, types = 0, type = 0;
            if (visited.size() >= 4096 || !visited.insert(node).second) {
                error = "native entity tree is cyclic or exceeds 4096 nodes";
                valid = false;
                break;
            }
            if (
                !Read(node + 0x19, &nil, sizeof nil) || nil ||
                !Pointer(node, left) || !Pointer(node + 0x10, right) ||
                !Pointer(node + 0x30, entity)) {
                error = "native entity tree node is invalid at " + std::to_string(node);
                valid = false;
                break;
            }
            if (!Pointer(entity + 0x68, types) || !Pointer(types, type)) {
                error = "native component-type list header is invalid for entity at " + std::to_string(entity);
                valid = false;
                break;
            }
            pending.push_back(left);
            pending.push_back(right);
            std::unordered_set<uintptr_t> typeNodes;
            while (type != types && valid) {
                uintptr_t components = 0, component = 0;
                if (++entries > 16384 || typeNodes.size() >= 256 || !typeNodes.insert(type).second ||
                    !Pointer(type + 0x18, components) || !Pointer(components, component)) {
                    error = "native component-type list node is invalid or cyclic at " + std::to_string(type);
                    valid = false;
                    break;
                }
                // Runtime Entity lists differ from the XML/reflection type tree and vectors.
                std::unordered_set<uintptr_t> componentNodes;
                while (component != components && valid) {
                    uintptr_t value = 0, control = 0;
                    if (++entries > 16384 || componentNodes.size() >= 4096 ||
                        !componentNodes.insert(component).second || !SharedValue(component + 0x10, value, control)) {
                        error = "native component-list node or shared ownership is invalid at " + std::to_string(component);
                        valid = false;
                        break;
                    }
                    uintptr_t table = 0;
                    if (!Read(value, &table, sizeof table)) {
                        error = "native component vtable is unreadable";
                        valid = false; break;
                    }
                    if (table == base + vtable &&
                        (!flow || Flow(reinterpret_cast<void*>(value), base, flow))) {
                        if (found) {
                            error = "native component selection is ambiguous";
                            valid = false; break;
                        }
                        found = value;
                        foundOwner = control;
                        foundEntity = entity;
                    }
                    if (!Pointer(component, component)) {
                        error = "native component-list next link is unavailable";
                        valid = false;
                    }
                }
                if (valid && !Pointer(type, type)) {
                    error = "native component-type list next link is unavailable";
                    valid = false;
                }
            }
        }
        if (!valid || !found) {
            if (valid) error = "native parsed group has no matching component";
            else if (error.empty()) error = "native parsed group graph exceeds traversal bounds";
            return false;
        }
        group.Retain(reinterpret_cast<void*>(object), reinterpret_cast<void*>(owner));
        machine.Retain(reinterpret_cast<void*>(found), reinterpret_cast<void*>(foundOwner));
        entityOwner = foundEntity;
        return true;
    }
public:
    bool Capture(const void* shared, uintptr_t base, const char* flow, std::string& error) {
        if (!base || !flow || !*flow) {
            error = "native state machine selection requires an image and flow";
            return false;
        }
        return CaptureMatching(shared, base, 0x2c98cf0, flow, error);
    }
    bool CaptureType(const void* shared, uintptr_t base, uintptr_t vtable, std::string& error) {
        if (!base || !vtable) {
            error = "native component selection requires an image and vtable";
            return false;
        }
        return CaptureMatching(shared, base, vtable, nullptr, error);
    }
    void* Component() const { return machine.object; }
    void* Group() const { return group.object; }
    void* Entity() const { return reinterpret_cast<void*>(entityOwner); }
    void Reset() { entityOwner = 0; machine.Reset(); group.Reset(); }
};
}
