"""BtcrecoverRunner: owns one seedrecover.py subprocess invocation, streams parsed
RecoveryEvent objects back to the caller, and supports thread-safe cancellation.

Qt-agnostic by design (no PySide6 import) — intended to be driven from a GUI-owned worker
thread (QThread/QRunnable), never the Qt main/event-loop thread, since runs can take from
seconds to hours (PRD 4.2/4.1). See the approved plan's Section B for the full rationale.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from robo_rec.recovery.args import (
    build_missing_word_known_position_args,
    build_missing_word_unknown_position_args,
    build_rearrangement_args,
    build_typo_correction_args,
)
from robo_rec.recovery.exceptions import LaunchError
from robo_rec.recovery.history import RunRecord, cap_log_lines, record as record_run
from robo_rec.recovery.models import (
    MissingWordKnownPositionSpec,
    MissingWordUnknownPositionSpec,
    RearrangementSpec,
    RecoveryEvent,
    RecoveryResult,
    RecoverySpec,
    TypoCorrectionSpec,
)
from robo_rec.recovery.parser import parse_line
from robo_rec.util.memory import MemorySampler
from robo_rec.util.paths import btcrecover_root, seedrecover_command
from robo_rec.util.process import stream_lines


def _build_argv_and_tokenlist(
    spec: RecoverySpec, *, use_gpu: bool
) -> tuple[list[str], Path | None]:
    if isinstance(spec, RearrangementSpec):
        argv, tokenlist_path = build_rearrangement_args(spec, use_gpu=use_gpu)
        return argv, tokenlist_path
    if isinstance(spec, MissingWordKnownPositionSpec):
        return build_missing_word_known_position_args(spec, use_gpu=use_gpu), None
    if isinstance(spec, MissingWordUnknownPositionSpec):
        return build_missing_word_unknown_position_args(spec, use_gpu=use_gpu), None
    if isinstance(spec, TypoCorrectionSpec):
        return build_typo_correction_args(spec, use_gpu=use_gpu), None
    raise TypeError(f"Unrecognized RecoverySpec variant: {type(spec).__name__}")


_OUT_OF_MEMORY_HINT = (
    "The search ran out of memory before it could start. Try again with fewer missing words "
    "or close other programs, and send the diagnostics export if it keeps happening."
)


def _describe_failure(return_code: int, error_lines: list[str], log_lines: list[str]) -> str:
    """User-facing explanation for a run that did not end in "found" or a genuine, completed
    "Seed not found" — i.e. the engine crashed, was refused, or exited without a verdict.

    seedrecover exits 0 for a search that ran to exhaustion and 1 for any error it reports
    itself (btcrpass.error_exit); anything else (e.g. 3 from Nuitka's segfault handler) means the
    process died underneath it. The engine's own message is included verbatim so nothing is lost.
    """
    detail = " ".join(error_lines[:3]).strip()
    if not detail:
        tail = [line.strip() for line in log_lines if line.strip()][-3:]
        detail = " ".join(tail)
    lowered = detail.lower()
    if "segmentation fault" in lowered or "out of memory" in lowered:
        message = _OUT_OF_MEMORY_HINT
    elif return_code == 0:
        message = "The search engine stopped without reporting a result."
    else:
        message = f"The search engine stopped unexpectedly (exit code {return_code})."
    return f"{message}\n\nEngine output: {detail}" if detail else message


class BtcrecoverRunner:
    """Not Qt-affine; safe to construct and drive from any single thread at a time."""

    def __init__(
        self,
        spec: RecoverySpec,
        *,
        btcrecover_dir: Path | None = None,
        use_gpu: bool = False,
    ) -> None:
        self._spec = spec
        self._btcrecover_dir = btcrecover_dir or btcrecover_root()
        self._use_gpu = use_gpu
        self._process: subprocess.Popen | None = None
        self._stop_event = threading.Event()
        self._tokenlist_path: Path | None = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def cancel(self) -> None:
        """Thread-safe: safe to call from the Qt main thread while run()/run_iter()
        executes on a worker thread."""
        self._stop_event.set()
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()

    def run(self, on_event: Callable[[RecoveryEvent], None]) -> RecoveryResult:
        """Blocking — call from a worker thread. Streams events to on_event; returns the
        final RecoveryResult once the subprocess exits."""
        result: RecoveryResult | None = None
        for event in self.run_iter():
            on_event(event)
            if event.kind == "finished":
                result = event.result
        assert result is not None
        return result

    def run_iter(self) -> Iterator[RecoveryEvent]:
        """Generator alternative to run(). Final yielded event has kind == 'finished' with
        .result populated. Cleans up any generated tokenlist file on exit or cancellation."""
        argv, tokenlist_path = _build_argv_and_tokenlist(self._spec, use_gpu=self._use_gpu)
        self._tokenlist_path = tokenlist_path
        full_argv = [*seedrecover_command(), *argv]
        start_time = time.time()

        try:
            process, lines = stream_lines(
                full_argv, cwd=self._btcrecover_dir, stop_event=self._stop_event
            )
        except OSError as exc:
            self._record_run(
                argv=argv,
                start_time=start_time,
                return_code=None,
                succeeded=False,
                mnemonic=None,
                matched_path=None,
                log_lines=[],
                launch_error=str(exc),
                outcome="error",
                error=f"Failed to launch seedrecover: {exc}",
            )
            raise LaunchError(f"Failed to launch seedrecover: {exc}") from exc

        # Assign self._process BEFORE yielding: cancel() reads self._process, and once
        # control returns to the caller after a yield, cancel() may be called immediately
        # (e.g. a GUI Cancel button right after seeing the "started" event). If the
        # assignment happened after this yield, an early cancel() would be a silent no-op
        # and the subprocess would run to completion unattended.
        self._process = process
        # Memory is sampled for the whole process tree (seedrecover spawns one worker process per
        # thread) so the diagnostics can show whether a crash coincided with memory exhaustion.
        memory_sampler = MemorySampler()
        memory_sampler.start(process.pid)
        output_started = time.monotonic()

        yield RecoveryEvent(kind="started", message="Starting recovery search...")
        found_result: RecoveryResult | None = None
        mnemonic: str | None = None
        matched_path: str | None = None
        log_lines: list[str] = []
        log_offsets: list[float] = []
        error_lines: list[str] = []
        saw_not_found = False

        try:
            for line in lines:
                event = parse_line(line)
                if event.raw_line is not None:
                    log_lines.append(event.raw_line)
                    log_offsets.append(round(time.monotonic() - output_started, 1))
                if event.kind == "error":
                    error_lines.append(event.message)
                elif event.kind == "not_found":
                    saw_not_found = True
                if event.kind == "found" and event.result is not None:
                    if event.result.mnemonic is not None:
                        mnemonic = event.result.mnemonic
                    if event.result.matched_path is not None:
                        matched_path = event.result.matched_path
                yield event

            return_code = process.wait()
        finally:
            memory_sampler.stop()
            self._cleanup_tokenlist()

        succeeded = mnemonic is not None
        cancelled = self._stop_event.is_set() and not succeeded
        # A genuine "not found" is ONLY a clean exit (0) that also printed "Seed not found".
        # Everything else that isn't a hit or a user cancel is a failure and must be reported
        # as one — otherwise a crash/out-of-memory looks identical to an exhausted search.
        completed_without_hit = return_code == 0 and saw_not_found and not error_lines
        error = None
        if not succeeded and not cancelled and not completed_without_hit:
            error = _describe_failure(return_code, error_lines, log_lines)
        found_result = RecoveryResult(
            mnemonic=mnemonic,
            matched_address=self._first_target_address(),
            matched_path=matched_path,
            return_code=return_code,
            succeeded=succeeded,
            error=error,
            cancelled=cancelled,
        )
        if succeeded:
            outcome = "found"
        elif cancelled:
            outcome = "cancelled"
        elif error:
            outcome = "error"
        else:
            outcome = "not_found"
        self._record_run(
            argv=argv,
            start_time=start_time,
            return_code=return_code,
            succeeded=succeeded,
            mnemonic=mnemonic,
            matched_path=matched_path,
            log_lines=log_lines,
            launch_error=None,
            outcome=outcome,
            error=error,
            log_offsets=log_offsets,
            peak_memory_bytes=memory_sampler.peak_bytes,
            memory_samples=memory_sampler.samples,
        )
        yield RecoveryEvent(
            kind="finished",
            message=(
                "Recovery finished."
                if succeeded
                else "Recovery cancelled."
                if cancelled
                else "Recovery failed."
                if error
                else "Recovery finished: not found."
            ),
            result=found_result,
        )

    def _record_run(
        self,
        *,
        argv: list[str],
        start_time: float,
        return_code: int | None,
        succeeded: bool,
        mnemonic: str | None,
        matched_path: str | None,
        log_lines: list[str],
        launch_error: str | None,
        outcome: str = "unknown",
        error: str | None = None,
        log_offsets: list[float] | None = None,
        peak_memory_bytes: int | None = None,
        memory_samples: list[tuple[float, int]] | None = None,
    ) -> None:
        """Feeds robo_rec.diagnostics.report via robo_rec.recovery.history — see that
        module's docstring for why this stores everything unredacted (redaction happens at
        export time, not here)."""
        record_run(
            RunRecord(
                timestamp=datetime.now(UTC),
                scenario=type(self._spec).__name__,
                wallet_type=getattr(self._spec, "wallet_type", "?"),
                gpu_requested=self._use_gpu,
                gpu_actually_used="--enable-opencl" in argv,
                argv=argv,
                duration_seconds=time.time() - start_time,
                return_code=return_code,
                succeeded=succeeded,
                cancelled=self._stop_event.is_set(),
                recovered_mnemonic=mnemonic,
                matched_address=self._first_target_address(),
                matched_path=matched_path,
                log_lines=cap_log_lines(log_lines),
                launch_error=launch_error,
                outcome=outcome,
                error=error,
                log_offsets=cap_log_lines(log_offsets or []),
                peak_memory_bytes=peak_memory_bytes,
                memory_samples=list(memory_samples or []),
            )
        )

    def _first_target_address(self) -> str | None:
        addrs = getattr(self._spec, "addrs", None)
        return addrs[0] if addrs else None

    def _cleanup_tokenlist(self) -> None:
        if self._tokenlist_path is not None:
            try:
                os.unlink(self._tokenlist_path)
            except FileNotFoundError:
                pass
            self._tokenlist_path = None
