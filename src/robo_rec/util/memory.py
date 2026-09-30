"""Memory measurement for the Diagnostics export, with no third-party dependency (psutil would
be one more package to bundle into an already fragile Nuitka build).

Why this exists: a 3-blank search crashed seedrecover with a raw Nuitka segfault after ~23
minutes and the diagnostics could not say whether memory was the cause — it recorded nothing
about memory at all. These helpers let every recovery run record how much memory its whole
process tree used, and let the report state how much the machine has.

Commit charge (PagefileUsage) is sampled rather than working set on purpose: a process that is
thrashing has its working set *shrink* as Windows pages it out, while its commit keeps growing —
working set would hide exactly the failure this is meant to expose.

Windows only (the app is Windows-only); everything returns None elsewhere or on any API failure,
because diagnostics must never be able to break a recovery run.
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass, field

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    _PROCESS_QUERY_INFORMATION = 0x0400
    _PROCESS_VM_READ = 0x0010
    _TH32CS_SNAPPROCESS = 0x00000002
    _INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    _kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MEMORYSTATUSEX)]
    _kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.K32GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_PROCESS_MEMORY_COUNTERS),
        wintypes.DWORD,
    ]
    _kernel32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    _kernel32.Process32FirstW.restype = wintypes.BOOL
    _kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    _kernel32.Process32NextW.restype = wintypes.BOOL


def system_memory() -> dict[str, int] | None:
    """Machine-wide totals in bytes, or None if unavailable. `commit_limit_bytes` (RAM plus
    page file) is the ceiling an out-of-memory failure actually hits on Windows."""
    if not _IS_WINDOWS:
        return None
    try:
        status = _MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if not _kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return {
            "total_ram_bytes": int(status.ullTotalPhys),
            "available_ram_bytes": int(status.ullAvailPhys),
            "commit_limit_bytes": int(status.ullTotalPageFile),
            "commit_available_bytes": int(status.ullAvailPageFile),
        }
    except Exception:  # noqa: BLE001 - diagnostics must never break the caller
        return None


def _process_commit_bytes(pid: int) -> int | None:
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ, False, pid)
    if not handle:
        return None
    try:
        counters = _PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS)
        if not _kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return int(counters.PagefileUsage)
    finally:
        _kernel32.CloseHandle(handle)


def _child_pid_map() -> dict[int, list[int]]:
    """parent pid -> child pids, from a single process snapshot."""
    children: dict[int, list[int]] = {}
    snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == _INVALID_HANDLE_VALUE:
        return children
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        more = _kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            children.setdefault(int(entry.th32ParentProcessID), []).append(
                int(entry.th32ProcessID)
            )
            more = _kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snapshot)
    return children


def process_tree_commit_bytes(root_pid: int) -> int | None:
    """Commit charge of `root_pid` plus every descendant, in bytes (None if unmeasurable).

    The whole tree matters: seedrecover spawns one worker process per thread, each holding its
    own copy of the wallet state, so the root alone understates real usage once the search is
    running. The root alone is the right number only during the initial counting pass."""
    if not _IS_WINDOWS:
        return None
    try:
        children = _child_pid_map()
        total = 0
        measured = False
        stack = [root_pid]
        seen: set[int] = set()
        while stack:
            pid = stack.pop()
            if pid in seen:
                continue
            seen.add(pid)
            stack.extend(children.get(pid, ()))
            usage = _process_commit_bytes(pid)
            if usage is not None:
                total += usage
                measured = True
        return total if measured else None
    except Exception:  # noqa: BLE001 - diagnostics must never break the caller
        return None


_MAX_SAMPLES = 240


@dataclass
class MemorySampler:
    """Polls a process tree's commit charge on a background thread for the life of one run.

    Samples are (seconds_since_start, bytes). When the list would exceed _MAX_SAMPLES every
    other sample is dropped, so a multi-day run still yields a bounded, evenly thinned curve."""

    interval_seconds: float = 2.0
    peak_bytes: int | None = None
    samples: list[tuple[float, int]] = field(default_factory=list)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _start: float = 0.0

    def start(self, pid: int) -> None:
        if not _IS_WINDOWS:
            return
        self._start = time.monotonic()
        self._thread = threading.Thread(
            target=self._run, args=(pid,), name="robo-rec-memory-sampler", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Ends sampling and waits briefly for the thread; peak_bytes/samples are then final."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def _sample(self, pid: int) -> None:
        usage = process_tree_commit_bytes(pid)
        if usage is None:
            return
        if self.peak_bytes is None or usage > self.peak_bytes:
            self.peak_bytes = usage
        self.samples.append((round(time.monotonic() - self._start, 1), usage))
        if len(self.samples) > _MAX_SAMPLES:
            del self.samples[1:-1:2]

    def _run(self, pid: int) -> None:
        self._sample(pid)
        while not self._stop.wait(self.interval_seconds):
            self._sample(pid)


__all__ = ["MemorySampler", "process_tree_commit_bytes", "system_memory"]
