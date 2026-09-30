"""SearchProgressWidget — the shared "searching..." view for Missing Words, Rearrange, and
Typo Correction. Replaces each panel's near-identical bespoke loading view.

Two UX fixes over the earlier per-panel implementation:
 - The title no longer uses a rotating icon (QTransform.rotate() on a static asymmetric
   glyph reads as flickering/oscillating rather than a clean spin, since Qt's SVG render
   + repeated re-pixmap-ing doesn't guarantee smooth sub-pixel rotation at this size).
   Replaced with a simple cycling "." -> ".." -> "..." -> "" text suffix on the title.
 - Shows a read-only summary of what was actually submitted (the seed phrase with blanks
   marked, and the target wallet/address) so the user can see at a glance what's running,
   without needing to go back to the form.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from robo_rec.gui.widgets.run_clock import RunClock
from robo_rec.gui.widgets.seed_row import SeedRow

_DOT_FRAMES = ("", ".", "..", "...")
_DOT_INTERVAL_MS = 450


class SearchProgressWidget(QWidget):
    cancel_requested = Signal()

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._base_title = title
        self._dot_index = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 24, 0, 0)
        layout.setSpacing(16)

        self._title_label = QLabel()
        self._title_label.setObjectName("DashboardTitle")
        layout.addWidget(self._title_label)

        self._subtitle_label = QLabel()
        self._subtitle_label.setObjectName("DashboardSubtitle")
        layout.addWidget(self._subtitle_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)  # indeterminate — btcrecover has no fine-grained %
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(8)
        layout.addWidget(self._progress_bar)

        # Sits directly under the progress bar and above the wrapping phase text, so its
        # position never shifts as the phase message grows.
        self.clock = RunClock()
        layout.addWidget(self.clock)

        self._phase_label = QLabel()
        self._phase_label.setObjectName("InfoNotice")
        self._phase_label.setWordWrap(True)
        layout.addWidget(self._phase_label)

        self._summary_group = QGroupBox("Running with")
        summary_layout = QVBoxLayout(self._summary_group)

        summary_words_label = QLabel("SEED PHRASE")
        summary_words_label.setObjectName("SectionLabel")
        summary_layout.addWidget(summary_words_label)
        self._summary_seed_row = SeedRow(length=12, editable=False)
        summary_layout.addWidget(self._summary_seed_row)

        self._summary_target_label = QLabel()
        self._summary_target_label.setWordWrap(True)
        summary_layout.addWidget(self._summary_target_label)
        layout.addWidget(self._summary_group)

        cancel_button = QPushButton("Cancel Search")
        cancel_button.clicked.connect(self.cancel_requested.emit)
        layout.addWidget(cancel_button)

        layout.addStretch(1)

        self._dot_timer = QTimer(self)
        self._dot_timer.setInterval(_DOT_INTERVAL_MS)
        self._dot_timer.timeout.connect(self._advance_dots)

    def start(
        self,
        *,
        subtitle: str,
        summary_words: list[str],
        target_summary: str,
    ) -> None:
        """Call when a search begins: resets phase text, populates the input summary, and
        starts the title's cycling-dots animation."""
        self._dot_index = 0
        self._title_label.setText(self._base_title)
        self._subtitle_label.setText(subtitle)
        self._phase_label.setText("")

        self._summary_seed_row.set_length(len(summary_words))
        self._summary_seed_row.set_words(summary_words)
        self._summary_target_label.setText(target_summary)

        self._dot_timer.start()
        self.clock.start()

    def stop(self) -> None:
        self._dot_timer.stop()
        self.clock.stop()

    def set_subtitle(self, text: str) -> None:
        self._subtitle_label.setText(text)

    def set_phase(self, text: str) -> None:
        self._phase_label.setText(text)

    def _advance_dots(self) -> None:
        self._dot_index = (self._dot_index + 1) % len(_DOT_FRAMES)
        self._title_label.setText(f"{self._base_title}{_DOT_FRAMES[self._dot_index]}")
