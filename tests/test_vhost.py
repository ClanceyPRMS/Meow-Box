"""虚拟主机渲染：两个平台「看起来该一样」的地方，恰恰相反（这是踩坑重灾区）。"""
from __future__ import annotations

import pytest

from meow import plat, vhost


@pytest.mark.parametrize(
    "is_windows,expected",
    [
        (True, "proxy:fcgi://127.0.0.1:9000/"),    # Windows 少了斜杠 → DNS lookup failure
        (False, "proxy:fcgi://127.0.0.1:9000"),    # Linux 多了斜杠 → No input file specified
    ],
)
def test_fcgi_target_trailing_slash_is_platform_specific(monkeypatch, is_windows, expected):
    monkeypatch.setattr(plat, "IS_WINDOWS", is_windows)
    assert vhost.fcgi_target(9000) == expected


def test_php_block_empty_for_mod_php():
    """端口为 None = 该站走 mod_php，不需要 FastCGI 段。"""
    assert vhost.php_block(None) == ""


def test_php_block_always_pins_script_filename(monkeypatch):
    """两个平台都必须显式拼 SCRIPT_FILENAME —— 默认值都不可靠。"""
    for is_windows in (True, False):
        monkeypatch.setattr(plat, "IS_WINDOWS", is_windows)
        block = vhost.php_block(9001, "8.5", "8.3")
        assert "ProxyFCGISetEnvIf" in block
        assert "%{reqenv:DOCUMENT_ROOT}%{reqenv:SCRIPT_NAME}" in block
        assert "SetHandler" in block


def test_hostname_for():
    assert vhost.hostname_for("blog", "meow") == "blog.meow"
    assert vhost.hostname_for("blog", "") == "blog"


def test_site_url_needs_a_cert_to_go_https(tmp_path):
    ssl_dir = tmp_path / "ssl"
    ssl_dir.mkdir()
    lay = {"ssl_sites": str(ssl_dir)}
    cfg = {"sites": {"tld": "meow"}, "web": {"ssl": False}}

    assert vhost.site_url(cfg, lay, "blog") == "http://blog.meow"

    cfg["web"]["ssl"] = True                    # 开了 HTTPS 但还没签证书
    assert vhost.site_url(cfg, lay, "blog") == "http://blog.meow"

    (ssl_dir / "blog.meow.crt").write_text("x", encoding="utf-8")
    (ssl_dir / "blog.meow.key").write_text("x", encoding="utf-8")
    assert vhost.site_url(cfg, lay, "blog") == "https://blog.meow"


def test_scan_ignores_hidden_and_private_dirs(tmp_path):
    for name in ("blog", "_backup", ".git"):
        (tmp_path / name).mkdir()
    (tmp_path / "index.php").write_text("", encoding="utf-8")
    assert vhost.scan(tmp_path) == ["blog"]


def test_scan_of_missing_dir_is_empty(tmp_path):
    assert vhost.scan(tmp_path / "nope") == []


def test_render_apache_falls_back_when_templates_are_missing(tmp_path):
    text = vhost.render_apache(
        tmp_path, "blog", "meow", 80, tmp_path / "logs", tmp_path / "no-templates"
    )
    assert "ServerName blog.meow" in text
    assert "DocumentRoot" in text
    # 占位符残留是「-t 通过、真启动才炸」那类 bug，必须为零
    assert "@" not in text


def test_render_apache_uses_user_template_when_present(tmp_path):
    tpl_dir = tmp_path / "tpl"
    tpl_dir.mkdir()
    (tpl_dir / "VirtualHost.tpl").write_text(
        "user-template @hostname@ @port@\n", encoding="utf-8"
    )
    text = vhost.render_apache(tmp_path, "blog", "meow", 81, tmp_path / "logs", tpl_dir)
    assert text == "user-template blog.meow 81\n"


def test_render_nginx_falls_back(tmp_path):
    text = vhost.render_nginx(
        tmp_path, "blog", "meow", 80, tmp_path / "logs", tmp_path / "no-templates"
    )
    assert "server_name blog.meow" in text
    assert "@" not in text
