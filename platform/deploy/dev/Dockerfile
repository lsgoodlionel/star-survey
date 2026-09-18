# Local development image for LimeSurvey (PHP 8.3 + Apache)
FROM php:8.3-apache

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpng-dev libjpeg-dev libfreetype6-dev libzip-dev libicu-dev \
        libldap2-dev libxml2-dev libonig-dev libsodium-dev libpq-dev unzip git \
    && docker-php-ext-configure gd --with-freetype --with-jpeg \
    && docker-php-ext-install -j"$(nproc)" gd zip intl ldap mysqli pdo_mysql mbstring xml sodium calendar pdo_pgsql pgsql \
    && pecl install xdebug && docker-php-ext-enable xdebug \
    && a2enmod rewrite \
    && rm -rf /var/lib/apt/lists/*

COPY --from=composer:2 /usr/bin/composer /usr/bin/composer
COPY php-dev.ini /usr/local/etc/php/conf.d/zz-dev.ini

WORKDIR /var/www/html
