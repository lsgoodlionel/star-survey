#!/usr/bin/env bash
# Shared setup for the isolated test stack. Source it after setting:
#   REPO_ROOT, TEST_DB (mysql|pgsql), is_fresh (true|false)
# Provides: TEST_DIR, COMPOSE, CONTAINER, DB_SERVICE; prepare_test_stack

TEST_DIR="$REPO_ROOT/platform/deploy/test"
COMPOSE=(docker compose -f "$REPO_ROOT/docker-compose.dev.yml" --profile test)
CONTAINER=survey-test-web
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
  cp "$TEST_DIR/config.$TEST_DB.php" "$TEST_DIR/.runtime/config.php"

  if $is_fresh; then
    "${COMPOSE[@]}" rm -sf "$DB_SERVICE" test-web >/dev/null
  fi
  "${COMPOSE[@]}" up -d "$DB_SERVICE" test-web >/dev/null
  # test-web does not depend on test-pg, so wait for the selected database explicitly.
  until [[ "$(docker inspect -f '{{.State.Health.Status}}' "survey-$DB_SERVICE")" == "healthy" ]]; do
    sleep 2
  done

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
    is_installed=$(docker exec survey-test-pg psql -U postgres -d limesurvey -tAc "$USERS_TABLE_QUERY")
  else
    is_installed=$(docker exec survey-test-db mariadb -uroot -proot -N -e \
      "$USERS_TABLE_QUERY AND table_schema='limesurvey'")
  fi
  if [[ "$is_installed" == "0" ]]; then
    echo "Installing LimeSurvey into the test database..."
    docker exec "$CONTAINER" php application/commands/console.php install \
      "$ADMIN_USER" "$ADMIN_PASSWORD" TravisLS no@email.com
  fi
}
