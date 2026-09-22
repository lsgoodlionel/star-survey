#!/usr/bin/env bash
# Shared setup for the isolated test stack. Source it after setting:
#   REPO_ROOT, TEST_DB (mysql|pgsql), is_fresh (true|false)
# Provides: TEST_DIR, COMPOSE, CONTAINER, DB_SERVICE, TEST_PREFIX; prepare_test_stack,
#           enable_remote_control, db_query
#
# SURVEY_TEST_PREFIX (default "survey") names the stack's containers
# (<prefix>-test-web, <prefix>-test-db, <prefix>-test-pg), so parallel lanes can
# run isolated stacks. Set COMPOSE_PROJECT_NAME to a matching value as well.

TEST_DIR="$REPO_ROOT/platform/deploy/test"
COMPOSE=(docker compose -f "$REPO_ROOT/docker-compose.dev.yml" --profile test)
TEST_PREFIX="${SURVEY_TEST_PREFIX:-survey}"
CONTAINER="$TEST_PREFIX-test-web"
ADMIN_USER=admin
ADMIN_PASSWORD=password

case "$TEST_DB" in
  mysql) DB_SERVICE=test-db ;;
  pgsql) DB_SERVICE=test-pg ;;
  *) echo "Unsupported TEST_DB: $TEST_DB (use mysql or pgsql)" >&2; exit 1 ;;
esac

prepare_test_stack() {
  # Fresh copy of the config template: tests may rewrite config.php.
  mkdir -p "$TEST_DIR/.runtime"
  local config_changed=false
  if ! cmp -s "$TEST_DIR/config.$TEST_DB.php" "$TEST_DIR/.runtime/config.php"; then
    config_changed=true
  fi
  cp "$TEST_DIR/config.$TEST_DB.php" "$TEST_DIR/.runtime/config.php"

  if $is_fresh; then
    "${COMPOSE[@]}" rm -sf "$DB_SERVICE" test-web >/dev/null
  fi
  "${COMPOSE[@]}" up -d "$DB_SERVICE" test-web >/dev/null
  # 切换 TEST_DB 时 config.php 被原地替换，而 PHP opcache 按时间戳隔几秒才重新校验：
  # 紧接着的请求可能仍用上一种数据库的配置（问卷发布到另一个库）。配置变了就重启 web 容器。
  if $config_changed && ! $is_fresh; then
    "${COMPOSE[@]}" restart test-web >/dev/null
  fi
  # test-web does not depend on test-pg, so wait for the selected database explicitly.
  until [[ "$(docker inspect -f '{{.State.Health.Status}}' "$TEST_PREFIX-$DB_SERVICE")" == "healthy" ]]; do
    sleep 2
  done

  # 引擎会在管理员首次登录时生成 application/config/allowed_hosts.php，
  # 而该文件一旦存在就会按白名单校验主机名；CI 的全新检出里没有它，
  # 留着会让 3 个 URL 相关用例失败。测试前移除，下次登录会自动重新生成。
  rm -f "$REPO_ROOT/application/config/allowed_hosts.php"

  # phpunit refuses to run without this marker (guard against production runs).
  touch "$REPO_ROOT/enabletests"

  # The tmp volume is shared by the mysql and pgsql runs; drop the schema/data
  # cache so switching TEST_DB never serves stale table metadata.
  docker exec "$CONTAINER" sh -c '
    set -e
    rm -rf tmp/runtime/cache
    mkdir -p tmp/runtime tmp/assets tmp/upload tests/tmp/runtime
    chmod -R 777 tmp tests/tmp upload
  '

  USERS_TABLE_QUERY="SELECT COUNT(*) FROM information_schema.tables WHERE table_name='lime_users'"
  if [[ "$TEST_DB" == "pgsql" ]]; then
    is_installed=$(docker exec "$TEST_PREFIX-test-pg" psql -U postgres -d limesurvey -tAc "$USERS_TABLE_QUERY")
  else
    is_installed=$(docker exec "$TEST_PREFIX-test-db" mariadb -uroot -proot -N -e \
      "$USERS_TABLE_QUERY AND table_schema='limesurvey'")
  fi
  if [[ "$is_installed" == "0" ]]; then
    echo "Installing LimeSurvey into the test database..."
    docker exec "$CONTAINER" php application/commands/console.php install \
      "$ADMIN_USER" "$ADMIN_PASSWORD" TravisLS no@email.com
  fi
}

# Run one SQL statement against the selected test database; prints bare rows.
db_query() {
  if [[ "$TEST_DB" == "pgsql" ]]; then
    docker exec "$TEST_PREFIX-test-pg" psql -U postgres -d limesurvey -tAq -c "$1"
  else
    docker exec "$TEST_PREFIX-test-db" mariadb -uroot -proot limesurvey -N -e "$1"
  fi
}

# Turn on the JSON-RPC RemoteControl interface (off by default after install).
enable_remote_control() {
  db_query "DELETE FROM lime_settings_global WHERE stg_name = 'RPCInterface'" >/dev/null
  db_query "INSERT INTO lime_settings_global (stg_name, stg_value) VALUES ('RPCInterface', 'json')" >/dev/null
  # settings_global is cached per request; drop the cache so the very first
  # RPC call already sees the interface enabled.
  docker exec "$CONTAINER" rm -rf tmp/runtime/cache
}
