"""PHP 多版本：发现、下载、php.ini 生成，以及 php-cgi 进程池。

喵盒从 0.2.0 起支持**站点级 PHP 版本**，思路分两条：

* **Apache**：mod_php 只能挂一个版本，所以「默认版本」走 mod_php，
  其他版本走 `php-cgi.exe -b 127.0.0.1:端口` + `SetHandler proxy:fcgi://…`
  （mod_proxy_fcgi，Apache Lounge 的包里有）。
* **Nginx**：本来就不跑 PHP，所有站点都走 php-cgi，只是**每个版本一个端口**。

端口分配规则（关键是稳定）：默认版本永远拿 `php.fastcgi_port`（默认 9000），
其余版本按版本号升序拿 9001、9002…… 只要默认版本不变，端口就不会变。
"""
from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from . import config as cfgmod
from . import download, util
from .util import ensure_dir, fwd, kill_tree, popen, run

#: 官方 Windows 包的下载目录（PHP 8.x 起在 releases/ 下，老补丁在 archives/）
RELEASES_URL = "https://windows.php.net/downloads/releases/"
ARCHIVES_URL = "https://windows.php.net/downloads/releases/archives/"

#: 默认开启的扩展 —— 都是官方包里自带的 DLL，且 WordPress / Discuz 常用。
#: 这里只列「确定安全」的：imap / ldap / pgsql 这些依赖外部 DLL，不碰。
WANT_EXTENSIONS = (
    "bz2", "curl", "fileinfo", "gd", "gettext", "gmp", "intl", "mbstring",
    "exif", "mysqli", "openssl", "pdo_mysql", "pdo_sqlite", "sodium",
    "sockets", "sqlite3", "soap", "tidy", "xsl", "zip",
)

_VERSION_RE = re.compile(r"php-(\d+)\.(\d+)\.(\d+)-Win32-vs\d+-(x64|x86)\.zip$")
_DIR_VER_RE = re.compile(r"php[-_ ]?(\d+)\.(\d+)")


# --------------------------------------------------------------------------- 版本
@dataclass(frozen=True)
class PhpVersion:
    """一个装好的 PHP（bin/php 下的一个目录）。"""

    label: str          # 8.5
    version: str        # 8.5.11
    dir: Path

    @property
    def exe(self) -> Path:
        return self.dir / "php.exe"

    @property
    def cgi(self) -> Path:
        return self.dir / "php-cgi.exe"

    @property
    def ini(self) -> Path:
        return self.dir / "php.ini"

    @property
    def thread_safe(self) -> bool:
        """线程安全版才有 php8apache2_4.dll，才能给 mod_php 用。"""
        return any(self.dir.glob("php*apache2_4.dll"))

    @property
    def has_cgi(self) -> bool:
        return self.cgi.is_file()

    def __str__(self) -> str:
        return f"PHP {self.version}"


_PROBE_CACHE: dict[str, str] = {}


def _probe_version(exe: Path) -> str | None:
    """问 php.exe 自己是啥版本（带缓存：面板每 2.5 秒刷新，别每次都起子进程）。"""
    key = str(exe).lower()
    cached = _PROBE_CACHE.get(key)
    if cached:
        return cached
    rc, out, _ = run([exe, "-v"], cwd=exe.parent, timeout=20)
    if rc != 0:
        return None
    match = re.search(r"PHP\s+(\d+\.\d+\.\d+)", out)
    if match:
        _PROBE_CACHE[key] = match.group(1)
        return match.group(1)
    return None


def container(cfg: dict) -> Path:
    """所有 PHP 版本放哪：<bin>/php/。"""
    return cfgmod.binary_root(cfg) / "bin" / "php"


def scan(cfg: dict) -> dict[str, PhpVersion]:
    """扫出装好的 PHP，返回 {标签: PhpVersion}。标签是 major.minor。"""
    base = container(cfg)
    found: list[PhpVersion] = []
    if not base.is_dir():
        return {}

    dirs: list[Path] = []
    if (base / "php.exe").is_file():
        dirs.append(base)                      # 直接放在 bin/php 下的老布局
    dirs += [d for d in sorted(base.iterdir()) if d.is_dir() and (d / "php.exe").is_file()]

    for d in dirs:
        version = _probe_version(d / "php.exe")
        if not version:
            match = _DIR_VER_RE.search(d.name)
            if not match:
                continue
            version = f"{match.group(1)}.{match.group(2)}.0"
        label = ".".join(version.split(".")[:2])
        # 同一个 major.minor 装了两份（比如 8.5.9 和 8.5.11）—— 标签加后缀区分
        if any(v.label == label for v in found):
            label = version
        found.append(PhpVersion(label=label, version=version, dir=d))

    return {v.label: v for v in sorted(found, key=lambda v: _ver_key(v.version))}


