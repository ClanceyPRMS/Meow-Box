"""打包入口：命令行版 `meowbox.exe`。

源码模式下它是 `python meowbox.py`；打出来的 exe 走同一个 `meowbox.main()`，
所以参数、输出、退出码完全一样。
"""
from __future__ import annotations

import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def run() -> int:
    import meowbox
    return meowbox.main()


if __name__ == "__main__":
    raise SystemExit(run())
