"""hosts 维护：喵盒只动自己那一块，用户手写的条目一个字都不能碰。

这段逻辑出错是**真实事故**（改坏系统 hosts → 用户断网 / 连不上内网系统），
所以这里把「可逆」「幂等」「成对标记」三条性质都钉死。
"""
from __future__ import annotations

from meow import hosts

USER_HOSTS = """127.0.0.1  localhost
::1        localhost
# 我自己写的一条，别动我
10.0.0.5   nas.lan
192.168.1.9  printer.lan  printer
"""


def test_writes_the_requested_hostnames():
    out = hosts.desired_text(USER_HOSTS, ["blog.meow", "wiki.meow"])
    assert "127.0.0.1  blog.meow" in out
    assert "127.0.0.1  wiki.meow" in out


def test_keeps_every_user_line():
    out = hosts.desired_text(USER_HOSTS, ["blog.meow"])
    for line in USER_HOSTS.strip().splitlines():
        assert line in out


def test_markers_are_paired_and_single():
    out = hosts.desired_text(USER_HOSTS, ["blog.meow"])
    assert out.count(hosts._OPEN) == 1
    assert out.count(hosts._CLOSE) == 1
    assert out.index(hosts._OPEN) < out.index(hosts._CLOSE)


def test_removing_the_block_restores_the_original():
    """可逆性：删掉喵盒那一块，剩的必须跟原文一模一样。"""
    out = hosts.desired_text(USER_HOSTS, ["blog.meow"])
    assert hosts.strip_block(out) == hosts.strip_block(USER_HOSTS)


def test_idempotent():
    once = hosts.desired_text(USER_HOSTS, ["blog.meow", "wiki.meow"])
    twice = hosts.desired_text(once, ["blog.meow", "wiki.meow"])
    assert once == twice


def test_changing_the_list_replaces_the_old_block():
    """删站点 / 换后缀之后，旧域名不能留在 hosts 里。"""
    out = hosts.desired_text(hosts.desired_text(USER_HOSTS, ["old.meow"]), ["new.meow"])
    assert "old.meow" not in out
    assert hosts.current_hostnames(out) == ["new.meow"]


def test_empty_list_leaves_only_the_markers():
    out = hosts.desired_text(USER_HOSTS, [])
    assert hosts.current_hostnames(out) == []
    assert hosts.strip_block(out) == hosts.strip_block(USER_HOSTS)


def test_current_hostnames_reads_what_we_wrote():
    out = hosts.desired_text(USER_HOSTS, ["a.meow", "b.meow"])
    assert hosts.current_hostnames(out) == ["a.meow", "b.meow"]
    # 用户自己那几行不算喵盒的
    assert "nas.lan" not in hosts.current_hostnames(out)


def test_custom_ip():
    out = hosts.desired_text("", ["a.meow"], ip="127.0.0.2")
    assert "127.0.0.2  a.meow" in out


def test_handles_empty_and_missing_file():
    assert hosts.read("/nonexistent/definitely/not/here") == ""
    out = hosts.desired_text("", ["a.meow"])
    assert hosts.current_hostnames(out) == ["a.meow"]
