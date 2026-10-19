"""扫描 www/ → 生成虚拟主机配置片段。"""
from __future__ import annotations

from pathlib import Path

from .util import fwd, render_template

FALLBACK_APACHE = """<VirtualHost *:@port@>
    DocumentRoot "@docroot@"
    ServerName @hostname@
    ServerAlias *.@hostname@
    <Directory "@docroot@">
        AllowOverride All
        Require all granted
    </Directory>
@PHP_BLOCK@    ErrorLog "@logdir@/site-@name@-error.log"
    CustomLog "@logdir@/site-@name@-access.log" common
</VirtualHost>
"""

FALLBACK_NGINX = """server {
    listen @port@;
    server_name @hostname@ *.@hostname@;
    root "@docroot@";
    index index.html index.php;

    location / {
        try_files $uri $uri/ /index.php?$query_string;
    }
}
"""

FALLBACK_APACHE_SSL = """<VirtualHost *:@port@>
    DocumentRoot "@docroot@"
    ServerName @hostname@
    ServerAlias *.@hostname@
    SSLEngine on
    SSLCertificateFile    "@cert@"
    SSLCertificateKeyFile "@key@"
    <Directory "@docroot@">
        AllowOverride All
        Require all granted
    </Directory>
@PHP_BLOCK@    ErrorLog "@logdir@/site-@name@-ssl-error.log"
    CustomLog "@logdir@/site-@name@-ssl-access.log" common
</VirtualHost>
"""


def scan(www_dir) -> list[str]:
    """一个子文件夹 = 一个站点。忽略 . 和 _ 开头的。"""
    www = Path(www_dir)
    if not www.is_dir():
        return []
    return sorted(
        d.name for d in www.iterdir()
        if d.is_dir() and not d.name.startswith((".", "_"))
    )


def hostname_for(name: str, tld: str) -> str:
    return f"{name}.{tld}" if tld else name


def site_url(cfg: dict, lay: dict, name: str) -> str:
    """这个站点现在该怎么打开：签过证书就 https，否则 http。

    面板里双击、命令行 `list`、托盘菜单都用这一个判断 —— 免得三处各写一套，
    最后出现「面板显示 http、浏览器却已经有小绿锁」这种不一致。
    """
    host = hostname_for(name, cfg["sites"]["tld"])
    if cfg.get("web", {}).get("ssl"):
        crt = Path(lay["ssl_sites"]) / f"{host}.crt"
        key = Path(lay["ssl_sites"]) / f"{host}.key"
        if crt.is_file() and key.is_file():
            return f"https://{host}"
    return f"http://{host}"


def php_block(port: int | None, label: str | None = None,
              default_label: str | None = None, docroot: str = "") -> str:
    """Apache 里让这个站点走 FastCGI 的那一段；默认版本（mod_php）返回空串。

    两个 Windows 专属的坑（都实测踩过）：
    * `SetHandler proxy:fcgi://127.0.0.1:9001` 少了结尾的 `/`，Apache 会把
      请求路径直接拼上去变成 `fcgi://127.0.0.1:9001D:/…`，于是去解析
      「主机 9001d」并报 DNS lookup failure —— 所以结尾的斜杠**必须有**。
    * 别改成 ProxyPassMatch：那样 r->filename 变成代理地址，SCRIPT_FILENAME
      就丢了，php-cgi 只回一句 No input file specified。
    """
    if not port:
        return ""
    hint = f"PHP {label}" if label else "PHP"
    if default_label:
        hint += f"（默认版本 {default_label} 走 mod_php，这里走 php-cgi）"
    return (
        f"    # {hint} —— FastCGI\n"
        f'    <FilesMatch "\\.php$">\n'
        f'        SetHandler "proxy:fcgi://127.0.0.1:{port}/"\n'
        f"    </FilesMatch>\n"
        # mod_proxy_fcgi 在 Windows 上会把 SCRIPT_FILENAME 前面加一个 `/`，
        # 变成 `/D:/…`（Windows 上不是合法绝对路径）→ php-cgi 只回
        # "No input file specified"。用 DOCUMENT_ROOT + SCRIPT_NAME 自己拼一份对的。
        f'    ProxyFCGISetEnvIf "true" SCRIPT_FILENAME '
        f'"%{{reqenv:DOCUMENT_ROOT}}%{{reqenv:SCRIPT_NAME}}"\n'
    )


def _template(templates_dir, filename: str, fallback: str) -> str:
    tpl = Path(templates_dir) / filename
    if tpl.is_file():
        return tpl.read_text(encoding="utf-8")
    return fallback


