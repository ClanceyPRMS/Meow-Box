"""喵盒控制面板 —— 命令行能做的事，这里基本都能点出来。

    pythonw meowbox.py gui

设计原则（跟命令行一致的）：

* **便携**：不装服务、不写注册表，所有操作就是普通进程 + 自己文件夹里的文件；
* **不留坑**：耗时动作（启动、下载、签证书）全丢后台线程，界面不假死；
* 出错用弹窗 + 日志双保险，别让人对着没反应的按钮发呆。
"""
from __future__ import annotations

import queue
import re
import shutil
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

from . import config as cfgmod
from . import hosts as hostsmod
from . import launcher as launchmod
from . import migrate as migratemod
from . import packages as pkgmod
from . import php as phpmod
from . import portable as portablemod
from . import runtime
from . import services as svcmod
from . import util, vhost
from . import vendor as vendormod


class _LocalizedBox:
    """弹窗里的提示在打包版要换说法（`python meowbox.py x` → `meowbox.exe x`）。"""

    def __getattr__(self, name):
        fn = getattr(_tk_messagebox, name)

        def call(title, message="", **options):
            return fn(title, runtime.localize(message), **options)

        return call


_tk_messagebox = messagebox
messagebox = _LocalizedBox()        # noqa: F811 —— 之后所有弹窗都自动过一遍

BG = "#fdf6f9"
ACCENT = "#e74a8a"
INK = "#3b2b33"

#: 站点名字（就是 www/ 下的文件夹名）
SITE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


