"""HTTPS 体检（doctor）—— 一层层查「为什么浏览器不给我小绿锁」。

排查顺序就是真实的故障顺序，别跳步：

1. **开关**：`web.ssl` 开着吗？端口是多少？
2. **根证书**：本地 CA 在不在、有没有装进当前用户的信任库（不装 = 浏览器红锁）；
3. **站点证书**：每个站点有没有 `.crt/.key`、链验得过吗、SAN 里有这个域名吗、到期没；
4. **虚拟主机**：`sites-enabled/auto.<域名>-ssl.conf` 生成了吗
   —— **换过 TLD 最容易漏这一层**（证书还停在旧后缀，vhost 就不生成）；
5. **服务**：`https_port` 谁在听、是不是当前那个 Web 服务器；
6. **实测**：真的带 SNI 握一次手，把服务器递过来的证书 CN 跟期望域名比。
   这一步能抓住「443 上跑的是明文 HTTP」——光看配置文件是看不出来的。

返回值（给 CLI 和面板共用）：

    {"checks": [(level, item, note), ...], "ok": bool,
     "fixable": bool, "advice": [str, ...], "tls": {...}}

level 用 `portable.OK / WARN / INFO` 那三个字符串，界面渲染方式保持一致。
"""
from __future__ import annotations

import re
import socket
import ssl as py_ssl
from datetime import datetime, timezone
from pathlib import Path

from . import config as cfgmod
from . import logs
from . import ssl as sslmod
from . import util, vhost
from .portable import INFO, OK, WARN


# ---------------------------------------------------------------- 小工具
def _days_left(not_after: str) -> int | None:
    """`Jun  5 12:00:00 2028 GMT` → 还剩几天。"""
    text = re.sub(r"\s+", " ", (not_after or "").strip())
    for fmt in ("%b %d %H:%M:%S %Y %Z", "%b %d %H:%M:%S %Y"):
        try:
            when = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return (when - datetime.now(timezone.utc)).days
        except ValueError:
            continue
    return None


def _san_names(ca, host: str) -> list[str]:
    """证书里的 subjectAltName 域名列表。"""
    crt, _ = ca.cert_paths(host)
    if not crt.is_file() or not ca.available:
        return []
    rc, out, _ = ca._openssl(["x509", "-in", util.fwd(crt), "-noout", "-ext", "subjectAltName"])
    return re.findall(r"DNS:([^,\s]+)", out) if rc == 0 else []


def _tls_probe(host: str | None, port: int, timeout: float = 3.0) -> dict:
    """真的握一次手，看服务器到底给了什么。不验证书（自签本来就验不过）。"""
    ctx = py_ssl.SSLContext(py_ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = py_ssl.CERT_NONE
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout) as raw:
            raw.settimeout(timeout)
            with ctx.wrap_socket(raw, server_hostname=host) as tls:
                return {
                    "ok": True,
                    "version": tls.version(),
                    "cipher": (tls.cipher() or ("?",))[0],
                    "der": tls.getpeercert(binary_form=True) or b"",
                }
    except py_ssl.SSLError as exc:
        message = str(exc)
        # 服务器拿明文 HTTP 回了 ClientHello，Python/OpenSSL 就报这几种
        plaintext = any(key in message for key in (
            "WRONG_VERSION_NUMBER", "record layer", "PACKET_LENGTH",
            "UNEXPECTED_MESSAGE", "unknown protocol",
        ))
        return {"ok": False, "error": message, "plaintext": plaintext}
    except OSError as exc:
        return {"ok": False, "error": f"连不上 127.0.0.1:{port}（{exc}）", "plaintext": False}


def _peer_identity(ca, der: bytes, run_dir: Path) -> dict:
    """把握到的那张证书落盘解一下，看 CN / SAN / 到期。"""
    if not der or not ca.available:
        return {}
    peek = Path(run_dir) / "ssl-peek.crt"
    try:
        util.ensure_dir(peek.parent)
        peek.write_bytes(der)
        rc, out, _ = ca._openssl(["x509", "-in", util.fwd(peek), "-noout",
                                  "-subject", "-dates", "-ext", "subjectAltName"])
    finally:
        peek.unlink(missing_ok=True)
    info = {"subject": "", "not_after": "", "san": []}
    if rc != 0:
        return info
    for line in out.splitlines():
        if line.startswith("subject="):
            info["subject"] = line.split("=", 1)[1].strip()
        elif line.startswith("notAfter="):
            info["not_after"] = line.split("=", 1)[1].strip()
    info["san"] = re.findall(r"DNS:([^,\s]+)", out)
    return info


