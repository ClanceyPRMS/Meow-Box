"""软件清单解析 + 下载辅助。

清单文件是用户能直接改的（`pkg list` / `pkg install`），所以它的解析规则
必须宽容但确定：注释、分组线、空行、地址里带查询串都不能出错。
"""
from __future__ import annotations

from meow import packages
from meow.download import human

SAMPLE = """# 喵盒软件清单：名字=下载地址
apache=https://example.com/httpd-2.4.66.zip

---
# PHP（要线程安全的包）
php=https://example.com/php-8.4.26.zip

; 分号也算注释
node=https://example.com/node.zip?v=24
没有等号的行应该被忽略
"""


def test_load_menu_parses_names_and_urls(tmp_path):
    (tmp_path / "packages.conf").write_text(SAMPLE, encoding="utf-8")
    entries = packages.load_menu({"root": str(tmp_path)})
    assert entries == [
        ("apache", "https://example.com/httpd-2.4.66.zip"),
        ("php", "https://example.com/php-8.4.26.zip"),
        ("node", "https://example.com/node.zip?v=24"),
    ]


def test_ensure_menu_writes_a_default_the_first_time(tmp_path):
    path = packages.ensure_menu({"root": str(tmp_path)})
    assert path.is_file()
    assert packages.load_menu({"root": str(tmp_path)}), "默认清单不该是空的"


def test_ensure_menu_does_not_overwrite_user_edits(tmp_path):
    path = tmp_path / "packages.conf"
    path.write_text("mine=https://example.com/mine.zip\n", encoding="utf-8")
    packages.ensure_menu({"root": str(tmp_path)})
    assert path.read_text(encoding="utf-8") == "mine=https://example.com/mine.zip\n"


def test_unknown_package_says_so(tmp_path):
    (tmp_path / "packages.conf").write_text(SAMPLE, encoding="utf-8")
    ok, message = packages.install({"root": str(tmp_path)}, "does-not-exist")
    assert ok is False
    assert "清单里没有" in message


def test_human_readable_sizes():
    assert human(0) == "0 B"
    assert human(1024) == "1.0 KB"
    assert human(2048) == "2.0 KB"
    assert human(5 * 1024 ** 2) == "5.0 MB"
    assert human(3 * 1024 ** 3) == "3.0 GB"
