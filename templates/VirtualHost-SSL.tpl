<VirtualHost *:@port@>
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

    ErrorLog "@logdir@/site-@name@-ssl-error.log"
    CustomLog "@logdir@/site-@name@-ssl-access.log" common
</VirtualHost>
