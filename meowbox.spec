# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包脚本 —— 一次产出三个 exe，共用一份 `_internal/`。

用法（在 D:\\meowbox 下）：

    python -m PyInstaller --noconfirm meowbox.spec

产物（dist/MeowBox/）：

    meowbox.exe        命令行版（控制台子系统，跟 python meowbox.py 一样）
    meowbox-tray.exe   系统托盘（窗口子系统，双击不弹黑框）
    meowbox-panel.exe  图形控制面板（窗口子系统，双击不弹黑框）
    _internal/         依赖 + templates/ + assets/

三个 exe 共用一份 `_internal/`：PyInstaller 的 onedir 模式，启动快、不需要
每次解压到临时目录（onefile 每次启动要解 30MB+，还会被杀软盯上）。
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve()          # noqa: F821 —— PyInstaller 注入的变量

#: 只读资源：模板和图标（根目录里没有时才用这份，见 meow/runtime.py）
datas = [
    (str(ROOT / "templates"), "templates"),
    (str(ROOT / "assets" / "meowbox.ico"), "assets"),
    (str(ROOT / "assets" / "meowbox-preview.png"), "assets"),
]

#: 懒加载的模块（`from meow import gui` 写在函数里）静态分析看不见，全部收进来
hiddenimports = collect_submodules("meow") + [
    "tkinter",
    "tkinter.messagebox",
    "tkinter.simpledialog",
    "tkinter.ttk",
]

excludes = ["numpy", "pandas", "matplotlib", "PIL", "pytest", "setuptools", "pip"]

ICON = str(ROOT / "assets" / "meowbox.ico")

COMMON = dict(
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)


def _analysis(entry: str):
    return Analysis([str(ROOT / "packaging" / entry)], **COMMON)   # noqa: F821


def _exe(analysis, name: str, console: bool):
    pyz = PYZ(analysis.pure)                                     # noqa: F821
    return EXE(                                                   # noqa: F821
        pyz,
        analysis.scripts,
        [],
        exclude_binaries=True,
        name=name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=console,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=ICON,
    )


def _merge(*tocs):
    """三份依赖合并去重（key = 目标路径），免得 COLLECT 里重复报警。"""
    seen, out = set(), []
    for toc in tocs:
        for entry in toc:
            if entry[0] in seen:
                continue
            seen.add(entry[0])
            out.append(entry)
    return out


a_cli = _analysis("entry_cli.py")
a_tray = _analysis("entry_tray.py")
a_panel = _analysis("entry_panel.py")

exe_cli = _exe(a_cli, "meowbox", console=True)
exe_tray = _exe(a_tray, "meowbox-tray", console=False)
exe_panel = _exe(a_panel, "meowbox-panel", console=False)

COLLECT(                                                        # noqa: F821
    exe_cli,
    exe_tray,
    exe_panel,
    _merge(a_cli.binaries, a_tray.binaries, a_panel.binaries),
    _merge(a_cli.datas, a_tray.datas, a_panel.datas),
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MeowBox",
)
