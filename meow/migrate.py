"""从 Laragon 收编：站点 + 数据库。

两条完全不同的路子：

* **站点**就是文件 —— `copytree` 过去就行，冲突默认跳过（想覆盖得明说，
  而且先把旧的挪到 `_backup/`，不给你"覆盖完才后悔"的机会）。
* **数据库**不能直接拷 `data/mysql/` —— 里面全是 InnoDB 的脏页、binlog、
  系统库和权限表，版本差一点就起不来。正路是：
  ① 用 Laragon 的马达（mysqld）**临时**起一个只给迁移用的实例（默认 3307，
     不碰 Laragon 平时用的 3306）；
  ② `mysqldump` 把每个库导成 .sql；
  ③ 导进喵盒自己的 MySQL；
  ④ 关掉临时实例。

临时实例的日志写 `logs/migrate-mysqld.log`，导出的 .sql 放 `run/migrate/`，
迁移完可以删。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

from . import config as cfgmod
from . import services as svcmod
from . import util
from .util import CREATE_NO_WINDOW, ensure_dir, fwd, kill_tree, popen, run

#: MySQL 系统库，绝不迁移
SYSTEM_DBS = {"mysql", "performance_schema", "sys", "information_schema"}

#: 看起来像临时/测试库的名字（照样迁，只是提醒一句）
LOOKS_TEMP = re.compile(r"(test|demo|tmp|temp)", re.I)


# --------------------------------------------------------------------------
# 侦察
# --------------------------------------------------------------------------
def find_source(cfg: dict, explicit=None) -> dict:
    """找到 Laragon 的根目录 / 网站目录 / 数据目录 / 域名后缀。"""
    root = Path(explicit or cfg.get("provider") or "").resolve()
    if not root.is_dir():
        raise RuntimeError(
            "找不到 Laragon 目录：" + (str(root) if str(root) != "." else "（配置里没有 provider）") +
            "\n用 --from D:/laragon 指定一下。")
    www = root / "www"
    datadir = None
    data_root = root / "data"
    if data_root.is_dir():
        # data/mysql、data/mysql-8.4、data/mysql-8.0.x —— 认那个有 ibdata1 的
        for cand in sorted(data_root.glob("mysql*"), reverse=True):
            if (cand / "ibdata1").is_file():
                datadir = cand
                break
    return {
        "root": root,
        "www": www if www.is_dir() else None,
        "datadir": datadir,
        "tld": laragon_tld(root),
    }


def laragon_tld(root: Path) -> str | None:
    """从 etc/apache2/sites-enabled/auto.<域名>.conf 里猜出 Laragon 用的后缀。"""
    sites_dir = Path(root) / "etc" / "apache2" / "sites-enabled"
    if not sites_dir.is_dir():
        return None
    counts: dict[str, int] = {}
    for conf in sites_dir.glob("auto.*.conf"):
        host = conf.name[len("auto."):-len(".conf")]
        if "." in host:
            tld = host.rsplit(".", 1)[1]
            counts[tld] = counts.get(tld, 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def _decode_db_dir(name: str) -> str:
    """MySQL 用 @002d 这种转义来表示目录名里的特殊字符。"""
    return re.sub(r"@([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), name)


def db_entries(datadir: Path) -> list[tuple[str, Path]]:
    """返回 [(库名, 目录), ...]，跳过系统库和杂项。"""
    out: list[tuple[str, Path]] = []
    if not datadir or not datadir.is_dir():
        return out
    for entry in datadir.iterdir():
        if not entry.is_dir() or entry.name.startswith(("#", ".")):
            continue
        name = _decode_db_dir(entry.name)
        if name.lower() in SYSTEM_DBS:
            continue
        out.append((name, entry))
    return sorted(out, key=lambda item: item[0])


def db_names(datadir: Path) -> list[str]:
    return [name for name, _path in db_entries(datadir)]


def dir_size(path: Path) -> int:
    total = 0
    for item in Path(path).rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def plan(cfg: dict, src: dict) -> dict:
    """列出要搬什么、会撞什么，不动任何东西。"""
    lay = cfgmod.layout(cfg)
    sites, dbs = [], []

    if src["www"]:
        for entry in sorted(src["www"].iterdir()):
            if not entry.is_dir() or entry.name.startswith((".", "_")):
                continue
            target = lay["www"] / entry.name
            sites.append({
                "name": entry.name,
                "source": entry,
                "target": target,
                "size": dir_size(entry),
                "exists": target.exists(),
            })

    if src["datadir"]:
        for name, path in db_entries(Path(src["datadir"])):
            dbs.append({
                "name": name,
                "path": path,
                "size": dir_size(path),
                "looks_temp": bool(LOOKS_TEMP.search(name)),
            })

    return {"sites": sites, "dbs": dbs, "src": src,
            "free": shutil.disk_usage(str(lay["root"])).free}


# --------------------------------------------------------------------------
# 站点
# --------------------------------------------------------------------------
def migrate_sites(cfg: dict, pl: dict, overwrite: bool = False, say=print) -> list[dict]:
    lay = cfgmod.layout(cfg)
    backup_root = lay["root"] / "_backup"
    results = []
    for item in pl["sites"]:
        name, src, dst = item["name"], item["source"], item["target"]
        if dst.exists():
            if not overwrite:
                say(f"  ⏭  {name}：目标已存在，跳过（要覆盖加 --overwrite）")
                results.append({"name": name, "status": "skipped", "note": "目标已存在"})
                continue
            stamp = time.strftime("%Y%m%d-%H%M%S")
            keep = ensure_dir(backup_root) / f"{name}-{stamp}"
            shutil.move(str(dst), str(keep))
            say(f"  📦 {name}：旧的先挪到 {keep}")
        say(f"  ⬇  {name}：{human(item['size'])} ……")
        started = time.time()
        try:
            shutil.copytree(src, dst)
        except Exception as exc:                       # noqa: BLE001
            say(f"  ❌ {name}：复制失败 —— {exc}")
            results.append({"name": name, "status": "error", "note": str(exc)})
            continue
        say(f"  ✅ {name}：好了（{time.time() - started:.1f}s）→ {dst}")
        results.append({"name": name, "status": "copied", "note": str(dst)})
    return results


# --------------------------------------------------------------------------
# 数据库
# --------------------------------------------------------------------------
class TempMySQL:
    """用 Laragon 的 mysqld 临时起一个实例（只给迁移用，用完就关）。

    为什么要临时实例？因为直接读 `data/mysql-8.4` 的文件是不行的 ——
    InnoDB 得让 mysqld 自己把脏页/日志收拾好，mysqldump 才能看到完整数据。
    """

    def __init__(self, mysql_dir: Path, datadir: Path, port: int = 3307, logdir: Path | None = None):
        self.dir = Path(mysql_dir)
        self.datadir = Path(datadir)
        self.port = port
        self.logdir = Path(logdir or self.datadir.parent)

    @property
    def mysqld(self) -> Path:
        return self.dir / "bin" / "mysqld.exe"

    @property
    def admin(self) -> Path:
        return self.dir / "bin" / "mysqladmin.exe"

    def __enter__(self):
        if util.port_in_use(self.port):
            raise RuntimeError(f"端口 {self.port} 已被占用，没法起临时 MySQL。"
                               "先用 freeport 腾出来，或者换个 --port。")
        ensure_dir(self.logdir)
        pid_file = self.datadir.parent / "meowbox-migrate.pid"
        err_file = self.logdir / "migrate-mysqld.log"
        args = [
            self.mysqld, "--no-defaults",
            f"--datadir={fwd(self.datadir)}",
            f"--basedir={fwd(self.dir)}",
            f"--port={self.port}",
            "--bind-address=127.0.0.1",
            "--skip-log-bin",
            f"--pid-file={fwd(pid_file)}",
            f"--log-error={fwd(err_file)}",
        ]
        self.proc = popen(args, cwd=self.dir,
                          log_file=self.logdir / "migrate-mysqld-console.log")
        deadline = time.time() + 60
        while time.time() < deadline:
            if util.port_in_use(self.port):
                util.invalidate_listeners()
                # 端口通了不代表能登录，再 ping 一下
                rc, _out, _err = run([self.admin, "--no-defaults", "-h", "127.0.0.1",
                                      f"-P{self.port}", "-u", "root", "ping"], timeout=10)
                if rc == 0:
                    return self
            if self.proc.poll() is not None:
                break
            time.sleep(0.5)
        tail = ""
        try:
            tail = err_file.read_text(encoding="utf-8", errors="replace")[-1500:]
        except OSError:
            pass
        self._shutdown()
        raise RuntimeError("临时 MySQL 起不来（日志末尾）：\n" + (tail or "（没有日志）"))

    def _shutdown(self):
        try:
            run([self.admin, "--no-defaults", "-h", "127.0.0.1", f"-P{self.port}",
                 "-u", "root", "shutdown"], timeout=30)
        except Exception:                              # noqa: BLE001
            pass
        for _ in range(20):
            if not util.port_in_use(self.port):
                break
            time.sleep(0.3)
        if util.port_in_use(self.port) and getattr(self, "proc", None):
            kill_tree(self.proc.pid)

    def __exit__(self, *_exc):
        self._shutdown()
        return False


def _mysql_bins(bins: dict) -> tuple[Path, ...]:
    base = Path(bins["mysql"])
    return (base / "bin" / "mysqldump.exe", base / "bin" / "mysql.exe")


def _run_to_file(cmd: list[str], out_path: Path, timeout: int = 3600):
    """把命令的输出写进文件（dump 可能有几百 M，不能塞内存）。"""
    ensure_dir(out_path.parent)
    with open(out_path, "wb") as fh:
        proc = subprocess.Popen([str(c) for c in cmd], stdout=fh,
                                stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                creationflags=CREATE_NO_WINDOW)
        try:
            _out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            return 124, "命令超时"
        return proc.returncode, err.decode("utf-8", "replace")


def _run_from_file(cmd: list[str], in_path: Path, timeout: int = 3600):
    """把文件喂给命令的 stdin（恢复用）。"""
    with open(in_path, "rb") as fh:
        proc = subprocess.Popen([str(c) for c in cmd], stdin=fh,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=CREATE_NO_WINDOW)
        try:
            _out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            return 124, "命令超时"
        return proc.returncode, err.decode("utf-8", "replace")


def target_databases(mysql_exe: Path, port: int, user: str = "root") -> set[str]:
    rc, out, _err = run([mysql_exe, "--no-defaults", "-h", "127.0.0.1", f"-P{port}",
                         "-u", user, "-N", "-B", "-e", "SHOW DATABASES"], timeout=30)
    if rc != 0:
        return set()
    return {line.strip() for line in out.splitlines() if line.strip()}


def ensure_mysql_running(cfg: dict, bins: dict, say=print):
    """迁移之前喵盒的 MySQL 必须在跑（不然没地方恢复）。"""
    services = svcmod.build(cfg, bins)
    mysql = services.get("mysql")
    if mysql is None:
        raise RuntimeError("配置里把 MySQL 关掉了（mysql.enabled = false）")
    if not mysql.available:
        raise RuntimeError("没找到 MySQL 程序（检查 provider / bin）")
    if not mysql.initialized():
        raise RuntimeError("喵盒的 MySQL 还没初始化： python meowbox.py db init")
    started = False
    if not mysql.running():
        say("  ▶  喵盒的 MySQL 没在跑，先起来 ...")
        mysql.start()
        started = True
        for _ in range(30):
            if util.port_in_use(mysql.port):
                break
            time.sleep(0.3)
    return mysql, started


def migrate_databases(cfg: dict, bins: dict, pl: dict, only=None,
                      overwrite: bool = False, say=print) -> list[dict]:
    src = pl["src"]
    if not src["datadir"]:
        say("  （Laragon 没有 data/mysql* 目录，跳过数据库）")
        return []

    wanted = [d for d in pl["dbs"] if not only or d["name"] in only]
    if only:
        missing = set(only) - {d["name"] for d in pl["dbs"]}
        for name in sorted(missing):
            say(f"  ⚠️  Laragon 里没有 {name} 这个库，跳过")
    if not wanted:
        say("  （没有要迁的库）")
        return []

    mysql, started = ensure_mysql_running(cfg, bins, say=say)
    dump_dir = ensure_dir(cfgmod.layout(cfg)["run"] / "migrate")
    dump_exe, mysql_exe = _mysql_bins(bins)
    existing = target_databases(mysql_exe, mysql.port)

    # 不在 Laragon 的原件上动刀：先整份复制出来，临时实例跑在副本上。
    # 多花几十秒，换来的是"就算中途出事，Laragon 的数据一个字节都没变"。
    work_dir = dump_dir / "laragon-datadir"
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    say("  ⧉  复制 Laragon 的数据目录（不碰原件）...")
    shutil.copytree(Path(src["datadir"]), work_dir)
    say(f"     {human(dir_size(work_dir))} → {work_dir}")

    results: list[dict] = []
    try:
        with TempMySQL(Path(bins["mysql"]), work_dir,
                       logdir=cfgmod.layout(cfg)["logs"]) as temp:
            say(f"  ●  临时 MySQL 起来了（跑的是副本，端口 {temp.port}）")
            for item in wanted:
                name = item["name"]
                tag = "（看着像测试库）" if item["looks_temp"] else ""
                sql = dump_dir / f"{name}.sql"
                say(f"  ⬇  {name}{tag}：导出中 ...")
                started_at = time.time()
                rc, err = _run_to_file([
                    dump_exe, "--no-defaults", "-h", "127.0.0.1", f"-P{temp.port}",
                    "-u", "root", "--single-transaction", "--quick", "--routines",
                    "--events", "--triggers", "--hex-blob",
                    "--default-character-set=utf8mb4", "--databases", name,
                ], sql)
                if rc != 0:
                    say(f"  ❌ {name}：导出失败 —— {err.strip()[-400:]}")
                    results.append({"name": name, "status": "error", "note": err.strip()[-400:]})
                    continue
                size = sql.stat().st_size
                say(f"      {human(size)}，{time.time() - started_at:.1f}s → {sql.name}")

                if name in existing:
                    if not overwrite:
                        say(f"  ⏭  {name}：喵盒里已经有同名库，跳过（要覆盖加 --overwrite）")
                        results.append({"name": name, "status": "skipped", "note": "目标库已存在"})
                        continue
                    stamp = time.strftime("%Y%m%d_%H%M%S")
                    bak_sql = dump_dir / f"{name}-喵盒原库备份-{stamp}.sql"
                    say(f"  📦 {name}：喵盒里已有的先备份 ...")
                    rc_bak, err_bak = _run_to_file([
                        dump_exe, "--no-defaults", "-h", "127.0.0.1", f"-P{mysql.port}",
                        "-u", "root", "--single-transaction", "--routines", "--events",
                        "--triggers", "--hex-blob", "--default-character-set=utf8mb4",
                        "--databases", name,
                    ], bak_sql)
                    if rc_bak != 0:
                        say(f"  ❌ {name}：旧库备份失败，不敢覆盖 —— {err_bak.strip()[-300:]}")
                        results.append({"name": name, "status": "error", "note": "目标库备份失败"})
                        continue
                    rc_drop, _out, err_drop = run(
                        [mysql_exe, "--no-defaults", "-h", "127.0.0.1", f"-P{mysql.port}",
                         "-u", "root", "-e", f"DROP DATABASE `{name}`"], timeout=120)
                    if rc_drop != 0:
                        say(f"  ❌ {name}：删不掉旧库 —— {err_drop.strip()[-300:]}")
                        results.append({"name": name, "status": "error", "note": "删旧库失败"})
                        continue
                    say(f"     旧库已备份为 {bak_sql.name}，接着重新导入")

                say(f"  ⬆  {name}：导进喵盒 MySQL ...")
                rc, err = _run_from_file([
                    mysql_exe, "--no-defaults", "-h", "127.0.0.1", f"-P{mysql.port}",
                    "-u", "root", "--default-character-set=utf8mb4",
                ], sql)
                if rc != 0:
                    say(f"  ❌ {name}：导入失败 —— {err.strip()[-400:]}")
                    results.append({"name": name, "status": "error", "note": err.strip()[-400:]})
                    continue
                say(f"  ✅ {name}：导入完成（{time.time() - started_at:.1f}s）")
                results.append({"name": name, "status": "imported", "note": str(sql)})
    finally:
        # 副本用完就扔（原件在 Laragon 里躺着，没动过）
        shutil.rmtree(work_dir, ignore_errors=True)

    if started:
        say("  （喵盒的 MySQL 是我刚拉起来的，留着别关）")
    return results


# --------------------------------------------------------------------------
# 一站式
# --------------------------------------------------------------------------
def apply_tld(cfg: dict, tld: str, say=print) -> str:
    tld = tld.strip().lstrip(".").lower()
    if not tld or tld == cfg["sites"]["tld"]:
        return cfg["sites"]["tld"]
    cfg["sites"]["tld"] = tld
    cfgmod.save(cfg)
    say(f"  域名后缀改成 .{tld}（跟 Laragon 一致，站点里的写死 URL 不会失效）")
    return tld
