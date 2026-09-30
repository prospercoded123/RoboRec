"""Process-wide, in-memory record of recent BtcrecoverRunner invocations.

Exists purely to feed the Diagnostics export (robo_rec.diagnostics.report): when a search
fails unexpectedly, the exported report should be able to show *what was actually run* (which
scenario, which flags, how long it took, what the engine printed) without the user having to
manually transcribe any of it. Not a persistent log — cleared on process exit, capped in size
so a long session doesn't grow this without bound.

Every field here can contain wallet-recovery material (seed words, addresses, a recovered
mnemonic) — this module stores it unredacted; robo_rec.diagnostics.report.redact_run_record()
is what strips it for anyone who doesn't opt in to an unredacted export. Keep it that way:
redaction belongs at the export boundary, not baked into what gets recorded.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime

_MAX_RECORDS = 10
_MAX_LOG_LINES = 500

_lock = threading.Lock()
_records: list[RunRecord] = []


@dataclass(frozen=True)
class RunRecord:
    timestamp: datetime
    scenario: str  # RecoverySpec subclass name, e.g. "MissingWordKnownPositionSpec"
    wallet_type: str
    gpu_requested: bool
    gpu_actually_used: bool  # "--enable-opencl" made it into argv (see recovery.args)
    argv: list[str]
    duration_seconds: float
    return_code: int | None
    succeeded: bool
    cancelled: bool
    recovered_mnemonic: str | None
    matched_address: str | None
    matched_path: str | None
    log_lines: list[str] = field(default_factory=list)
    launch_error: str | None = None
    # What actually happened, decided by the runner: "found" | "not_found" | "cancelled" |
    # "error". succeeded=False alone is ambiguous — a crash and a fully exhausted search both
    # have it — which is exactly how a segfault got read as "no match" for a whole support cycle.
    outcome: str = "unknown"
    error: str | None = None
    # Seconds since the process started, parallel to (and capped the same as) log_lines. Lets a
    # reader see "silent for 23 minutes after this line" instead of inferring it from duration.
    log_offsets: list[float] = field(default_factory=list)
    # Commit charge of the whole seedrecover process tree (see robo_rec.util.memory). None when
    # it couldn't be measured — never 0, so "unknown" and "tiny" stay distinguishable.
    peak_memory_bytes: int | None = None
    memory_samples: list[tuple[float, int]] = field(default_factory=list)


def record(run: RunRecord) -> None:
    with _lock:
        _records.append(run)
        del _records[:-_MAX_RECORDS]


def recent_runs() -> list[RunRecord]:
    with _lock:
        return list(_records)


def clear() -> None:
    """Test-only escape hatch — production code never needs to clear this."""
    with _lock:
        _records.clear()


def cap_log_lines(lines: list) -> list:
    """Keeps the most recent _MAX_LOG_LINES — early phase-transition lines matter less than
    what was happening right before the process exited. Also used for log_offsets so the two
    lists stay index-aligned after capping."""
    return lines[-_MAX_LOG_LINES:]


__all__ = ["RunRecord", "cap_log_lines", "clear", "record", "recent_runs"]
