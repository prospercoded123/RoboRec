"""RecoverySpec variants and the event/result types BtcrecoverRunner streams back.

Every RecoverySpec variant requires at least one of `addrs`/`mpk` (btcrecover's own hard
requirement — see robo-rec-implementation.md Section 2). The --listseeds/no-address
checksum-only search path is intentionally not modeled here (out of scope for this pass;
see the approved plan's Context section).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from robo_rec.recovery.exceptions import InvalidSpecError


def _validate_target(addrs: list[str] | None, mpk: str | None) -> None:
    if not addrs and not mpk:
        raise InvalidSpecError(
            "At least one of addrs or mpk is required to verify a recovery search "
            "(btcrecover cannot run without a target to check candidates against)."
        )


@dataclass(frozen=True)
class RearrangementSpec:
    """PRD 4.1 — 12-word full scramble, or 24-word known-segment + scrambled remainder.

    known_words: full-length list (12 or 24), positions holding a known-correct word are
    the actual word string, positions that are part of the scrambled segment are None.
    scrambled_words: the words known to belong somewhere in the scrambled segment (their
    exact position is what's being searched for).
    """

    known_words: list[str | None]
    scrambled_words: list[str]
    wallet_type: str
    addrs: list[str] | None = None
    mpk: str | None = None
    addr_limit: int = 5

    def __post_init__(self) -> None:
        _validate_target(self.addrs, self.mpk)
        num_blank = sum(1 for w in self.known_words if w is None)
        if num_blank != len(self.scrambled_words):
            raise InvalidSpecError(
                f"known_words has {num_blank} blank slot(s) but scrambled_words has "
                f"{len(self.scrambled_words)} word(s) — they must match."
            )
        if len(self.known_words) not in (12, 24):
            raise InvalidSpecError("known_words must be length 12 or 24 (PRD Section 4.1).")


@dataclass(frozen=True)
class MissingWordKnownPositionSpec:
    """PRD 4.2 (known positions) — 1-4 missing words, position(s) specified by the caller."""

    words: list[str | None]  # full-length list; None marks a known blank position
    wallet_type: str
    addrs: list[str] | None = None
    mpk: str | None = None
    addr_limit: int = 5

    def __post_init__(self) -> None:
        _validate_target(self.addrs, self.mpk)
        if len(self.words) not in (12, 24):
            raise InvalidSpecError("words must be length 12 or 24 (PRD Section 4.2).")
        num_missing = sum(1 for w in self.words if w is None)
        if not 1 <= num_missing <= 4:
            raise InvalidSpecError(
                f"Known-position missing-word recovery supports 1-4 missing words "
                f"(PRD v1.1 Section 4.2); got {num_missing}."
            )


@dataclass(frozen=True)
class MissingWordUnknownPositionSpec:
    """PRD 4.2 (unknown positions) — 1-2 missing words only; position(s) unknown, so
    btcrecover must also search over placement. 3+ is out of scope (PRD v1.1 Non-Goals)."""

    words: list[str]  # shorter than full_length by the number of missing words
    full_length: int
    wallet_type: str
    addrs: list[str] | None = None
    mpk: str | None = None
    addr_limit: int = 5

    def __post_init__(self) -> None:
        _validate_target(self.addrs, self.mpk)
        if self.full_length not in (12, 24):
            raise InvalidSpecError("full_length must be 12 or 24 (PRD Section 4.2).")
        num_missing = self.full_length - len(self.words)
        if not 1 <= num_missing <= 2:
            raise InvalidSpecError(
                "Unknown-position missing-word recovery supports 1-2 missing words only "
                f"(PRD v1.1 Section 4.2 — 3+ unknown-position combinations exceed the "
                f"5+ known-position infeasibility bar); got {num_missing}."
            )


@dataclass(frozen=True)
class TypoCorrectionSpec:
    """PRD 4.3 — full best-guess mnemonic, no blanks; btcrecover's typo engine searches
    for close-word/wrong-word substitutions."""

    best_guess_mnemonic: str
    wallet_type: str
    addrs: list[str] | None = None
    mpk: str | None = None
    typos: int | None = None
    big_typos: int | None = None
    close_match: float | None = None
    addr_limit: int = 5

    def __post_init__(self) -> None:
        _validate_target(self.addrs, self.mpk)
        if not self.best_guess_mnemonic.strip():
            raise InvalidSpecError("best_guess_mnemonic must not be empty.")


RecoverySpec = (
    RearrangementSpec
    | MissingWordKnownPositionSpec
    | MissingWordUnknownPositionSpec
    | TypoCorrectionSpec
)


RecoveryEventKind = Literal[
    "started", "phase", "eta", "found", "not_found", "error", "log", "finished"
]


@dataclass(frozen=True)
class RecoveryResult:
    mnemonic: str | None
    matched_address: str | None
    matched_path: str | None
    return_code: int
    succeeded: bool
    # Set when the engine did NOT finish normally (crash, out-of-memory, bad arguments, ...).
    # A search that genuinely ran to exhaustion and found nothing has error=None — callers must
    # never present an error as "no match found".
    error: str | None = None
    cancelled: bool = False


@dataclass(frozen=True)
class RecoveryEvent:
    kind: RecoveryEventKind
    message: str
    raw_line: str | None = None
    phase_current: int | None = None
    phase_total: int | None = None
    eta_seconds: int | None = None
    result: RecoveryResult | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
