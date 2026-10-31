"""通用小工具：跑命令、看端口、判管理员权限。"""
from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import time
from pathlib import Path

IS_WINDOWS = os.name == "nt"
# 让子进程不要弹出黑框
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def fwd(path) -> str:
    """转成 Windows 工具也认的正斜杠路径。"""
    return str(path).replace("\\", "/")


def ensure_dir(path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def render_template(text: str, **values) -> str:
    """把模板里的 @名字@ 换成真实值。

    故意不用 str.format —— Apache/Nginx 配置里全是 `{ }` 和 `%{...}i`，
    用 format 会被当成占位符炸掉。
    """
    for key, val in values.items():
        text = text.replace(f"@{key}@", str(val))
    return text


def pythonw() -> str:
    """当前解释器对应的、不带控制台窗口的 Python。"""
    import sys
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate) if candidate.is_file() else str(exe)


def preferred_pythonw() -> str:
    """挑一个最稳的无控制台解释器给启动器/开机自启用。

    优先系统装的 Python —— 因为 Hermes 自带的解释器在
    `%LOCALAPPDATA%\\hermes\\tools\\` 下面，哪天升级或换机器就失效了。
    """
    import shutil
    import sys

    found = shutil.which("pythonw")
    if found and "hermes" not in found.lower():
        return found
    for version in ("314", "313", "312", "311"):
        for drive in ("C:", "D:"):
            candidate = Path(f"{drive}/Python{version}/pythonw.exe")
            if candidate.is_file():
                return str(candidate)
    return pythonw()


def open_path(target) -> None:
    """用系统默认程序打开一个文件 / 文件夹 / 网址。"""
    if IS_WINDOWS:
        os.startfile(str(target))
    else:
        subprocess.Popen(["xdg-open", str(target)])


def run(cmd, cwd=None, timeout=60, env=None):
    """跑一个命令并抓回输出。返回 (returncode, stdout, stderr)，不抛异常。"""
    try:
        proc = subprocess.run(
            [str(c) for c in cmd],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            timeout=timeout,
            env=env,
            creationflags=CREATE_NO_WINDOW,
        )
        return (
            proc.returncode,
            proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"),
        )
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    except subprocess.TimeoutExpired:
        return 124, "", "命令超时: " + " ".join(str(c) for c in cmd)


def popen(cmd, cwd=None, log_file=None, env=None):
    """以后台方式启动一个进程（不阻塞）。

    env 传了就整个替换子进程环境（调用方通常基于 os.environ 改几个再传进来），
    php-cgi 的 PHP_FCGI_CHILDREN 就是靠它设置的。
    """
    out = None
    if log_file:
        ensure_dir(Path(log_file).parent)
        out = open(log_file, "ab")
    proc = subprocess.Popen(
        [str(c) for c in cmd],
        cwd=str(cwd) if cwd else None,
        stdout=out or subprocess.DEVNULL,
        stderr=subprocess.STDOUT if out else subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        env=env,
        creationflags=CREATE_NO_WINDOW,
    )
    return proc


def is_admin() -> bool:
    """当前进程是否有管理员权限（改 hosts 需要）。"""
    if not IS_WINDOWS:
        return os.geteuid() == 0
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, int(port))) == 0


def who_holds_port(port: int) -> str:
    """谁占着这个端口？返回 "httpd.exe (pid 1234)" 或空串。"""
    info = port_holder(port)
    return f"{info[1]} (pid {info[0]})" if info else ""


# ---------------------------------------------------------------- Win32 快路
# `tasklist` 在这台机器上要 **0.9 秒**、`netstat` 0.09 秒。面板每 2.5 秒刷一次
# 状态、托盘每次弹菜单都要查一遍，几个服务加起来就是 3 秒 —— 窗口直接「未响应」。
# 所以端口和进程名一律先走 Win32 API（微秒级），子进程那套只当回退。
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_INVALID_PARAMETER = 87
_TCP_TABLE_OWNER_PID_LISTENER = 3
_AF_INET = 2

_win_dll = {}


