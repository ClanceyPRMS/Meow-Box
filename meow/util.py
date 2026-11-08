"""通用小工具：跑命令、看端口、判管理员权限。

平台差异本身全在 `meow/plat.py`，这里只做三件事：

* 给「查端口 / 查进程」的结果加缓存（面板每 2.5 秒刷一次，不能每次都起子进程）；
* 保持这些函数的老签名（Windows 那边一行都不用改）；
* 提供 `free_port` 这种「两个平台做法不同但语义一样」的高层动作。

**Linux 上的三个关键差异**（都在这里的调用方处理）：
Apache / Nginx 在 Linux 上默认会 fork 到后台然后父进程退出 —— 于是
`popen()` 记下的 pid 当场就死了。所以启动命令必须带 `-DFOREGROUND`
（Apache）和 `-g "daemon off;"`（Nginx）。
"""
from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

from . import plat

IS_WINDOWS = plat.IS_WINDOWS
IS_LINUX = plat.IS_LINUX
#: 可执行文件后缀（Linux 上是空串）
EXE = plat.EXE
#: 让子进程不要弹出黑框
CREATE_NO_WINDOW = plat.CREATE_NO_WINDOW


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
    return plat.python_windowless()


def preferred_pythonw() -> str:
    """挑一个最稳的解释器给启动器/开机自启用。"""
    return plat.preferred_python()


def open_path(target) -> None:
    """用系统默认程序打开一个文件 / 文件夹 / 网址。"""
    plat.open_path(target)


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
    except OSError as exc:                            # 权限不够之类
        return 126, "", str(exc)


def popen(cmd, cwd=None, log_file=None, env=None):
    """以后台方式启动一个进程（不阻塞）。

    env 传了就整个替换子进程环境（调用方通常基于 os.environ 改几个再传进来），
    php-cgi 的 PHP_FCGI_CHILDREN 就是靠它设置的。

    Linux 上额外 `start_new_session=True`：服务脱离当前终端会话，
    这样关掉终端/面板不会连带把服务带走，`kill_tree` 也能按进程组收尾。
    """
    out = None
    if log_file:
        ensure_dir(Path(log_file).parent)
        out = open(log_file, "ab")
    kwargs = {}
    if not IS_WINDOWS:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        [str(c) for c in cmd],
        cwd=str(cwd) if cwd else None,
        stdout=out or subprocess.DEVNULL,
        stderr=subprocess.STDOUT if out else subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        env=env,
        creationflags=CREATE_NO_WINDOW,
        **kwargs,
    )
    return proc


# ---------------------------------------------------------------- 权限
def is_admin() -> bool:
    """当前进程是否有写 hosts 的权限（Windows 管理员 / Linux root）。"""
    return plat.is_admin()


def can_elevate() -> bool:
    """能不能弹出一个提权对话框（UAC / polkit）。"""
    if IS_WINDOWS:
        return True
    import shutil
    return shutil.which("pkexec") is not None


def shell_execute_elevated(exe: str, args: str, cwd=None) -> bool:
    """用提权器启动一个程序（Windows 弹 UAC，Linux 弹 polkit）。返回是否成功发起。"""
    if args and isinstance(args, str):
        argv = [exe] + args.split()
    else:
        argv = [exe]
    return plat.elevate(argv, cwd)


def sudo_hint(argv) -> str:
    """给用户抄的一条 sudo 命令（没有 pkexec 时的兜底）。"""
    return plat.sudo_hint(argv)


# ---------------------------------------------------------------- 端口
def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, int(port))) == 0


# netstat + tasklist 都不便宜，缓存一下，免得界面每 2.5 秒刷一次就起一堆子进程
_LISTEN_CACHE = {"at": 0.0, "map": {}}


def invalidate_listeners() -> None:
    _LISTEN_CACHE["at"] = 0.0
    _LISTEN_CACHE["map"] = {}


def listeners(ttl: float = 1.5) -> dict:
    """{端口: (pid, 进程名)} —— 当前所有正在监听 TCP 的端口。"""
    now = time.time()
    if ttl and _LISTEN_CACHE["map"] and now - _LISTEN_CACHE["at"] < ttl:
        return _LISTEN_CACHE["map"]

    by_port = plat.listening_ports()
    if by_port is None:                                # 拿不到（权限/平台不支持）
        result = plat._listeners_shell()
    else:
        result = {}
        for port, pid in by_port.items():
            name = plat.image_name(pid) if pid else ""
            result[port] = (pid, name or "未知程序")

    _LISTEN_CACHE.update({"at": now, "map": result})
    return result


def port_holder(port):
    """返回占着这个端口的 (pid, 进程名)；没人占返回 None。"""
    return listeners().get(int(port))


def who_holds_port(port: int) -> str:
    """谁占着这个端口？返回 "httpd.exe (pid 1234)" 或空串。"""
    info = port_holder(port)
    return f"{info[1]} (pid {info[0]})" if info else ""


# ---------------------------------------------------------------- 进程名 / 杀进程
def image_name(pid):
    """进程映像名。三种返回值：名字 / ""（pid 上没人）/ None（查不了）。"""
    return plat.image_name(pid)


def pid_is(pid, names) -> bool:
    """pid 还活着，**并且**进程名对得上。

    只比 pid 数字是不够的：进程死了以后系统会把这个号码发给别人，
    于是 `run/mailpit.pid` 里的 2292 可能已经变成别的进程 ——
    再「停」它就会去杀一个无关的进程，还报「停不掉，多半是权限问题」。
    """
    return plat.pid_matches(pid, names)


def pid_alive(pid) -> bool:
    """这个 pid 上还有进程吗（不看名字）。"""
    return plat.pid_alive(pid)


def kill_tree(pid: int, grace: float = 3.0):
    """结束一个进程以及它的所有子进程。"""
    return plat.kill_tree(pid, grace=grace)


def free_port(port: int, use_elevation: bool = True):
    """把占着这个端口的进程关掉；没权限就提权去关。

    返回 (成功?, 说明)。这个存在的意义：有时候占用者是**提权启动的**
    （比如之前用 root/管理员跑起来的 Apache），普通权限既停不掉、也看不见它。
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

    # Linux 上「别人（root）起的进程」普通用户杀不掉 —— 再试一次提权
    if (not IS_WINDOWS) and use_elevation and not is_admin():
        kill_exe = "/bin/kill"
        if plat.elevate([kill_exe, "-TERM", str(pid)]):
            for _ in range(24):
                time.sleep(0.25)
                if not port_in_use(port):
                    invalidate_listeners()
                    return True, f"已（提权）关掉占着 {port} 的 {label}"
    return False, f"权限不够，杀不掉 {label}（它可能是 root 启动的）"