def render_apache(www_dir, name, tld, port, logdir, templates_dir,
                  fcgi_port: int | None = None, fcgi_label: str | None = None,
                  default_label: str | None = None) -> str:
    tpl = _template(templates_dir, "VirtualHost.tpl", FALLBACK_APACHE)
    return render_template(
        tpl,
        name=name,
        hostname=hostname_for(name, tld),
        port=port,
        docroot=fwd(Path(www_dir) / name),
        logdir=fwd(logdir),
        PHP_BLOCK=php_block(fcgi_port, fcgi_label, default_label, fwd(Path(www_dir) / name)),
    )


def render_apache_ssl(www_dir, name, tld, port, logdir, templates_dir, cert, key,
                      fcgi_port: int | None = None, fcgi_label: str | None = None,
                      default_label: str | None = None) -> str:
    """HTTPS 版的虚拟主机片段。"""
    tpl = _template(templates_dir, "VirtualHost-SSL.tpl", FALLBACK_APACHE_SSL)
    return render_template(
        tpl,
        name=name,
        hostname=hostname_for(name, tld),
        port=port,
        docroot=fwd(Path(www_dir) / name),
        logdir=fwd(logdir),
        cert=fwd(cert),
        key=fwd(key),
        PHP_BLOCK=php_block(fcgi_port, fcgi_label, default_label, fwd(Path(www_dir) / name)),
    )


def render_nginx(www_dir, name, tld, port, logdir, templates_dir,
                 fastcgi_port: int = 9000, fastcgi_params=None) -> str:
    tpl = _template(templates_dir, "nginx-server.tpl", FALLBACK_NGINX)
    return render_template(
        tpl,
        name=name,
        hostname=hostname_for(name, tld),
        port=port,
        docroot=fwd(Path(www_dir) / name),
        logdir=fwd(logdir),
        fastcgi_port=fastcgi_port,
        fastcgi_params=fwd(fastcgi_params) if fastcgi_params else "fastcgi_params",
    )


def render_nginx_ssl(www_dir, name, tld, port, logdir, templates_dir, cert, key,
                     fastcgi_port: int = 9000, fastcgi_params=None) -> str:
    """Nginx 的 HTTPS server 块（证书跟 Apache 共用一份 PEM，不用转换）。"""
    tpl = _template(templates_dir, "nginx-server-ssl.tpl", FALLBACK_NGINX_SSL)
    return render_template(
        tpl,
        name=name,
        hostname=hostname_for(name, tld),
        port=port,
        docroot=fwd(Path(www_dir) / name),
        logdir=fwd(logdir),
        cert=fwd(cert),
        key=fwd(key),
        fastcgi_port=fastcgi_port,
        fastcgi_params=fwd(fastcgi_params) if fastcgi_params else "fastcgi_params",
    )


FALLBACK_NGINX_DEFAULT = """# 喵盒默认站点：未知域名（localhost / 127.0.0.1）也能看到东西
server {
    listen       @port@ default_server;
    server_name  localhost _;
    root         "@www@";
    index        index.html index.htm index.php;

    location / {
        try_files $uri $uri/ =404;
    }

    location ~ \\.php$ {
        fastcgi_pass   127.0.0.1:@fastcgi_port@;
        fastcgi_index  index.php;
        include        "@fastcgi_params@";
        fastcgi_param  SCRIPT_FILENAME  $document_root$fastcgi_script_name;
    }
}
"""


def render_nginx_default(www_dir, port, fastcgi_port: int = 9000,
                         fastcgi_params=None) -> str:
    return render_template(
        FALLBACK_NGINX_DEFAULT,
        port=port,
        www=fwd(www_dir),
        fastcgi_port=fastcgi_port,
        fastcgi_params=fwd(fastcgi_params) if fastcgi_params else "fastcgi_params",
    )


FALLBACK_NGINX_SSL = """server {
    listen       @port@ ssl;
    server_name  @hostname@ *.@hostname@;
    root         "@docroot@";
    index        index.html index.htm index.php;

    ssl_certificate      "@cert@";
    ssl_certificate_key  "@key@";
    ssl_protocols        TLSv1.2 TLSv1.3;
    ssl_session_cache    shared:MEOWSSL:10m;

    location / {
        try_files $uri $uri/ /index.php?$query_string;
    }

    location ~ \\.php$ {
        fastcgi_pass   127.0.0.1:@fastcgi_port@;
        fastcgi_index  index.php;
        include        "@fastcgi_params@";
        fastcgi_param  SCRIPT_FILENAME  $document_root$fastcgi_script_name;
    }

    access_log "@logdir@/site-@name@-ssl-access.log" meow;
    error_log  "@logdir@/site-@name@-ssl-error.log"  warn;
}
"""
