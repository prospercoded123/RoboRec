"""Build identity for the Diagnostics export (scripts/write_build_info.py + util.build_info)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from robo_rec.util import build_info

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "write_build_info.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("write_build_info", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["write_build_info"] = module
    spec.loader.exec_module(module)
    return module


def test_generator_collects_every_field_and_never_raises():
    info = _load_script().collect()
    assert set(info) == {
        "app_commit", "app_commit_short", "app_branch", "app_dirty",
        "btcrecover_commit", "built_at_utc", "built_with_python",
    }
    assert info["app_commit_short"] == info["app_commit"][:10] or info["app_commit"] == "unknown"


def test_generated_module_is_importable_python(tmp_path, monkeypatch):
    module = _load_script()
    monkeypatch.setattr(module, "OUTPUT", tmp_path / "_build_info.py")
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    assert module.main() == 0
    namespace: dict = {}
    exec((tmp_path / "_build_info.py").read_text(encoding="utf-8"), namespace)
    assert "app_commit" in namespace["BUILD_INFO"]


def test_from_source_prefers_live_git_over_a_stale_build_stamp(monkeypatch):
    build_info.get_build_info.cache_clear()
    monkeypatch.setattr(build_info, "is_compiled", lambda: False)
    monkeypatch.setattr(build_info, "_from_git", lambda: {"source": "git-at-runtime", "x": 1})
    assert build_info.get_build_info()["source"] == "git-at-runtime"
    build_info.get_build_info.cache_clear()


def test_compiled_without_a_stamp_says_unavailable_instead_of_guessing(monkeypatch):
    build_info.get_build_info.cache_clear()
    monkeypatch.setattr(build_info, "is_compiled", lambda: True)
    monkeypatch.setitem(sys.modules, "robo_rec._build_info", None)  # forces ImportError
    info = build_info.get_build_info()
    assert info["source"] == "unavailable"
    build_info.get_build_info.cache_clear()
