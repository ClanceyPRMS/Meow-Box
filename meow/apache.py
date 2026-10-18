"""Apache 的管家：渲染配置、语法自检、启停。

关键点：喵盒的 httpd.conf 用 `-f` 指定，ServerRoot 仍指向 Apache
自己的安装目录（这样 `modules/` 和 `conf/mime.types` 才能按相对路径
找到），但日志、站点配置全部落在 D:\\meowbox 里，互不干扰。
"""
from __future__ import annotations

import time
from pathlib import Path

from . import config as cfgmod
from . import util, vhost
from .util import ensure_dir, fwd, kill_tree, popen, run


class Apache:
    name = "apache"

    def __init__(self, cfg: dict, bins: dict):
        self.cfg = cfg
        self.lay = cfgmod.layout(cfg)
        self.dir = Path(bins["apache"]) if bins.get("apache") else None
        self.php_dir = Path(bins["php"]) if bins.get("php") else None

    # ---------- 路径 ----------
    @property
    def available(self) -> bool:
        return self.dir is not None and (self.dir / "bin" / "httpd.exe").is_file()

    @property
    def exe(self) -> Path:
        return self.dir / "bin" / "httpd.exe"

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
        """喵盒的 Apache 会监听的端口（开了 HTTPS 就多一个 443）。"""
        ports = [self.port]
        if self.cfg["web"].get("ssl"):
            ports.append(int(self.cfg["web"]["https_port"]))
        return ports

    def orphan(self):
        """端口被 httpd.exe 占着，但喵盒没有它的 pid 记录。

        典型场景：之前有人用**管理员权限**启动过喵盒的 Apache。
        那个进程普通权限既杀不掉、也不在喵盒的 pid 文件里 ——
        于是界面显示"已停止"，可站点其实还活着，端口也还占着。
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
            return ("\n它看起来是另一个 Apache —— 多半是之前用管理员权限启动的喵盒，"
                    f"\n普通权限停不掉它。解决： python meowbox.py freeport {port}")
        if "steam" in name or "accelerator" in name or "proxifier" in name:
            return "\n这是加速器/代理抢的端口，把它关掉或在它的设置里换一个口。"
        if name.startswith("mysqld"):
            return "\n另有一个 mysqld 在跑（是不是 Laragon 也开着？）"
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

        text = util.render_template(
            tpl,
            SERVERROOT=fwd(self.dir),
            DOCROOT=fwd(lay["www"]),
            LOGDIR=fwd(lay["logs"]),
            HTTP_PORT=web["http_port"],
            SERVER_NAME="meowbox",
            SSL_LISTEN=f"Listen {web['https_port']}" if ssl else "# SSL 未启用",
            SSL_MODULES=(
                "LoadModule socache_shmcb_module modules/mod_socache_shmcb.so\n"
                "LoadModule ssl_module modules/mod_ssl.so"
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
        )
        self.conf.write_text(text, encoding="utf-8", newline="\n")
        mod_php.write_text(self._mod_php_text(), encoding="utf-8", newline="\n")
        ssl_conf.write_text(self._ssl_conf_text(), encoding="utf-8", newline="\n")

        self._render_sites()
        self._render_default()
        return self.conf

    def _mod_php_text(self) -> str:
        """把选定的 PHP 版本挂进 Apache。"""
        if not self.php_dir or not self.php_dir.is_dir():
            return "# 喵盒：没找到 PHP，本次不挂载 php 模块\n"
        php = self.php_dir
        lines = ["# 喵盒自动生成 —— 换 PHP 版本只需重跑 render", ""]
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

        wanted: dict[str, str] = {}
        for name in names:
            host = vhost.hostname_for(name, tld)
            wanted[f"auto.{host}.conf"] = vhost.render_apache(
                lay["www"], name, tld, self.port, lay["logs"], lay["templates"])

        if ssl_on:
            for name in names:
                host = vhost.hostname_for(name, tld)
                crt, key = lay["ssl_sites"] / f"{host}.crt", lay["ssl_sites"] / f"{host}.key"
                if crt.is_file() and key.is_file():
                    wanted[f"auto.{host}-ssl.conf"] = vhost.render_apache_ssl(
                        lay["www"], name, tld, https_port, lay["logs"], lay["templates"],
                        crt, key)

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
    def test(self):
        """让 Apache 自己检查配置语法。"""
        if not self.available:
            return 1, "", "没找到 Apache，无法检查"
        return run([self.exe, "-t", "-f", self.conf], cwd=self.dir, timeout=30)

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
        if not pid:
            return False
        rc, out, _ = run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], timeout=20)
        return str(pid) in out

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
                raise RuntimeError(f"端口 {port} 已被占用：{desc}" + self._port_hint(port, holder))
        self.render()
        rc, out, err = self.test()
        if rc != 0:
            raise RuntimeError("Apache 配置有语法错误，已中止启动：\n" + (out + err).strip())
        proc = popen([self.exe, "-f", self.conf], cwd=self.dir,
                     log_file=self.lay["logs"] / "apache-console.log")
        self.pidfile.write_text(str(proc.pid), encoding="utf-8")
        for _ in range(30):
            if util.port_in_use(self.port):
                break
            time.sleep(0.2)
        return proc.pid

    def stop(self) -> bool:
        pid = self._pid()
        if not pid:
            return False
        if not self._alive(pid):
            self.pidfile.unlink(missing_ok=True)
            return False
        if self.available:
            # 先温柔地发信号让它自己收尾（别硬砍，MySQL 那边同理）
            run([self.exe, "-k", "shutdown", "-f", self.conf], cwd=self.dir, timeout=30)
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
                "它多半是以管理员身份启动的，普通权限杀不掉。\n"
                "关掉那个 Apache，或者用管理员身份重开喵盒/终端。"
            )
        self.pidfile.unlink(missing_ok=True)
        return True

    def status(self) -> str:
        if self.running():
            return "运行中"
        if self.orphan():
            return "被外部占用"
        return "已停止"
