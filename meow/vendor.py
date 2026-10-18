"""彻底自带（摆脱 provider）：把程序搬进喵盒自己的 `bin/`。

现在喵盒是"借" Laragon 的程序用的（`provider: "D:/laragon"`）。这一步之后：

```
D:\\meowbox\\bin\\apache\\httpd-2.4.68-...\\bin\\httpd.exe
D:\\meowbox\\bin\\php\\php-8.3.33-...\\php.exe
D:\\meowbox\\bin\\mysql\\mysql-8.4.3-...\\bin\\mysqld.exe
D:\\meowbox\\bin\\nginx\\nginx-1.30.4\\nginx.exe
D:\\meowbox\\bin\\mailpit\\1.22.3\\mailpit.exe
D:\\meowbox\\bin\\openssl\\openssl.exe
```

配置里的 `provider` 变成 `null`，之后喵盒只认自己的 `bin/`，Laragon 可以整个删掉。

**复制完还要改一处**：Laragon 的 `php.ini` 里写死了五处 Laragon 路径
（`extension_dir`、`error_log`、`session.save_path`、`sendmail_path`、
`curl.cainfo`），不改成喵盒的路径，Laragon 一删 PHP 就废 ——
这里的 `patch_php_ini()` 会顺着改掉，顺便把 `sendmail_path` 指向 Mailpit
（这样 PHP 的 `mail()` 发出去的信直接进本地假邮箱）。
"""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from . import config as cfgmod
from . import packages as pkgmod
from . import services as svcmod
from .migrate import dir_size, human

#: 要自带的组件（名字就是 bin/ 下的子目录名）
COMPONENTS = ("apache", "php", "mysql", "nginx", "mailpit")

#: 界面上显示的名字
LABELS = {"apache": "Apache", "php": "PHP", "mysql": "MySQL",
          "nginx": "Nginx", "mailpit": "Mailpit", "openssl": "openssl"}


def plan(cfg: dict, bins: dict) -> list[dict]:
    """看看谁需要搬、搬多大、搬到哪。"""
    root = Path(cfg["root"])
    entries = []
    for key in COMPONENTS:
        src = bins.get(key)
        entry = {"key": key, "src": src, "dest": None, "size": 0, "problem": None}
        if src is None:
            entry["problem"] = "provider 里没找到 —— 要下载"
        else:
            entry["dest"] = root / "bin" / key / Path(src).name
            entry["size"] = dir_size(Path(src))
        entries.append(entry)
    return entries


def copy_components(cfg: dict, pl: list[dict], say=print) -> list[dict]:
    results = []
    for item in pl:
        key = item["key"]
        label = LABELS.get(key, key)
        if item["problem"]:
            say(f"  ⏭  {label}：{item['problem']}（先 python meowbox.py pkg install {key}）")
            results.append({"key": key, "status": "skipped", "note": item["problem"]})
            continue
        dest: Path = item["dest"]
        if dest.exists():
            say(f"  ⏭  {label}：{dest.name} 已经在 bin/ 里了，跳过")
            results.append({"key": key, "status": "exists", "note": str(dest)})
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        say(f"  ⬇  {label}：{human(item['size'])} ……")
        started = time.time()
        shutil.copytree(Path(item["src"]), dest)
        say(f"  ✅ {label}：{time.time() - started:.1f}s → {dest}")
        results.append({"key": key, "status": "copied", "note": str(dest)})
    return results


def _replace_active(text: str, pattern: str, repl: str) -> tuple[str, int]:
    """只替换没被注释掉的行（行首不能是 ; 或 #）。"""
    def _do(match: re.Match) -> str:
        return repl
    new_text, count = re.subn(rf"^(?![;#])\s*{pattern}.*$", _do, text, flags=re.M)
    return new_text, count


