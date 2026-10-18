"""打包入口：图形控制面板 `meowbox-panel.exe`（窗口子系统，不弹黑框）。

等价于源码模式的 `pythonw meowbox.py gui`。没有配置也会先初始化一个，
跟命令行一致（`cmd_gui` 里做的）。附加参数原样转给 CLI。
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
    return meowbox.main(["gui", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(run())
