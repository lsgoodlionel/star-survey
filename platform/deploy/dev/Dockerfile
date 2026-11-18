# Local development image for LimeSurvey (PHP 8.3 + Apache)
FROM php:8.3-apache

# Diagnostic branch: fixed failure codes identify the failing command without dumping logs.
RUN ( apt-get update || exit 41 ) \
    && ( apt-get install -y --no-install-recommends \
        libpng-dev libjpeg-dev libfreetype6-dev libzip-dev libicu-dev \
        libldap2-dev libxml2-dev libonig-dev libsodium-dev libpq-dev unzip git || exit 42 ) \
    && ( docker-php-ext-configure gd --with-freetype --with-jpeg || exit 43 ) \
    && ( docker-php-ext-install -j"$(nproc)" gd zip intl ldap mysqli pdo_mysql mbstring xml sodium calendar pdo_pgsql pgsql || exit 44 ) \
    && ( pecl install xdebug || exit 45 ) \
    && ( docker-php-ext-enable xdebug || exit 46 ) \
    && ( a2enmod rewrite || exit 47 ) \
    && ( rm -rf /var/lib/apt/lists/* || exit 48 )

COPY --from=composer:2 /usr/bin/composer /usr/bin/composer
COPY php-dev.ini /usr/local/etc/php/conf.d/zz-dev.ini

# 答卷附件与运行时目录的服务器级防护（ADR 0020）。文件名带 zz- 前缀，保证它
# 在上游 php:8.3-apache 自带的 docker-php.conf（那里把 AllowOverride 设回了 All）
# 之后加载。放进服务器配置而不是 .htaccess，是为了让防护与 AllowOverride 无关。
# nginx 部署用 engine-static-guard.nginx.conf，见 ADR 0020。
COPY engine-static-guard.conf /etc/apache2/conf-enabled/zz-engine-static-guard.conf

WORKDIR /var/www/html
