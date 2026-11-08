#pragma once
#include <windows.h>

namespace TrackNative {
struct Shared {
    void* object = nullptr;
    void* control = nullptr;
    Shared() = default;
    Shared(const Shared&) = delete;
    Shared& operator=(const Shared&) = delete;
    ~Shared() { Reset(); }
    void Retain(void* value, void* owner) {
        if (owner) InterlockedIncrement(reinterpret_cast<volatile LONG*>(static_cast<char*>(owner) + 8));
        Reset();
        object = value;
        control = owner;
    }
    void Reset() {
        void* old = control;
        object = control = nullptr;
        if (!old) return;
        auto counts = reinterpret_cast<volatile LONG*>(static_cast<char*>(old) + 8);
        auto table = *reinterpret_cast<void(***)(void*)>(old);
        if (!InterlockedDecrement(counts)) {
            table[0](old);
            if (!InterlockedDecrement(counts + 1)) table[1](old);
        }
    }
};
static_assert(sizeof(Shared) == 16, "Native shared pointer ABI");
}
