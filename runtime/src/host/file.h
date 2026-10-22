#pragma once

// Files by descriptor, the same on every host: positional reads and writes
// that several threads may issue at once on one descriptor, and the
// details each host wants (close-on-exec, binary mode). Errors leave the
// reason in errno, with the POSIX codes vfs.cpp maps to the title's.

#include <cstdint>
#include <cstdio>
#include <filesystem>

namespace cafe::host {

enum OpenFlags {
    kOpenRead = 1,
    kOpenWrite = 2,
    kOpenCreate = 4,    // the file may be new
    kOpenTruncate = 8,  // an existing file is emptied
    kOpenExclusive = 16 // the file must be new
};

// A descriptor, or -1 (errno).
int open_file(const std::filesystem::path& path, int flags);
void close_file(int fd);
// Bytes transferred; 0 at the end of the file, -1 on error (errno).
int64_t pread_file(int fd, void* buffer, size_t size, uint64_t offset);
int64_t pwrite_file(int fd, const void* buffer, size_t size, uint64_t offset);
// A sequential write at the descriptor's position.
int64_t write_file(int fd, const void* buffer, size_t size);
uint64_t file_size(int fd);
// The file's data to the disk (fdatasync); false on error (errno).
bool flush_file(int fd);

// Whether a standard stream is a terminal.
bool is_terminal(FILE* stream);

} // namespace cafe::host
