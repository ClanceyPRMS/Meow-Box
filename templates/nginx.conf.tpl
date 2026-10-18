# ============================================================
#  Meow Box (喵盒) —— 自动生成的 Nginx 主配置
#  不要手改这个文件！改完配置请重跑： python meowbox.py render
# ============================================================

# 喵盒同一时间只跑一个 Web 服务器，端口从 meowbox.config.json 的 web.http_port 来
worker_processes  @WORKERS@;
error_log  "@LOGDIR@/nginx-error.log" warn;
pid        "@RUN@/nginx-master.pid";

events {
    worker_connections  1024;
}

http {
    # mime.types 直接用 Nginx 自己 conf/ 下的那份（provider 提供的）
    include       "@MIME_TYPES@";
    default_type  application/octet-stream;
    charset       utf-8;

    log_format  meow  '$remote_addr - $remote_user [$time_local] "$request" '
                      '$status $body_bytes_sent "$http_referer" "$http_user_agent"';
    access_log  "@LOGDIR@/nginx-access.log"  meow;

    client_max_body_size  64m;
    keepalive_timeout     65;
    server_tokens         off;

    # 粗活目录全部落在喵盒里 —— 不加这几行，Nginx 会把临时文件写进 provider 的安装目录
    client_body_temp_path  "@TEMP@/client_body";
    proxy_temp_path        "@TEMP@/proxy";
    fastcgi_temp_path      "@TEMP@/fastcgi";
    uwsgi_temp_path        "@TEMP@/uwsgi";
    scgi_temp_path         "@TEMP@/scgi";

    gzip              on;
    gzip_min_length   1k;
    gzip_comp_level   5;
    gzip_vary         on;
    gzip_types        text/plain text/css text/xml application/json
                      application/javascript application/xml image/svg+xml;

    # ---------- 喵盒自己的站点 ----------
    include "@SITES_DIR@/*.conf";
}
