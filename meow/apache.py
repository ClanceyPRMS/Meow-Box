"""Apache 的管家：渲染配置、语法自检、启停。

两个平台的差别（都收在 `plat.Paths` 和模板占位符里）：

* **Windows** —— `bin/apache/<版本>/bin/httpd.exe`，ServerRoot 是那个版本目录，
  这样 `modules/` 和 `conf/mime.types` 才能按相对路径找到；MPM 是 `winnt`。
* **Arch Linux** —— 系统包的 `/usr/bin/httpd`，模块在 `/usr/lib/httpd/modules`，
  mime.types 在 `/etc/httpd/conf/mime.types`；MPM 是 `event`。
  **Linux 上 httpd 默认会 fork 到后台然后父进程退出** —— 于是
  `popen()` 记下的 pid 当场就死了，所以启动时必须带 `-DFOREGROUND`。

还有一条 Linux 特有的：发行版的 php 包**不带 mod_php**（Arch 的 `libphp.so`
在 AUR 的 php-apache 里），所以所有站点都走 `php-cgi` + `mod_proxy_fcgi`。
"""
from __future__ import annotations

import time
from pathlib import Path

from . import config as cfgmod
from . import php as phpmod
from . import plat, util, vhost
from .util import ensure_dir, fwd, kill_tree, popen, run