def _ver_key(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", text)[:3]) or (0,)


def default_label(cfg: dict, versions: dict[str, PhpVersion] | None = None) -> str | None:
    """默认版本：配置里写的那个；没写就用装好的最高版本。"""
    versions = versions if versions is not None else scan(cfg)
    if not versions:
        return None
    want = (cfg.get("php") or {}).get("version")
    if want:
        want = str(want)
        if want in versions:
            return want
        for label, ver in versions.items():          # 允许写完整版本号
            if ver.version == want:
                return label
    return max(versions, key=lambda k: _ver_key(versions[k].version))


def port_map(cfg: dict, versions: dict[str, PhpVersion] | None = None) -> dict[str, int]:
    """版本 → FastCGI 端口。默认版本拿 fastcgi_port，其余顺延。"""
    versions = versions if versions is not None else scan(cfg)
    base = int((cfg.get("php") or {}).get("fastcgi_port", 9000))
    default = default_label(cfg, versions)
    ports: dict[str, int] = {}
    if default:
        ports[default] = base
    rest = [l for l in versions if l != default]
    for i, label in enumerate(sorted(rest, key=lambda k: _ver_key(versions[k].version)), start=1):
        ports[label] = base + i
    return ports


# --------------------------------------------------------------------------- 站点绑定
def site_label(cfg: dict, name: str, versions: dict[str, PhpVersion] | None = None) -> str | None:
    """这个站点用哪个版本；没单独指定就跟着默认版本。"""
    versions = versions if versions is not None else scan(cfg)
    if not versions:
        return None
    choice = ((cfg.get("sites") or {}).get("php") or {}).get(name)
    if choice:
        choice = str(choice)
        if choice in versions:
            return choice
        for label, ver in versions.items():
            if ver.version == choice:
                return label
    return default_label(cfg, versions)


def set_site_version(cfg: dict, name: str, label: str | None) -> None:
    php_map = dict((cfg.get("sites") or {}).get("php") or {})
    if label in (None, "", "default"):
        php_map.pop(name, None)
    else:
        php_map[name] = str(label)
    cfg.setdefault("sites", {})["php"] = php_map


def prunes(cfg: dict, names: list[str]) -> bool:
    """把已经删掉的站点从 sites.php 里清掉。返回是否改动过。"""
    php_map = dict((cfg.get("sites") or {}).get("php") or {})
    kept = {k: v for k, v in php_map.items() if k in names}
    if kept != php_map:
        cfg["sites"]["php"] = kept
        return True
    return False


# --------------------------------------------------------------------------- php-cgi 池
class CgiPool:
    """每个版本一个 php-cgi 进程，互相不干扰。"""

    def __init__(self, cfg: dict, versions: dict[str, PhpVersion] | None = None):
        self.cfg = cfg
        self.lay = cfgmod.layout(cfg)
        self.versions = versions if versions is not None else scan(cfg)
        self.ports = port_map(cfg, self.versions)

    # -- 需要哪些版本 --
    def labels_for(self, names: list[str], server: str) -> list[str]:
        """站点真正要用 FastCGI 的版本列表。

        Nginx 没有 mod_php，所有站点都要 php-cgi；Apache 只有「不是默认版本」
        的站点才需要（默认版本由 mod_php 直接跑）。
        """
        if not self.versions:
            return []
        default = default_label(self.cfg, self.versions)
        labels: list[str] = []
        for name in names:
            label = site_label(self.cfg, name, self.versions)
            if not label:
                continue
            if server == "apache" and label == default:
                continue
            labels.append(label)
        if server != "apache":
            fallback = default or (list(self.versions)[0] if self.versions else None)
            if fallback:
                labels.append(fallback)        # 默认站点（localhost）也要有 PHP
        return list(dict.fromkeys(labels))

    # -- 进程 --
    def pid_file(self, label: str) -> Path:
        return self.lay["run"] / f"php-cgi-{label}.pid"

    def log_file(self, label: str) -> Path:
        return self.lay["logs"] / f"php-cgi-{label}.log"

    def _pid(self, label: str) -> int | None:
        try:
            return int(self.pid_file(label).read_text().strip())
        except Exception:                          # noqa: BLE001
            return None

    @staticmethod
    def _alive(pid) -> bool:
        return util.pid_is(pid, ("php-cgi",))

    def running(self, label: str) -> bool:
        return self._alive(self._pid(label))

    def port(self, label: str) -> int:
        return self.ports[label]

    def start(self, labels: list[str]) -> dict[str, int]:
        started: dict[str, int] = {}
        for label in labels:
            ver = self.versions.get(label)
            if ver is None:
                continue
            started[label] = self._start_one(ver)
        return started

    def _start_one(self, ver: PhpVersion) -> int:
        port = self.port(ver.label)
        if self.running(ver.label):
            return self._pid(ver.label)
        if util.port_in_use(port):
            holder = util.port_holder(port)
            if holder and holder[1].lower().startswith("php-cgi"):
                self.pid_file(ver.label).write_text(str(holder[0]), encoding="utf-8")
                return holder[0]
            desc = f"{holder[1]} (pid {holder[0]})" if holder else "未知程序"
            raise RuntimeError(
                f"FastCGI 端口 {port} 被 {desc} 占着，PHP {ver.label} 起不来。\n"
                f"关掉它，或者改 meowbox.config.json 里的 php.fastcgi_port。")
        if not ver.cgi.is_file():
            raise RuntimeError(f"PHP {ver.version} 里没有 php-cgi.exe（{fwd(ver.dir)}）")

        cmd = [ver.cgi, "-b", f"127.0.0.1:{port}"]
        if ver.ini.is_file():
            cmd += ["-c", fwd(ver.ini)]
        proc = popen(cmd, cwd=ver.dir, log_file=self.log_file(ver.label))
        self.pid_file(ver.label).write_text(str(proc.pid), encoding="utf-8")
        for _ in range(40):
            if util.port_in_use(port):
                util.invalidate_listeners()
                return proc.pid
            time.sleep(0.2)
        tail = ""
        try:
            tail = self.log_file(ver.label).read_text(encoding="utf-8", errors="replace")[-800:]
        except OSError:
            pass
        self.stop_label(ver.label)
        raise RuntimeError(f"PHP {ver.label} 的 php-cgi 没能监听 {port}。\n{tail}")

    def stop_label(self, label: str) -> bool:
        pid = self._pid(label)
        if pid and self._alive(pid):
            kill_tree(pid)
            for _ in range(10):
                if not self._alive(pid):
                    break
                time.sleep(0.2)
        self.pid_file(label).unlink(missing_ok=True)
        return bool(pid)

    def stop_all(self) -> list[str]:
        stopped = []
        for label in self.versions:
            if self.stop_label(label):
                stopped.append(label)
        return stopped

    def status(self) -> dict[str, str]:
        return {l: ("运行中" if self.running(l) else "已停止") for l in self.versions}


def needed_labels(cfg: dict, names: list[str], server: str) -> list[str]:
    return CgiPool(cfg).labels_for(names, server)


# --------------------------------------------------------------------------- 下载安装
def _index(url: str) -> list[tuple[tuple[int, int, int], str, str]]:
    """解析官方目录列表：[(版本元组, 文件名, 完整地址)]，只要 x64 的 TS 版。"""
    try:
        html = download.fetch_text(url)
    except Exception:                              # noqa: BLE001
        return []
    out = []
    for href in re.findall(r'href="([^"]+\.zip)"', html):
        filename = href.rsplit("/", 1)[-1]
        match = _VERSION_RE.search(filename)
        if not match or match.group(4) != "x64":
            continue
        if "-nts-" in filename:                     # mod_php 只能挂线程安全版
            continue
        ver = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        base = url if url.endswith("/") else url + "/"
        out.append((ver, filename, base + filename))
    return sorted(out, key=lambda item: item[0])


def resolve_remote(want: str = "") -> tuple[str, str, str]:
    """找官方下载地址。want 可以是空（最新）、8.5、8.5.11。"""
    want = re.sub(r"^[^0-9]*", "", (want or "").strip()).lower()
    candidates = _index(RELEASES_URL)
    if not candidates:
        raise RuntimeError("连不上 windows.php.net（检查网络/代理）")

    def match(ver) -> bool:
        if not want:
            return True
        parts = [int(p) for p in re.findall(r"\d+", want)]
        return list(ver)[:len(parts)] == parts

    pool = [c for c in candidates if match(c[0])]
    if not pool and len(re.findall(r"\d+", want)) == 3:
        pool = [c for c in _index(ARCHIVES_URL) if match(c[0])]
    if not pool:
        raise RuntimeError(f"官方源里没有 PHP {want or '（最新）'} 的 Windows x64 包")
    ver, filename, url = max(pool, key=lambda c: c[0])
    return ".".join(str(p) for p in ver), filename, url


def install(cfg: dict, want: str = "", say=print) -> PhpVersion:
    """从 php.net 下载一个 Windows x64 TS 版，解压进 bin/php 并配好 php.ini。"""
    version, filename, url = resolve_remote(want)
    say(f"准备安装 PHP {version}（{filename}）")

    cache = ensure_dir(Path(cfg["root"]) / "cache" / "php")
    archive = download.fetch(url, cache / filename, say=say)

    folder_name = filename[:-4]                     # php-8.5.11-Win32-vs17-x64
    target = container(cfg) / folder_name
    say(f"解压 → {fwd(target)}")
    download.rmtree(target)
    ensure_dir(target)
    download.extract(archive, target, say=say)

    if not (target / "php.exe").is_file():
        raise RuntimeError(f"解压后没找到 php.exe：{fwd(target)}")

    write_ini(cfg, target, say=say)

    found = scan(cfg)
    label = next((l for l, v in found.items() if v.dir == target), None)
    if label is None:
        raise RuntimeError("装完了但喵盒没扫到这个版本")
    ver = found[label]

    rc, out, err = run([ver.exe, "-v"], cwd=ver.dir, timeout=30)
    if rc != 0:
        raise RuntimeError(f"php.exe -v 跑不起来：\n{(out + err).strip()[:600]}")
    say(f"  ✅ {out.strip().splitlines()[0]}")
    return ver


def write_ini(cfg: dict, php_dir: Path, say=print) -> Path:
    """给新装的 PHP 生成 php.ini（基于官方 production 模板 + 喵盒的路径）。"""
    php_dir = Path(php_dir)
    lay = cfgmod.layout(cfg)
    source = None
    for name in ("php.ini-production", "php.ini-development"):
        if (php_dir / name).is_file():
            source = php_dir / name
            break
    text = source.read_text(encoding="utf-8", errors="replace") if source else ""

    def set_key(key: str, value: str) -> None:
        nonlocal text
        pattern = rf"^(?![;#])\s*{key}\s*=.*$"
        line = f"{key} = {value}"
        if re.search(pattern, text, flags=re.M):
            text = re.sub(pattern, line, text, count=1, flags=re.M)
        else:
            text += f"\n{line}\n"

    ext_dir = php_dir / "ext"
    session_dir = ensure_dir(lay["run"] / "php-session")
    ensure_dir(lay["logs"])
    ensure_dir(lay["etc"] / "php" / "pear")

    set_key("extension_dir", f'"{fwd(ext_dir)}"')
    set_key("error_log", f'"{fwd(lay["logs"] / "php-errors.log")}"')
    set_key("session.save_path", f'"{fwd(session_dir)}"')
    set_key("include_path", f'".;{fwd(lay["etc"] / "php" / "pear")}"')
    set_key("memory_limit", "256M")
    set_key("upload_max_filesize", "64M")
    set_key("post_max_size", "64M")
    set_key("max_execution_time", "300")
    set_key("max_input_vars", "5000")
    set_key("max_input_time", "300")
    set_key("cgi.fix_pathinfo", "1")
    set_key("display_errors", "On")
    set_key("display_startup_errors", "On")
    set_key("log_errors", "On")
    set_key("error_reporting", "E_ALL & ~E_DEPRECATED & ~E_STRICT")

    mail = cfg.get("mailpit") or {}
    mailpit_exe = cfgmod.binary_root(cfg) / "bin" / "mailpit"
    if mail.get("enabled", True) and mailpit_exe.is_dir():
        real = next(iter(sorted(mailpit_exe.glob("mailpit.exe"))), None) or \
            next(iter(sorted(mailpit_exe.glob("*/mailpit.exe"))), None)
        if real:
            port = int(mail.get("smtp_port", 1025))
            set_key("sendmail_path", f'"{fwd(real)} sendmail --smtp-addr 127.0.0.1:{port}"')

    enabled = []
    for name in WANT_EXTENSIONS:
        if (ext_dir / f"php_{name}.dll").is_file():
            enabled.append(name)
    text += "\n; ---------- 喵盒：默认开启的扩展 ----------\n"
    for name in enabled:
        text += f"extension={name}\n"
    if (ext_dir / "php_opcache.dll").is_file():
        text += "\nzend_extension=opcache\nopcache.enable=1\nopcache.memory_consumption=128\n"

    ini = php_dir / "php.ini"
    ini.write_text(text, encoding="utf-8", newline="\n")
    say(f"  php.ini 已生成（{len(enabled)} 个扩展）")
    return ini


# --------------------------------------------------------------------------- phpMyAdmin
PHPMYADMIN_INDEX = "https://www.phpmyadmin.net/downloads/"
PHPMYADMIN_RE = re.compile(
    r"https://files\.phpmyadmin\.net/phpMyAdmin/(\d+\.\d+\.\d+)/"
    r"phpMyAdmin-(\d+\.\d+\.\d+)-all-languages\.zip")


def phpmyadmin_dir(cfg: dict) -> Path:
    return cfgmod.layout(cfg)["www"] / "phpmyadmin"


def phpmyadmin_ready(cfg: dict) -> bool:
    return (phpmyadmin_dir(cfg) / "index.php").is_file()


def install_phpmyadmin(cfg: dict, say=print) -> Path:
    """装一份 phpMyAdmin 到 www/phpmyadmin（root 免密，仅本机用）。"""
    html = download.fetch_text(PHPMYADMIN_INDEX)
    hits = PHPMYADMIN_RE.findall(html)
    if not hits:
        url = ("https://files.phpmyadmin.net/phpMyAdmin/5.2.2/"
               "phpMyAdmin-5.2.2-all-languages.zip")
        version = "5.2.2"
    else:
        version, _ = max(hits, key=lambda h: _ver_key(h[0]))
        url = (f"https://files.phpmyadmin.net/phpMyAdmin/{version}/"
               f"phpMyAdmin-{version}-all-languages.zip")
    say(f"准备安装 phpMyAdmin {version}")

    cache = ensure_dir(Path(cfg["root"]) / "cache")
    archive = download.fetch(url, cache / url.rsplit("/", 1)[-1], say=say)
    tmp = ensure_dir(Path(cfg["root"]) / "run" / "phpmyadmin-tmp")
    download.rmtree(tmp)
    ensure_dir(tmp)
    download.extract(archive, tmp, say=say)

    inner = [d for d in tmp.iterdir() if d.is_dir()]
    source = inner[0] if len(inner) == 1 else tmp
    target = phpmyadmin_dir(cfg)
    download.rmtree(target)
    ensure_dir(target.parent)
    download.move_tree(source, target, say=say)
    download.rmtree(tmp)

    config = target / "config.inc.php"
    config.write_text(
        "<?php\n"
        "// 喵盒自动生成：本地开发用，免登录（只看本机 127.0.0.1）\n"
        "$cfg['blowfish_secret'] = '" + secrets.token_urlsafe(24)[:32] + "';\n"
        "$i = 0;\n"
        "$i++;\n"
        "$cfg['Servers'][$i]['auth_type'] = 'config';\n"
        "$cfg['Servers'][$i]['host'] = '127.0.0.1';\n"
        "$cfg['Servers'][$i]['port'] = '" + str((cfg.get('mysql') or {}).get('port', 3306)) + "';\n"
        "$cfg['Servers'][$i]['user'] = 'root';\n"
        "$cfg['Servers'][$i]['password'] = '';\n"
        "$cfg['Servers'][$i]['AllowNoPassword'] = true;\n"
        "$cfg['UploadDir'] = '';\n"
        "$cfg['SaveDir'] = '';\n",
        encoding="utf-8", newline="\n")
    say(f"  ✅ phpMyAdmin 装好了：{fwd(target)}")
    return target
