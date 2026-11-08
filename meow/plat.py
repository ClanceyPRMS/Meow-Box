"""平台层：Windows / Linux 的差异**全部**收在这里。

喵盒 0.3.0 起同时支持两个平台：

* **Windows** —— Laragon / 自带 `bin/`，Apache Lounge 的 `bin/httpd.exe` 布局，
  进程用 `taskkill /T /F` 收尾，hosts 靠 UAC（`runas`）提权。
* **Arch Linux / 通用 Linux** —— 系统包（`/usr/bin/httpd`）或自带 `bin/`
  解包的同名二进制，进程用信号收尾，hosts 靠 `pkexec` 提权。

三条铁规（改代码时别破坏）：

1. 业务模块里**不许**写 `os.name == "nt"`、不许自己拼 `bin/httpd.exe`
   —— 一律问 `IS_WINDOWS` / 用 `paths()` 拿；
2. Windows 的行为**一个字都不能变**，所有 Linux 分支都是新增；
3. 平台差异只在这里出现，别的地方只在「文案」上分叉。
"""
from __future__ import annotations

import ctypes
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

IS_WINDOWS = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")
IS_MAC = sys.platform == "darwin"

#: 可执行文件后缀（Linux 上是空串）
EXE = ".exe" if IS_WINDOWS else ""

#: 让子进程不要弹出黑框（只在 Windows 有意义）
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def _run(cmd, timeout=20):
    """跑一条命令抓输出。返回 (rc, stdout, stderr)，不抛异常。"""
    try:
        proc = subprocess.run(
            [str(c) for c in cmd],
            capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW,
        )
        return (proc.returncode,
                proc.stdout.decode("utf-8", "replace"),
                proc.stderr.decode("utf-8", "replace"))
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return 127, "", ""


# =========================================================================== 进程
# ---------------------------------------------------------------- 进程名
def image_name(pid):
    """进程的映像名（`httpd` / `httpd.exe`）。

    返回值三种意思（调用方必须区分）：

    * `"httpd.exe"` —— 进程在，名字拿到了；
    * `""`          —— **这个 pid 上没有进程**（已经退出了）；
    * `None`        —— 查不了（权限不够 / 平台不支持 / API 失败），
      调用方要回退到别的手段。

    区分后两种很重要：只回一个 False 的话，进程没了就会每次回退到很慢的
    子进程查询（Windows 上 `tasklist` 要 0.9 秒）。
    """
    if not pid:
        return ""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return ""
    if IS_WINDOWS:
        return _image_name_win32(pid)
    return _image_name_proc(pid)


def _image_name_proc(pid: int):
    """Linux/macOS：读 /proc/<pid>/comm。

    `/proc/<pid>` 不存在 = 进程没了（返回 `""`）；有目录但读不到 = 权限不够
    （返回 `None`）。comm 会被内核截断到 15 个字符 —— 喵盒关心的
    httpd / nginx / mariadbd / php-cgi / mailpit 都短于这个长度。
    """
    base = Path("/proc") / str(pid)
    try:
        if not base.exists():
            return ""
        return (base / "comm").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_INVALID_PARAMETER = 87
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


def _image_name_win32(pid: int):
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


def pid_alive(pid) -> bool:
    """这个 pid 上还有进程吗（不看名字）。"""
    if not pid:
        return False
    name = image_name(pid)
    return name is None or bool(name)


def pid_matches(pid, names) -> bool:
    """pid 还活着，**并且**进程名对得上。

    只比 pid 数字是不够的：进程死了以后系统会把这个号码发给别人
    （Windows 实测踩过 —— 去杀一个系统进程还报「权限不够」）。
    """
    if not pid:
        return False
    name = image_name(pid)
    if name is not None:
        low = (name or "").lower()
        return any(str(n).lower() in low for n in names)
    return _pid_matches_shell(pid, names)


def _pid_matches_shell(pid, names) -> bool:
    """回退：Windows 问 tasklist，Linux 用 ps。"""
    if IS_WINDOWS:
        rc, out, _ = _run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], timeout=20)
        if rc != 0 or str(pid) not in out:
            return False
        low = out.lower()
        return any(str(n).lower() in low for n in names)
    rc, out, _ = _run(["ps", "-o", "comm=", "-p", str(int(pid))], timeout=10)
    if rc != 0:
        return False
    low = out.lower()
    return any(str(n).lower() in low for n in names)


