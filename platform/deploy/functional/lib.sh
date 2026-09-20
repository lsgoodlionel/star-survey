#!/usr/bin/env bash
# survey-functional 栈的共享逻辑。使用前需设置 REPO_ROOT。
# 对外提供：COMPOSE、CONTAINER、ADMIN_USER、ADMIN_PASSWORD、DOMAIN、
#           WEBDRIVER_HOST、prepare_functional_stack、wait_for_selenium、
#           ensure_free_selenium_slot、activate_mjy_plugins、deactivate_mjy_plugins

FUNCTIONAL_DIR="$REPO_ROOT/platform/deploy/functional"
COMPOSE=(docker compose -f "$FUNCTIONAL_DIR/docker-compose.functional.yml")
CONTAINER=survey-functional-web
ADMIN_USER=admin
ADMIN_PASSWORD=password

# selenium 与 web 共享网络命名空间，三个角色互为 localhost，与 CI 的 runner 一致。
# 对应 TestBaseClassWeb::$domain 与 functional.yml 里的 DOMAIN=localhost。
DOMAIN=localhost
# phpunit 访问 WebDriver 用的主机名；端口由 TestBaseClassWeb::$webPort 写死为 4444。
WEBDRIVER_HOST=localhost
# Selenium 4 的 W3C 端点在根路径上，不是 Selenium 3 的 /wd/hub。
WEBDRIVER_SUFFIX=none

# 等 Selenium 节点变为可用，最多 WAIT_SELENIUM_SECONDS 秒。
WAIT_SELENIUM_SECONDS=120

# 判断「节点在不在」只能看 availability，不能看 /status 里的 "ready"：
# ready 表达的是「还有空槽位」，槽位被占满时它是 false，但节点其实好好的。
selenium_status() {
  docker exec survey-functional-selenium \
    curl -sf http://localhost:4444/status 2>/dev/null || true
}

wait_for_selenium() {
  local waited=0
  until selenium_status | grep -q '"availability": *"UP"'; do
    if (( waited >= WAIT_SELENIUM_SECONDS )); then
      echo "Selenium 节点在 ${WAIT_SELENIUM_SECONDS}s 内没有起来" >&2
      docker logs --tail 30 survey-functional-selenium >&2
      return 1
    fi
    sleep 2
    waited=$((waited + 2))
  done
}

# 上一轮如果在 quit() 上抛了 "Failed to decode response from marionette"，那个会话
# 会一直占着槽位直到网格自己超时回收（默认 300s）。槽位被占满期间新会话只能排队，
# 表现出来就是一整批用例莫名其妙地 TimeoutException / POST /session 超时。
# 所以每轮开跑前确认至少有一个空槽位，等不到就重启 selenium 容器
# （只影响我们这一栈，代价几秒钟）。
WAIT_FREE_SLOT_SECONDS=30

ensure_free_selenium_slot() {
  local waited=0
  while ! selenium_status | grep -q '"ready": *true'; do
    if (( waited >= WAIT_FREE_SLOT_SECONDS )); then
      echo "Selenium 槽位仍被上一轮的残留会话占着，重启 selenium 容器"
      "${COMPOSE[@]}" restart selenium >/dev/null
      wait_for_selenium
      return 0
    fi
    sleep 2
    waited=$((waited + 2))
  done
}

