"""hosts 文件管理。

喵盒用**成对标记**圈住自己加的行：只动 `# >>> meowbox` 和
`# <<< meowbox` 之间的内容，你手写的条目一个字都不会碰。
（Laragon 是逐行打 `# laragon magic!` 标签，删起来容易漏。）

**两个平台写 hosts 的方式不同**：

* Windows —— 非管理员时另起一个 UAC 提权进程（`ShellExecuteW(runas)`），
  那边只说明「弹窗发出去了」，写完必须回头核对文件（见 `wait_until`）；
* Linux —— `/etc/hosts` 是 root 的，用 `pkexec cp` 提权覆盖。
  pkexec **会阻塞到认证和执行完成**，所以比 Windows 那条路干脆。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

from . import plat
from .util import is_admin, run

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


def sync(hostnames, path=None, apply: bool = False, ip: str = "127.0.0.1",
         elevate: bool = True) -> dict:
    """让 hosts 里的喵盒条目 = hostnames。apply=False 时只算不改（dry-run）。

    Linux 上没 root 也能用：`elevate=True` 时会自己走 `pkexec cp` 提权
    （弹出 polkit 认证框），不需要用户手敲命令。
    """
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
    if not (result["changed"] and apply):
        return result

    nl = "\r\n" if "\r\n" in current else "\n"
    if result["admin"]:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(wanted.replace("\n", nl))
        result["applied"] = True
        return result

    if plat.IS_WINDOWS or not elevate:
        result["error"] = ("改 hosts 需要管理员权限，请用管理员身份运行终端"
                           if plat.IS_WINDOWS else
                           f"改 hosts 需要 root。跑一下：\n  {_sudo_hint(path)}")
        return result

    # ---- Linux：写临时文件 → pkexec cp ----
    ok, note = _apply_elevated_linux(wanted, path)
    result["applied"] = ok
    if ok:
        result["note"] = note
        have = current_hostnames(read(path))
        if have != list(hostnames):
            result["applied"] = False
            result["error"] = ("提权命令执行了，但 hosts 内容没对上：\n"
                               f"  现在是 {', '.join(have) or '（没有喵盒条目）'}\n"
                               f"  应该是 {', '.join(hostnames) or '（没有站点）'}")
    else:
        result["error"] = note
    return result


def _sudo_hint(path: Path) -> str:
    return f"sudo meowbox.py hosts --apply（或者手动编辑 {path}）"


def _apply_elevated_linux(wanted: str, path: Path, timeout: float = 45.0) -> tuple[bool, str]:
    """用 pkexec 覆盖 hosts。返回 (成功?, 说明)。

    pkexec 会一直阻塞到用户在 polkit 对话框里认证完、命令跑完。
    **桌面上没有 polkit agent 时它会一直挂着**（实测 90 秒都没反应），
    所以这里给一个短超时，超时就退回去让用户自己 sudo。

    待写入的内容**先落一份到 /tmp** —— 无论提权成不成，用户都能
    `sudo cp /tmp/meowbox-hosts.new /etc/hosts` 一把搞定。
    """
    fd, tmp_name = tempfile.mkstemp(prefix="meowbox-hosts-", suffix=".new")
    tmp = Path(tmp_name)
    keep = Path(tempfile.gettempdir()) / "meowbox-hosts.new"
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(wanted)
        try:
            shutil.copyfile(tmp, keep)
            keep.chmod(0o644)
        except OSError:
            keep = tmp

        cp = shutil.which("cp") or "/usr/bin/cp"
        argv = [cp, "--preserve=mode,ownership", str(tmp), str(path)]

        pkexec = shutil.which("pkexec")
        if pkexec:
            rc, out, err = run([pkexec, *argv], timeout=timeout)
            if rc == 0:
                return True, "已通过 pkexec 写入 hosts"
            detail = (err or out).strip()[-400:]
            if "No authentication agent" in detail:
                return False, (f"桌面上没有 polkit 认证程序，pkexec 弹不出框。\n"
                               f"手动跑一次：\n  sudo cp {keep} {path}")
            if "auth" in detail.lower() and ("dismiss" in detail.lower()
                                             or "not authorized" in detail.lower()):
                return False, ("认证被取消了（polkit 对话框点了「取消」）。\n"
                               f"手动跑一次：\n  sudo cp {keep} {path}")
            return False, (f"pkexec 没写成：{detail or '（没输出）'}\n"
                           f"手动跑一次：\n  sudo cp {keep} {path}")

        # 没有 pkexec 或者它失败了：试试免密码 sudo
        rc, out, err = run(["sudo", "-n", *argv], timeout=30)
        if rc == 0:
            return True, "已通过 sudo 写入 hosts"
        return False, (f"没法自动提权（pkexec 弹不出来、sudo 又要密码）。\n"
                       f"手动跑一次就成：\n  sudo cp {keep} {path}")
    finally:
        tmp.unlink(missing_ok=True)


def wait_until(hostnames, path=None, timeout: float = 60.0, interval: float = 1.0):
    """等 hosts 里的喵盒条目变成 `hostnames`：等到就返回那些条目，超时返回 None。

    为什么需要它：界面没管理员权限时是**另起一个提权进程**去写 hosts，
    `ShellExecute` 成功只说明「UAC 弹窗发出去了」，跟写没写进去毫无关系。
    提权进程哪怕当场报错退出（实测过：参数传错，argparse 直接退出），
    只盯着 ShellExecute 的界面也会报成功。所以写完之后一律回头看文件。
    """
    path = Path(path) if path else default_path()
    wanted = list(hostnames)
    deadline = time.monotonic() + timeout
    while True:
        have = current_hostnames(read(path))
        if have == wanted:
            return have
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


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


def ensure_proxy_bypass(tld: str = "meow", hostnames=(), extra=("loc", "test")) -> dict:
    """把本地站点的域名加进「代理绕过列表」。

    为什么必须做：装了 Clash / Watt Toolkit 之类的代理工具时，浏览器会把
    `mq.meow` 也丢给代理，代理去远端解析这个域名当然失败，于是报
    **ERR_TUNNEL_CONNECTION_FAILED** —— 看起来像站点挂了，其实是请求没到本机 Apache。

    加两类东西，都不写死：
    * `*.<当前后缀>` —— 后缀来自 `sites.tld`，用户改成 `.lh`、`.dev`、`.abc` 都跟着变；
    * **每个站点的完整域名** —— 万一后缀是空的（域名就是站点名），或者用户在
      浏览器里用了别的写法，逐条列出来最保险。

    只动当前用户（HKCU），不需要管理员。
    """
    if os.name != "nt":
        return {"changed": False, "skipped": "只有 Windows 需要"}
    import winreg

    wanted: list[str] = []
    if tld:
        wanted.append(f"*.{tld}")
    wanted += [h for h in hostnames if h]
    wanted += [f"*.{t}" for t in extra if t]
    # 去重但保序
    wanted = list(dict.fromkeys(wanted))

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
