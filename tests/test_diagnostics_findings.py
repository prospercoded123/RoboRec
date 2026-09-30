"""The Diagnostics export must state its own conclusions. Regression for a client report: a
3-blank search died with a Nuitka segfault (exit code 3) after ~23 minutes; the export held the
facts but no verdict and no memory data, so the cause had to be inferred from source code."""

from __future__ import annotations

from datetime import UTC, datetime

from robo_rec.diagnostics.findings import build_findings, classify_failure
from robo_rec.diagnostics.report import _run_record_to_dict
from robo_rec.diagnostics.self_test import SelfTestResult
from robo_rec.recovery.history import RunRecord

GB = 2**30
# A 32 GB machine with a 45 GB commit limit, like the one that produced the real report.
MACHINE = {
    "total_ram_bytes": 32 * GB,
    "available_ram_bytes": 4 * GB,
    "commit_limit_bytes": 45 * GB,
    "commit_available_bytes": 1 * GB,
}
SEGFAULT_LINES = [
    "Phase 1/1: up to 3 mistakes, 3 of which can be an entirely different seed word.",
    "Duplicate Check Level: 0 , Add --no-dupchecks up to 4 times fully disable duplicate checking",
    "Nuitka: A segmentation fault has occurred. This is highly unusual and can",
]


def _run(**overrides) -> RunRecord:
    fields = dict(
        timestamp=datetime(2026, 9, 30, tzinfo=UTC),
        scenario="MissingWordKnownPositionSpec",
        wallet_type="solana",
        gpu_requested=True,
        gpu_actually_used=False,
        argv=["--mnemonic", "abandon ability %%", "--addrs", "SOMEADDRESS"],
        duration_seconds=1399.0,
        return_code=3,
        succeeded=False,
        cancelled=False,
        recovered_mnemonic=None,
        matched_address="SOMEADDRESS",
        matched_path=None,
        log_lines=SEGFAULT_LINES,
        log_offsets=[0.4, 0.9, 1.0],
        outcome="error",
        error="The search ran out of memory.\n\nEngine output: Nuitka: A segmentation fault",
    )
    fields.update(overrides)
    return RunRecord(**fields)


def test_client_crash_is_reported_as_an_error_with_the_evidence():
    crash = _run(peak_memory_bytes=44 * GB)
    findings = build_findings([crash], [], MACHINE)

    assert findings[0]["severity"] == "error"
    assert findings[0]["run"] == 1
    detail = findings[0]["detail"]
    assert "crashed" in findings[0]["title"]
    assert "exit code 3" in detail
    assert "no output for the last 23 min" in detail  # 1399s run, last line at 1.0s
    assert "never reached" in detail and "Will try" in detail
    assert "44.0 GB" in detail and "most likely cause" in detail


def test_low_peak_memory_does_not_overclaim_out_of_memory():
    detail = build_findings([_run(peak_memory_bytes=2 * GB)], [], MACHINE)[0]["detail"]
    assert "most likely cause" not in detail
    assert "spike can be missed" in detail


def test_unmeasured_memory_is_stated_not_hidden():
    detail = build_findings([_run(peak_memory_bytes=None)], [], MACHINE)[0]["detail"]
    assert "memory was not recorded" in detail


def test_clean_not_found_is_info_and_blames_the_input_not_the_engine():
    run = _run(outcome="not_found", error=None, return_code=0, log_lines=["Will try 5 passwords"])
    findings = build_findings([run], [], MACHINE)
    assert [f["severity"] for f in findings] == ["info", "info"]
    assert "input words" in findings[0]["detail"]


def test_errors_sort_before_warnings_and_info():
    ok = _run(outcome="not_found", error=None, return_code=0)
    gpu_unused = _run(outcome="found", error=None, return_code=0, wallet_type="bip39")
    findings = build_findings([ok, gpu_unused, _run()], [], MACHINE)
    severities = [f["severity"] for f in findings]
    assert severities == sorted(severities, key=["error", "warning", "info"].index)
    assert severities[0] == "error"


def test_solana_not_using_the_gpu_is_not_flagged():
    solana = _run(outcome="found", error=None, return_code=0, wallet_type="solana")
    assert all(f["severity"] != "warning" for f in build_findings([solana], [], MACHINE))


def test_failed_self_test_is_an_error_finding():
    failed = SelfTestResult(
        coin="ethereum", passed=False, elapsed_seconds=1.0, gpu_requested=True,
        gpu_actually_used=True, error="boom", mnemonic=None, address=None,
        recovered_mnemonic=None,
    )
    finding = build_findings([], [failed], MACHINE)[0]
    assert finding["severity"] == "error" and "ethereum" in finding["title"]


def test_no_runs_reports_nothing_abnormal():
    findings = build_findings([], [], MACHINE)
    assert len(findings) == 1 and findings[0]["title"] == "No abnormal runs were recorded"


def test_classify_failure_signatures():
    assert classify_failure("... Nuitka: A segmentation fault ...") == "memory"
    assert classify_failure("Error: out of memory") == "memory"
    assert classify_failure("Error: ETA > --max-eta option (168 hours)") == "max_eta"
    assert classify_failure("stopped unexpectedly (exit code 7)") == "crash"
    assert classify_failure(None) == "other"


def test_findings_never_quote_engine_output_or_wallet_material():
    crash = _run(
        error="Engine output: secretword appeared here",
        log_lines=["secretword", *SEGFAULT_LINES],
        log_offsets=[0.1, 0.4, 0.9, 1.0],
    )
    for finding in build_findings([crash], [], MACHINE):
        assert "secretword" not in finding["title"] + finding["detail"]


def test_redacted_export_scrubs_secrets_from_error_and_log_but_keeps_timing():
    run = _run(
        argv=["--mnemonic", "abandon ability %%", "--addrs", "SOMEADDRESS"],
        error="Engine output: abandon and SOMEADDRESS leaked",
        log_lines=["abandon ability", "SOMEADDRESS", "fine line"],
        log_offsets=[1.5, 2.5, 3.5],
    )
    redacted = _run_record_to_dict(run, include_sensitive=False)
    flat = str(redacted["error"]) + str(redacted["log"])
    assert "abandon" not in flat and "SOMEADDRESS" not in flat
    assert [entry["t"] for entry in redacted["log"]] == [1.5, 2.5, 3.5]
    assert redacted["outcome"] == "error"

    unredacted = _run_record_to_dict(run, include_sensitive=True)
    assert "SOMEADDRESS" in str(unredacted["log"])


def test_timestamped_log_tolerates_records_without_offsets():
    run = _run(log_offsets=[])
    entries = _run_record_to_dict(run, include_sensitive=True)["log"]
    assert len(entries) == len(SEGFAULT_LINES)
    assert all(entry["t"] is None for entry in entries)


def test_memory_curve_is_exported_in_gb():
    run = _run(peak_memory_bytes=3 * GB, memory_samples=[(0.0, GB), (2.0, 3 * GB)])
    exported = _run_record_to_dict(run, include_sensitive=False)
    assert exported["peak_memory_gb"] == 3.0
    assert exported["memory_samples_gb"] == [[0.0, 1.0], [2.0, 3.0]]
