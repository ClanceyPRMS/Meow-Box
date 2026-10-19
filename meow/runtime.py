"""运行方式探测：源码模式（`python meowbox.py`）还是打包成 exe（PyInstaller）。

打包之后有三件事跟源码模式不一样，全部收在这个模块里，别在别处写
`Path(__file__).parent.parent`：

* **根目录** `app_root()`：源码模式 = `meowbox.py` 所在目录；exe 模式 = exe
  所在目录（`sys.executable` 的父目录）。所以 `meowbox.config.json`、`www/`、
  `bin/` 永远躺在程序旁边 —— 打包不破坏「整个文件夹搬走还能跑」这条底线。
* **只读资源** `templates_dir()` / `assets_dir()`：templates 和 assets 会被塞进
  `_internal/`（`sys._MEIPASS`）。**根目录里有就用根目录那份**（用户能改），
  没有才退回包里那份。
* **再起一个自己** `launch_argv()`：源码模式是 `pythonw.exe meowbox.py gui`，
  exe 模式直接启动 `meowbox-panel.exe` / `meowbox-tray.exe`（窗口版，没有黑框），
  不需要机器上装 Python。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

#: 打包后「某个子命令」用哪个可执行文件（界面版是窗口子系统，双击不弹黑框）
EXE_FOR_COMMAND = {
    "gui": "meowbox-panel.exe",
    "tray": "meowbox-tray.exe",
}
#: 控制台版（CLI 子命令、需要 UAC 的 hosts 都用它）
CONSOLE_EXE = "meowbox.exe"


def frozen() -> bool:
    """是不是 PyInstaller 打出来的 exe。"""
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """喵盒根目录（配置、www、bin、logs 都在这底下）。"""
    if frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path:
    """PyInstaller 解出来的只读包目录；源码模式下就是根目录。"""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else app_root()


def templates_dir(root=None) -> Path:
    """去哪读配置模板：优先根目录（用户可改），否则包里那份。"""
    local = Path(root or app_root()) / "templates"
    return local if local.is_dir() else bundle_dir() / "templates"


def assets_dir(root=None) -> Path:
    """去哪读图标等素材：同样优先根目录。"""
    local = Path(root or app_root()) / "assets"
    return local if local.is_dir() else bundle_dir() / "assets"


def exe_available(root=None, name: str = CONSOLE_EXE) -> bool:
    return (Path(root or app_root()) / name).is_file()


def launch_argv(command: str, *args) -> list[str]:
    """再起一个自己：返回完整命令行（第一个元素是可执行文件/解释器）。"""
    extra = [str(a) for a in args]
    if frozen():
        name = EXE_FOR_COMMAND.get(command)
        exe = Path(app_root()) / name if name else None
        if exe is not None and exe.is_file():
            return [str(exe), *extra]
        # 只打包了一个 exe：同一个可执行文件加子命令
        return [sys.executable, command, *extra]
    from . import util                      # 延迟导入，免得 runtime <-> util 打结
    return [util.preferred_pythonw(), str(app_root() / "meowbox.py"), command, *extra]


def console_argv(command: str, *args) -> list[str]:
    """要看得见输出的那种（UAC 提权写 hosts）：源码模式换成 python.exe。"""
    argv = launch_argv(command, *args)
    if not frozen():
        argv[0] = argv[0].replace("pythonw.exe", "python.exe")
    return argv


def quote_args(argv) -> str:
    """ShellExecute 只收一整条字符串，参数自己加引号。"""
    return " ".join(f'"{a}"' if " " in str(a) else str(a) for a in argv)


#: 提示文案里那些「怎么在命令行做」的例子，exe 模式下要换个说法
_HINTS = (
    ("pythonw meowbox.py", CONSOLE_EXE),
    ("python meowbox.py", CONSOLE_EXE),
)


def localize(text) -> str:
    """把提示里的 `python meowbox.py xxx` 换成 `meowbox.exe xxx`（仅 exe 模式）。"""
    if not frozen() or text is None:
        return text
    out = str(text)
    for old, new in _HINTS:
        out = out.replace(old, new)
    return out


def _std_is_console(stream) -> bool:
    """这个流是不是真的接着控制台窗口。

    只看 `isatty()` 是不够的：**Windows 的 NUL 设备也是字符设备，`isatty()`
    返回 True**，但它按 locale（GBK）编码，写 `🐾` 照样 `UnicodeEncodeError`。
    所以再问一次 Win32 的 `GetConsoleMode` —— 只有真正的控制台才有"模式"这个属性。

    实测：`meowbox.exe --help > NUL` 就崩在这上面（`> 文件` 反而没事，因为文件
    的 isatty() 是 False）。
    """
    if not stream.isatty():
        return False
    try:
        import ctypes

        handle = ctypes.windll.kernel32.GetStdHandle(
            -11 if stream is sys.stdout else -12      # STD_OUTPUT / STD_ERROR
        )
        mode = ctypes.c_uint32()
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    except Exception:
        return True          # 判断不了就保守：当成控制台，不去动它的编码


def prepare_stdio() -> None:
    """控制台输出别因为编码问题崩掉。

    * 窗口版 exe 根本没有控制台（`sys.stdout is None`）→ 先接到日志文件；
    * 输出被重定向到管道/文件时，Windows 默认按 GBK 编码，`🐾` 直接抛
      `UnicodeEncodeError` → 改成 UTF-8（自家 `util.run()` 也是按 UTF-8 解）；
    * 真控制台保持原样：Python 自己走 WriteConsoleW，中文和 emoji 都正常。
    """
    ensure_stdio()
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            if _std_is_console(stream):
                continue
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def ensure_stdio() -> None:
    """窗口版 exe 没有控制台：`print()` 会因为 sys.stdout is None 炸掉。

    所以打包版的窗口程序先把 stdout/stderr 接到 `logs/<exe名>.log`，
    接不上就丢进 devnull —— 宁可少记日志，也不能一 print 就崩。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    stream = None
    try:
        logs = Path(app_root()) / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        name = Path(sys.executable).stem if frozen() else "meowbox"
        stream = open(logs / f"{name}.log", "a", encoding="utf-8", buffering=1)
    except OSError:
        stream = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
