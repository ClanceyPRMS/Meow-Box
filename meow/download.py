"""下载小工具：只用标准库，冻结成 exe 也不缺东西。

为什么不用 `curl` / `unzip`：
* curl 在 Windows 10 1803+ 才有，旧机器上没有；
* `unzip` 根本不是 Windows 自带程序（这台机器上那个是 Git Bash 带进来的）。
  exe 发出去以后 PATH 里没有它，`pkg install` 就哑了。

所以下载走 urllib、解压走 zipfile/tarfile，一套代码到处能跑。
SSL 出错时再退回系统 curl（有时候 Python 的证书链不如系统的全）。
"""
from __future__ import annotations

import os
import shutil
import tarfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from .util import ensure_dir, fwd, run

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def fetch_text(url: str, timeout: int = 60) -> str:
    """取一个网页/目录列表的文本。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def fetch(url: str, dest, say=print, timeout: int = 900) -> Path:
    """下载到 dest。已存在且非空就跳过（缓存）。"""
    dest = Path(dest)
    ensure_dir(dest.parent)
    if dest.is_file() and dest.stat().st_size > 0:
        say(f"  缓存里已经有 {dest.name}（{dest.stat().st_size // 1024 // 1024} MB），跳过下载")
        return dest

    say(f"  下载 {url}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        tmp = dest.with_suffix(dest.suffix + ".part")
        got = 0
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0)
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                got += len(chunk)
                if total and got % (8 * 1024 * 1024) < 256 * 1024:
                    say(f"    {got // 1024 // 1024}/{total // 1024 // 1024} MB")
        tmp.replace(dest)
    except Exception as exc:                                  # noqa: BLE001
        say(f"  ⚠️  urllib 下载失败（{exc}），改用系统 curl 再试一次")
        rc, out, err = run(["curl", "-L", "--fail", "--retry", "2",
                            "-o", fwd(dest), url], timeout=timeout)
        if rc != 0:
            raise RuntimeError(f"下载失败：{(err or out).strip()}") from exc
    size = dest.stat().st_size
    say(f"  完成：{dest.name}（{size // 1024 // 1024} MB）")
    return dest


def extract(archive, dest, say=print) -> Path:
    """按后缀解压 zip / tar.*。返回解压目录。"""
    archive = Path(archive)
    dest = ensure_dir(dest)
    lower = archive.name.lower()
    if lower.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)
    elif lower.endswith((".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar")):
        with tarfile.open(archive) as tf:
            tf.extractall(dest)
    else:
        raise RuntimeError(f"不认识这种压缩包：{archive.name}")
    say(f"  解压到 {dest}")
    return dest


def move_tree(src, dest, say=print) -> Path:
    """把解压出来的一层目录搬到最终位置（目标存在就整体替换）。"""
    src, dest = Path(src), Path(dest)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    ensure_dir(dest.parent)
    shutil.move(str(src), str(dest))
    say(f"  装到 {dest}")
    return dest


def rmtree(path) -> None:
    shutil.rmtree(Path(path), ignore_errors=True)


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def env_has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def path_of(cmd: str) -> str | None:
    return shutil.which(cmd)


def _noop() -> None:
    pass


if os.name != "nt":                       # pragma: no cover - 只为可读性
    _noop()

__all__ = ["fetch", "fetch_text", "extract", "move_tree", "rmtree", "human"]
