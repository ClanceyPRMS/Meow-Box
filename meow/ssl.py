"""自签 HTTPS 证书 —— 建一个本地 CA，给每个站点签证书，再装进系统信任库。

思路和 mkcert 一样：
    1. 生成一个只属于这台机器的根证书（CA），10 年
    2. 用它给每个站点签发证书（带 SAN，现代浏览器只认 SAN 不认 CN）
    3. 把根证书装进 **当前用户** 的信任库 —— 这一步不需要管理员
    4. 打开 `web.ssl`，让 Apache 监听 443 并为每个站点生成 SSL 虚拟主机

装完之后 https://blog.meow 就是小绿锁，不再报警告。
不想要了就 `ssl untrust` + `ssl off`，根证书也随时可以删。
"""
from __future__ import annotations

import os
from pathlib import Path

from . import config as cfgmod
from .util import ensure_dir, fwd, run

CA_CN = "Meow Box Local CA"
CA_SUBJECT = f"/C=CN/O=Meow Box/CN={CA_CN}"
CA_DAYS = 3650
CERT_DAYS = 825

#: 有些 openssl 构建（比如 Apache Lounge / Laragon 自带的）把默认配置路径写死成
#: `C:\Apache24\conf\openssl.cnf`。那个文件不存在时它直接罢工，所以喂它一份最小的。
MINIMAL_CNF = """\
# Meow Box 自动生成的最小 OpenSSL 配置
# 用 OPENSSL_CONF 指到这里，免得 openssl 去找不存在的默认配置。
[ req ]
default_bits       = 2048
default_md         = sha256
distinguished_name = req_distinguished_name
prompt             = no

[ req_distinguished_name ]
CN = localhost

[ v3_ca ]
basicConstraints = critical,CA:TRUE,pathlen:0
keyUsage         = critical,keyCertSign,cRLSign

[ v3_leaf ]
basicConstraints = CA:FALSE
keyUsage         = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
"""

#: 按优先级找 openssl。注意 Apache Lounge 自带的那个可能是坏的
#: （实测返回 0xC0000139 STATUS_ENTRYPOINT_NOT_FOUND），所以每个都要验一遍。
def _candidates(cfg: dict, bins: dict) -> list[Path]:
    out: list[Path] = []
    if cfg.get("openssl"):
        out.append(Path(cfg["openssl"]))
    base = cfgmod.binary_root(cfg)
    out += [
        base / "bin" / "laragon" / "utils" / "openssl" / "openssl.exe",   # Laragon 自带
        base / "bin" / "openssl" / "openssl.exe",                          # 喵盒自己下的
        Path(r"C:\Program Files\Git\usr\bin\openssl.exe"),
        Path(r"C:\Program Files\Git\mingw64\bin\openssl.exe"),
    ]
    if bins.get("apache"):
        apache = Path(bins["apache"])
        out.append(apache / "bin" / "openssl.exe")
        out += sorted(apache.glob("bin/openssl.exe"))
    return out


def find_openssl(cfg: dict, bins: dict):
    """返回一个真的能跑的 openssl.exe（拿 `version` 实测过），找不到返回 None。"""
    seen = set()
    for candidate in _candidates(cfg, bins):
        key = str(candidate).lower()
        if key in seen or not candidate.is_file():
            continue
        seen.add(key)
        rc, out, _ = run([candidate, "version"], timeout=30)
        if rc == 0 and "OpenSSL" in out:
            return candidate
    return None


