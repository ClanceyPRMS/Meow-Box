"""配置读写 + 二进制目录自动探测。

**便携是硬要求**：喵盒不装服务、不写注册表、不往系统目录里丢东西 ——
整个程序就是 `D:\\meowbox` 这一个文件夹，搬到哪个盘都能跑。为此：

* `root` 在配置文件里永远是 `"."`，真正的根目录 = **配置文件所在的那个目录**
  （`load()` 里现算），所以文件夹挪走、换机器都不用改配置；
* `sites.www`、`provider` 这些也尽量存相对路径，`save()` 会自动把
  根目录内的绝对路径改写成相对路径；
* 用到路径时才展开（`resolve()`），配置里存的是"相对于喵盒"的说法。
"""
from __future__ import annotations

import json
from pathlib import Path

from .util import ensure_dir, fwd

CONFIG_NAME = "meowbox.config.json"

#: 在 bin/ 下面按"一个容器目录里套一个版本目录"的惯例找二进制
BIN_LAYOUT = {
    # Apache Lounge 的布局：版本目录下还有一层 bin/ 放 httpd.exe
    "apache": ("bin/apache", "bin/httpd.exe"),
    "php": ("bin/php", "php.exe"),
    "mysql": ("bin/mysql", "bin/mysqld.exe"),
    "nginx": ("bin/nginx", "nginx.exe"),
    "mailpit": ("bin/mailpit", "mailpit.exe"),
}


def config_path(root: Path) -> Path:
    return Path(root) / CONFIG_NAME


def resolve(root, value) -> Path:
    """把配置里的路径说法变成真实路径：相对的就相对喵盒根目录。"""
    path = Path(str(value))
    return path if path.is_absolute() else (Path(root) / path)


def _templates_dir(root) -> Path:
    """配置模板在哪：根目录里有就用（用户改过的），没有就用包里那份。"""
    from . import runtime
    return runtime.templates_dir(root)


def _relative_to_root(path, root) -> str | None:
    try:
        return fwd(Path(path).resolve().relative_to(Path(root).resolve()))
    except (ValueError, OSError):
        return None


def default_config(root: Path, provider: Path | None = None) -> dict:
    return {
        # 永远写 "." —— 意思是"就是配置文件所在的这个文件夹"
        "root": ".",
        "provider": fwd(provider) if provider else None,
        # web.server 决定喵盒看管哪一个 Web 服务器：apache / nginx（同时只跑一个）
        "web": {"server": "apache", "http_port": 80, "https_port": 443, "ssl": False},
        # nginx 自己不跑 PHP，要靠 php-cgi 的 FastCGI 端口
        # version = 默认 PHP 版本（8.5 这种标签）；站点可以在 sites.php 里单独指定
        # cgi_children = 每个版本起几个 php-cgi worker（1 个的话一个慢请求会堵死整池）
        "php": {"fastcgi_port": 9000, "version": None, "cgi_children": 8},
        # 本地假邮箱：SMTP 收信 + 网页看信
        "mailpit": {"enabled": True, "smtp_port": 1025, "http_port": 8025, "max_messages": 500},
        "mysql": {"enabled": True, "port": 3306},
        "sites": {"tld": "meow", "www": "www"},
        "autostart": ["apache", "mysql"],
        "openssl": None,
    }


def normalize(cfg: dict) -> dict:
    """把老配置文件缺的键补上（改结构时别把人家的配置弄坏）。"""
    cfg.setdefault("web", {})
    cfg["web"].setdefault("server", "apache")
    cfg["web"].setdefault("http_port", 80)
    cfg["web"].setdefault("https_port", 443)
    cfg["web"].setdefault("ssl", False)
    cfg.setdefault("php", {})
    cfg["php"].setdefault("fastcgi_port", 9000)
    cfg["php"].setdefault("version", None)
    cfg["php"].setdefault("cgi_children", 8)
    cfg.setdefault("mailpit", {})
    cfg["mailpit"].setdefault("enabled", True)
    cfg["mailpit"].setdefault("smtp_port", 1025)
    cfg["mailpit"].setdefault("http_port", 8025)
    cfg["mailpit"].setdefault("max_messages", 500)
    cfg.setdefault("sites", {})
    cfg["sites"].setdefault("tld", "meow")
    cfg["sites"].setdefault("www", "www")
    cfg["sites"].setdefault("php", {})          # 站点名 → PHP 版本标签（8.5）
    cfg.setdefault("mysql", {"enabled": True, "port": 3306})
    return cfg


