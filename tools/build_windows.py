#!/usr/bin/env python3
"""Build the port for Windows, from Linux or from Windows itself.

    python tools/build_windows.py "/path/to/TEKKEN TAG 2 Wii U EDITION (EU).wua"

fetches a toolchain (llvm-mingw: clang, lld and libc++ for mingw-w64) and
the port's libraries (SDL3, shaderc, the Vulkan loader, OpenSSL, zstd, zlib,
as MSYS2's CLANG64 packages) into third_party/windows/, extracts the game's
executable, recompiles it, builds ttt2.exe in build-windows/ and puts it with
the .dll files it needs into dist/windows/ (and dist/ttt2-windows-x64.zip).
Everything is pinned or resolved from the package database, so the same
command works on both hosts; on Windows, CMake and Ninja are fetched too
when they are not installed.

The game is needed once, as a .wua archive or as a title folder (code/,
content/, meta/ with update v16 merged in; its code/Tekken.rpx is used as
it is): later runs reuse local/wua and generated/ttt2 (--recompile redoes
the recompilation). Without a game, --tools-only builds
the tools and the tests (and --test runs them, under Wine on Linux).
"""

import argparse
import hashlib
import io
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WINDOWS_DIR = ROOT / "third_party" / "windows"
DOWNLOADS = WINDOWS_DIR / "downloads"
LLVM_MINGW_DIR = WINDOWS_DIR / "llvm-mingw"
SYSROOT = WINDOWS_DIR / "clang64"
TOOLS_DIR = WINDOWS_DIR / "tools"
TOOLCHAIN_FILE = ROOT / "cmake" / "toolchain-windows-llvm-mingw.cmake"

HOST_WINDOWS = platform.system() == "Windows"

# llvm-mingw release (https://github.com/mstorsjo/llvm-mingw/releases).
LLVM_MINGW_TAG = "20261020"
LLVM_MINGW_URL = "https://github.com/mstorsjo/llvm-mingw/releases/download/{tag}/{asset}"

# MSYS2's CLANG64 repository: the packages the port links, with what they
# depend on resolved from the repository's database.
MSYS2_MIRROR = "https://repo.msys2.org/mingw/clang64/"
MSYS2_PREFIX = "mingw-w64-clang-x86_64-"
MSYS2_PACKAGES = ["sdl3", "shaderc", "vulkan-headers", "vulkan-loader", "openssl", "zstd", "zlib"]

# For a Windows host without them.
CMAKE_VERSION = "3.31.8"
CMAKE_URL = f"https://github.com/Kitware/CMake/releases/download/v{CMAKE_VERSION}/cmake-{CMAKE_VERSION}-windows-x86_64.zip"
NINJA_VERSION = "1.13.1"
NINJA_URL = f"https://github.com/ninja-build/ninja/releases/download/v{NINJA_VERSION}/ninja-win.zip"

# Libraries Windows provides; anything else a program imports is shipped.
SYSTEM_DLLS = {
    "kernel32.dll", "user32.dll", "gdi32.dll", "advapi32.dll", "shell32.dll", "ole32.dll", "oleaut32.dll",
    "shlwapi.dll", "ws2_32.dll", "winmm.dll", "imm32.dll", "version.dll", "setupapi.dll", "crypt32.dll",
    "bcrypt.dll", "ntdll.dll", "msvcrt.dll", "comdlg32.dll", "uuid.dll", "dwmapi.dll", "hid.dll", "cfgmgr32.dll",
    "userenv.dll", "dbghelp.dll", "psapi.dll", "iphlpapi.dll", "rpcrt4.dll", "uxtheme.dll", "comctl32.dll",
    "winspool.drv", "dinput8.dll", "xinput1_4.dll", "dxgi.dll", "d3d11.dll", "d3d12.dll", "opengl32.dll",
    "powrprof.dll", "secur32.dll", "mpr.dll", "wininet.dll", "ncrypt.dll", "wldap32.dll", "netapi32.dll",
}


def say(message):
    print(f"==> {message}", flush=True)


def fail(message):
    sys.exit(f"build_windows: {message}")


