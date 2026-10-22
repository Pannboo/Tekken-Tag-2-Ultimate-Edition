# Standalone port architecture

Goal: a native Linux x86-64 Tekken Tag Tournament 2 (Wii U, EU v16) that runs
**without CPU emulation, without Cemu**, and whose code can be freely modified
afterwards. Decided 2026-10-01.

```
Tekken.rpx ──► cafe-recomp ──► generated/*.cpp  (one C++ function per guest function)
                                      │
                 runtime/ (native Cafe OS: coreinit, gx2→Vulkan, snd, vpad, fs)
                                      │
                 PPC_FUNC(sub_...) overrides of guest functions ──► ttt2 (ELF x86-64)
```

## Why our own recompiler

nWiiURecomp lifts *basic blocks* that return to a dispatch loop with an
instruction budget and an interpreter fallback: an accelerated emulator, built
for hosting inside Cemu. A port wants *function-level* output (the
XenonRecomp / N64Recomp model): `bl` is a C++ call, `blr` a return, every guest
function a named, overridable C++ function. (nWiiURecomp was evaluated first
and is not used.)

## Facts the design rests on

Measured on the v16 RPX with `build/recomp/cafe-census`:

| Fact | Consequence |
| --- | --- |
| `.symtab` holds 205,140 sized `STT_FUNC` symbols that partition `.text` exactly (0 overlaps, 5 gaps); names are stripped | Function boundaries are given, not guessed |
| 29,769,084 instructions, **0 undecodable words** in any function | No data islands inside code |
| 190 instruction variants, standard Espresso set incl. paired singles; no `sc` | All OS access is through the 508 imports |
| Game writes GQR5 (`mtspr 901`) | Quantized `psq_l/psq_st` need real GQR state |
| 470,080 `bctrl`, 8,775 `bctr`; 106,292 relocations take function addresses | Indirect calls go through a guest-address → host-function table |
| Switches are `lis/addi; mtctr; bctr` into an in-function table of `b`s (222 functions) | `bctr` with known in-function targets becomes `switch` + `goto` |
| ~1,000 branches enter the save/restore-register helpers at `0x0913D06C..0x0913D3F4` mid-sequence | Branch targets inside another function become alternate entry functions |
| Entry calls an undefined weak symbol (address 0) behind a guard | Null calls trap with a diagnostic |
| Only 79,507 functions / 6.0M instructions (20.3%) are reachable from the entry point and address-taken functions | Compile the reachable set optimised; the rest cheaply, logging if ever called |

Every stored code address in an RPL carries a relocation, which is what makes
the reachability bound sound.

## Generated code conventions

- Guest state: `PPCContext` (r0–r31, f0–f31 as paired doubles, CR as eight
  fields, LR, CTR, XER, FPSCR, GQR0–7, reservation).
- Guest memory: one 4 GiB host reservation; guest address `a` is `base + a`.
  Data stays big-endian; loads/stores byte-swap.
- Every guest load and store is a volatile access (`ppc_ops.h`): guest memory
  is shared between guest threads, so the compiler must perform each access
  in program order. With plain loads clang compiled a guest spin-wait
  (`lwz; cmpwi; bne` to itself) into a single read and the game's worker
  handshake broke. `build/tests/memory_model_test` guards this.
- Each function: `void sub_XXXXXXXX(PPCContext& ctx, uint8_t* base)`, emitted
  as a weak alias of `sub_XXXXXXXX_orig`, so user code can replace any function
  by defining it, and still call the original.
- Imports: calls to stub addresses become calls to
  `cafe_import_<module>_<name>`. The runtime defines the implemented ones;
  generated weak defaults stop with "unimplemented import" for the rest.
- Planned: a names file mapping addresses to readable names as they are
  identified.

## Runtime (native Cafe OS)

Hosts: Linux (the original) and Windows (clang for mingw-w64, lld, libc++;
`tools/build_windows.py`, `cmake/toolchain-windows-llvm-mingw.cmake`). What
differs between them is kept in a few places: guest memory
(`memory.cpp`: mmap/mprotect or VirtualAlloc reserve/commit), the fault
handler (`diagnostics.cpp`: SIGSEGV or a vectored exception handler), host
threads (`os/kernel.h`: `HostThread`, pthreads or `_beginthreadex` with the
stack size as a reservation), files by descriptor with positional reads
(`host/file.cpp`: pread/pwrite or ReadFile/WriteFile with an offset), the
user's folders and the program's path (`host/paths.cpp`: XDG or the known
folders) and the installer's launcher (a .desktop file or a Start Menu
shortcut). Generated code is the same: COFF has weak aliases and weak
definitions, so overriding a `sub_XXXXXXXX` works as on ELF (checked with
lld). On Windows the C++ runtime is linked statically, `setjmp` is the
non-SEH form (OSExitThread leaves generated code with `longjmp`) and the
program's manifest makes the narrow APIs UTF-8.

Implemented in host C++, no guest-code interpretation:

1. **coreinit**: heaps, threads (guest thread = host thread), mutex /
   condition / semaphore / event / alarm / message queue, time, OSDynLoad,
   filesystem mapped onto the extracted content directory, save directory
   under `~/.local/share/ttt2/`.
2. **gx2 → GPU** (see below): GX2 implemented natively, writing PM4 command
   buffers that a command processor executes against a Latte register file
   and hands to a rendering backend: Vulkan, or null for headless runs.
   Latte shaders the game ships are translated to GLSL and compiled to
   SPIR-V at run time; tiled surfaces are detiled.
3. **snd_core / snd_user → host audio**: AX voices mixed natively and
   played through SDL3 (see the audio section below).
4. **vpad / padscore → SDL3 gamepad**.
5. **nsysnet / nlibcurl / nn_\***: offline stubs.

Cemu and decaf-emu were references for the system's and the GPU's behaviour,
and Dolphin's interpreter is the instruction tests' oracle; no code from them
is in this repository. Facts such as structure layouts, register values and
the processor's estimate tables come from their reverse engineering and
hardware measurements.

Guest threads are host threads; every kernel object is examined and changed
under one kernel lock. A blocked thread sleeps on a *wait channel* keyed by
the object it waits for (a mutex, condition, semaphore, event, message
queue, thread queue or thread, by guest address; or the interrupt lock,
display, GPU, alarm schedule), and waking an object notifies only its
channel. With one shared condition variable every mutex unlock and every
OSRestoreInterrupts woke all ~40 of the title's blocked threads: the title's
allocator locks a mutex per allocation, and the boot-time loading screen
took ~17 s of herd wake-ups for 0.9 s of work (now ~1 s; CPU use fell from
~5 cores to a quarter of one).

System libraries the title loads with OSDynLoad are implemented the same
way: `swkbd` (software keyboard) and `erreula` (error viewer) have no UI
yet, so a request for text is confirmed with the title's own initial text
and an error is printed and acknowledged. An OSDynLoad export the runtime
lacks still gets an address; calling it stops with its name.

### GX2 and the GPU

```
GX2 (runtime/src/gx2/) ──PM4──► command buffers in guest memory
                                     │ GX2Flush / display lists
                                     ▼
command processor thread (runtime/src/gpu/) ── Latte register file
   draws, clears, copies, swaps ──► Backend (Vulkan; null when headless)
   timestamps, GPU-written memory, swap/flip counts ──► guest
```

- GX2 writes real PM4 packets, big-endian, into the command buffer pool
  GX2Init receives, used as a ring, or into display lists. Context states
  work as on hardware: register writes are shadowed into the
  GX2ContextState while it is current and loaded back with LOAD_* packets.
  Operations the console performs with GX2-internal shaders (clears,
  surface copies, resolves, scan-buffer copies, swaps) are runtime-defined
  packets (opcodes 0x01-0x08) carrying the GX2 structures the title passed.
