from robo_rec.recovery.args import (
    build_missing_word_known_position_args,
    build_missing_word_unknown_position_args,
    build_rearrangement_args,
    build_typo_correction_args,
)
from robo_rec.recovery.models import (
    MissingWordKnownPositionSpec,
    MissingWordUnknownPositionSpec,
    RearrangementSpec,
    TypoCorrectionSpec,
)

WORDS_12 = [
    "rotate", "dream", "drip", "opinion", "key", "dove",
    "region", "mind", "visit", "diesel", "negative", "speed",
]
ADDR = "1FMHvVtJkJFnSxaN9KUn5q3KtqNwej1sZ6"


def test_missing_word_known_position_uses_placeholder():
    words = WORDS_12.copy()
    words[4] = None
    spec = MissingWordKnownPositionSpec(words=words, wallet_type="bip39", addrs=[ADDR])
    argv = build_missing_word_known_position_args(spec)

    assert "--mnemonic" in argv
    mnemonic = argv[argv.index("--mnemonic") + 1]
    assert mnemonic == "rotate dream drip opinion %% dove region mind visit diesel negative speed"
    assert "--mnemonic-length" in argv
    assert argv[argv.index("--mnemonic-length") + 1] == "12"
    assert "--wallet-type" in argv
    assert argv[argv.index("--wallet-type") + 1] == "bip39"
    assert "--addrs" in argv
    assert argv[argv.index("--addrs") + 1] == ADDR


def test_missing_word_known_position_sets_typos_and_big_typos_to_missing_count():
    """Without both flags, btcrseed.py's big_typos budget defaults to 0 and goes negative
    for any missing word, so the search phase is skipped with "Not enough entirely
    different seed words permitted" — reporting "Seed not found" even for a correct
    phrase. Confirmed by direct testing against a known-good mnemonic/address pair."""
    words = WORDS_12.copy()
    words[4] = None
    words[7] = None
    spec = MissingWordKnownPositionSpec(words=words, wallet_type="bip39", addrs=[ADDR])
    argv = build_missing_word_known_position_args(spec)
    assert argv[argv.index("--typos") + 1] == "2"
    assert argv[argv.index("--big-typos") + 1] == "2"


def test_missing_word_known_position_budgets_unmatchable_known_word_too():
    """A known word that maps to no wordlist entry costs a big typo of its own inside
    btcrseed.py. Budgeting only for the blanks would make that phase abort instantly
    ("Not enough entirely different seed words permitted") on a single mistyped word."""
    words = WORDS_12.copy()
    words[4] = None
    words[6] = "zzzzzzzz"  # no close match anywhere in the wordlist
    spec = MissingWordKnownPositionSpec(words=words, wallet_type="bip39", addrs=[ADDR])
    argv = build_missing_word_known_position_args(spec)
    assert argv[argv.index("--big-typos") + 1] == "2"  # 1 blank + 1 unmatchable word
    assert argv[argv.index("--typos") + 1] == "2"


def test_missing_word_known_position_counts_close_spelling_as_regular_typo_only():
    """A misspelling that difflib still maps to a real word is corrected via a regular
    typo, not a full 2048-word search, so it must not inflate the --big-typos budget."""
    words = WORDS_12.copy()
    words[4] = None
    words[6] = "regionn"  # close match: "region"
    spec = MissingWordKnownPositionSpec(words=words, wallet_type="bip39", addrs=[ADDR])
    argv = build_missing_word_known_position_args(spec)
    assert argv[argv.index("--big-typos") + 1] == "1"  # the blank only
    assert argv[argv.index("--typos") + 1] == "2"  # blank + close-spelling correction


def test_missing_word_known_position_omits_gpu_flag_by_default():
    words = WORDS_12.copy()
    words[4] = None
    spec = MissingWordKnownPositionSpec(words=words, wallet_type="bip39", addrs=[ADDR])
    argv = build_missing_word_known_position_args(spec)
    assert "--enable-opencl" not in argv


def test_missing_word_known_position_passes_enable_opencl_when_gpu_requested():
    """--enable-opencl, not --enable-gpu: the latter requires init_opencl_kernel(), which
    WalletBIP39 doesn't implement (only WalletBitcoinCore does) and would error_exit
    immediately in btcrpass.py. See the module docstring for the full trace."""
    words = WORDS_12.copy()
    words[4] = None
    spec = MissingWordKnownPositionSpec(words=words, wallet_type="bip39", addrs=[ADDR])
    argv = build_missing_word_known_position_args(spec, use_gpu=True)
    assert "--enable-opencl" in argv
    assert "--enable-gpu" not in argv


def test_rearrangement_passes_enable_opencl_when_gpu_requested():
    known = [None] * 12
    known[0], known[1] = "rotate", "dream"
    scrambled = WORDS_12[2:]
    spec = RearrangementSpec(
        known_words=known, scrambled_words=scrambled, wallet_type="bip39", addrs=[ADDR]
    )
    argv, tokenlist_path = build_rearrangement_args(spec, use_gpu=True)
    try:
        assert "--enable-opencl" in argv
    finally:
        tokenlist_path.unlink()


