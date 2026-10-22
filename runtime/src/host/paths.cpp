#include "host/paths.h"

#include <cstdlib>
#include <string>

#ifdef _WIN32
#include <windows.h>
#include <shlobj.h>
#endif

namespace cafe::host {
namespace {

namespace fs = std::filesystem;

#ifdef _WIN32

fs::path known_folder(const KNOWNFOLDERID& id) {
    PWSTR wide = nullptr;
    fs::path result;
    if (SHGetKnownFolderPath(id, KF_FLAG_CREATE, nullptr, &wide) == S_OK && wide != nullptr) result = wide;
    if (wide != nullptr) CoTaskMemFree(wide);
    return result;
}

fs::path known_folder_or(const KNOWNFOLDERID& id, const char* variable) {
    fs::path folder = known_folder(id);
    if (folder.empty()) {
        if (const char* v = std::getenv(variable); v && *v) folder = v;
        else folder = home_dir();
    }
    return folder;
}

#else

fs::path xdg(const char* variable, const char* fallback) {
    const char* v = std::getenv(variable);
    return v && *v == '/' ? fs::path(v) : home_dir() / fallback;
}

#endif

} // namespace

fs::path home_dir() {
#ifdef _WIN32
    if (const fs::path profile = known_folder(FOLDERID_Profile); !profile.empty()) return profile;
    if (const char* p = std::getenv("USERPROFILE"); p && *p) return p;
    return ".";
#else
    const char* h = std::getenv("HOME");
    return h && *h ? fs::path(h) : fs::path("/");
#endif
}

fs::path data_dir() {
#ifdef _WIN32
    return known_folder_or(FOLDERID_RoamingAppData, "APPDATA");
#else
    return xdg("XDG_DATA_HOME", ".local/share");
#endif
}

fs::path config_dir() {
#ifdef _WIN32
    return known_folder_or(FOLDERID_RoamingAppData, "APPDATA");
#else
    return xdg("XDG_CONFIG_HOME", ".config");
#endif
}

fs::path cache_dir() {
#ifdef _WIN32
    return known_folder_or(FOLDERID_LocalAppData, "LOCALAPPDATA");
#else
    return xdg("XDG_CACHE_HOME", ".cache");
#endif
}

fs::path launcher_dir() {
#ifdef _WIN32
    return known_folder_or(FOLDERID_Programs, "APPDATA");
#else
    return data_dir() / "applications";
#endif
}

fs::path executable_path() {
#ifdef _WIN32
    std::wstring buffer(260, L'\0');
    for (;;) {
        const DWORD n = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
        if (n == 0) return {};
        if (n < buffer.size()) {
            buffer.resize(n);
            return fs::path(buffer);
        }
        buffer.resize(buffer.size() * 2);
    }
#else
    std::error_code ec;
    const fs::path self = fs::read_symlink("/proc/self/exe", ec);
    return ec ? fs::path() : self;
#endif
}

} // namespace cafe::host
