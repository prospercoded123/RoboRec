"""Real-process checks for the ctypes memory helpers (Windows only — the app is Windows-only)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from robo_rec.util.memory import MemorySampler, process_tree_commit_bytes, system_memory

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only memory APIs")

_ALLOCATE_MB = 150
# Parent spawns a grandchild that holds the allocation, so only a tree-wide measurement sees it.
_GRANDCHILD = (
    "import subprocess, sys, time;"
    f"g = subprocess.Popen([sys.executable, '-c', "
    f"'b = bytearray({_ALLOCATE_MB} * 1024 * 1024); b[::4096] = bytes(len(b[::4096])); "
    "import time; time.sleep(3)']);"
    "time.sleep(4)"
)


def test_system_memory_reports_plausible_totals():
    memory = system_memory()
    assert memory is not None
    assert memory["total_ram_bytes"] > memory["available_ram_bytes"] > 0
    assert memory["commit_limit_bytes"] >= memory["total_ram_bytes"]


def test_sampler_sees_memory_held_by_a_grandchild_process():
    process = subprocess.Popen([sys.executable, "-c", _GRANDCHILD])
    sampler = MemorySampler(interval_seconds=0.3)
    sampler.start(process.pid)
    process.wait()
    sampler.stop()

    assert sampler.peak_bytes is not None
    assert sampler.peak_bytes > _ALLOCATE_MB * 2**20
    assert len(sampler.samples) >= 3
    times = [t for t, _ in sampler.samples]
    assert times == sorted(times)


def test_unmeasurable_pid_returns_none_not_zero():
    assert process_tree_commit_bytes(0x7FFFFFF0) is None


def test_sample_list_stays_bounded_and_keeps_its_endpoints(monkeypatch):
    from robo_rec.util import memory

    readings = iter(range(1, 10_000))
    monkeypatch.setattr(memory, "process_tree_commit_bytes", lambda pid: next(readings))
    sampler = MemorySampler()
    for _ in range(1000):
        sampler._sample(pid=1)

    assert len(sampler.samples) <= 240
    assert sampler.samples[0][1] == 1  # the first reading survives every thinning pass
    assert sampler.samples[-1][1] == 1000  # and so does the latest
    assert sampler.peak_bytes == 1000
