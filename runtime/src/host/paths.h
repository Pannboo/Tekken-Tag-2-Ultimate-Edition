#pragma once

// Where the port keeps the user's files, and this program's own path, on
// each host:
//
//                 Linux                              Windows
//   data_dir      $XDG_DATA_HOME   (~/.local/share)  %APPDATA%
//   config_dir    $XDG_CONFIG_HOME (~/.config)       %APPDATA%
//   cache_dir     $XDG_CACHE_HOME  (~/.cache)        %LOCALAPPDATA%
//
// Callers add "ttt2" and what is theirs (saves, character packs, compiled
// shaders, the install record). The XDG variables count only when they
// hold absolute paths, as the specification says.

#include <filesystem>

namespace cafe::host {

std::filesystem::path home_dir();
std::filesystem::path data_dir();
std::filesystem::path config_dir();
std::filesystem::path cache_dir();
// Where a launcher goes: $XDG_DATA_HOME/applications (a .desktop file), or
// the user's Start Menu programs folder (a shortcut).
std::filesystem::path launcher_dir();

// This program's file, readable even when its file has been replaced (a
// rebuild during an install); empty if the host cannot tell.
std::filesystem::path executable_path();

} // namespace cafe::host
