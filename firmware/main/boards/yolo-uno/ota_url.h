#pragma once

#include <string>

inline std::string YoloUnoOtaUrl(const std::string& saved, const std::string& configured) {
    const std::string& url = saved.empty() ? configured : saved;
    if (url == "http://192.168.1.7:8003/xiaozhi/ota/" ||
        url == "http://192.168.1.7:8003/xiaozhi/ota") {
        return "http://192.168.1.7:8005/xiaozhi/ota/";
    }
    return url;
}
