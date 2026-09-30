"""Pure argv-builder functions, one per PRD recovery scenario. No subprocess involved here —
these just construct the seedrecover.py CLI argument list, so they're independently testable
against the exact strings already validated by hand (robo-rec-implementation.md).

All builders always pass --wallet-type explicitly (never rely on address-prefix
auto-detection — robo-rec-implementation.md Section 3) and --no-gui/--dsw so the subprocess
never blocks on a Tk dialog or security-warning prompt.

GPU acceleration (PRD 4.5) uses --enable-opencl, NOT --enable-gpu: btcrpass.py's
--enable-gpu path requires the loaded wallet class to implement init_opencl_kernel(), which
only WalletBitcoinCore has (wallet.dat recovery — out of scope per PRD Section 3) — passing
it for a BIP39 seed recovery would hit btcrpass.py's error_exit() immediately. --enable-opencl
is the separate, correct flag for BIP39/Electrum seed recovery (btcrseed.py ~line 4829's own
help text: "only supports BIP39 (for supported coin) and Electrum wallets"), verified via
WalletBIP32.return_verified_password_or_false()'s opencl branch, which WalletBIP39 inherits.
Device selection is left to btcrecover's own auto-select (no --opencl-platform/--opencl-devices
passed) since gpu/opencl_probe.py's device parsing is best-effort and unverified on real
hardware (see that module's docstring).

Every builder takes use_gpu explicitly rather than reading robo_rec.gui.gpu_state directly —
this module has no PySide6/GUI dependency by design (mirrors runner.py's own "Qt-agnostic"
boundary), so the GUI layer (recovery_worker.py) is what decides use_gpu from the real probe
result and passes it in.
"""

from __future__ import annotations

import os
from pathlib import Path

from robo_rec.recovery.models import (
    MissingWordKnownPositionSpec,
    MissingWordUnknownPositionSpec,
    RearrangementSpec,
    TypoCorrectionSpec,
)
from robo_rec.recovery.tokenlist import build_tokenlist_file
from robo_rec.util.mnemonic import close_words, is_valid_word

# btcrecover defaults --threads to the full logical core count, which pegs every core at
# 100% for the run's whole duration (hours, per PRD 4.1/4.2) and can starve the OS/GUI badly
# enough to crash the system. Reserve one logical core so the machine stays responsive;
# btcrecover only ever lowers this further itself (e.g. by VRAM budget on GPU), never raises it.
_WORKER_THREADS = max(1, (os.cpu_count() or 1) - 1)

# --no-dupchecks (passed once) turns off btcrpass's password DuplicateChecker, which remembers
# every candidate it has generated in a Python dict (~200 bytes each) for the whole first pass.
# That's ~0.8 GB at 2 blanks but ~1.5 TB at 3 blanks (2048^3 candidates), and it runs during the
# up-front counting pass, so a 3+ blank search dies before it starts — in the compiled build as a
# raw Nuitka segfault, not a MemoryError btcrseed's handle_oom() could catch.
#
# seedrecover's own default phase ladder adds this flag itself at big_typos=2, but passing an
# explicit --typos/--big-typos (which _typo_flags does, to reach 3-4 blanks) skips that ladder and
# silently drops the flag with it. Blanks are filled from distinct wordlist entries, so there are
# essentially no duplicates to catch; at worst a repeat is verified twice, never missed. A single
# flag keeps the separate, much smaller token-combination dup check Rearrange still benefits from.
_COMMON_FLAGS = ["--no-gui", "--dsw", "--no-dupchecks", "--threads", str(_WORKER_THREADS)]


# Wallet classes that implement btcrseed.py's _return_verified_password_or_false_opencl
# (WalletBIP32 and its subclasses WalletBIP39/WalletEthereum). WalletSolana
# (WalletPyCryptoHDWallet) has no OpenCL path at all — passing --enable-opencl for it makes
# seedrecover.py's own arg-parsing call btcrpass.error_exit("... does not support OpenCL
# acceleration") and exit immediately, before any candidates are even generated. Confirmed by
# direct testing: a GPU-requested Solana search "succeeds" (subprocess exits 0) in ~2s with no
# result, which looks identical to a real exhausted search from the GUI's side — so this can't
# be left to fail loudly on its own; it must simply never be requested.
_OPENCL_CAPABLE_WALLET_TYPES = {"bip39", "ethereum"}


def _gpu_flags(use_gpu: bool, wallet_type: str) -> list[str]:
    if use_gpu and wallet_type in _OPENCL_CAPABLE_WALLET_TYPES:
        return ["--enable-opencl"]
    return []


