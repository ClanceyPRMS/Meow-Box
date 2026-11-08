"""双击启动器 —— 便携的小关键。

为什么不注册表 / 不做服务？因为"便携"的底线就是**文件夹搬走还能跑**。
所以喵盒只用两条自发式的入口：

**Windows**（`.vbs`）：
* `meowbox-tray.vbs` / `meowbox-panel.vbs`：双击就跑，路径用
  `WScript.ScriptFullName` 现取，整个文件夹搬到别的盘也不会坏；
* 开机自启 = 在「启动」文件夹里放一个同样的 .vbs（见 `tray.set_autostart`）。

**Linux**（`.desktop`）：
* `meowbox-panel.desktop` —— 拖到 `~/.local/share/applications/` 就进应用菜单；
* 开机自启 = `~/.config/autostart/meowbox.desktop`（XDG autostart，同样删掉就取消）。

两种启动器：
* **打包版**（根目录里有 `meowbox-tray.exe` / `meowbox-panel.exe`）→ 直接跑 exe，
  不需要机器上装 Python；
* **源码版** → `pythonw.exe meowbox.py tray|gui`。

注意 `.vbs` 必须存成 **UTF-16LE**：WSH 把 UTF-8 的 BOM 当非法字符，
双击只会弹一个「无效字符」的错误框。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from . import plat, runtime, util

VBS_TEMPLATE = (
    "' Meow Box launcher -- {label}  (meowbox.py {cmd})\n"
    "' 双击即可启动，不会弹黑框窗口。\n"
    "' 路径是运行时取的，整个文件夹搬走也不会坏。\n"
    "' 注意：这个文件必须存成 UTF-16LE —— WSH 会把 UTF-8 的 BOM 当成非法字符。\n"
    'Set fso = CreateObject("Scripting.FileSystemObject")\n'
    'Set sh  = CreateObject("WScript.Shell")\n'
    'base = fso.GetParentFolderName(WScript.ScriptFullName)\n'
    'sh.CurrentDirectory = base\n'
    'sh.Run """{py}"" """ & base & "\\meowbox.py"" {cmd}", 0, False\n'
)

LAUNCHERS = (
    ("meowbox-tray.vbs", "tray", "系统托盘"),
    ("meowbox-panel.vbs", "gui", "控制面板"),
)

#: 打包版：直接跑同目录的 exe（窗口子系统，双击本来就没有黑框）
VBS_EXE_TEMPLATE = (
    "' Meow Box launcher -- {label}  ({exe})\n"
    "' 双击即可启动（打包版，不需要装 Python）。\n"
    "' 路径是运行时取的，整个文件夹搬走也不会坏。\n"
    "' 注意：这个文件必须存成 UTF-16LE —— WSH 会把 UTF-8 的 BOM 当成非法字符。\n"
    'Set fso = CreateObject("Scripting.FileSystemObject")\n'
    'base = fso.GetParentFolderName(WScript.ScriptFullName)\n'
    'CreateObject("WScript.Shell").Run """" & base & "\\{exe}""", 1, False\n'
)


def exe_for(cmd: str) -> str:
    """某个子命令在打包版里对应的 exe 名字。"""
    return runtime.EXE_FOR_COMMAND.get(cmd, runtime.CONSOLE_EXE)


def has_exe(root) -> bool:
    """根目录里是不是躺着打包好的程序（决定启动器怎么写）。"""
    if runtime.frozen():
        return True
    root = Path(root)
    if not plat.IS_WINDOWS:
        return (root / "meowbox-panel").is_file() or (root / "meowbox").is_file()
    return (root / runtime.CONSOLE_EXE).is_file() or (root / exe_for("tray")).is_file()


def write_launchers(root, python: str | None = None) -> list[Path]:
    """把双击启动器写到喵盒根目录，返回生成的文件路径。"""
    if not plat.IS_WINDOWS:
        return _write_desktops(root, python)
    root = Path(root)
    use_exe = has_exe(root)
    py = python or util.preferred_pythonw()
    made = []
    for filename, cmd, label in LAUNCHERS:
        target = root / filename
        if use_exe:
            text = VBS_EXE_TEMPLATE.format(label=label, exe=exe_for(cmd))
        else:
            text = VBS_TEMPLATE.format(label=label, cmd=cmd, py=py)
        target.write_text(text, encoding="utf-16")
        made.append(target)
    return made


# ------------------------------------------------------------------ Linux：.desktop
DESKTOP_TEMPLATE = """[Desktop Entry]
Type=Application
Version=1.0
Name={label}
Comment=本地 Web 开发环境管家（喵盒）
Exec={exec_line}
Path={root}
Terminal=false
StartupNotify=false
Categories=Development;
{icon_line}"""


def _desktop_exec(root: Path, cmd: str, python: str | None = None) -> str:
    """Desktop Entry 的 Exec= 一行（带正确的引号）。

    `root` 是**数据目录**，`meowbox.py` 可能在另一个地方（代码和数据分开）
    —— 所以脚本路径要问 `runtime.app_root()`，别拿 root 去拼。
    """
    root = Path(root)
    app = Path(runtime.app_root())
    exe_name = {"gui": "meowbox-panel", "tray": "meowbox-tray"}.get(cmd)
    if exe_name and (app / exe_name).is_file():
        return f'"{app / exe_name}" --root "{root}"'
    py = python or util.preferred_pythonw()
    script = app / "meowbox.py"
    return f'"{py}" "{script}" --root "{root}" {cmd}'


def _icon_line(root: Path) -> str:
    for name in ("meowbox.png", "meowbox.svg", "meowbox.ico"):
        candidate = Path(root) / "assets" / name
        if candidate.is_file():
            return f"Icon={candidate}\n"
    return ""


def desktop_entry(root, cmd: str = "gui", label: str = "喵盒 Meow Box",
                  python: str | None = None) -> str:
    root = Path(root).resolve()
    return DESKTOP_TEMPLATE.format(
        label=label,
        exec_line=_desktop_exec(root, cmd, python),
        root=root,
        icon_line=_icon_line(root),
    )


def _write_desktops(root, python: str | None = None) -> list[Path]:
    root = Path(root)
    target = root / "meowbox-panel.desktop"
    target.write_text(desktop_entry(root, "gui", python=python),
                      encoding="utf-8", newline="\n")
    target.chmod(0o755)
    return [target]


def _autostart_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "autostart"


def autostart_path() -> Path:
    return _autostart_dir() / "meowbox.desktop"


def xdg_autostart_enabled(root=None) -> bool:
    return autostart_path().is_file()


def set_xdg_autostart(root, enable: bool) -> Path:
    """开机自启：往 `~/.config/autostart/` 放/删一个 .desktop（不碰 systemd）。"""
    path = autostart_path()
    if not enable:
        path.unlink(missing_ok=True)
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(desktop_entry(root, "gui", label="喵盒 Meow Box（开机自启）"),
                    encoding="utf-8", newline="\n")
    path.chmod(0o644)
    return path


def has_desktop(root) -> bool:
    """Linux：根目录里有没有生成过 .desktop。"""
    return (Path(root) / "meowbox-panel.desktop").is_file()


def _read(path) -> str:
    path = Path(path)
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-16")
    except (OSError, UnicodeError):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""


def launcher_python(path) -> str | None:
    """从已生成的启动器里把解释器路径抠出来（便携检查用）。"""
    text = _read(path)
    if not text:
        return None
    marker = 'sh.Run """'
    start = text.find(marker)
    if start < 0:
        return None
    start += len(marker)
    end = text.find('""', start)
    return text[start:end] if end > start else None


def launcher_target(path) -> tuple[str, str] | None:
    """启动器指向什么：`("exe", 文件名)` 还是 `("python", 解释器路径)`。"""
    text = _read(path)
    if not text:
        return None
    for _filename, cmd, _label in LAUNCHERS:
        name = exe_for(cmd)
        if name in text:
            return ("exe", name)
    py = launcher_python(path)
    return ("python", py) if py else None
