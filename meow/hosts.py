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


# --------------------------------------------------------------------------- 代理绕过
#: 写 HKCU 不需要管理员，所以这一步跟 hosts 分开做
_PROXY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
#: 不管用户配了什么后缀，这些本地开发常用的都该绕过代理
ALWAYS_BYPASS = ("localhost", "127.*", "<local>")


def proxy_bypass_entries() -> list[str]:
    """当前用户的代理绕过列表（分号分隔的那些）。"""
    if os.name != "nt":
        return []
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PROXY_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "ProxyOverride")
            return [p for p in str(value).split(";") if p.strip()]
    except OSError:
        return []


def ensure_proxy_bypass(tld: str = "meow", extra=("loc", "test")) -> dict:
    """把 `*.meow` 这类本地域名加进「代理绕过列表」。

    为什么必须做：装了 Clash / Watt Toolkit 之类的代理工具时，浏览器会把
    `mq.meow` 也丢给代理，代理去远端解析这个域名当然失败，于是报
    **ERR_TUNNEL_CONNECTION_FAILED** —— 看起来像站点挂了，其实是请求没到本机 Apache。
    这一步只动当前用户（HKCU），不需要管理员。
    """
    if os.name != "nt":
        return {"changed": False, "skipped": "只有 Windows 需要"}
    import winreg

    wanted = [f"*.{t}" for t in (tld, *extra) if t]
    current = proxy_bypass_entries()
    have = {p.strip().lower() for p in current}
    added = [w for w in wanted if w.lower() not in have]
    if not added:
        return {"changed": False, "entries": current}

    merged = current + added
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PROXY_KEY, 0,
                            winreg.KEY_READ | winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, ";".join(merged))
    except OSError as exc:
        return {"changed": False, "error": str(exc), "entries": current}

    # 通知系统「代理设置变了」，不然已经开着的浏览器还按老列表走
    try:
        import ctypes
        wininet = ctypes.WinDLL("wininet", use_last_error=True)
        INTERNET_OPTION_SETTINGS_CHANGED, INTERNET_OPTION_REFRESH = 39, 37
        wininet.InternetSetOptionW(None, INTERNET_OPTION_SETTINGS_CHANGED, None, 0)
        wininet.InternetSetOptionW(None, INTERNET_OPTION_REFRESH, None, 0)
    except Exception:                                  # noqa: BLE001
        pass
    return {"changed": True, "added": added, "entries": merged}
