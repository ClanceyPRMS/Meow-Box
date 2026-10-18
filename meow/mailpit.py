"""Mailpit 的管家：本地「假邮箱」——拦住站点发出去的邮件，在网页里看。

Mailpit 一个程序干两件事：

* **收**：当 SMTP 服务器（默认 1025），站点/脚本发给它的信全被截下来；
* **看**：自带网页 UI + JSON API（默认 8025），`http://localhost:8025` 就能翻。

它没有配置文件，全靠命令行参数；喵盒把参数从 `meowbox.config.json` 的
`mailpit` 段落拼出来，数据（SQLite 库）放在 `data/mailpit/` 里，
所以换机器 / 备份只要拖这一个文件。
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from . import config as cfgmod
from . import util
from .util import ensure_dir, fwd, kill_tree, popen, run


class Mailpit:
    name = "mailpit"

    def __init__(self, cfg: dict, bins: dict):
        self.cfg = cfg
        self.lay = cfgmod.layout(cfg)
        self.dir = Path(bins["mailpit"]) if bins.get("mailpit") else None

    # ---------- 路径 ----------
    @property
    def available(self) -> bool:
        return self.dir is not None and (self.dir / "mailpit.exe").is_file()

    @property
    def exe(self) -> Path:
        return self.dir / "mailpit.exe"

    @property
    def data_dir(self) -> Path:
        return self.lay["data"] / "mailpit"

    @property
    def db(self) -> Path:
        return self.data_dir / "mailpit.db"

    @property
    def pidfile(self) -> Path:
        return self.lay["run"] / "mailpit.pid"

    # ---------- 配置 ----------
    @property
    def conf(self) -> dict:
        return self.cfg.get("mailpit") or {}

    @property
    def smtp_port(self) -> int:
        return int(self.conf.get("smtp_port", 1025))

    @property
    def http_port(self) -> int:
        return int(self.conf.get("http_port", 8025))

    @property
    def max_messages(self) -> int:
        return int(self.conf.get("max_messages", 500))

    @property
    def ports(self) -> list[int]:
        return [self.smtp_port, self.http_port]

    @property
    def port(self) -> int:
        """给通用代码看的「主端口」= 网页 UI 那个。"""
        return self.http_port

    def ui_url(self) -> str:
        return f"http://127.0.0.1:{self.http_port}/"

    def smtp_url(self) -> str:
        return f"127.0.0.1:{self.smtp_port}"

    # ---------- 渲染（其实就是拼参数，没有配置文件） ----------
    def argv(self) -> list[str]:
        ensure_dir(self.data_dir)
        return [
            str(self.exe),
            "--smtp", f"127.0.0.1:{self.smtp_port}",
            "--listen", f"127.0.0.1:{self.http_port}",
            "--database", fwd(self.db),
            "--max", str(self.max_messages),
        ]

    def command_line(self) -> str:
        return " ".join(self.argv())

    # ---------- 进程 ----------
    def _pid(self):
        try:
            return int(self.pidfile.read_text().strip())
        except Exception:
            return None

    @staticmethod
    def _alive(pid) -> bool:
        if not pid:
            return False
        rc, out, _ = run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], timeout=20)
        return str(pid) in out

    def running(self) -> bool:
        return self._alive(self._pid())

    def orphan(self):
        """端口被 mailpit.exe 占着，但喵盒没记 pid（比如管理员权限启动的）。"""
        if self.running():
            return None
        for port in self.ports:
            holder = util.port_holder(port)
            if holder and holder[1].lower().startswith("mailpit"):
                return {"port": port, "pid": holder[0], "name": holder[1]}
        return None

    def start(self) -> int:
        if not self.available:
            raise RuntimeError("没找到 Mailpit，请先 python meowbox.py pkg install mailpit")
        if self.running():
            return self._pid()
        for port in self.ports:
            if util.port_in_use(port):
                holder = util.port_holder(port)
                desc = f"{holder[1]} (pid {holder[0]})" if holder else "未知程序"
                raise RuntimeError(
                    f"端口 {port} 已被占用：{desc}\n"
                    "Mailpit 要两个口（SMTP + 网页 UI），在 meowbox.config.json 的 "
                    "mailpit.smtp_port / http_port 里换个空着的。")

        ensure_dir(self.data_dir)
        self.pidfile.unlink(missing_ok=True)
        proc = popen(self.argv(), cwd=self.dir,
                     log_file=self.lay["logs"] / "mailpit-console.log")
        self.pidfile.write_text(str(proc.pid), encoding="utf-8")

        for _ in range(50):
            if util.port_in_use(self.http_port):
                util.invalidate_listeners()
                return proc.pid
            if not self._alive(proc.pid):
                break
            time.sleep(0.2)

        tail = self._tail_log()
        self.pidfile.unlink(missing_ok=True)
        raise RuntimeError("Mailpit 没能起来（日志末尾如下）：\n" + tail)

    def _tail_log(self, limit: int = 1200) -> str:
        path = self.lay["logs"] / "mailpit-console.log"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "（没有日志）"
        return text.strip()[-limit:] or "（日志是空的）"

    def stop(self) -> bool:
        pid = self._pid()
        if not pid:
            orphan = self.orphan()
            if orphan:
                # 不是喵盒启动的，普通权限多半也停不掉 —— 交给 freeport
                raise PermissionError(
                    f"Mailpit 在跑，但不是喵盒启动的（{orphan['name']} pid {orphan['pid']}）。\n"
                    f"用 python meowbox.py freeport {orphan['port']} 收回来。")
            self.pidfile.unlink(missing_ok=True)
            return False
        if not self._alive(pid):
            self.pidfile.unlink(missing_ok=True)
            return False

        kill_tree(pid)
        for _ in range(20):
            if not self._alive(pid):
                break
            time.sleep(0.2)
        if self._alive(pid):
            raise PermissionError(
                f"停不掉 Mailpit（pid {pid}）。它多半是以管理员身份启动的，"
                "普通权限杀不掉。")
        self.pidfile.unlink(missing_ok=True)
        return True

    def status(self) -> str:
        if self.running():
            return "运行中"
        if self.orphan():
            return "被外部占用"
        return "已停止"

    # ---------- 小功能 ----------
    def summary(self, timeout: float = 1.5):
        """问一句它的 API：一共几封信、几封没读。跑着才有结果，否则 None。"""
        if not self.running():
            return None
        url = f"http://127.0.0.1:{self.http_port}/api/v1/messages?limit=1"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception:                      # noqa: BLE001 —— 查不到就当没查
            return None
        return {
            "total": payload.get("total", payload.get("messages_count", 0)),
            "unread": payload.get("unread", 0),
        }

    def purge(self) -> list[str]:
        """清空信箱：删掉 SQLite 库（必须先停服务）。"""
        if self.running():
            raise RuntimeError("Mailpit 还在跑，先 python meowbox.py stop mailpit")
        removed = []
        for suffix in ("", "-wal", "-shm"):
            target = Path(str(self.db) + suffix)
            if target.is_file():
                target.unlink()
                removed.append(target.name)
        return removed
