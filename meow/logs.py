"""日志的追加（带滚动）与清理。

喵盒的日志分两类，**别一刀切**：

* **喵盒自己写的**（`https-check.log`、`vendor-run.log`…）：有诊断价值，
  按大小滚动，留两份备份（`.1` `.2`），所以它永远不会无限长；
* **服务器自己写的**（apache/nginx 的 access/error、`site-*.log`、`*-console.log`）：
  人家进程开着句柄在那儿写，喵盒**不能替它改名**（Windows 上会 PermissionError），
  只能在"太大 / 太旧"时截断或删掉；被占着就如实报告"跳过"。

清理的规矩（`clean()`）：

* 超过 `keep_days` 天没动过 → 删；
* 单个文件超过 `max_bytes` → 截断到 0；
* 删/截失败（文件被服务占用）→ 记进 `skipped`，并提示"先停服务"；
* `dry_run=True` 只算不动，面板里的"预览一下"就是这个。
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from . import util

CHECK_LOG = "https-check.log"                 # 体检记录（喵盒自己写的）
CHECK_MAX_BYTES = 512 * 1024                  # 它超过就滚动
CHECK_BACKUPS = 2                             # 留 .1 .2

DEFAULT_MAX_BYTES = 2 * 1024 * 1024           # 服务器日志：单文件 2MB 就算大
DEFAULT_KEEP_DAYS = 7                         # 7 天没动过就清掉
WARN_TOTAL_BYTES = 20 * 1024 * 1024           # 面板启动时：总量过 20MB 就提一句


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


# ---------------------------------------------------------------- 追加 + 滚动
def _rotate(path: Path, backups: int) -> bool:
    """xxx.log → xxx.log.1 → xxx.log.2（旧的往后挪，最老的丢掉）。"""
    for index in range(backups - 1, 0, -1):
        src = path.with_name(f"{path.name}.{index}")
        dst = path.with_name(f"{path.name}.{index + 1}")
        if src.is_file():
            try:
                src.replace(dst)
            except OSError:
                pass
    try:
        path.replace(path.with_name(f"{path.name}.1"))
        return True
    except OSError:
        return False


def append_log(path, text: str, max_bytes: int = CHECK_MAX_BYTES,
               backups: int = CHECK_BACKUPS) -> Path:
    """追加一段日志；超过 `max_bytes` 就先滚动一份再写。"""
    path = Path(path)
    util.ensure_dir(path.parent)
    if path.is_file():
        try:
            if path.stat().st_size >= max_bytes:
                _rotate(path, backups)
        except OSError:
            pass
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(text.rstrip() + "\n")
    return path


# ---------------------------------------------------------------- 统计 / 清理
def survey(logs_dir) -> dict:
    """看看 logs/ 里都有什么、多大、多久没动过。"""
    logs_dir = Path(logs_dir)
    entries = []
    now = time.time()
    for path in sorted(logs_dir.glob("*")):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append({
            "name": path.name,
            "path": path,
            "size": stat.st_size,
            "age_days": (now - stat.st_mtime) / 86400,
        })
    entries.sort(key=lambda item: item["size"], reverse=True)
    return {
        "dir": logs_dir,
        "files": entries,
        "total": sum(item["size"] for item in entries),
        "count": len(entries),
    }


def busy_prefixes(cfg: dict, bins: dict | None = None) -> set[str]:
    """哪些日志现在被服务占着（占着就别截断 —— 会写出空洞，先停服务）。

    返回前缀集合：`apache` / `nginx` / `mysql` / `mailpit` / `site-`。
    """
    from . import services as svcmod
    services = svcmod.build(cfg, bins or {})
    prefixes: set[str] = set()
    web = svcmod.web(services)
    if web is not None and web.available and web.running():
        prefixes.add(web.name)                    # apache / nginx
        prefixes.update({"apache", "nginx", "site-"})
    for name in ("mysql", "mailpit"):
        svc = services.get(name)
        if svc is not None and svc.running():
            prefixes.add(name)
    return prefixes


def _prefix(name: str) -> str:
    return "site-" if name.startswith("site-") else name.split("-")[0]


def clean(logs_dir, max_bytes: int = DEFAULT_MAX_BYTES,
          keep_days: int = DEFAULT_KEEP_DAYS, dry_run: bool = False,
          busy: set[str] | None = None) -> dict:
    """删太旧的、截断太大的。返回做了什么（以及哪些因为被占用没做成）。

    `busy` 里的前缀表示「这些服务在跑，它的日志别截断」：在 Windows 上把
    别的进程正写着的文件清零，那进程会继续按旧偏移写，文件前段出现一大堆
    空洞（NUL），所以宁可跳过，让它先停。
    """
    logs_dir = Path(logs_dir)
    busy = busy or set()
    removed, truncated, skipped = [], [], []
    freed = 0
    now = time.time()
    for path in sorted(logs_dir.glob("*.log*")):
        if not path.is_file():
            continue
        if path.name == CHECK_LOG or path.name.startswith(f"{CHECK_LOG}."):
            continue                      # 体检记录自己会滚动，不参与清理
        try:
            stat = path.stat()
        except OSError:
            continue
        too_old = (now - stat.st_mtime) / 86400 > keep_days
        too_big = stat.st_size > max_bytes
        if not (too_old or too_big):
            continue
        action = "删除" if too_old else "截断"
        if too_old and _prefix(path.name) in busy:
            skipped.append((path.name, stat.st_size, action, "服务在跑，先停服务"))
            continue
        if too_big and _prefix(path.name) in busy:
            skipped.append((path.name, stat.st_size, action, "服务正在写它，先停服务"))
            continue
        if dry_run:
            (removed if too_old else truncated).append((path.name, stat.st_size))
            freed += stat.st_size
            continue
        try:
            if too_old:
                path.unlink()
                removed.append((path.name, stat.st_size))
            else:
                with open(path, "w", encoding="utf-8"):
                    pass                     # 就地清零（服务停着才安全）
                truncated.append((path.name, stat.st_size))
            freed += stat.st_size
        except OSError as exc:               # 还是被谁占着
            skipped.append((path.name, stat.st_size, action, exc.strerror or str(exc)))
    return {
        "removed": removed,
        "truncated": truncated,
        "skipped": skipped,
        "freed": freed,
        "dry_run": dry_run,
    }


def summary_text(info: dict) -> str:
    """把 survey() 的结果说成人话。"""
    lines = [f"日志目录：{util.fwd(info['dir'])}"]
    lines.append(f"共 {info['count']} 个文件，{human(info['total'])}")
    if not info["count"]:
        return "\n".join(lines)
    lines.append("")
    for item in info["files"][:12]:
        lines.append(f"  {human(item['size']):>8}   {item['name']}"
                     f"   （{item['age_days']:.0f} 天没动）")
    if info["count"] > 12:
        lines.append(f"  …… 还有 {info['count'] - 12} 个没列")
    return "\n".join(lines)


def clean_text(result: dict) -> str:
    """把 clean() 的结果说成人话。"""
    head = "预览（没动任何文件）：" if result["dry_run"] else "清理完成："
    lines = [head]
    if result["removed"]:
        lines.append("  删掉：")
        lines.extend(f"    {name}（{human(size)}）" for name, size in result["removed"])
    if result["truncated"]:
        lines.append("  截断：")
        lines.extend(f"    {name}（{human(size)} → 0）" for name, size in result["truncated"])
    if result["skipped"]:
        lines.append("  跳过（还被服务占着，先 stop 再清）：")
        lines.extend(f"    {name}（{human(size)}，本来要{action}：{why}）"
                     for name, size, action, why in result["skipped"])
    if not (result["removed"] or result["truncated"] or result["skipped"]):
        lines.append("  没有需要收拾的日志 —— 已经很干净了")
    elif result["freed"]:
        lines.append(f"  合计腾出 {human(result['freed'])}")
    return "\n".join(lines)