prepare_functional_stack() {
  mkdir -p "$FUNCTIONAL_DIR/.runtime"

  if $is_fresh; then
    "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  fi
  "${COMPOSE[@]}" up -d >/dev/null
  until [[ "$(docker inspect -f '{{.State.Health.Status}}' survey-functional-db)" == "healthy" ]]; do
    sleep 2
  done
  wait_for_selenium
  ensure_free_selenium_slot

  # application/config 是命名卷，每轮都从只读的仓库副本重新灌一遍，再覆盖 config.php。
  # 这样 InstallationControllerTest 删掉 config.php 之后，下一轮仍然从干净状态开始，
  # 而且全程不会写到仓库里的 application/config（那是引擎源码，属于只读区）。
  docker exec "$CONTAINER" sh -c '
    set -e
    cp -a /opt/engine-src/application/config/. /var/www/html/application/config/
    cp /opt/engine-src/platform/deploy/functional/config.mysql.php \
       /var/www/html/application/config/config.php
    # 引擎在管理员首次登录时生成 allowed_hosts.php，之后就按白名单校验 Host。
    # CI 的全新检出里没有这个文件；留着会让依赖 URL 的用例失败。
    rm -f /var/www/html/application/config/allowed_hosts.php
    # upload 也是命名卷，同样每轮从仓库的干净副本重灌，保证主题类用例可重复。
    rm -rf /var/www/html/upload/*
    cp -a /opt/engine-src/upload/. /var/www/html/upload/
    chmod -R 777 /var/www/html/application/config /var/www/html/upload
  '

  # phpunit 拒绝在没有这个标记文件时运行（防止误在生产上跑）。
  touch "$REPO_ROOT/enabletests"

  # tmp 是命名卷，和仓库里的 tmp 不是同一份；清掉 schema 缓存并放开权限，
  # 让 Apache（www-data）、phpunit（root）和 Selenium 容器里的 seluser 都能写。
  #
  # sudo 垫片：InstallationControllerTest 在跑安装向导之前会执行
  #   exec("sudo chmod -R 777 ./tmp")
  # 来兜住安装器的 tmp 可写性预检。CI 的 runner 上有 sudo，我们的容器里没有，
  # 这行会静默失败，于是预检把 /tmp directory 标红、向导不渲染 #ls-next 按钮。
  # phpunit 本来就是 root，垫片直接 exec 后面的命令即可，语义与 CI 一致。
  docker exec "$CONTAINER" sh -c '
    set -e
    if ! command -v sudo >/dev/null 2>&1; then
      printf "#!/bin/sh\nexec \"\$@\"\n" > /usr/local/bin/sudo
      chmod 755 /usr/local/bin/sudo
    fi
    rm -rf tmp/runtime/cache
    mkdir -p tmp/runtime tmp/assets tmp/upload tests/tmp/runtime tests/tmp/screenshots
    chmod -R 777 tmp tests/tmp
  '

  # InstallationControllerTest 会 DROP DATABASE，失败时不会再建回来；
  # 后面的套件会直接在 bootstrap 阶段连不上库。每轮先确保库存在。
  docker exec survey-functional-db mariadb -uroot -proot -e \
    "CREATE DATABASE IF NOT EXISTS limesurvey CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

  local users_table_query="SELECT COUNT(*) FROM information_schema.tables \
WHERE table_name='lime_users' AND table_schema='limesurvey'"
  local is_installed
  is_installed=$(docker exec survey-functional-db mariadb -uroot -proot -N -e "$users_table_query")
  if [[ "$is_installed" == "0" ]]; then
    echo "正在把 LimeSurvey 安装进 functional 测试库..."
    docker exec "$CONTAINER" php application/commands/console.php install \
      "$ADMIN_USER" "$ADMIN_PASSWORD" TravisLS no@email.com
  fi
}

# 自研插件在全新库里根本不会出现在 lime_plugins 表里（引擎只在插件管理器扫描后
# 才登记），所以基线跑的是「我们的插件完全不参与」。要判断失败是否由我们引起，
# 需要把它们显式激活后重跑同一批套件做对照。
# 直接写 lime_plugins 与 platform/tests/e2e/fault_injection.php 的做法一致。
MJY_PLUGINS=(MjyPlatformBridge MjyQuestionExtensions MjyRuntimePolicy)

activate_mjy_plugins() {
  # lime_plugins.name 上没有唯一索引，先删后插，避免重复行。
  local sql="DELETE FROM lime_plugins WHERE name LIKE 'Mjy%';"
  for plugin in "${MJY_PLUGINS[@]}"; do
    sql+="INSERT INTO lime_plugins (name, plugin_type, active, priority, version, load_error) \
VALUES ('$plugin', 'user', 1, 0, '0.1.0', 0);"
  done
  docker exec survey-functional-db mariadb -uroot -proot limesurvey -e "$sql"
  echo "已激活自研插件: ${MJY_PLUGINS[*]}"
  docker exec survey-functional-db mariadb -uroot -proot limesurvey -N -e \
    "SELECT name, active, load_error FROM lime_plugins WHERE name LIKE 'Mjy%'"
}

deactivate_mjy_plugins() {
  docker exec survey-functional-db mariadb -uroot -proot limesurvey -e \
    "DELETE FROM lime_plugins WHERE name LIKE 'Mjy%';"
  echo "已移除自研插件的 lime_plugins 记录（回到基线状态）"
}
