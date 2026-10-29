#!/usr/bin/env python3
"""喵盒 (Meow Box) —— 命令行入口。

    python meowbox.py init                     初始化（默认从 D:/laragon 借 bin/）
    python meowbox.py info                     看配置 + 探测到的组件
    python meowbox.py list                     列出 www/ 里的站点
    python meowbox.py site add blog            新建站点 blog.loc
    python meowbox.py site remove blog         删站点
    python meowbox.py render                   重新生成所有配置文件
    python meowbox.py check                    让当前 Web 服务器自检语法
    python meowbox.py web                      看现在用的是 Apache 还是 Nginx
    python meowbox.py web nginx                换成 Nginx（会先停掉 Apache）
    python meowbox.py hosts --apply            把站点写进 hosts（要管理员）
    python meowbox.py start all                启动当前 Web 服务器 + mysql
    python meowbox.py stop  web                停止当前 Web 服务器（apache/nginx 都行）
    python meowbox.py status                   看运行状态
    python meowbox.py mail                     看 Mailpit（本地假邮箱）状态
    python meowbox.py mail open                打开收件箱网页
    python meowbox.py mail clear               清空信箱（要先停掉）
    python meowbox.py portable                 便携检查（能不能整个文件夹搬走）
    python meowbox.py logs                     看日志占用 / 该不该清
    python meowbox.py logs clean --dry-run     预览要清哪些日志（太旧/太大）
    python meowbox.py logs clean               真的清掉（服务在写的会跳过）
    python meowbox.py ssl check                HTTPS 体检（缺哪层说哪层，并记进 logs/）
    python meowbox.py migrate                  从 Laragon 迁站点+数据库（先预览）
    python meowbox.py migrate --yes --tld auto 真搬，顺带把域名后缀改成跟 Laragon 一样
    python meowbox.py vendor                   彻底自带：组件搬进自己的 bin/（先预览）
    python meowbox.py gui                      打开控制面板（所有功能都能点出来）
    python meowbox.py db init                  初始化 MySQL 数据目录
    python meowbox.py pkg list                 看软件清单
    python meowbox.py pkg install PHP-8.4      下载并解压一个包
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

# 允许「双击 / 任意目录下 python meowbox.py」都能跑。打包成 exe 之后代码已经在
# 包里了，不用再加路径（PyInstaller 会把 sys._MEIPASS 塞进 sys.path）。
HERE = Path(__file__).resolve().parent
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(HERE))

from meow import __version__                      # noqa: E402
from meow import config as cfgmod                 # noqa: E402
from meow import runtime                          # noqa: E402
from meow import hosts as hostsmod                # noqa: E402
from meow import launcher as launchmod            # noqa: E402
from meow import migrate as migratemod            # noqa: E402
from meow import packages as pkgmod               # noqa: E402
from meow import portable as portablemod          # noqa: E402
from meow import services as svcmod               # noqa: E402
from meow import util, vhost                      # noqa: E402
from meow import vendor as vendormod              # noqa: E402
from meow.util import ensure_dir, fwd             # noqa: E402

#: 喵盒根目录。源码模式 = meowbox.py 所在目录；exe 模式 = exe 所在目录
#: （配置里存的永远是相对路径，所以 exe 放在哪、整个文件夹挪到哪个盘都不坏）。
ROOT = runtime.app_root()

WELCOME_PHP = """<?php
// 喵盒欢迎页 —— 说明你的环境已经跑起来了 :3
phpinfo();
"""


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def _title(text: str) -> None:
    print(f"\n🐾 {text}")
    print("─" * 46)


def _row(name: str, value: str) -> None:
    print(f"  {name:<12} {value}")


def _ensure_templates(root: Path) -> None:
    """把模板复制到喵盒根目录（首次 init 时）。

    源码模式从 `meowbox.py` 旁边的 templates/ 拷；exe 模式从包里
    （`_internal/templates`）拷 —— 拷出来是给用户改的，改完优先用根目录那份。
    """
    src = runtime.templates_dir()
    dst = root / "templates"
    if src.resolve() == dst.resolve():
        dst.mkdir(parents=True, exist_ok=True)
        return
    dst.mkdir(parents=True, exist_ok=True)
    for tpl in src.glob("*"):
        target = dst / tpl.name
        if not target.exists():
            shutil.copy2(tpl, target)


def _load(root: Path):
    cfg = cfgmod.load(root)
    bins = cfgmod.discover(cfg)
    return cfg, bins


def _web(services: dict):
    """当前的 Web 服务器对象（Apache 或 Nginx，看配置）。"""
    svc = svcmod.web(services)
    if svc is None:
        raise RuntimeError("配置里没认出 Web 服务器（web.server 只能是 apache / nginx）")
    return svc


def _php_summary(cfg: dict) -> str:
    """「PHP 8.3（默认）+ 8.5」这样的一行。"""
    from meow import php as phpmod
    versions = phpmod.scan(cfg)
    if not versions:
        return "没装 PHP"
    default = phpmod.default_label(cfg, versions)
    others = [l for l in versions if l != default]
    text = f"PHP {default}（默认）"
    if others:
        text += " + " + "、".join(others)
    return text


def _server_line(cfg: dict, bins: dict) -> str:
    """「nginx（已就绪，PHP 走 FastCGI 127.0.0.1:9000）」这样的一行人话。"""
    name = svcmod.server_name(cfg)
    found = bins.get(name)
    if not found:
        return f"{svcmod.pretty(name)} —— 没找到程序目录"
    from meow import php as phpmod
    versions = phpmod.scan(cfg)
    ports = phpmod.port_map(cfg, versions)
    if name == "nginx":
        pairs = "、".join(f"{l}→{p}" for l, p in ports.items()) or "没装 PHP"
        return f"{svcmod.pretty(name)}（PHP 走 FastCGI：{pairs}）"
    default = phpmod.default_label(cfg, versions)
    return f"{svcmod.pretty(name)}（PHP {default or '—'} 以模块方式挂载）"


# --------------------------------------------------------------------------
# 命令
# --------------------------------------------------------------------------
def cmd_init(args) -> int:
    root = Path(args.root).resolve()
    provider = Path(args.provider).resolve() if args.provider else None
    ensure_dir(root)
    _ensure_templates(root)

    cfg = cfgmod.default_config(root, provider)
    # default_config 里 root 是 "."（便携写法），先换成绝对路径干活，save() 会再写回 "."
    cfg["root"] = fwd(root)
    cfg["sites"]["www"] = fwd(root / "www")
    cfgmod.save(cfg)
    lay = cfgmod.bootstrap_dirs(cfg)
    pkgmod.ensure_menu(cfg)

    welcome = lay["www"] / "index.php"
    if not welcome.exists():
        welcome.write_text(WELCOME_PHP, encoding="utf-8")

    bins = cfgmod.discover(cfg)
    _title(f"喵盒已就位（v{__version__}）")
    _row("根目录", fwd(root))
    _row("网站目录", fwd(lay["www"]))
    _row("二进制来源", fwd(provider) if provider else "喵盒自己的 bin/")
    for name, path in bins.items():
        _row(name, fwd(path) if path else "— 没找到")
    _row("HTTP 端口", str(cfg["web"]["http_port"]))
    _row("站点后缀", "." + cfg["sites"]["tld"])
    print("\n  下一步：  " + runtime.localize("python meowbox.py list")
          + "  →  render  →  start all")
    return 0


def cmd_info(args) -> int:
    cfg, bins = _load(Path(args.root))
    lay = cfgmod.layout(cfg)
    _title(f"喵盒 v{__version__}")
    _row("根目录", cfg["root"])
    _row("二进制来源", cfg.get("provider") or "本地 bin/")
    _row("Web 服务器", _server_line(cfg, bins))
    _row("站点后缀", "." + cfg["sites"]["tld"])
    _row("HTTP/HTTPS", f"{cfg['web']['http_port']} / {cfg['web']['https_port']}  ssl={cfg['web']['ssl']}")
    _row("PHP", _php_summary(cfg))
    _row("MySQL 端口", str(cfg["mysql"]["port"]))
    mail = cfg.get("mailpit") or {}
    if mail.get("enabled", True):
        _row("Mailpit", f"SMTP {mail.get('smtp_port', 1025)} / 收件箱 {mail.get('http_port', 8025)}")
    _row("管理员权限", "是" if util.is_admin() else "否（写 hosts 需要）")
    print("\n  探测到的组件：")
    for name, path in bins.items():
        _row(name, fwd(path) if path else "— 没找到")
    print("\n  目录：")
    for key in ("www", "etc_apache", "sites_enabled", "logs", "data"):
        _row(key, fwd(lay[key]))
    return 0


def _site_names(cfg) -> list[str]:
    return vhost.scan(cfgmod.layout(cfg)["www"])


def cmd_list(args) -> int:
    cfg, _ = _load(Path(args.root))
    from meow import php as phpmod

    lay = cfgmod.layout(cfg)
    names = _site_names(cfg)
    tld = cfg["sites"]["tld"]
    versions = phpmod.scan(cfg)
    current = set(hostsmod.current_hostnames(
        hostsmod.read(getattr(args, "hosts_file", None) or hostsmod.default_path())
    ))
    _title(f"站点（{len(names)} 个）")
    if not names:
        print("  （空的。用 `site add 名字` 建一个）")
        return 0
    for name in names:
        host = vhost.hostname_for(name, tld)
        url = vhost.site_url(cfg, lay, name)
        label = phpmod.site_label(cfg, name, versions) or "—"
        flag = "✅ hosts 已登记" if host in current else "⚠️  hosts 里还没有"
        print(f"  {name:<20} {url:<28} PHP {label:<6} {flag}")
    return 0


def cmd_site(args) -> int:
    cfg, _ = _load(Path(args.root))
    lay = cfgmod.layout(cfg)
    name = args.name.strip().strip("/\\")
    if not name:
        print("站点名字不能是空的")
        return 1
    target = lay["www"] / name

    if args.action == "add":
        if target.exists():
            print(f"已经存在了：{fwd(target)}")
            return 0
        ensure_dir(target)
        (target / "index.php").write_text(
            f"<?php\n// {name} —— 由喵盒创建\necho '喵～ {name} 跑起来了';\n",
            encoding="utf-8",
        )
        host = vhost.hostname_for(name, cfg["sites"]["tld"])
        print(f"已创建 {fwd(target)}")
        print(f"  接着跑： python meowbox.py render")
        print(f"          python meowbox.py hosts --apply   （要管理员，加 {host} → 127.0.0.1）")
        return 0

    if not target.exists():
        print(f"没有这个站点：{fwd(target)}")
        return 1
    shutil.rmtree(target)
    from meow import php as phpmod
    if phpmod.prunes(cfg, _site_names(cfg)):
        cfgmod.save(cfg)
    print(f"已删除 {fwd(target)}")
    return 0


_TLD_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def _validate_tld(tld: str) -> str | None:
    """返回错误说明；None 表示合法。"""
    if not tld:
        return "后缀不能为空"
    if "." in tld:
        return "后缀里不要带点 —— 域名是拼出来的：<站点名>.<后缀>"
    if not _TLD_RE.match(tld):
        return "后缀只能用小写字母、数字和连字符，且不能以连字符开头或结尾"
    return None


def cmd_tld(args) -> int:
    cfg, bins = _load(Path(args.root))
    current = cfg["sites"]["tld"]

    if not args.value:
        _title("站点域名后缀")
        _row("当前", "." + current)
        _row("示例", vhost.hostname_for("blog", current))
        print("\n  改法： python meowbox.py tld <后缀>")
        print("  比如： python meowbox.py tld meow")
        return 0

    tld = args.value.strip().lstrip(".").lower()
    error = _validate_tld(tld)
    if error:
        print(f"❌ {error}")
        return 1
    if tld == current:
        print(f"已经是 .{tld} 了，没变")
        return 0

    cfg["sites"]["tld"] = tld
    cfgmod.save(cfg)

    server = _web(svcmod.build(cfg, bins))
    regenerated = server.available
    if regenerated:
        server.render()

    names = _site_names(cfg)
    _title("域名后缀已更新")
    _row("之前", "." + current)
    _row("现在", "." + tld)
    if regenerated:
        _row("配置", "已重新生成（旧的 auto.*.conf 已清掉）")
    if names:
        print("\n  站点现在长这样：")
        for name in names:
            print(f"    http://{vhost.hostname_for(name, tld)}")
    print(f"\n  别忘了同步 hosts： python meowbox.py hosts --apply   （要管理员，会整块重写成 .{tld}）")
    return 0


def _ensure_config(args, root: Path) -> None:
    """没有配置就先初始化一个。

    双击启动的用户（托盘 / 面板）不该被一句「先运行 meowbox.exe init」顶回去 ——
    他们连命令行都没开。命令行子命令不调这个，该报错就报错。
    """
    if cfgmod.exists(root):
        return
    provider = Path(args.provider) if getattr(args, "provider", None) else Path("D:/laragon")
    args.root = str(root)
    args.provider = str(provider) if provider.is_dir() else None
    print("还没有配置，先初始化一个（provider = "
          f"{args.provider or '无，用自己 bin/'}）")
    cmd_init(args)


def cmd_tray(args) -> int:
    from meow import tray
    root = Path(args.root).resolve()
    _ensure_config(args, root)
    if args.selftest:
        print("（自检模式：建好托盘图标，3 秒后自动退出）")
        return tray.run_tray(root, timeout=3.0)
    return tray.run_tray(root)


def cmd_gui(args) -> int:
    """打开控制面板。**没有配置也能开**：会先按默认值初始化，别逼用户回命令行。"""
    from meow import gui
    root = Path(args.root).resolve()
    _ensure_config(args, root)
    return gui.run_panel(root)


def cmd_shortcut(args) -> int:
    cfg, _ = _load(Path(args.root))
    root = Path(cfg["root"])
    py = args.python or util.preferred_pythonw()
    made = launchmod.write_launchers(root, py)
    _title("双击启动器已生成")
    if launchmod.has_exe(root):
        _row("启动方式", "打包版 exe（不需要 Python）")
    else:
        _row("用的解释器", py)
    for path in made:
        _row("", fwd(path))
    print("\n  双击上面任意一个 .vbs 就能启动喵盒，不会弹黑框窗口")
    return 0


def cmd_logs(args) -> int:
    """看日志占用 / 清理太旧太大的。"""
    from meow import logs as logsmod
    cfg, bins = _load(Path(args.root))
    lay = cfgmod.layout(cfg)
    if args.action == "clean":
        result = logsmod.clean(lay["logs"],
                               max_bytes=int(args.max_mb * 1024 * 1024),
                               keep_days=args.days, dry_run=args.dry_run,
                               busy=logsmod.busy_prefixes(cfg, bins))
        _title("清理日志" + ("（预览，没动手）" if args.dry_run else ""))
        print(logsmod.clean_text(result))
        return 0

    info = logsmod.survey(lay["logs"])
    _title("日志占用")
    print(logsmod.summary_text(info))
    big = [f for f in info["files"] if f["size"] > logsmod.DEFAULT_MAX_BYTES]
    old = [f for f in info["files"] if f["age_days"] > logsmod.DEFAULT_KEEP_DAYS]
    check_log = Path(lay["logs"]) / logsmod.CHECK_LOG
    if check_log.is_file():
        print(f"\n  体检记录：{logsmod.CHECK_LOG}（{logsmod.human(check_log.stat().st_size)}，"
              "按大小滚动，留 .1 .2）")
    if big or old:
        print(f"\n  {len(big)} 个超过 {logsmod.human(logsmod.DEFAULT_MAX_BYTES)}、"
              f"{len(old)} 个超过 {logsmod.DEFAULT_KEEP_DAYS} 天没动")
        print("  先预览： " + runtime.localize("meowbox.py logs clean --dry-run")
              + "\n  真清理： " + runtime.localize("meowbox.py logs clean"))
    else:
        print("\n  ✅ 不用清（单文件都在 2MB 内，也没有 7 天没动的）")
    return 0


def cmd_portable(args) -> int:
    """便携检查：配置里有没有写死路径、搬家后会不会坏。"""
    cfg, bins = _load(Path(args.root))
    if args.fix:
        path = portablemod.fix(cfg)
        print(f"✅ 配置已改写成相对路径：{fwd(path)}")
        cfg, bins = _load(Path(args.root))

    info = portablemod.report(cfg, bins)
    _title("便携检查（喵盒 = 一个文件夹，不装服务、不写注册表）")
    marks = {"ok": "✅", "warn": "⚠️ ", "info": "ℹ️ "}
    for level, item, note in info["checks"]:
        print(f"  {marks.get(level, '·')} {item:<10} {note}")
    print()
    if info["portable"]:
        print("  ✅ 结论：整个文件夹可以随便搬 —— 复制到别的盘/别的机器，")
        print("     直接 " + runtime.localize("python meowbox.py start") + " 就能继续用。")
    else:
        print("  ⚠️  结论：还有写死的路径，搬走会出问题。")
        print("     修一下： " + runtime.localize("python meowbox.py portable --fix"))
    return 0 if info["portable"] else 1


def cmd_autostart(args) -> int:
    from meow import tray
    if not args.state:
        _title("开机自启")
        _row("状态", "已开启" if tray.autostart_enabled() else "已关闭")
        _row("位置", fwd(tray.startup_link()))
        return 0
    enable = args.state == "on"
    tray.set_autostart(enable)
    print(("✅ 已开启" if enable else "✅ 已关闭") + "开机自启")
    return 0


def cmd_render(args) -> int:
    cfg, bins = _load(Path(args.root))
    services = svcmod.build(cfg, bins)
    server = _web(services)
    label = svcmod.pretty(server.name)
    if not server.available:
        print(f"没找到 {label}，无法生成配置。先跑 init 或检查 provider。")
        return 1
    conf = server.render()
    names = _site_names(cfg)
    lay = cfgmod.layout(cfg)
    _title(f"配置已生成（{label}）")
    _row("主配置", fwd(conf))
    if server.name == "nginx":
        _row("站点片段", fwd(lay["nginx_sites"]))
        from meow import php as phpmod
        ports = phpmod.port_map(cfg, phpmod.scan(cfg))
        pairs = "、".join(f"{l}→127.0.0.1:{p}" for l, p in ports.items()) or "没装 PHP"
        _row("PHP", f"php-cgi FastCGI {pairs}")
    else:
        _row("PHP 挂载", fwd(lay["etc_apache"] / "mod_php.conf"))
        _row("站点片段", fwd(lay["sites_enabled"]))
    _row("站点数", str(len(names)))

    rc, out, err = server.test()
    text = (out + err).strip()
    print(f"\n  {label} 语法自检：", "✅ 通过" if rc == 0 else "❌ 有错")
    for line in text.splitlines():
        print("    " + line)

    if rc == 0 and server.running():
        if server.name == "nginx":
            ok = server.reload()
            print("\n  " + ("✅ 已热加载新配置（Nginx reload，连接没断）" if ok
                            else "⚠️ reload 没成功，手动重启一下： python meowbox.py restart"))
        else:
            print("\n  ⚠️ Apache 改了配置要重启才生效： python meowbox.py restart")
    return 0 if rc == 0 else 1


def cmd_check(args) -> int:
    cfg, bins = _load(Path(args.root))
    server = _web(svcmod.build(cfg, bins))
    label = svcmod.pretty(server.name)
    if not server.available:
        print(f"没找到 {label}")
        return 1
    if not server.conf.is_file():
        server.render()
    rc, out, err = server.test()
    print((out + err).strip())
    return rc


def cmd_web(args) -> int:
    """看/换 Web 服务器（Apache ↔ Nginx）。"""
    cfg, bins = _load(Path(args.root))
    current = svcmod.server_name(cfg)
    if not args.name:
        _title("Web 服务器")
        _row("当前", _server_line(cfg, bins))
        _row("配置", fwd(_web(svcmod.build(cfg, bins)).conf))
        _row("切换", "python meowbox.py web " +
             ("apache" if current == "nginx" else "nginx"))
        for name in svcmod.WEB_SERVERS:
            found = bins.get(name)
            _row(svcmod.pretty(name), fwd(found) if found else "— 没找到")
        return 0

    target = args.name.strip().lower()
    _title(f"切换 Web 服务器：{svcmod.pretty(current)} → {svcmod.pretty(target)}")
    try:
        info = svcmod.switch_web(cfg, bins, target)
    except Exception as exc:                        # noqa: BLE001
        print(f"  ❌ {exc}")
        return 1
    if not info["changed"]:
        print(f"  已经是 {svcmod.pretty(target)} 了，没变")
        return 0
    if info["stopped"]:
        _row("已停止", svcmod.pretty(info["from"]))
    _row("主配置", fwd(info["conf"]))
    _row("语法自检", "✅ 通过")
    print(f"\n  ✅ 以后 start / stop / status 都是在管 {svcmod.pretty(target)} 了")
    print(f"     启动： python meowbox.py start {target}")
    if target == "nginx":
        print("     PHP：  每个版本各起一个 php-cgi，端口见 `python meowbox.py php list`")
    return 0


def cmd_hosts(args) -> int:
    cfg, _ = _load(Path(args.root))
    tld = cfg["sites"]["tld"]
    hosts_file = Path(args.hosts_file) if args.hosts_file else hostsmod.default_path()
    names = [vhost.hostname_for(n, tld) for n in _site_names(cfg)]
    result = hostsmod.sync(names, path=hosts_file, apply=args.apply)
    _title("hosts 同步")
    _row("文件", result["path"])
    _row("管理员", "是" if result["admin"] else "否")
    _row("已有条目", ", ".join(result["before"]) or "（无）")
    _row("目标条目", ", ".join(result["after"]) or "（无）")

    # 代理绕过：不写这一步的话，开着 Clash/加速器时浏览器会报
    # ERR_TUNNEL_CONNECTION_FAILED（请求根本没到本机 Apache）
    if args.apply:
        bypass = hostsmod.ensure_proxy_bypass(tld)
        if bypass.get("changed"):
            _row("代理绕过", "已加 " + "、".join(bypass["added"]))
        elif bypass.get("error"):
            _row("代理绕过", "没改成功：" + bypass["error"])
        else:
            _row("代理绕过", "已经包含 *." + tld)

    if result.get("error"):
        print(f"\n  ❌ {result['error']}")
        return 1
    if not result["changed"]:
        print("\n  ✅ 已经是最新的，不用改")
        return 0
    if result["applied"]:
        print("\n  ✅ 已写入 hosts")
    else:
        print("\n  （预览模式，没有真的写。加 --apply 才会写）")
    return 0


def _print_service_results(rows) -> None:
    for name, state, extra in rows:
        mark = {"已启动": "✅", "已停止": "⏹", "跳过": "⏭", "失败": "❌"}.get(state, "·")
        print(f"  {mark} {name:<8} {state}  {extra}")


def cmd_start(args) -> int:
    cfg, bins = _load(Path(args.root))
    services = svcmod.build(cfg, bins)
    names = args.names or cfg.get("autostart") or ["apache"]
    _title("启动服务")
    rows = svcmod.start(services, names)
    _print_service_results(rows)
    server = _web(services)
    if any(state == "已启动" for _, state, _ in rows) and server.running():
        tld = cfg["sites"]["tld"]
        port = cfg["web"]["http_port"]
        suffix = "" if port == 80 else f":{port}"
        print("\n  可以访问：")
        print(f"    http://localhost{suffix}/")
        for name in _site_names(cfg):
            print(f"    http://{vhost.hostname_for(name, tld)}{suffix}/")
    return 0 if all(state != "失败" for _, state, _ in rows) else 1


def cmd_stop(args) -> int:
    cfg, bins = _load(Path(args.root))
    services = svcmod.build(cfg, bins)
    _title("停止服务")
    _print_service_results(svcmod.stop(services, args.names))

    # 停完之后再查一遍：端口上是不是还蹲着「不是喵盒启动的」进程
    stuck = []
    for name, svc in services.items():
        orphan = getattr(svc, "orphan", lambda: None)()
        if orphan:
            stuck.append((name, orphan))
    if stuck:
        print("\n  ⚠️ 还有端口被别的进程占着（不是喵盒启动的，普通权限停不掉）：")
        for name, orphan in stuck:
            print(f"     {name}: 端口 {orphan['port']} ← {orphan['name']} (pid {orphan['pid']})")
        print(f"\n     要强行关掉（会弹 UAC）： python meowbox.py freeport {stuck[0][1]['port']}")
    return 0


def cmd_freeport(args) -> int:
    cfg, bins = _load(Path(args.root))
    _title(f"释放端口 {args.port}")
    holder = util.port_holder(args.port)
    if not holder:
        print("  ✅ 这个端口本来就空着")
        return 0
    _row("占用者", f"{holder[1]} (pid {holder[0]})")
    if holder[1].lower().startswith("mysqld"):
        print("  是数据库进程 —— 会走 mysqladmin 优雅关闭，不会硬杀")
    elif not util.is_admin() and not args.no_elevate:
        print("  喵盒没有管理员权限 —— 会弹一个 UAC 确认框，点「是」就行")

    services = svcmod.build(cfg, bins)
    if args.no_elevate and not holder[1].lower().startswith("mysqld"):
        ok, message = util.free_port(args.port, use_elevation=False)
    else:
        ok, message = svcmod.free_port(cfg, services, args.port)
    print(("  ✅ " if ok else "  ❌ ") + message)
    return 0 if ok else 1


def cmd_restart(args) -> int:
    cfg, bins = _load(Path(args.root))
    services = svcmod.build(cfg, bins)
    _title("重启服务")
    names = args.names
    if not names:
        # 只把「重启前在跑的」起回来。以前这里是按 autostart 起的，
        # 结果 mailpit 这种不在 autostart 里的服务被顺手关掉就再也不起来。
        names = [n for n, svc in services.items() if svc.status() == "运行中"] \
            or list(cfg.get("autostart") or [])
    _print_service_results(svcmod.stop(services, names))
    time.sleep(0.6)
    _print_service_results(svcmod.start(services, names))
    return 0


def _ssl_list(ca, cfg) -> int:
    from meow import ssl as sslmod
    _title("HTTPS 状态")
    if not ca.available:
        print("  ❌ 找不到可用的 openssl.exe")
        print('     在 meowbox.config.json 里把 "openssl" 指到一个能用的就行')
        return 1
    _row("openssl", fwd(ca.openssl))
    if ca.ca_exists():
        _row("根证书", fwd(ca.ca_crt))
        _row("信任状态", "已装进当前用户信任库" if sslmod.is_trusted(ca)
             else "还没装（浏览器会报警告）")
    else:
        _row("根证书", "还没建（跑 python meowbox.py ssl）")
    print()
    hosts = sslmod.site_hostnames(cfg)
    if not hosts:
        print("  （还没有站点）")
        return 0
    for host in hosts:
        if not ca.has_cert(host):
            print(f"  ⚠️   {host:<24} 没有证书")
            continue
        info = ca.info(host)
        ok, _ = ca.verify(host)
        print(f"  {'✅' if ok else '❌'}   {host:<24} 到期 {info['not_after']}")
    return 0


def cmd_ssl(args) -> int:
    from meow import ssl as sslmod
    cfg, bins = _load(Path(args.root))
    action = args.action
    ca = sslmod.CertAuthority(cfg, bins)

    if action == "list":
        return _ssl_list(ca, cfg)

    if action == "check":
        from meow import doctor
        _title("HTTPS 体检")
        report = doctor.https_report(cfg, bins)
        print(doctor.as_text(report))
        if not report["ok"]:
            print("\n  想一键修： " + runtime.localize("meowbox.py ssl renew")
                  + "   （重签全部证书 + 重生成配置 + 重启）")
        return 0 if report["ok"] else 1

    if action == "init":
        _title("生成本地根证书")
        try:
            created = ca.ensure_ca()
        except Exception as exc:                    # noqa: BLE001
            print(f"  ❌ {exc}")
            return 1
        _row("openssl", fwd(ca.openssl))
        _row("证书", fwd(ca.ca_crt))
        _row("私钥", fwd(ca.ca_key))
        print("\n  " + ("✅ 新建了一张（10 年有效）" if created else "已经有一张了，没动它"))
        return 0

    if action in ("trust", "untrust"):
        ok, message = (sslmod.trust_ca(ca) if action == "trust" else sslmod.untrust_ca(ca))
        print(("✅ " if ok else "❌ ") + message)
        return 0 if ok else 1

    if action == "off":
        sslmod.disable(cfg)
        server = _web(svcmod.build(cfg, bins))
        if server.available:
            server.render()
        print("✅ 已关闭 HTTPS，SSL 虚拟主机片段也清掉了")
        print("   （证书文件都还留着，随时 python meowbox.py ssl 再打开）")
        print("   重启后生效： python meowbox.py restart")
        return 0

    # ---------- on / renew：一键 ----------
    _title("自签 HTTPS 证书" + ("（重新签发全部）" if action == "renew" else ""))
    try:
        info = sslmod.provision(cfg, bins, force=(action == "renew"),
                                do_trust=not args.no_trust,
                                log=lambda message: print("   " + message))
    except Exception as exc:                        # noqa: BLE001
        print(f"\n  ❌ {exc}")
        return 1

    server = _web(svcmod.build(cfg, bins))
    label = svcmod.pretty(server.name)
    if not server.available:
        print(f"\n  ⚠️ 没找到 {label}，配置没重新生成")
        return 1
    server.render()
    rc, out, err = server.test()
    if rc != 0:
        sslmod.disable(cfg)
        server.render()
        print(f"\n  ❌ {label} 语法检查没过，已经自动把 SSL 关回去（免得服务起不来）：")
        print("    " + (out + err).strip()[-900:])
        return 1
    print(f"   {label} 配置已重新生成，语法检查通过")

    if server.running():
        try:
            server.stop()
            server.start()
            print(f"   {label} 已重启")
        except Exception as exc:                    # noqa: BLE001
            print(f"\n   ⚠️ 重启失败：{exc}")
            print(f"   配置其实已经生成好了，你手动重启一下 {label} 就生效。")
    else:
        print(f"   （{label} 没在跑，启动后生效： python meowbox.py start）")

    https = cfg["web"]["https_port"]
    suffix = "" if https == 443 else f":{https}"
    print("\n  现在可以访问：")
    for host in info["hosts"]:
        print(f"    https://{host}{suffix}/")
    if info.get("trusted") is False:
        print("\n  ⚠️ 根证书没装进信任库，浏览器还会警告。手动装：")
        print("     python meowbox.py ssl trust")
    return 0


def cmd_mail(args) -> int:
    """Mailpit —— 本地假邮箱：站点发出去的邮件全被它截下来，网页里翻。"""
    cfg, bins = _load(Path(args.root))
    services = svcmod.build(cfg, bins)
    mail = services.get("mailpit")
    action = args.action or "status"

    if mail is None:
        print("配置里把 mailpit.enabled 关掉了（或者没探测到程序）。")
        print("想用就把它改成 true： meowbox.config.json → mailpit.enabled")
        return 1
    if not mail.available:
        print("没找到 Mailpit 程序。先装一个： python meowbox.py pkg install mailpit")
        return 1

    if action == "open":
        if not mail.running():
            print("Mailpit 没在跑，先把它拉起来 ...")
            try:
                mail.start()
            except Exception as exc:                # noqa: BLE001
                print(f"  ❌ {exc}")
                return 1
        print(f"收件箱：{mail.ui_url()}")
        util.open_path(mail.ui_url())
        return 0

    if action == "clear":
        try:
            removed = mail.purge()
        except Exception as exc:                    # noqa: BLE001
            print(f"  ❌ {exc}")
            return 1
        print("✅ 信箱已清空" + (f"（删了 {', '.join(removed)}）" if removed else "（本来就是空的）"))
        return 0

    _title("Mailpit —— 本地假邮箱")
    _row("状态", mail.status())
    _row("收件箱", mail.ui_url())
    _row("SMTP", mail.smtp_url())
    _row("数据库", fwd(mail.db))
    _row("邮件上限", f"{mail.max_messages} 封")
    info = mail.summary()
    if info:
        _row("现在有", f"{info['total']} 封信（未读 {info['unread']}）")
    print("\n  启动/停止： python meowbox.py start mailpit / stop mailpit")
    print("  打开收件箱： python meowbox.py mail open")
    print("  清空信箱：   python meowbox.py mail clear")
    return 0


def cmd_migrate(args) -> int:
    """从 Laragon 收编站点和数据库（默认只预览，加 --yes 才真搬）。"""
    cfg, bins = _load(Path(args.root))
    _title("从 Laragon 迁移")
    try:
        src = migratemod.find_source(cfg, args.source)
    except Exception as exc:                        # noqa: BLE001
        print(f"  ❌ {exc}")
        return 1

    pl = migratemod.plan(cfg, src)
    if args.db_only:
        pl["sites"] = []
    if args.sites_only:
        pl["dbs"] = []

    need = sum(s["size"] for s in pl["sites"]) + sum(d["size"] for d in pl["dbs"])
    _row("Laragon", fwd(src["root"]))
    _row("网站目录", fwd(src["www"]) if src["www"] else "—（没有 www/）")
    _row("数据目录", fwd(src["datadir"]) if src["datadir"] else "—（没有 data/mysql*）")
    _row("Laragon 后缀", "." + (src["tld"] or "?"))
    _row("喵盒后缀", "." + cfg["sites"]["tld"])
    _row("要占空间", f"约 {migratemod.human(need)}（所在盘还剩 {migratemod.human(pl['free'])}）")

    print("\n  站点：")
    if not pl["sites"]:
        print("    （没有）")
    for item in pl["sites"]:
        flag = "⚠️ 目标已存在" if item["exists"] else "新站点"
        print(f"    {item['name']:<22} {migratemod.human(item['size']):>10}   {flag}")

    print("\n  数据库：")
    if not pl["dbs"]:
        print("    （没有）")
    for item in pl["dbs"]:
        in_plan = ""
        if args.only_db:
            in_plan = "" if item["name"] in args.only_db else "   （--db 没选它）"
        tag = "（看着像测试库）" if item["looks_temp"] else ""
        print(f"    {item['name']:<22} {migratemod.human(item['size']):>10}   {tag}{in_plan}")

    if args.dry_run or not args.yes:
        print("\n  👀 这只是预览，一个字节都没动。")
        print("  真搬：  python meowbox.py migrate --yes")
        if src["tld"] and src["tld"] != cfg["sites"]["tld"]:
            print(f"  想保持原来的网址（.{src['tld']}）："
                  f" python meowbox.py migrate --yes --tld {src['tld']}")
        return 0

    if args.tld:
        target_tld = src["tld"] if args.tld == "auto" else args.tld
        if target_tld:
            migratemod.apply_tld(cfg, target_tld, say=print)

    site_results = []
    if pl["sites"]:
        print("\n  —— 站点 ——")
        site_results = migratemod.migrate_sites(cfg, pl, overwrite=args.overwrite, say=print)

    db_results = []
    if pl["dbs"]:
        print("\n  —— 数据库 ——")
        try:
            db_results = migratemod.migrate_databases(
                cfg, bins, pl, only=args.only_db, overwrite=args.overwrite, say=print)
        except Exception as exc:                    # noqa: BLE001
            print(f"  ❌ 数据库迁移失败：{exc}")
            db_results = []

    # 重新生成配置（新站点才有 vhost）
    print("\n  —— 收尾 ——")
    services = svcmod.build(cfg, bins)
    server = _web(services)
    if server.available:
        server.render()
        rc, out, err = server.test()
        print(f"  {svcmod.pretty(server.name)} 配置已重新生成，语法自检："
              + ("✅ 通过" if rc == 0 else "❌ 有错"))
        if rc != 0:
            print("    " + (out + err).strip()[-800:])
        elif server.running() and server.name == "nginx":
            server.reload()
            print("  （Nginx 已热加载）")
        elif server.running():
            print("  （Apache 改了配置要重启才生效： python meowbox.py restart）")

    hosts = [vhost.hostname_for(n, cfg["sites"]["tld"]) for n in _site_names(cfg)]
    if hosts:
        result = hostsmod.sync(hosts, apply=True)
        if result.get("error"):
            print(f"  ⚠️  hosts 还没写（{result['error']}）")
            print("     用管理员终端跑一次： python meowbox.py hosts --apply")
        elif result["applied"]:
            print("  ✅ hosts 已同步：" + ", ".join(hosts))
        else:
            print("  ✅ hosts 本来就是最新的")

    copied = [r["name"] for r in site_results if r["status"] == "copied"]
    imported = [r["name"] for r in db_results if r["status"] == "imported"]
    print("\n  迁移结果：")
    _row("站点", f"{len(copied)} 个" + (f"：{', '.join(copied)}" if copied else ""))
    _row("数据库", f"{len(imported)} 个" + (f"：{', '.join(imported)}" if imported else ""))
    _row("导出的 .sql", fwd(cfgmod.layout(cfg)["run"] / "migrate"))
    for host in hosts:
        print(f"    http://{host}/")
    return 0


def cmd_vendor(args) -> int:
    """彻底自带：把 provider 里的程序复制进自己的 bin/，然后把 provider 关掉。"""
    cfg, bins = _load(Path(args.root))
    _title("彻底自带：把程序搬进喵盒自己的 bin/")

    if not cfg.get("provider"):
        _row("provider", "已经是 null —— 自带模式，不依赖任何外部目录")
        for name, path in bins.items():
            _row(name, fwd(path) if path else "— 没找到")
        print("\n  想重跑一遍（比如某个组件被删了）就先把 provider 指回去，")
        print("  或者直接 python meowbox.py pkg install <组件名> 装进 bin/。")
        return 0

    pl = vendormod.plan(cfg, bins)
    total = sum(e["size"] for e in pl)
    missing = [e["key"] for e in pl if e["problem"]]

    _row("现在借的", fwd(cfg["provider"]))
    _row("要复制", f"约 {migratemod.human(total)}")
    free = shutil.disk_usage(cfg["root"]).free
    _row("盘上剩余", migratemod.human(free))
    print()
    for entry in pl:
        label = vendormod.LABELS.get(entry["key"], entry["key"])
        size = migratemod.human(entry["size"]) if entry["size"] else "—"
        dest = fwd(entry["dest"]) if entry["dest"] else "（provider 里没有，要下载）"
        print(f"    {label:<8} {size:>10}  {dest}")

    if missing:
        print("\n  这些 provider 里没有：" + ", ".join(missing))
        print("  加 --download 让我现在就下（走 packages.conf 的地址）。")

    if args.dry_run or not args.yes:
        print("\n  👀 预览：没复制、没改配置。")
        print("  真做： python meowbox.py vendor --yes" +
              ("" if not missing else " --download"))
        return 0

    if missing and args.download:
        vendor_mod.download_missing(cfg, pl, say=print)
        bins = cfgmod.discover(cfg)
        pl = vendormod.plan(cfg, bins)
        missing = [e["key"] for e in pl if e["problem"]]
    if missing:
        print("\n  ❌ 还有组件没着落（" + ", ".join(missing) + "），先补齐再切 provider。")
        return 1

    print()
    info = vendormod.full_vendor(cfg, bins, say=print, restart=not args.no_restart)

    cfg2, bins2 = _load(Path(args.root))
    _title("现在是真的自带喵盒了")
    _row("provider", "null（谁都不靠）")
    _row("组件目录", fwd(Path(cfg2["root"]) / "bin"))
    for name, path in bins2.items():
        _row(name, fwd(path) if path else "— 没找到")

    hosts = [vhost.hostname_for(n, cfg2["sites"]["tld"]) for n in _site_names(cfg2)]
    if hosts:
        print("\n  站点：")
        for host in hosts:
            print(f"    http://{host}/")
    print("\n  Laragon 现在可以整个删掉了（删之前确认上面的自检全是 ✅）。")
    print("  后悔药：把 meowbox.config.json 的 provider 改回 \"D:/laragon\" 就退回借用模式。")
    return 0 if all(ok for ok, _l, _t in info["checks"]) else 1


def cmd_status(args) -> int:
    cfg, bins = _load(Path(args.root))
    services = svcmod.build(cfg, bins)
    _title("运行状态")
    marks = {"运行中": "🟢", "被外部占用": "🟠"}
    for name, state in svcmod.status(services):
        print(f"  {marks.get(state, '⚪')} {name:<8} {state}")
        orphan = getattr(services[name], "orphan", lambda: None)()
        if orphan:
            print(f"            ↳ 端口 {orphan['port']} 被 {orphan['name']} "
                  f"(pid {orphan['pid']}) 占着，不是喵盒启动的")
            print(f"              强行关掉： python meowbox.py freeport {orphan['port']}")
    labels: dict[int, str] = {}
    server = svcmod.web(services)
    if server is not None:
        labels[server.port] = f"端口 {server.port}（{svcmod.pretty(server.name)}）"
        if cfg["web"].get("ssl"):
            labels[int(cfg["web"]["https_port"])] = f"端口 {cfg['web']['https_port']}（HTTPS）"
    mail = services.get("mailpit")
    if mail is not None:
        labels[mail.smtp_port] = f"端口 {mail.smtp_port}（SMTP）"
        labels[mail.http_port] = f"端口 {mail.http_port}（收件箱）"
    for port, label in labels.items():
        _row(label, util.who_holds_port(port) or "空闲")

    # 非默认 PHP 版本靠 php-cgi 跑，它的状态也得看得见
    from meow import php as phpmod
    versions = phpmod.scan(cfg)
    if versions:
        pool = phpmod.CgiPool(cfg, versions)
        running = [l for l in versions if pool.running(l)]
        if running:
            detail = "、".join(f"{l}(:{pool.port(l)})" for l in running)
            print(f"  🟢 {'php-cgi':<8} 运行中   {detail}")
        else:
            print(f"  ⚪ {'php-cgi':<8} 已停止")
    return 0


def cmd_db(args) -> int:
    cfg, bins = _load(Path(args.root))
    mysql = svcmod.build(cfg, bins)["mysql"]
    if not mysql.available:
        print("没找到 MySQL")
        return 1
    if args.action != "init":
        print("目前只支持： python meowbox.py db init")
        return 1
    _title("初始化 MySQL 数据目录")
    _row("数据目录", fwd(mysql.datadir))
    if mysql.initialized():
        print("\n  ✅ 已经初始化过了，跳过")
        return 0
    rc, out, err = mysql.init()
    if rc == 0:
        print("\n  ✅ 完成！root 账号没有密码（本地开发够用了）")
    else:
        print("\n  ❌ 初始化失败：")
        print((out + err).strip()[-2000:])
    return rc


def cmd_pkg(args) -> int:
    cfg, _ = _load(Path(args.root))
    if args.action == "list":
        _title("软件清单（packages.conf）")
        for name, url in pkgmod.load_menu(cfg):
            print(f"  {name:<20} {url}")
        print("\n  安装： python meowbox.py pkg install <名字>")
        return 0
    if args.action == "install":
        if not args.name:
            print("要指定包名，例如： pkg install PHP-8.4")
            return 1
        _title(f"下载安装 {args.name}")
        ok, msg = pkgmod.install(cfg, args.name)
        print(("  ✅ " if ok else "  ❌ ") + msg)
        return 0 if ok else 1
    return 1


def _php_apply(cfg, bins, restart: bool = True) -> str:
    """PHP 版本改动之后：存配置 → 重渲染 → （需要的话）重启 Web 服务器。"""
    cfgmod.save(cfg)
    services = svcmod.build(cfg, bins)
    server = _web(services)
    if not server.available:
        return "（没找到 Web 服务器，只改了配置）"
    server.render()
    if not restart or not server.running():
        return "（配置已重新生成，下次启动生效）"
    server.stop()
    server.start()
    return "（配置已重新生成，Web 服务器已重启）"


def cmd_php(args) -> int:
    """PHP 多版本：看、装、切默认、给单个站点指定、打开 ini。"""
    from meow import php as phpmod

    cfg, bins = _load(Path(args.root))
    action = args.action or "list"
    versions = phpmod.scan(cfg)
    default = phpmod.default_label(cfg, versions)
    ports = phpmod.port_map(cfg, versions)

    if action == "list":
        _title(f"PHP 版本（{len(versions)} 个）")
        if not versions:
            print("  一个都没有。装一个： python meowbox.py php install 8.3")
            return 0
        names = _site_names(cfg)
        for label, ver in versions.items():
            tags = []
            if label == default:
                tags.append("默认")
            tags.append("TS" if ver.thread_safe else "NTS(不能挂 mod_php)")
            if defaults := [n for n in names if phpmod.site_label(cfg, n, versions) == label]:
                tags.append("站点：" + "、".join(defaults))
            print(f"  {label:<6} {ver.version:<10} :{ports.get(label, '-'):<6} "
                  f"{' · '.join(tags)}")
            print(f"         {fwd(ver.dir)}")
        print("\n  给站点换版本： python meowbox.py php site <站点> <版本>")
        print("  换默认版本：   python meowbox.py php use <版本>")
        return 0

    if action == "install":
        want = (args.name or "").strip()
        _title(f"安装 PHP {want or '（最新版）'}")
        had_any = bool(versions)
        try:
            ver = phpmod.install(cfg, want)
        except Exception as exc:                          # noqa: BLE001
            print(f"  ❌ {exc}")
            return 1
        if not had_any and not (cfg.get("php") or {}).get("version"):
            cfg["php"]["version"] = ver.label
            cfgmod.save(cfg)
            print(f"  这是第一个 PHP，已设为默认版本（{ver.label}）")
        print("  " + _php_apply(cfg, bins))
        return 0

    if action == "use":
        if not args.name:
            _row("默认版本", default or "（没有装 PHP）")
            return 0
        label = args.name.strip()
        if label not in versions:
            print(f"没有装 {label}。现有：{', '.join(versions) or '（空）'}")
            return 1
        cfg["php"]["version"] = label
        print(f"  默认 PHP 版本 → {versions[label].version}")
        print("  " + _php_apply(cfg, bins))
        return 0

    if action == "site":
        if not args.name:
            _title("站点 → PHP 版本")
            for name in _site_names(cfg):
                label = phpmod.site_label(cfg, name, versions)
                mark = "" if cfg["sites"].get("php", {}).get(name) else "（跟默认）"
                print(f"  {name:<20} {label or '—'} {mark}")
            return 0
        site = args.name.strip()
        if site not in _site_names(cfg):
            print(f"没有这个站点：{site}")
            return 1
        label = (args.version or "").strip()
        if not label:
            cur = phpmod.site_label(cfg, site, versions)
            own = cfg["sites"].get("php", {}).get(site)
            _row(site, f"{cur}（{'单独指定' if own else '跟默认'}）")
            return 0
        if label not in ("default", "默认") and label not in versions:
            print(f"没有装 {label}。现有：{', '.join(versions) or '（空）'}")
            return 1
        phpmod.set_site_version(cfg, site, None if label in ("default", "默认") else label)
        print(f"  {site} → {phpmod.site_label(cfg, site, versions)}")
        print("  " + _php_apply(cfg, bins))
        return 0

    if action == "restart":
        # 卡住时不用重启整个 Web 服务器，只把 php-cgi 池换掉
        pool = phpmod.CgiPool(cfg, versions)
        stopped = pool.stop_all()
        labels = pool.labels_for(_site_names(cfg), svcmod.server_name(cfg))
        started = pool.start(labels) if labels else {}
        _title("PHP 进程池已重启")
        _row("停掉", "、".join(stopped) or "（本来就没在跑）")
        _row("重新起", "、".join(f"{k} → :{pool.port(k)}" for k in started)
             or "（没有站点需要 php-cgi）")
        _row("worker", f"每版本 {pool.children} 个")
        return 0

    if action in ("open", "ini"):
        label = (args.name or default or "").strip()
        if label not in versions:
            print(f"没有装 {label or '（任何版本）'}。现有：{', '.join(versions) or '（空）'}")
            return 1
        ver = versions[label]
        target = ver.ini if action == "ini" else ver.dir
        if action == "ini" and not ver.ini.is_file():
            print(f"{label} 还没有 php.ini：{fwd(ver.ini)}")
            return 1
        util.open_path(target)
        print(f"  已打开 {fwd(target)}")
        return 0

    if action == "myadmin":
        lay = cfgmod.layout(cfg)
        if not phpmod.phpmyadmin_ready(cfg):
            _title("安装 phpMyAdmin")
            try:
                phpmod.install_phpmyadmin(cfg)
            except Exception as exc:                      # noqa: BLE001
                print(f"  ❌ {exc}")
                return 1
            server = _web(svcmod.build(cfg, bins))
            if server.available:
                server.render()
            print("  还要同步一下 hosts： python meowbox.py hosts --apply")
        url = vhost.site_url(cfg, lay, "phpmyadmin")
        _row("地址", url)
        util.open_path(url)
        return 0

    return 1


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meowbox",
        description="🐾 喵盒 Meow Box —— 轻量本地 Web 开发环境管家",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=runtime.localize(__doc__ or ""),
    )
    parser.add_argument("--root", default=str(ROOT),
                        help="喵盒根目录（默认：程序所在目录）")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("init", help="初始化喵盒")
    p.add_argument("--provider", default="D:/laragon",
                   help="从哪借 bin/ 目录（默认 D:/laragon；传空字符串表示用喵盒自己的 bin/）")
    p.set_defaults(func=cmd_init)

    sub.add_parser("info", help="显示配置和探测结果").set_defaults(func=cmd_info)
    p = sub.add_parser("list", help="列出站点")
    p.add_argument("--hosts-file", default=None, help="指定 hosts 文件路径（测试用）")
    p.set_defaults(func=cmd_list)
    sub.add_parser("render", help="重新生成配置文件").set_defaults(func=cmd_render)
    sub.add_parser("check", help="当前 Web 服务器语法自检").set_defaults(func=cmd_check)
    p = sub.add_parser("web", help="查看/切换 Web 服务器（apache ↔ nginx）")
    p.add_argument("name", nargs="?", choices=list(svcmod.WEB_SERVERS),
                   help="要换成哪一个；不加则只看当前用的是哪个")
    p.set_defaults(func=cmd_web)
    p = sub.add_parser("mail", help="Mailpit 本地假邮箱（状态 / 打开收件箱 / 清空）")
    p.add_argument("action", nargs="?", default="status",
                   choices=["status", "open", "clear"])
    p.set_defaults(func=cmd_mail)

    p = sub.add_parser("migrate", help="从 Laragon 迁移站点和数据库（默认只预览）")
    p.add_argument("--from", dest="source", default=None,
                   help="Laragon 目录（默认用配置里的 provider）")
    p.add_argument("--tld", default=None,
                   help="域名后缀：auto = 跟 Laragon 一致；也可以写 meow / test …")
    p.add_argument("--db", dest="only_db", action="append", default=None,
                   help="只迁指定库（可以写多次）")
    p.add_argument("--sites-only", action="store_true", help="只迁站点，不动数据库")
    p.add_argument("--db-only", action="store_true", help="只迁数据库，不动站点")
    p.add_argument("--overwrite", action="store_true",
                   help="目标已存在就覆盖（会先把旧的备份）")
    p.add_argument("--yes", action="store_true", help="真的执行（不加只预览）")
    p.add_argument("--dry-run", action="store_true", help="只预览，什么都不改")
    p.set_defaults(func=cmd_migrate)
    sub.add_parser("status", help="看运行状态").set_defaults(func=cmd_status)

    p = sub.add_parser("site", help="新建/删除站点")
    p.add_argument("action", choices=["add", "remove"])
    p.add_argument("name")
    p.set_defaults(func=cmd_site)

    p = sub.add_parser("hosts", help="同步 hosts 条目")
    p.add_argument("--apply", action="store_true", help="真的写入（不加只预览）")
    p.add_argument("--hosts-file", default=None, help="指定 hosts 文件路径（测试用）")
    p.set_defaults(func=cmd_hosts)

    p = sub.add_parser("start", help="启动服务")
    p.add_argument("names", nargs="*",
                   help="apache / nginx / mysql / mailpit / all / web（默认按配置的 autostart）")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("stop", help="停止服务")
    p.add_argument("names", nargs="*",
                   help="apache / nginx / mysql / mailpit / all / web（默认全部）")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("restart", help="重启服务")
    p.add_argument("names", nargs="*", help="apache / nginx / mysql / mailpit / all / web")
    p.set_defaults(func=cmd_restart)

    p = sub.add_parser("freeport", help="把占着某个端口的进程关掉（必要时弹 UAC）")
    p.add_argument("port", type=int, help="端口号，比如 80 / 443 / 3306")
    p.add_argument("--no-elevate", action="store_true",
                   help="不提权，只用普通权限试（不会弹 UAC）")
    p.set_defaults(func=cmd_freeport)

    p = sub.add_parser("ssl", help="自签 HTTPS 证书（一键）")
    p.add_argument("action", nargs="?",
                   default="on",
                   choices=["on", "init", "renew", "trust", "untrust", "list", "off", "check"])
    p.add_argument("--no-trust", action="store_true",
                   help="不把根证书装进系统信任库")
    p.set_defaults(func=cmd_ssl)

    p = sub.add_parser("tray", help="启动系统托盘图标")
    p.add_argument("--selftest", action="store_true",
                   help="只测试能不能建出图标，3 秒后自动退出")
    p.set_defaults(func=cmd_tray)

    sub.add_parser("gui", help="打开图形控制面板（所有功能都能在这里点）").set_defaults(func=cmd_gui)

    p = sub.add_parser("vendor", help="彻底自带：把组件复制进 bin/，provider 设成 null")
    p.add_argument("--yes", action="store_true", help="真的执行（不加只预览）")
    p.add_argument("--dry-run", action="store_true", help="只预览，什么都不改")
    p.add_argument("--download", action="store_true",
                   help="provider 里没有的组件，按 packages.conf 下载")
    p.add_argument("--no-restart", action="store_true",
                   help="切完不自动重启服务（默认会重启并验证）")
    p.set_defaults(func=cmd_vendor)

    p = sub.add_parser("portable", help="便携检查：配置里有没有写死路径")
    p.add_argument("--fix", action="store_true",
                   help="把配置里的绝对路径改写成相对路径（搬家的前提）")
    p.set_defaults(func=cmd_portable)

    p = sub.add_parser("logs", help="看日志占用 / 清理太旧太大的日志")
    p.add_argument("action", nargs="?", default="list", choices=["list", "clean"])
    p.add_argument("--days", type=int, default=7, help="超过这么多天没动过就删（默认 7）")
    p.add_argument("--max-mb", type=float, default=2.0, help="单个文件超过多少 MB 就截断（默认 2）")
    p.add_argument("--dry-run", action="store_true", help="只预览会清什么，不动手")
    p.set_defaults(func=cmd_logs)

    p = sub.add_parser("shortcut", help="生成双击启动器（.vbs）")
    p.add_argument("--python", default=None,
                   help="指定无控制台解释器（默认自动挑系统装的 Python）")
    p.set_defaults(func=cmd_shortcut)

    p = sub.add_parser("autostart", help="开机自启开关")
    p.add_argument("state", nargs="?", choices=["on", "off"], help="on / off；不加则查看状态")
    p.set_defaults(func=cmd_autostart)

    p = sub.add_parser("tld", help="查看/修改站点域名后缀（默认 .meow）")
    p.add_argument("value", nargs="?", help="新的后缀，例如 meow / test / local")
    p.set_defaults(func=cmd_tld)

    p = sub.add_parser("db", help="数据库操作")
    p.add_argument("action", choices=["init"])
    p.set_defaults(func=cmd_db)

    p = sub.add_parser("pkg", help="软件包")
    p.add_argument("action", choices=["list", "install"])
    p.add_argument("name", nargs="?")
    p.set_defaults(func=cmd_pkg)

    p = sub.add_parser("php", help="PHP 多版本：查看 / 安装 / 切换 / 打开 phpMyAdmin")
    p.add_argument("action", nargs="?", default="list",
                   choices=["list", "install", "use", "site", "open", "ini", "myadmin", "restart"])
    p.add_argument("name", nargs="?", help="版本号（8.5）、站点名，或 install 的目标版本")
    p.add_argument("version", nargs="?", help="php site <站点> <版本> 里的版本")
    p.set_defaults(func=cmd_php)

    return parser


def main(argv=None) -> int:
    runtime.prepare_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"❌ {runtime.localize(exc)}")
        return 1
    except KeyboardInterrupt:
        print("\n已取消")
        return 130


if __name__ == "__main__":
    sys.exit(main())
