"""扫描 www/ → 生成虚拟主机配置片段。"""
from __future__ import annotations

from pathlib import Path

from .util import fwd, render_template

FALLBACK_APACHE = """<VirtualHost *:{port}>
    DocumentRoot "{docroot}"
    ServerName {hostname}
    ServerAlias *.{hostname}
    <Directory "{docroot}">
        AllowOverride All
        Require all granted
    </Directory>
    ErrorLog "{logdir}/site-{name}-error.log"
    CustomLog "{logdir}/site-{name}-access.log" common
</VirtualHost>
"""

FALLBACK_NGINX = """server {{
    listen {port};
    server_name {hostname} *.{hostname};
    root "{docroot}";
    index index.html index.php;

    location / {{
        try_files $uri $uri/ /index.php?$query_string;
    }}
}}
"""

FALLBACK_APACHE_SSL = """<VirtualHost *:{port}>
    DocumentRoot "{docroot}"
    ServerName {hostname}
    ServerAlias *.{hostname}
    SSLEngine on
    SSLCertificateFile    "{cert}"
    SSLCertificateKeyFile "{key}"
    <Directory "{docroot}">
        AllowOverride All
        Require all granted
    </Directory>
    ErrorLog "{logdir}/site-{name}-ssl-error.log"
    CustomLog "{logdir}/site-{name}-ssl-access.log" common
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


def _template(templates_dir, filename: str, fallback: str) -> str:
    tpl = Path(templates_dir) / filename
    if tpl.is_file():
        return tpl.read_text(encoding="utf-8")
    return fallback


def render_apache(www_dir, name, tld, port, logdir, templates_dir) -> str:
    tpl = _template(templates_dir, "VirtualHost.tpl", FALLBACK_APACHE)
    return render_template(
        tpl,
        name=name,
        hostname=hostname_for(name, tld),
        port=port,
        docroot=fwd(Path(www_dir) / name),
        logdir=fwd(logdir),
    )


def render_apache_ssl(www_dir, name, tld, port, logdir, templates_dir, cert, key) -> str:
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