class Apache:
    name = "apache"

    def __init__(self, cfg: dict, bins: dict):
        self.cfg = cfg
        self.bins = bins
        self.lay = cfgmod.layout(cfg)
        self.paths = plat.as_paths("apache", (bins or {}).get("apache"), cfg)
        self.dir = self.paths.root if self.paths else None

        # PHP 可能装了好几个版本：mod_php 只能挂「默认版本」，
        # 其他版本交给 php-cgi（见 meow/php.py 的 CgiPool）
        self.php_versions = phpmod.scan(cfg)
        self.php_default = phpmod.default_label(cfg, self.php_versions)
        self.php_ports = phpmod.port_map(cfg, self.php_versions)
        default_dir = (self.php_versions[self.php_default].dir
                       if self.php_default else None)
        self.php_dir = default_dir or (Path(bins["php"]) if bins.get("php") else None)
        self._pool: phpmod.CgiPool | None = None

    # ---------- 路径 ----------
    @property
    def available(self) -> bool:
        return self.paths is not None and self.paths.exe.is_file()

    @property
    def exe(self) -> Path:
        return self.paths.exe

    @property
    def modules_dir(self) -> Path | None:
        return self.paths.modules

    @property
    def mime_types(self) -> Path | None:
        return self.paths.mime_types

    @property
    def server_root(self) -> Path:
        """ServerRoot：Linux 上指喵盒自己的 etc 目录（全是绝对路径，够用）；
        Windows 上必须是版本目录，否则 `modules/` 找不到。"""
        if plat.IS_WINDOWS:
            return self.paths.root
        return self.lay["etc_apache"]

    @property
    def conf(self) -> Path:
        return self.lay["etc_apache"] / "httpd.conf"

    @property
    def pidfile(self) -> Path:
        return self.lay["run"] / "apache.pid"

    @property
    def port(self) -> int:
        return int(self.cfg["web"]["http_port"])

    @property
    def ports(self) -> list[int]:
        """喵盒的 Apache 会监听的端口（开了 HTTPS 就多一个）。"""
        ports = [self.port]
        if self.cfg["web"].get("ssl"):
            ports.append(int(self.cfg["web"]["https_port"]))
        return ports

    def orphan(self):
        """端口被 httpd 占着，但喵盒没有它的 pid 记录。

        典型场景：之前有人用**提权**的方式启动过喵盒的 Apache
        （Windows 上的管理员、Linux 上的 root），那个进程普通权限既杀不掉、
        也不在喵盒的 pid 文件里 —— 于是界面显示"已停止"，可站点其实还活着。
        """
        if self.running():
            return None
        for port in self.ports:
            holder = util.port_holder(port)
            if holder and holder[1].lower().startswith("httpd"):
                return {"port": port, "pid": holder[0], "name": holder[1]}
        return None

    def _port_hint(self, port, holder) -> str:
        name = holder[1].lower() if holder else ""
        if name.startswith("httpd"):
            return ("\n它看起来是另一个 Apache —— 多半是之前提权启动的喵盒，"
                    f"\n普通权限停不掉它。解决： python meowbox.py freeport {port}")
        if "steam" in name or "accelerator" in name or "proxifier" in name:
            return "\n这是加速器/代理抢的端口，把它关掉或在它的设置里换一个口。"
        if name.startswith("mysqld") or name.startswith("mariadbd"):
            return "\n另有一个数据库在跑（是不是 Laragon / 系统 mariadb 也开着？）"
        return f"\n把占着它的程序关掉，或者 python meowbox.py freeport {port}"

    # ---------- 渲染 ----------
    def render(self) -> Path:
        lay = self.lay
        web = self.cfg["web"]
        ssl = bool(web.get("ssl"))
        tpl_path = lay["templates"] / "httpd.conf.tpl"
        tpl = tpl_path.read_text(encoding="utf-8")

        mod_php = lay["etc_apache"] / "mod_php.conf"
        ssl_conf = lay["etc_apache"] / "httpd-ssl.conf"
        ensure_dir(lay["etc_apache"])
        ensure_dir(lay["run"])

        # 模块目录：Windows 是版本目录下的 modules/，Linux 是 /usr/lib/httpd/modules
        modules_dir = self.modules_dir
        modules = fwd(modules_dir) if modules_dir is not None else "modules"

        # 有没有站点要用 FastCGI？有才加载 mod_proxy_fcgi
        names = vhost.scan(lay["www"])
        pool = phpmod.CgiPool(self.cfg, self.php_versions)
        fcgi_labels = pool.labels_for(names, "apache")
        if fcgi_labels:
            proxy_modules = (
                f"LoadModule proxy_module {modules}/mod_proxy.so\n"
                f"LoadModule proxy_fcgi_module {modules}/mod_proxy_fcgi.so"
            )
        else:
            proxy_modules = "# 没有站点需要 FastCGI —— 全都走 mod_php"

        mpm = plat.apache_mpm(self.exe) if self.available else (
            "winnt" if plat.IS_WINDOWS else "event")
        mime_types = self.mime_types
        mime_text = (fwd(mime_types) if mime_types is not None
                     else fwd(Path(self.server_root) / "conf" / "mime.types"))
        text = util.render_template(
            tpl,
            SERVERROOT=fwd(self.server_root),
            MODULES_DIR=modules,
            MIME_TYPES=mime_text,
            DOCROOT=fwd(lay["www"]),
            LOGDIR=fwd(lay["logs"]),
            RUN=fwd(lay["run"]),
            PIDFILE=fwd(self.pidfile),
            HTTP_PORT=web["http_port"],
            SERVER_NAME="meowbox",
            MPM_MODULES=self._mpm_modules(mpm),
            USER_BLOCK=self._user_block(),
            EXTRA_RUNTIME=self._runtime_block(),
            SSL_LISTEN=f"Listen {web['https_port']}" if ssl else "# SSL 未启用",
            SSL_MODULES=(
                f"LoadModule socache_shmcb_module {modules}/mod_socache_shmcb.so\n"
                f"LoadModule ssl_module {modules}/mod_ssl.so"
            ) if ssl else "# SSL 未启用（要开 HTTPS 就把 web.ssl 改成 true）",
            SSL_SEEDS=(
                "<IfModule ssl_module>\n"
                f"    SSLSessionCache shmcb:{fwd(lay['run'])}/ssl_scache(512000)\n"
                "    SSLSessionCacheTimeout 300\n"
                "    SSLRandomSeed startup builtin\n"
                "    SSLRandomSeed connect builtin\n"
                "</IfModule>"
            ) if ssl else "# SSL 未启用",
            ALIAS_DIR=fwd(lay["alias_dir"]),
            SITES_DIR=fwd(lay["sites_enabled"]),
            SSL_CONF=fwd(ssl_conf),
            MOD_PHP_CONF=fwd(mod_php),
            PROXY_MODULES=proxy_modules,
        )
        self.conf.write_text(text, encoding="utf-8", newline="\n")
        mod_php.write_text(self._mod_php_text(), encoding="utf-8", newline="\n")
        ssl_conf.write_text(self._ssl_conf_text(), encoding="utf-8", newline="\n")

        self._render_sites()
        self._render_default()
        return self.conf

    def _mpm_modules(self, mpm: str) -> str:
        """按平台/编译情况加载正确的 MPM。

        **Windows 上什么都不用加载**：Apache Lounge 的 winnt MPM 是编译进
        `httpd.exe` 的，`modules/` 里根本没有 `mod_mpm_winnt.so` ——
        写了就是「找不到指定的模块」。
        Linux 上 MPM（Arch 是 event）是 .so，而且必须配 `mod_unixd`。
        """
        if plat.IS_WINDOWS:
            return "# Windows：MPM(winnt) 编译在 httpd.exe 里，不用 LoadModule"
        mods = fwd(self.modules_dir) if self.modules_dir is not None else "modules"
        return (f"LoadModule mpm_{mpm}_module {mods}/mod_mpm_{mpm}.so\n"
                f"LoadModule unixd_module {mods}/mod_unixd.so")

    def _user_block(self) -> str:
        """User / Group。

        Windows 上没有这个概念；Linux 上以普通用户跑时 Apache 会忽略它们
        并打一条警告 —— 所以干脆不写，让它用默认值。
        """
        if plat.IS_WINDOWS:
            return "# Windows：没有 User / Group"
        return ("# Linux：以普通用户运行（要绑 80/443 请用 `python meowbox.py cap on`）\n"
                "ServerName localhost")

    def _runtime_block(self) -> str:
        """运行时文件（scoreboard / mutex）落在哪。

        不显式指的话，Linux 上的 Apache 会往 `ServerRoot/logs/` 里写，
        而喵盒的 ServerRoot 是个没有 logs 子目录的 etc 目录 → 起不来。

        语法注意：`Mutex` 是 `机制:目录`（Arch 官方就是 `Mutex default:/run/httpd`），
        写成 `Mutex file:"目录" default` 会报「Invalid Mutex directory」。
        """
        if plat.IS_WINDOWS:
            return ""
        run_dir = fwd(self.lay["run"])
        return (
            f'DefaultRuntimeDir "{run_dir}"\n'
            f'ScoreBoardFile  "{run_dir}/apache_scoreboard"\n'
            f'Mutex default:{run_dir}\n'
        )

    def _mod_php_text(self) -> str:
        """把选定的 PHP 版本挂进 Apache。

        **Linux 上不做这件事**：发行版的 php 包没有 mod_php，硬找
        `php*apache2_4.dll` 只会得到一句误导人的报错。所有站点走 FastCGI。
        """
        if not plat.IS_WINDOWS:
            return (
                "# 喵盒：Linux 上的 Apache 不挂 mod_php（发行版包不带 libphp.so）\n"
                "# 所有 .php 请求都通过 mod_proxy_fcgi 转给 php-cgi（见各站点配置）\n"
            )
        if not self.php_dir or not self.php_dir.is_dir():
            return "# 喵盒：没找到 PHP，本次不挂载 php 模块\n"
        php = self.php_dir
        label = (f"PHP {self.php_versions[self.php_default].version}"
                 if self.php_default in self.php_versions else "PHP（未知版本）")
        lines = [f"# 喵盒自动生成 —— mod_php 挂的是默认版本 {label}", ""]
        for lib in ("libcrypto-3-x64.dll", "libssl-3-x64.dll", "libssh2.dll"):
            if (php / lib).is_file():
                lines.append(f'LoadFile "{fwd(php / lib)}"')
        dlls = sorted(php.glob("php*apache2_4.dll"))
        if dlls:
            lines.append(f'LoadModule php_module "{fwd(dlls[-1])}"')
        else:
            lines.append("# 没找到 php*apache2_4.dll，PHP 未挂载")
        lines.append(f'PHPIniDir "{fwd(php)}"')
        lines.append("")
        lines.append("<IfModule mime_module>")
        lines.append("    AddType application/x-httpd-php .php")
        lines.append("</IfModule>")
        return "\n".join(lines) + "\n"

    def _ssl_conf_text(self) -> str:
        """SSL 全局设置（只在开了 HTTPS 时才有内容）。"""
        if not self.cfg["web"].get("ssl"):
            return "# 喵盒：SSL 未启用（跑 `python meowbox.py ssl` 打开）\n"
        return (
            "# 喵盒自动生成的 SSL 全局设置\n"
            "SSLProtocol all -SSLv3 -TLSv1 -TLSv1.1\n"
            "SSLCipherSuite HIGH:!aNULL:!MD5:!3DES\n"
            "SSLHonorCipherOrder off\n"
            "SSLCompression off\n"
        )

    def _render_sites(self) -> list[str]:
        lay = self.lay
        tld = self.cfg["sites"]["tld"]
        ssl_on = bool(self.cfg["web"].get("ssl"))
        https_port = int(self.cfg["web"]["https_port"])
        ensure_dir(lay["sites_enabled"])
        names = vhost.scan(lay["www"])

        as_module = phpmod._default_runs_as_module(self.cfg, self.php_versions)

        def fcgi_for(name):
            """这个站点要不要走 php-cgi？要的话返回 (端口, 标签)。"""
            if not self.php_versions:
                return None, None
            label = phpmod.site_label(self.cfg, name, self.php_versions)
            if not label:
                return None, None
            if as_module and label == self.php_default:
                return None, None
            return self.php_ports.get(label), label

        wanted: dict[str, str] = {}
        for name in names:
            host = vhost.hostname_for(name, tld)
            port, label = fcgi_for(name)
            wanted[f"auto.{host}.conf"] = vhost.render_apache(
                lay["www"], name, tld, self.port, lay["logs"], lay["templates"],
                fcgi_port=port, fcgi_label=label, default_label=self.php_default)

        if ssl_on:
            for name in names:
                host = vhost.hostname_for(name, tld)
                crt, key = lay["ssl_sites"] / f"{host}.crt", lay["ssl_sites"] / f"{host}.key"
                if crt.is_file() and key.is_file():
                    port, label = fcgi_for(name)
                    wanted[f"auto.{host}-ssl.conf"] = vhost.render_apache_ssl(
                        lay["www"], name, tld, https_port, lay["logs"], lay["templates"],
                        crt, key, fcgi_port=port, fcgi_label=label,
                        default_label=self.php_default)

        # 清掉不该存在的（换后缀、关 SSL、删站点之后留下的）
        for old in lay["sites_enabled"].glob("auto.*.conf"):
            if old.name not in wanted:
                old.unlink()
        for filename, text in wanted.items():
            (lay["sites_enabled"] / filename).write_text(text, encoding="utf-8", newline="\n")
        return names

    def _render_default(self) -> None:
        text = (
            "# 喵盒默认站点（第一个虚拟主机必须是主配置的浅拷贝）\n"
            f'<VirtualHost _default_:{self.port}>\n'
            f'    <Directory "{fwd(self.lay["www"])}">\n'
            "        AllowOverride All\n"
            "        Require all granted\n"
            "    </Directory>\n"
            "</VirtualHost>\n"
        )
        (self.lay["sites_enabled"] / "00-default.conf").write_text(text, encoding="utf-8", newline="\n")

    # ---------- 运行 ----------
    def _argv(self, *extra) -> list[str]:
        argv = [str(self.exe), *extra, "-f", fwd(self.conf)]
        if not plat.IS_WINDOWS:
            # Linux 上不加这个，httpd 会 fork 到后台然后父进程退出，
            # `popen()` 记下的 pid 立刻就没了 → status 永远是「已停止」
            argv.insert(1, "-DFOREGROUND")
        return argv

    def test(self):
        """让 Apache 自己检查配置语法。"""
        if not self.available:
            return 1, "", "没找到 Apache，无法检查"
        return run(self._argv("-t"), cwd=self.dir, timeout=30)

    @property
    def pid(self):
        """喵盒记录的进程号（没启动过就是 None）。"""
        return self._pid()

    def _pid(self):
        try:
            return int(self.pidfile.read_text().strip())
        except Exception:
            return None

    def _alive(self, pid) -> bool:
        return util.pid_is(pid, ("httpd",))

    def running(self) -> bool:
        return self._alive(self._pid())

    def start(self) -> int:
        if not self.available:
            raise RuntimeError("没找到 Apache，请检查配置里的 provider / bin 目录")
        if self.running():
            return self._pid()
        for port in self.ports:
            if util.port_in_use(port):
                holder = util.port_holder(port)
                desc = f"{holder[1]} (pid {holder[0]})" if holder else "未知程序"
                hint = self._port_hint(port, holder)
                if plat.needs_cap(port) and not plat.is_admin():
                    hint += (f"\nLinux 上绑 {port} 需要 CAP_NET_BIND_SERVICE："
                             "\n  python meowbox.py cap on      # 或者把 web.http_port 改成 8080")
                raise RuntimeError(f"端口 {port} 已被占用：{desc}" + hint)
        self.render()
        rc, out, err = self.test()
        if rc != 0:
            raise RuntimeError("Apache 配置有语法错误，已中止启动：\n" + (out + err).strip())
        self._start_pool()
        proc = popen(self._argv(), cwd=self.dir,
                     log_file=self.lay["logs"] / "apache-console.log")
        self.pidfile.write_text(str(proc.pid), encoding="utf-8")
        for _ in range(30):
            if util.port_in_use(self.port):
                break
            time.sleep(0.2)
        else:
            self._stop_pool()
            raise RuntimeError(
                "Apache 起来了但没监听 " + str(self.port) + " 端口，日志末尾：\n"
                + self._tail_log("apache-console.log"))

        # Linux：httpd 慢一点才写好 PidFile，顺便把它跟喵盒记的对齐
        if not plat.IS_WINDOWS and proc.poll() is not None:
            raise RuntimeError(
                "Apache 启动后立刻退出了，日志末尾：\n" + self._tail_log("apache-console.log"))
        return proc.pid

    # ---------- PHP FastCGI 池 ----------
    def pool(self) -> phpmod.CgiPool:
        if self._pool is None:
            self._pool = phpmod.CgiPool(self.cfg, self.php_versions)
        return self._pool

    def _start_pool(self) -> None:
        """非默认版本的站点要 php-cgi 先起来，Apache 才有东西转发。"""
        names = vhost.scan(self.lay["www"])
        labels = self.pool().labels_for(names, "apache")
        if labels:
            self.pool().start(labels)

    def _stop_pool(self) -> None:
        if self.php_versions:
            self.pool().stop_all()

    def _tail_log(self, filename: str, limit: int = 1000) -> str:
        try:
            text = (self.lay["logs"] / filename).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "（没有日志）"
        return text.strip()[-limit:] or "（日志是空的）"

    def stop(self) -> bool:
        pid = self._pid()
        if not pid:
            self._stop_pool()
            return False
        if not self._alive(pid):
            self.pidfile.unlink(missing_ok=True)
            self._stop_pool()
            return False
        if self.available:
            # 先温柔地发信号让它自己收尾（别硬砍，MySQL 那边同理）
            run([str(self.exe), "-k", "shutdown", "-f", fwd(self.conf)],
                cwd=self.dir, timeout=30)
        for _ in range(20):
            if not self._alive(pid):
                break
            time.sleep(0.25)
        if self._alive(pid):
            kill_tree(pid)
            for _ in range(10):
                if not self._alive(pid):
                    break
                time.sleep(0.25)
        if self._alive(pid):
            # 停不掉就别删 pid 文件 —— 不然下次 start 以为没在跑，
            # 然后把「端口被占用」当成用户的问题报出来。
            raise PermissionError(
                f"停不掉 Apache（pid {pid}）。\n"
                "它多半是以管理员 / root 身份启动的，普通权限杀不掉。\n"
                "关掉那个 Apache，或者用提权身份重开喵盒/终端。"
            )
        self.pidfile.unlink(missing_ok=True)
        self._stop_pool()
        return True

    def status(self) -> str:
        if self.running():
            return "运行中"
        if self.orphan():
            return "被外部占用"
        return "已停止"