- Addresses in command buffers are guest virtual addresses; the title never
  translates addresses itself.
- Surface layouts (size, alignment, pitch, mip offsets, tile mode) come from
  AMD's address library in its R600 variant (MIT, pinned in
  `dependencies.lock.json`, `third_party/addrlib`), with the console's
  parameters, because the title allocates from them and ships pre-tiled
  textures laid out by them.
- Fetch shaders are generated as real Latte microcode (little-endian, the
  GPU's order), so the shader translator reads vertex layouts from one
  format whether GX2 or the title produced them.
- The display flips frames at 59.94 Hz, honouring the swap interval;
  GX2GetSwapStatus reports what the title paces its loop on.
- GX2CopySurface with an unaligned linear (LINEAR_SPECIAL) surface on
  either side is a synchronous CPU copy, as on the console: the title
  copies each texture out of one staging buffer and reuses the buffer at
  once. Other copies go to the GPU in order.
- GX2Invalidate's CPU flag (and DMA engine transfers) tell the renderer the
  CPU wrote memory (`gpu::cpu_wrote`); SURFACE_SYNC packets are GPU cache
  operations only. The title flushes the whole texture cache several times
  a frame, which says nothing about what changed.
- A texture over a render target of a different format with the same
  element size is copied from it, reinterpreted: the title compresses
  textures on the GPU by rendering each 4x4 block as one texel (BC1 over
  R16G16B16A16, BC3 over R32G32B32A32_UINT) and samples the memory as BC1
  or BC3, every mip level from its own target.
- The title never uses multisampled render targets and never issues a
  stream-out "draw opaque" (it does not import GX2DrawStreamOut); neither
  is implemented.

### Audio

Every 3 ms AX frame (`runtime/src/os/ax.cpp`) mixes each playing voice,
runs the title's frame callbacks, passes each device's output through the
device final-mix callbacks (48 kHz planar 32-bit buffers in 16-bit range:
TV 6 channels, GamePad 4 x 2) and plays the TV's channels 0-1 as stereo
(`runtime/src/host/audio.cpp`, SDL3). A voice reads PCM16, PCM8 or
DSP-ADPCM from guest memory at its 16.16 rate ratio (relative to AX's
32 kHz renderer), linearly interpolated, scaled by its volume envelope and
mixed into each device channel by its device mix; volumes ramp by their
per-sample deltas. Frames are steady 3 ms ticks, as on the console: the
title's sound engine fills its voices from its own threads, and frames run
in bursts at the device's demand read audio it has not written yet (that
silenced half of the output after the intro). With a device the tick is
nudged by at most 0.5% to keep ~2048 frames queued, following the device's
clock (in practice by a few hundredths of a percent); if the queue runs dry
(start-up, a stall) silence refills it at once.

The title's CRI ADX2 middleware decodes and mixes in software and outputs
5.1 at 44.1 kHz through six looping PCM16 voices of 2,880 samples; their
device mixes fold them to TV stereo, and their volume envelopes come from
the MIX library (`snd_user.cpp`: input levels in 0.1 dB, ramped over one
frame). No voice sends to the aux (effect) buses, so AXFX effects are
never run; filters are accepted and not applied.

### Latte shaders

`runtime/src/latte/` decodes Latte microcode (control flow, ALU groups,
texture and vertex fetches, subroutines) and translates it to GLSL
(`translate.cpp`). Each invocation runs as one thread of the console GPU:
GPRs are integer `ivec4`s that float instructions bit-cast, an ALU group
reads every operand before it writes, PV/PS carry the previous group's
results, and DX9-style `MUL`/`MULADD`/`DOT4` treat 0 × anything as 0. The
per-thread active mask and push/pop stack are variables, so every clause
is guarded and the jumps that only skip inactive threads are dropped;
DX10 loops become `while` loops with `break`. Relative register addressing
(`R[base + AR]`, used by loops over register arrays such as the main
menu's depth-of-field blur) goes through `rel_load`/`rel_store`, which
switch over the program's registers (`SQ_PGM_RESOURCES.NUM_GPRS`) so the
register file is only ever indexed by constants: indexing it directly
made the driver keep all 128 registers in scratch memory, and that one
blur took 16.5 ms a pass at 1440p (now 0.07 ms; every shader the title
uses through a match compiles with no scratch memory, checked with
`RADV_DEBUG=shaderstats`). What the microcode does not
decide (vertex semantics, the VS→PS parameter linkage, texture types,
render target number types, alpha test, point sprites, stream-out) comes
from the register file (`environment.cpp`) and is part of the shader key.
The fetch shader is inlined at `CALL_FS`.

Shaders read guest memory directly through buffer device addresses
(`shader_abi.h`): vertex buffers (decoded and endian-swapped in the
shader), uniform blocks (raw little-endian, as the title stores them) and
buffer fetches; stream-out (`MEM_STREAM`) writes guest memory the same
way. `cafe-shader translate <dump dir>` translates and compiles every
draw recorded with `TTT2_DUMP_SHADERS`.

### Vulkan renderer

`runtime/src/gpu/vulkan/`: Vulkan 1.3 with dynamic rendering, push
descriptors and extended dynamic state; one pipeline per shader pair,
attachment formats and blend state.

- Guest memory (MEM2, MEM1) is imported with VK_EXT_external_memory_host,
  so the GPU reads the title's buffers in place.
- Submission is asynchronous: command buffers (8 in turn) and upload
  memory (8 chunks of a 256 MiB host buffer) are tracked on a timeline
  semaphore and reused once the GPU has finished with them. The command
  processor never waits; a retirement thread performs what the guest can
  observe (retired timestamps, end-of-pipe memory writes, finished
  frames) in command order once the GPU work before it has finished, so
  buffers the guest reuses after a wait are no longer read.
- Render targets and depth buffers are GPU images keyed by address,
  format and size (the title aliases memory between differently sized
  targets). A texture is an image per resource: copied from a render
  target at its address if the GPU wrote that last, otherwise detiled from
  guest memory and reloaded when the CPU writes it and its hash changes.
  Depth buffers sampled as textures are copied through a buffer.
- Write-back: the title reads some render targets with the CPU, as the
  console's GPU renders into guest memory. Its exposure averages the scene
  down to one element of a 64x64 target and reads it two frames later
  (three targets in turn); with nothing there it read black and turned the
  exposure up, which washed stages out (Odeum of Illusions: a white floor,
  white spotlights). Small colour targets at the title's size (up to
  64 KiB, stored as in guest memory) are copied to staging memory at the
  end of each submission that wrote them (before a frame's replays) and
  tiled into guest memory by the retirement thread once it finished,
  before the CPU can see that it has.
- Detiling (`gpu/tiling.cpp`) uses addrlib once per 8×8 micro tile: in
  thin single-sample modes every micro tile has the same internal layout
  (verified against per-element addrlib, `TTT2_CHECK_TILING=1`).
- The TV scan buffer is blitted, letterboxed, to an SDL3 window
  (`runtime/src/host/window.cpp`, on the process's main thread).
- Upscaling: targets the size of the screen and its halvings (heights 720,
  368, 192, 96) get images `TTT2_SCALE` times larger (default: enough to
  cover the display, 2 on 1080p and 1440p, 3 on 4K). Shadow maps, the
  GPU's texture-compression targets and the GamePad screen keep the
  title's size. Passes into upscaled targets scale the viewport, scissor
  and point size; pixel shaders see `gl_FragCoord` in the title's pixels;
  textures copied from upscaled targets stay upscaled, and shaders scale
  texel coordinates, size queries and texel offsets for them
  (`DrawConstants` carries the scale and a per-stage mask). Copies
  between images of different scale are filtered blits.
- Validated 2026-10-02 with the Khronos layer (core, synchronization,
  object lifetime, thread safety) through boot, menus and a match: no
  errors or hazards. One warning remains by design,
  Undefined-Value-ShaderOutputNotConsumed: some depth-only passes use pixel
  shaders that also write colour, which Vulkan discards when no colour
  attachment is bound; trimming those outputs would multiply shader
  variants.
- Frame interpolation (`gpu/vulkan/interpolate.cpp`, default on displays of
  100 Hz or more, `TTT2_FPS=60/120/180/240`): the title's logic, physics
  and hitboxes stay at 59.94 steps a second; between two of its frames the
  renderer shows K-1 more. The command processor logs each frame (register
  writes and operations with their arguments, nothing the guest can
  observe) and replays it after the swap; every draw's constant data
  (uniform blocks, buffer resources: camera, transforms, the characters'
  bone matrices, fetched by the vertex shaders) is copied into a
  GPU-visible record that the real draw reads too, and a replay blends it
  with the previous frame's matching draw, value by value where both are
  ordinary floats, except values that are whole numbers or 64ths in both
  frames: indices kept as floats and sprite-sheet cells step to this
  frame's value (blending the school stage crowd's flipbook cell drew the
  halves of neighbouring cells: legs above, bodies sunk into the ground;
  Norway's fire drew blobs in the sky). Draws match by key (shaders,
  targets, vertex count and first texture): first those whose data did not
  change, then each with the most similar draw near its place among those
  with its key, so sprites sharing a key keep their partners when others
  come and go (a blinking cursor once paired every later menu sprite with
  its neighbour). Only depth-tested (3D) draws blend: menus and the HUD
  place parts of a widget with vertex data the CPU writes each frame, which
  a replay cannot blend, so they are shown as the frame has them
  (`TTT2_INTERP_2D=1` blends them too). Draws whose data mostly jumps (a
  different object, a cut) and frames where fewer than 80% of draws match
  are shown as they are. Replays skip anything that writes guest memory
  (stream-out writes, CPU-side surface copies); render targets a frame
  reads before writing them start replays from their start-of-frame
  content, and the next frame sees the real frame's. With unblended replays
  (`TTT2_INTERP_TEST=1`) every extra frame equals its real frame pixel for
  pixel (checked through menus, character select and fights). Particles
  whose geometry the CPU rebuilds each frame and the HUD move at 60 Hz, and
  so do models skinned through stream-out: in fights the fighters are
  (point draws that write their skinned vertices), so their extra frames
  show the real frame's pose. A frame whose swap comes while the GPU has
  yet to finish the previous frame's images gets no extra frames (the
  previous image stays up through their part of its slot): the title learns
  a frame is done only after its replays, so on a shared or overloaded GPU
  they would slow the game itself (measured: a fight on a desktop
  compositor taking ~80% of the GPU went from 46-51 to 55-60 title frames a
  second; the statistics line counts the extra frames skipped).
