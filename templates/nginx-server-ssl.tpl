server {
    listen       @port@ ssl;
    server_name  @hostname@ *.@hostname@;
    root         "@docroot@";
    index        index.html index.htm index.php;

    ssl_certificate      "@cert@";
    ssl_certificate_key  "@key@";
    ssl_protocols        TLSv1.2 TLSv1.3;
    ssl_session_cache    shared:MEOWSSL:10m;
    ssl_session_timeout  10m;
    ssl_ciphers          HIGH:!aNULL:!MD5:!3DES;

    location / {
        try_files $uri $uri/ /index.php?$query_string;
    }

    location ~ \.php$ {
        fastcgi_pass   127.0.0.1:@fastcgi_port@;
        fastcgi_index  index.php;
        include        "@fastcgi_params@";
        fastcgi_param  SCRIPT_FILENAME  $document_root$fastcgi_script_name;
    }

    location ~ /\. {
        deny all;
    }

    access_log "@logdir@/site-@name@-ssl-access.log" meow;
    error_log  "@logdir@/site-@name@-ssl-error.log"  warn;
}
