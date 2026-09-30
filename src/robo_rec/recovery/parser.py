"""Turns seedrecover.py stdout lines into RecoveryEvent objects.

Regexes below are matched against real captured output (see robo-rec-implementation.md and
this session's own terminal verification), e.g.:

    2026-08-19 00:03:24 : Phase 2/4: 1 mistake which can be an entirely different seed word.
    Will try 2,048 passwords, ETA 1 seconds ...
    2026-08-19 00:03:24 : ***MATCHING SEED FOUND***, Matched on Address at derivation path: m/44'/0'/0'/0/0
    Seed found: rotate dream drip opinion key dove region mind visit diesel negative speed
     Seed not found, sorry...

btcrecover auto-disables its live progress bar when stdout isn't a tty (--no-progress
defaults to True), so there is no fine-grained percentage stream to parse — only phase
transitions, one ETA line per phase, and a final found/not-found line. Callers should present
progress as phase-based, not a smooth bar.
"""

from __future__ import annotations

import re

from robo_rec.recovery.models import RecoveryEvent, RecoveryResult

_PHASE_RE = re.compile(r"Phase (\d+)/(\d+): (.+)")
_ETA_RE = re.compile(r"Will try ([\d,]+) passwords, ETA (.+?) \.\.\.")
_FOUND_RE = re.compile(r"\*\*\*MATCHING SEED FOUND\*\*\*, (.+)")
_SEED_FOUND_RE = re.compile(r"Seed found: (.+)")
_NOT_FOUND_RE = re.compile(r"\s*Seed not found")
_MATCH_PATH_RE = re.compile(r"Matched on Address at derivation path: (\S+)")
# Lines that mean the engine itself failed, as opposed to a search that completed without a hit:
#   "Error: ..."          btcrpass.error_exit() / handle_oom() ("Error: out of memory",
#                         "Error: at least N passwords to try, ETA > --max-eta ...")
#   "Nuitka: A segmentation fault ..."  the compiled seedrecover.exe dying at C level (e.g. when
#                         memory runs out before Python can raise MemoryError)
#   "Traceback (most ..."  an unhandled Python exception
_ERROR_RE = re.compile(
    r"^(?:Error:|Nuitka: A segmentation fault|Traceback \(most recent call last\))"
)


def _eta_to_seconds(eta_text: str) -> int | None:
    """Parses '1 seconds', '3 minutes', '2 hours 5 minutes 1 seconds', etc."""
    total = 0
    matched = False
    for value, unit in re.findall(r"(\d+)\s*(hour|minute|second)s?", eta_text):
        matched = True
        total += int(value) * {"hour": 3600, "minute": 60, "second": 1}[unit]
    return total if matched else None


def parse_line(line: str) -> RecoveryEvent:
    """Classifies a single line of seedrecover.py output. Unrecognized lines become
    kind='log' events so nothing is silently dropped — the GUI can choose to show or hide
    raw log lines independent of the higher-level phase/eta/found events."""
    stripped = line.strip()

    if _ERROR_RE.match(stripped):
        return RecoveryEvent(kind="error", message=stripped, raw_line=line)

    if match := _PHASE_RE.search(stripped):
        current, total, detail = match.groups()
        return RecoveryEvent(
            kind="phase",
            message=f"Phase {current}/{total}: {detail}",
            raw_line=line,
            phase_current=int(current),
            phase_total=int(total),
        )

    if match := _ETA_RE.search(stripped):
        count_text, eta_text = match.groups()
        return RecoveryEvent(
            kind="eta",
            message=f"Will try {count_text} passwords, ETA {eta_text}",
            raw_line=line,
            eta_seconds=_eta_to_seconds(eta_text),
        )

    if match := _FOUND_RE.search(stripped):
        detail = match.group(1)
        path_match = _MATCH_PATH_RE.search(detail)
        return RecoveryEvent(
            kind="found",
            message=detail,
            raw_line=line,
            result=RecoveryResult(
                mnemonic=None,  # populated once the "Seed found: ..." line arrives
                matched_address=None,
                matched_path=path_match.group(1) if path_match else None,
                return_code=0,
                succeeded=True,
            ),
        )

    if match := _SEED_FOUND_RE.search(stripped):
        mnemonic = match.group(1).strip()
        return RecoveryEvent(
            kind="found",
            message=f"Seed found: {mnemonic}",
            raw_line=line,
            result=RecoveryResult(
                mnemonic=mnemonic,
                matched_address=None,
                matched_path=None,
                return_code=0,
                succeeded=True,
            ),
        )

    if _NOT_FOUND_RE.match(line):
        return RecoveryEvent(kind="not_found", message=stripped or "Seed not found", raw_line=line)

    return RecoveryEvent(kind="log", message=stripped, raw_line=line)
