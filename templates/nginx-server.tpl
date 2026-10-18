server {
    listen       @port@;
    server_name  @hostname@ *.@hostname@;
    root         "@docroot@";
    index        index.html index.htm index.php;

    location / {
        try_files $uri $uri/ /index.php?$query_string;
    }

    # PHP 交给 php-cgi（FastCGI）—— 端口在 meowbox.config.json 的 php.fastcgi_port
    location ~ \.php$ {
        fastcgi_pass   127.0.0.1:@fastcgi_port@;
        fastcgi_index  index.php;
        include        "@fastcgi_params@";
        fastcgi_param  SCRIPT_FILENAME  $document_root$fastcgi_script_name;
    }

    # .htaccess / .git 之类的点文件不许外部访问
    location ~ /\. {
        deny all;
    }

    access_log "@logdir@/site-@name@-access.log" meow;
    error_log  "@logdir@/site-@name@-error.log"  warn;
}
