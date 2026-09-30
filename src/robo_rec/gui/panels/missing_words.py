"""Missing Words panel — wired to robo_rec.recovery via RecoveryWorker.

Single unified flow per the user's sketch: paste/type the words you know in
order, leave boxes blank for the ones you don't. A "do you know the exact
position of each blank" toggle decides how the engine will search (mirrors
PRD 4.2: known positions support 1-4 missing words; unknown positions support
only 1-2, since combinatorics make 3+ unknown-position infeasible).
"""

from __future__ import annotations

import math

from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
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
from robo_rec.gui.estimate import format_estimate, is_estimate_impractical
from robo_rec.gui.icons import load_pixmap
from robo_rec.gui.panels.base_panel import BasePanel
from robo_rec.gui.log_filter import extract_log_from_event
from robo_rec.gui.recovery_worker import RecoveryWorker
from robo_rec.gui.result_text import failure_text
from robo_rec.gui.terminal_sidebar import TerminalSidebar
from robo_rec.gui.theme import ACCENT
from robo_rec.gui.widgets.animated_stack import AnimatedStackedWidget
from robo_rec.gui.widgets.copy_button import CopyButton
from robo_rec.gui.widgets.run_clock import RunClock
from robo_rec.gui.widgets.search_progress import SearchProgressWidget
from robo_rec.gui.widgets.seed_row import SeedRow
from robo_rec.recovery.exceptions import InvalidSpecError
from robo_rec.recovery.models import MissingWordKnownPositionSpec, MissingWordUnknownPositionSpec

KNOWN_POSITION_MAX = 4
UNKNOWN_POSITION_MAX = 2

KNOWN_POSITION_WARNINGS = {
    3: "3 missing words at known positions can take hours on a GPU, up to a "
    "few days on CPU alone.",
    4: "4 missing words at known positions can take days even with a GPU. "
    "GPU is strongly recommended before starting.",
}
UNKNOWN_POSITION_OVER_LIMIT = (
    "Robo-Rec can only search unknown positions for 1-2 missing words — beyond "
    "that the number of possible arrangements is infeasible to check. Mark the "
    "positions you do know, or reduce the number of blanks."
)


def combinations_for(total_words: int, missing: int, *, known_position: bool) -> int:
    if missing == 0:
        return 0
    combinations = 2048**missing
    if not known_position:
        combinations *= math.comb(total_words, missing)
    return combinations