def _win32():
    """(kernel32, iphlpapi)，签名声明好再返回；不是 Windows 就 (None, None)。"""
    if not IS_WINDOWS:
        return None, None
    if not _win_dll:
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD)]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

        class MIB_TCPROW_OWNER_PID(ctypes.Structure):
            _fields_ = [("dwState", wintypes.DWORD),
                        ("dwLocalAddr", wintypes.DWORD),
                        ("dwLocalPort", wintypes.DWORD),
                        ("dwRemoteAddr", wintypes.DWORD),
                        ("dwRemotePort", wintypes.DWORD),
                        ("dwOwningPid", wintypes.DWORD)]

        iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
        iphlpapi.GetExtendedTcpTable.argtypes = [
            wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
            wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
        iphlpapi.GetExtendedTcpTable.restype = wintypes.DWORD
        _win_dll.update(kernel32=kernel32, iphlpapi=iphlpapi, row=MIB_TCPROW_OWNER_PID,
                        wintypes=wintypes)
    return _win_dll["kernel32"], _win_dll["iphlpapi"]


def image_name(pid):
    """进程的映像名（`httpd.exe`）。

    返回值三种意思：
    * `"httpd.exe"` —— 进程在，名字拿到了；
    * `""`        —— **这个 pid 上没有进程**（已经退出了）；
    * `None`      —— 查不了（权限不够 / 不是 Windows / API 失败），
      调用方要回退到 `tasklist`。

    区分后两种很重要：只回 False 的话，进程没了就会每次都去起一个
    0.9 秒的 tasklist。
    """
    kernel32, _ = _win32()
    if kernel32 is None:
        return None
    try:
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    except Exception:                                  # noqa: BLE001
        return None
    if not handle:
        return "" if ctypes.get_last_error() == _ERROR_INVALID_PARAMETER else None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = _win_dll["wintypes"].DWORD(len(buf))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return buf.value.rsplit("\\", 1)[-1]
    except Exception:                                  # noqa: BLE001
        return None
    finally:
        kernel32.CloseHandle(handle)


def _listening_ports_win32():
    """{端口: pid} —— GetExtendedTcpTable 一次拿全，约 1 毫秒。失败返回 None。"""
    _, iphlpapi = _win32()
    if iphlpapi is None:
        return None
    try:
        wintypes = _win_dll["wintypes"]
        size = wintypes.DWORD(0)
        iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, _AF_INET,
                                     _TCP_TABLE_OWNER_PID_LISTENER, 0)
        buf = ctypes.create_string_buffer(max(size.value, 4))
        rc = iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False, _AF_INET,
                                          _TCP_TABLE_OWNER_PID_LISTENER, 0)
        if rc != 0:
            return None
        count = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD)).contents.value
        if not count:
            return {}
        rows = ctypes.cast(
            ctypes.byref(buf, ctypes.sizeof(wintypes.DWORD)),
            ctypes.POINTER(_win_dll["row"] * count)).contents
        # dwLocalPort 是网络字节序，低 16 位才是端口
        return {((r.dwLocalPort & 0xFF) << 8) | ((r.dwLocalPort >> 8) & 0xFF): r.dwOwningPid
                for r in rows}
    except Exception:                                  # noqa: BLE001
        return None


# netstat + tasklist 都不便宜，缓存一下，免得界面每 2.5 秒刷一次就起一堆子进程
_LISTEN_CACHE = {"at": 0.0, "map": {}}


def invalidate_listeners() -> None:
    _LISTEN_CACHE["at"] = 0.0
    _LISTEN_CACHE["map"] = {}


def _listeners_shell() -> dict:
    """回退方案：netstat + tasklist（慢，但任何 Windows 都能用）。"""
    by_port: dict[int, int] = {}
    rc, out, _ = run(["netstat", "-ano", "-p", "TCP"])
    if rc == 0:
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[3] == "LISTENING":
                try:
                    by_port[int(parts[1].rsplit(":", 1)[1])] = int(parts[4])
                except (ValueError, IndexError):
                    continue

    names: dict[int, str] = {}
    if by_port:
        rc2, out2, _ = run(["tasklist", "/FO", "CSV", "/NH"], timeout=30)
        for line in out2.splitlines():
            cols = line.strip().strip('"').split('","')
            if len(cols) >= 2 and cols[1].isdigit():
                names[int(cols[1])] = cols[0]

    return {port: (pid, names.get(pid, "未知程序")) for port, pid in by_port.items()}