def patch_php_ini(php_dir: Path, cfg: dict, mailpit_exe: Path | None,
                  cacert: Path | None, say=print) -> list[str]:
    """把 php.ini 里写死的 Laragon 路径改成喵盒自己的。"""
    ini = Path(php_dir) / "php.ini"
    if not ini.is_file():
        say("  ⚠️  这个 PHP 没有 php.ini，跳过改写（PHP 会用默认值）")
        return []

    lay = cfgmod.layout(cfg)
    root = lay["root"]
    ext = Path(php_dir) / "ext"
    session_dir = lay["run"] / "php-session"
    session_dir.mkdir(parents=True, exist_ok=True)
    (lay["logs"]).mkdir(parents=True, exist_ok=True)

    rules = [
        (r"extension_dir\s*=", f'extension_dir = "{_fwd(ext)}"'),
        (r"error_log\s*=", f'error_log = "{_fwd(lay["logs"] / "php-errors.log")}"'),
        (r"session\.save_path\s*=", f'session.save_path = "{_fwd(session_dir)}"'),
        (r"include_path\s*=",
         f'include_path = ".;{_fwd(lay["etc"] / "php" / "pear")}"'),
    ]
    (lay["etc"] / "php" / "pear").mkdir(parents=True, exist_ok=True)
    if mailpit_exe is not None:
        rules.append((r"sendmail_path\s*=",
                      f'sendmail_path = "{_fwd(mailpit_exe)} sendmail"'))
    if cacert is not None:
        rules.append((r"curl\.cainfo\s*=", f'curl.cainfo = "{_fwd(cacert)}"'))

    text = ini.read_text(encoding="utf-8", errors="replace")
    changed = []
    for pattern, repl in rules:
        text, count = _replace_active(text, pattern, repl)
        if count:
            changed.append(repl)
    ini.write_text(text, encoding="utf-8", newline="\n")
    for line in changed:
        say(f"     php.ini ← {line}")
    if not changed:
        say("     php.ini 里没找到需要改的行（可能已经是相对路径了）")
    return changed


def _fwd(path) -> str:
    return str(path).replace("\\", "/")


def vendor_cacert(cfg: dict, bins: dict, say=print) -> Path | None:
    """把 provider 的 CA 证书包复制进来（PHP 的 curl 要用）。"""
    lay = cfgmod.layout(cfg)
    target = lay["ssl"] / "cacert.pem"
    if target.is_file():
        return target
    base = cfgmod.binary_root(cfg)
    for candidate in (base / "etc" / "ssl" / "cacert.pem",):
        if candidate.is_file():
            lay["ssl"].mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, target)
            say(f"  ✅ CA 证书包：{target.name}（{candidate.stat().st_size // 1024} KB）")
            return target
    say("  ⏭  CA 证书包：provider 里没找到，跳过（不影响本地开发）")
    return None


def vendor_openssl(cfg: dict, bins: dict, say=print) -> Path | None:
    """把能用的 openssl 复制到 bin/openssl/（Laragon 那个 3.6.0 是好的）。"""
    from . import ssl as sslmod
    lay = cfgmod.layout(cfg)
    target = lay["root"] / "bin" / "openssl"
    if (target / "openssl.exe").is_file():
        return target / "openssl.exe"
    found = sslmod.find_openssl(cfg, bins)
    if not found:
        say("  ⏭  openssl：没找到能跑的，跳过（以后签证书要手动指路径）")
        return None
    source_dir = Path(found).parent
    target.mkdir(parents=True, exist_ok=True)
    for item in source_dir.iterdir():
        if item.is_file():
            shutil.copy2(item, target / item.name)
    say(f"  ✅ openssl：{found.name} → {target}")
    return target / "openssl.exe"


def flip_provider(cfg: dict, say=print) -> dict:
    """把 provider 设成 null：从今往后只认自己的 bin/。"""
    old = cfg.get("provider")
    cfg["provider"] = None
    cfgmod.save(cfg)
    say(f"  ✅ provider：{old or '（无）'} → null（只用自己的 bin/）")
    return cfg