- Presentation (`gpu/vulkan/present.cpp`) runs on its own thread and on a
  compute queue (AMD's asynchronous compute, `TTT2_PRESENT_BLIT=1` for
  the rendering queue): a present there waits only for its own frame, not
  for rendering queued after it. A compute shader scales the frame,
  letterboxed, into the swapchain. The mode follows the display the
  window is on, chosen again when the window moves to another display or
  its mode changes. On a display refreshing at a multiple of 60 Hz of at
  least 120 Hz with present waits (`VK_KHR_present_wait2`), the thread
  presents at every refresh in FIFO order, a few presents ahead of the
  screen (about 8 ms: 3 at 240 Hz, 2 at 120 Hz; KWin latches a commit
  about two refreshes before showing it, and tells late that it did), and
  counts refreshes one per present reaching the screen, plus, by time
  since the last present it waited for, any refresh the screen showed
  twice (once the next present confirms it: a single late wake-up is not
  one), part refreshes carried over (a display slower than reported by
  other than a whole factor) and, when it fell behind or the compositor
  stalled, the time that passed. Every 60th of a second of them is the title's vertical blank
  (`gpu::host_vsync`; the 59.94 Hz timer stands in when they stop for two
  frames, e.g. for a hidden window, or at once when presentation stops
  following the display, and the blanks it gives count against the
  host's when they resume), and each frame's images are due at fixed
  refreshes after the vblank its frame started from, the extra frames
  first and the real one last. So the title runs at the display's own
  rate (59.99 Hz on a 239.97 Hz display) and every image stays on screen
  for the same number of refreshes: measured in fights at 2x on a 240 Hz
  KWin desktop, 99% of images shown for exactly two refreshes (before,
  frames timed by the clock alone came 6 to 11 ms apart). The time
  between presents it waited for checks the reported rate: a display
  refreshing more than 2% faster (in two windows of 240 presents in a
  row) chooses the mode again with the measured rate; a slower one, or a
  compositor skipping refreshes, is already counted by time
  (`TTT2_DISPLAY_HZ` fakes a reported rate to test this). Presents that fail or do not reach the screen show the newest
  finished image at once, so the queue keeps moving; when the renderer
  replaces the presentation images (the TV buffer changed size) the
  thread lets go of them first. The lead from vblank to the first image
  is what frames needed over the last ten seconds, all but the slowest
  1%, raised at once when frames keep coming late (7-9 refreshes,
  ~30-37 ms, in fights). On other displays, including 60 Hz ones where
  presenting every refresh would only add its queue's latency
  (`TTT2_PRESENT_TIMED=1` forces it), frames get display slots one title
  frame apart (re-anchored when the title falls behind) at a lead after
  their swap and are presented at those times (mailbox). With
  interpolation the real image is shown half a frame later than
  without.
- Compiled SPIR-V and the Vulkan pipeline cache persist in
  `~/.cache/ttt2` (`$XDG_CACHE_HOME/ttt2`).

## Presentation on a computer

`runtime/src/title/` changes what the title shows, not how it plays, and
makes everything from the Wii U game's own files at start-up
(`title::prepare_presentation`, about 0.35 s).

**The title's archives** (`title/archive.*`). `content/hdd/dataNNN.ofs`
indexes `dataNNN.bin`: chunks `ofsi` (each entry's name hash, the CRC-32 of
its name with the register starting at zero, and a word) and
`ofsd` (one big-endian offset per entry into the `.bin`'s payload at 0x100,
plus the end; entries are 16-byte aligned and the low four bits give the
entry's length modulo 16). Every entry is scrambled by XOR with four 32-bit
linear congruential streams, one per big-endian word of each 16-byte block,
seeded from the name hash (x = x * 0xBDE95 + 0x8EF345, four times) and
stepped once per block (w = w * 1531 + 5011); most Wii U entries are then a
zlib stream behind a 16-byte `NRCZ` header (size, flags). Textures are
`NTP3` (BC1/BC2/BC3 blocks in rows, or ARGB8), fonts an `NFH` glyph table
plus an `NTP3` texture, texts `NTXB` banks. At start-up the title puts
every entry of every index into one tree by name hash, the archives in a
fixed order and the update's data100 last, so data100's newer copies of a
few entries win (a French text bank among them). The VFS can serve an
archive with entries replaced or added (`vfs::set_archive_entries`, calls
adding up): a rebuilt index, new entries after the last one, and the `.bin`
assembled from pieces of the original and the new entries.

**Menu buttons.** The title's menus ask which button confirms and which
cancels (`sub_03483B78`, `sub_03483C08`); the answer is a word at +8 of the
system settings object built by `sub_0864107C` (from the PlayStation 3
version's system setting; 1 on the Wii U: confirm with A, the right button).
The port overrides `sub_0864107C` to keep the object's address and writes 0
(confirm with the bottom button) unless a Nintendo controller is in use, at
start and whenever the controller changes. The help bar's icons follow the
same word. Fights read the buttons themselves, so they do not change.

**Icons** (`title/font.*`, `title/draw.*`). Button icons are glyphs of the
main font (data003, glyph table 3A27BC43, texture 4FC9A19E) at private-use
code points that name a button by place (E024 right, E025 bottom, E026 top,
E027 left, four on for highlighted ones; E036/E037 + and −; 2282/2283 L R,
2286/2287 ZL ZR; 253C… the D-pad; E015-E023 Tekken button combinations). Two
textures are made: neutral (face buttons as the font's own four-dot Tekken
button pictures with that button's dot lit, Start and Select as arrows) and
PlayStation-style (symbols, L1 R1 L2 R2, Start and Select, a split D-pad and
coloured dots, drawn with 16 samples per pixel and the font's letters). The
renderer swaps them in (`gpu/replacements.h`): a texture whose level 0 has a
registered MD5 loads the current set as RGBA8 instead, and loads again when
the controller changes (`host::on_controller_change`: the kind of the last
gamepad pressed, SDL's PS3/PS4/PS5 and Switch types).

**Fonts** (`title/font.*`). A glyph table (`NFH`) holds the font's size in
pixels at +4 and the glyph count at +8 (little- or big-endian, by font),
then from 0x450 a 32-byte record per glyph sorted by code point: from +4 its
box in the texture (x, y), left bearing, top above the baseline, advance and
line height (64ths of a pixel), width and height, and the code point
(big-endian) at +22; the rest is bookkeeping (the glyph's place among those
of its code points' high byte). Texts are drawn with the main font
(24 px, 3A27BC43/4FC9A19E), a plain one (descriptions, 7C74E49A/099AF947)
and the Korean and Japanese gothic fonts (22 px, help panels such as
Controller Setup; B1277EE3/C4C9633E, 46B70814/335915C9), which have Latin
letters; only the main font has the icon glyphs.

**Texts** (`title/text.*`). An `NTXB` record is the key (padded to four with
at least one zero), two bytes, the length in characters, the UTF-8 text and
the English original (each padded with at least two zeros); the bank's index
points at records, so a text can change in place. In every bank (data003's
and data100's), names of the Wii U's buttons (English, French, Italian,
German, Spanish: "the X Button", "bouton X", "X-Knopf", "+Control Pad", …)
become the buttons' icons, which are always shorter, and the prompt "PRESS
THE + BUTTON" and its translations show the Start icon. An icon in a text is
a symbol no text uses that all four fonts have (£ ¥ ¢ ª for the right,
bottom, top and left buttons, ＼ ³ » for +, − and the D-pad), its glyph
changed in each: in the main font to the icon's box and placement, in the
others to a box over unused glyphs (Þ, Œ, …; unused ideographs and Hangul
in the gothic fonts) where the icon is drawn, scaled to 21 texels, into the
font's texture (one per kind of controller). The changed banks and glyph
tables reach the title through the archive overlays.

**Title screen** (`title/title_screen.*`). The logo is a video ending on the
still in the title screen's texture set (data007 76C7E3CF). The new logo is
that still without "Wii U Edition": rows below the "TAG TOURNAMENT 2" banner
fade to black (the kanji tucked behind it), the subtitle's stroke that curls
up beside the banner is removed (thin bright parts connected to the
subtitle), and the logo moves 54 rows down. Video frames (BT.601
limited-range planes passed to the renderer's plane filter) that fit
a * Wii U still + b (least squares over the logo, b from the black around
it, the fit explaining over half the variance) become a * new still + b plus
what the fit left (the fade from white and the light sweeping over the
logo), small differences dropped as coding noise and the rest blurred where
the stills differ; chroma moves by a * the stills' difference. The copyright
lines (700x32 textures) lose the three words before the last seven ("Wii U
EDITION" before "& (C) 2012 …") and are centred again.

## Added fighters

`runtime/src/title/roster.*` adds fighters from character packs (folders in
`$TTT2_CHARACTERS`, else `~/.local/share/ttt2/characters`), made on the
player's machine from their own games by `tools/characters/`. This repository
holds no part of any fighter.

**Where it fits.** A fighter is a select id (0-58; the 63-entry code table
at `0x107C57F4` adds 59-62, the practice dummy, an empty dummy and the
Fight Lab's two mobs; 63 means "random" or "none") with a three-letter code,
character records (`0x107B4220`, 139 x 84 bytes: id, costume slot, place in
the UI order, motion row; 139 means "none") and a motion row (`0x105A51C4`,
68 x the keys of its motion pack, moveset and movelist). Added fighter k
(its pack's `slot`; a pack naming none takes the first free one) takes ids
the title does not have: select id 64 + k, records 140 + 2k and 141 + 2k
(held by the port, not in the title's table), a motion row (the rows no
fighter names, 58, 64, 61, 62 and 65, then rows from 69 on, whose keys the
port puts in the spare row 63 while `sub_075DA0C8` reads them) and a select
grid cell 62 + k. The title's per-fighter tables have 63 rows, so the
functions that would read past them for an added fighter, or turn its id
into 62 or "none", are overridden: the record accessors (`sub_033D502C`
and the functions around it), the code table's reader in the character
loader (`sub_044BE1EC`, run with a stand-in: Paul's code slot holds the
added fighter's code meanwhile, and the player gets the real id after), the
readers of tables indexed by id (given the donor's id, or the donor's row
lent the added fighter's entries while they run), and the match set-up's
id bytes (side struct +640, a member every 4288 bytes), which twelve setters
cap at 62 (the port writes the picks back after them and before the VS
screen reads them through `sub_06EEAEE4`). At start-up, after the title
registers its own fighters (`sub_065C3128`), the port registers each added
fighter's code with the title's key registry (`*0x107A3FB4`: per code, the
keys of its body files and part packs by type) and chara master
(`*0x107A71E8`), the way the title registers its own.

**Its cell.** The select grid (`0x107B7668`, 32-byte cells: fighter, the
cells the cursor moves to, column and row) has 62 cells; it grows over the
following, unused grid of the PlayStation 3's smaller roster. The added
cells sit at the free ends of the top row (columns 0-2 and 19-22), then in
new columns beyond the rows' ends (-1 and 23, then -2 and 24): the cursor's
place on the screen is computed from the column (`sub_06F4D7B8`: 48 pixels
a column, signed), and the importer puts each cell's picture in the grid's
layouts at its column. Every row is a ring left and right and every column
a ring up and down, in column and row order, as the title's own cells are
(rebuilt from the cells' columns and rows, this reproduces the title's
links exactly). A slot with no fighter has a random cell nothing moves to;
the grilles over the top row's ends are hidden where a cell sits.

**Its files.** The pack's files reach the title as entries added to data100
(`vfs::set_archive_entries`) under keys of its own code (the CRCs of
`<code>.xml` and so on, and of `<code>/parts/<kind>` for its part packs);
whatever the pack leaves out is copied from the donor, a TTT2 fighter the
pack names. A pack: `character.ini` (code, donor, sex, slot, chr_tones),
`<code>.xml/.nud/.nut/.bon` (item database, model, textures, skeleton),
`<code>.moveset/.motion`, optionally `<code>.mop/.havok.xml/.mvl`, its sound
banks, costume parts in `parts/<kind>/<index>` and its own pictures in
`art/`. The title's entries that several added fighters change (layouts, the
big portraits' container, the HUD's pictures, the colour palettes and grid,
the select screen's name calls) are made by the importer for all installed
packs together, each fighter at its number, in the folder `shared/keys/`
(`<key>.bin` a changed copy, `<key>.add` textures appended to a container; a
copy of a sound bank stays raw, in the original's archive). Each frame the
game skins the vertices of the models on screen (both fighters, some stages'
animals) into one of four stream-out buffers (80 bytes a vertex: position,
normal, tangents and a per-vertex state its skinning shader carries from
frame to frame), and in a match's first frame those of all four fighters,
which is the only time a tag partner's state is set up; a partner that does
not fit there draws black from its tag-in on. The buffers are sized for the
title's fighters (20,000-34,000 vertices, 7 MiB each), so the port triples
them (`sub_08977AE8`'s configuration), and the importer keeps an added fighter
under 42,000 vertices (leaving out 2P colour layers if it must; Noctis, whose
model holds all his summoned weapons and his trousers, 90,000: two of him and
two others fit) and its textures at 4 MB like the title's (the largest
halved). The effect files a match copies in (the common ones, the stage's and
each fighter's) share the resource manager's 20 MiB pool (`sub_08A7ED8C`'s
size) with textures that are loading and much else; two fighters with many
effects filled it and the match did not load, so with added fighters it is
twice the size. And the CPU's play (`sub_039A6214`) reads a table of 67
fighters by the player's motion row with no bound, past which added fighters
from place 5 on lie: the attract demo crashed on Lidia, so it gets the
donor's row meanwhile.

**Its voice.** A fighter has sound banks (raw NUS3BANKs in the sound
archives): shouts, damage voices, KO screams and the announcer's call of its
name (`se_Chr_<code>`), win quotes (`se_Dwi_<code>`), intro lines
(`rm_Dst_<code>`) and the long win quote one win pose streams
(`rm_Dwi_<code>`, found by key in a table of 63, `0x105C5180`, as the intro
lines are in two others; a fighter with none there streams from a file with
no name, which the title reports as an error). The first two are found by
bank ids (`sub_07A5CAC8`, `sub_07A5CAA4`) whose keys `sub_07A5D01C` gives.
The title has a bank object for each id below 264 and only four ids with no
bank (1, 122, 233, 253), so an added fighter has one bank (its shouts, then
its win quotes: `se_Chr_<code>` with `chr_tones` in `character.ini`), and a
fight load (`sub_079660FC`, asking each member's ids) binds one of the four
to it: an id is given to another fighter only when no fighter list holds it
and its bank object holds no bank (the title unloads a match's banks when it
ends), so a fighter always loads its own bank on a newly bound id, and a tag
match's four fighters fit. The sound library also refuses a bank whose
project id (in its `BINF` chunk) a loaded bank already has, so every added
fighter's banks get ids of their own (1000 + 4 x its number + the bank's
kind; the title's are 0-461). Moves do not name tones: a shout or damage voice
is a number in the fighter's voice list (`0x107D44EC`, by select id, copied
at start-up by `sub_0796F300`), which holds sound ids, and the sound table
(`sub_079E5B1C`, 8506 x {tone, bank id, group, flags}) gives each id its tone
and bank. A move's shout n (`2 << 24 | n`) is the n-th entry of category 5
(the entries whose 16-bit mask has a bit of `0x105DEFDC[5]`, 0x20;
`sub_0796F4C0` counts them, `sub_0796F218` finds the n-th; n past the count,
like the 999 most moves of both games carry, plays nothing), a damage voice
n the (count - 1 - n)-th of category 6, and both games number shouts alike
(short, medium, long, then the others), so the importer fills the donor's
tones by its voice list: shout n gets the Tekken 7 fighter's shout n, and
shouts past the donor's count are tones appended to the bank
(`character.ini` `voice_extra`), which the port adds to the fighter's voice
list (answering `sub_0796F4C0` and lending the list and its counts to
`sub_0796F218`). The added fighter's bank keeps the donor's layout, so the
port gives it the donor's voice list with its ids pointing at copies of the
donor's sound table entries (an extended table, new ids above the ones
`sub_079E5CD0` remaps) whose bank is the id bound to it and whose win quotes
come after its shouts. Win quotes are played by tone of the se_Dwi bank
(property 0x82f9, value 0x0E003000 | tone, `sub_07753DA0`: the port adds
`chr_tones`). The select screen's name calls are tones of the shared bank
se_Slt_StartChar (`sub_079E5D64(id, 1)`); the shared folder adds every
fighter's to a changed copy of it. Between matches the title releases its
fighters' cached bank files (`sub_07967F58`); the port releases the added
fighters' then too.

**Its pictures.** The 2D screens find a fighter's pictures by its UI
position, its row in tables of 59 (from the select id through `sub_06F0A484`
or from a record through `sub_06F0A4A4`): labels of per-fighter sprites in
the Lumen layouts of data007 (`face_<code>`, `chara_face_yoko_<code>`,
`p1_chara_waku_<code>`, whose animation ends on the fighter's name), keys of
portrait textures, frames of the big select portraits. An added fighter has
no row of its own, so the port gives it Paul's (0) and, while the code works
on it, Paul's row of each table holds that fighter's entries (label names,
keys, the big portraits' frames 118 + 2 x number); the functions that ask put
Paul's back when they return. The
select screen asks for both team members first and sets their pictures
after, so there each member's pictures follow its own question (told apart
by call sites), and moving between Paul and the added fighter resets the
member's cached position. In fights the HUD draws names and faces from its
texture container by numbers from a table by UI position (`0x1035AC88`), the
position from the record (`sub_033D555C`); the shared folder adds three textures per
fighter after the container's 259, and Paul's numbers become a fighter's while
the HUD draws it.

**Its costume.** The select screen's costume chooser lists a fighter's
costumes from rows by select id (`0x103B4B84`, read by `sub_0580D690`, 59
rows) with a picture each, from a texture container whose key is in another
table of 59 (`0x10161590`), then five customisation slots; the added
fighter's place in that table is past its end, in unrelated strings, and a
key nobody requested makes the chooser assert. The added fighter has one
costume: the list function reads its row (the donor's costume 1) in the
place of Paul's, its pictures (the pack's `art/costumes.nut`, else the
donor's) have their key lent to that place only while the chooser's
functions run, and the chooser leaves out the customisation slots (made per
fighter in a mode the added fighter is not in). Opening the chooser notes the
fighter under the cursor (`sub_02669380`, which asserts an id below 63): an
added fighter is noted as its donor.

**Its extra bones (Kazumi's tiger).** A skeleton's OPT list names extra
bones (`cha:OPT<number>_<name>`); the fighter's spring file (`<code>.mop`,
one 0x60-byte entry per bone number) swings them, and a bone in the list
without an entry hangs under gravity. Some fighters' extra bones are
animated: a move property (0x82de; 0x82df only in win poses, 0x82e0 under
another condition) starts an animation of part 3 of the motion pack on the
fighter's animator at player +7944 (value 0x4000 + index, frame at +16),
and per-fighter code posts the channels into the bones' records (the model's
bone container at +80, `sub_06A5C5FC` by bone number: a 144-byte record,
the local transform first). Both the animator's clock (`sub_05BDC714`) and
the posing (Devil Jin's wings: `sub_069D3184`, from the per-fighter
dispatcher `sub_069D8B30`) are written for a few character ids only, and
body animations' extra channels are taken by the same per-fighter code (at
most 64; more make it write to address 0). The pack's tiger is extra bones
600 on with a spring entry that keeps each still (Devil Jin's kind, flag 8),
hidden 100 m below the floor in the bind pose; for the added fighter the
port moves the clock of its part-3 animations and, after the dispatcher,
writes each bone's local transform from the animation's raw channels
(translation, rotation, scale per bone), the last frame putting the tiger
back below the floor.

**Its hair and tails (Lucky Chloe).** Tekken 7 simulates chains of bones
that its meshes model straight out from the body (her twin tails stand 1.3
m out sideways in the bind pose). The importer poses each chain hanging
(links turned to directions the fighter's definition gives, the vertices
with them) and makes its bones extra bones 300 on, each with a copy of
Lili's hair entry (flag 0: it swings) and Lili's collider round the neck in
the spring file; TTT2 then swings them as it swings Lili's hair.

**Its weapons (Negan's bat).** Tekken 7 draws a weapon as part of the
costume, skinned to an attachment bone under a hand (or the hips, the back),
and the moves pose that bone with the extra channels of their body
animations: after the 23 body channels, a pair (translation, rotation) per
weapon bone; without the pair the bone stays where Tekken 7's bind pose has
it. The pairs are TTT2's own kind: TTT2 poses Yoshimitsu's katanas (extra
bones under his hands) from such pairs, writing them into the bones'
records unchanged, and Tekken 7's item moves are TTT2's very animations. So a
pair is the weapon bone's local transform in TTT2's frame of its parent (their
resting values are Tekken 7's bind poses: Kunimitsu II's swords within 2
degrees and 1 mm; her kunai flies straight at the opponent); the bone's own
axes are Tekken 7's with y reversed, and the pairs come in Tekken 7's order
(Negan: the left bat's, then the right's). The importer makes each weapon an extra bone
(800 + the pair's number; still ones 820 on) bound where Tekken 7's bind pose
has it, skins the weapon's triangles to it and gives it still spring entries;
the points it hits with become extra bones under it. Every frame TTT2
samples each fighter's current animation with all its channels
(`sub_069DF470` -> `sub_069DCBD0` -> the sampler `sub_0689C42C`) and copies
the extra ones into the model (+13984; +13220 how many), where only some
fighters' code reads them; after the per-fighter dispatcher the port writes
them as the weapon bones' local transforms. (The sampling a move starts with,
`sub_06897540`, is once a move: pairs taken from it froze a weapon at its
animation's first frame, and Noctis's thrown sword never left his hand.) A
weapon always in hand ignores a pair over 1.5 m out: Tekken 7's spin
reactions carry 20 m paths in those channels. Hits:
TTT2's hit test (`sub_03C74F50`) takes a bone's position only for hit
locations 0..21 and keeps points per player for 27..77, those of a held
item at player +3416 (70..77, written only by items; above 77 a location
has no position at all), so the weapon's points hit as locations 70..73 and
the port writes their positions there every frame, made from the parent
bone's world matrix (the title keeps none for still extra bones). That
matrix (the hit test's skeleton) is a frame behind the animation, so the
points take the local drawn the frame before; with the current one, Noctis's
thrown sword (1 to 4 m from a hand swinging down) had its points fly over the
opponent's head while the sword drawn went through him.

**Summoned weapons (Noctis).** Noctis's mesh of weapons holds a copy of each
for each hand (a great sword, a sword, a dagger; a spear in the right, a
shield on the left forearm), and the pairs of a hand carry one pose, the
grip's; a move shows weapon n with property 0x82a7 (value n) until 0x82a8
(n, or 16: all; his stance ends them all), and its appearing and vanishing
particles come with 0x82af (n << 16 | effect id). The importer turns 0x82a7 /
0x82a8 into 0x822E / 0x822F (ids TTT2's property interpreter ignores and no
TTT2 moveset uses; the intro and win poses get those of Tekken 7's pose too),
the particles into the fighter's effects at the weapon's hand (their mesh
particles, the weapon's own models, are left out: the model draws them), and
character.ini lists a weapon's summon (`k:parent:points:summon`). The port
keeps each player's summoned weapons (a hook on the interpreter,
`sub_03AE86A0`) and collapses a weapon not summoned into its bone (axes 0).
Tekken 7 puts the weapons away when the fighter is hit (its game does, not
the moveset); the added fighter is hit, falls and gets up through the
donor's generic moves and its stance, so each of those dismisses them as it
starts (his own moves never pass through them with a weapon out).
His Warp-Strike throws the sword (its pair flies it up to 4.8 m) and warps to
it when it hits; Tekken 7 dismisses it as it hits, with particles of its mesh
lingering there (left out, as above), and summons it again for the warp. The
importer starts the move a hit leads to (the definition's `WEAPON_HOLD`) with
0x822B (another id TTT2 ignores), and the port keeps a thrown weapon (over
40 cm from its hand) where it was drawn last, drawn though dismissed (placed
with the hand's pose extrapolated a frame on), until
the warp's animation has it nearer the hand than that, a dismissal of all, or
two seconds.
His moves hit with a weapon's tip (Tekken 7's hitbox (tip, 0, tip, hand)) and
some with its guard: a hand's weapons share the four point slots (a point
`n>slot`), a slot taking the summoned weapon's point, else the first listed's.

**Saving Tekken 7's movesets** (`tekken7/autodump.py`, `tekken7/memdump.py`,
`tools/characters/t7helper/`): Tekken 7's data files are encrypted, and a
fighter's moveset is readable only once the game has loaded it, so the game
runs, unseen and untouched: Proton inside gamescope's headless backend, its
sound sent to a silent sink of its own (it does not start without an audio
device). A small Windows program built with MinGW runs in the game's prefix
(served over a localhost socket that wants a per-run secret: Proton's
launcher passes no stdin or stdout on). It asks Steam, as the game does,
which DLC the player owns (only owned fighters are dumped), and it calls
the game's own functions while the game sits on its start-up screens: the
loader that takes a fighter's three-letter code and has the game's
background tasks read its files (moveset, animations, command list; the
game decrypts them), then the two in-place conversions the game runs before
a fight, which turn the moveset's offsets and indices into pointers and the
command list's offsets into the form it keeps. The helper knows those six
functions (and the two below) and checks their first bytes before calling
any, so another build of the game is refused, not crashed; no address comes
from the connection.
Nothing is pressed and no screen is read, so key bindings, settings,
language and pop-ups play no part. The dumper then reads the result from
the game's memory: the moveset is one buffer (header with the aliases, 19
tables back to back, the strings), every link an address; the .tkmvst form
has row indices, string and animation offsets instead, and the command
list's offsets are turned back. The first 15 fighters and Heihachi load in
one run and match TKMovesets2's files exactly: every table, name, animation
and the command list (Geese's and Noctis's load the same way).

Some packages and sound banks Tekken 7 also keeps encrypted inside its paks
(Geese Howard's and Noctis's costumes, Noctis's voice bank: about 2,300
files, which then do not start with the package tag or BKHD); the game
decodes them in its pak file layer as it opens them. In the same run the
helper has the game read those files with Unreal's whole-file reader
(`FFileHelper::LoadFileToArray`, the call its asset registry uses), a batch
of paths at a time on one thread of its own (the pak layer keeps a reader
per thread), copies the buffers out and frees them with the game's
`FMemory::Free`; paths must be relative ones under the game's content. The
dumper saves them under their pak paths in `~/.local/share/ttt2/tekken7/files/`,
and the importer's pak reader takes a file from there whenever the pak's
copy is encrypted. No key is looked for or used: the game reads its own
files, as it does when it loads the fighter.

**The importer** (`tools/characters/`, Python with numpy and Pillow) reads
Tekken 7's pak files (version 3; every entry is flagged encrypted but
stored plain, but for the files above, read from the saved copies) and its
Unreal Engine 4.14 packages, the fighters' movesets
(.tkmvst, TKMovesets2's layout) and the installed TTT2 game. The model keeps the
donor's skeleton with the Tekken 7 joint positions (the face joints keep
the donor's offsets, as face animations set absolute positions), skins the
Tekken 7 meshes to it and bakes the costume's colours into BC1/BC3
textures of at most three mip levels. Tekken 7's skin shader brightens its
skin textures, which TTT2 draws as they are, so skin colours are scaled by
a gain measured at import time on the fighters both games have (each one's
TTT2 face texture against its Tekken 7 face, of the fighter's sex: the median
of the five nearest in brightness to the added fighter's face, as TTT2 evens
skin tones out); hair keeps Tekken 7's alpha cut-off rather than the
template's. Tekken 7 also lifts its cloth and hair with rim lights TTT2's
templates lack, which matters only for the darkest (Noctis's outfit and hair,
their colours 0.02-0.06 of the textures'): a definition can give a material
kind or a material a gain (`GAIN`, measured against a Tekken 7 screenshot
relative to the skin) and take a texture's brightness alone (`GREY`: his
outfit is blue-grey where its textures are mauve); his hair's lighter tips
come from a gradient texture its material samples beside its parameters. An
item that another item of the costume depends on (`DependencyItems`:
Noctis's trousers and boots, worn with his jacket) is worn too when the
definition says so (`DEPENDENCY_ITEMS`). A fighter's character effects are found by bare names its item
database lists, which the title expands to `<code>/alo/<name>.alo` with the
fighter's own code (each fighter's folder has its own copies of the effect
files and their textures): the importer copies the effect files an added
fighter uses (its donor's, and the closest of TTT2's own for its Tekken 7
effect ids) with their textures into the pack's `alo/` folder, which the
port serves under the fighter's code. Tekken 7 plays some moves' animations
as deltas over another (Akuma's forward and back jumps); TTT2 has no such
layers, so those moves become the donor's generic moves. Materials are classified by their Tekken 7 parent material
(skin, eyes, mouth, blended face layers, hair, cloth with or without holes),
a hair material's second pass (the same triangles) is left out, and a
definition lists only exceptions. The moveset keeps the donor's generic
and tag moves and converts the fighter's own: body animations are the same
in both games but for byte order; requirement and property ids, hitbox
codes and reactions are mapped through tables derived at import time by
aligning Heihachi's moveset in both games; projectiles (Kazumi's tiger
strikes) become TTT2 projectiles, which the same five properties spawn in
both games (limb, three offsets, the projectile); a summoned model (her
tiger, an actor of its own in Tekken 7) joins the skeleton as extra bones
with its meshes, its animations (decoded from Unreal's variable-key
compression) become part-3 animations and the moves that summon it play
them; mechanics TTT2 lacks (Rage
Arts and Drives, power crush armour) are left out, and Screw becomes Bound.
The donor's intros and win poses stay (their camera animations end the
intro and the win screen) and play the fighter's animations. The command
list is converted from Tekken 7's (both games keep an MVLT
file of names, commands in nine languages and demos given as pad inputs).
The voice is decoded from Tekken 7's Wwise banks (IMA ADPCM), re-encoded as
DSP ADPCM where the donor's banks use it, and laid out like the donor's
banks. The pictures: every layout with per-fighter labels gets the
fighter's beside Paul's (whole label animations copied), its Tekken 7
pictures scaled so that its face sits where the others' faces sit, cut to
the area theirs cover and laid in the frame and background they share; its
name is put together from letters cut out of the other fighters' name
pictures (the title's own lettering), with the shadow measured on them; the
costume chooser's picture is its Tekken 7 costume-slot picture cut out of its
grey panel and stood in the room the title's own pictures share.
Player 2's colours: TTT2 tints "colourable" cloth per player
(2 x grey x lerp(A, B, mask)) from two palette entries per material; the
fighter's cloth that changes between Tekken 7's 1P and 2P costumes gets an
alpha-tested colourable layer over the changing texels, with colours fitted
to both costumes and placed in grid cells no other fighter uses (the pack
carries the colour grid and both palettes). The pack holds Namco's assets:
the importer refuses to write it inside this repository, and `.gitignore`
keeps the games' file types out.

## Running the port

`build/ttt2 "<path>/TEKKEN TAG 2 Wii U EDITION (EU).wua"` reads the
executable and all assets from the archive in place (a directory with
code/, content/, meta/ also works). Reading through the archive costs
nothing measurable in play (`TTT2_TRACE_FS=1`): a fight reads ~0.5 MB of
music per 10 s (0.3 ms), loading a stage ~95 MB (0.5 s from a hard disk,
mostly seeks).

`ttt2 --install [GAME [FOLDER]] [--no-launcher]` (`install.cpp`) installs
the port like other programs: the game's `.wua` and the install folder
come from the desktop's file dialogs (SDL3, the XDG desktop portal; they
may open behind the active window, see the taskbar), from a terminal
prompt when there are none, or from the arguments. A chosen folder that
is empty, new or an earlier install is the install folder; any other gets
a "Tekken Tag Tournament 2" folder inside. The game is checked against
the RPX hash the port was generated from before anything is copied, and
against the free space. The install folder gets `game/` (code/,
content/, meta/; 15.9 GB, ~4 minutes from a hard disk), the executable
named "Tekken Tag Tournament 2" (what users see; `ttt2` stays the build's name),
`icon.png` (meta/iconTex.tga) and `.ttt2-install`, written last;
`$XDG_CONFIG_HOME/ttt2/installed` names the folder, and
`$XDG_DATA_HOME/applications/ttt2.desktop` puts it in the application menu
(the window's Wayland app id is `ttt2`, so it gets that entry's icon). Files
are copied through `<name>.part` and renamed when complete, so an
interrupted install resumes; files already there are kept, so a file
replaced for a mod survives installing again (deleting one restores it).
`ttt2` without arguments runs the game next to the executable or, failing
that, the recorded install; `ttt2 --update [FOLDER]` copies a rebuilt
executable into the install. Saves go to `$TTT2_SAVE_DIR`, default
`~/.local/share/ttt2/save`. The OS layer is in `runtime/src/os/`; each OS
function is plain C++ registered with `CAFE_EXPORT(module, name, fn)`, and
anything not implemented stops with the function's name and a guest
backtrace. Iterating on the runtime rebuilds and relinks in ~2 s.

The window shows the TV image (F11 toggles fullscreen, `TTT2_FULLSCREEN=1`
starts in it). Keyboard: arrows
D-pad, X/Z/S/A the A/B/X/Y buttons, Q/W L/R, 1/2 ZL/ZR, Enter +, Backspace
−, H Home, I/J/K/L the left stick; SDL gamepads map by button position.
`TTT2_GPU=null` runs headless without a window. Sound plays on the
default output device; `TTT2_AUDIO=0` disables it and
`TTT2_AUDIO_VOLUME=<percent>` scales it.

Unattended runs press buttons with `TTT2_INPUT_SCRIPT`. From a cold start
this reaches an arcade match (solo, Heihachi) at about 72 s; append
presses such as `,72:y,73:x,...` to fight:
`TTT2_INPUT_SCRIPT="8:plus,21:plus,24:plus,31:a,37:a,41:right,42:right,43:right,45:a,49:right,50:right,53:a,57:a"`.
Character select refuses a character already picked for the team. An item
`@<name hash>` waits until the title first reads that archive entry and
times after it count from then, presses before it that have not come yet
being dropped, so scripts do not drift with loading times (screens'
entries: `TTT2_TRACE_ENTRIES=1`).

A guest memory fault prints the guest registers and backtrace, says when
the address lies just below the stack pointer (a stack overflow), and
gives the host code offset, which `addr2line -f -e build/ttt2
<offset>` turns into the guest function. The main thread gets at least a
2 MB stack whatever the RPX asks for (TTT2's says 64 KB, but its menus
nest two functions with 501 KB frames).

Debugging: every guest function is a native function (`sub_XXXXXXXX_orig`),
so gdb breakpoints, watchpoints and backtraces work on guest code directly;
host threads are named `<OSThread address>/<core>`. The export thunks take
`(PPCContext&, uint8_t* base)`, so `$rsi` in any `imp_*` frame is the guest
base address. `TTT2_TRACE_THREADS=1` logs thread creation, priorities,
affinities and names. The GPU backends print frames per second and draws
per frame every ten seconds. The captures, dumps and traces below hold the
game's own images, shaders and sounds: write them under `local/`, which Git
ignores. Renderer debugging:

| Variable | Effect |
| --- | --- |
| `TTT2_CAPTURE=<dir>` | TV/GamePad images as PNG every `TTT2_CAPTURE_INTERVAL` seconds (default 5) |
| `TTT2_CAPTURE_SEQUENCE=<n>` | with `TTT2_CAPTURE`, also every image shown for the next n frames after each capture, in order (`seq_<capture>_<frame>_<n>.png`, the real image last) |
| `TTT2_TRACE_AT=<s>`, `TTT2_TRACE_FRAME=<n>` | log every command of one frame; with `TTT2_CAPTURE`, save all its render targets |
| `TTT2_INPUT_SCRIPT="25:plus,26.5:a"` | press GamePad buttons at given seconds, for unattended runs (`@<hash>` items wait for an archive entry) |
| `TTT2_TRACE_ENTRIES=1` | every archive entry the title reads, with times |
| `TTT2_TRACE_ROSTER=1` | the added fighter's entries |
| `TTT2_DUMP_SHADERS=<dir>` | shader binaries, draw registers and generated GLSL |
| `TTT2_DUMP_TEXTURES=<dir>` | every texture loaded from memory, as PNG |
| `TTT2_TRACE_TARGETS=1`, `TTT2_WATCH=<hex address>` | render target creation; writes and loads touching an address |
| `TTT2_CHECK_TILING=1` | compare fast detiling with addrlib per element |
| `TTT2_SCALE=1..4` | render scale of screen-sized targets (default from the display) |
| `TTT2_FPS=60/120/180/240` | frames shown per second (default 120 on displays of 100 Hz and more) |
| `TTT2_INTERP_TEST=1` | replays without blending: extra frames must equal real ones |
| `TTT2_INTERP_2D=1` | blend flat (not depth-tested) draws too |
| `TTT2_PRESENT_TIMED=1` | present at chosen times instead of at every refresh |
| `TTT2_PRESENT_QUEUE=1..4` | presents kept ahead of the screen when presenting every refresh (default about 8 ms of refreshes) |
| `TTT2_DISPLAY_HZ=<hz>` | report this refresh rate for the window's display (tests the rate check) |
| `TTT2_FULLSCREEN=1` | start fullscreen |
| `TTT2_TRACE_INTERP=1`, `TTT2_TRACE_PACING=1` | draw matching, carried targets; frame readiness, swap times after the vblank |
| `TTT2_PROFILE_AT=<s>` | GPU time of every operation of one frame |
| `TTT2_VK_VALIDATION=1` | Khronos validation layer, if installed (synchronization checks: `khronos_validation.validate_sync = true` in a file named by `VK_LAYER_SETTINGS_PATH`) |
| `TTT2_AUDIO_DUMP=<file.wav>` | everything played, as 48 kHz stereo WAV (also without a device) |
| `TTT2_TRACE_AX=1` | voice set-up, device mixes, output level |
| `TTT2_TRACE_FS=1` | file and save opens, reads and writes, with times |
| `TTT2_TRACE_TEXTURES=1` | every texture loaded from memory once, with the MD5 a replacement names |
| `TTT2_TRACE_PRESENTATION=1` | how long the icons, texts and logo took to prepare |
| `TTT2_CONTROLLER=playstation/nintendo/xbox` | icons and menu buttons for this kind of controller |

## Verification

- Per-instruction differential tests (`build/tests/semantics_test`, done):
  `cafe-semgen` samples up to 48 real encodings of every non-control-flow
  instruction variant in the game (6,753 encodings); each runs on 300 random
  machine states (edge integers, NaNs, infinities, denormals, random GQR
  formats) and must match Dolphin's Broadway interpreter bit for bit in every
  register and in memory. Dolphin is built from `third_party/ref` against a
  shim (`tests/oracle/`; its `Interpreter.h` and `PowerPC.h` are derived from
  the checkout at configure time) and is a test-only dependency: the test
  programs contain GPL-2.0-or-later code and are not distributed. Result 2026-10-01: 0
  mismatches. One oracle correction: Dolphin's `addme`/`subfme` carry is
  wrong when CA=1 (architecture and Cemu agree on CA=1); the test documents it.
  Branches, `lwarx`/`stwcx.`, `dcbz` and traps are not covered by it yet.
- Reference checkouts (sparse, read-only, never shipped; the README's Tests
  section has the commands for Dolphin's, a non-cone sparse checkout), at
  these revisions:
  Dolphin `771fb154059c5812d6a715a23226249cc42877a2` into
  `third_party/ref/dolphin` (Source/Core/Core/PowerPC/Interpreter,
  Gekko.h, PowerPC.h, ConditionRegister.*, Common/{BitField,BitUtils,
  CommonTypes,FloatUtils,Inline,Swap}); Cemu (MPL-2.0)
  `0a2b6ff7db61871b0dd028ac47dc72eda94351a1` into `third_party/ref/cemu`
  (src/Cafe/HW/Espresso/Interpreter, src/Cafe/HW/Latte/{ISA,Core,LatteAddrLib},
  src/Cafe/OS/libs/{coreinit,gx2,snd_core,dmae,swkbd,erreula,...});
  decaf-emu (GPL-3.0) `e6c528a20a41c34e0f9eb91dd3da40f119db2dee` into
  `third_party/ref/decaf` (src/libdecaf/src/cafe/libraries/{gx2,swkbd,erreula},
  src/libgpu/latte, src/libgpu/src). Where the two disagree, Cemu's
  behaviour was preferred because it runs this title.
- `build/tests/memory_model_test`: guest polling loops, compiled as the
  generator emits them with the port's flags, must wait for and then see
  another thread's store (fails against plain loads; passes).
- Per-function differential tests on the real image.
- Later: function-entry/exit traces from the game running in Cemu (test oracle
  only) compared with the port.
- Acceptance is behavioural: boots, menus, an offline match, saves, and long
  runs without divergence. A window opening is not acceptance.

## Milestones

| | Milestone | Exit criterion |
| --- | --- | --- |
| M0 | Loader, decoder, census | Done 2026-10-01 |
| M1 | Code generator for the full ISA | Done 2026-10-01: all 205,200 entries compile (`--all`, 14 min); semantics and control-flow tests pass with 0 mismatches |
| M2 | Runtime core, boot | Done 2026-10-01: the game runs its main loop at a steady 59.9 frames/s (vsync-paced) on the null GPU backend, every static import implemented |
| M3 | GX2 → Vulkan | Done 2026-10-01: intro movie, logos, title screen, attract-mode fights, main menu and character select render correctly at ~58 frames/s |
| M4 | Input, audio, filesystem, saves | Done 2026-10-02: menus and character select navigate, sound plays (checked from WAV dumps: content, levels, no buffer underruns), saves load and persist (the battle record survives a restart) |
| M5 | Gameplay | Reached with scripted input 2026-10-02: an arcade match from character select through both rounds, K.O., continue and game over, back to the menu with the record updated. Confirmed by hand 2026-10-05: a whole arcade mode played through |
| M6 | Hardening | All stages/characters, long runs, performance |
