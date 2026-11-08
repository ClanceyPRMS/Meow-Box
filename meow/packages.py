"""包仓库：一份「菜单」+ 下载解压。

和 Laragon 的 usr/packages.conf 同一个思路——一行一个 `名字=下载地址`，
喵盒照着下、解压到 bin/ 下面。

**Linux 上这份清单只有 Mailpit 有用**：Apache / Nginx / PHP / MariaDB 全走
发行版的包管理器（`pacman -S`），让喵盒再去下一份自己维护的二进制是给自己
找麻烦（glibc 版本、依赖库、安全更新都得自己跟）。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import download, plat
from .util import ensure_dir, fwd, run

DEFAULT_MENU_WINDOWS = """# 喵盒软件清单：名字=下载地址
# 一行一个；`---` 用来分组；# 开头是注释。
#
# ⚠️ 名字就是装到 bin/ 下面的目录名，喵盒靠它找程序 —— 别乱改：
#      apache  → bin/apache/<解压目录>/bin/httpd.exe
#      php     → bin/php/php.exe
#      mysql   → bin/mysql/<解压目录>/bin/mysqld.exe
#      nginx   → bin/nginx/nginx.exe
#      mailpit → bin/mailpit/mailpit.exe

# Web 服务器
apache=https://www.apachelounge.com/download/VS18/binaries/httpd-2.4.66-260223-Win64-VS18.zip
nginx=https://nginx.org/download/nginx-1.29.5.zip

---
# PHP（要 **线程安全(TS)** 的包：mod_php 只能挂 TS，php-cgi 用 TS 也没问题。
# 想装别的版本用： python meowbox.py php install 8.5
php=https://windows.php.net/downloads/releases/php-8.4.26-Win32-vs17-x64.zip

---
# 数据库
mysql=https://dev.mysql.com/get/Downloads/MySQL-8.4/mysql-8.4.6-winx64.zip

---
# 邮件测试（Mailpit）
mailpit=https://github.com/axllent/mailpit/releases/download/v1.22.3/mailpit-windows-amd64.zip

---
# Node.js（不是喵盒管的组件，纯下载用）
node-24.12=https://nodejs.org/dist/v24.12.0/node-v24.12.0-win-x64.zip
"""

DEFAULT_MENU_LINUX = """# 喵盒软件清单（Linux）
#
# Apache / Nginx / PHP / MariaDB 在 Linux 上**不从这里下** —— 直接用发行版的包：
#
#     sudo pacman -S apache nginx php php-cgi mariadb
#     # Debian/Ubuntu： sudo apt install apache2 nginx php php-cgi mariadb-server
#
# 为什么要这样：这些程序在系统里还得有匹配的依赖库和 glibc，自己下一份
# 二进制迟早会跟系统打架；而发行版的包管理器会给安全更新。喵盒只负责
# 「配置 + 进程看管 + hosts + HTTPS」，程序本身交给系统。
#
# 下面这份清单只剩「官方仓库里没有」的东西。

# 邮件测试（Mailpit：Arch 官方仓库没有，喵盒下静态二进制）
mailpit=https://github.com/axllent/mailpit/releases/download/v1.22.3/mailpit-linux-amd64.tar.gz
"""

#: 这些组件在 Linux 上应该用系统包管理器，不要从 packages.conf 下
SYSTEM_PACKAGES = {
    "apache": "apache",
    "nginx": "nginx",
    "php": "php php-cgi",
    "mysql": "mariadb",
}


def default_menu() -> str:
    return DEFAULT_MENU_WINDOWS if plat.IS_WINDOWS else DEFAULT_MENU_LINUX


def menu_path(cfg: dict) -> Path:
    return Path(cfg["root"]) / "packages.conf"


def ensure_menu(cfg: dict) -> Path:
    path = menu_path(cfg)
    if not path.is_file():
        path.write_text(default_menu(), encoding="utf-8", newline="\n")
    return path


def load_menu(cfg: dict) -> list[tuple[str, str]]:
    """返回 [(名字, 地址), ...]。"""
    path = ensure_menu(cfg)
    entries = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")) or line.startswith("---"):
            continue
        if "=" not in line:
            continue
        name, url = line.split("=", 1)
        entries.append((name.strip(), url.strip()))
    return entries


def install(cfg: dict, name: str) -> tuple[bool, str]:
    """下载并解压一个包到 bin/ 下。"""
    if not plat.IS_WINDOWS and name in SYSTEM_PACKAGES:
        return False, (
            f"Linux 上 {name} 用发行版的包管理器装，别从清单里下：\n"
            f"  sudo pacman -S {SYSTEM_PACKAGES[name]}\n"
            "（自己下一份二进制会和系统的依赖库打架，也拿不到安全更新）")
    entries = dict(load_menu(cfg))
    if name not in entries:
        return False, f"清单里没有叫 {name} 的包（用 pkg list 看看有什么）"
    url = entries[name]

    root = Path(cfg["root"])
    cache = ensure_dir(root / "cache")
    filename = url.split("/")[-1].split("?")[0] or "download.bin"
    target = cache / filename

    # 下载/解压都走标准库（见 meow/download.py）—— 冻结成 exe 以后
    # PATH 里没有 unzip，用子进程调外部命令会当场哑掉。
    try:
        download.fetch(url, target)
    except Exception as exc:                        # noqa: BLE001
        return False, f"下载失败：{exc}"

    dest = ensure_dir(root / "bin" / name)
    try:
        download.extract(target, dest)
    except Exception as exc:                        # noqa: BLE001
        return False, f"解压失败：{exc}"
    return True, f"已装到 {dest}"