# ---------------------------------------------------------------- 端口
_TCP_TABLE_OWNER_PID_LISTENER = 3
_AF_INET = 2


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


def _listening_ports_ss():
    """Linux：`ss -tlnpH` 一次拿全（iproute2 是基础包，必有）。"""
    rc, out, _ = _run(["ss", "-tlnpH"], timeout=10)
    if rc != 0:
        return None
    by_port: dict[int, int] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        local = parts[3]
        if ":" not in local:
            continue
        try:
            port = int(local.rsplit(":", 1)[1])
        except ValueError:
            continue
        # users:(("httpd",pid=1234,fd=4),("httpd",pid=1233,fd=4))
        pid = 0
        if "pid=" in line:
            chunk = line.split("pid=", 1)[1]
            digits = ""
            for ch in chunk:
                if ch.isdigit():
                    digits += ch
                else:
                    break
            if digits:
                pid = int(digits)
        if port and pid:
            by_port[port] = pid
    return by_port


def _listening_ports_proc():
    """Linux 回退：解析 /proc/net/tcp{,6} 再反查 socket inode 属于谁。"""
    port_of: dict[str, int] = {}
    for name in ("tcp", "tcp6"):
        try:
            text = (Path("/proc/net") / name).read_text()
        except OSError:
            continue
        for line in text.splitlines()[1:]:
            fields = line.split()
            if len(fields) < 10 or fields[3] != "0A":       # 0A = LISTEN
                continue
            try:
                port = int(fields[1].rsplit(":", 1)[1], 16)
            except (ValueError, IndexError):
                continue
            port_of[fields[9]] = port
    if not port_of:
        return {}
    by_port: dict[int, int] = {}
    need = set(port_of)
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fds = list((entry / "fd").iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                link = os.readlink(fd)
            except OSError:
                continue
            if not link.startswith("socket:["):
                continue
            inode = link[8:-1]
            if inode in need:
                by_port[port_of[inode]] = int(entry.name)
                need.discard(inode)
        if not need:
            break
    return by_port


def listening_ports():
    """{端口: pid} —— 当前所有正在监听 TCP 的端口。拿不到返回 None。"""
    if IS_WINDOWS:
        return _listening_ports_win32()
    found = _listening_ports_ss()
    if found is None:
        found = _listening_ports_proc()
    return found


def _listeners_shell() -> dict:
    """回退方案：Windows 用 netstat + tasklist，Linux 用 ss 不带 -p。"""
    by_port: dict[int, int] = {}
    if IS_WINDOWS:
        rc, out, _ = _run(["netstat", "-ano", "-p", "TCP"])
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
            _, out2, _ = _run(["tasklist", "/FO", "CSV", "/NH"], timeout=30)
            for line in out2.splitlines():
                cols = line.strip().strip('"').split('","')
                if len(cols) >= 2 and cols[1].isdigit():
                    names[int(cols[1])] = cols[0]
        return {port: (pid, names.get(pid, "未知程序")) for port, pid in by_port.items()}

    rc, out, _ = _run(["ss", "-tlnH"], timeout=10)
    if rc == 0:
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4 and ":" in parts[3]:
                try:
                    by_port[int(parts[3].rsplit(":", 1)[1])] = 0
                except ValueError:
                    continue
    return {port: (0, "未知程序") for port in by_port}


# ---------------------------------------------------------------- 杀进程
def _descendants(pid: int) -> list[int]:
    """Linux：扫 /proc 找所有子孙进程（先子后父的逆序）。"""
    children: dict[int, list[int]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # comm 里可能有空格和括号，从最后一个 ')' 之后切
        rest = stat.rsplit(")", 1)[-1].split()
        if len(rest) < 2:
            continue
        try:
            ppid = int(rest[1])
        except ValueError:
            continue
        children.setdefault(ppid, []).append(int(entry.name))

    out: list[int] = []
    stack = [pid]
    while stack:
        current = stack.pop()
        for kid in children.get(current, ()):
            out.append(kid)
            stack.append(kid)
    return out


def _signal(pid: int, sig) -> None:
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def kill_tree(pid: int, grace: float = 3.0) -> bool:
    """结束一个进程以及它的所有子进程。返回是否确认杀掉了。

    Windows：直接 `taskkill /T /F`（它自己会处理进程树）。
    Linux：先 SIGTERM 全树 → 等 `grace` 秒 → SIGKILL 剩下的。
    绝不先 SIGKILL —— 那会让 mysqld 的 InnoDB 来不及收尾。
    """
    if IS_WINDOWS:
        _run(["taskkill", "/PID", str(pid), "/T", "/F"])
        return True
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    tree = [pid] + _descendants(pid)
    for p in tree:
        _signal(p, signal.SIGTERM)
    deadline = time.time() + grace
    while time.time() < deadline:
        if not any(pid_alive(p) for p in tree):
            return True
        time.sleep(0.1)
    for p in tree:
        _signal(p, signal.SIGKILL)
    time.sleep(0.2)
    return not any(pid_alive(p) for p in tree)


# ---------------------------------------------------------------- 权限 / 提权
def is_admin() -> bool:
    """当前进程有没有改 hosts 的权限。"""
    if not IS_WINDOWS:
        try:
            return os.geteuid() == 0
        except AttributeError:
            return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:                                  # noqa: BLE001
        return False


def has_passwordless_sudo() -> bool:
    """Linux：能不能不输密码 sudo（有些人配了 NOPASSWD）。"""
    if IS_WINDOWS:
        return False
    rc, _, _ = _run(["sudo", "-n", "true"], timeout=8)
    return rc == 0


def elevate(argv, root=None) -> bool:
    """用系统的提权器起一个进程。

    * Windows：`ShellExecuteW(runas)` —— 弹 UAC；
    * Linux：`pkexec` —— 弹 polkit 认证框（桌面环境自带）。

    返回**是否成功发起**。注意：这只说明对话框弹出去了，跟「事情做成了没有」
    毫无关系 —— 调用方必须回头核对结果（见 `hosts.wait_until`）。
    """
    args = [str(a) for a in argv]
    if not args:
        return False
    if IS_WINDOWS:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        shell32.ShellExecuteW.restype = ctypes.c_void_p
        shell32.ShellExecuteW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                          ctypes.c_wchar_p, ctypes.c_wchar_p,
                                          ctypes.c_wchar_p, ctypes.c_int]
        kernel32.GetConsoleWindow.restype = ctypes.c_void_p
        hwnd = kernel32.GetConsoleWindow()
        quoted = " ".join(f'"{a}"' if " " in a else a for a in args[1:])
        rc = shell32.ShellExecuteW(hwnd, "runas", args[0], quoted,
                                   str(root) if root else None, 1)
        return int(rc or 0) > 32

    pkexec = shutil.which("pkexec")
    if not pkexec:
        return False
    env = {k: v for k, v in os.environ.items()
           if k in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR",
                    "DBUS_SESSION_BUS_ADDRESS", "PATH", "LANG", "LC_ALL", "HOME")}
    try:
        subprocess.Popen(args, executable=pkexec, cwd=str(root) if root else None,
                         env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except OSError:
        return False


def sudo_hint(argv) -> str:
    """给用户抄的一条 sudo 命令（提权器不可用时的兜底）。"""
    parts = [str(a) for a in argv]
    return "sudo " + " ".join(f'"{a}"' if " " in a else a for a in parts)


# ---------------------------------------------------------------- 其它
def open_path(target) -> None:
    """用系统默认程序打开一个文件 / 文件夹 / 网址。"""
    if IS_WINDOWS:
        os.startfile(str(target))
        return
    opener = shutil.which("xdg-open") or "xdg-open"
    subprocess.Popen([opener, str(target)], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def python_console() -> str:
    """当前解释器（看得见输出的那种）。"""
    return sys.executable


def python_windowless() -> str:
    """不带控制台窗口的 Python。

    Windows 上是 `pythonw.exe`（找不到就退回 python.exe）；
    Linux 上没有这个概念，直接给 sys.executable（GUI 由 X/Wayland 显示）。
    """
    if not IS_WINDOWS:
        return sys.executable
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate) if candidate.is_file() else str(exe)


def preferred_python() -> str:
    """挑一个最稳的解释器给启动器 / 开机自启用。

    * **Windows**：优先系统装的 Python —— Hermes 自带的解释器在
      `%LOCALAPPDATA%\\hermes\\tools\\` 下，哪天升级或换机器就失效了。
    * **Linux**：同理，优先 `/usr/bin/python3`，别把 venv 里那个塞进
      `.desktop` —— 那个 venv 是某个工具的，删了就成死链。
    """
    if not IS_WINDOWS:
        for cand in ("/usr/bin/python3", "/usr/local/bin/python3"):
            if Path(cand).is_file():
                return cand
        found = shutil.which("python3")
        return found or sys.executable
    found = shutil.which("pythonw")
    if found and "hermes" not in found.lower():
        return found
    for version in ("314", "313", "312", "311"):
        for drive in ("C:", "D:"):
            candidate = Path(f"{drive}/Python{version}/pythonw.exe")
            if candidate.is_file():
                return str(candidate)
    return python_windowless()


def fs_type(path) -> str | None:
    """路径所在文件系统的类型（`ntfs3` / `ext4` / `btrfs` …）；查不到返回 None。

    跨系统开发要关心这个：NTFS 上的数据库目录被两个系统轮着打开是有风险的，
    所以 `init` 会提示「数据目录最好放 Linux 原生分区」。
    """
    if IS_WINDOWS:
        return None
    try:
        target = str(Path(path).resolve())
    except OSError:
        return None
    try:
        text = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    best_len, best_fs = -1, None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mount = parts[1].replace("\\040", " ")          # /proc/mounts 用 \040 转义空格
        if target == mount or target.startswith(mount.rstrip("/") + "/"):
            if len(mount) > best_len:
                best_len, best_fs = len(mount), parts[2]
    return best_fs


def foreign_fs(path) -> str | None:
    """这个路径在「不是 Linux 原生」的文件系统上吗？是就返回类型。

    `ext4/btrfs/xfs/zfs/f2fs/overlay/tmpfs` 都算原生；
    `ntfs*/fuseblk/vfat/exfat` 这类跨系统共享盘会被认出来。
    """
    fs = fs_type(path)
    if fs is None:
        return None
    native = ("ext2", "ext3", "ext4", "btrfs", "xfs", "zfs", "f2fs",
              "overlay", "tmpfs", "bcachefs", "reiserfs")
    return None if fs in native else fs


def ensure_display() -> bool:
    """Linux/Wayland：Tk 只能走 X11，`DISPLAY` 没传下来就自己猜一个。

    niri / sway 这类 Wayland 合成器下，XWayland 一般在 `:0`（或 `:1`）上跑，
    但环境变量不一定传下来 —— 从 `.desktop`、cron、或者一个干净的子 shell
    起来的程序就看不到它，于是面板当场报
    `_tkinter.TclError: couldn't connect to display ""`。
    """
    if IS_WINDOWS:
        return True
    if os.environ.get("DISPLAY"):
        return True
    for n in range(0, 4):
        if Path(f"/tmp/.X11-unix/X{n}").exists():
            os.environ["DISPLAY"] = f":{n}"
            return True
    return False


# =========================================================================== 组件路径
@dataclass(frozen=True)
class Paths:
    """一个组件在磁盘上的位置。

    `root` 是组件自己的前缀（Apache 的 ServerRoot、Nginx 的 -p、PHP 的安装目录）；
    `system=True` 表示用的是系统包（/usr），不是喵盒 `bin/` 里那份。
    """

    name: str
    exe: Path
    root: Path
    system: bool = False
    modules: Path | None = None         # Apache / Nginx 的 .so 目录
    mime_types: Path | None = None
    conf_dir: Path | None = None
    admin: Path | None = None           # mysqladmin / mariadb-admin
    client: Path | None = None          # mysql / mariadb
    helper: Path | None = None          # mariadb-install-db / mysqld --initialize

    @property
    def dir(self) -> Path:
        """兼容旧代码：很多地方管这个叫 `dir`。"""
        return self.root

    def __fspath__(self) -> str:
        """老代码里的 `Path(bins["apache"])` 拿到的是组件 root。"""
        return str(self.root)

    def __str__(self) -> str:
        return str(self.root)

    def exists(self) -> bool:
        return self.exe.is_file()


def _first_existing(*candidates):
    for cand in candidates:
        if cand and Path(cand).exists():
            return Path(cand)
    return None


def _find_version_dir(base: Path, exe_rel: str) -> Path | None:
    """在 bin/apache/ 这种容器目录里找出真正含可执行文件的那一层。"""
    if not base.is_dir():
        return None
    candidates = [base] + sorted([d for d in base.iterdir() if d.is_dir()], reverse=True)
    for cand in candidates:
        if (cand / exe_rel).is_file():
            return cand
    return None


def _bin_root(cfg: dict) -> Path:
    """去哪找 bin/：优先 provider，其次喵盒自己的根目录。"""
    from . import config as cfgmod
    return cfgmod.binary_root(cfg)


def _httpd_root(exe: Path) -> Path | None:
    """`httpd -V` 里的 HTTPD_ROOT（安装前缀）。"""
    rc, out, err = _run([exe, "-V"], timeout=15)
    text = out + err
    for line in text.splitlines():
        if "HTTPD_ROOT" in line and "=" in line:
            value = line.split("=", 1)[1].strip().strip('"')
            if value:
                return Path(value)
    return None


def _apache_system() -> Paths | None:
    """Linux：找系统装的 Apache。"""
    exe = shutil.which("httpd") or shutil.which("apache2")
    if not exe:
        return None
    exe = Path(exe)
    prefix = exe.parent.parent                        # /usr/bin/httpd → /usr
    httpd_root = _httpd_root(exe)
    modules = _first_existing(
        prefix / "lib/httpd/modules",                 # Arch
        prefix / "lib/apache2/modules",               # Debian
        prefix / "modules",
        (httpd_root / "modules") if httpd_root else None,
    )
    mime = _first_existing(
        (httpd_root / "conf/mime.types") if httpd_root else None,
        prefix / "share/apache/conf/mime.types",
        prefix / "share/httpd/conf/mime.types",
    )
    return Paths(name="apache", exe=exe, root=httpd_root or (prefix / "etc/httpd"),
                 system=True, modules=modules, mime_types=mime,
                 conf_dir=(httpd_root / "conf") if httpd_root else None)


def apache(cfg: dict) -> Paths | None:
    """Apache 在哪。优先 `bin/apache/`（自带），否则系统包。"""
    base = _bin_root(cfg) / "bin" / "apache"
    version_dir = _find_version_dir(base, f"bin/httpd{EXE}")
    if version_dir is not None:
        return Paths(
            name="apache",
            exe=version_dir / "bin" / f"httpd{EXE}",
            root=version_dir,
            system=False,
            modules=version_dir / "modules",
            mime_types=version_dir / "conf" / "mime.types",
            conf_dir=version_dir / "conf",
        )
    if IS_WINDOWS:
        return None
    return _apache_system()


def _nginx_prefix(exe: Path) -> Path | None:
    """`nginx -V` 里的 --prefix=..."""
    rc, out, err = _run([exe, "-V"], timeout=15)
    text = out + err
    if "--prefix=" in text:
        value = text.split("--prefix=", 1)[1].split()[0].strip('"')
        if value:
            return Path(value)
    return None


def nginx(cfg: dict) -> Paths | None:
    base = _bin_root(cfg) / "bin" / "nginx"
    for rel in (f"nginx{EXE}", f"sbin/nginx{EXE}"):
        version_dir = _find_version_dir(base, rel)
        if version_dir is not None:
            return Paths(name="nginx", exe=version_dir / rel, root=version_dir,
                         system=False, mime_types=version_dir / "conf" / "mime.types",
                         conf_dir=version_dir / "conf")
    if IS_WINDOWS:
        return None
    exe = shutil.which("nginx")
    if not exe:
        return None
    exe = Path(exe)
    prefix = _nginx_prefix(exe)
    return Paths(name="nginx", exe=exe, root=prefix or (exe.parent.parent / "etc/nginx"),
                 system=True,
                 mime_types=_first_existing(
                     (prefix / "conf/mime.types") if prefix else None,
                     Path("/etc/nginx/mime.types")),
                 conf_dir=(prefix / "conf") if prefix else Path("/etc/nginx"))


def mysql(cfg: dict) -> Paths | None:
    """MySQL / MariaDB 在哪。Linux 上优先 MariaDB（Arch 只有它）。"""
    base = _bin_root(cfg) / "bin" / "mysql"
    version_dir = _find_version_dir(base, f"bin/mysqld{EXE}")
    if version_dir is not None:
        return Paths(name="mysql", exe=version_dir / "bin" / f"mysqld{EXE}",
                     root=version_dir, system=False,
                     admin=version_dir / "bin" / f"mysqladmin{EXE}",
                     client=version_dir / "bin" / f"mysql{EXE}",
                     helper=version_dir / "bin" / f"mysql_install_db{EXE}")
    if IS_WINDOWS:
        return None
    # 系统包：MariaDB 的二进制叫 mariadbd，但 mysqld 是它的软链接
    exe = (shutil.which("mariadbd") or shutil.which("mysqld")
           or shutil.which("mysqld_safe"))
    if not exe:
        return None
    exe = Path(exe)
    if exe.name == "mysqld_safe":                     # 不要 wrapper，要真正的服务端
        real = exe.with_name("mariadbd")
        exe = real if real.is_file() else exe
    prefix = Path("/usr")
    return Paths(
        name="mysql", exe=exe, root=prefix, system=True,
        admin=_first_existing(prefix / "bin/mariadb-admin", prefix / "bin/mysqladmin",
                              prefix / "bin/mariadb-admin"),
        client=_first_existing(prefix / "bin/mariadb", prefix / "bin/mysql"),
        helper=_first_existing(prefix / "bin/mariadb-install-db",
                               prefix / "bin/mysql_install_db"),
    )


def mailpit(cfg: dict) -> Paths | None:
    base = _bin_root(cfg) / "bin" / "mailpit"
    version_dir = _find_version_dir(base, f"mailpit{EXE}")
    if version_dir is not None:
        return Paths(name="mailpit", exe=version_dir / f"mailpit{EXE}",
                     root=version_dir, system=False)
    if IS_WINDOWS:
        return None
    exe = shutil.which("mailpit")
    return Paths(name="mailpit", exe=Path(exe), root=Path(exe).parent,
                 system=True) if exe else None


def as_paths(name: str, value, cfg: dict):
    """把 `discover()` 返回的东西统一成 `Paths`。

    `discover()` 已经返回 `Paths` 了；这里只是给「手工传了个路径」的老调用
    兜底 —— 别的地方请不要再用裸路径。
    """
    if isinstance(value, Paths):
        return value
    if value is None:
        return None
    resolver = RESOLVERS.get(name)
    return resolver(cfg) if resolver else None


def apache_mpm(exe: Path) -> str:
    """Apache 编译进来的是哪个 MPM（`event` / `prefork` / `worker` / `winnt`）。"""
    if IS_WINDOWS:
        return "winnt"
    rc, out, err = _run([exe, "-V"], timeout=15)
    for line in (out + err).splitlines():
        if "Server MPM:" in line:
            name = line.split(":", 1)[1].strip().lower()
            if name in ("event", "prefork", "worker", "itk", "peruser"):
                return name
    return "event"


#: 各组件的解析器（config.discover 用）
RESOLVERS = {
    "apache": apache,
    "nginx": nginx,
    "mysql": mysql,
    "mailpit": mailpit,
}


# =========================================================================== 平台默认值
def describe() -> str:
    """一行平台说明（`info` 和面板顶上那行用）。"""
    if IS_WINDOWS:
        return f"Windows {platform.release()}"
    if IS_LINUX:
        name = ""
        try:
            for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
                if line.startswith("PRETTY_NAME="):
                    name = line.split("=", 1)[1].strip().strip('"')
                    break
        except OSError:
            pass
        return f"{name or 'Linux'}（{platform.machine()}）"
    return f"{platform.system()} {platform.release()}"


def default_ports() -> dict:
    """新装一个喵盒时的默认端口。

    Windows 上 80/443 谁都能绑；Linux 上低于 1024 的端口要
    `CAP_NET_BIND_SERVICE`（见 `meowbox.py cap`），所以默认先用 8080/8443，
    用 `cap on` 提权之后再改回 80/443。
    """
    if IS_WINDOWS:
        return {"http": 80, "https": 443}
    return {"http": 8080, "https": 8443}


def needs_cap(port: int) -> bool:
    """这个端口在 Linux 上是不是需要额外权限。"""
    return (not IS_WINDOWS) and 0 < int(port) < 1024


def cap_tool() -> str | None:
    """给二进制加 CAP_NET_BIND_SERVICE 的工具（setcap）。"""
    if IS_WINDOWS:
        return None
    return shutil.which("setcap")
