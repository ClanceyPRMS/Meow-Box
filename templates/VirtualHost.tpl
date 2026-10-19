<VirtualHost *:@port@>
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
