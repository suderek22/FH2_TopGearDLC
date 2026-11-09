#pragma once
#include <string>

namespace TrackLifecycle {
inline bool Valid(const std::string& name) {
    if (name.size() > 32 || (name != "trackloadercps" &&
        (name.compare(0, 15, "trackloadercps_") || name.size() <= 15))) return false;
    return name.find_first_not_of("abcdefghijklmnopqrstuvwxyz0123456789_") == std::string::npos;
}
struct Names {
    std::string group = "trackloadercps";
    std::string Flow(const char* state) const { return group + "." + state; }
    std::string Entity(const char* state) const { return "entities_" + group + "_" + state; }
    std::string FlowPath() const { return "stateflow\\" + group + ".xml"; }
    std::string EntityPath(const char* state) const { return "EntityModel\\" + Entity(state) + ".xml"; }
    bool Contains(const std::string& path) const {
        return path == FlowPath() || path == EntityPath("enter") || path == EntityPath("return") ||
            path == EntityPath("static_route") || path == EntityPath("mainland");
    }
};
}
