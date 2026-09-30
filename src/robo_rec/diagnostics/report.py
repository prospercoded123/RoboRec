"""Builds and exports the full Diagnostics Report: fresh GPU probe + a live BTC/ETH/SOL
self-test (robo_rec.diagnostics.self_test) + recent recovery-run history
(robo_rec.recovery.history), bundled into one JSON file.

RoboRec recovers wallet seed phrases, so several of the things worth including here — target
addresses, the words a user typed in, a phrase that was actually recovered — are also exactly
the material someone would need to drain a real wallet. include_sensitive defaults to False
everywhere in this module for that reason: callers must opt in explicitly (a checkbox in the
GUI) before any of that leaves the process unredacted. Redaction happens here, at the export
boundary, not in robo_rec.recovery.history — that module stores everything unredacted so nothing
is lost before the user decides what they're comfortable exporting.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from robo_rec import __version__ as app_version
from robo_rec.diagnostics.findings import build_findings
from robo_rec.diagnostics.self_test import SelfTestResult, run_self_test
from robo_rec.gpu.report import GpuStatusReport, probe_gpu_status
from robo_rec.recovery.history import RunRecord, recent_runs
from robo_rec.util.build_info import get_build_info
from robo_rec.util.memory import system_memory
from robo_rec.util.paths import is_compiled

_REDACTED = "[REDACTED]"
_OMITTED = "[OMITTED]"

# --mnemonic and --mpk each take exactly one value; --addrs takes one-or-more, ending at the
# next "--"-prefixed flag. --tokenlist's value is a path to an already-deleted tempfile (see
# BtcrecoverRunner._cleanup_tokenlist) — not useful for debugging, masked unconditionally.
_SINGLE_VALUE_SENSITIVE_FLAGS = {"--mnemonic", "--mpk"}
_MULTI_VALUE_SENSITIVE_FLAGS = {"--addrs"}
_ALWAYS_OMITTED_FLAGS = {"--tokenlist"}


def _redact_argv(argv: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        out.append(token)
        i += 1
        if token in _SINGLE_VALUE_SENSITIVE_FLAGS:
            if i < len(argv):
                out.append(_REDACTED)
                i += 1
        elif token in _ALWAYS_OMITTED_FLAGS:
            if i < len(argv):
                out.append(_OMITTED)
                i += 1
        elif token in _MULTI_VALUE_SENSITIVE_FLAGS:
            while i < len(argv) and not argv[i].startswith("--"):
                out.append(_REDACTED)
                i += 1
    return out


def _sensitive_values_in_argv(argv: list[str]) -> list[str]:
    """Every literal value that would let someone reconstruct real wallet material: whole
    --mnemonic/--mpk/--addrs values AND their individual whitespace-split words, since a
    startup echo line elsewhere in stdout could print a known word on its own rather than
    the whole flag value."""
    values: list[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        i += 1
        if token in _SINGLE_VALUE_SENSITIVE_FLAGS and i < len(argv):
            values.append(argv[i])
            values.extend(w for w in argv[i].split() if w != "%%")
            i += 1
        elif token in _MULTI_VALUE_SENSITIVE_FLAGS:
            while i < len(argv) and not argv[i].startswith("--"):
                values.append(argv[i])
                i += 1
    return values


def _scrub(text: str, secrets: list[str]) -> str:
    # Longest-first: a whole-phrase secret (e.g. the full recovered mnemonic) must be
    # matched and replaced before any of its individual words are — otherwise the
    # word-level pass fragments the phrase first and the whole-phrase match never fires
    # again, silently leaving any word NOT in the word-level list (e.g. a recovered word
    # that filled a blank, which was never a "known word" to begin with) unredacted.
    # Confirmed by direct testing: a recovered blank-position word leaked through with the
    # naive unsorted order.
    for secret in sorted({s for s in secrets if s}, key=len, reverse=True):
        text = text.replace(secret, _REDACTED)
    return text


def _timestamped_log(lines: list[str], offsets: list[float]) -> list[dict[str, Any]]:
    """Each engine line with the seconds since launch at which it arrived (None for records
    that predate offsets), so silences are visible rather than inferred from total duration."""
    return [
        {"t": offsets[i] if i < len(offsets) else None, "line": line}
        for i, line in enumerate(lines)
    ]


def _gb(num_bytes: int) -> float:
    return round(num_bytes / 2**30, 3)


def _run_record_to_dict(run: RunRecord, *, include_sensitive: bool) -> dict[str, Any]:
    base = {
        "timestamp": run.timestamp.isoformat(),
        "scenario": run.scenario,
        "wallet_type": run.wallet_type,
        "gpu_requested": run.gpu_requested,
        "gpu_actually_used": run.gpu_actually_used,
        "duration_seconds": round(run.duration_seconds, 2),
        "return_code": run.return_code,
        "succeeded": run.succeeded,
        "cancelled": run.cancelled,
        "matched_path": run.matched_path,
        "launch_error": run.launch_error,
        "outcome": run.outcome,
        "peak_memory_gb": _gb(run.peak_memory_bytes) if run.peak_memory_bytes else None,
        # (seconds since launch, GB) of the whole seedrecover process tree's commit charge
        "memory_samples_gb": [[t, _gb(b)] for t, b in run.memory_samples],
    }
    if include_sensitive:
        return {
            **base,
            "error": run.error,
            "argv": run.argv,
            "recovered_mnemonic": run.recovered_mnemonic,
            "matched_address": run.matched_address,
            "log": _timestamped_log(run.log_lines, run.log_offsets),
        }

    secrets = [
        *_sensitive_values_in_argv(run.argv),
        *([run.recovered_mnemonic] if run.recovered_mnemonic else []),
        *([run.matched_address] if run.matched_address else []),
    ]
    return {
        **base,
        "argv": _redact_argv(run.argv),
        "recovered_mnemonic": _REDACTED if run.recovered_mnemonic else None,
        "matched_address": _REDACTED if run.matched_address else None,
        "error": _scrub(run.error, secrets) if run.error else None,
        "log": _timestamped_log([_scrub(line, secrets) for line in run.log_lines], run.log_offsets),
    }


def _self_test_to_dict(result: SelfTestResult, *, include_sensitive: bool) -> dict[str, Any]:
    base = {
        "coin": result.coin,
        "passed": result.passed,
        "elapsed_seconds": round(result.elapsed_seconds, 2),
        "gpu_requested": result.gpu_requested,
        "gpu_actually_used": result.gpu_actually_used,
        "error": result.error,
    }
    if include_sensitive:
        return {
            **base,
            "mnemonic": result.mnemonic,
            "address": result.address,
            "recovered_mnemonic": result.recovered_mnemonic,
        }
    return {
        **base,
        "mnemonic": _REDACTED if result.mnemonic else None,
        "address": _REDACTED if result.address else None,
        "recovered_mnemonic": _REDACTED if result.recovered_mnemonic else None,
    }


def _app_info(memory: dict[str, int] | None) -> dict[str, Any]:
    return {
        "app_version": app_version,
        "running_compiled_build": is_compiled(),
        "build": get_build_info(),
        "python_version": sys.version,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "logical_cores": os.cpu_count(),
        # Machine totals as of this export. commit_limit (RAM + page file) is the ceiling that
        # an out-of-memory crash actually hits on Windows; available_* are current, not at run time.
        "memory_gb": {k.removesuffix("_bytes"): _gb(v) for k, v in memory.items()}
        if memory
        else None,
    }


def _gpu_report_to_dict(report: GpuStatusReport) -> dict[str, Any]:
    payload = asdict(report)
    payload["generated_at"] = report.generated_at.isoformat()
    return payload


def build_diagnostics_report(*, use_gpu_for_self_test: bool, include_sensitive: bool) -> dict[str, Any]:
    """Gathers everything into one dict, ready for json.dump. Always re-runs the GPU probe
    (cheap unless the correctness verdict isn't cached yet) and the full BTC/ETH/SOL
    self-test (~30-90s per coin) — this is meant to be run right before handing the report to
    someone for debugging, not on a hot path, so it deliberately favors completeness over
    speed."""
    gpu_report = probe_gpu_status()
    self_test_results = run_self_test(use_gpu=use_gpu_for_self_test, keep_sensitive=include_sensitive)
    memory = system_memory()
    runs = recent_runs()

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "include_sensitive": include_sensitive,
        # Read this first: one-line conclusions per abnormal run, errors before warnings.
        "findings": build_findings(runs, self_test_results, memory),
        "app": _app_info(memory),
        "gpu": _gpu_report_to_dict(gpu_report),
        "self_test": [
            _self_test_to_dict(r, include_sensitive=include_sensitive) for r in self_test_results
        ],
        "recent_recovery_runs": [
            _run_record_to_dict(r, include_sensitive=include_sensitive) for r in runs
        ],
    }


def export_diagnostics_report(path: Path, *, use_gpu_for_self_test: bool, include_sensitive: bool) -> None:
    report = build_diagnostics_report(
        use_gpu_for_self_test=use_gpu_for_self_test, include_sensitive=include_sensitive
    )
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")


__all__ = ["build_diagnostics_report", "export_diagnostics_report"]