def test_typo_correction_passes_enable_opencl_when_gpu_requested():
    spec = TypoCorrectionSpec(
        best_guess_mnemonic=" ".join(WORDS_12), wallet_type="bip39", addrs=[ADDR]
    )
    argv = build_typo_correction_args(spec, use_gpu=True)
    assert "--enable-opencl" in argv


def test_missing_word_unknown_position_omits_word_and_sets_length():
    words = WORDS_12.copy()
    del words[4]  # omit "key" entirely — no placeholder
    spec = MissingWordUnknownPositionSpec(
        words=words, full_length=12, wallet_type="bip39", addrs=[ADDR]
    )
    argv = build_missing_word_unknown_position_args(spec)

    mnemonic = argv[argv.index("--mnemonic") + 1]
    assert mnemonic == "rotate dream drip opinion dove region mind visit diesel negative speed"
    assert "%%" not in mnemonic
    assert argv[argv.index("--mnemonic-length") + 1] == "12"
    assert argv[argv.index("--typos") + 1] == "1"
    assert argv[argv.index("--big-typos") + 1] == "1"


def test_missing_word_unknown_position_passes_enable_opencl_when_gpu_requested():
    words = WORDS_12.copy()
    del words[4]
    spec = MissingWordUnknownPositionSpec(
        words=words, full_length=12, wallet_type="bip39", addrs=[ADDR]
    )
    argv = build_missing_word_unknown_position_args(spec, use_gpu=True)
    assert "--enable-opencl" in argv


def test_typo_correction_passes_full_mnemonic_as_is():
    mnemonic = " ".join(WORDS_12)
    spec = TypoCorrectionSpec(best_guess_mnemonic=mnemonic, wallet_type="bip39", addrs=[ADDR])
    argv = build_typo_correction_args(spec)

    assert argv[argv.index("--mnemonic") + 1] == mnemonic
    assert "--mnemonic-length" not in argv
    assert "%%" not in mnemonic


def test_typo_correction_forwards_optional_tuning_flags():
    spec = TypoCorrectionSpec(
        best_guess_mnemonic=" ".join(WORDS_12),
        wallet_type="bip39",
        addrs=[ADDR],
        typos=2,
        big_typos=1,
        close_match=0.7,
    )
    argv = build_typo_correction_args(spec)

    assert argv[argv.index("--typos") + 1] == "2"
    assert argv[argv.index("--big-typos") + 1] == "1"
    assert argv[argv.index("--close-match") + 1] == "0.7"


def test_rearrangement_builds_tokenlist_and_argv():
    known = [None] * 12
    known[0], known[1] = "rotate", "dream"
    scrambled = WORDS_12[2:]
    spec = RearrangementSpec(
        known_words=known, scrambled_words=scrambled, wallet_type="bip39", addrs=[ADDR]
    )
    argv, tokenlist_path = build_rearrangement_args(spec)
    try:
        assert "--tokenlist" in argv
        assert argv[argv.index("--tokenlist") + 1] == str(tokenlist_path)
        assert "--keep-tokens-order" not in argv
        assert "--mnemonic" not in argv
        # --tokenlist mode requires both explicitly (seedrecover.py can't infer them from
        # the tokenlist file itself — confirmed by direct terminal testing).
        assert argv[argv.index("--mnemonic-length") + 1] == "12"
        assert argv[argv.index("--language") + 1] == "en"

        content = tokenlist_path.read_text()
        lines = content.splitlines()
        assert "^1^rotate" in lines
        assert "^2^dream" in lines
        for word in scrambled:
            assert word in lines
    finally:
        tokenlist_path.unlink()


def _all_builders_argv():
    known = MissingWordKnownPositionSpec(
        words=["abandon"] * 11 + [None], wallet_type="bip39", addrs=["x"]
    )
    unknown = MissingWordUnknownPositionSpec(
        words=["abandon"] * 11, full_length=12, wallet_type="bip39", addrs=["x"]
    )
    typo = TypoCorrectionSpec(
        best_guess_mnemonic="abandon " * 11 + "about", wallet_type="bip39", addrs=["x"]
    )
    rearr = RearrangementSpec(
        known_words=["abandon"] * 10 + [None, None],
        scrambled_words=["about", "zoo"],
        wallet_type="bip39",
        addrs=["x"],
    )
    rearr_argv, tokenlist = build_rearrangement_args(rearr)
    tokenlist.unlink()
    return [
        build_missing_word_known_position_args(known),
        build_missing_word_unknown_position_args(unknown),
        build_typo_correction_args(typo),
        rearr_argv,
    ]


def test_every_scenario_disables_the_duplicate_checker_exactly_once():
    # Regression: explicit --typos/--big-typos skips seedrecover's own phase ladder, which is
    # what normally adds --no-dupchecks; without it a 3-blank search builds a ~1.5 TB dict while
    # counting candidates and the compiled exe segfaults. Once (not twice) keeps Rearrange's
    # separate token-combination dup check, which needs < 2.
    for argv in _all_builders_argv():
        assert argv.count("--no-dupchecks") == 1
