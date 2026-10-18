"""打包入口：系统托盘 `meowbox-tray.exe`（窗口子系统，双击不弹黑框）。

等价于源码模式的 `pythonw meowbox.py tray`；附加参数原样转给 CLI，
所以 `meowbox-tray.exe --selftest` 也能用。
"""
from __future__ import annotations

import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def run() -> int:
    from meow import runtime
    runtime.prepare_stdio()
    import meowbox
    return meowbox.main(["tray", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(run())
