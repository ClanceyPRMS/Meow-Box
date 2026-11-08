# ============================================================
#  Meow Box (喵盒) —— 自动生成的 Apache 主配置
#  不要手改这个文件！改完配置请重跑： python meowbox.py render
# ============================================================

Define SRVROOT "@SERVERROOT@"
ServerRoot "@SERVERROOT@"

# 模块目录：Windows 是版本目录下的 modules/，Linux 是 /usr/lib/httpd/modules
Define MEOW_MODULES "@MODULES_DIR@"

# 别把 pid 文件写到 Apache 自己的 logs/ 里去（那会和别的工具打架）
PidFile "@PIDFILE@"

Listen @HTTP_PORT@
@SSL_LISTEN@

ServerAdmin admin@meowbox.local
ServerName @SERVER_NAME@

@USER_BLOCK@

# 运行时文件（scoreboard / mutex）落在喵盒的 run/ 里
# —— 不这么指，Linux 上的 Apache 会往 ServerRoot/logs 写然后起不来
@EXTRA_RUNTIME@

# ---------- 模块（MPM 必须先加载） ----------
@MPM_MODULES@
LoadModule access_compat_module ${MEOW_MODULES}/mod_access_compat.so
LoadModule auth_basic_module ${MEOW_MODULES}/mod_auth_basic.so
LoadModule authn_core_module ${MEOW_MODULES}/mod_authn_core.so
LoadModule authn_file_module ${MEOW_MODULES}/mod_authn_file.so
LoadModule authz_core_module ${MEOW_MODULES}/mod_authz_core.so
LoadModule authz_groupfile_module ${MEOW_MODULES}/mod_authz_groupfile.so
LoadModule authz_host_module ${MEOW_MODULES}/mod_authz_host.so
LoadModule authz_user_module ${MEOW_MODULES}/mod_authz_user.so
LoadModule filter_module ${MEOW_MODULES}/mod_filter.so
LoadModule reqtimeout_module ${MEOW_MODULES}/mod_reqtimeout.so
LoadModule autoindex_module ${MEOW_MODULES}/mod_autoindex.so
LoadModule mime_module ${MEOW_MODULES}/mod_mime.so
LoadModule log_config_module ${MEOW_MODULES}/mod_log_config.so
LoadModule alias_module ${MEOW_MODULES}/mod_alias.so
LoadModule dir_module ${MEOW_MODULES}/mod_dir.so
LoadModule rewrite_module ${MEOW_MODULES}/mod_rewrite.so
LoadModule env_module ${MEOW_MODULES}/mod_env.so
LoadModule headers_module ${MEOW_MODULES}/mod_headers.so
LoadModule setenvif_module ${MEOW_MODULES}/mod_setenvif.so
LoadModule version_module ${MEOW_MODULES}/mod_version.so
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
    TypesConfig "@MIME_TYPES@"
    AddType application/x-compress .Z
    AddType application/x-gzip .gz .tgz
</IfModule>

<IfModule headers_module>
    RequestHeader unset Proxy early
</IfModule>

EnableSendfile Off
EnableMMAP Off

# Windows 专属：accept 过滤器和 winnt MPM 的线程栈。
# 包在 IfModule 里，Linux 上这整块会被跳过（不然会刷一排「不支持」警告）。
<IfModule mpm_winnt_module>
    AcceptFilter http none
    AcceptFilter https none
    ThreadStackSize 8388608
</IfModule>

@SSL_SEEDS@

# ---------- 喵盒自己的配置 ----------
IncludeOptional "@ALIAS_DIR@/*.conf"
IncludeOptional "@SITES_DIR@/*.conf"
IncludeOptional "@SSL_CONF@"
Include "@MOD_PHP_CONF@"
