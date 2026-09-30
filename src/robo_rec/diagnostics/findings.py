"""Turns the raw facts in a Diagnostics export into plain-language conclusions.

Before this existed the export was a pile of facts with no verdict: a crashed run and an
exhausted search both said `succeeded: false`, and the only way to spot a segfault after ~23
minutes was to read every run's exit code and log tail. Findings put the answer at the top.

Findings are built only from facts that are safe in a redacted export (outcome, exit code,
timings, memory, signature classification). They never quote engine output, which could contain
wallet words, so the same findings are valid for both redacted and unredacted reports.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from robo_rec.diagnostics.self_test import SelfTestResult
from robo_rec.recovery.history import RunRecord

# Fraction of the machine's commit limit (RAM + page file) above which a recorded peak is
# considered "close to the limit" and out-of-memory becomes the stated likely cause.
_NEAR_LIMIT_FRACTION = 0.80
_OPENCL_CAPABLE_WALLET_TYPES = {"bip39", "ethereum"}
_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def classify_failure(error: str | None) -> str:
    """Coarse signature of a failed run: "memory" | "max_eta" | "crash" | "other"."""
    text = (error or "").lower()
    if "segmentation fault" in text or "out of memory" in text or "memoryerror" in text:
        return "memory"
    if "max-eta" in text:
        return "max_eta"
    if "traceback" in text or "exit code" in text:
        return "crash"
    return "other"


def _gb(num_bytes: int) -> str:
    return f"{num_bytes / 2**30:.1f} GB"


def _seconds(value: float) -> str:
    if value >= 120:
        return f"{value / 60:.0f} min"
    return f"{value:.0f} s"


def _limit_bytes(memory: dict[str, int] | None) -> int | None:
    if not memory:
        return None
    return memory.get("commit_limit_bytes") or memory.get("total_ram_bytes")


def _run_label(index: int, run: RunRecord) -> str:
    return f"Run {index + 1} ({run.scenario.removesuffix('Spec')}, {run.wallet_type})"


def _error_finding(index: int, run: RunRecord, memory: dict[str, int] | None) -> dict[str, Any]:
    signature = classify_failure(run.error)
    headline = {
        "memory": "crashed (segmentation fault / out of memory)",
        "max_eta": "was refused by the engine (estimated runtime over its --max-eta limit)",
        "crash": "crashed",
        "other": "ended without a result",
    }[signature]
    facts = [f"exit code {run.return_code}", f"ran {_seconds(run.duration_seconds)}"]

    if run.log_offsets:
        silent = run.duration_seconds - run.log_offsets[-1]
        if silent >= 30:
            facts.append(f"no output for the last {_seconds(silent)} before it ended")
    if not any("Will try" in line for line in run.log_lines):
        facts.append(
            "never reached the \"Will try N passwords\" line, so it died while still "
            "counting/preparing candidates, before the search itself began"
        )

    if run.peak_memory_bytes is None:
        facts.append("memory was not recorded for this run")
    else:
        peak = f"peak memory of the search process tree was {_gb(run.peak_memory_bytes)}"
        limit = _limit_bytes(memory)
        if limit and run.peak_memory_bytes >= _NEAR_LIMIT_FRACTION * limit:
            peak += (
                f", which is {run.peak_memory_bytes / limit:.0%} of this machine's "
                f"{_gb(limit)} commit limit — running out of memory is the most likely cause"
            )
        elif limit and signature == "memory":
            peak += (
                f" against a {_gb(limit)} machine limit; that is well below the limit, but "
                "memory is sampled every couple of seconds and a sudden spike can be missed"
            )
        facts.append(peak)

    return {
        "severity": "error",
        "run": index + 1,
        "title": f"{_run_label(index, run)} {headline}",
        "detail": "; ".join(facts) + ".",
    }


def build_findings(
    runs: Sequence[RunRecord],
    self_tests: Sequence[SelfTestResult],
    memory: dict[str, int] | None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    for result in self_tests:
        if not result.passed:
            findings.append(
                {
                    "severity": "error",
                    "run": None,
                    "title": f"Self-test failed for {result.coin}",
                    "detail": "The built-in recover-a-known-phrase check did not pass"
                    + (f" ({result.error})." if result.error else "."),
                }
            )

    for index, run in enumerate(runs):
        if run.outcome == "error":
            findings.append(_error_finding(index, run, memory))
        elif run.outcome == "not_found":
            findings.append(
                {
                    "severity": "info",
                    "run": index + 1,
                    "title": f"{_run_label(index, run)} searched everything and found no match",
                    "detail": "Clean exit after a complete search — the input words, blank "
                    "positions or test address are the likely cause, not the engine.",
                }
            )
        if (
            run.gpu_requested
            and not run.gpu_actually_used
            and run.wallet_type in _OPENCL_CAPABLE_WALLET_TYPES
        ):
            findings.append(
                {
                    "severity": "warning",
                    "run": index + 1,
                    "title": f"{_run_label(index, run)} asked for the GPU but did not use it",
                    "detail": "--enable-opencl was not passed even though this wallet type "
                    "supports it.",
                }
            )

    if not any(f["severity"] in ("error", "warning") for f in findings):
        findings.append(
            {
                "severity": "info",
                "run": None,
                "title": "No abnormal runs were recorded",
                "detail": "Every recorded run ended normally and the self-test passed.",
            }
        )

    findings.sort(key=lambda f: _SEVERITY_ORDER[f["severity"]])
    return findings


__all__ = ["build_findings", "classify_failure"]