def verify(cfg: dict, bins: dict) -> list[tuple[bool, str, str]]:
    """验证五个组件都能从自己的 bin/ 里跑起来。"""
    from .util import run
    checks: list[tuple[bool, str, str]] = []
    probes = {
        "apache": (["bin", "httpd.exe"], ["-v"]),
        "php": (["php.exe"], ["-v"]),
        "mysql": (["bin", "mysqld.exe"], ["--version"]),
        "nginx": (["nginx.exe"], ["-v"]),
        "mailpit": (["mailpit.exe"], ["version"]),
    }
    for key in COMPONENTS:
        path = bins.get(key)
        label = LABELS.get(key, key)
        if not path:
            checks.append((False, label, "没找到"))
            continue
        rel, args = probes.get(key, ([], []))
        exe = Path(path).joinpath(*rel) if rel else None
        if exe is None or not exe.is_file():
            checks.append((False, label, f"程序文件不在：{path}"))
            continue
        rc, out, err = run([exe, *args], cwd=Path(path), timeout=30)
        text = (out or err).strip().splitlines()
        first = text[0] if text else ""
        ok = rc == 0 and bool(first)
        checks.append((ok, label, first or f"rc={rc}"))
    # openssl / CA
    ca = cfgmod.layout(cfg)["root"] / "bin" / "openssl" / "openssl.exe"
    if ca.is_file():
        rc, out, _ = run([ca, "version"], timeout=30)
        checks.append((rc == 0, "openssl", out.strip() or "rc!=0"))
    return checks


def full_vendor(cfg: dict, bins: dict, say=print, restart: bool = True) -> dict:
    """一条龙：复制 → 改 php.ini → 拷 openssl/CA → 切 provider → 起停验证。"""
    root = Path(cfg["root"])
    say("① 复制组件到自己的 bin/")
    pl = plan(cfg, bins)
    copied = copy_components(cfg, pl, say=say)

    say("② 改 php.ini 里写死的路径")
    php_entry = next((e for e in pl if e["key"] == "php"), None)
    php_dir = Path(php_entry["dest"]) if php_entry and php_entry["dest"] else None
    mailpit_entry = next((e for e in pl if e["key"] == "mailpit"), None)
    mailpit_exe = None
    if mailpit_entry and mailpit_entry["dest"]:
        candidate = Path(mailpit_entry["dest"]) / "mailpit.exe"
        if candidate.is_file():
            mailpit_exe = candidate
    cacert = vendor_cacert(cfg, bins, say=say)
    if php_dir and php_dir.is_dir():
        patch_php_ini(php_dir, cfg, mailpit_exe, cacert, say=say)

    say("③ 复制 openssl（以后签 HTTPS 证书要用）")
    vendor_openssl(cfg, bins, say=say)

    say("④ 切换 provider → null")
    flip_provider(cfg, say=say)

    say("⑤ 重新探测 + 语法自检")
    new_bins = cfgmod.discover(cfg)
    services = svcmod.build(cfg, new_bins)
    # 两个 Web 服务器的配置都要重渲染 —— 不然另一份里还留着旧 provider 的路径。
    # 但语法自检只能测"当前那个"：两个都想要 80 端口，一起测会 bind 失败。
    active_name = svcmod.server_name(cfg)
    for name, svr in svcmod.web_servers(cfg, new_bins).items():
        if not svr.available:
            continue
        svr.render()
        if name != active_name:
            say(f"  {svcmod.pretty(name)} 配置已重新生成（切过去时再做语法自检）")
            continue
        rc, out, err = svr.test()
        say(f"  {svcmod.pretty(name)} 配置重新生成：" +
            ("✅ 通过" if rc == 0 else "❌ " + (out + err).strip()[-500:]))
    server = svcmod.web(services)

    if restart:
        say("⑥ 用新程序重启服务")
        svcmod.stop(services, ["all"])
        time.sleep(0.6)
        rows = svcmod.start(services, ["all"])
        for name, state, extra in rows:
            say(f"  {svcmod.pretty(name)}：{state} {extra}".rstrip())

    say("⑦ 验证")
    checks = verify(cfg, new_bins)
    for ok, label, text in checks:
        say(f"  {'✅' if ok else '❌'} {label:<8} {text}")

    return {"copied": copied, "bins": new_bins, "checks": checks,
            "root": root, "server": server}


def download_missing(cfg: dict, pl: list[dict], say=print) -> list[dict]:
    """provider 里没有的组件，走 packages.conf 下载（名字用 key 本身）。"""
    out = []
    for item in pl:
        if not item["problem"]:
            continue
        key = item["key"]
        say(f"  ⬇  下载 {LABELS.get(key, key)} ……")
        ok, message = pkgmod.install(cfg, key)
        say(("  ✅ " if ok else "  ❌ ") + message)
        out.append({"key": key, "status": "downloaded" if ok else "error", "note": message})
    return out
