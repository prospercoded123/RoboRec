"""How BtcrecoverRunner classifies a finished subprocess: a hit, a genuinely exhausted search,
a user cancel, or an engine failure. Uses a fake subprocess so no search actually runs.

Regression for a client report: a 3-blank search crashed (Nuitka segfault, exit code 3) after
~23 minutes and the GUI told them "No matching phrase found — double-check your words", which
sent them debugging their input when the engine had simply died.
"""

from __future__ import annotations


import pytest

from robo_rec.gui.result_text import CANCELLED_TITLE, ERROR_TITLE, failure_text
from robo_rec.recovery import runner as runner_module
from robo_rec.recovery.models import MissingWordKnownPositionSpec
from robo_rec.recovery.runner import BtcrecoverRunner

NOT_FOUND = [
    "Phase 1/1: up to 2 mistakes, 2 of which can be an entirely different seed word.",
    "Will try 4,194,304 passwords, ETA 1 minutes 28 seconds ...",
    "Seed not found, sorry...",
]
SEGFAULT = [
    "Phase 1/1: up to 3 mistakes, 3 of which can be an entirely different seed word.",
    "Duplicate Check Level: 0 , Add --no-dupchecks up to 4 times fully disable duplicate checking",
    "",
    "Nuitka: A segmentation fault has occurred. This is highly unusual and can",
    "have multiple reasons. Please check https://nuitka.net/info/segfault.html",
]


class _FakeProcess:
    pid = 0  # never a live process, so the memory sampler measures nothing (None, not 0)

    def __init__(self, return_code: int) -> None:
        self._return_code = return_code

    def wait(self) -> int:
        return self._return_code

    def poll(self):
        return self._return_code

    def terminate(self) -> None:
        pass


def _spec() -> MissingWordKnownPositionSpec:
    return MissingWordKnownPositionSpec(
        words=["abandon"] * 9 + [None] * 3, wallet_type="bip39", addrs=["x"]
    )


RECORDED: list = []


def _run(monkeypatch, lines, return_code, *, cancel=False):
    runner = BtcrecoverRunner(_spec())
    RECORDED.clear()

    def fake_stream_lines(argv, *, cwd, stop_event):
        if cancel:
            runner._stop_event.set()
        return _FakeProcess(return_code), iter(lines)

    monkeypatch.setattr(runner_module, "stream_lines", fake_stream_lines)
    monkeypatch.setattr(runner_module, "record_run", RECORDED.append)
    events = list(runner.run_iter())
    assert events[-1].kind == "finished"
    return events, events[-1].result


def test_completed_search_with_no_hit_is_a_genuine_not_found(monkeypatch):
    _, result = _run(monkeypatch, NOT_FOUND, 0)
    assert result.succeeded is False
    assert result.error is None
    assert result.cancelled is False


def test_segfault_is_reported_as_an_error_not_as_not_found(monkeypatch):
    events, result = _run(monkeypatch, SEGFAULT, 3)
    assert result.succeeded is False
    assert result.error is not None
    assert "ran out of memory" in result.error
    assert "segmentation fault" in result.error.lower()
    assert any(e.kind == "error" for e in events)
    assert events[-1].message == "Recovery failed."


def test_error_exit_code_one_with_error_line_is_an_error(monkeypatch):
    _, result = _run(monkeypatch, ["Error: at least 9 passwords to try, ETA > --max-eta"], 1)
    assert result.error is not None
    assert "max-eta" in result.error


def test_clean_exit_without_any_verdict_is_an_error(monkeypatch):
    # Exit 0 but no "Seed not found" line: the engine never said it finished searching.
    _, result = _run(monkeypatch, ["Wallet Type: btcrseed.WalletSolana"], 0)
    assert result.error is not None


def test_nonzero_exit_without_error_lines_still_reports_the_exit_code(monkeypatch):
    _, result = _run(monkeypatch, ["some output"], 7)
    assert result.error is not None
    assert "exit code 7" in result.error


def test_found_seed_is_success_with_no_error(monkeypatch):
    lines = [
        "***MATCHING SEED FOUND***, Matched on Address at derivation path: m/44'/0'/0'/0/0",
        "Seed found: " + " ".join(["abandon"] * 11 + ["about"]),
    ]
    _, result = _run(monkeypatch, lines, 0)
    assert result.succeeded is True
    assert result.error is None


def test_user_cancel_is_not_an_error(monkeypatch):
    _, result = _run(monkeypatch, ["Phase 1/1: x"], 1, cancel=True)
    assert result.cancelled is True
    assert result.error is None


@pytest.mark.parametrize(
    ("lines", "code", "expected_title"),
    [(SEGFAULT, 3, ERROR_TITLE), (["Phase 1/1: x"], 1, CANCELLED_TITLE)],
)
def test_failure_text_distinguishes_error_and_cancel(monkeypatch, lines, code, expected_title):
    _, result = _run(monkeypatch, lines, code, cancel=expected_title == CANCELLED_TITLE)
    title, subtitle = failure_text(result, not_found_title="NF", not_found_subtitle="nf")
    assert title == expected_title
    assert subtitle != "nf"


def test_failure_text_uses_panel_wording_only_for_a_real_not_found(monkeypatch):
    _, result = _run(monkeypatch, NOT_FOUND, 0)
    assert failure_text(result, not_found_title="NF", not_found_subtitle="nf") == ("NF", "nf")


def test_history_record_carries_outcome_error_and_aligned_log_offsets(monkeypatch):
    _run(monkeypatch, SEGFAULT, 3)
    (record,) = RECORDED
    assert record.outcome == "error"
    assert record.error and "segmentation fault" in record.error.lower()
    assert len(record.log_offsets) == len(record.log_lines) == len(SEGFAULT)
    assert record.log_offsets == sorted(record.log_offsets)
    assert record.peak_memory_bytes is None  # unmeasured is None, never a misleading 0


@pytest.mark.parametrize(
    ("lines", "code", "cancel", "expected"),
    [
        (NOT_FOUND, 0, False, "not_found"),
        (["Seed found: a b c"], 0, False, "found"),
        (["Phase 1/1: x"], 1, True, "cancelled"),
        (SEGFAULT, 3, False, "error"),
    ],
)
def test_history_outcome_matches_what_happened(monkeypatch, lines, code, cancel, expected):
    _run(monkeypatch, lines, code, cancel=cancel)
    assert RECORDED[0].outcome == expected