class Panel:
    def __init__(self, root_dir) -> None:
        self.root_dir = Path(root_dir)
        self.cfg = cfgmod.load(self.root_dir)
        self.bins = cfgmod.discover(self.cfg)
        self.services = svcmod.build(self.cfg, self.bins)
        self.lay = cfgmod.layout(self.cfg)
        self.queue: queue.Queue = queue.Queue()

        self.tk = tk.Tk()
        self.tk.title("喵盒 Meow Box")
        self.tk.geometry("580x610")
        self.tk.minsize(520, 500)
        self.tk.configure(bg=BG)
        icon = self.root_dir / "assets" / "meowbox.ico"
        if icon.is_file():
            try:
                self.tk.iconbitmap(default=str(icon))
            except tk.TclError:
                pass

        self._load_header_icon()
        self._build()
        self.refresh()
        self.tk.after(150, self._drain)
        self.tk.after(2500, self._tick)

    # ------------------------------------------------------------ 界面
    def _build(self) -> None:
        style = ttk.Style(self.tk)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=INK, font=("Microsoft YaHei UI", 9))
        style.configure("Head.TLabel", font=("Microsoft YaHei UI", 13, "bold"), foreground=ACCENT)
        style.configure("TButton", font=("Microsoft YaHei UI", 9))

        self._build_menu()

        header = ttk.Frame(self.tk, padding=(14, 10, 14, 4))
        header.pack(fill="x")
        if self._icon_img is not None:
            ttk.Label(header, image=self._icon_img).pack(side="left", padx=(0, 6))
        ttk.Label(header, text="喵盒 Meow Box", style="Head.TLabel").pack(side="left")
        self.lbl_bins = ttk.Label(header, text="", style="TLabel")
        self.lbl_bins.pack(side="right")

        # 状态行 —— 用 tk.Label 上色，别用 emoji（Tk 里 emoji 只有单色轮廓，分不出来）
        status_row = ttk.Frame(self.tk, padding=(14, 2, 14, 2))
        status_row.pack(fill="x")
        self._status_row = status_row
        self.svc_labels: dict[str, tk.Label] = {}
        self._sync_labels()

        # 第二行：HTTPS 状态 + 端口占用（端口多，单独占一行才不会挤出窗口）
        port_row = ttk.Frame(self.tk, padding=(14, 0, 14, 8))
        port_row.pack(fill="x")
        self.lbl_ssl = tk.Label(port_row, text="", bg=BG, fg="#8a7d84",
                                font=("Microsoft YaHei UI", 9))
        self.lbl_ssl.pack(side="left", padx=(0, 6))
        # 体检按钮就摆在 HTTPS 状态旁边 —— 红灯了第一眼就该看到它
        ttk.Button(port_row, text="体检", width=6, command=self.ssl_doctor).pack(side="left", padx=(0, 12))
        self.lbl_port = tk.Label(port_row, text="", bg=BG, fg="#8a7d84",
                                 font=("Microsoft YaHei UI", 9), justify="left")
        self.lbl_port.pack(side="left")

        # ---- 服务按钮 ----
        box = ttk.Frame(self.tk, padding=(12, 0))
        box.pack(fill="x")
        for text, fn in (("启动全部", self.start_all), ("停止全部", self.stop_all),
                         ("重启", self.restart)):
            ttk.Button(box, text=text, command=fn).pack(side="left", padx=3)
        box2 = ttk.Frame(self.tk, padding=(12, 6, 12, 0))
        box2.pack(fill="x")
        for text, fn in (("重新生成配置", self.render), ("同步 hosts", self.sync_hosts),
                         ("生成 SSL 证书", self.make_ssl), ("释放被占端口", self.free_ports)):
            ttk.Button(box2, text=text, command=fn).pack(side="left", padx=3)
        # Apache ↔ Nginx 切换（文字在 refresh() 里按当前服务器改）
        self.btn_switch = ttk.Button(box2, text="换成 Nginx", command=self.switch_server)
        self.btn_switch.pack(side="left", padx=3)

        # ---- 站点 ----
        mid = ttk.Frame(self.tk, padding=(12, 12, 12, 0))
        mid.pack(fill="both", expand=True)
        ttk.Label(mid, text="站点（双击打开）", style="TLabel").pack(anchor="w")
        wrap = ttk.Frame(mid)
        wrap.pack(fill="both", expand=True, pady=(4, 0))
        self.listbox = tk.Listbox(wrap, bg="white", fg=INK, relief="flat",
                                  highlightthickness=1, highlightbackground="#f0c9dc",
                                  font=("Consolas", 10), activestyle="none")
        self.listbox.pack(side="left", fill="both", expand=True)
        bar = ttk.Scrollbar(wrap, orient="vertical", command=self.listbox.yview)
        bar.pack(side="right", fill="y")
        self.listbox.configure(yscrollcommand=bar.set)
        self.listbox.bind("<Double-Button-1>", self._open_selected)

        sitebar = ttk.Frame(mid)
        sitebar.pack(fill="x", pady=(6, 0))
        ttk.Button(sitebar, text="新建站点…", command=self.site_add).pack(side="left", padx=(0, 4))
        ttk.Button(sitebar, text="删除选中", command=self.site_remove).pack(side="left", padx=4)
        ttk.Button(sitebar, text="打开选中", command=self._open_selected).pack(side="left", padx=4)

        # ---- 域名后缀 ----
        tldrow = ttk.Frame(self.tk, padding=(12, 10, 12, 0))
        tldrow.pack(fill="x")
        ttk.Label(tldrow, text="域名后缀  .", style="TLabel").pack(side="left")
        self.var_tld = tk.StringVar(value=self.cfg["sites"]["tld"])
        ttk.Entry(tldrow, textvariable=self.var_tld, width=12).pack(side="left", padx=4)
        ttk.Button(tldrow, text="应用", command=self.apply_tld).pack(side="left")
        ttk.Button(tldrow, text="打开网站目录", command=self.open_www).pack(side="right")
        ttk.Button(tldrow, text="打开喵盒目录", command=self.open_root).pack(side="right", padx=4)
        ttk.Button(tldrow, text="邮件收件箱", command=self.open_mail).pack(side="right", padx=4)

        # ---- 日志 ----
        bottom = ttk.Frame(self.tk, padding=(12, 10, 12, 12))
        bottom.pack(fill="both")
        ttk.Label(bottom, text="日志", style="TLabel").pack(anchor="w")
        self.log_text = tk.Text(bottom, height=6, bg="white", fg=INK, relief="flat",
                                highlightthickness=1, highlightbackground="#f0c9dc",
                                font=("Consolas", 9), wrap="word")
        self.log_text.pack(fill="both", expand=True, pady=(4, 0))
        self.log_text.configure(state="disabled")

    def _build_menu(self) -> None:
        """菜单栏：把命令行那些命令都挂上去，省得挤一堆按钮。"""
        menubar = tk.Menu(self.tk)

        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label="生成双击启动器（.vbs）", command=self.do_shortcut)
        self.var_autostart = tk.BooleanVar(value=self._autostart_enabled())
        m_file.add_checkbutton(label="开机自动启动（启动文件夹里的 .vbs）",
                               variable=self.var_autostart, command=self.toggle_autostart)
        m_file.add_separator()
        m_file.add_command(label="便携检查…", command=self.portable_check)
        m_file.add_separator()
        m_file.add_command(label="退出", command=self.tk.destroy)
        menubar.add_cascade(label="文件", menu=m_file)

        m_svc = tk.Menu(menubar, tearoff=0)
        m_svc.add_command(label="启动全部", command=self.start_all)
        m_svc.add_command(label="停止全部", command=self.stop_all)
        m_svc.add_command(label="重启", command=self.restart)
        m_svc.add_separator()
        m_svc.add_command(label="切换 Web 服务器（Apache ↔ Nginx）", command=self.switch_server)
        m_svc.add_command(label="初始化 MySQL 数据目录…", command=self.db_init)
        m_svc.add_separator()
        m_svc.add_command(label="释放被占端口", command=self.free_ports)
        menubar.add_cascade(label="服务", menu=m_svc)

        m_site = tk.Menu(menubar, tearoff=0)
        m_site.add_command(label="新建站点…", command=self.site_add)
        m_site.add_command(label="删除选中站点…", command=self.site_remove)
        m_site.add_command(label="打开选中站点", command=self._open_selected)
        m_site.add_separator()
        m_site.add_command(label="同步 hosts（需要管理员）", command=self.sync_hosts)
        m_site.add_command(label="修改域名后缀…", command=self.ask_tld)
        menubar.add_cascade(label="站点", menu=m_site)

        m_php = tk.Menu(menubar, tearoff=0)
        self.m_php = m_php
        m_php.configure(postcommand=self._fill_php_menu)
        menubar.add_cascade(label="PHP", menu=m_php)

        m_tool = tk.Menu(menubar, tearoff=0)
        m_ssl = tk.Menu(m_tool, tearoff=0)
        m_ssl.add_command(label="HTTPS 体检（为什么没小绿锁）", command=self.ssl_doctor)
        m_ssl.add_separator()
        m_ssl.add_command(label="一键启用 HTTPS（签发 + 信任）", command=self.make_ssl)
        m_ssl.add_command(label="重新签发全部证书", command=lambda: self.make_ssl(force=True))
        m_ssl.add_command(label="查看证书状态", command=self.ssl_list)
        m_ssl.add_separator()
        m_ssl.add_command(label="把根证书装进信任库", command=lambda: self.ssl_trust(True))
        m_ssl.add_command(label="从信任库移除根证书", command=lambda: self.ssl_trust(False))
        m_ssl.add_separator()
        m_ssl.add_command(label="关闭 HTTPS", command=self.ssl_off)
        m_tool.add_cascade(label="HTTPS 证书", menu=m_ssl)
        m_tool.add_separator()
        m_tool.add_command(label="打开邮件收件箱（Mailpit）", command=self.open_mail)
        m_tool.add_command(label="查看信箱状态（几封信）", command=self.mail_status)
        m_tool.add_command(label="清空邮件信箱…", command=self.mail_clear)
        m_tool.add_separator()
        m_tool.add_command(label="安装软件包…", command=self.open_packages)
        m_tool.add_command(label="从 Laragon 迁移站点和数据库…", command=self.migrate_from_laragon)
        m_tool.add_command(label="彻底自带（组件搬进 bin/）…", command=self.vendor_components)
        m_tool.add_separator()
        m_tool.add_command(label="日志占用…", command=self.logs_survey)
        m_tool.add_command(label="清理日志（太旧 / 太大）…", command=self.logs_clean)
        m_tool.add_command(label="打开日志文件夹", command=lambda: util.open_path(self.lay["logs"]))
        m_tool.add_command(label="启动系统托盘", command=self.start_tray)
        menubar.add_cascade(label="工具", menu=m_tool)

        m_help = tk.Menu(menubar, tearoff=0)
        m_help.add_command(label="关于喵盒…", command=self.about)
        menubar.add_cascade(label="帮助", menu=m_help)

        self.tk.config(menu=menubar)

    def _load_header_icon(self) -> None:
        """把猫爪画成小图放进标题旁边（emoji 在 Tk 里不上色，不如用我们自己的图）。"""
        self._icon_img = None
        try:
            from . import icon as iconmod
            png = self.root_dir / "assets" / "meowbox-24.png"
            if not png.is_file():
                iconmod.write_png(png, 24)
            self._icon_img = tk.PhotoImage(file=str(png))
        except Exception:                       # noqa: BLE001 —— 图挂了不影响功能
            self._icon_img = None

    # ------------------------------------------------------------ 小工具
    def log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _run(self, label: str, fn) -> None:
        """耗时动作丢到后台线程，结果回主线程打印。"""
        self.log(f"> {label} ...")

        def worker():
            try:
                self.queue.put((label, "ok", fn() or "完成"))
            except Exception as exc:                    # noqa: BLE001
                self.queue.put((label, "err", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _bg_log(self, text: str) -> None:
        """后台线程里打日志用这个 —— Tk 控件不能从别的线程碰。"""
        self.queue.put(("", "log", str(text)))

    def _drain(self) -> None:
        while True:
            try:
                label, kind, message = self.queue.get_nowait()
            except queue.Empty:
                break
            if kind == "log":                       # 后台的进度输出，直接转进日志框
                self.log(message)
                continue
            mark = "[OK]" if kind == "ok" else "[!!]"
            if kind == "ssl-report":
                problems = sum(1 for level, _i, _n in message["checks"] if level == "warn")
                self.log(f"[OK] {label}: " + ("全绿 ✅" if message["ok"]
                                             else f"发现 {problems} 处不对劲，详情见窗口"))
                self._show_ssl_report(message)
                self.refresh()
                continue
            self.log(f"{mark} {label}: {message}")
            if kind == "err":
                messagebox.showerror("喵盒", f"{label}失败：\n\n{message}")
            self.refresh()
        self.tk.after(150, self._drain)

    def _tick(self) -> None:
        self._refresh_status()
        self.tk.after(2500, self._tick)

    # ------------------------------------------------------------ 刷新
    def _sync_labels(self) -> None:
        """服务集合可能变（换 Web 服务器后 apache↔nginx），标签跟着重排。"""
        names = list(self.services)
        if set(names) == set(self.svc_labels):
            return
        for label in self.svc_labels.values():
            label.destroy()
        self.svc_labels = {}
        for name in names:
            label = tk.Label(self._status_row, text="", bg=BG,
                             font=("Microsoft YaHei UI", 10))
            label.pack(side="left", padx=(0, 14))
            self.svc_labels[name] = label

    def _reload_services(self) -> None:
        """配置可能被换过服务器 —— 重新按配置建一次服务表（很便宜，不碰进程）。"""
        self.services = svcmod.build(self.cfg, self.bins)

    def _refresh_status(self) -> None:
        states = dict(svcmod.status(self.services))
        for name, label in self.svc_labels.items():
            state = states.get(name, "已停止")
            pretty = svcmod.pretty(name)
            if state == "运行中":
                text, color = "● " + pretty, "#2e9e5b"
            elif state == "被外部占用":
                text, color = "◐ " + pretty + "（外部占用）", "#d1802c"
            else:
                text, color = "○ " + pretty, "#a89ba1"
            label.configure(text=text, fg=color)

        blocked = set(svcmod.blocked_ports(self.services))
        bits = []
        for port in svcmod.service_ports(self.services):
            holder = util.port_holder(port)
            if not holder:
                bits.append(f"{port} 空闲")
            elif port in blocked:
                bits.append(f"{port}←{holder[1]}（非喵盒启动）")
            else:
                bits.append(f"{port} 正常")
        # 端口多了会超出一行宽度（80/443/3306/1025/8025），每三个折一行
        lines, chunk = [], []
        for bit in bits:
            chunk.append(bit)
            if len(chunk) == 3:
                lines.append("  ".join(chunk))
                chunk = []
        if chunk:
            lines.append("  ".join(chunk))
        if lines:
            lines[0] = "端口 " + lines[0]
        self.lbl_port.configure(text="\n".join(lines),
                                justify="left",
                                fg="#d1802c" if blocked else "#8a7d84")

    def _refresh_ssl(self) -> None:
        """HTTPS 状态。certutil 要起子进程，所以只在 refresh() 里算，别放 2.5 秒的轮询里。"""
        if not self.cfg["web"].get("ssl"):
            self.lbl_ssl.configure(text="HTTPS：未启用", fg="#a89ba1")
            return
        from . import ssl as sslmod
        ca = sslmod.CertAuthority(self.cfg, self.bins)
        trusted = ca.ca_exists() and sslmod.is_trusted(ca)
        self.lbl_ssl.configure(
            text="HTTPS：已启用" + ("（证书受信任）" if trusted else "（根证书未信任）"),
            fg="#2e9e5b" if trusted else "#c98b3a",
        )

    def refresh(self) -> None:
        tld = self.cfg["sites"]["tld"]
        self.bins = cfgmod.discover(self.cfg)
        self._reload_services()
        self._sync_labels()
        other = "apache" if svcmod.server_name(self.cfg) == "nginx" else "nginx"
        self.btn_switch.configure(text="换成 " + svcmod.pretty(other))
        self.lbl_bins.configure(
            text="  ".join(n for n, p in self.bins.items() if p) or "没有探测到组件")
        self.listbox.delete(0, "end")
        self.sites: list[tuple[str, str]] = []
        versions = phpmod.scan(self.cfg)
        default = phpmod.default_label(self.cfg, versions)
        for name in vhost.scan(self.lay["www"]):
            host = vhost.hostname_for(name, tld)
            url = vhost.site_url(self.cfg, self.lay, name)
            label = phpmod.site_label(self.cfg, name, versions) or "—"
            # 不是默认版本就标出来 —— 一眼能看出哪个站在用老 PHP
            php_text = f"PHP {label}" + ("" if label == default else " *")
            self.sites.append((name, host))
            self.listbox.insert("end", f"  {name:<18} {url:<27} {php_text}")
        self._refresh_status()
        self._refresh_ssl()
        try:
            self.var_autostart.set(self._autostart_enabled())
        except Exception:                       # noqa: BLE001
            pass

    # ------------------------------------------------------------ 服务
    def start_all(self) -> None:
        def job():
            rows = svcmod.start(self.services, ["all"])
            bad = [f"{svcmod.pretty(n)}: {e}" for n, s, e in rows if s == "失败"]
            if bad:
                raise RuntimeError("\n".join(bad))
            return "服务已启动"
        self._run("启动全部", job)

    def stop_all(self) -> None:
        def job():
            rows = svcmod.stop(self.services, ["all"])
            bad = [f"{svcmod.pretty(n)}: {e}" for n, s, e in rows if s == "失败"]
            if bad:
                raise RuntimeError("\n".join(bad))
            return "服务已停止"
        self._run("停止全部", job)

    def restart(self) -> None:
        def job():
            # 只重启「刚才在跑的」，别顺手把 mailpit 这种不在 autostart 里的关掉
            names = [n for n, svc in self.services.items() if svc.status() == "运行中"] or ["all"]
            svcmod.stop(self.services, ["all"])
            import time
            time.sleep(0.5)
            rows = svcmod.start(self.services, names)
            bad = [f"{svcmod.pretty(n)}: {e}" for n, s, e in rows if s == "失败"]
            if bad:
                raise RuntimeError("\n".join(bad))
            return "服务已重启"
        self._run("重启", job)

    def switch_server(self) -> None:
        """Apache ↔ Nginx 一键切换（会先停掉正在跑的那个）。"""
        current = svcmod.server_name(self.cfg)
        target = "apache" if current == "nginx" else "nginx"

        def job():
            info = svcmod.switch_web(self.cfg, self.bins, target)
            if not info["changed"]:
                return f"已经是 {svcmod.pretty(target)} 了"
            tail = f"（已停掉 {svcmod.pretty(info['from'])}）" if info["stopped"] else ""
            return f"Web 服务器已换成 {svcmod.pretty(target)}{tail}"
        self._run("切换 Web 服务器", job)

    def db_init(self) -> None:
        mysql = self.services.get("mysql")
        if mysql is None:
            messagebox.showinfo("喵盒", "配置里把 MySQL 关掉了（mysql.enabled = false）")
            return
        if not mysql.available:
            messagebox.showwarning("喵盒", "没找到 MySQL 程序（检查 provider / bin 目录）")
            return
        if mysql.initialized():
            self.log("MySQL 数据目录已经初始化过了，跳过")
            return
        if not messagebox.askyesno("喵盒", f"初始化 MySQL 数据目录？\n\n{mysql.datadir}\n\nroot 会没有密码，本地开发够用。"):
            return

        def job():
            rc, out, err = mysql.init()
            if rc != 0:
                raise RuntimeError((out + err).strip()[-1200:])
            return "MySQL 数据目录已初始化（root 无密码）"
        self._run("初始化数据库", job)

    # ------------------------------------------------------------ 配置 / 站点
    def render(self) -> None:
        def job():
            server = svcmod.web(self.services)
            if server is None:
                raise RuntimeError("配置里没认出 Web 服务器")
            label = svcmod.pretty(server.name)
            if not server.available:
                raise RuntimeError(f"没找到 {label}")
            server.render()
            rc, out, err = server.test()
            if rc != 0:
                raise RuntimeError(f"{label} 配置有语法错误：\n" + (out + err).strip()[-1200:])
            extra = ""
            if server.running() and server.name == "nginx":
                extra = "（已热加载）"
            return f"{label} 配置已重新生成，语法自检通过{extra}"
        self._run("重新生成配置", job)

    def site_add(self) -> None:
        name = simpledialog.askstring("新建站点", "站点名字（会变成 <名字>.<后缀>）：", parent=self.tk)
        if not name:
            return
        name = name.strip()
        if not SITE_RE.fullmatch(name):
            messagebox.showwarning("喵盒", "名字只能用字母、数字、连字符和下划线，且不能以符号开头")
            return
        target = self.lay["www"] / name
        if target.exists():
            messagebox.showinfo("喵盒", f"已经存在了：\n{target}")
            return

        def job():
            target.mkdir(parents=True, exist_ok=True)
            (target / "index.php").write_text(
                f"<?php\n// {name} —— 由喵盒创建\necho '喵～ {name} 跑起来了';\n",
                encoding="utf-8")
            server = svcmod.web(self.services)
            if server is not None and server.available:
                server.render()
            host = vhost.hostname_for(name, self.cfg["sites"]["tld"])
            return f"已创建 {host}（目录 {target}）"
        self._run("新建站点", job)

    def site_remove(self) -> None:
        selection = self.listbox.curselection()
        if not selection or not getattr(self, "sites", None):
            messagebox.showinfo("喵盒", "先在列表里点一个站点")
            return
        name, host = self.sites[selection[0]]
        if not messagebox.askyesno(
                "喵盒", f"删除站点 {name}（{host}）？\n\n"
                        f"整个文件夹会被删掉：\n{self.lay['www'] / name}\n\n删了就找不回来。"):
            return

        def job():
            shutil.rmtree(self.lay["www"] / name)
            if phpmod.prunes(self.cfg, vhost.scan(self.lay["www"])):
                cfgmod.save(self.cfg)
            server = svcmod.web(self.services)
            if server is not None and server.available:
                server.render()
            return f"已删除 {name}（记得同步 hosts，不然域名还指着本地）"
        self._run("删除站点", job)

    def ask_tld(self) -> None:
        new = simpledialog.askstring(
            "域名后缀", "新的后缀（不带点，例如 meow / test / local）：",
            initialvalue=self.cfg["sites"]["tld"], parent=self.tk)
        if not new:
            return
        self.var_tld.set(new.strip().lstrip(".").lower())
        self.apply_tld()

    def apply_tld(self) -> None:
        import re as _re
        new = self.var_tld.get().strip().lstrip(".").lower()
        if not new:
            messagebox.showwarning("喵盒", "后缀不能为空")
            return
        if "." in new or not _re.match(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", new):
            messagebox.showwarning("喵盒", "后缀只能用小写字母、数字和连字符，且不能以连字符开头或结尾")
            return
        old = self.cfg["sites"]["tld"]
        if new == old:
            self.log(f"后缀已经是 .{new} 了")
            return
        self.cfg["sites"]["tld"] = new
        cfgmod.save(self.cfg)
        server = svcmod.web(self.services)
        if server is not None and server.available:
            server.render()
        self.log(f"✅ 域名后缀：.{old} → .{new}（配置已重新生成）")
        self.log("   别忘了点「同步 hosts」让浏览器认识新域名")
        self.refresh()

    # ------------------------------------------------------------ hosts / 端口
    def sync_hosts(self) -> None:
        names = [host for _name, host in getattr(self, "sites", [])] or \
                [vhost.hostname_for(n, self.cfg["sites"]["tld"])
                 for n in vhost.scan(self.lay["www"])]

        # 写 hosts 要管理员；界面里也别让人手动去敲命令 —— 直接弹 UAC
        if not util.is_admin():
            if not names:
                messagebox.showinfo("喵盒", "还没有站点，不用同步")
                return
            if not messagebox.askyesno(
                    "喵盒", "改 hosts 需要管理员权限。\n\n"
                            "要现在弹 UAC 提权去写吗？（点「是」后请在系统弹窗里选「是」）"):
                return
            argv = runtime.console_argv("hosts", "--apply")
            if util.shell_execute_elevated(argv[0], runtime.quote_args(argv[1:]),
                                           str(self.lay["root"])):
                self.log("已弹出 UAC：在系统窗口里点「是」，hosts 就会写好")
            else:
                messagebox.showerror("喵盒", "提权失败（UAC 被取消了？）")
            return

        def job():
            result = hostsmod.sync(names, apply=True)
            if result.get("error"):
                raise PermissionError(result["error"])
            if not result["applied"]:
                return "已经是最新的，不用改"
            return "已写入 " + ", ".join(names)
        self._run("同步 hosts", job)

    def free_ports(self) -> None:
        """把被「不是喵盒启动的」进程占着的端口抢回来（没权限会弹 UAC）。"""
        def job():
            messages = []
            for port in svcmod.blocked_ports(self.services):
                ok, message = svcmod.free_port(self.cfg, self.services, port)
                messages.append(("" if ok else "失败：") + message)
            return "；".join(messages) if messages else "没有端口被别的进程占着"
        self._run("释放被占端口", job)

    # ------------------------------------------------------------ HTTPS
    def ssl_list(self) -> None:
        def job():
            from . import ssl as sslmod
            ca = sslmod.CertAuthority(self.cfg, self.bins)
            if not ca.available:
                raise RuntimeError("找不到可用的 openssl.exe（meowbox.config.json 的 openssl 指一个）")
            lines = ["根证书：" + ("已建（%s）" % ca.ca_crt if ca.ca_exists() else "还没建")]
            if ca.ca_exists():
                lines.append("信任状态：" + ("已装进当前用户信任库" if sslmod.is_trusted(ca)
                                            else "没装（浏览器会警告）"))
            hosts = sslmod.site_hostnames(self.cfg)
            if not hosts:
                lines.append("（还没有站点）")
            for host in hosts:
                if not ca.has_cert(host):
                    lines.append(f"⚠️  {host}  没有证书")
                    continue
                info = ca.info(host)
                ok, _ = ca.verify(host)
                lines.append(f"{'✅' if ok else '❌'}  {host}  到期 {info['not_after']}")
            return "\n".join(lines)
        self._run("查看证书", job)

    def ssl_doctor(self) -> None:
        """HTTPS 体检：从开关一路查到真握手，缺哪层就说哪层。"""
        self.log("> HTTPS 体检 ...")

        def worker():
            try:
                from . import doctor
                self.queue.put(("HTTPS 体检", "ssl-report",
                                doctor.https_report(self.cfg, self.bins)))
            except Exception as exc:                    # noqa: BLE001
                self.queue.put(("HTTPS 体检", "err", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _show_ssl_report(self, report: dict) -> None:
        """把体检结果摆成一页，能修的话直接给一个「一键修复」。"""
        win = tk.Toplevel(self.tk)
        win.title("HTTPS 体检")
        win.configure(bg=BG)
        win.geometry("780x440")
        win.transient(self.tk)
        head = ("一层层查下来全绿：" if report["ok"]
                else "从下往上修，缺哪层就修哪层：")
        ttk.Label(win, text=head + " 开关 → 根证书 → 站点证书 → SSL 虚拟主机 → 端口 → 真握手",
                  style="TLabel").pack(anchor="w", padx=12, pady=(10, 6))

        wrap = ttk.Frame(win)
        wrap.pack(fill="both", expand=True, padx=12)
        text = tk.Text(wrap, bg="white", fg=INK, relief="flat", wrap="word",
                       font=("Microsoft YaHei UI", 9), highlightthickness=1,
                       highlightbackground="#f0c9dc", padx=8, pady=6)
        text.pack(side="left", fill="both", expand=True)
        sbar = ttk.Scrollbar(wrap, orient="vertical", command=text.yview)
        sbar.pack(side="right", fill="y")
        text.configure(yscrollcommand=sbar.set)
        for tag, colour in (("ok", "#2e7d32"), ("warn", "#c2410c"),
                            ("info", "#6b7280"), ("advice", ACCENT)):
            text.tag_configure(tag, foreground=colour, font=("Microsoft YaHei UI", 9))
        text.tag_configure("verdict", foreground=INK, font=("Microsoft YaHei UI", 9, "bold"))

        marks = {"ok": ("✅ ", "ok"), "warn": ("⚠️ ", "warn"), "info": ("ℹ️ ", "info")}
        for level, item, note in report["checks"]:
            mark, tag = marks.get(level, ("· ", "info"))
            text.insert("end", mark + item + "：", tag)
            text.insert("end", note.replace("**", "") + "\n", tag)
        if report["advice"]:
            text.insert("end", "\n接下来：\n", "verdict")
            for tip in report["advice"]:
                text.insert("end", "  → " + tip + "\n", "advice")
        text.insert("end", "\n" + ("结论：小绿锁链路是通的 ✅" if report["ok"]
                                   else "结论：还差几步，按上面的箭头做就行"), "verdict")
        text.insert("end", "\n（这次体检已记到 logs/https-check.log，"
                           "以后锁变红可以回溯）", "info")
        text.configure(state="disabled")

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=12, pady=10)
        if report.get("fixable"):
            def fix_now():
                win.destroy()
                self.make_ssl(force=True)
            ttk.Button(bar, text="一键修复（重签全部证书 → 重生成配置 → 重启）",
                       command=fix_now).pack(side="left")
        ttk.Button(bar, text="重新体检",
                   command=lambda: (win.destroy(), self.ssl_doctor())
                   ).pack(side="left", padx=6)
        ttk.Button(bar, text="关闭", command=win.destroy).pack(side="right")

    # ------------------------------------------------------------ 日志
    def _logs_watch(self) -> None:
        """启动时瞄一眼日志占用：超过 20MB 就提一句（**不自动删**，删不删你说了算）。"""
        try:
            from . import logs as logsmod
            info = logsmod.survey(self.lay["logs"])
        except OSError:
            return
        if info["total"] <= logsmod.WARN_TOTAL_BYTES:
            return
        biggest = info["files"][0] if info["files"] else None
        hint = (f"⚠️ 日志已经 {logsmod.human(info['total'])}（{info['count']} 个文件"
                + (f"，最大的是 {biggest['name']} {logsmod.human(biggest['size'])}" if biggest else "")
                + "）—— 工具 → 清理日志 可以一键收拾")
        self.log(hint)

    def logs_survey(self) -> None:
        """看看 logs/ 堆了多少、要不要清。"""
        def job():
            from . import logs as logsmod
            info = logsmod.survey(self.lay["logs"])
            text = logsmod.summary_text(info)
            big = [f for f in info["files"] if f["size"] > logsmod.DEFAULT_MAX_BYTES]
            old = [f for f in info["files"] if f["age_days"] > logsmod.DEFAULT_KEEP_DAYS]
            if big or old:
                text += (f"\n\n建议清理：{len(big)} 个超过 "
                         f"{logsmod.human(logsmod.DEFAULT_MAX_BYTES)}，"
                         f"{len(old)} 个超过 {logsmod.DEFAULT_KEEP_DAYS} 天没动"
                         "（工具 → 清理日志）")
            else:
                text += "\n\n暂时不用清（单文件都在 2MB 内，也没有 7 天没动的）"
            if (self.lay["logs"] / logsmod.CHECK_LOG).is_file():
                text += f"\n体检记录：{logsmod.CHECK_LOG}（按大小滚动，留 .1 .2 两份）"
            return text
        self._run("日志占用", job)

    def logs_clean(self) -> None:
        """清太旧 / 太大的日志：先预览，确认了再动手；被占用的自动跳过。"""
        from . import logs as logsmod
        busy = logsmod.busy_prefixes(self.cfg, self.bins)
        preview = logsmod.clean(self.lay["logs"], dry_run=True, busy=busy)
        if not (preview["removed"] or preview["truncated"] or preview["skipped"]):
            messagebox.showinfo("喵盒", logsmod.clean_text(preview))
            return
        if not messagebox.askyesno("喵盒", logsmod.clean_text(preview)
                                   + "\n\n现在真的清掉？（被服务占着的会自动跳过）"):
            return

        def job():
            return logsmod.clean_text(logsmod.clean(self.lay["logs"], busy=busy))
        self._run("清理日志", job)

    def ssl_trust(self, enable: bool) -> None:
        def job():
            from . import ssl as sslmod
            ca = sslmod.CertAuthority(self.cfg, self.bins)
            ok, message = (sslmod.trust_ca(ca) if enable else sslmod.untrust_ca(ca))
            if not ok:
                raise RuntimeError(message)
            return message
        self._run("信任根证书" if enable else "取消信任", job)

    def ssl_off(self) -> None:
        if not messagebox.askyesno("喵盒", "关掉 HTTPS？\n\n证书文件都会留着，随时能再打开。"):
            return

        def job():
            from . import ssl as sslmod
            sslmod.disable(self.cfg)
            server = svcmod.web(self.services)
            if server is not None and server.available:
                server.render()
            return "已关闭 HTTPS（证书都留着，重启服务后生效）"
        self._run("关闭 HTTPS", job)

    def make_ssl(self, force: bool = False) -> None:
        """一键：建 CA → 签证书 → 开 HTTPS → 装信任库 → 重启当前 Web 服务器。"""
        def job():
            from . import ssl as sslmod
            lines = []
            info = sslmod.provision(self.cfg, self.bins, force=force,
                                    do_trust=True, log=lines.append)
            server = svcmod.web(self.services)
            if server is None or not server.available:
                return "证书已签发，但没找到 Web 服务器\n" + "\n".join(lines)
            label = svcmod.pretty(server.name)
            was_running = server.running()
            server.render()
            rc, out, err = server.test()
            if rc != 0:
                sslmod.disable(self.cfg)
                server.render()
                raise RuntimeError(f"{label} 语法检查没过，已把 SSL 关回去（免得服务起不来）：\n"
                                   + (out + err).strip()[-600:])
            if was_running:
                try:
                    server.stop()
                    server.start()
                except Exception as exc:            # noqa: BLE001
                    return f"证书已就绪，但 {label} 重启失败：{exc}"
            made, skipped = len(info["made"]), len(info["skipped"])
            trust = "根证书已装进信任库" if info["trusted"] else "根证书没装进信任库"
            return f"签发 {made} 张、复用 {skipped} 张；{trust}"
        self._run("重新签发证书" if force else "生成 SSL 证书", job)

    # ------------------------------------------------------------ 邮件 / 软件包
    def open_mail(self) -> None:
        """打开 Mailpit 收件箱（没在跑就先把它拉起来）。"""
        mail = self.services.get("mailpit")
        if mail is None:
            messagebox.showinfo("喵盒", "配置里把 mailpit.enabled 关掉了（或者没装 Mailpit）")
            return
        if not mail.available:
            messagebox.showwarning("喵盒", "没找到 Mailpit 程序： python meowbox.py pkg install mailpit")
            return

        def job():
            if not mail.running():
                mail.start()
            util.open_path(mail.ui_url())
            return f"收件箱：{mail.ui_url()}"
        self._run("打开邮件收件箱", job)

    def mail_clear(self) -> None:
        mail = self.services.get("mailpit")
        if mail is None:
            messagebox.showinfo("喵盒", "配置里没启用 Mailpit")
            return
        if not messagebox.askyesno("喵盒", "清空 Mailpit 信箱？测试邮件会全没（服务会先停一下）。"):
            return

        def job():
            was_running = mail.running()
            if was_running:
                mail.stop()
            removed = mail.purge()
            tail = f"（删了 {', '.join(removed)}）" if removed else "（本来就是空的）"
            return "信箱已清空" + tail + ("；Mailpit 已停止，要接着用就点启动" if was_running else "")
        self._run("清空信箱", job)

    def mail_status(self) -> None:
        """信箱里现在几封信（跟命令行 python meowbox.py mail 一个信息）。"""
        def job():
            mail = self.services.get("mailpit")
            if mail is None:
                raise RuntimeError("配置里没启用 Mailpit（mailpit.enabled）")
            if not mail.available:
                raise RuntimeError("没找到 Mailpit 程序： python meowbox.py pkg install mailpit")
            info = mail.summary()
            state = mail.status()
            if info is None:
                return f"状态：{state}（没在跑就没有统计）"
            return f"状态：{state}；共 {info['total']} 封、未读 {info['unread']} 封 —— {mail.ui_url()}"
        self._run("查看信箱状态", job)

    def start_tray(self) -> None:
        util.popen(runtime.launch_argv("tray"), cwd=self.lay["root"])
        self.log("托盘已拉起（已经在跑的话它自己会退出 —— 单实例）")

    def migrate_from_laragon(self) -> None:
        """从 Laragon 把站点文件夹和数据库搬过来（对话框里能看到要搬多少）。"""
        try:
            src = migratemod.find_source(self.cfg)
        except Exception as exc:                        # noqa: BLE001
            messagebox.showerror("喵盒", str(exc))
            return
        pl = migratemod.plan(self.cfg, src)
        if not pl["sites"] and not pl["dbs"]:
            messagebox.showinfo("喵盒", "Laragon 里没找到站点或数据库")
            return

        sites_txt = "\n".join(f"  {s['name']}    {migratemod.human(s['size'])}"
                              for s in pl["sites"]) or "  （无）"
        dbs_txt = "\n".join(f"  {d['name']}    {migratemod.human(d['size'])}"
                            for d in pl["dbs"]) or "  （无）"
        total = sum(s["size"] for s in pl["sites"]) + sum(d["size"] for d in pl["dbs"])
        if not messagebox.askyesno(
                "从 Laragon 迁移",
                f"要搬 {migratemod.human(total)}：\n\n站点：\n{sites_txt}\n\n"
                f"数据库：\n{dbs_txt}\n\n"
                "数据库会用 Laragon 的 mysqld 临时起一个实例导出（跑的是复制出来的副本，"
                "原件不动），再导进喵盒的 MySQL。\n\n现在开始？"):
            return

        keep = src.get("tld")
        keep_tld = bool(keep) and keep != self.cfg["sites"]["tld"] and messagebox.askyesno(
            "域名后缀",
            f"Laragon 用的是 .{keep}，喵盒现在是 .{self.cfg['sites']['tld']}。\n\n"
            f"点「是」= 保持 .{keep}（站点里写死的网址不会失效，verinawiki 这种必须保持）\n"
            f"点「否」= 用 .{self.cfg['sites']['tld']}（那站点里的旧网址要你自己改）")

        def job():
            lines: list[str] = []
            say = lines.append

            def emit(msg):
                say(str(msg))

            if keep_tld:
                migratemod.apply_tld(self.cfg, keep, say=emit)
            if pl["sites"]:
                emit("站点：")
                migratemod.migrate_sites(self.cfg, pl, overwrite=False, say=emit)
            if pl["dbs"]:
                emit("数据库：")
                migratemod.migrate_databases(self.cfg, self.bins, pl, say=emit)
            server = svcmod.web(self.services)
            if server is not None and server.available:
                server.render()
                rc, out, err = server.test()
                emit(f"{svcmod.pretty(server.name)} 配置重新生成："
                     + ("✅ 通过" if rc == 0 else "❌ " + (out + err).strip()[-400:]))
                if rc == 0 and server.name == "nginx" and server.running():
                    server.reload()
            return "\n".join(lines) or "没有需要迁移的东西"
        self._run("从 Laragon 迁移", job)

    def vendor_components(self) -> None:
        """彻底自带：把 provider 里的组件复制到自己的 bin/，provider 设成 null。"""
        if not self.cfg.get("provider"):
            messagebox.showinfo("喵盒", "已经是自带模式了（provider = null）\n\n"
                                        "现在用的是自己 bin/ 里的程序。")
            return
        pl = vendormod.plan(self.cfg, self.bins)
        total = sum(e["size"] for e in pl)
        missing = [e["key"] for e in pl if e["problem"]]
        detail = "\n".join(
            f"  {vendormod.LABELS.get(e['key'], e['key']):<8} "
            f"{migratemod.human(e['size']) if e['size'] else '—'}"
            for e in pl)
        extra = ""
        if missing:
            extra = "\n\n⚠️ provider 里没有：" + ", ".join(missing) + \
                    "\n先把它们下载下来（python meowbox.py pkg install …）再点一次。"
        if not messagebox.askyesno(
                "彻底自带",
                f"把 {migratemod.human(total)} 复制到 D:\\meowbox\\bin\\：\n\n{detail}\n\n"
                "复制完会把 provider 改成 null，并且用新程序重启服务（会短暂断开）。\n"
                f"Laragon 之后就能删了。{extra}\n\n现在开始？"):
            return
        if missing:
            messagebox.showwarning("喵盒", "还有组件没着落：" + ", ".join(missing))
            return

        def job():
            lines: list[str] = []
            info = vendormod.full_vendor(self.cfg, self.bins, say=lines.append, restart=True)
            self.bins = cfgmod.discover(self.cfg)
            ok = sum(1 for c in info["checks"] if c[0])
            lines.append(f"自检通过 {ok}/{len(info['checks'])} 项")
            return "\n".join(lines)
        self._run("彻底自带", job)

    def open_packages(self) -> None:
        """软件包对话框：从 packages.conf 里挑，一键下载解压到 bin/。"""
        try:
            entries = pkgmod.load_menu(self.cfg)
        except Exception as exc:                        # noqa: BLE001
            messagebox.showerror("喵盒", f"读 packages.conf 失败：{exc}")
            return

        win = tk.Toplevel(self.tk)
        win.title("安装软件包")
        win.configure(bg=BG)
        win.geometry("640x360")
        win.transient(self.tk)
        ttk.Label(win, text="从 packages.conf 里挑一个，下到 cache/ 再解压到 bin/"
                            "（走 curl，慢的话等一会儿）",
                  style="TLabel").pack(anchor="w", padx=12, pady=(10, 4))
        wrap = ttk.Frame(win)
        wrap.pack(fill="both", expand=True, padx=12)
        box = tk.Listbox(wrap, bg="white", fg=INK, relief="flat", activestyle="none",
                         font=("Consolas", 9), highlightthickness=1,
                         highlightbackground="#f0c9dc")
        box.pack(side="left", fill="both", expand=True)
        sbar = ttk.Scrollbar(wrap, orient="vertical", command=box.yview)
        sbar.pack(side="right", fill="y")
        box.configure(yscrollcommand=sbar.set)
        for name, url in entries:
            box.insert("end", f"  {name:<18} {url}")

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=12, pady=10)

        def install():
            selection = box.curselection()
            if not selection:
                return
            name = entries[selection[0]][0]
            if not messagebox.askyesno("喵盒", f"下载并安装 {name}？"):
                return
            win.destroy()

            def job():
                ok, message = pkgmod.install(self.cfg, name)
                if not ok:
                    raise RuntimeError(message)
                self.bins = cfgmod.discover(self.cfg)
                return message
            self._run(f"安装 {name}", job)

        ttk.Button(bar, text="安装选中", command=install).pack(side="left")
        ttk.Button(bar, text="刷新清单", command=lambda: (win.destroy(), self.open_packages())
                   ).pack(side="left", padx=6)
        ttk.Button(bar, text="关闭", command=win.destroy).pack(side="right")

    # ------------------------------------------------------------ 便携 / 启动器
    def _autostart_enabled(self) -> bool:
        try:
            from . import tray
            return tray.autostart_enabled()
        except Exception:                       # noqa: BLE001
            return False

    def toggle_autostart(self) -> None:
        enable = bool(self.var_autostart.get())
        try:
            from . import tray
            tray.set_autostart(enable)
        except Exception as exc:                        # noqa: BLE001
            self.var_autostart.set(not enable)
            messagebox.showerror("喵盒", f"改开机自启失败：{exc}")
            return
        self.log(("✅ 已开启" if enable else "✅ 已关闭") +
                 "开机自启（启动文件夹里的 .vbs，不写注册表）")

    def do_shortcut(self) -> None:
        def job():
            made = launchmod.write_launchers(self.lay["root"])
            return "已生成：" + ", ".join(p.name for p in made)
        self._run("生成双击启动器", job)

    def portable_check(self) -> None:
        """便携检查：配置里有没有写死路径、搬家会不会坏。"""
        def job():
            info = portablemod.report(self.cfg, self.bins)
            marks = {"ok": "✅", "warn": "⚠️", "info": "ℹ️"}
            lines = [f"{marks.get(level, '·')} {item}：{note}"
                     for level, item, note in info["checks"]]
            verdict = ("结论：整个文件夹可以随便搬（不装服务、不写注册表）"
                       if info["portable"] else
                       "结论：还有写死的路径，搬家前先修一下（python meowbox.py portable --fix）")
            return "\n".join(lines) + "\n" + verdict
        self._run("便携检查", job)

    def about(self) -> None:
        from . import __version__
        messagebox.showinfo(
            "关于喵盒",
            f"喵盒 Meow Box v{__version__}\n\n"
            f"根目录：{self.lay['root']}\n"
            f"配置文件：{cfgmod.config_path(self.lay['root'])}\n"
            "Web 服务器：" + svcmod.pretty(svcmod.server_name(self.cfg)) + "\n\n"
            "便携：整个文件夹复制走就能用；不装 Windows 服务、不写注册表。\n"
            "（开机自启只是「启动」文件夹里一个 .vbs，删掉就取消）")

    # ------------------------------------------------------------ 其它
    def open_www(self) -> None:
        util.open_path(self.lay["www"])

    def open_root(self) -> None:
        util.open_path(self.lay["root"])

    def _open_selected(self, _event=None) -> None:
        selection = self.listbox.curselection()
        if not selection or not getattr(self, "sites", None):
            return
        index = selection[0]
        if 0 <= index < len(self.sites):
            util.open_path(vhost.site_url(self.cfg, self.lay, self.sites[index][0]))

    # ------------------------------------------------------------ PHP
    def _fill_php_menu(self) -> None:
        """每次拉开菜单都重新扫一遍（可能刚装完新版本）。"""
        menu = self.m_php
        menu.delete(0, "end")
        versions = phpmod.scan(self.cfg)
        default = phpmod.default_label(self.cfg, versions)
        ports = phpmod.port_map(self.cfg, versions)
        if versions:
            menu.add_command(label=f"默认版本：{default}（点下面的换）", state="disabled")
            for label, ver in versions.items():
                tick = "●" if label == default else "○"
                menu.add_command(
                    label=f"{tick} {ver.version}　FastCGI :{ports.get(label, '-')}",
                    command=lambda l=label: self.php_use(l))
            menu.add_separator()
        else:
            menu.add_command(label="还没有装 PHP", state="disabled")
            menu.add_separator()
        menu.add_command(label="安装 PHP 版本…（从 php.net 下载）", command=self.php_install)
        menu.add_command(label="给选中的站点换 PHP 版本…", command=self.php_set_site)
        menu.add_separator()
        menu.add_command(label="打开 php.ini", command=self.php_open_ini)
        menu.add_command(label="打开 PHP 目录", command=self.php_open_dir)
        menu.add_separator()
        menu.add_command(label="打开 phpMyAdmin（没装会先装）", command=self.php_myadmin)

    def _php_apply(self, message: str) -> None:
        """PHP 版本变了：存配置 → 重渲染 → 正在跑就重启。"""
        cfgmod.save(self.cfg)

        def job():
            server = svcmod.web(self.services)
            if server is None or not server.available:
                return message + "（没找到 Web 服务器，只改了配置）"
            was_running = server.running()
            server.render()
            if was_running:
                server.stop()
                server.start()
                return message + "（Web 服务器已重启）"
            return message + "（下次启动生效）"
        self._run("切换 PHP 版本", job)

    def php_use(self, label: str) -> None:
        versions = phpmod.scan(self.cfg)
        if label not in versions:
            messagebox.showwarning("喵盒", f"没有装 PHP {label}")
            return
        self.cfg["php"]["version"] = label
        self._php_apply(f"默认 PHP 版本 → {versions[label].version}（{label}）")

    def php_install(self) -> None:
        want = simpledialog.askstring(
            "安装 PHP", "要装哪个版本？\n\n填 8.3 / 8.4 / 8.5 这样的大版本就行（会装该系列最新补丁），留空装最新版：",
            initialvalue="", parent=self.tk)
        if want is None:
            return
        want = want.strip()
        if not messagebox.askyesno(
                "喵盒", f"从 windows.php.net 下载 PHP {want or '（最新版）'} 的 x64 线程安全包？\n\n"
                        "大约 30~40 MB，装进 bin/php/，不会动已经装好的其他版本。"):
            return

        def job():
            ver = phpmod.install(self.cfg, want, say=self._bg_log)
            return f"已安装 PHP {ver.version}（{ver.label}）"

        self._run("安装 PHP", job)

    def php_set_site(self) -> None:
        selection = self.listbox.curselection()
        if not selection or not getattr(self, "sites", None):
            messagebox.showinfo("喵盒", "先在站点列表里点一个站点")
            return
        name, _host = self.sites[selection[0]]
        versions = phpmod.scan(self.cfg)
        if not versions:
            messagebox.showinfo("喵盒", "还没有装 PHP")
            return
        current = phpmod.site_label(self.cfg, name, versions)
        options = "、".join(versions)
        answer = simpledialog.askstring(
            "站点 PHP 版本",
            f"{name} 现在用的是 {current}\n\n可选：{options}\n"
            "输入 default 表示跟随默认版本：",
            initialvalue=current or "", parent=self.tk)
        if answer is None:
            return
        answer = answer.strip()
        if answer not in ("default", "默认") and answer not in versions:
            messagebox.showwarning("喵盒", f"没有装 PHP {answer}")
            return
        phpmod.set_site_version(self.cfg, name, None if answer in ("default", "默认") else answer)
        target = phpmod.site_label(self.cfg, name, versions)
        self._php_apply(f"{name} → PHP {target}")

    def php_open_ini(self) -> None:
        versions = phpmod.scan(self.cfg)
        label = phpmod.default_label(self.cfg, versions)
        if not label:
            messagebox.showinfo("喵盒", "还没有装 PHP")
            return
        ini = versions[label].ini
        if not ini.is_file():
            messagebox.showwarning("喵盒", f"PHP {label} 没有 php.ini：\n{ini}")
            return
        util.open_path(ini)

    def php_open_dir(self) -> None:
        versions = phpmod.scan(self.cfg)
        label = phpmod.default_label(self.cfg, versions)
        util.open_path(versions[label].dir if label else (self.lay["root"] / "bin" / "php"))

    def php_myadmin(self) -> None:
        lay = self.lay
        if not phpmod.phpmyadmin_ready(self.cfg):
            if not messagebox.askyesno(
                    "喵盒", "还没装 phpMyAdmin。\n\n从官方源下载一份放到 www/phpmyadmin？\n"
                            "（免登录、只监听本机，本地开发用）"):
                return

            def job():
                target = phpmod.install_phpmyadmin(self.cfg, say=self._bg_log)
                server = svcmod.web(self.services)
                if server is not None and server.available:
                    server.render()
                return f"phpMyAdmin 已装好：{target}（记得同步 hosts）"

            self._run("安装 phpMyAdmin", job)
            return
        url = vhost.site_url(self.cfg, lay, "phpmyadmin")
        util.open_path(url)
        self.log(f"[OK] 打开 {url}")

    def run(self) -> int:
        self.log(f"喵盒已就绪 —— {self.lay['root']}")
        where = sys.executable if runtime.frozen() else util.pythonw()
        self.log(f"运行方式：{where}　（菜单栏里还有更多功能：服务 / 站点 / 工具）")
        self._logs_watch()
        self.tk.mainloop()
        return 0


def run_panel(root_dir) -> int:
    try:
        panel = Panel(root_dir)
    except Exception as exc:                            # noqa: BLE001
        import tkinter as _tk
        from tkinter import messagebox as _mb
        hidden = _tk.Tk()
        hidden.withdraw()
        _mb.showerror("喵盒 Meow Box", f"打不开控制面板：\n\n{exc}")
        hidden.destroy()
        return 1
    return panel.run()