class CertAuthority:
    """本地 CA + 站点证书。"""

    def __init__(self, cfg: dict, bins: dict):
        self.cfg = cfg
        self.lay = cfgmod.layout(cfg)
        self.openssl = find_openssl(cfg, bins)

    # ---------- 路径 ----------
    @property
    def available(self) -> bool:
        return self.openssl is not None

    @property
    def ssl_dir(self) -> Path:
        return self.lay["ssl"]

    @property
    def sites_dir(self) -> Path:
        return self.lay["ssl_sites"]

    @property
    def ca_key(self) -> Path:
        return self.ssl_dir / "meowbox-ca.key"

    @property
    def ca_crt(self) -> Path:
        return self.ssl_dir / "meowbox-ca.crt"

    @property
    def ca_serial(self) -> Path:
        return self.ssl_dir / "meowbox-ca.srl"

    @property
    def openssl_cnf(self) -> Path:
        return self.ssl_dir / "openssl.cnf"

    def ensure_openssl_cnf(self) -> Path:
        if not self.openssl_cnf.is_file():
            ensure_dir(self.ssl_dir)
            self.openssl_cnf.write_text(MINIMAL_CNF, encoding="utf-8", newline="\n")
        return self.openssl_cnf

    def cert_paths(self, host: str) -> tuple[Path, Path]:
        return self.sites_dir / f"{host}.crt", self.sites_dir / f"{host}.key"

    def has_cert(self, host: str) -> bool:
        crt, key = self.cert_paths(host)
        return crt.is_file() and key.is_file()

    def ca_exists(self) -> bool:
        return self.ca_crt.is_file() and self.ca_key.is_file()

    # ---------- 跑 openssl ----------
    def _openssl(self, args, timeout=120):
        env = os.environ.copy()
        env["RANDFILE"] = str(self.lay["run"] / ".rnd")
        env["OPENSSL_CONF"] = str(self.ensure_openssl_cnf())
        return run([self.openssl] + [str(a) for a in args], timeout=timeout, env=env)

    def _fail(self, what, out, err):
        detail = (err or out).strip()
        raise RuntimeError(f"{what}失败：\n{detail[-1200:]}")

    # ---------- 干活的 ----------
    def ensure_ca(self) -> bool:
        """建根证书。返回 True 表示这次新建了。"""
        if self.ca_exists():
            return False
        if not self.available:
            raise RuntimeError(
                "找不到可用的 openssl.exe。\n"
                "可以在 meowbox.config.json 里把 \"openssl\" 设成你自己的 openssl 路径。"
            )
        ensure_dir(self.ssl_dir)
        rc, out, err = self._openssl([
            "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256",
            "-days", str(CA_DAYS),
            "-keyout", fwd(self.ca_key), "-out", fwd(self.ca_crt),
            "-subj", CA_SUBJECT,
            "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        ])
        if rc != 0:
            self._fail("生成根证书", out, err)
        return True

    def make_cert(self, host: str, days: int = CERT_DAYS) -> tuple[Path, Path]:
        """给一个域名签证书（带 SAN，含通配子域）。"""
        if not self.ca_exists():
            raise RuntimeError("还没有根证书，先跑： python meowbox.py ssl init")
        ensure_dir(self.sites_dir)
        crt, key = self.cert_paths(host)
        csr = self.sites_dir / f"{host}.csr"
        ext = self.sites_dir / f"{host}.ext"
        ext.write_text(
            "basicConstraints=CA:FALSE\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\n"
            "extendedKeyUsage=serverAuth\n"
            f"subjectAltName=DNS:{host},DNS:*.{host}\n",
            encoding="utf-8",
        )
        rc, out, err = self._openssl([
            "req", "-new", "-newkey", "rsa:2048", "-nodes", "-sha256",
            "-keyout", fwd(key), "-out", fwd(csr),
            "-subj", f"/C=CN/O=Meow Box/CN={host}",
            "-addext", f"subjectAltName=DNS:{host},DNS:*.{host}",
        ])
        if rc != 0:
            self._fail(f"生成 {host} 的密钥和请求文件", out, err)

        rc, out, err = self._openssl([
            "x509", "-req", "-in", fwd(csr),
            "-CA", fwd(self.ca_crt), "-CAkey", fwd(self.ca_key),
            "-CAcreateserial", "-CAserial", fwd(self.ca_serial),
            "-out", fwd(crt), "-days", str(days), "-sha256",
            "-extfile", fwd(ext),
        ])
        if rc != 0:
            self._fail(f"用根证书签发 {host}", out, err)
        csr.unlink(missing_ok=True)
        return crt, key

    def verify(self, host: str) -> tuple[bool, str]:
        """拿根证书验一下签出来的证书，确保链是对的。"""
        crt, _ = self.cert_paths(host)
        if not crt.is_file():
            return False, "证书不存在"
        rc, out, err = self._openssl(["verify", "-CAfile", fwd(self.ca_crt), fwd(crt)])
        return rc == 0, (out or err).strip()

    def info(self, host: str) -> dict:
        crt, _ = self.cert_paths(host)
        rc, out, _ = self._openssl(["x509", "-in", fwd(crt), "-noout", "-subject", "-dates"])
        info = {"host": host, "exists": crt.is_file(), "subject": "", "not_after": ""}
        for line in out.splitlines():
            if line.startswith("subject="):
                info["subject"] = line.split("=", 1)[1].strip()
            elif line.startswith("notAfter="):
                info["not_after"] = line.split("=", 1)[1].strip()
        return info

    def thumbprint(self) -> str:
        rc, out, _ = self._openssl(["x509", "-in", fwd(self.ca_crt), "-noout",
                                    "-fingerprint", "-sha1"])
        return out.split("=", 1)[-1].strip().replace(":", "").upper() if "=" in out else ""


# ------------------------------------------------------------------ 信任库
def is_trusted(ca: CertAuthority) -> bool:
    """根证书在不在当前用户的信任库里。"""
    if not ca.ca_exists():
        return False
    rc, out, err = run(["certutil", "-store", "-user", "Root", CA_CN], timeout=30)
    text = ((out or "") + (err or "")).upper()
    thumb = ca.thumbprint()
    if thumb and thumb in text.replace(" ", "").replace("\n", ""):
        return True
    return rc == 0 and CA_CN.upper() in text and "找不到" not in (out or "")


def trust_ca(ca: CertAuthority) -> tuple[bool, str]:
    """把根证书装进当前用户的 Root 信任库（不需要管理员）。"""
    if not ca.ca_exists():
        return False, "还没有根证书"
    rc, out, err = run(["certutil", "-addstore", "-user", "Root", str(ca.ca_crt)], timeout=60)
    if rc != 0:
        return False, ((err or out).strip() or f"certutil 返回 {rc}")[-600:]
    return True, "已装进当前用户的信任库"


def untrust_ca(ca: CertAuthority) -> tuple[bool, str]:
    if not ca.ca_exists():
        return False, "还没有根证书"
    rc, out, err = run(["certutil", "-delstore", "-user", "Root", CA_CN], timeout=60)
    if rc != 0:
        return False, ((err or out).strip() or f"certutil 返回 {rc}")[-600:]
    return True, "已从信任库移除"


# ------------------------------------------------------------------ 一键
def provision(cfg: dict, bins: dict, force: bool = False, do_trust: bool = True,
              log=None) -> dict:
    """一键：建 CA → 给所有站点签证书 → 打开 HTTPS → 装信任库。

    调用方负责重新渲染 Apache 配置（和重启服务）。
    """
    from . import vhost

    say = log or (lambda _m: None)
    ca = CertAuthority(cfg, bins)
    if not ca.available:
        raise RuntimeError(
            "找不到可用的 openssl.exe —— 没法签证书。\n"
            "在 meowbox.config.json 里把 \"openssl\" 指到一个能用的 openssl 就行。"
        )
    say(f"用这个 openssl：{ca.openssl}")

    created_ca = ca.ensure_ca()
    say("根证书：新建了一张" if created_ca else "根证书：已经有了，直接复用")

    lay = cfgmod.layout(cfg)
    hosts = [vhost.hostname_for(n, cfg["sites"]["tld"]) for n in vhost.scan(lay["www"])]
    made, skipped = [], []
    for host in hosts:
        if ca.has_cert(host) and not force:
            skipped.append(host)
            say(f"已有证书：{host}")
            continue
        ca.make_cert(host)
        ok, message = ca.verify(host)
        say(f"已签发{'' if ok else '（但验证没过！）'}：{host}")
        made.append(host)

    cfg["web"]["ssl"] = True
    cfgmod.save(cfg)
    say("配置里已打开 HTTPS（web.ssl = true）")

    trusted = None
    if do_trust:
        trusted, message = trust_ca(ca)
        say(("信任库：" + message) if trusted else ("信任库：装失败了 —— " + message))

    return {"ca_created": created_ca, "made": made, "skipped": skipped,
            "hosts": hosts, "trusted": trusted, "ca": ca, "ssl_dir": ca.ssl_dir}


def disable(cfg: dict) -> None:
    """关掉 HTTPS（证书文件保留，随时能再开）。"""
    cfg["web"]["ssl"] = False
    cfgmod.save(cfg)


def site_hostnames(cfg: dict) -> list[str]:
    from . import vhost
    lay = cfgmod.layout(cfg)
    return [vhost.hostname_for(n, cfg["sites"]["tld"]) for n in vhost.scan(lay["www"])]


__all__ = ["CertAuthority", "provision", "disable", "trust_ca", "untrust_ca",
           "is_trusted", "find_openssl", "site_hostnames", "CA_CN"]
