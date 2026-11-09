"""配置读写：便携性全靠「根目录从配置文件位置现算 + 存相对路径」这两条。"""
from __future__ import annotations

import json
from pathlib import Path

from meow import config


def _cfg(root: Path) -> dict:
    cfg = config.default_config(root)
    cfg["root"] = str(root)
    cfg["sites"]["www"] = str(root / "www")
    return cfg


def test_save_writes_root_as_dot(tmp_path):
    data = json.loads(config.save(_cfg(tmp_path)).read_text(encoding="utf-8"))
    assert data["root"] == "."
    assert data["sites"]["www"] == "www"


def test_inside_paths_become_relative(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["provider"] = str(tmp_path / "bin")
    data = json.loads(config.save(cfg).read_text(encoding="utf-8"))
    assert data["provider"] == "bin"


def test_outside_paths_stay_absolute(tmp_path):
    """Laragon 那种「盘外」的路径只能存绝对路径，相对化就没意义了。"""
    cfg = _cfg(tmp_path)
    outside = tmp_path.parent / "laragon"
    cfg["provider"] = str(outside)
    data = json.loads(config.save(cfg).read_text(encoding="utf-8"))
    assert Path(data["provider"]).is_absolute()


def test_load_ignores_root_written_in_the_file(tmp_path):
    """配置文件里写的 root 不作数 —— 一切以「文件躺在哪」为准（这就是便携）。"""
    cfg = _cfg(tmp_path)
    path = config.save(cfg)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["root"] = str(tmp_path.parent / "somewhere-else")
    path.write_text(json.dumps(data), encoding="utf-8")
    assert Path(config.load(tmp_path)["root"]) == Path(tmp_path).resolve()


def test_roundtrip_moves_with_the_folder(tmp_path):
    """存档 → 改 root → 再读：两份配置读出来的真实路径都指向各自的根。"""
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    config.save(_cfg(first))
    raw = config.config_path(first).read_text(encoding="utf-8")
    config.config_path(second).write_text(raw, encoding="utf-8")

    for root in (first, second):
        cfg = config.load(root)
        assert Path(cfg["root"]) == root.resolve()
        assert config.resolve(cfg["root"], cfg["sites"]["www"]) == root.resolve() / "www"


def test_normalize_never_rewrites_existing_ports():
    """老配置里写死 80/443，normalize 不许把它「顺手」改成 8080/8443。"""
    cfg = config.normalize({"web": {"http_port": 80, "https_port": 443}})
    assert cfg["web"]["http_port"] == 80
    assert cfg["web"]["https_port"] == 443


def test_normalize_backfills_missing_keys():
    cfg = config.normalize({})
    assert cfg["sites"]["tld"] == "meow"
    assert cfg["sites"]["php"] == {}
    assert cfg["php"]["cgi_children"] == 8
    assert cfg["ui"]["tray_with_panel"] is True


def test_resolve_relative_and_absolute(tmp_path):
    assert config.resolve(tmp_path, "www") == tmp_path / "www"
    absolute = tmp_path.parent
    assert config.resolve(tmp_path, str(absolute)) == absolute


def test_binary_root_prefers_provider(tmp_path):
    cfg = _cfg(tmp_path)
    assert config.binary_root(cfg) == tmp_path
    cfg["provider"] = "laragon"
    assert config.binary_root(cfg) == tmp_path / "laragon"


def test_load_without_file_says_what_to_do(tmp_path):
    try:
        config.load(tmp_path)
    except FileNotFoundError as exc:
        assert "init" in str(exc)
    else:                                        # pragma: no cover
        raise AssertionError("没配置就该报 FileNotFoundError")
