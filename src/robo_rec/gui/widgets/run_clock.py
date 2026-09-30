"""RunClock — a simple wall-clock readout for a long-running job: elapsed time ticking by
the second, plus the wall-clock time it started and (once finished) stopped.

Pure frontend: it only timestamps start()/stop() calls, so any panel can drop one next to a
job. The elapsed figure uses a monospace face so digits don't shift as they change, which
keeps the readout visually stationary.
"""

from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

_TICK_MS = 250  # finer than 1s so the displayed second never visibly lags


def format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _format_wall(moment: datetime | None) -> str:
    return moment.strftime("%H:%M:%S") if moment else "—"


class RunClock(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RunClock")
        self.setProperty("state", "idle")

        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = None
        self._started_mono: float | None = None
        self._stopped_mono: float | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(24)

        elapsed_col = QVBoxLayout()
        elapsed_col.setSpacing(0)
        elapsed_caption = QLabel("Elapsed")
        elapsed_caption.setObjectName("RunClockCaption")
        elapsed_col.addWidget(elapsed_caption)
        self._elapsed_label = QLabel(format_elapsed(0))
        self._elapsed_label.setObjectName("RunClockElapsed")
        elapsed_col.addWidget(self._elapsed_label)
        layout.addLayout(elapsed_col)

        layout.addStretch(1)

        self._started_label = self._add_stamp(layout, "Started")
        self._stopped_label = self._add_stamp(layout, "Stopped")

        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._refresh)

    @staticmethod
    def _add_stamp(layout: QHBoxLayout, caption: str) -> QLabel:
        column = QVBoxLayout()
        column.setSpacing(0)
        caption_label = QLabel(caption)
        caption_label.setObjectName("RunClockCaption")
        column.addWidget(caption_label)
        value = QLabel("—")
        value.setObjectName("RunClockStamp")
        column.addWidget(value)
        layout.addLayout(column)
        return value

    # ---- lifecycle ----------------------------------------------------

    def start(self) -> None:
        self._started_at = datetime.now()
        self._stopped_at = None
        self._started_mono = time.monotonic()
        self._stopped_mono = None
        self._set_state("running")
        self._refresh()
        self._timer.start()

    def stop(self) -> None:
        if self._started_mono is None or self._stopped_mono is not None:
            return
        self._timer.stop()
        self._stopped_at = datetime.now()
        self._stopped_mono = time.monotonic()
        self._set_state("stopped")
        self._refresh()

    def show_span_of(self, other: RunClock) -> None:
        """Display another clock's finished run as a static readout (used by result views)."""
        self._timer.stop()
        self._started_at = other._started_at
        self._stopped_at = other._stopped_at
        self._started_mono = other._started_mono
        self._stopped_mono = other._stopped_mono
        self._set_state("stopped" if other._stopped_mono is not None else "idle")
        self._refresh()

    # ---- internals ----------------------------------------------------

    def _set_state(self, state: str) -> None:
        self.setProperty("state", state)
        self.style().unpolish(self)
        self.style().polish(self)

    def _refresh(self) -> None:
        if self._started_mono is None:
            elapsed = 0.0
        else:
            end = self._stopped_mono if self._stopped_mono is not None else time.monotonic()
            elapsed = end - self._started_mono
        self._elapsed_label.setText(format_elapsed(elapsed))
        self._started_label.setText(_format_wall(self._started_at))
        self._stopped_label.setText(_format_wall(self._stopped_at))