def _typo_flags(num_blanks: int, known_words: list[str]) -> list[str]:
    """--typos/--big-typos sized to what btcrseed.py's run_btcrecover() actually requires.

    It models two separate budgets, and a phase is skipped outright (printing "Not enough
    entirely different seed words permitted" and reporting "Seed not found") if either goes
    negative:

      * big_typos — spent on each blank/insert and on each word it can't map to any wordlist
        entry, i.e. anything needing a full 2048-word search. Defaults to 0 and is NOT raised
        by passing --typos alone, so omitting --big-typos makes any blank overrun it.
      * typos — the total budget, which also covers close-spelling corrections.

    Left unspecified, seedrecover falls back to an auto ladder of phases capping at
    big_typos=2, so 3-4 known-position blanks (which PRD 4.2 supports) could never succeed.
    Known words are inspected here because a word that matches nothing in the wordlist costs
    a big typo of its own — budgeting only for the blanks would make one mistyped word abort
    the whole search instantly.
    """
    unmatchable = sum(
        1 for word in known_words if not is_valid_word(word) and not close_words(word)
    )
    close_only = sum(1 for word in known_words if not is_valid_word(word) and close_words(word))
    big_typos = num_blanks + unmatchable
    return ["--typos", str(big_typos + close_only), "--big-typos", str(big_typos)]


def _target_flags(addrs: list[str] | None, mpk: str | None, addr_limit: int) -> list[str]:
    flags: list[str] = []
    if addrs:
        flags += ["--addrs", *addrs]
    if mpk:
        flags += ["--mpk", mpk]
    flags += ["--addr-limit", str(addr_limit)]
    return flags


def build_rearrangement_args(
    spec: RearrangementSpec, *, use_gpu: bool = False
) -> tuple[list[str], Path]:
    """Returns (argv, tokenlist_path). The caller owns deleting tokenlist_path once the
    subprocess has exited."""
    tokenlist_path = build_tokenlist_file(
        known_words=spec.known_words, scrambled_words=spec.scrambled_words
    )
    argv = [
        *_COMMON_FLAGS,
        *_gpu_flags(use_gpu, spec.wallet_type),
        "--wallet-type",
        spec.wallet_type,
        "--tokenlist",
        str(tokenlist_path),
        # --tokenlist mode cannot infer phrase length or wordlist language on its own;
        # seedrecover.py exits immediately with "Error: Mnemonic length needs to be
        # specificed if using tokenlist or passwordlist" (and then, once that's fixed,
        # the equivalent error for --language) without these (confirmed by direct
        # terminal testing — see robo-rec-implementation.md).
        "--mnemonic-length",
        str(len(spec.known_words)),
        "--language",
        "en",
        *_target_flags(spec.addrs, spec.mpk, spec.addr_limit),
        # Deliberately NOT passing --keep-tokens-order: permutation of the unanchored
        # (scrambled) tokens is the whole point of this scenario.
    ]
    return argv, tokenlist_path


def build_missing_word_known_position_args(
    spec: MissingWordKnownPositionSpec, *, use_gpu: bool = False
) -> list[str]:
    mnemonic = " ".join(word if word is not None else "%%" for word in spec.words)
    num_missing = sum(1 for word in spec.words if word is None)
    return [
        *_COMMON_FLAGS,
        *_gpu_flags(use_gpu, spec.wallet_type),
        "--wallet-type",
        spec.wallet_type,
        "--mnemonic",
        mnemonic,
        "--mnemonic-length",
        str(len(spec.words)),
        *_typo_flags(num_missing, [word for word in spec.words if word is not None]),
        *_target_flags(spec.addrs, spec.mpk, spec.addr_limit),
    ]


def build_missing_word_unknown_position_args(
    spec: MissingWordUnknownPositionSpec, *, use_gpu: bool = False
) -> list[str]:
    """words is intentionally SHORTER than full_length (missing word(s) simply omitted, not
    '%%') — this is what makes btcrecover search over position as well as word (see
    robo-rec-implementation.md Section 6.2)."""
    mnemonic = " ".join(spec.words)
    num_missing = spec.full_length - len(spec.words)
    return [
        *_COMMON_FLAGS,
        *_gpu_flags(use_gpu, spec.wallet_type),
        "--wallet-type",
        spec.wallet_type,
        "--mnemonic",
        mnemonic,
        "--mnemonic-length",
        str(spec.full_length),
        # Each omitted word is an insert, which draws on the same two budgets a blank does.
        *_typo_flags(num_missing, list(spec.words)),
        *_target_flags(spec.addrs, spec.mpk, spec.addr_limit),
    ]


def build_typo_correction_args(spec: TypoCorrectionSpec, *, use_gpu: bool = False) -> list[str]:
    argv = [
        *_COMMON_FLAGS,
        *_gpu_flags(use_gpu, spec.wallet_type),
        "--wallet-type",
        spec.wallet_type,
        "--mnemonic",
        spec.best_guess_mnemonic,
        *_target_flags(spec.addrs, spec.mpk, spec.addr_limit),
    ]
    if spec.typos is not None:
        argv += ["--typos", str(spec.typos)]
    if spec.big_typos is not None:
        argv += ["--big-typos", str(spec.big_typos)]
    if spec.close_match is not None:
        argv += ["--close-match", str(spec.close_match)]
    return argv
