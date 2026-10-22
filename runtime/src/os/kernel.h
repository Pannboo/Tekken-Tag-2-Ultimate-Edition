#pragma once

// Internal model of Cafe OS threads and synchronisation.
//
// Every guest thread runs on its own host thread. One kernel lock guards all
// OS state; a blocked thread waits on the shared condition variable and
// re-checks its own condition when woken, so wake-ups never get lost. Guest
// structures keep the state games can observe (owner, counts, flags); host
// structures keep what only the runtime needs.

#include "cafe/guest.h"
#include "cafe/ppc_context.h"

#include <chrono>
#include <condition_variable>
#include <csetjmp>
#include <cstdint>
#include <ctime>
#include <mutex>
#include <string>

namespace cafe::os {

// OSThread field offsets (structure is 0x6A0 bytes, owned by the game).
namespace osthread {
constexpr uint32_t kSize = 0x6A0;
constexpr uint32_t kGqr = 0x1BC;
constexpr uint32_t kUpir = 0x1DC;
constexpr uint32_t kErrno = 0x300;
constexpr uint32_t kMagic = 0x320;
constexpr uint32_t kState = 0x324;
constexpr uint32_t kAttr = 0x325;
constexpr uint32_t kId = 0x326;
constexpr uint32_t kSuspend = 0x328;
constexpr uint32_t kEffectivePriority = 0x32C;
constexpr uint32_t kBasePriority = 0x330;
constexpr uint32_t kExitValue = 0x334;
constexpr uint32_t kStackBase = 0x394; // upper end
constexpr uint32_t kStackEnd = 0x398;  // lower end
constexpr uint32_t kEntry = 0x39C;
constexpr uint32_t kCrt = 0x3A0;
constexpr uint32_t kSpecific = 0x57C; // 16 slots
constexpr uint32_t kType = 0x5BC;
constexpr uint32_t kName = 0x5C0;
constexpr uint32_t kCleanup = 0x5CC;
constexpr uint32_t kDeallocator = 0x5D0;
constexpr uint32_t kMagicValue = 0x74487244; // "tHrD"
enum State : uint8_t { kNone = 0, kReady = 1, kRunning = 2, kWaiting = 4, kMoribund = 8 };
enum Attr : uint8_t { kAffinityMask = 7, kDetached = 8 };
} // namespace osthread

template <typename T>
be<T>& field(uint32_t object, uint32_t offset) {
    return *guest<be<T>>(object + offset);
}

// A host thread (pthread or Win32), started by start_host_thread.
struct HostThread {
#ifdef _WIN32
    void* handle = nullptr;
#else
    unsigned long id = 0; // pthread_t
#endif
};
// Runs `fn(argument)` on a new host thread with `stack_size` bytes of stack;
// a detached thread's handle is released at once, else join_host_thread
// waits for it. False if no thread could be made.
bool start_host_thread(HostThread& thread, void* (*fn)(void*), void* argument, size_t stack_size,
                       bool detached);
void join_host_thread(HostThread& thread);
// Names the calling host thread for debuggers and profilers.
void name_host_thread(const char* name);

struct Thread {
    uint32_t guest = 0; // OSThread
    PPCContext ctx{};
    HostThread host{};
    bool host_started = false;
    bool finished = false; // entry returned or OSExitThread ran
    bool interrupts_disabled = false;
    int core = 1;
    std::jmp_buf exit_jump{};
};

// The kernel lock, held while any kernel object is examined or changed.
std::mutex& kernel_mutex();
using KernelLock = std::unique_lock<std::mutex>;

Thread* current_thread();
// A guest thread for runtime work (alarms, deallocators): OSThread and stack
// in system memory, never visible to the title's thread functions.
Thread* create_internal_thread(const char* name, int core);
// Makes `t` the current guest thread of the calling host thread.
void bind_current_thread(Thread* t);
Thread* thread_for(uint32_t guest_thread); // null for unknown structures

// Wait channels: a blocked thread sleeps on the channel of the object it
// waits for, keyed by the object's guest address (or one of the runtime
// keys below), and waking an object notifies only its channel. Keys share
// channels by hash, so a woken thread always re-checks its condition.
enum WaitKey : uint32_t {
    kWaitInterrupts = 1, // the interrupt lock
    kWaitDisplay = 2,    // vsync and flip counters
    kWaitGpu = 3,        // retired timestamps
    kWaitAlarms = 4,     // the alarm schedule
    kWaitNothing = 5,    // timed sleeps nothing ends early
    kWaitDeferred = 6,   // work for the system thread
};
std::condition_variable& wait_channel(uint32_t key);
void wake(uint32_t key);

// Blocks the calling thread until `ready()` holds; `key` names what it
// waits for. Gives up the interrupt lock while blocked, as a context switch
// would on hardware.
template <typename Predicate>
void wait_until(KernelLock& lock, uint32_t key, Predicate ready);
// Same, with a timeout in nanoseconds; returns whether `ready()` held.
template <typename Predicate>
bool wait_until_for(KernelLock& lock, uint32_t key, uint64_t nanoseconds, Predicate ready);

// OSDisableInterrupts as a global critical section (see sync.cpp).
void acquire_interrupt_lock(KernelLock& lock, Thread* self);
void release_interrupt_lock_for_wait(Thread* self, bool& was_held);
void reacquire_interrupt_lock_after_wait(KernelLock& lock, Thread* self, bool was_held);

// OSTicksToCalendarTime into the OSCalendarTime at `calendar`.
void ticks_to_calendar_time(int64_t ticks, uint32_t calendar);
// The local time zone's offset from UTC at `when`, in seconds.
int64_t utc_offset_seconds(time_t when);

// The default heap, allocated through the MEMAllocFromDefaultHeapEx /
// MEMFreeToDefaultHeap pointers the title may have replaced.
uint32_t default_heap_alloc(PPCContext& ctx, uint32_t size, uint32_t alignment);
void default_heap_free(PPCContext& ctx, uint32_t block);

// Starts the game's main thread running `entry` and waits for the process to
// exit; returns the exit code.
int run_main_thread(uint32_t entry, uint32_t argc, uint32_t argv, uint32_t stack_size,
                    uint32_t sda_base, uint32_t sda2_base);

// ---- implementation of the wait templates
template <typename Predicate>
void wait_until(KernelLock& lock, uint32_t key, Predicate ready) {
    Thread* self = current_thread();
    if (ready()) return;
    bool held = false;
    release_interrupt_lock_for_wait(self, held);
    wait_channel(key).wait(lock, ready);
    reacquire_interrupt_lock_after_wait(lock, self, held);
}

template <typename Predicate>
bool wait_until_for(KernelLock& lock, uint32_t key, uint64_t nanoseconds, Predicate ready) {
    Thread* self = current_thread();
    if (ready()) return true;
    bool held = false;
    release_interrupt_lock_for_wait(self, held);
    const bool result =
        wait_channel(key).wait_for(lock, std::chrono::nanoseconds(nanoseconds), ready);
    reacquire_interrupt_lock_after_wait(lock, self, held);
    return result;
}

} // namespace cafe::os
