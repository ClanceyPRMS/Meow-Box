"""MySQL 的管家：生成 my.ini、初始化数据目录、启停。"""
from __future__ import annotations

import time
from pathlib import Path

from . import config as cfgmod
from . import util
from .util import ensure_dir, fwd, kill_tree, popen, run


MY_INI = """# 喵盒自动生成的 MySQL 配置
[mysqld]
basedir="{basedir}"
datadir="{datadir}"
port={port}
bind-address=127.0.0.1
character-set-server=utf8mb4
collation-server=utf8mb4_general_ci
max_allowed_packet=64M
[client]
port={port}
default-character-set=utf8mb4
"""


class MySQL:
    name = "mysql"

    def __init__(self, cfg: dict, bins: dict):
        self.cfg = cfg
        self.lay = cfgmod.layout(cfg)
        self.dir = Path(bins["mysql"]) if bins.get("mysql") else None

    @property
    def available(self) -> bool:
        return self.dir is not None and (self.dir / "bin" / "mysqld.exe").is_file()

    @property
    def exe(self) -> Path:
        return self.dir / "bin" / "mysqld.exe"

    @property
    def admin_exe(self) -> Path:
        return self.dir / "bin" / "mysqladmin.exe"

    @property
    def client_exe(self) -> Path:
        return self.dir / "bin" / "mysql.exe"

    @property
    def datadir(self) -> Path:
        return self.lay["data"] / "mysql"

    @property
    def my_ini(self) -> Path:
        return self.lay["etc"] / "my.ini"

    @property
    def pidfile(self) -> Path:
        return self.lay["run"] / "mysql.pid"

    @property
    def port(self) -> int:
        return int(self.cfg["mysql"]["port"])

    def render(self) -> Path:
        ensure_dir(self.lay["etc"])
        self.my_ini.write_text(
            MY_INI.format(basedir=fwd(self.dir), datadir=fwd(self.datadir), port=self.port),
            encoding="utf-8", newline="\n",
        )
        return self.my_ini

    def initialized(self) -> bool:
        # initialize 完之后会生成 mysql / performance_schema 这些系统库
        return (self.datadir / "mysql").is_dir()

    def init(self):
        """初始化数据目录（等价于 mysqld --initialize-insecure，root 无密码）。"""
        if not self.available:
            raise RuntimeError("没找到 MySQL")
        if self.initialized():
            return 0, "数据目录已存在，跳过初始化", ""
        ensure_dir(self.datadir)
        self.render()
        return run([self.exe, "--defaults-file=" + fwd(self.my_ini), "--initialize-insecure"],
                   cwd=self.dir, timeout=180)

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
            raise RuntimeError("没找到 MySQL")
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
        if self.admin_exe.is_file():
            run([self.admin_exe, "-u", "root", f"--port={self.port}", "--protocol=TCP", "shutdown"], timeout=30)
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
                f"停不掉 MySQL（pid {pid}）。多半是它当初以管理员身份启动的，"
                "普通权限杀不掉。"
            )
        self.pidfile.unlink(missing_ok=True)
        return True

    def shutdown_external(self, pid: int) -> bool:
        """关闭一个「不是喵盒启动的」mysqld —— 走 mysqladmin，绝不硬杀。"""
        if not self.admin_exe.is_file():
            return False
        run([self.admin_exe, "-u", "root", f"--port={self.port}",
             "--protocol=TCP", "shutdown"], cwd=self.dir, timeout=30)
        for _ in range(30):
            if not util.port_in_use(self.port):
                util.invalidate_listeners()
                return True
            time.sleep(0.3)
        return False

    def orphan(self):
        """3306 被 mysqld 占着，但不是喵盒启动的。"""
        if self.running():
            return None
        holder = util.port_holder(self.port)
        if holder and holder[1].lower().startswith("mysqld"):
            return {"port": self.port, "pid": holder[0], "name": holder[1]}
        return None

    def status(self) -> str:
        if self.running():
            return "运行中"
        if self.orphan():
            return "被外部占用"
        return "已停止"
