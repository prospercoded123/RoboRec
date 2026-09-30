"""Which source produced this running app — for the Diagnostics export.

Compiled builds read the module scripts/write_build_info.py generated at build time. Running
from source has no such file, so the same facts are read live from git instead (and labelled as
such, since a dev checkout can change after launch). If neither is possible the report says
"unavailable" rather than guessing, so nobody mistakes missing data for a match.
"""

from __future__ import annotations

import importlib
import subprocess
from functools import lru_cache
from typing import Any

from robo_rec.util.paths import is_compiled, repo_root


def _git(*args: str, cwd) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = result.stdout.strip()
    return out if result.returncode == 0 and out else None


def _from_git() -> dict[str, Any] | None:
    try:
        root = repo_root()
    except FileNotFoundError:
        return None
    commit = _git("rev-parse", "HEAD", cwd=root)
    if commit is None:
        return None
    return {
        "source": "git-at-runtime",
        "app_commit": commit,
        "app_commit_short": commit[:10],
        "app_branch": _git("rev-parse", "--abbrev-ref", "HEAD", cwd=root),
        "app_dirty": bool(_git("status", "--porcelain", "--untracked-files=no", cwd=root)),
        "btcrecover_commit": _git("rev-parse", "HEAD", cwd=root / "vendor" / "btcrecover"),
    }


@lru_cache(maxsize=1)
def get_build_info() -> dict[str, Any]:
    # Running from source: git is authoritative. A leftover _build_info.py from an earlier build
    # would otherwise report a stale commit for code that has since changed.
    if not is_compiled():
        from_git = _from_git()
        if from_git is not None:
            return from_git
    try:
        module = importlib.import_module("robo_rec._build_info")
        return {"source": "build-time", **module.BUILD_INFO}
    except (ImportError, AttributeError):
        pass
    return {
        "source": "unavailable",
        "note": "No build stamp in this build and no git checkout to read — the exact source "
        "of this build can't be determined.",
    }


__all__ = ["get_build_info"]