def exists(root: Path) -> bool:
    return config_path(root).is_file()


def load(root: Path) -> dict:
    root = Path(root).resolve()
    path = config_path(root)
    if not path.is_file():
        raise FileNotFoundError(
            f"还没初始化喵盒：找不到 {path}\n先运行  python meowbox.py init"
        )
    with open(path, "r", encoding="utf-8") as fh:
        cfg = normalize(json.load(fh))
    # 根目录不认配置文件里写的，一律以"配置躺在哪个目录"为准 —— 这就是便携
    cfg["root"] = fwd(root)
    return cfg


def save(cfg: dict) -> Path:
    """存配置：根目录内的路径自动改写成相对路径（这样搬走才不会坏）。"""
    root = Path(cfg["root"])
    path = config_path(root)
    data = json.loads(json.dumps(cfg, ensure_ascii=False))

    data["root"] = "."
    sites = dict(data.get("sites") or {})
    sites["www"] = _relative_to_root(cfg["sites"]["www"], root) or fwd(cfg["sites"]["www"])
    data["sites"] = sites

    provider = cfg.get("provider")
    if provider:
        data["provider"] = _relative_to_root(provider, root) or fwd(provider)

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path


def raw_config(root: Path) -> dict:
    """原封不动读配置文件（便携检查要看它存的到底是相对还是绝对）。"""
    with open(config_path(root), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _find_version_dir(base: Path, exe_rel: str) -> Path | None:
    """在 bin/apache/ 这种容器目录里找出真正含 exe 的那一层。"""
    if not base.is_dir():
        return None
    candidates = [base] + sorted([d for d in base.iterdir() if d.is_dir()], reverse=True)
    for cand in candidates:
        if (cand / exe_rel).is_file():
            return cand
    return None


def binary_root(cfg: dict) -> Path:
    """去哪找 bin/：优先 provider，其次喵盒自己的根目录。"""
    if cfg.get("provider"):
        return resolve(cfg["root"], cfg["provider"])
    return Path(cfg["root"])


def discover(cfg: dict) -> dict:
    """探测每个组件的安装目录（绝对路径），没找到的返回 None。"""
    base = binary_root(cfg)
    found: dict[str, Path | None] = {}
    for name, (sub, exe) in BIN_LAYOUT.items():
        found[name] = _find_version_dir(base / sub, exe)
    # PHP 可能装了好几个版本：别按目录名倒序随便挑一个，
    # 要跟「默认版本」保持一致，否则面板显示的路径和实际挂载的不是同一个。
    found["php"] = _default_php_dir(cfg, found.get("php"))
    return found


def _default_php_dir(cfg: dict, fallback):
    try:
        from . import php as phpmod
        versions = phpmod.scan(cfg)
        label = phpmod.default_label(cfg, versions)
        if label:
            return versions[label].dir
    except Exception:                              # noqa: BLE001 —— 探测失败就用旧的
        pass
    return fallback


def layout(cfg: dict) -> dict:
    """喵盒自己的目录结构（全部在根目录底下）。"""
    root = Path(cfg["root"])
    return {
        "root": root,
        "www": resolve(root, cfg["sites"]["www"]),
        "etc": root / "etc",
        "etc_apache": root / "etc" / "apache2",
        "sites_enabled": root / "etc" / "apache2" / "sites-enabled",
        "alias_dir": root / "etc" / "apache2" / "alias",
        "etc_nginx": root / "etc" / "nginx",
        "nginx_sites": root / "etc" / "nginx" / "sites-enabled",
        "logs": root / "logs",
        "data": root / "data",
        "run": root / "run",
        # 模板优先用根目录那份（用户可改）；没有就退回包里那份（打包版）
        "templates": _templates_dir(root),
        "ssl": root / "etc" / "ssl",
        "ssl_sites": root / "etc" / "ssl" / "sites",
    }


def bootstrap_dirs(cfg: dict) -> dict:
    """把目录结构一次建好。"""
    lay = layout(cfg)
    for key in ("www", "etc_apache", "sites_enabled", "alias_dir", "etc_nginx",
                "nginx_sites", "logs", "data", "run", "ssl", "ssl_sites"):
        ensure_dir(lay[key])
    return lay