class MissingWordsPanel(BasePanel):
    def __init__(self, parent=None) -> None:
        super().__init__(
            "Missing Words",
            "Type or paste the words you know, in order. Leave a box blank "
            "for each word you don't know.",
            parent,
        )

        self._worker: RecoveryWorker | None = None

        self._view_stack = AnimatedStackedWidget()
        self.root_layout.addWidget(self._view_stack, stretch=1)

        self._view_stack.addWidget(self._build_form_view())

        self._progress = SearchProgressWidget("Searching for your seed phrase")
        self._progress.cancel_requested.connect(self._on_cancel_clicked)
        self._view_stack.addWidget(self._progress)

        self._view_stack.addWidget(self._build_result_view())

        self._refresh_missing_count()

    # ---- form view ----------------------------------------------------

    def _build_form_view(self) -> QWidget:
        view = QWidget()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        options_row = QHBoxLayout()
        options_row.setSpacing(16)

        length_group = QGroupBox("Phrase length")
        length_layout = QHBoxLayout(length_group)
        self._length_combo = QComboBox()
        self._length_combo.addItems(["12 words", "24 words"])
        self._length_combo.currentIndexChanged.connect(self._on_length_changed)
        length_layout.addWidget(self._length_combo)
        options_row.addWidget(length_group)

        position_group = QGroupBox("Do you know the position of each blank?")
        position_layout = QHBoxLayout(position_group)
        self._known_radio = QRadioButton("Yes, mark exact blanks")
        self._known_radio.setChecked(True)
        self._unknown_radio = QRadioButton("No, just missing somewhere")
        position_group_btns = QButtonGroup(self)
        position_group_btns.addButton(self._known_radio)
        position_group_btns.addButton(self._unknown_radio)
        self._known_radio.toggled.connect(self._refresh_missing_count)
        position_layout.addWidget(self._known_radio)
        position_layout.addWidget(self._unknown_radio)
        options_row.addWidget(position_group, stretch=1)

        layout.addLayout(options_row)

        self._warning_label = QLabel()
        self._warning_label.setObjectName("WarningNotice")
        self._warning_label.setWordWrap(True)
        self._warning_label.hide()
        layout.addWidget(self._warning_label)

        tiles_header = QHBoxLayout()
        tiles_label = QLabel("PASTE ALL KNOWN WORDS, IN ORDER")
        tiles_label.setObjectName("SectionLabel")
        tiles_header.addWidget(tiles_label)
        tiles_header.addStretch(1)
        self._missing_count_label = QLabel()
        self._missing_count_label.setObjectName("SectionLabel")
        tiles_header.addWidget(self._missing_count_label)
        layout.addLayout(tiles_header)

        self._seed_row = SeedRow(length=12, editable=True)
        self._seed_row.words_changed.connect(self._refresh_missing_count)
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

        estimate_row = QHBoxLayout()
        self._estimate_label = QLabel()
        self._estimate_label.setObjectName("InfoNotice")
        estimate_row.addWidget(self._estimate_label, stretch=1)
        layout.addLayout(estimate_row)

        self._start_button = QPushButton("Proceed")
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
        title_row.addWidget(self._result_title)
        title_row.addStretch(1)
        layout.addLayout(title_row)

        self._result_subtitle = QLabel()
        self._result_subtitle.setWordWrap(True)
        layout.addWidget(self._result_subtitle)

        self._result_clock = RunClock()
        layout.addWidget(self._result_clock)

        self._seed_card = QWidget()
        self._seed_card.setObjectName("SuccessSeedCard")
        seed_card_layout = QVBoxLayout(self._seed_card)
        seed_card_layout.setContentsMargins(20, 16, 20, 20)
        seed_card_layout.setSpacing(12)

        seed_header = QHBoxLayout()
        seed_label = QLabel("SEED PHRASE")
        seed_label.setObjectName("SectionLabel")
        seed_header.addWidget(seed_label)
        seed_header.addStretch(1)
        self._copy_button = CopyButton()
        seed_header.addWidget(self._copy_button)
        seed_card_layout.addLayout(seed_header)

        self._result_row = SeedRow(length=12, editable=False)
        seed_card_layout.addWidget(self._result_row)

        layout.addWidget(self._seed_card)

        again_button = QPushButton("Start Another Recovery")
        again_button.clicked.connect(self._reset_to_form)
        layout.addWidget(again_button)

        layout.addStretch(2)
        return view

    # ---- behavior ---------------------------------------------------

    def _on_length_changed(self) -> None:
        length = 12 if self._length_combo.currentIndex() == 0 else 24
        self._seed_row.set_length(length)
        self._refresh_missing_count()

    def _on_seed_row_length_exceeded(self, words: list[str]) -> None:
        """A paste had more words than the row currently fits — grow to 24 words
        (the only longer supported length) and re-run the paste at the new size."""
        if len(words) <= 12 or self._length_combo.currentIndex() == 1:
            return
        self._length_combo.setCurrentIndex(1)  # triggers _on_length_changed -> set_length(24)
        self._seed_row.paste_all(words)
        self._refresh_missing_count()

    def _on_address_changed(self, text: str) -> None:
        detected = detect_coin_label(text)
        if detected:
            index = self._token_combo.findText(detected)
            if index >= 0:
                self._token_combo.setCurrentIndex(index)

    def _refresh_missing_count(self) -> None:
        missing = self._seed_row.missing_count()
        total = len(self._seed_row.tiles())
        known_position = self._known_radio.isChecked()

        self._missing_count_label.setText(f"MISSING WORDS: {missing} (derived)")

        limit = KNOWN_POSITION_MAX if known_position else UNKNOWN_POSITION_MAX
        over_limit = missing > limit

        warning = None
        if over_limit and not known_position:
            warning = UNKNOWN_POSITION_OVER_LIMIT
        elif over_limit and known_position:
            warning = (
                f"{missing} missing words at known positions is beyond what Robo-Rec "
                "supports (max 4). Fill in more words to continue."
            )
        elif known_position:
            warning = KNOWN_POSITION_WARNINGS.get(missing)

        if warning:
            self._warning_label.setText(warning)
            self._warning_label.show()
        else:
            self._warning_label.hide()

        impractical = False
        if missing == 0:
            estimate_text = "Fill in at least one blank to see a time estimate."
        else:
            combinations = combinations_for(total, missing, known_position=known_position)
            impractical = is_estimate_impractical(combinations)
            estimate_text = (
                f"Estimated time: {format_estimate(combinations)} "
                f"(based on {missing} missing word(s))"
            )
        self._estimate_label.setText(estimate_text)
        self._estimate_label.setObjectName("DangerNotice" if impractical else "InfoNotice")
        self._estimate_label.style().unpolish(self._estimate_label)
        self._estimate_label.style().polish(self._estimate_label)

        self._start_button.setEnabled(0 < missing <= limit)

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
        known_position = self._known_radio.isChecked()
        wallet_type = wallet_type_for_coin(coin)

        try:
            if known_position:
                spec = MissingWordKnownPositionSpec(
                    words=[w or None for w in words],
                    wallet_type=wallet_type,
                    addrs=[address],
                )
            else:
                spec = MissingWordUnknownPositionSpec(
                    words=[w for w in words if w],
                    full_length=len(words),
                    wallet_type=wallet_type,
                    addrs=[address],
                )
        except InvalidSpecError as exc:
            QMessageBox.warning(self, "Can't start this search", str(exc))
            return

        combinations = combinations_for(
            len(words), self._seed_row.missing_count(), known_position=known_position
        )
        self._view_stack.setCurrentIndex(1)
        self._progress.start(
            subtitle=f"Estimated time: {format_estimate(combinations)}.",
            summary_words=words,
            target_summary=f"{self._token_combo.currentText()}  ·  {address}",
        )
        self._start_search(spec)

    def _start_search(self, spec) -> None:
        self._worker = RecoveryWorker(spec)
        self._worker.event.connect(self._on_recovery_event)
        self._worker.finished.connect(self._on_recovery_finished)
        self._worker.failed.connect(self._on_recovery_failed)

        terminal = self._get_terminal_sidebar()
        if terminal is not None:
            terminal.clear()
            terminal.show()
            terminal.set_active(True)
            self._worker.event.connect(lambda event: self._log_event_to_terminal(event, terminal))

        self._worker.start()

    def _get_terminal_sidebar(self) -> TerminalSidebar | None:
        """Find the main window and get its terminal sidebar."""
        from PySide6.QtWidgets import QApplication
        from robo_rec.gui.main_window import MainWindow

        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, MainWindow):
                return widget.get_terminal_sidebar()
        return None

    def _log_event_to_terminal(self, event, terminal: TerminalSidebar) -> None:
        line, level = extract_log_from_event(event)
        if line is not None:
            terminal.log(line, level)

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
        self._hide_terminal_sidebar()
        self._cleanup_worker()
        self._show_result(result)

    def _on_recovery_failed(self, message: str) -> None:
        self._progress.stop()
        self._hide_terminal_sidebar()
        self._cleanup_worker()
        self._view_stack.setCurrentIndex(0)
        QMessageBox.critical(self, "Recovery failed to start", message)

    def _hide_terminal_sidebar(self) -> None:
        terminal = self._get_terminal_sidebar()
        if terminal is not None:
            terminal.set_active(False)
            terminal.hide()

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
            self._result_icon.setPixmap(load_pixmap("party-popper", ACCENT, 32))
            self._result_title.setText("Recovered your seed phrase")
            self._result_title.setObjectName("ResultTitleSuccess")
            self._result_subtitle.setText(
                "Every word below matches a valid, address-verified phrase."
            )
            self._result_subtitle.setObjectName("ResultSubtitleSuccess")
            self._seed_card.setObjectName("SuccessSeedCard")
            words = result.mnemonic.split()
            self._result_row.set_length(len(words))
            self._result_row.set_words(words)
            self._copy_button.set_text_to_copy(result.mnemonic)
            self._copy_button.setVisible(True)
        else:
            self._result_icon.setPixmap(load_pixmap("loader-circle", ACCENT, 22))
            title, subtitle = failure_text(
                result,
                not_found_title="No matching phrase found",
                not_found_subtitle=(
                    "Robo-Rec searched every combination in this range and none matched the "
                    "test address. Double-check the address and the words you entered."
                ),
            )
            self._result_title.setText(title)
            self._result_title.setObjectName("DashboardTitle")
            self._result_subtitle.setText(subtitle)
            self._result_subtitle.setObjectName("DashboardSubtitle")
            self._seed_card.setObjectName("")
            self._result_row.set_length(len(self._seed_row.tiles()))
            self._result_row.set_words(self._seed_row.words())
            self._copy_button.setVisible(False)

        for widget in (self._result_title, self._result_subtitle, self._seed_card):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

        self._view_stack.setCurrentIndex(2)

    def _reset_to_form(self) -> None:
        self._view_stack.setCurrentIndex(0)
        self._seed_row.focus_first_blank()

    def focus_first_word(self) -> None:
        if self._view_stack.currentIndex() == 0:
            self._seed_row.focus_first_blank()
