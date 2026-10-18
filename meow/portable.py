"""便携检查：喵盒到底有多"绿色"？

判断标准就三条：

1. 配置里**不写死绝对路径**（`root` 是 `"."`，网站目录、provider 尽量相对）；
2. 一切运行数据都在自己的文件夹里（`bin/ data/ logs/ run/ etc/ www/`）；
3. 不装服务、不写注册表 —— 唯一的"系统痕迹"是「启动」文件夹里那个
   `.vbs`（开机自启，删了就干净）。

搬文件夹之后如果自启失效，重跑一次 `autostart on` 就行。
"""
from __future__ import annotations

from pathlib import Path

from . import config as cfgmod
from .util import fwd

OK, WARN, INFO = "ok", "warn", "info"


def report(cfg: dict, bins: dict | None = None) -> dict:
    """返回 {"checks": [(等级, 项目, 说明), ...], "portable": bool}。"""
    root = Path(cfg["root"])
    checks: list[tuple[str, str, str]] = []
    try:
        raw = cfgmod.raw_config(root)
    except OSError:
        raw = {}

    # 1. 配置里的根目录
    raw_root = str(raw.get("root", ""))
    if raw_root in (".", "./", ""):
        checks.append((OK, "根目录", '配置里写的是 "."（= 配置文件所在文件夹）'))
    else:
        checks.append((WARN, "根目录",
                       f'配置里写死了 {raw_root}，搬到别的盘要改这一行。'
                       ' 跑一次 meowbox.py portable --fix 就会改成 "."'))

    # 2. 网站目录
    raw_www = str((raw.get("sites") or {}).get("www", ""))
    www = cfgmod.layout(cfg)["www"]
    if raw_www and not Path(raw_www).is_absolute():
        checks.append((OK, "网站目录", f'{raw_www}（相对）→ {fwd(www)}'))
    else:
        checks.append((WARN, "网站目录",
                       f"{raw_www}（绝对路径，建议改成相对 www/）"))

    # 3. 组件来源
    provider = cfg.get("provider")
    if not provider:
        checks.append((OK, "组件来源", "喵盒自己的 bin/（完全自带）"))
    else:
        p = cfgmod.resolve(root, provider)
        inside = str(p).lower().startswith(str(root).lower())
        note = f"{fwd(p)}"
        if inside:
            checks.append((OK, "组件来源", f"自己的 bin/（{note}）"))
        else:
            checks.append((INFO, "组件来源",
                           f"借用外部安装（{note}）—— 换机器/换盘要确认这个目录还在，"
                           f"或者 python meowbox.py pkg install 装到自己 bin/"))

    # 4. 运行数据都在根目录里
    lay = cfgmod.layout(cfg)
    outside = [key for key in ("www", "etc", "logs", "data", "run")
               if not str(lay[key]).lower().startswith(str(root).lower())]
    if outside:
        checks.append((WARN, "运行数据", "有目录跑到喵盒外面去了：" + ", ".join(outside)))
    else:
        checks.append((OK, "运行数据", "www / etc / logs / data / run 全在喵盒文件夹里"))

    # 5. 系统痕迹
    checks.append((OK, "系统服务", "不装任何 Windows 服务（start/stop 都是普通进程）"))
    checks.append((OK, "注册表", "不写注册表（唯一例外：SSL 根证书装进当前用户证书库，可 untrust 撤销）"))

    # 6. 开机自启：启动文件夹里的 .vbs
    try:
        from . import tray
        link = tray.startup_link()
        enabled = tray.autostart_enabled()
    except Exception:                       # noqa: BLE001 —— 非 Windows / 托盘模块挂了
        link, enabled = None, False
    if enabled and link is not None:
        from . import runtime
        expected = runtime.launch_argv("tray")[0]       # exe 模式 = meowbox-tray.exe
        text = ""
        try:
            text = link.read_text(encoding="utf-16")
        except (OSError, UnicodeError):
            try:
                text = link.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
        if expected in text:
            checks.append((OK, "开机自启", f"「启动」文件夹里的脚本指向当前目录（{fwd(link)}）"))
        else:
            checks.append((WARN, "开机自启",
                           "自启脚本指向的还是旧路径（文件夹搬过家）。"
                           " 跑一次 meowbox.py autostart on 重新生成"))
    else:
        checks.append((INFO, "开机自启", "没开（开了之后是「启动」文件夹里一个 .vbs，不进注册表）"))

    # 7. 双击启动器
    from . import launcher
    for filename, _cmd, _label in launcher.LAUNCHERS:
        path = root / filename
        if not path.is_file():
            checks.append((INFO, filename, "还没生成（meowbox.py shortcut）"))
            continue
        target = launcher.launcher_target(path)
        if not target:
            checks.append((WARN, filename, "看不出它指向哪（重跑 shortcut）"))
            continue
        kind, value = target
        if kind == "exe":
            ok = (root / value).is_file()
            checks.append((OK if ok else WARN, filename,
                           f"打包版，直接启动 {value}" if ok else f"找不到 {value}（重跑 shortcut）"))
        elif Path(value).is_file():
            checks.append((OK, filename, f"解释器在线：{value}"))
        else:
            checks.append((WARN, filename, f"解释器不见了：{value}（重跑 shortcut）"))

    portable = not any(level == WARN for level, _item, _note in checks)
    return {"checks": checks, "portable": portable}


def fix(cfg: dict) -> Path:
    """把配置改写成相对路径（真正搬家的前提）。"""
    return cfgmod.save(cfg)