def _mod_ssl_warning(root: Path) -> tuple[str, str] | None:
    """日志里 mod_ssl 的版本告警：返回 (编译用版本, 实际加载版本)。"""
    log = Path(root) / "logs" / "apache-error.log"
    if not log.is_file():
        return None
    try:
        with open(log, "r", encoding="utf-8", errors="replace") as fh:
            tail = fh.readlines()[-400:]
    except OSError:
        return None
    pattern = re.compile(r"compiled against a newer library \(OpenSSL ([\d.]+).*?\(OpenSSL ([\d.]+)")
    for line in reversed(tail):
        found = pattern.search(line)
        if found:
            return found.group(1), found.group(2)
    return None


def _version_key(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text or ""))


# ---------------------------------------------------------------- 主流程
def https_report(cfg: dict, bins: dict | None = None) -> dict:
    bins = bins or {}
    lay = cfgmod.layout(cfg)
    web = cfg.get("web") or {}
    port = int(web.get("https_port", 443))
    server_name = web.get("server", "apache")
    hosts = sslmod.site_hostnames(cfg)
    ca = sslmod.CertAuthority(cfg, bins)

    checks: list[tuple[str, str, str]] = []
    advice: list[str] = []
    fixable = False

    # 1) 开关
    if web.get("ssl"):
        checks.append((OK, "HTTPS 开关", f"已打开（{server_name}，端口 {port}）"))
    else:
        checks.append((WARN, "HTTPS 开关", "关着（web.ssl = false）"))
        advice.append("点「生成 SSL 证书」一键打开（会自动签发 + 装信任库 + 重启）")
        fixable = True

    # 2) 根证书 + 信任
    if not ca.available:
        checks.append((WARN, "openssl", "找不到可用的 openssl.exe（签发/检查都要它）"))
        advice.append('在 meowbox.config.json 里把 "openssl" 指到一个能跑的 openssl.exe')
    elif not ca.ca_exists():
        checks.append((WARN, "根证书", "还没建（本地 CA 不在）"))
        advice.append("点「生成 SSL 证书」，会先建根证书再签站点证书")
        fixable = True
    else:
        trusted = sslmod.is_trusted(ca)
        checks.append((OK if trusted else WARN, "根证书",
                       "已建，并且装进了当前用户信任库"
                       if trusted else "已建，但**没装进信任库** —— 浏览器会红锁"))
        if not trusted:
            advice.append("在「HTTPS 证书」菜单里点「把根证书装进信任库」")
            fixable = True

    # 3) 每个站点的证书
    if not hosts:
        checks.append((INFO, "站点", "www/ 里还没有站点，没什么可签的"))
    for host in hosts:
        crt, key = ca.cert_paths(host)
        if not (crt.is_file() and key.is_file()):
            checks.append((WARN, f"{host} 的证书", "没有（这个站就算配了 ssl 也不会生成 vhost）"))
            advice.append("点「重新签发证书」补齐")
            fixable = True
            continue
        chain_ok, chain_msg = ca.verify(host) if ca.available else (False, "没有 openssl")
        info = ca.info(host)
        days = _days_left(info.get("not_after", ""))
        san = _san_names(ca, host)
        if not chain_ok:
            checks.append((WARN, f"{host} 的证书", f"链验不过：{chain_msg}"))
            advice.append("证书是用旧 CA 签的（换过 CA/后缀），点「重新签发证书」")
            fixable = True
        elif host not in san:
            checks.append((WARN, f"{host} 的证书", f"链没问题，但 SAN 里没有 {host}（有：{', '.join(san) or '无'}）"))
            advice.append("证书域名对不上，点「重新签发证书」")
            fixable = True
        elif days is not None and days <= 0:
            checks.append((WARN, f"{host} 的证书", f"已经过期了（{info['not_after']}）"))
            advice.append("点「重新签发证书」")
            fixable = True
        elif days is not None and days < 30:
            checks.append((WARN, f"{host} 的证书", f"{days} 天后过期（{info['not_after']}）"))
            advice.append("点「重新签发证书」续一下")
            fixable = True
        else:
            left = f"，还剩 {days} 天" if days is not None else ""
            checks.append((OK, f"{host} 的证书", f"有效（{info['not_after']}{left}）"))

    # 4) SSL 虚拟主机片段
    ssl_sites = lay["sites_enabled"] if server_name == "apache" else lay["nginx_sites"]
    for host in hosts:
        if server_name == "apache":
            piece = Path(ssl_sites) / f"auto.{host}-ssl.conf"
        else:
            piece = Path(ssl_sites) / f"auto.{host}.conf"      # nginx 的 ssl 在同一份里
        if piece.is_file():
            checks.append((OK, f"{host} 的 SSL 配置", f"在（{util.fwd(piece)}）"))
        else:
            checks.append((WARN, f"{host} 的 SSL 配置", f"没生成（{server_name} 不会拿 https 服务这个站）"))
            advice.append("点「重新生成配置」；证书不全的话先点「重新签发证书」")
            fixable = True

    # 5) 端口 + 服务
    holder = util.port_holder(port)
    if holder:
        pid, name = holder
        expected = ("httpd", "nginx")
        if any(word in (name or "").lower() for word in expected):
            checks.append((OK, f"{port} 端口", f"{name}（pid {pid}）在听"))
        else:
            checks.append((WARN, f"{port} 端口", f"被别的程序占了：{name}（pid {pid}）"))
            advice.append(f"先「释放被占端口」，或者换个 https_port")
    else:
        checks.append((WARN, f"{port} 端口", "没有程序在听（服务没起来？）"))
        advice.append("点「启动全部」，或「重启」")

    # 6) 真握手
    tls: dict = {}
    if hosts and holder:
        for host in hosts:
            probe = _tls_probe(host, port)
            tls[host] = probe
            if probe.get("ok"):
                peer = _peer_identity(ca, probe.get("der", b""), lay["run"])
                cn_ok = host in (peer.get("san") or []) or f"CN={host}" in (peer.get("subject") or "")
                detail = f"{probe['version']} / {probe['cipher']}"
                if not peer:
                    checks.append((OK, f"{host} 真握手", f"成功（{detail}），证书没解出来"))
                elif cn_ok:
                    checks.append((OK, f"{host} 真握手",
                                   f"成功（{detail}），服务器给的是 {host} 的证书 ✅"))
                else:
                    checks.append((WARN, f"{host} 真握手",
                                   f"成功了，但服务器递过来的证书是「{peer.get('subject') or '未知'}」"
                                   f"，不是 {host}（SNI 没命中这个站）"))
                    advice.append("检查该站的 -ssl.conf 是否在、ServerName 对不对")
            elif probe.get("plaintext"):
                checks.append((WARN, f"{host} 真握手",
                               f"**拿到的是明文 HTTP 响应** —— {port} 上落脚的是普通 HTTP 站点"))
                advice.append("443 上没有 SSL 虚拟主机：先「重新签发证书」，再「重新生成配置」+「重启」")
                fixable = True
            else:
                checks.append((WARN, f"{host} 真握手", f"失败：{probe.get('error')}"))
                advice.append("先看 logs/apache-error.log 里的 SSL 报错")

    # 7) mod_ssl 的 OpenSSL 版本告警（只是提示，握手通了就无所谓）
    mismatch = _mod_ssl_warning(Path(cfg["root"]))
    if mismatch:
        built, loaded = mismatch
        if _version_key(loaded)[:2] < _version_key(built)[:2]:
            checks.append((WARN, "mod_ssl / OpenSSL",
                           f"编译用 {built}，实际加载 {loaded}（跨了次版本，可能出怪问题）"))
            advice.append("把 bin/openssl 里的 libssl/libcrypto 拷进 apache 的 bin/（先停 apache）")
        else:
            checks.append((INFO, "mod_ssl / OpenSSL",
                           f"编译用 {built}，实际加载 {loaded}（同一条 3.x ABI，忽略即可）"))

    ok = not any(level == WARN for level, _item, _note in checks)
    # 同一个建议可能被每个站点各加一遍，去重（保持第一次出现的顺序）
    advice = list(dict.fromkeys(advice))
    report = {"checks": checks, "ok": ok, "fixable": fixable, "advice": advice, "tls": tls}
    _record(cfg, report, port, server_name)
    return report


