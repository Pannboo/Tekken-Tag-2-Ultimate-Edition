# Building for Windows with llvm-mingw (clang, lld, libc++ for mingw-w64) and
# the port's libraries from MSYS2's CLANG64 packages, from Linux or from
# Windows itself. tools/build_windows.py fetches both into third_party/windows
# and configures with this file; by hand:
#
#   cmake -S . -B build-windows -G Ninja -DCMAKE_BUILD_TYPE=Release \
#     -DCMAKE_TOOLCHAIN_FILE=cmake/toolchain-windows-llvm-mingw.cmake \
#     [-DTTT2_LLVM_MINGW=<llvm-mingw folder>] [-DTTT2_WINDOWS_SYSROOT=<clang64 folder>]
#
# (the defaults are third_party/windows/llvm-mingw and third_party/windows/clang64).

set(CMAKE_SYSTEM_NAME Windows)
set(CMAKE_SYSTEM_PROCESSOR AMD64)

set(TTT2_LLVM_MINGW "${CMAKE_CURRENT_LIST_DIR}/../third_party/windows/llvm-mingw" CACHE PATH "llvm-mingw toolchain folder")
set(TTT2_WINDOWS_SYSROOT "${CMAKE_CURRENT_LIST_DIR}/../third_party/windows/clang64" CACHE PATH "MSYS2 CLANG64 packages (the clang64 folder)")
get_filename_component(TTT2_LLVM_MINGW "${TTT2_LLVM_MINGW}" ABSOLUTE)
get_filename_component(TTT2_WINDOWS_SYSROOT "${TTT2_WINDOWS_SYSROOT}" ABSOLUTE)

set(_ttt2_triple x86_64-w64-mingw32)
foreach(tool clang clang++ windres)
    set(_path "${TTT2_LLVM_MINGW}/bin/${_ttt2_triple}-${tool}")
    if(EXISTS "${_path}.exe")
        set(_path "${_path}.exe")
    elseif(NOT EXISTS "${_path}")
        message(FATAL_ERROR "llvm-mingw not found at ${TTT2_LLVM_MINGW} (${_path} is missing): run tools/build_windows.py, or set TTT2_LLVM_MINGW")
    endif()
    set(_ttt2_${tool} "${_path}")
endforeach()
set(CMAKE_C_COMPILER "${_ttt2_clang}")
set(CMAKE_CXX_COMPILER "${_ttt2_clang++}")
set(CMAKE_RC_COMPILER "${_ttt2_windres}")

if(NOT EXISTS "${TTT2_WINDOWS_SYSROOT}/lib/libSDL3.dll.a")
    message(FATAL_ERROR "the Windows libraries are not at ${TTT2_WINDOWS_SYSROOT}: run tools/build_windows.py, or set TTT2_WINDOWS_SYSROOT")
endif()

# Libraries, headers and CMake packages from the MSYS2 folder only.
set(CMAKE_FIND_ROOT_PATH "${TTT2_WINDOWS_SYSROOT}")
set(CMAKE_PREFIX_PATH "${TTT2_WINDOWS_SYSROOT}")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
set(OPENSSL_ROOT_DIR "${TTT2_WINDOWS_SYSROOT}")
set(ZLIB_ROOT "${TTT2_WINDOWS_SYSROOT}")
# (MSYS2's .pc files name /clang64; the CMake packages find themselves.)
set(PKG_CONFIG_EXECUTABLE "PKG_CONFIG_EXECUTABLE-NOTFOUND" CACHE FILEPATH "" FORCE)

# The C++ runtime is linked into the programs (libc++, libunwind), so the
# only .dll files beside them are the libraries' own, from the MSYS2 folder.
set(CMAKE_EXE_LINKER_FLAGS_INIT "-static-libstdc++ -Wl,-Bstatic,-lunwind,-Bdynamic")
set(CMAKE_SHARED_LINKER_FLAGS_INIT "-static-libstdc++ -Wl,-Bstatic,-lunwind,-Bdynamic")

# Cross-building on Linux: tests run under Wine when it is there.
if(NOT CMAKE_HOST_WIN32)
    find_program(TTT2_WINE wine)
    if(TTT2_WINE)
        set(CMAKE_CROSSCOMPILING_EMULATOR "${TTT2_WINE}")
    endif()
endif()