def run(command, **kwargs):
    print("+ " + " ".join(str(c) for c in command), flush=True)
    subprocess.run([str(c) for c in command], check=True, **kwargs)


# ------------------------------------------------------------ downloading

def download(url, target):
    """Fetches `url` into `target` unless it is there already."""
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    say(f"downloading {url}")
    part = target.with_suffix(target.suffix + ".part")
    with urllib.request.urlopen(url) as response, open(part, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while chunk := response.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total and sys.stderr.isatty():
                print(f"\r    {done / 1e6:.0f} / {total / 1e6:.0f} MB", end="", file=sys.stderr, flush=True)
        if total and sys.stderr.isatty():
            print(file=sys.stderr)
    part.replace(target)
    return target


def zstd_decompress(data):
    """zstd data as bytes: Python 3.14's module, the zstandard package, or
    the zstd program."""
    try:
        from compression import zstd  # Python 3.14+
        return zstd.decompress(data)
    except ImportError:
        pass
    try:
        import zstandard
        return zstandard.ZstdDecompressor().decompressobj().decompress(data)
    except ImportError:
        pass
    for program in ("zstd", "unzstd"):
        if shutil.which(program):
            return subprocess.run([program, "-d", "-c"], input=data, capture_output=True, check=True).stdout
    fail("cannot read zstd archives: use Python 3.14 or newer, `pip install zstandard`, or install the zstd program")


def safe_members(archive, strip=0):
    """An archive's members with `strip` leading path components removed,
    none escaping the extraction folder (links included: llvm-mingw shares
    its headers between targets through relative links)."""
    for member in archive.getmembers():
        parts = Path(member.name).parts
        if any(part in ("..", "") for part in parts) or Path(member.name).is_absolute():
            raise RuntimeError(f"unsafe path in archive: {member.name}")
        if len(parts) <= strip:
            continue
        member.name = "/".join(parts[strip:])
        if member.issym() or member.islnk():
            if Path(member.linkname).is_absolute():
                continue
            target = member.linkname if member.islnk() else os.path.join(os.path.dirname(member.name), member.linkname)
            if os.path.normpath(target).startswith(".."):
                continue
        yield member


def extract_tar(path, destination, strip=0):
    say(f"extracting {path.name}")
    if path.suffix == ".zst":
        data = zstd_decompress(path.read_bytes())
        archive = tarfile.open(fileobj=io.BytesIO(data))
    else:
        archive = tarfile.open(path)
    with archive:
        destination.mkdir(parents=True, exist_ok=True)
        members = list(safe_members(archive, strip))
        if hasattr(tarfile, "data_filter"):
            archive.extractall(destination, members=members, filter="tar")
        else:
            archive.extractall(destination, members=members)


def extract_zip(path, destination, strip=0):
    say(f"extracting {path.name}")
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            parts = Path(info.filename).parts
            if ".." in parts or Path(info.filename).is_absolute() or len(parts) <= strip:
                continue
            target = destination.joinpath(*parts[strip:])
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(target, "wb") as out:
                shutil.copyfileobj(source, out)
            if (info.external_attr >> 16) & 0o111:
                target.chmod(target.stat().st_mode | 0o111)


# ------------------------------------------------------------ the sysroot

def llvm_mingw_asset():
    system = platform.system()
    if system == "Windows":
        return f"llvm-mingw-{LLVM_MINGW_TAG}-ucrt-x86_64.zip"
    if system == "Linux":
        machine = platform.machine()
        arch = {"x86_64": "x86_64", "AMD64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}.get(machine)
        if arch is None:
            fail(f"no llvm-mingw build for {machine}")
        return f"llvm-mingw-{LLVM_MINGW_TAG}-ucrt-ubuntu-22.04-{arch}.tar.xz"
    if system == "Darwin":
        return f"llvm-mingw-{LLVM_MINGW_TAG}-ucrt-macos-universal.tar.xz"
    fail(f"no llvm-mingw build for {system}")


def ensure_llvm_mingw(update):
    marker = LLVM_MINGW_DIR / ".ttt2-version"
    if marker.exists() and marker.read_text().strip() == LLVM_MINGW_TAG and not update:
        return
    asset = llvm_mingw_asset()
    archive = download(LLVM_MINGW_URL.format(tag=LLVM_MINGW_TAG, asset=asset), DOWNLOADS / asset)
    if LLVM_MINGW_DIR.exists():
        shutil.rmtree(LLVM_MINGW_DIR)
    if asset.endswith(".zip"):
        extract_zip(archive, LLVM_MINGW_DIR, strip=1)
    else:
        extract_tar(archive, LLVM_MINGW_DIR, strip=1)
    # (The C++ headers are in the shared include/ folder in the Windows zip
    # and reached through a per-target link in the Linux tarball.)
    headers = ("include/c++/v1/cstdint", "x86_64-w64-mingw32/include/c++/v1/cstdint")
    if not any((LLVM_MINGW_DIR / h).exists() for h in headers):
        fail(f"{asset} did not extract as expected: the C++ headers are missing")
    for needed in ("x86_64-w64-mingw32/lib/libc++.a", "bin/x86_64-w64-mingw32-clang++" + (".exe" if HOST_WINDOWS else "")):
        if not (LLVM_MINGW_DIR / needed).exists():
            fail(f"{asset} did not extract as expected: {needed} is missing")
    marker.write_text(LLVM_MINGW_TAG + "\n")


def read_msys2_database(mirror):
    """The repository's packages by name: {name: {field: [values]}}."""
    path = DOWNLOADS / "clang64.db.tar.zst"
    if path.exists():
        path.unlink()  # always current: old package files leave the mirror
    download(mirror + "clang64.db.tar.zst", path)
    packages = {}
    with tarfile.open(fileobj=io.BytesIO(zstd_decompress(path.read_bytes()))) as archive:
        for member in archive.getmembers():
            if not member.name.endswith("/desc"):
                continue
            fields, key = {}, None
            for line in archive.extractfile(member).read().decode().splitlines():
                if line.startswith("%") and line.endswith("%"):
                    key = line.strip("%")
                    fields[key] = []
                elif line and key:
                    fields[key].append(line)
            packages[fields["NAME"][0]] = fields
    return packages


def resolve_packages(packages):
    """The packages to install: the port's and, transitively, what they
    depend on (through what other packages provide)."""
    provided = {}
    for name, fields in packages.items():
        for item in fields.get("PROVIDES", []):
            provided.setdefault(re.split(r"[<>=]", item)[0], name)
    chosen, pending = [], [MSYS2_PREFIX + p for p in MSYS2_PACKAGES]
    while pending:
        name = re.split(r"[<>=]", pending.pop(0))[0]
        if name not in packages:
            name = provided.get(name)
        if name is None or name in chosen:
            continue
        if name not in packages:
            fail(f"{name} is not in the MSYS2 repository")
        chosen.append(name)
        pending += packages[name].get("DEPENDS", [])
    return [packages[name] for name in chosen]


def ensure_sysroot(mirror, update):
    marker = SYSROOT / ".ttt2-packages"
    if marker.exists() and not update:
        return
    say("resolving the MSYS2 CLANG64 packages")
    chosen = resolve_packages(read_msys2_database(mirror))
    files = []
    for fields in chosen:
        name = fields["FILENAME"][0]
        archive = download(mirror + name, DOWNLOADS / name)
        if "SHA256SUM" in fields:
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            if digest != fields["SHA256SUM"][0]:
                archive.unlink()
                fail(f"{name} is corrupt (sha256 {digest}); run again")
        files.append(name)
    if SYSROOT.exists():
        shutil.rmtree(SYSROOT)
    for name in files:
        with tarfile.open(fileobj=io.BytesIO(zstd_decompress((DOWNLOADS / name).read_bytes()))) as archive:
            members = [m for m in safe_members(archive, strip=1) if not m.name.startswith(".")]
            SYSROOT.mkdir(parents=True, exist_ok=True)
            if hasattr(tarfile, "data_filter"):
                archive.extractall(SYSROOT, members=members, filter="tar")
            else:
                archive.extractall(SYSROOT, members=members)
    marker.write_text("\n".join(files) + "\n")
    say(f"{len(files)} packages in {SYSROOT}")


def ensure_build_tools():
    """cmake and ninja: the installed ones, or on Windows fetched copies."""
    tools = {}
    for name in ("cmake", "ninja"):
        found = shutil.which(name)
        if found is None and HOST_WINDOWS:
            for candidate in TOOLS_DIR.rglob(name + ".exe"):
                found = str(candidate)
                break
        if found is None and HOST_WINDOWS:
            if name == "cmake":
                archive = download(CMAKE_URL, DOWNLOADS / Path(CMAKE_URL).name)
                extract_zip(archive, TOOLS_DIR / "cmake", strip=1)
                found = str(TOOLS_DIR / "cmake" / "bin" / "cmake.exe")
            else:
                archive = download(NINJA_URL, DOWNLOADS / Path(NINJA_URL).name)
                extract_zip(archive, TOOLS_DIR / "ninja")
                found = str(TOOLS_DIR / "ninja" / "ninja.exe")
        if found is None:
            fail(f"{name} is not installed (on Arch: pacman -S cmake ninja)")
        tools[name] = found
    return tools


# ------------------------------------------------------------ building

def configure(tools, build_dir, generated, extra=()):
    command = [tools["cmake"], "-S", ROOT, "-B", build_dir, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
               f"-DCMAKE_MAKE_PROGRAM={tools['ninja']}", f"-DCMAKE_TOOLCHAIN_FILE={TOOLCHAIN_FILE}",
               f"-DTTT2_LLVM_MINGW={LLVM_MINGW_DIR}", f"-DTTT2_WINDOWS_SYSROOT={SYSROOT}"]
    if generated is not None:
        command.append(f"-DTTT2_GENERATED_DIR={generated}")
    run(command + list(extra))


def build(tools, build_dir, targets, jobs):
    command = [tools["cmake"], "--build", build_dir]
    if jobs:
        command += ["-j", str(jobs)]
    if targets:
        command += ["--target", *targets]
    run(command)


def windows_tools(tools, jobs, build_dir):
    configure(tools, build_dir, None)
    build(tools, build_dir, ["wua_extract", "cafe-recomp"], jobs)
    return {"wua_extract": build_dir / "wua_extract.exe", "cafe-recomp": build_dir / "recomp" / "cafe-recomp.exe"}


def host_tools(tools, jobs, build_dir):
    """wua_extract and cafe-recomp that run on this machine, as a command
    prefix and the two programs: on Windows the ones built here; on Linux
    the native ones (build/ if it has them, else built in build-windows/host,
    else the Windows ones under Wine)."""
    if HOST_WINDOWS:
        return {**windows_tools(tools, jobs, build_dir), "prefix": []}
    existing = {"wua_extract": ROOT / "build" / "wua_extract", "cafe-recomp": ROOT / "build" / "recomp" / "cafe-recomp"}
    if all(p.exists() for p in existing.values()):
        return {**existing, "prefix": []}
    native = build_dir / "host"
    say("building the extraction tool and the recompiler for this machine")
    try:
        run([tools["cmake"], "-S", ROOT, "-B", native, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
             f"-DCMAKE_MAKE_PROGRAM={tools['ninja']}"])
        build(tools, native, ["wua_extract", "cafe-recomp"], jobs)
        return {"wua_extract": native / "wua_extract", "cafe-recomp": native / "recomp" / "cafe-recomp", "prefix": []}
    except subprocess.CalledProcessError:
        wine = shutil.which("wine")
        if wine is None:
            fail("the tools need this machine's build requirements (see README: Requirements), or Wine")
        say("no native build: running the Windows tools under Wine")
        return {**windows_tools(tools, jobs, build_dir), "prefix": [wine]}


def find_rpx():
    candidates = sorted((ROOT / "local" / "wua").glob("*_v16/code/Tekken.rpx"))
    # The update's folder (0005000E...) before the base game's (00050000...).
    candidates.sort(key=lambda p: not p.parts[-3].lower().startswith("0005000e"))
    return candidates[0] if candidates else None


def prepare_generated(game, tools, jobs, build_dir, recompile):
    """The game's code as C++ in generated/ttt2: extracted from the game
    into local/wua and recompiled, each step kept from the last run."""
    generated = ROOT / "generated" / "ttt2"
    if (generated / "sources.cmake").exists() and not recompile:
        return generated
    rpx = find_rpx()
    if rpx is None and game is None:
        fail("the game is needed the first time: build_windows.py GAME (a .wua or a game folder), or --tools-only")
    if game is not None and game.is_dir():
        # A title folder (code/, content/, meta/, with the update's files
        # over the game's), or an install folder with the title in game/:
        # nothing to extract, the executable is right there.
        for candidate in (game / "code" / "Tekken.rpx", game / "game" / "code" / "Tekken.rpx"):
            if candidate.exists():
                rpx = candidate
                break
        else:
            fail(f"{game} holds no code/Tekken.rpx")
    programs = host_tools(tools, jobs, build_dir)
    if rpx is None:
        say("extracting the game's executables and metadata into local/wua")
        (ROOT / "local" / "wua").mkdir(parents=True, exist_ok=True)
        run(programs["prefix"] + [programs["wua_extract"], "extract-analysis", game, ROOT / "local" / "wua"])
        rpx = find_rpx()
        if rpx is None:
            fail(f"{game} holds no version 16 Tekken.rpx")
    say(f"recompiling {rpx} into {generated}")
    generated.mkdir(parents=True, exist_ok=True)
    run(programs["prefix"] + [programs["cafe-recomp"], rpx, generated])
    return generated


# ------------------------------------------------------------ packaging

def pe_imports(path):
    """The names of the .dll files a Windows program or library imports."""
    data = path.read_bytes()
    (e_lfanew,) = struct.unpack_from("<I", data, 0x3C)
    if data[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        raise RuntimeError(f"{path} is not a Windows program")
    coff = e_lfanew + 4
    sections, optional_size = struct.unpack_from("<H", data, coff + 2)[0], struct.unpack_from("<H", data, coff + 16)[0]
    optional = coff + 20
    magic = struct.unpack_from("<H", data, optional)[0]
    directories = optional + (0x70 if magic == 0x20B else 0x60)
    import_rva, import_size = struct.unpack_from("<II", data, directories + 8)
    if import_rva == 0:
        return []
    table = optional + optional_size
    ranges = []
    for i in range(sections):
        virtual_size, virtual_address, raw_size, raw_pointer = struct.unpack_from("<IIII", data, table + i * 40 + 8)
        ranges.append((virtual_address, max(virtual_size, raw_size), raw_pointer))

    def offset(rva):
        for address, size, pointer in ranges:
            if address <= rva < address + size:
                return pointer + rva - address
        raise RuntimeError(f"{path}: address 0x{rva:x} is in no section")

    names = []
    descriptor = offset(import_rva)
    while True:
        lookup, _, _, name_rva, thunk = struct.unpack_from("<IIIII", data, descriptor)
        if name_rva == 0:
            break
        at = offset(name_rva)
        end = data.index(b"\0", at)
        names.append(data[at:end].decode("ascii"))
        descriptor += 20
    return names


def package(build_dir, dist, zip_path):
    program = build_dir / "ttt2.exe"
    if not program.exists():
        fail(f"{program} was not built")
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(parents=True)
    shutil.copy2(program, dist / program.name)
    search = [SYSROOT / "bin", LLVM_MINGW_DIR / "x86_64-w64-mingw32" / "bin"]
    pending, shipped, missing = [dist / program.name], set(), set()
    while pending:
        for name in pe_imports(pending.pop()):
            lower = name.lower()
            if lower in shipped or lower in SYSTEM_DLLS or lower.startswith("api-ms-win-") or lower.startswith("ext-ms-"):
                continue
            source = next((d / name for d in search if (d / name).exists()), None)
            if source is None:
                missing.add(name)
                continue
            shutil.copy2(source, dist / name)
            shipped.add(lower)
            pending.append(dist / name)
    if missing:
        say("warning: not found, so not shipped: " + ", ".join(sorted(missing)))
    (dist / "README.txt").write_text(
        "Tekken Tag Tournament 2 Wii U Edition, native Windows port\n"
        "\n"
        "ttt2.exe needs the game: TEKKEN TAG TOURNAMENT 2 Wii U EDITION, European\n"
        "release with update v16, as a .wua archive or a folder with code/, content/\n"
        "and meta/. No game files are included.\n"
        "\n"
        "Double-click ttt2.exe (or run ttt2.exe --install) to choose the game and\n"
        "an install folder: the game, this program and its .dll files are copied\n"
        "there and a Start Menu shortcut is added. Or run it from a terminal with\n"
        "the .wua as its argument to play straight from the archive.\n"
        "\n"
        "Saves: %APPDATA%\\ttt2\\save. Compiled shaders and, when there is no\n"
        "terminal, the program's messages (ttt2.log): %LOCALAPPDATA%\\ttt2.\n"
        "Needs Windows 10 or 11 on x86-64 with AVX2, and a Vulkan 1.3 driver.\n"
        "\n"
        "The .dll files beside the program are SDL3 (zlib license), shaderc,\n"
        "glslang and SPIRV-Tools (Apache-2.0), the Vulkan loader (Apache-2.0),\n"
        "OpenSSL (Apache-2.0), zstd (BSD) and zlib, from MSYS2's packages.\n")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(dist.iterdir()):
            archive.write(file, Path("ttt2") / file.name)
    say(f"packaged {dist} ({len(shipped)} libraries) and {zip_path}")


# ------------------------------------------------------------ main

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("game", nargs="?", help="the game's .wua archive or title folder (needed once)")
    parser.add_argument("--tools-only", action="store_true", help="build the tools and tests, not the game")
    parser.add_argument("--sysroot-only", action="store_true", help="fetch the toolchain and libraries, then stop")
    parser.add_argument("--update-sysroot", action="store_true", help="fetch the toolchain and libraries again")
    parser.add_argument("--recompile", action="store_true", help="run cafe-recomp again on the extracted game")
    parser.add_argument("--test", action="store_true", help="run the tests (under Wine on Linux)")
    parser.add_argument("--no-package", action="store_true", help="leave dist/ alone")
    parser.add_argument("--jobs", "-j", type=int, help="parallel compile jobs")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build-windows")
    parser.add_argument("--dist", type=Path, default=ROOT / "dist" / "windows")
    parser.add_argument("--mirror", default=MSYS2_MIRROR, help="MSYS2 mirror (the mingw/clang64/ folder)")
    args = parser.parse_args()
    mirror = args.mirror if args.mirror.endswith("/") else args.mirror + "/"
    build_dir = args.build_dir.resolve()

    say("pinned sources (ZArchive, addrlib)")
    run([sys.executable, ROOT / "tools" / "setup_deps.py"])
    say("Windows toolchain and libraries")
    ensure_llvm_mingw(args.update_sysroot)
    ensure_sysroot(mirror, args.update_sysroot)
    if args.sysroot_only:
        return
    tools = ensure_build_tools()

    if args.tools_only:
        configure(tools, build_dir, None)
        build(tools, build_dir, [], args.jobs)
    else:
        game = Path(args.game).resolve() if args.game else None
        if game is not None and not game.exists():
            fail(f"{game} does not exist")
        generated = prepare_generated(game, tools, args.jobs, build_dir, args.recompile)
        configure(tools, build_dir, generated)
        say("building ttt2.exe (compiling the generated code takes a few minutes)")
        build(tools, build_dir, ["ttt2"], args.jobs)
        if args.test:
            build(tools, build_dir, [], args.jobs)
    if args.test:
        say("running the tests")
        environment = dict(os.environ)
        if not HOST_WINDOWS:
            environment["WINEPATH"] = str(SYSROOT / "bin")
            environment.setdefault("WINEDEBUG", "-all")
        run([shutil.which("ctest") or Path(tools["cmake"]).with_name("ctest" + (".exe" if HOST_WINDOWS else "")),
             "--test-dir", build_dir, "--output-on-failure"], env=environment)
    if not args.tools_only and not args.no_package:
        package(build_dir, args.dist.resolve(), args.dist.resolve().parent / "ttt2-windows-x64.zip")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        fail(f"{Path(str(error.cmd[0])).name} failed (exit code {error.returncode})")
    except KeyboardInterrupt:
        fail("interrupted")
