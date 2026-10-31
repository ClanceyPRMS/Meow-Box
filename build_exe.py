"""把喵盒打包成 exe（PyInstaller 的贴心外壳）。

    python build_exe.py            # 只打包，产物在 dist/MeowBox/
    python build_exe.py --install  # 打完顺手放进喵盒根目录
    python build_exe.py --verify   # 打完跑一遍 info/status 自检

为什么用**系统装的** `python` 而不是 venv / 嵌入式解释器：
Windows 自带的 `ctypes.wintypes` 在精简解释器里可能缺 `HCURSOR`，托盘会当场炸。
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist" / "MeowBox"
EXES = ("meowbox.exe", "meowbox-tray.exe", "meowbox-panel.exe")


def human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def ensure_pyinstaller() -> None:
    """没有 PyInstaller 就别自动装了。

    以前这里是「没装就 pip install pyinstaller」—— 装到的是**最新版**。
    哪天 PyInstaller 大版本一变，onedir 结构或 hook 行为就跟着变，打出来的包
    跟上次不一样，而这种漂移是最难查的一类问题。版本现在锁在 `uv.lock` 里。
    """
    try:
        import PyInstaller  # noqa: F401
        return
    except ImportError:
        pass
    print("✗ 没有 PyInstaller，先准备开发环境：")
    print("    uv sync                          # 按 uv.lock 装，版本锁死（推荐）")
    print("    pip install pyinstaller==6.22.3  # 没装 uv 的话")
    raise SystemExit(1)


def build(clean: bool = True) -> None:
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm"]
    if clean:
        cmd.append("--clean")
    cmd.append(str(ROOT / "meowbox.spec"))
    print("→ " + " ".join(cmd))
    started = time.time()
    subprocess.check_call(cmd, cwd=str(ROOT))
    print(f"→ 打包完成，用了 {time.time() - started:.0f} 秒")


def verify() -> int:
    """跑一遍真正的命令，看 exe 起的来、根目录认的对不对。"""
    rc_all = 0
    for exe in EXES[:1]:                      # 界面版会弹窗，自检只跑命令行版
        path = DIST / exe
        if not path.is_file():
            print(f"❌ 没找到 {path}")
            rc_all = 1
            continue
        print(f"\n=== {exe} info ===")
        rc = subprocess.call([str(path), "info"], cwd=str(DIST))
        print(f"=== 退出码 {rc} ===")
        rc_all |= rc
        print(f"\n=== {exe} status ===")
        rc = subprocess.call([str(path), "status"], cwd=str(DIST))
        print(f"=== 退出码 {rc} ===")
        rc_all |= rc
    return rc_all


def running_exes() -> list[str]:
    """哪些喵盒 exe 正在跑（跑着的时候覆盖会 PermissionError，而且会把 _internal 删半截）。"""
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                         capture_output=True).stdout.decode("gbk", "replace")
    alive = []
    for exe in EXES:
        if exe.lower() in out.lower():
            alive.append(exe)
    return alive


def install() -> None:
    """把 exe 和 _internal 放进喵盒根目录（= 真正的便携版安装）。

    两步走，别直接 rmtree 老 _internal —— 中途被锁会留下一个「半截的包」，
    exe 当场变砖（实测踩过）。所以：先整个复制成 `_internal.new`，成功了再换名。
    """
    if not DIST.is_dir():
        raise SystemExit("还没打包：先跑 build_exe.py")

    busy = running_exes()
    if busy:
        print("⚠️  这些还在跑，先从托盘/面板退出（或「停止全部」）：" + "、".join(busy))
        raise SystemExit(2)

    for exe in EXES:
        src = DIST / exe
        if src.is_file():
            shutil.copy2(src, ROOT / exe)
            print(f"  ✅ {exe}")

    src_dir = DIST / "_internal"
    new_dir = ROOT / "_internal.new"
    #: 老包的名字必须**唯一**：上次要是被锁（杀软 / 还在跑的 dll）留下了
    #: `_internal.old`，直接 rename 过来就是 WinError 183，装到一半就断。
    old_dir = ROOT / f"_internal.old-{time.strftime('%Y%m%d-%H%M%S')}"
    _sweep_leftovers()
    if new_dir.exists():
        shutil.rmtree(new_dir, ignore_errors=True)
    shutil.copytree(src_dir, new_dir)
    size = sum(p.stat().st_size for p in new_dir.rglob("*") if p.is_file())

    dst = ROOT / "_internal"
    if dst.exists():
        dst.rename(old_dir)                       # 老包先挪开，别删（删一半就变砖）
    new_dir.rename(dst)
    try:
        shutil.rmtree(old_dir)
    except OSError as exc:                        # 被锁的 dll：留着，下次再扫
        print(f"  ⚠️ 旧包没删干净（{exc.__class__.__name__}）：{old_dir.name}，下次打包会自动再试")
    print(f"  ✅ _internal/（{human(size)}）")
    print("\n  现在双击 meowbox-panel.exe / meowbox-tray.exe 就能用，不用装 Python。")
    print("  旧的双击启动器（.vbs）跑一次 `meowbox.exe shortcut` 就会改成指向 exe。")


def _sweep_leftovers() -> None:
    """清掉上次打包留下的 `_internal.old*`（删不掉就留着，不报错）。"""
    for path in sorted(ROOT.glob("_internal.old*")):
        try:
            shutil.rmtree(path)
        except OSError:
            print(f"  · {path.name} 还被锁着，先留着")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="打包喵盒成 exe")
    ap.add_argument("--install", action="store_true", help="打包完放进喵盒根目录")
    ap.add_argument("--verify", action="store_true", help="打包完跑一遍自检")
    ap.add_argument("--no-clean", action="store_true", help="复用上次的 build/（快，但容易带旧文件）")
    args = ap.parse_args(argv)

    ensure_pyinstaller()
    build(clean=not args.no_clean)

    if not DIST.is_dir():
        print("❌ 没生成 dist/MeowBox，看看上面的报错")
        return 1

    total = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file())
    print(f"\n产物：{DIST}  共 {human(total)}")
    for exe in EXES:
        path = DIST / exe
        print(f"  {'✅' if path.is_file() else '❌'} {exe:<20} "
              f"{human(path.stat().st_size) if path.is_file() else '—'}")

    rc = 0
    if args.verify:
        rc = verify()
    if args.install:
        install()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
