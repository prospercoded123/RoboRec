"""Typo Correction panel — wired to robo_rec.recovery via RecoveryWorker.

Mirrors PRD 4.3: enter a complete phrase you believe is correct but might
contain individual typos or slightly-wrong words, and Robo-Rec's typo engine
searches nearby spellings (and, if needed, entirely different words) for a
combination that verifies against your address.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from robo_rec.derivation import validate_address
from robo_rec.gui.coin_options import (
    COIN_OPTION_LABELS,
    UNSUPPORTED_COIN_MESSAGE,
    coin_for_label,
    detect_coin_label,
    wallet_type_for_coin,
)
from robo_rec.gui.icons import load_pixmap
from robo_rec.gui.panels.base_panel import BasePanel
from robo_rec.gui.recovery_worker import RecoveryWorker
from robo_rec.gui.result_text import failure_text
from robo_rec.gui.theme import ACCENT
from robo_rec.gui.widgets.animated_stack import AnimatedStackedWidget
from robo_rec.gui.widgets.copy_button import CopyButton
from robo_rec.gui.widgets.run_clock import RunClock
from robo_rec.gui.widgets.search_progress import SearchProgressWidget
from robo_rec.gui.widgets.seed_row import SeedRow
from robo_rec.recovery.exceptions import InvalidSpecError
from robo_rec.recovery.models import TypoCorrectionSpec


class TypoCorrectionPanel(BasePanel):
    def __init__(self, parent=None) -> None:
        super().__init__(
            "Typo Correction",
            "Enter your best-guess phrase, complete and in order — even if a word or "
            "two might be slightly wrong. Robo-Rec checks nearby spellings first.",
            parent,
        )

        self._worker: RecoveryWorker | None = None

        self._view_stack = AnimatedStackedWidget()
        self.root_layout.addWidget(self._view_stack, stretch=1)

        self._view_stack.addWidget(self._build_form_view())

        self._progress = SearchProgressWidget("Checking for typos")
        self._progress.cancel_requested.connect(self._on_cancel_clicked)
        self._view_stack.addWidget(self._progress)

        self._view_stack.addWidget(self._build_result_view())

    # ---- form view ----------------------------------------------------

    def _build_form_view(self) -> QWidget:
        view = QWidget()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        length_group = QGroupBox("Phrase length")
        length_layout = QHBoxLayout(length_group)
        self._length_combo = QComboBox()
        self._length_combo.addItems(["12 words", "24 words"])
        self._length_combo.currentIndexChanged.connect(self._on_length_changed)
        length_layout.addWidget(self._length_combo)
        layout.addWidget(length_group)

        tiles_label = QLabel("YOUR BEST-GUESS PHRASE")
        tiles_label.setObjectName("SectionLabel")
        layout.addWidget(tiles_label)

        self._seed_row = SeedRow(length=12, editable=True)
        self._seed_row.length_exceeded.connect(self._on_seed_row_length_exceeded)
        layout.addWidget(self._seed_row)

        target_group = QGroupBox("Test address")
        target_layout = QHBoxLayout(target_group)
        self._address_field = QLineEdit()
        self._address_field.setPlaceholderText("An address you know is associated with this wallet")
        self._address_field.textChanged.connect(self._on_address_changed)
        target_layout.addWidget(self._address_field, stretch=1)
        self._token_combo = QComboBox()
        self._token_combo.addItems(list(COIN_OPTION_LABELS))
        target_layout.addWidget(self._token_combo)
        layout.addWidget(target_group)

        info = QLabel(
            "Robo-Rec first tries words that look similar to what you typed (a likely "
            "misspelling), then falls back to trying entirely different words if needed."
        )
        info.setObjectName("InfoNotice")
        info.setWordWrap(True)
        layout.addWidget(info)

        self._start_button = QPushButton("Start Search")
        self._start_button.setObjectName("PrimaryButton")
        self._start_button.clicked.connect(self._on_proceed_clicked)
        layout.addWidget(self._start_button)

        layout.addStretch(1)
        return view

    # ---- result view ---------------------------------------------------

    def _build_result_view(self) -> QWidget:
        view = QWidget()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 40, 0, 0)
        layout.setSpacing(16)
        layout.addStretch(1)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        self._result_icon = QLabel()
        title_row.addWidget(self._result_icon)
        self._result_title = QLabel()
        self._result_title.setObjectName("DashboardTitle")
        title_row.addWidget(self._result_title)
        title_row.addStretch(1)
        layout.addLayout(title_row)

        self._result_subtitle = QLabel()
        self._result_subtitle.setObjectName("DashboardSubtitle")
        self._result_subtitle.setWordWrap(True)
        layout.addWidget(self._result_subtitle)

        self._result_clock = RunClock()
        layout.addWidget(self._result_clock)

        seed_header = QHBoxLayout()
        seed_label = QLabel("SEED PHRASE")
        seed_label.setObjectName("SectionLabel")
        seed_header.addWidget(seed_label)
        seed_header.addStretch(1)
        self._copy_button = CopyButton()
        seed_header.addWidget(self._copy_button)
        layout.addLayout(seed_header)

        self._result_row = SeedRow(length=12, editable=False)
        layout.addWidget(self._result_row)

        again_button = QPushButton("Start Another Search")
        again_button.clicked.connect(self._reset_to_form)
        layout.addWidget(again_button)

        layout.addStretch(2)
        return view

    # ---- behavior ---------------------------------------------------

    def _on_length_changed(self) -> None:
        length = 12 if self._length_combo.currentIndex() == 0 else 24
        self._seed_row.set_length(length)

    def _on_seed_row_length_exceeded(self, words: list[str]) -> None:
        """A paste had more words than the row currently fits — grow to 24 words
        (the only longer supported length) and re-run the paste at the new size."""
        if len(words) <= 12 or self._length_combo.currentIndex() == 1:
            return
        self._length_combo.setCurrentIndex(1)  # triggers _on_length_changed -> set_length(24)
        self._seed_row.paste_all(words)

    def _on_address_changed(self, text: str) -> None:
        detected = detect_coin_label(text)
        if detected:
            index = self._token_combo.findText(detected)
            if index >= 0:
                self._token_combo.setCurrentIndex(index)

    def _on_proceed_clicked(self) -> None:
        address = self._address_field.text().strip()
        if not address:
            QMessageBox.warning(
                self,
                "Test address required",
                "Robo-Rec needs an address to verify candidate phrases against before "
                "it can search. Enter an address you know is associated with this wallet.",
            )
            return

        coin = coin_for_label(self._token_combo.currentText())
        if coin is None:
            QMessageBox.warning(self, "Unsupported token", UNSUPPORTED_COIN_MESSAGE)
            return

        address_error = validate_address(address, coin)
        if address_error is not None:
            QMessageBox.warning(self, "Address doesn't look right", address_error)
            return

        words = self._seed_row.words()
        if any(not w for w in words):
            QMessageBox.warning(
                self,
                "Complete phrase required",
                "Typo Correction is for complete phrases with possible mistakes — not "
                "missing words. Use Missing Words if any tile is blank.",
            )
            return

        wallet_type = wallet_type_for_coin(coin)
        try:
            spec = TypoCorrectionSpec(
                best_guess_mnemonic=" ".join(words),
                wallet_type=wallet_type,
                addrs=[address],
            )
        except InvalidSpecError as exc:
            QMessageBox.warning(self, "Can't start this search", str(exc))
            return

        self._view_stack.setCurrentIndex(1)
        self._progress.start(
            subtitle="Checking nearby spellings first…",
            summary_words=words,
            target_summary=f"{self._token_combo.currentText()}  ·  {address}",
        )
        self._start_search(spec)

    def _start_search(self, spec: TypoCorrectionSpec) -> None:
        self._worker = RecoveryWorker(spec)
        self._worker.event.connect(self._on_recovery_event)
        self._worker.finished.connect(self._on_recovery_finished)
        self._worker.failed.connect(self._on_recovery_failed)
        self._worker.start()

    def _on_cancel_clicked(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def _on_recovery_event(self, event) -> None:
        if event.kind == "phase":
            self._progress.set_phase(event.message)
        elif event.kind == "eta":
            self._progress.set_subtitle(event.message)

    def _on_recovery_finished(self, result) -> None:
        self._progress.stop()
        self._cleanup_worker()
        self._show_result(result)

    def _on_recovery_failed(self, message: str) -> None:
        self._progress.stop()
        self._cleanup_worker()
        self._view_stack.setCurrentIndex(0)
        QMessageBox.critical(self, "Search failed to start", message)

    def _cleanup_worker(self) -> None:
        if self._worker is not None:
            self._worker.wait_and_cleanup()
            self._worker = None

    def shutdown(self) -> None:
        """Called from MainWindow.closeEvent: cancel and join any in-flight search so its
        background QThread doesn't get destroyed while still running."""
        if self._worker is not None:
            self._worker.cancel()
            self._cleanup_worker()

    def _show_result(self, result) -> None:
        self._result_clock.show_span_of(self._progress.clock)
        if result.succeeded and result.mnemonic:
            self._result_icon.setPixmap(load_pixmap("party-popper", ACCENT, 22))
            self._result_title.setText("Found the corrected phrase")
            self._result_subtitle.setText(
                "Every word below matches a valid, address-verified phrase."
            )
            words = result.mnemonic.split()
            self._result_row.set_length(len(words))
            self._result_row.set_words(words)
            self._copy_button.set_text_to_copy(result.mnemonic)
            self._copy_button.setVisible(True)
        else:
            self._result_icon.setPixmap(load_pixmap("loader-circle", ACCENT, 22))
            title, subtitle = failure_text(
                result,
                not_found_title="No corrected phrase found",
                not_found_subtitle=(
                    "Robo-Rec searched nearby spellings and word substitutions and none "
                    "matched the test address. Double-check the address and your phrase."
                ),
            )
            self._result_title.setText(title)
            self._result_subtitle.setText(subtitle)
            self._result_row.set_length(len(self._seed_row.tiles()))
            self._result_row.set_words(self._seed_row.words())
            self._copy_button.setVisible(False)
        self._view_stack.setCurrentIndex(2)

    def _reset_to_form(self) -> None:
        self._view_stack.setCurrentIndex(0)
