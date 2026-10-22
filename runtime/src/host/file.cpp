#include "host/file.h"

#include <cerrno>

#ifdef _WIN32
#include <windows.h>
#include <fcntl.h>
#include <io.h>
#include <sys/stat.h>
#else
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace cafe::host {

#ifdef _WIN32

namespace {

void errno_from_last_error() {
    switch (GetLastError()) {
    case ERROR_FILE_NOT_FOUND: case ERROR_PATH_NOT_FOUND: errno = ENOENT; break;
    case ERROR_ACCESS_DENIED: case ERROR_SHARING_VIOLATION: case ERROR_LOCK_VIOLATION: errno = EACCES; break;
    case ERROR_DISK_FULL: case ERROR_HANDLE_DISK_FULL: errno = ENOSPC; break;
    case ERROR_TOO_MANY_OPEN_FILES: errno = EMFILE; break;
    default: errno = EIO; break;
    }
}

// ReadFile and WriteFile at an offset, which Windows allows several threads
// to do at once on one handle (the handle's own position is not used).
int64_t transfer(int fd, void* buffer, size_t size, uint64_t offset, bool write) {
    const HANDLE handle = reinterpret_cast<HANDLE>(_get_osfhandle(fd));
    if (handle == INVALID_HANDLE_VALUE) {
        errno = EBADF;
        return -1;
    }
    OVERLAPPED overlapped{};
    overlapped.Offset = static_cast<DWORD>(offset);
    overlapped.OffsetHigh = static_cast<DWORD>(offset >> 32);
    const DWORD want = static_cast<DWORD>(size > 0x40000000u ? 0x40000000u : size);
    DWORD done = 0;
    const BOOL ok = write ? WriteFile(handle, buffer, want, &done, &overlapped)
                          : ReadFile(handle, buffer, want, &done, &overlapped);
    if (!ok) {
        if (!write && GetLastError() == ERROR_HANDLE_EOF) return 0;
        errno_from_last_error();
        return -1;
    }
    return done;
}

} // namespace

int open_file(const std::filesystem::path& path, int flags) {
    int mode = _O_BINARY | _O_NOINHERIT;
    if ((flags & kOpenRead) && (flags & kOpenWrite)) mode |= _O_RDWR;
    else if (flags & kOpenWrite) mode |= _O_WRONLY;
    else mode |= _O_RDONLY;
    if (flags & kOpenCreate) mode |= _O_CREAT;
    if (flags & kOpenTruncate) mode |= _O_TRUNC;
    if (flags & kOpenExclusive) mode |= _O_CREAT | _O_EXCL;
    return _wopen(path.c_str(), mode, _S_IREAD | _S_IWRITE);
}

void close_file(int fd) { _close(fd); }

int64_t pread_file(int fd, void* buffer, size_t size, uint64_t offset) {
    return transfer(fd, buffer, size, offset, false);
}

int64_t pwrite_file(int fd, const void* buffer, size_t size, uint64_t offset) {
    return transfer(fd, const_cast<void*>(buffer), size, offset, true);
}

int64_t write_file(int fd, const void* buffer, size_t size) {
    const int n = _write(fd, buffer, static_cast<unsigned>(size > 0x40000000u ? 0x40000000u : size));
    return n;
}

uint64_t file_size(int fd) {
    struct _stat64 st;
    if (_fstat64(fd, &st) != 0) return 0;
    return static_cast<uint64_t>(st.st_size);
}

bool flush_file(int fd) {
    if (_commit(fd) == 0) return true;
    return false;
}

bool is_terminal(FILE* stream) { return _isatty(_fileno(stream)) != 0; }

#else

int open_file(const std::filesystem::path& path, int flags) {
    int mode = O_CLOEXEC;
    if ((flags & kOpenRead) && (flags & kOpenWrite)) mode |= O_RDWR;
    else if (flags & kOpenWrite) mode |= O_WRONLY;
    else mode |= O_RDONLY;
    if (flags & kOpenCreate) mode |= O_CREAT;
    if (flags & kOpenTruncate) mode |= O_TRUNC;
    if (flags & kOpenExclusive) mode |= O_CREAT | O_EXCL;
    return ::open(path.c_str(), mode, 0644);
}

void close_file(int fd) { ::close(fd); }

int64_t pread_file(int fd, void* buffer, size_t size, uint64_t offset) {
    for (;;) {
        const ssize_t n = ::pread(fd, buffer, size, static_cast<off_t>(offset));
        if (n < 0 && errno == EINTR) continue;
        return n;
    }
}

int64_t pwrite_file(int fd, const void* buffer, size_t size, uint64_t offset) {
    for (;;) {
        const ssize_t n = ::pwrite(fd, buffer, size, static_cast<off_t>(offset));
        if (n < 0 && errno == EINTR) continue;
        return n;
    }
}

int64_t write_file(int fd, const void* buffer, size_t size) {
    for (;;) {
        const ssize_t n = ::write(fd, buffer, size);
        if (n < 0 && errno == EINTR) continue;
        return n;
    }
}

uint64_t file_size(int fd) {
    struct stat st;
    if (::fstat(fd, &st) != 0) return 0;
    return static_cast<uint64_t>(st.st_size);
}

bool flush_file(int fd) { return ::fdatasync(fd) == 0; }

bool is_terminal(FILE* stream) { return ::isatty(::fileno(stream)) != 0; }

#endif

} // namespace cafe::host
