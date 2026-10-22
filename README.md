# Tekken Tag 2 Ultimate Edition

<img width="1593" height="888" alt="image" src="https://github.com/user-attachments/assets/3bd58409-1442-4ce7-b9a6-8f036f38f4f8" />


An unofficial native Linux port (with a Windows build) of **Tekken Tag
Tournament 2 Wii U Edition**, made by static recompilation: the game's PowerPC code is translated to C++
ahead of time and runs as native x86-64 code on a reimplementation of the
Wii U's system libraries. There is no CPU emulation; the game's GPU commands
and shaders are translated to Vulkan as it runs.

> [!IMPORTANT]
> **No game files are included.** This repository holds only original tools
> and runtime code; you need your own copy of the game
> ([see below](#the-game)). The C++ generated from the game, programs built
> from it, an install folder, and captures or dumps made with the debugging
> options all contain the game's code or assets: do not share or upload
> them, and do not attach them to issues.
>
> This project is not affiliated with or endorsed by Bandai Namco
> Entertainment or Nintendo. TEKKEN, Tekken Tag Tournament and Wii U are
> trademarks of their respective owners.

## Features

- **Plays natively**: intro, menus, character select, arcade mode and versus
  matches, with sound and saves. Arcade mode has been played through by
  hand.
- **120 frames per second** on displays of 100 Hz and more: between each
  two frames the game renders, the port shows one more, drawn from the same
  frame with the camera, objects and character skeletons blended halfway.
  The game's logic, physics and hitboxes stay at 60 Hz. When other programs
  keep the GPU busy, frames go out without their extra frame rather than
  slowing the game down.
- **Higher resolutions**: up to 4x the original 720p; by default the
  smallest scale that covers your display (2x, 2560x1440, on 1080p and
  1440p displays; 3x on 4K).
- **Even frame pacing** on 120 Hz and 240 Hz displays (with
  `VK_KHR_present_wait2`): the game's clock follows the display's
  refreshes, so every image stays on screen equally long.
- **Controls and icons as on a computer**: menus confirm with the bottom face
  button and go back with the right one, as on the other consoles (Nintendo
  controllers keep the Wii U's way). Button icons follow the controller in
  use: PlayStation controllers get PlayStation-style icons, any other
  neutral ones that show each face button by its place on the pad. The
  game's texts show the icons where they named the Wii U's buttons, and the
  title screen's logo no longer says "Wii U Edition". All of it is made by
  the port from the Wii U game's own files.
- **Installs like any other program**: `ttt2 --install` copies the game into
  a folder you choose and adds it to the application menu.
- **Made to be modified**: every function of the game is a C++ function that
  can be replaced ([see below](#changing-the-game)).

## Status

Playable from boot through arcade mode. Not done yet: the Wii U GamePad's
screen and speaker, online play, the software keyboard and error viewer
screens, and audio effects the game does not seem to use. Only the European
version with update 16 is supported (other versions have different code).
Tested on Linux with an AMD GPU (RADV) under KDE Plasma (Wayland). The
[Windows build](#windows) compiles and passes the tests under Wine, and has
not been run on Windows itself yet. Design notes, measurements and
milestones are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## The game

You need **TEKKEN TAG TOURNAMENT 2 Wii U EDITION**, European release, with
update v16, as a `.wua` archive (Cemu can create one from an installed title;
the update may be merged into the game's folder or stored beside it), or as
a folder with the game's `code/`, `content/` and `meta/` and the update's
files over them.

| Property | Value |
| --- | --- |
| Product code | WUP-P-AKNP |
| Title ID | `000500001010F800` (update `0005000E1010F800`) |
| Version | 16 |
| `code/Tekken.rpx` SHA-256 | `fc0270465d384386f67804e48716e30bfbe85a5a1004e3a6bfe3ac75f9de5019` |

`cafe-recomp` refuses any other `Tekken.rpx`, and the port and its installer
refuse a game whose executable is not the one they were built from.

## Requirements

- Linux on x86-64 with AVX2 and FMA (x86-64-v3: Intel Haswell, AMD Excavator
  or newer).
- A Vulkan 1.3 GPU and driver with `VK_EXT_external_memory_host` and push
  descriptors (current AMD, Intel and NVIDIA drivers).
- To build: Git, CMake 3.20+, Ninja, Clang (or GCC 15+), Python 3, and the
  development files of SDL 3.2 or newer, shaderc, the Vulkan loader, Vulkan
  headers recent enough to define `VK_KHR_present_wait2` (mid-2025 or
  newer), OpenSSL 3, zstd and zlib.

On Arch Linux:

```bash
sudo pacman -S --needed base-devel git cmake ninja clang python zstd zlib openssl vulkan-headers vulkan-icd-loader sdl3 shaderc
```

Other distributions package the same libraries under similar names; older
releases such as Ubuntu 24.04 and Debian 12 ship SDL and Vulkan headers that
are too old.

## Building

```bash
git clone https://github.com/Petexy/Tekken-Tag-2-Ultimate-Edition.git && cd Tekken-Tag-2-Ultimate-Edition
python3 tools/setup_deps.py                      # ZArchive and AMD's addrlib, at pinned revisions

cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++
cmake --build build --target wua_extract cafe-recomp

# The game's executables and metadata (about 70 MB per title folder) into local/wua
build/wua_extract extract-analysis "/path/to/TEKKEN TAG 2 Wii U EDITION (EU).wua" local/wua
# The v16 executable's code as C++ in generated/ttt2 (in the update's
# folder, or in the game's when the update is merged into it)
build/recomp/cafe-recomp local/wua/*_v16/code/Tekken.rpx generated/ttt2

cmake -B build -DTTT2_GENERATED_DIR="$PWD/generated/ttt2"
cmake --build build --target ttt2
```

Recompiling takes a few seconds; compiling the generated code takes a few
minutes. `local/`, `generated/`, `third_party/` and `build*/` are ignored by
Git, so nothing derived from the game ends up in a commit; keep captures and
dumps from the debugging options under `local/` too.

## Playing

Run the game straight from its archive (or from a game folder):

```bash
build/ttt2 "/path/to/TEKKEN TAG 2 Wii U EDITION (EU).wua"
```

Or install it: choose the archive and the install folder in file dialogs, or
give them as `--install GAME FOLDER`. This copies the game (16 GB) and the
program into that folder (a folder that is not empty gets a
`Tekken Tag Tournament 2` folder inside it) and adds **Tekken Tag Tournament
2** to the application menu (`--no-launcher` leaves that out). Then start it
from the menu, or run `ttt2` without arguments; the `.wua` is no longer
needed.

```bash
build/ttt2 --install
build/ttt2 --update      # after a rebuild: copies the new program into the install
```

Saves go to `~/.local/share/ttt2/save` and compiled shaders to
`~/.cache/ttt2` (on Windows `%APPDATA%\ttt2\save` and `%LOCALAPPDATA%\ttt2`).
F11 toggles fullscreen.

| Wii U GamePad | Keyboard |
| --- | --- |
| D-pad | Arrow keys |
| A / B / X / Y | X / Z / S / A |
| L / R | Q / W |
| ZL / ZR | 1 / 2 |
| + / − | Enter / Backspace |
| HOME | H |
| Left stick | I / J / K / L |

Keys go by position, as on a US QWERTY keyboard. Gamepads work through SDL
by button position, as on the Wii U: the right face button is A, the bottom
one B, the top one X and the left one Y, and fights use them that way. In
menus the bottom button (B, Z on the keyboard) confirms and the right one
goes back, except with a Nintendo controller, which confirms with its A as on
the Wii U. The icons follow the last controller used (the keyboard counts as
a neutral one).

| Environment variable | Effect |
| --- | --- |
| `TTT2_SCALE=1..4` | render scale (default from the display) |
| `TTT2_FPS=60/120/180/240` | frames shown per second (default 120 on displays of 100 Hz and more) |
| `TTT2_FULLSCREEN=1` | start fullscreen |
| `TTT2_AUDIO=0` | no sound |
| `TTT2_AUDIO_VOLUME=0..100` | volume in percent (default 100) |
| `TTT2_SAVE_DIR=<folder>` | where saves go |
| `TTT2_CONTROLLER=playstation/nintendo/xbox` | icons and menu buttons for this kind of controller, whichever is used |
| `TTT2_CHARACTERS=<folder>` | where character packs are (default `~/.local/share/ttt2/characters`) |

Debugging variables are listed in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#running-the-port).

## Windows

The same code builds for Windows 10 and 11 (x86-64 with AVX2, a Vulkan 1.3
driver), with clang targeting mingw-w64. One script does the whole build,
from Linux or from Windows itself:

```bash
python tools/build_windows.py "/path/to/TEKKEN TAG 2 Wii U EDITION (EU).wua"
```

On Windows, `tools\build_windows.cmd` runs it (double-click it, or drop
the `.wua` on it); it needs Python 3 and Git installed. The script fetches
into `third_party/windows/` a pinned [llvm-mingw](https://github.com/mstorsjo/llvm-mingw)
toolchain (clang, lld, libc++) and the port's libraries (SDL3, shaderc, the
Vulkan loader, OpenSSL, zstd, zlib) as MSYS2's CLANG64 packages, plus CMake
and Ninja on a Windows host without them (on Linux they must be installed,
and the game's executable is extracted and recompiled with the native tools:
`build/` if it is there, else a native build in `build-windows/host`, else
the Windows ones under Wine). Then it extracts and recompiles the game as
above (once; `--recompile` redoes it; a game folder with `code/`, `content/`
and `meta/` works in place of the `.wua`, its `code/Tekken.rpx` recompiled
as it is), builds `ttt2.exe` in `build-windows/`
and puts it with the `.dll` files it needs into `dist/windows/` and
`dist/ttt2-windows-x64.zip`. Without a game, `--tools-only` builds the tools
and the tests, and `--test` runs them (under Wine on Linux). The tests'
own needs are fetched too: numpy and Pillow into the Python running the
script (`--no-pip` leaves that out), `fmt` with the other packages, and
Dolphin's interpreter into `third_party/ref/dolphin`; with the game known,
the instruction tests are configured with its `Tekken.rpx`. The CMake
toolchain file is [`cmake/toolchain-windows-llvm-mingw.cmake`](cmake/toolchain-windows-llvm-mingw.cmake)
for building by hand.

`ttt2.exe` works as on Linux: with the `.wua` as its argument it plays
straight from the archive; `ttt2.exe --install` (or just opening
`ttt2.exe` with no game installed) copies the game, the program and its
`.dll` files into a folder chosen in a dialog and adds a Start Menu shortcut.
It is a windowed program: started from a terminal it prints there, started
from the desktop its messages go to `%LOCALAPPDATA%\ttt2\ttt2.log`.
Character packs go in `%APPDATA%\ttt2\characters` (the importer itself
runs on Linux only). Paths and arguments are UTF-8, as on Linux.

## Adding fighters

The fighters of Tekken 7 that Tekken Tag Tournament 2 does not have can join
the roster beside its own (no fighter is replaced): Kazumi Mishima, Lucky
Chloe, Eliza, Claudio Serafino, Akuma, Gigas, Josie Rizal, Katarina Alves,
Master Raven, Shaheen, Fahkumram, Leroy Smith, Lidia Sobieska, Kunimitsu II,
Negan, Geese Howard and Noctis. Each gets a cell of its own on the select
screen: the free ends of the top row first, then new columns beyond the rows'
ends (19 cells; the order follows the order of import).

Nothing of them is in this repository: an importer makes them from **your own
copies of both games** and writes character packs on your computer. You need:

- Tekken 7 for PC (Steam), installed and started from Steam at least once
  (the importer finds it, its Proton and its prefix through Steam), and Steam
  running;
- the game installed with `ttt2 --install` (the importer reads its folder);
- gamescope, `pactl` (PipeWire or PulseAudio), MinGW
  (`x86_64-w64-mingw32-gcc`, to build a small helper the first time) and
  Python 3 with numpy and Pillow.

```bash
python3 tools/characters/import_character.py --all --game /path/to/your/install
```

A fighter's moveset exists only inside the running Tekken 7, so the importer
first saves the movesets it does not have yet: Tekken 7 starts in the
background, with no window and no sound (gamescope's headless mode and a
silent audio output of its own), the game's own loader loads the fighters
while it sits on its start-up screens, and it is closed again. Nothing is
pressed and no menu is used, so your key bindings and settings do not matter.
Only fighters your Steam account owns are imported (Steam must be running; it
tells which DLC you own): a fighter whose DLC you have not bought is skipped,
whichever movesets are already saved. Tekken 7 also keeps Geese's and
Noctis's costumes (and Noctis's voice) encrypted in its files: in the same
run the game reads those files with its own file reader, the one its loading
uses, which hands them over decoded, and they are saved to
`~/.local/share/ttt2/tekken7/files/` (about 670 MB for both) for the
importer. `tools/characters/dump_tekken7.py` does that step alone (the
movesets go to `~/.local/share/ttt2/tekken7/movesets/`); movesets saved with
[TKMovesets2](https://github.com/Kiloutre/TKMovesets) work too
(`--movesets FOLDER`).

The fighters' names for the importer: `kazumi`, `chloe`, `eliza`, `claudio`,
`akuma`, `gigas`, `josie`, `katarina`, `master_raven`, `shaheen`,
`fahkumram`, `leroy`, `lidia`, `kunimitsu2`, `negan`, `geese`, `noctis` (one or several; each
takes about a minute, and what they share is made once at the end). The packs
go to `~/.local/share/ttt2/characters/` (a folder per fighter, and `shared/`,
the title's pictures, colours and name calls the added fighters share;
`$TTT2_CHARACTERS` picks another folder). Delete a fighter's folder and import
any other fighter again to remove them. **The packs hold Namco's assets: keep
them to yourself and never upload or share them.** The importer refuses to
write them inside this repository, and `.gitignore` keeps the games' file
types out of commits.

Each has their own model with both players' colours (skin tones matched to
Tekken Tag Tournament 2's own fighters), moves, voice, command list, and their
pictures and name on the menus' screens and in fights; Kazumi's tiger appears
in the moves that summon it, long hair and tails hang and swing, their moves'
effects (Eliza's and Akuma's fireballs, auras, smoke) are Tekken 7's own,
converted to Tekken Tag Tournament 2's effect files (close, not the same:
Tekken 7's glow and some of its shading are left out), Negan swings his bat
and hits with it, Noctis summons his swords, daggers, spear and shield for the
moves that use them, Leroy's cane and Master Raven's sheathed katana are where
they carry them, and the moves that hit with a weapon (Kunimitsu II's blades
too) reach as far as the weapon. Left out for now: Rage Arts and Rage Drives,
power crush armour (those moves are there, without it), Screw becomes Bound,
the weapons they take in hand only for some moves (Kunimitsu II's blades,
Master Raven's drawn sword, Leroy's folding staff, Negan's knife), the weapons
of Noctis's Rage Art, Katarina's and Leroy's sunglasses, and Gigas's back
tubes.

## How it works

- `recomp/`: `cafe-recomp` loads the RPX, finds all 205,200 function entry
  points and writes the 79,564 reachable ones as C++ functions (`--all`
  writes every one). Guest calls are C++ calls; indirect calls go through an
  address table.
- `runtime/src/os/`: the Cafe OS libraries the game imports (threads,
  synchronisation, memory, file system, saves, input, sound mixing), written
  natively.
- `runtime/src/gx2/` and `runtime/src/gpu/`: GX2 writes real GPU command
  buffers, which a command processor executes; the Vulkan renderer
  (`runtime/src/gpu/vulkan/`) translates the GPU's shaders to SPIR-V
  (`runtime/src/latte/`), interpolates frames and paces presentation.
- `runtime/src/title/`: what this game shows differently on a computer: the
  menu buttons, the icon sets, texts and the title screen, made at start-up
  from the game's archives (their format is described in
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#presentation-on-a-computer)),
  and the added fighters from their character packs.
- `tools/characters/`: the importer that makes a character pack from the
  player's own Tekken 7 and Tekken Tag Tournament 2
  ([`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#added-fighters)).
- `tests/`: a memory-model test, tests of reading split archives and of the
  game's file formats and the icons and texts made from them, and
  differential tests that run every instruction form the game uses against
  Dolphin's PowerPC interpreter.

### Changing the game

A generated function `sub_<address>` can be replaced by defining it in a
`.cpp` file added to the `ttt2` target (`target_sources(ttt2 PRIVATE ...)` in
`CMakeLists.txt`); every call, direct or through a function pointer, then
reaches the replacement, and `sub_<address>_orig` is still the original:

```cpp
#include "cafe/ppc_ops.h"

PPC_FUNC(sub_0123ABCD_orig);
PPC_FUNC(sub_0123ABCD) {
    // ... before
    sub_0123ABCD_orig(ctx, base);
    // ... after
}
```

## Tests

```bash
cmake --build build      # also builds the tests
ctest --test-dir build
```

runs `memory_model_test`, `wua_layers_test`, `title_test` and, with Python 3,
numpy and Pillow, `character_import_test` (the importer's formats). The
differential tests (`semantics_test`, `control_flow_test`) also need `fmt`,
the game's `Tekken.rpx` (by default
`local/wua/000500001010f800_v16/code/Tekken.rpx`; otherwise configure with
`-DTTT2_RPX=<path>`), and a partial checkout of Dolphin:

```bash
git clone --filter=blob:none --no-checkout https://github.com/dolphin-emu/dolphin.git third_party/ref/dolphin
git -C third_party/ref/dolphin sparse-checkout set --no-cone \
  /Source/Core/Core/PowerPC/Interpreter/ /Source/Core/Core/PowerPC/Gekko.h \
  /Source/Core/Core/PowerPC/PowerPC.h '/Source/Core/Core/PowerPC/ConditionRegister.*' \
  /Source/Core/Common/BitField.h /Source/Core/Common/BitUtils.h /Source/Core/Common/CommonTypes.h \
  '/Source/Core/Common/FloatUtils.*' /Source/Core/Common/Inline.h /Source/Core/Common/Swap.h
git -C third_party/ref/dolphin checkout 771fb154059c5812d6a715a23226249cc42877a2
cmake -B build && cmake --build build && ctest --test-dir build
```

They build Dolphin's interpreter (GPL-2.0 or later) into the test programs;
nothing of Dolphin is stored in this repository.

## License

This repository's code is under the [MIT License](LICENSE). It does not cover
the game, or anything generated from it.

Fetched by `tools/setup_deps.py`, under their own licenses:
[ZArchive](https://github.com/Exzap/ZArchive) (MIT-0) and AMD's address
library ([decaf-emu/addrlib](https://github.com/decaf-emu/addrlib), MIT,
built into the `ttt2` program). The port also links SDL3 (zlib), shaderc
(Apache-2.0), the Vulkan loader (Apache-2.0), OpenSSL (Apache-2.0), zstd
(BSD) and zlib.

## Acknowledgements

- [Cemu](https://github.com/cemu-project/Cemu) and
  [decaf-emu](https://github.com/decaf-emu/decaf-emu), whose reverse
  engineering of the Wii U's system libraries and GPU documented much of the
  behaviour reimplemented here (structure layouts, register values, formats,
  GX2's surface layout rules). This repository's code is its own.
- [Dolphin](https://github.com/dolphin-emu/dolphin), whose hardware-tested
  PowerPC interpreter is the reference the instruction tests compare
  against, and whose hardware research measured the processor's estimate
  tables.
- [XenonRecomp](https://github.com/hedge-dev/XenonRecomp) and
  [N64Recomp](https://github.com/N64Recomp/N64Recomp), for showing how far
  function-level static recompilation can go.
- [TKMovesets2](https://github.com/Kiloutre/TKMovesets) and
  [TekkenMovesetExtractor](https://github.com/Kiloutre/TekkenMovesetExtractor),
  whose research documented the Tekken 7 and TTT2 moveset layouts, their
  animations and what their requirement and property ids mean, which the
  character importer builds on (its movesets are in TKMovesets2's .tkmvst
  layout, which it can also read);
  [UE Viewer](https://github.com/gildor2/UEViewer) and
  [CUE4Parse](https://github.com/FabianFG/CUE4Parse), for Tekken 7's Unreal
  Engine 4.14 packages. The importer's code is its own.
