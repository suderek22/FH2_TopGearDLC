#pragma once
#include <cstddef>
#include <cstdint>

#ifndef TRACKLOADER_STANDALONE
#include "..\CarPackStudio\native\include\track_visit_api.h"
#endif

namespace TrackVisit {
#ifdef TRACKLOADER_STANDALONE
struct Client {
    uint32_t size;
    void* user;
    void (*tick)(void*, void*, int);
};
struct Api {
    uint32_t size;
    int (*attach)(const Client*, char*, size_t);
    int (*acquire)(char*, size_t);
    int (*release)(char*, size_t);
    int (*held)();
};
#else
using Client = CPSTrackVisitClient;
using Api = CPSTrackVisitApi;
#endif
}
