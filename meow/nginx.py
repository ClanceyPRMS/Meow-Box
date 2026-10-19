"""Nginx 的管家：渲染配置、语法自检、启停 —— 外加一个 php-cgi 小跟班。

跟 Apache 那套不一样的地方（都是实测出来的）：

* Nginx 自己不跑 PHP：必须另起一个 `php-cgi.exe -b 127.0.0.1:端口` 的
  FastCGI 进程，站点里的 `.php` 请求全转给它。这个进程由喵盒一并看管。
* 主配置用 `-c` 指过去，`-p` 把 prefix 指到喵盒根目录 —— 不这么指，
  Windows 版 Nginx 会把默认的粗活目录（temp/、logs/）写进
  `D:\\laragon\\bin\\nginx\\<版本>\\` 里，脏了别人的安装目录。
* 重载/优雅停止不是命令行里按 Ctrl+C，而是 `nginx -s reload|quit`，
  它靠配置里 `pid` 指向的那个文件找主进程。
* Nginx 的主进程在 Windows 上是**我们 popen 起来的那个进程本身**，
  所以 pid 文件里记的就是主进程号；它自己还会写一份 `nginx-master.pid`，
  两份都在 `run/` 下，谁丢了都能互相兜底。
"""
from __future__ import annotations

import time
from pathlib import Path

from . import config as cfgmod
from . import php as phpmod
from . import util, vhost
from .util import ensure_dir, fwd, kill_tree, popen, run


#: 站点 server 块共用的 FastCGI 参数表（和 nginx 自带 fastcgi_params 等价，
#: 只是路径由喵盒自己写死，免得依赖 provider 的 conf 目录）
FASTCGI_PARAMS = """\
# 喵盒自动生成 —— 站点 server 块 include 这个文件
fastcgi_param  QUERY_STRING       $query_string;
fastcgi_param  REQUEST_METHOD     $request_method;
fastcgi_param  CONTENT_TYPE       $content_type;
fastcgi_param  CONTENT_LENGTH     $content_length;

fastcgi_param  SCRIPT_NAME        $fastcgi_script_name;
fastcgi_param  REQUEST_URI        $request_uri;
fastcgi_param  DOCUMENT_URI       $document_uri;
fastcgi_param  DOCUMENT_ROOT      $document_root;
fastcgi_param  SERVER_PROTOCOL    $server_protocol;
fastcgi_param  REQUEST_SCHEME     $scheme;
fastcgi_param  HTTPS              $https if_not_empty;

fastcgi_param  GATEWAY_INTERFACE  CGI/1.1;
fastcgi_param  SERVER_SOFTWARE    nginx/$nginx_version;

fastcgi_param  REMOTE_ADDR        $remote_addr;
fastcgi_param  REMOTE_PORT        $remote_port;
fastcgi_param  SERVER_ADDR        $server_addr;
fastcgi_param  SERVER_PORT        $server_port;
fastcgi_param  SERVER_NAME        $server_name;

fastcgi_param  REDIRECT_STATUS    200;
"""


