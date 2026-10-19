# ============================================================
#  Meow Box (喵盒) —— 自动生成的 Apache 主配置
#  不要手改这个文件！改完配置请重跑： python meowbox.py render
# ============================================================

Define SRVROOT "@SERVERROOT@"
ServerRoot "@SERVERROOT@"

# 别把 pid 文件写到 Apache 自己的 logs/ 里去（那会和别的工具打架）
PidFile "@LOGDIR@/httpd.pid"

Listen @HTTP_PORT@
@SSL_LISTEN@

ServerAdmin admin@meowbox.local
ServerName @SERVER_NAME@

# ---------- 模块 ----------
LoadModule access_compat_module modules/mod_access_compat.so
LoadModule auth_basic_module modules/mod_auth_basic.so
LoadModule authn_core_module modules/mod_authn_core.so
LoadModule authn_file_module modules/mod_authn_file.so
LoadModule authz_core_module modules/mod_authz_core.so
LoadModule authz_groupfile_module modules/mod_authz_groupfile.so
LoadModule authz_host_module modules/mod_authz_host.so
LoadModule authz_user_module modules/mod_authz_user.so
LoadModule filter_module modules/mod_filter.so
LoadModule reqtimeout_module modules/mod_reqtimeout.so
LoadModule autoindex_module modules/mod_autoindex.so
LoadModule mime_module modules/mod_mime.so
LoadModule log_config_module modules/mod_log_config.so
LoadModule alias_module modules/mod_alias.so
LoadModule dir_module modules/mod_dir.so
LoadModule rewrite_module modules/mod_rewrite.so
LoadModule env_module modules/mod_env.so
LoadModule headers_module modules/mod_headers.so
LoadModule setenvif_module modules/mod_setenvif.so
LoadModule version_module modules/mod_version.so
@SSL_MODULES@
@PROXY_MODULES@

# ---------- 主站 ----------
<Directory />
    AllowOverride none
    Require all denied
</Directory>

DocumentRoot "@DOCROOT@"
<Directory "@DOCROOT@">
    Options Indexes FollowSymLinks Includes ExecCGI
    AllowOverride All
    Require all granted
</Directory>

<IfModule dir_module>
    DirectoryIndex index.html index.htm index.php
</IfModule>

<Files ".ht*">
    Require all denied
</Files>

ErrorLog "@LOGDIR@/apache-error.log"
LogLevel warn

<IfModule log_config_module>
    LogFormat "%h %l %u %t \"%r\" %>s %b \"%{Referer}i\" \"%{User-Agent}i\"" combined
    LogFormat "%h %l %u %t \"%r\" %>s %b" common
    CustomLog "@LOGDIR@/apache-access.log" common
</IfModule>

<IfModule mime_module>
    TypesConfig conf/mime.types
    AddType application/x-compress .Z
    AddType application/x-gzip .gz .tgz
</IfModule>

<IfModule headers_module>
    RequestHeader unset Proxy early
</IfModule>

AcceptFilter http none
AcceptFilter https none
EnableSendfile Off
EnableMMAP Off

<IfModule mpm_winnt_module>
    ThreadStackSize 8388608
</IfModule>

@SSL_SEEDS@

# ---------- 喵盒自己的配置 ----------
IncludeOptional "@ALIAS_DIR@/*.conf"
IncludeOptional "@SITES_DIR@/*.conf"
IncludeOptional "@SSL_CONF@"
Include "@MOD_PHP_CONF@"
