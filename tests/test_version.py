"""版本号：发版清单里「四处一致」这件事，让机器来记，别靠人眼。

`meow/__init__.py` / `pyproject.toml` / `uv.lock` / `CHANGELOG.md` 四处对不上，
用户就会看到「面板显示 0.2.5、exe 是 0.3.0」这种没人能解释的现象。
"""
from __future__ import annotations

import re
from pathlib import Path

from meow import __version__

ROOT = Path(__file__).resolve().parent.parent


def test_pyproject_matches_package():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "pyproject.toml 里没找到 version"
    assert match.group(1) == __version__


def test_uv_lock_matches_package():
    text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    match = re.search(
        r'\[\[package\]\]\nname = "meow-box"\nversion = "([^"]+)"', text
    )
    assert match, "uv.lock 里没找到 meow-box 的版本（跑一下 uv lock）"
    assert match.group(1) == __version__


def test_changelog_has_an_entry_for_this_version():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{__version__}]" in text, f"CHANGELOG 里没有 {__version__} 那一节"


def test_changelog_keeps_an_unreleased_section():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [未发布]" in text


def test_license_copyright_matches_authors():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "玛莉嘉" in text and "于光远" in text