def listeners(ttl: float = 1.5) -> dict:
    """{端口: (pid, 进程名)} —— 当前所有正在监听 TCP 的端口。"""
    now = time.time()
    if ttl and _LISTEN_CACHE["map"] and now - _LISTEN_CACHE["at"] < ttl:
        return _LISTEN_CACHE["map"]

    by_port = _listening_ports_win32()
    if by_port is None:                                # 非 Windows / API 失败
        result = _listeners_shell()
    else:
        result = {}
        for port, pid in by_port.items():
            name = image_name(pid)
            result[port] = (pid, name or "未知程序")

    _LISTEN_CACHE.update({"at": now, "map": result})
    return result


def port_holder(port):
    """返回占着这个端口的 (pid, 进程名)；没人占返回 None。"""
    return listeners().get(int(port))


def shell_execute_elevated(exe: str, args: str, cwd=None) -> bool:
    """用 UAC 提权启动一个程序（会弹确认框）。返回是否成功发起。"""
    if not IS_WINDOWS:
        return False
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    shell32.ShellExecuteW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                      ctypes.c_wchar_p, ctypes.c_wchar_p,
                                      ctypes.c_wchar_p, ctypes.c_int]
    kernel32.GetConsoleWindow.restype = ctypes.c_void_p
    hwnd = kernel32.GetConsoleWindow()
    rc = shell32.ShellExecuteW(hwnd, "runas", exe, args, cwd, 1)
    return int(rc or 0) > 32


def free_port(port: int, use_elevation: bool = True):
    """把占着这个端口的进程关掉；没权限就弹 UAC 提权去关。

    返回 (成功?, 说明)。这个存在的意义：有时候占用者是**管理员权限**启动的
    （比如之前提权跑起来的 Apache），普通权限的喵盒既停不掉它、也看不见它。
    """
    info = port_holder(port)
    if not info:
        return True, f"端口 {port} 本来就空着"
    pid, name = info
    label = f"{name} (pid {pid})"

    if IS_WINDOWS and use_elevation and not is_admin():
        if not shell_execute_elevated("taskkill", f"/PID {pid} /T /F"):
            return False, "提权被取消（或 UAC 被拒绝）"
        for _ in range(24):
            time.sleep(0.25)
            if not port_in_use(port):
                invalidate_listeners()
                return True, f"已关掉占着 {port} 的 {label}"
        return False, f"提权之后 {port} 还是被 {label} 占着"

    kill_tree(pid)
    for _ in range(24):
        time.sleep(0.25)
        if not port_in_use(port):
            invalidate_listeners()
            return True, f"已关掉占着 {port} 的 {label}"
    return False, f"权限不够，杀不掉 {label}（它可能是管理员权限启动的）"


def kill_tree(pid: int) -> None:
    """结束一个进程以及它的所有子进程。"""
    run(["taskkill", "/PID", str(pid), "/T", "/F"])


def pid_is(pid, names) -> bool:
    """pid 还活着，**并且**进程名对得上。

    只比 pid 数字是不够的：进程死了以后 Windows 会把这个号码发给别人，
    于是 `run/mailpit.pid` 里的 2292 可能已经变成 WUDFHost.exe ——
    再「停」它就会去杀一个系统进程，还报「停不掉，多半是管理员权限」，
    把人吓得以为权限出了问题。实测踩过（2026-10）。

    先走 `image_name()` 的 Win32 快路（微秒级）；查不到名字时才退回
    `tasklist`（0.9 秒）—— 面板每 2.5 秒问一次，几条服务加起来能顶到 3 秒，
    窗口就「未响应」了。
    """
    if not pid:
        return False
    name = image_name(pid)
    if name is not None:
        low = name.lower()
        return any(str(n).lower() in low for n in names)
    return _pid_is_shell(pid, names)


def _pid_is_shell(pid, names) -> bool:
    """回退方案：问 tasklist（慢但准）。"""
    rc, out, _ = run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], timeout=20)
    if str(pid) not in out:
        return False
    low = out.lower()
    return any(str(n).lower() in low for n in names)
