"""自签证书：根私钥的权限位（多用户机器上这是真实安全问题）。

`openssl -keyout` 按 umask 建文件，umask 022 时是 0644 —— 同一台机器上的
任何用户都能读走根私钥，然后给任意 `*.meow` 域名签一张浏览器信任的假证书。
`_lock_down()` 就是为这一步存在的，所以这里钉住它。
"""
from __future__ import annotations

import os

import pytest

from meow import plat, ssl


@pytest.mark.skipif(os.name == "nt", reason="Windows 的权限由 ACL 管，没有 POSIX 权限位")
def test_private_key_becomes_owner_only(tmp_path):
    key = tmp_path / "ca.key"
    key.write_text("pretend-private-key", encoding="utf-8")
    key.chmod(0o644)

    ssl._lock_down(key)

    assert key.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="同上")
def test_lock_down_is_idempotent_and_survives_missing_file(tmp_path):
    key = tmp_path / "a.key"
    key.write_text("x", encoding="utf-8")
    ssl._lock_down(key)
    ssl._lock_down(key)
    assert key.stat().st_mode & 0o777 == 0o600

    # 文件不在了也不许抛（签发流程不该因为这一步炸）
    ssl._lock_down(tmp_path / "not-there.key")


def test_windows_is_a_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(plat, "IS_WINDOWS", True)
    key = tmp_path / "ca.key"
    key.write_text("x", encoding="utf-8")
    key.chmod(0o644)
    ssl._lock_down(key)
    if os.name != "nt":
        assert key.stat().st_mode & 0o777 == 0o644   # 没被改
