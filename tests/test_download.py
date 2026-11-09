"""下载的完整性校验：哈希对不上就不许解压。

这里**不联网**：`fetch()` 在缓存命中时会直接返回，所以拿一个现成的文件就能
把「下载完 → 校验 → 失败删文件」这条路走一遍。
"""
from __future__ import annotations

import hashlib

import pytest

from meow import download

PAYLOAD = b"pretend-to-be-a-zip-archive"
GOOD = hashlib.sha256(PAYLOAD).hexdigest()


def test_sha256_of_matches_hashlib(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(PAYLOAD)
    assert download.sha256_of(f) == GOOD


def test_check_sha256_accepts_uppercase_and_spaces(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(PAYLOAD)
    assert download.check_sha256(f, f"  {GOOD.upper()} ") == GOOD


def test_check_sha256_rejects_a_different_file(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(PAYLOAD + b"tampered")
    with pytest.raises(ValueError) as exc:
        download.check_sha256(f, GOOD)
    assert GOOD in str(exc.value)          # 报错里要能直接看到两个哈希


def test_cached_file_is_verified_not_trusted(tmp_path):
    """缓存命中也要校验：缓存里可能是上次下坏的半截文件。"""
    dest = tmp_path / "pkg.zip"
    dest.write_bytes(PAYLOAD)
    quiet = lambda _m: None                     # noqa: E731

    assert download.fetch("https://example.invalid/pkg.zip", dest, say=quiet,
                          expected_sha256=GOOD) == dest

    with pytest.raises(ValueError):
        download.fetch("https://example.invalid/pkg.zip", dest, say=quiet,
                       expected_sha256="0" * 64)
    assert not dest.exists(), "校验失败的文件必须删掉，否则下次缓存命中还会拿它解压"


def test_unpinned_download_prints_the_hash(tmp_path):
    """没写 sha256 时不拦，但要打印实际哈希 —— 用户复制进 packages.conf 就锁上了。"""
    dest = tmp_path / "pkg.zip"
    dest.write_bytes(PAYLOAD)
    said: list[str] = []
    download.fetch("https://example.invalid/pkg.zip", dest, say=said.append)
    assert GOOD in "\n".join(said)
    assert "packages.conf" in "\n".join(said)