def _record(cfg: dict, report: dict, port: int, server_name: str) -> None:
    """把这次体检记进 `logs/https-check.log`（滚动，最多两份备份）。

    记日志失败绝不能挡住体检本身 —— 路径不可写、磁盘满都直接咽掉。
    """
    try:
        path = Path(cfgmod.layout(cfg)["logs"]) / logs.CHECK_LOG
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        problems = sum(1 for level, _i, _n in report["checks"] if level == WARN)
        head = (f"[{stamp}] {'全绿 ✅' if report['ok'] else f'{problems} 处不对劲 ⚠️'}"
                f"  —  {server_name}:{port}")
        marks = {OK: "✅", WARN: "⚠️", INFO: "ℹ️"}
        lines = [head]
        for level, item, note in report["checks"]:
            lines.append(f"    {marks.get(level, '·')} {item}：{note.replace('**', '')}")
        for tip in report["advice"]:
            lines.append(f"    → {tip}")
        logs.append_log(path, "\n".join(lines))
    except (OSError, KeyError):
        pass


def as_text(report: dict) -> str:
    """CLI / 日志里用的一行行文本。"""
    marks = {OK: "✅", WARN: "⚠️", INFO: "ℹ️"}
    lines = [f"{marks.get(level, '·')} {item}：{note}" for level, item, note in report["checks"]]
    if report["advice"]:
        lines.append("")
        lines.extend("   → " + tip for tip in report["advice"])
    lines.append("")
    lines.append("结论：小绿锁链路是通的 ✅" if report["ok"]
                 else "结论：还差几步（按上面的 → 做就行）")
    return "\n".join(lines)
