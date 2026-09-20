#!/usr/bin/env bash
# P0-00.8 私有化交付栈的共享定义。调用方需先设置 REPO_ROOT。
# 提供：COMPOSE、实例常量、灌码与安装函数。

PRIVATE_DIR="$REPO_ROOT/platform/deploy/private"
COMPOSE_FILES=(-f "$PRIVATE_DIR/docker-compose.private.yml")
if [[ "${PRIVATE_EXPOSE:-0}" == "1" ]]; then
  COMPOSE_FILES+=(-f "$PRIVATE_DIR/docker-compose.expose.yml")
fi
COMPOSE=(docker compose -p survey-private "${COMPOSE_FILES[@]}")

WEB=survey-private-web
DB=survey-private-db
DB_NAME=survey_private
DB_USER=survey_private
DB_PASSWORD=private-db-pass
ADMIN_USER=admin
ADMIN_PASSWORD=private-admin-secret
ADMIN_NAME="Private Admin"
ADMIN_EMAIL=admin@private.invalid
WEB_PORT="${PRIVATE_WEB_PORT:-8094}"

# 备份件里三类内容的固定名字，backup.sh 与 restore.sh 必须一致。
BACKUP_DB_FILE=db.sql.gz
BACKUP_UPLOAD_FILE=upload.tar.gz
BACKUP_CONFIG_FILE=config.tar.gz
BACKUP_MANIFEST_FILE=manifest.txt

# 灌码：把仓库工作树复制进本实例独占的 engine-code 卷。
# 这一步刻意不用绑定挂载——ADR 0002 F-01～F-04 的四项未拒绝全部来自共享代码树，
# 私有化交付里每个实例必须持有自己的一份。
seed_engine_code() {
  echo "灌入引擎代码副本（排除 .git 与本地 .runtime）..." >&2
  # --no-mac-metadata：不产出 AppleDouble 扩展头，容器里的 GNU tar 会对它刷警告
  tar --no-mac-metadata -cf - -C "$REPO_ROOT" \
    --exclude='./.git' \
    --exclude='./platform/deploy/tenancy/.runtime' \
    --exclude='./platform/deploy/test/.runtime' \
    --exclude='./platform/deploy/private/.runtime' \
    . | docker exec -i "$WEB" tar -xf - -C /var/www/html
  docker exec "$WEB" sh -c '
    set -e
    mkdir -p tmp/runtime tmp/assets tmp/upload upload/surveys
    chmod -R 777 tmp upload
    chown -R www-data:www-data /var/www/html/tmp /var/www/html/upload
  '
}

# 本实例独占的配置。三个文件都落在 engine-code 卷里，不回写仓库。
install_engine_config() {
  docker exec -i "$WEB" sh -c 'cat > /var/www/html/application/config/config.php' \
    < "$PRIVATE_DIR/config.private.php"
  docker exec -i "$WEB" sh -c 'cat > /var/www/html/application/config/allowed_hosts.php' \
    < "$PRIVATE_DIR/allowed_hosts.private.php"
}

engine_is_installed() {
  docker exec "$DB" mariadb "-u$DB_USER" "-p$DB_PASSWORD" -N -e \
    "SELECT COUNT(*) FROM information_schema.tables
     WHERE table_name='lime_users' AND table_schema='$DB_NAME'" 2>/dev/null | tr -d '[:space:]'
}

wait_for_db() {
  local tries=0
  until [[ "$(docker inspect -f '{{.State.Health.Status}}' "$DB" 2>/dev/null)" == "healthy" ]]; do
    tries=$((tries + 1))
    if [[ $tries -gt 60 ]]; then
      echo "数据库 $DB 在 120 秒内没有变healthy，放弃。" >&2
      return 1
    fi
    sleep 2
  done
}

# 交付加固：关掉 private-deployment.md §2.8 里 config.php 管不到的那几条出网通道。
# 全部改在本实例独占的代码副本上（engine-code 卷），仓库里的 application/** 不动。
#
# 分两部分：
#   1) 数据库里停掉两个核心更新插件——它们没有任何配置开关；
#   2) 代码补丁——三处前端外联在引擎里是硬编码的，配置管不住。
harden_engine_code() {
  echo "应用交付加固（停更新插件 ＋ 打三处前端外联补丁）..." >&2

  # UpdateCheck / ComfortUpdateChecker 只能靠 lime_plugins.active 关。
  docker exec "$DB" mariadb "-u$DB_USER" "-p$DB_PASSWORD" "$DB_NAME" -e \
    "UPDATE lime_plugins SET active = 0 WHERE name IN ('UpdateCheck','ComfortUpdateChecker');" 2>/dev/null

  docker exec "$WEB" sh -c '
    set -e
    cd /var/www/html

    # 全树唯一一处扩展更新源。必须清掉：更新器会遍历“全部已安装扩展”，
    # 包括未启用的，所以停用 TwoFactorAdminLogin 本身并不能阻止它外联。
    sed -i "s#<source>https://comfortupdate.limesurvey.org[^<]*</source>#<source></source>#" \
      application/core/plugins/TwoFactorAdminLogin/config.xml

    # 地图题的三个第三方域名：引擎里是硬编码的，没有配置开关。
    # 这里指向内网占位地址；真实交付要换成客户内网的瓦片/地名服务，
    # 或者干脆在平台侧禁用地图题型。
    sed -i "s#//www.openlayers.org/api/OpenLayers.js#/assets/scripts/openlayers-local.js#" \
      application/helpers/qanda_helper.php
    sed -i "s#//{s}.tile.openstreetmap.org/{z}/{x}/{y}#/tiles/{z}/{x}/{y}#; \
            s#api.geonames.org#geonames.internal#; \
            s#secure.geonames.org#geonames.internal#" \
      assets/scripts/map.js

    # 唯一一处 CSS 里的 Google Fonts @import。
    sed -i "s#@import url(https://fonts.googleapis.com/css2[^)]*);##" \
      themes/survey/bootswatch/css/variations/united.min.css
  '
}

prepare_private_stack() {
  "${COMPOSE[@]}" up -d >&2
  wait_for_db
  if ! docker exec "$WEB" test -f /var/www/html/index.php 2>/dev/null; then
    seed_engine_code
  fi
  install_engine_config
  if [[ "$(engine_is_installed)" != "1" ]]; then
    echo "安装引擎实例..." >&2
    docker exec "$WEB" php application/commands/console.php install \
      "$ADMIN_USER" "$ADMIN_PASSWORD" "$ADMIN_NAME" "$ADMIN_EMAIL" >&2
  fi
  harden_engine_code
}
