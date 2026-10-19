"""Process / system memory helpers without psutil (not installed in the project venv).

peak_rss()      -> peak resident set (Windows: PeakWorkingSetSize; POSIX: ru_maxrss) of THIS process, bytes.
                   NOTE: includes touched pages of memory-mapped files (shared page cache, reclaimable).
peak_private()  -> peak private commit (Windows: PeakPagefileUsage), i.e. memory NOT shareable with other processes;
                   POSIX: falls back to peak_rss()
rss()           -> current resident set, bytes
ram_total(), ram_available() -> system physical memory, bytes
"""
from __future__ import annotations

import os
import sys

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    class _MSX(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)
    _k32.GetCurrentProcess.restype = wintypes.HANDLE
    _psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]

    def _pmc() -> _PMC:
        c = _PMC(); c.cb = ctypes.sizeof(_PMC)
        _psapi.GetProcessMemoryInfo(_k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
        return c

    def peak_rss() -> int:
        return int(_pmc().PeakWorkingSetSize)

    def rss() -> int:
        return int(_pmc().WorkingSetSize)

    def peak_private() -> int:
        return int(_pmc().PeakPagefileUsage)

    def _msx() -> _MSX:
        m = _MSX(); m.dwLength = ctypes.sizeof(_MSX)
        _k32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m

    def ram_total() -> int:
        return int(_msx().ullTotalPhys)

    def ram_available() -> int:
        return int(_msx().ullAvailPhys)
else:  # pragma: no cover - exercised on Linux/macOS only (e.g. the atlas Pi)
    import resource

    def peak_rss() -> int:
        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(r if sys.platform == "darwin" else r * 1024)

    def peak_private() -> int:
        return peak_rss()

    def rss() -> int:
        try:
            with open("/proc/self/statm") as f:
                return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        except OSError:
            return peak_rss()

    def ram_total() -> int:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))

    def ram_available() -> int:
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) * 1024
        except OSError:
            pass
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES"))
