"""hosts 文件管理。

喵盒用**成对标记**圈住自己加的行：只动 `# >>> meowbox` 和
`# <<< meowbox` 之间的内容，你手写的条目一个字都不会碰。
（Laragon 是逐行打 `#laragon magic!` 标签，删起来容易漏。）
"""
from __future__ import annotations

import os
from pathlib import Path

from .util import is_admin

BEGIN = "# >>> meowbox (喵盒) 以下条目由喵盒自动维护，请勿手改"
END = "# <<< meowbox"
_OPEN = "# >>> meowbox"
_CLOSE = "# <<< meowbox"


def default_path() -> Path:
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        return Path(system_root) / "System32" / "drivers" / "etc" / "hosts"
    return Path("/etc/hosts")


def read(path) -> str:
    path = Path(path)
    if not path.is_file():
        return ""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def strip_block(text: str) -> str:
    """把喵盒自己的那块整段删掉，返回剩下的原文。"""
    kept, inside = [], False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(_OPEN):
            inside = True
            continue
        if s.startswith(_CLOSE):
            inside = False
            continue
        if not inside:
            kept.append(line)
    return "\n".join(kept).rstrip("\n")


def current_hostnames(text: str) -> list[str]:
    """读出喵盒当前在 hosts 里登记的域名。"""
    found, inside = [], False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(_OPEN):
            inside = True
            continue
        if s.startswith(_CLOSE):
            inside = False
            continue
        if inside:
            parts = line.split()
            if len(parts) >= 2:
                found.append(parts[1])
    return found


def render_block(hostnames, ip: str = "127.0.0.1") -> str:
    lines = [BEGIN]
    for host in hostnames:
        lines.append(f"{ip}  {host}")
    lines.append(END)
    return "\n".join(lines)


def desired_text(current: str, hostnames, ip: str = "127.0.0.1") -> str:
    base = strip_block(current)
    block = render_block(hostnames, ip)
    return (base + "\n\n" + block + "\n").lstrip("\n")


def sync(hostnames, path=None, apply: bool = False, ip: str = "127.0.0.1") -> dict:
    """让 hosts 里的喵盒条目 = hostnames。apply=False 时只算不改（dry-run）。"""
    path = Path(path) if path else default_path()
    current = read(path)
    wanted = desired_text(current, hostnames, ip)
    result = {
        "path": str(path),
        "changed": wanted != current,
        "count": len(hostnames),
        "admin": is_admin(),
        "applied": False,
        "before": current_hostnames(current),
        "after": list(hostnames),
    }
    if result["changed"] and apply:
        if not result["admin"]:
            result["error"] = "改 hosts 需要管理员权限，请用管理员身份运行终端"
            return result
        nl = "\r\n" if "\r\n" in current else "\n"
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(wanted.replace("\n", nl))
        result["applied"] = True
    return result
