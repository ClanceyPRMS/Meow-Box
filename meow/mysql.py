"""MySQL / MariaDB 的管家：生成 my.cnf、初始化数据目录、启停。

两个平台的差别：

* **Windows** —— `bin/mysql/bin/mysqld.exe`，初始化用
  `mysqld --initialize-insecure`，root 无密码。
* **Arch Linux** —— 系统包 `/usr/bin/mariadbd`（`mysqld` 是它的软链接），
  初始化用 **`mariadb-install-db`**（MariaDB 不认 `--initialize-insecure`）。
  **必须带 `--auth-root-authentication-method=normal`**：MariaDB 默认给 root
  用 `unix_socket` 认证，那样 phpMyAdmin / 站点用 TCP 连是连不上的。

MariaDB 和 MySQL 的 **socket 路径**在 Linux 上很重要：不给的话它去找
`/run/mysqld/mysqld.sock`（那是 systemd 服务的目录，普通用户没有），
所以喵盒把 socket 和 pid 都落在自己的 `run/` 里。
"""
from __future__ import annotations

import time
from pathlib import Path

from . import config as cfgmod
from . import plat, util
from .util import ensure_dir, fwd, kill_tree, popen, run


MY_INI = """# 喵盒自动生成的 MySQL / MariaDB 配置
[mysqld]
basedir="{basedir}"
datadir="{datadir}"
port={port}
bind-address=127.0.0.1
socket="{socket}"
pid-file="{pidfile}"
character-set-server=utf8mb4
collation-server=utf8mb4_general_ci
max_allowed_packet=64M
skip-log-bin
[client]
port={port}
socket="{socket}"
default-character-set=utf8mb4
"""


class MySQL:
    name = "mysql"

    def __init__(self, cfg: dict, bins: dict):
        self.cfg = cfg
        self.lay = cfgmod.layout(cfg)
        self.paths = plat.as_paths("mysql", (bins or {}).get("mysql"), cfg)
        self.dir = self.paths.root if self.paths else None

    @property
    def available(self) -> bool:
        return self.paths is not None and self.paths.exe.is_file()

    @property
    def exe(self) -> Path:
        return self.paths.exe

    @property
    def admin_exe(self) -> Path | None:
        return self.paths.admin if self.paths else None

    @property
    def client_exe(self) -> Path | None:
        return self.paths.client if self.paths else None

    @property
    def datadir(self) -> Path:
        return self.lay["data"] / "mysql"

    @property
    def my_ini(self) -> Path:
        return self.lay["etc"] / ("my.ini" if plat.IS_WINDOWS else "my.cnf")

    @property
    def pidfile(self) -> Path:
        return self.lay["run"] / "mysql.pid"

    @property
    def socket(self) -> Path:
        """Unix socket（Windows 上 MySQL 也支持，但没人用）。"""
        return self.lay["run"] / "mysql.sock"

    @property
    def port(self) -> int:
        return int(self.cfg["mysql"]["port"])

    def render(self) -> Path:
        ensure_dir(self.lay["etc"])
        ensure_dir(self.lay["run"])
        self.my_ini.write_text(
            MY_INI.format(
                basedir=fwd(self.dir), datadir=fwd(self.datadir), port=self.port,
                socket=fwd(self.socket), pidfile=fwd(self.pidfile),
            ),
            encoding="utf-8", newline="\n",
        )
        return self.my_ini

    def initialized(self) -> bool:
        # initialize 完之后会生成 mysql / performance_schema 这些系统库
        return (self.datadir / "mysql").is_dir()

    def init(self):
        """初始化数据目录（root 无密码）。"""
        if not self.available:
            raise RuntimeError("没找到 MySQL / MariaDB")
        if self.initialized():
            return 0, "数据目录已存在，跳过初始化", ""
        ensure_dir(self.datadir)
        self.render()

        if plat.IS_WINDOWS:
            return run([self.exe, "--defaults-file=" + fwd(self.my_ini),
                        "--initialize-insecure"], cwd=self.dir, timeout=180)

        helper = self.paths.helper if self.paths else None
        if helper is None or not helper.is_file():
            raise RuntimeError(
                "没找到 mariadb-install-db。装一下：sudo pacman -S mariadb")
        # `--defaults-file` 必须是第一个参数，否则 MariaDB 会先去读 /etc/my.cnf
        argv = [
            helper,
            "--defaults-file=" + fwd(self.my_ini),
            "--basedir=" + fwd(self.dir),
            "--datadir=" + fwd(self.datadir),
            # ★ 关键：不加这个，root 是 unix_socket 认证，站点和 phpMyAdmin 连不上
            "--auth-root-authentication-method=normal",
            "--skip-test-db",
        ]
        rc, out, err = run(argv, cwd=self.dir, timeout=300)
        return rc, out, err

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
        return util.pid_is(pid, ("mysqld", "mariadbd"))

    def running(self) -> bool:
        return self._alive(self._pid())

    def start(self) -> int:
        if not self.available:
            raise RuntimeError("没找到 MySQL / MariaDB")
        if self.running():
            return self._pid()
        if util.port_in_use(self.port):
            holder = util.who_holds_port(self.port) or "未知程序"
            raise RuntimeError(f"端口 {self.port} 已被占用：{holder}")
        if not self.initialized():
            raise RuntimeError("数据目录还没初始化，先跑： python meowbox.py db init")
        self.render()
        proc = popen([self.exe, "--defaults-file=" + fwd(self.my_ini)], cwd=self.dir,
                     log_file=self.lay["logs"] / "mysql-console.log")
        self.pidfile.write_text(str(proc.pid), encoding="utf-8")
        return proc.pid

    def stop(self) -> bool:
        pid = self._pid()
        if not pid:
            return False
        if not self._alive(pid):
            self.pidfile.unlink(missing_ok=True)
            return False
        admin = self.admin_exe
        if admin is not None and admin.is_file():
            run([admin, "-u", "root", f"--port={self.port}", "--protocol=TCP",
                 "--host=127.0.0.1", "shutdown"], timeout=30)
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
            raise PermissionError(
                f"停不掉 MySQL（pid {pid}）。多半是它当初以提权身份启动的，"
                "普通权限杀不掉。"
            )
        self.pidfile.unlink(missing_ok=True)
        return True

    def shutdown_external(self, pid: int) -> bool:
        """关闭一个「不是喵盒启动的」数据库进程 —— 走 mysqladmin，绝不硬杀。"""
        admin = self.admin_exe
        if admin is None or not admin.is_file():
            return False
        run([admin, "-u", "root", f"--port={self.port}",
             "--protocol=TCP", "--host=127.0.0.1", "shutdown"],
            cwd=self.dir, timeout=30)
        for _ in range(30):
            if not util.port_in_use(self.port):
                util.invalidate_listeners()
                return True
            time.sleep(0.3)
        return False

    def orphan(self):
        """端口被数据库进程占着，但不是喵盒启动的。"""
        if self.running():
            return None
        holder = util.port_holder(self.port)
        if holder and holder[1].lower().startswith(("mysqld", "mariadbd")):
            return {"port": self.port, "pid": holder[0], "name": holder[1]}
        return None

    def status(self) -> str:
        if self.running():
            return "运行中"
        if self.orphan():
            return "被外部占用"
        return "已停止"