class Nginx:
    name = "nginx"

    def __init__(self, cfg: dict, bins: dict):
        self.cfg = cfg
        self.bins = bins
        self.lay = cfgmod.layout(cfg)
        self.dir = Path(bins["nginx"]) if bins.get("nginx") else None

        # Nginx 没有 mod_php：每个 PHP 版本一个 php-cgi 端口
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
        return self.dir is not None and (self.dir / "nginx.exe").is_file()

    @property
    def exe(self) -> Path:
        return self.dir / "nginx.exe"

    @property
    def prefix(self) -> Path:
        """Nginx 的工作目录（-p）：粗活文件全落在喵盒里，不碰 provider。"""
        return self.lay["root"]

    @property
    def conf(self) -> Path:
        return self.lay["etc_nginx"] / "nginx.conf"

    @property
    def sites_dir(self) -> Path:
        return self.lay["nginx_sites"]

    @property
    def fastcgi_params(self) -> Path:
        return self.lay["etc_nginx"] / "fastcgi_params"

    @property
    def temp_dir(self) -> Path:
        return self.lay["run"] / "nginx-temp"

    @property
    def mime_types(self) -> Path:
        return self.dir / "conf" / "mime.types"

    @property
    def pidfile(self) -> Path:
        """喵盒记下的主进程号。"""
        return self.lay["run"] / "nginx.pid"

    @property
    def master_pidfile(self) -> Path:
        """Nginx 自己在配置里 pid 指令写的那份。"""
        return self.lay["run"] / "nginx-master.pid"

    @property
    def php_pidfile(self) -> Path:
        return self.lay["run"] / "php-cgi.pid"

    @property
    def php_exe(self) -> Path | None:
        if not self.php_dir:
            return None
        return self.php_dir / "php-cgi.exe"

    @property
    def php_ini(self) -> Path | None:
        if not self.php_dir:
            return None
        candidate = self.php_dir / "php.ini"
        return candidate if candidate.is_file() else None

    @property
    def fcgi_port(self) -> int:
        """默认版本的端口（默认站点 / 没指定版本的站点用它）。"""
        php = self.cfg.get("php") or {}
        if self.php_default and self.php_default in self.php_ports:
            return self.php_ports[self.php_default]
        return int(php.get("fastcgi_port", 9000))

    def port_for(self, name: str) -> int:
        label = phpmod.site_label(self.cfg, name, self.php_versions)
        if label and label in self.php_ports:
            return self.php_ports[label]
        return self.fcgi_port

    @property
    def port(self) -> int:
        return int(self.cfg["web"]["http_port"])

    @property
    def ports(self) -> list[int]:
        ports = [self.port]
        if self.cfg["web"].get("ssl"):
            ports.append(int(self.cfg["web"]["https_port"]))
        return ports

    # ---------- 渲染 ----------
    def render(self) -> Path:
        lay = self.lay
        web = self.cfg["web"]
        ssl = bool(web.get("ssl"))
        ensure_dir(lay["etc_nginx"])
        ensure_dir(self.sites_dir)
        for sub in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi"):
            ensure_dir(self.temp_dir / sub)
        ensure_dir(lay["logs"])

        tpl = (lay["templates"] / "nginx.conf.tpl").read_text(encoding="utf-8")
        text = util.render_template(
            tpl,
            WORKERS=2,
            LOGDIR=fwd(lay["logs"]),
            RUN=fwd(lay["run"]),
            TEMP=fwd(self.temp_dir),
            MIME_TYPES=fwd(self.mime_types),
            SITES_DIR=fwd(self.sites_dir),
        )
        self.conf.write_text(text, encoding="utf-8", newline="\n")
        self.fastcgi_params.write_text(FASTCGI_PARAMS, encoding="utf-8", newline="\n")

        self._render_sites(ssl)
        self._render_default(ssl)
        return self.conf

    def _render_sites(self, ssl_on: bool) -> list[str]:
        lay = self.lay
        tld = self.cfg["sites"]["tld"]
        https_port = int(self.cfg["web"]["https_port"])
        names = vhost.scan(lay["www"])

        wanted: dict[str, str] = {}
        for name in names:
            host = vhost.hostname_for(name, tld)
            wanted[f"auto.{host}.conf"] = vhost.render_nginx(
                lay["www"], name, tld, self.port, lay["logs"], lay["templates"],
                fastcgi_port=self.port_for(name), fastcgi_params=self.fastcgi_params)

        if ssl_on:
            for name in names:
                host = vhost.hostname_for(name, tld)
                crt = lay["ssl_sites"] / f"{host}.crt"
                key = lay["ssl_sites"] / f"{host}.key"
                if crt.is_file() and key.is_file():
                    wanted[f"auto.{host}-ssl.conf"] = vhost.render_nginx_ssl(
                        lay["www"], name, tld, https_port, lay["logs"], lay["templates"],
                        crt, key, fastcgi_port=self.port_for(name),
                        fastcgi_params=self.fastcgi_params)

        # 换后缀 / 关 SSL / 删站点之后，旧的 server 块要清干净
        for old in self.sites_dir.glob("auto.*.conf"):
            if old.name not in wanted:
                old.unlink()
        for filename, text in wanted.items():
            (self.sites_dir / filename).write_text(text, encoding="utf-8", newline="\n")
        return names

    def _render_default(self, ssl_on: bool) -> None:
        """监听默认 server：未知域名（localhost）也要有东西看。"""
        text = vhost.render_nginx_default(
            self.lay["www"], self.port, fastcgi_port=self.fcgi_port,
            fastcgi_params=self.fastcgi_params)
        (self.sites_dir / "00-default.conf").write_text(text, encoding="utf-8", newline="\n")

    # ---------- 命令 ----------
    def _argv(self, *extra) -> list[str]:
        return [str(self.exe), *extra, "-p", fwd(self.prefix) + "/", "-c", fwd(self.conf)]

    def test(self):
        """让 Nginx 自己检查配置语法（输出走 stderr）。"""
        if not self.available:
            return 1, "", "没找到 Nginx，无法检查"
        return run(self._argv("-t"), cwd=self.dir, timeout=30)

    def _signal(self, signal: str):
        return run(self._argv("-s", signal), cwd=self.dir, timeout=30)

    def reload(self) -> bool:
        if not self.running():
            return False
        rc, _, _ = self._signal("reload")
        return rc == 0

    # ---------- 进程 ----------
    def _pid(self):
        try:
            return int(self.pidfile.read_text().strip())
        except Exception:
            return None

    def _master_pid(self):
        try:
            return int(self.master_pidfile.read_text().strip())
        except Exception:
            return None

    def _php_pid(self):
        try:
            return int(self.php_pidfile.read_text().strip())
        except Exception:
            return None

    @staticmethod
    def _alive(pid) -> bool:
        return util.pid_is(pid, ("nginx",))

    def running(self) -> bool:
        return self._alive(self._pid())

    def php_running(self) -> bool:
        return self._alive(self._php_pid())

    def orphan(self):
        """端口被 nginx.exe 占着，但喵盒没有它的 pid 记录。"""
        if self.running():
            return None
        for port in self.ports:
            holder = util.port_holder(port)
            if holder and holder[1].lower().startswith("nginx"):
                return {"port": port, "pid": holder[0], "name": holder[1]}
        return None

    def _port_hint(self, port, holder) -> str:
        name = holder[1].lower() if holder else ""
        if name.startswith("nginx"):
            return ("\n它看起来是另一个 Nginx —— 多半是之前用管理员权限启动的喵盒，"
                    f"\n普通权限停不掉它。解决： python meowbox.py freeport {port}")
        if name.startswith("httpd"):
            return ("\nApache 还开着 —— 喵盒同一时间只跑一个 Web 服务器。"
                    "\n先 python meowbox.py stop apache")
        if "steam" in name or "accelerator" in name or "proxifier" in name:
            return "\n这是加速器/代理抢的端口，把它关掉或在它的设置里换一个口。"
        return f"\n把占着它的程序关掉，或者 python meowbox.py freeport {port}"

    # ---------- 启动 ----------
    def pool(self) -> phpmod.CgiPool:
        if self._pool is None:
            self._pool = phpmod.CgiPool(self.cfg, self.php_versions)
        return self._pool

    def _start_php(self) -> dict[str, int]:
        """把站点用到的每个 PHP 版本都起一个 php-cgi。"""
        if not self.php_versions:
            raise RuntimeError(
                "Nginx 不会自己跑 PHP —— 需要 php-cgi.exe，但一个 PHP 都没装。\n"
                "先 `python meowbox.py php install 8.3`，或者用 `vendor` 带上 PHP。")
        names = vhost.scan(self.lay["www"])
        labels = self.pool().labels_for(names, "nginx")
        if not labels:
            return {}
        missing = [l for l in labels if not self.php_versions[l].cgi.is_file()]
        if missing:
            raise RuntimeError(
                "这些 PHP 版本里没有 php-cgi.exe：" + "、".join(missing) +
                "\n换一个线程安全（TS）的官方包，或者改用 Apache。")
        return self.pool().start(labels)

    def start(self) -> int:
        if not self.available:
            raise RuntimeError("没找到 Nginx，请检查配置里的 provider / bin 目录")
        if self.running():
            return self._pid()
        for port in self.ports:
            if util.port_in_use(port):
                holder = util.port_holder(port)
                desc = f"{holder[1]} (pid {holder[0]})" if holder else "未知程序"
                raise RuntimeError(
                    f"端口 {port} 已被占用：{desc}" + self._port_hint(port, holder))

        self.render()
        # 上一轮退出时 Nginx 可能留下一个**空的** pid 文件，先清掉
        for stale in (self.pidfile, self.master_pidfile):
            stale.unlink(missing_ok=True)
        rc, out, err = self.test()
        if rc != 0:
            raise RuntimeError("Nginx 配置有语法错误，已中止启动：\n" + (out + err).strip())

        self._start_php()
        proc = popen(self._argv(), cwd=self.dir,
                     log_file=self.lay["logs"] / "nginx-console.log")
        self.pidfile.write_text(str(proc.pid), encoding="utf-8")

        for _ in range(50):
            if util.port_in_use(self.port):
                util.invalidate_listeners()
                return proc.pid
            if not self._alive(proc.pid):
                break
            time.sleep(0.2)

        tail = self._tail_log("nginx-console.log")
        self._stop_php()
        for stale in (self.pidfile, self.master_pidfile):
            stale.unlink(missing_ok=True)
        raise RuntimeError("Nginx 没能起来（日志末尾如下）：\n" + tail)

    def _tail_log(self, filename: str, limit: int = 1200) -> str:
        path = self.lay["logs"] / filename
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "（没有日志）"
        return text.strip()[-limit:] or "（日志是空的）"

    # ---------- 停止 ----------
    def _stop_php(self) -> bool:
        """把所有 php-cgi 进程收掉（有几个版本就收几个）。"""
        self.php_pidfile.unlink(missing_ok=True)
        if not self.php_versions:
            return False
        return bool(self.pool().stop_all())

    def stop(self) -> bool:
        pid = self._pid() or self._master_pid()
        if not pid:
            holder = None
            for port in self.ports:
                info = util.port_holder(port)
                if info and info[1].lower().startswith("nginx"):
                    holder = info
                    break
            if holder:
                pid = holder[0]
        if not pid:
            self._stop_php()
            for stale in (self.pidfile, self.master_pidfile):
                stale.unlink(missing_ok=True)
            return False

        handled = False
        if self._alive(pid):
            handled = True
            if self.available:
                # 先礼后兵：quit 是优雅退出，让 worker 把手上的请求做完。
                # 注意：还有 keepalive 连接挂着时它会等一会儿，所以只等 3 秒，
                # 之后用 stop（立刻收尾）——免得点「停止」跟死机一样没反应。
                self._signal("quit")
                for _ in range(12):
                    if not self._alive(pid):
                        break
                    time.sleep(0.25)
                if self._alive(pid):
                    self._signal("stop")
                    for _ in range(8):
                        if not self._alive(pid):
                            break
                        time.sleep(0.25)
            if self._alive(pid):
                kill_tree(pid)
                for _ in range(12):
                    if not self._alive(pid):
                        break
                    time.sleep(0.25)
        if self._alive(pid):
            # 跟 Apache 一样：停不掉就别删 pid 文件，免得下次 start 把
            # 「端口被占用」当成用户自己的问题报出来
            raise PermissionError(
                f"停不掉 Nginx（pid {pid}）。\n"
                "它多半是以管理员身份启动的，普通权限杀不掉。\n"
                "用管理员身份重开喵盒/终端，或者 python meowbox.py freeport "
                f"{self.port}"
            )
        self.pidfile.unlink(missing_ok=True)
        self.master_pidfile.unlink(missing_ok=True)
        self._stop_php()
        return handled

    def shutdown_external(self, pid: int) -> bool:
        """关掉一个「不是喵盒启动的」nginx（freeport 用）。先 -s quit 再硬砍。"""
        self._signal("quit")
        for _ in range(20):
            if not self._alive(pid):
                return True
            time.sleep(0.25)
        kill_tree(pid)
        for _ in range(12):
            if not self._alive(pid):
                return True
            time.sleep(0.25)
        return False

    def status(self) -> str:
        if self.running():
            return "运行中"
        if self.orphan():
            return "被外部占用"
        return "已停止"
