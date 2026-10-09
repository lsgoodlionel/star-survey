#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
DEMO_DIR="$REPO_ROOT/platform/deploy/demo"
RUNTIME_DIR="$DEMO_DIR/.runtime"
RUNTIME_ENV="$RUNTIME_DIR/runtime.env"
ACCESS_FILE="$RUNTIME_DIR/access.json"
METADATA_FILE="$RUNTIME_DIR/demo-metadata.json"
EVENT_SECRET_FILE="$RUNTIME_DIR/engine-event-secret"
ENGINES_FILE="$RUNTIME_DIR/engines.json"
COMPOSE_FILE="$DEMO_DIR/admin-web-demo.compose.yml"
SUPPORT="$DEMO_DIR/admin_web_demo.py"
PROJECT=adminweb-demo
PLATFORM_JAR="$REPO_ROOT/platform/services/business/target/business-0.1.0-SNAPSHOT.jar"
JAVA_IMAGE=maven:3.9-eclipse-temurin-21
COMPOSE=(docker compose -p "$PROJECT" -f "$COMPOSE_FILE")

fail() {
  echo "admin-web demo: $*" >&2
  exit 1
}

random_secret() {
  python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
}

private_mode() {
  if stat -f '%Lp' "$1" >/dev/null 2>&1; then
    stat -f '%Lp' "$1"
  else
    stat -c '%a' "$1"
  fi
}

require_private() {
  [[ -f "$1" && "$(private_mode "$1")" == "600" ]] || fail "private runtime file permissions are invalid"
}

run_quiet() {
  umask 077
  mkdir -p "$RUNTIME_DIR"
  local log="$RUNTIME_DIR/lifecycle.log"
  local capture
  : >"$log"
  chmod 600 "$log"
  capture="$(mktemp "$RUNTIME_DIR/.lifecycle-output.XXXXXX")"
  if ! "$@" >"$capture" 2>&1; then
    printf '%s failed; command output was discarded\n' "${RUN_QUIET_LABEL:-lifecycle command}" >"$log"
    rm -f "$capture"
    fail "${RUN_QUIET_LABEL:-lifecycle command} failed; output was discarded to protect credentials"
  fi
  rm -f "$capture"
}

parse_runtime_env() {
  local line key value seen='|'
  PLATFORM_JWT_HMAC_SECRET=''
  PLATFORM_ENGINE_EVENTS_SECRET=''
  PUBGW_SHARED_SECRET=''
  DEMO_ENGINE_ADMIN_USER=''
  DEMO_ENGINE_ADMIN_PASSWORD=''
  ENGINE_DB_ROOT_PASSWORD=''
  PLATFORM_DB_SUPERUSER_PASSWORD=''
  PLATFORM_DB_APP_PASSWORD=''
  PLATFORM_DB_OWNER_PASSWORD=''
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^([A-Z0-9_]+)=([A-Za-z0-9_-]+)$ ]] || fail "runtime.env contains an invalid key=value line"
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    case "$key" in
      PLATFORM_JWT_HMAC_SECRET|PLATFORM_ENGINE_EVENTS_SECRET|PUBGW_SHARED_SECRET|DEMO_ENGINE_ADMIN_USER|DEMO_ENGINE_ADMIN_PASSWORD|ENGINE_DB_ROOT_PASSWORD|PLATFORM_DB_SUPERUSER_PASSWORD|PLATFORM_DB_APP_PASSWORD|PLATFORM_DB_OWNER_PASSWORD) ;;
      *) fail "runtime.env contains an unknown key" ;;
    esac
    [[ "$seen" != *"|$key|"* ]] || fail "runtime.env contains a duplicate key"
    seen+="$key|"
    printf -v "$key" '%s' "$value"
  done <"$RUNTIME_ENV"
}

validate_base_runtime_env() {
  [[ ${#PLATFORM_JWT_HMAC_SECRET} -ge 32 ]] || fail "runtime.env is missing a valid JWT secret"
  [[ ${#PLATFORM_ENGINE_EVENTS_SECRET} -ge 32 ]] || fail "runtime.env is missing a valid engine event secret"
  [[ ${#PUBGW_SHARED_SECRET} -ge 32 ]] || fail "runtime.env is missing a valid gateway secret"
  [[ ${#DEMO_ENGINE_ADMIN_PASSWORD} -ge 32 ]] || fail "runtime.env is missing a valid engine password"
  [[ "$DEMO_ENGINE_ADMIN_USER" =~ ^[a-z][a-z0-9_-]{1,39}$ ]] || fail "runtime.env contains an invalid engine username"
}

validate_runtime_env() {
  validate_base_runtime_env
  [[ ${#ENGINE_DB_ROOT_PASSWORD} -ge 32 ]] || fail "runtime.env is missing a valid engine database password"
  [[ ${#PLATFORM_DB_SUPERUSER_PASSWORD} -ge 32 ]] || fail "runtime.env is missing a valid database bootstrap password"
  [[ ${#PLATFORM_DB_APP_PASSWORD} -ge 32 ]] || fail "runtime.env is missing a valid application database password"
  [[ ${#PLATFORM_DB_OWNER_PASSWORD} -ge 32 ]] || fail "runtime.env is missing a valid owner database password"
  export PLATFORM_JWT_HMAC_SECRET PLATFORM_ENGINE_EVENTS_SECRET PUBGW_SHARED_SECRET
  export DEMO_ENGINE_ADMIN_USER DEMO_ENGINE_ADMIN_PASSWORD ENGINE_DB_ROOT_PASSWORD
  export PLATFORM_DB_SUPERUSER_PASSWORD PLATFORM_DB_APP_PASSWORD PLATFORM_DB_OWNER_PASSWORD
}

load_runtime_env() {
  parse_runtime_env
  validate_runtime_env
}

upgrade_runtime_env() {
  if [[ -n "$ENGINE_DB_ROOT_PASSWORD" && -n "$PLATFORM_DB_SUPERUSER_PASSWORD" \
      && -n "$PLATFORM_DB_APP_PASSWORD" && -n "$PLATFORM_DB_OWNER_PASSWORD" ]]; then
    return 0
  fi
  local temporary="$RUNTIME_DIR/.runtime.env.$$"
  [[ -z "$ENGINE_DB_ROOT_PASSWORD" ]] && ENGINE_DB_ROOT_PASSWORD="$(random_secret)"
  [[ -z "$PLATFORM_DB_SUPERUSER_PASSWORD" ]] && PLATFORM_DB_SUPERUSER_PASSWORD="$(random_secret)"
  [[ -z "$PLATFORM_DB_APP_PASSWORD" ]] && PLATFORM_DB_APP_PASSWORD="$(random_secret)"
  [[ -z "$PLATFORM_DB_OWNER_PASSWORD" ]] && PLATFORM_DB_OWNER_PASSWORD="$(random_secret)"
  {
    printf 'PLATFORM_JWT_HMAC_SECRET=%s\n' "$PLATFORM_JWT_HMAC_SECRET"
    printf 'PLATFORM_ENGINE_EVENTS_SECRET=%s\n' "$PLATFORM_ENGINE_EVENTS_SECRET"
    printf 'PUBGW_SHARED_SECRET=%s\n' "$PUBGW_SHARED_SECRET"
    printf 'DEMO_ENGINE_ADMIN_USER=%s\n' "$DEMO_ENGINE_ADMIN_USER"
    printf 'DEMO_ENGINE_ADMIN_PASSWORD=%s\n' "$DEMO_ENGINE_ADMIN_PASSWORD"
    printf 'ENGINE_DB_ROOT_PASSWORD=%s\n' "$ENGINE_DB_ROOT_PASSWORD"
    printf 'PLATFORM_DB_SUPERUSER_PASSWORD=%s\n' "$PLATFORM_DB_SUPERUSER_PASSWORD"
    printf 'PLATFORM_DB_APP_PASSWORD=%s\n' "$PLATFORM_DB_APP_PASSWORD"
    printf 'PLATFORM_DB_OWNER_PASSWORD=%s\n' "$PLATFORM_DB_OWNER_PASSWORD"
  } >"$temporary"
  chmod 600 "$temporary"
  mv "$temporary" "$RUNTIME_ENV"
}

ensure_runtime() {
  umask 077
  if [[ -f "$RUNTIME_ENV" ]]; then
    require_private "$RUNTIME_ENV"
    parse_runtime_env
    validate_base_runtime_env
    upgrade_runtime_env
    validate_runtime_env
  else
    mkdir -p "$RUNTIME_DIR"
    chmod 700 "$RUNTIME_DIR"
    local temporary="$RUNTIME_DIR/.runtime.env.$$"
    {
      printf 'PLATFORM_JWT_HMAC_SECRET=%s\n' "$(random_secret)"
      printf 'PLATFORM_ENGINE_EVENTS_SECRET=%s\n' "$(random_secret)"
      printf 'PUBGW_SHARED_SECRET=%s\n' "$(random_secret)"
      printf 'DEMO_ENGINE_ADMIN_USER=admin\n'
      printf 'DEMO_ENGINE_ADMIN_PASSWORD=%s\n' "$(random_secret)"
      printf 'ENGINE_DB_ROOT_PASSWORD=%s\n' "$(random_secret)"
      printf 'PLATFORM_DB_SUPERUSER_PASSWORD=%s\n' "$(random_secret)"
      printf 'PLATFORM_DB_APP_PASSWORD=%s\n' "$(random_secret)"
      printf 'PLATFORM_DB_OWNER_PASSWORD=%s\n' "$(random_secret)"
    } >"$temporary"
    chmod 600 "$temporary"
    mv "$temporary" "$RUNTIME_ENV"
    load_runtime_env
  fi
  chmod 700 "$RUNTIME_DIR"
  export PUBGW_ENGINE_ADMIN_WEB_PASSWORD="$DEMO_ENGINE_ADMIN_PASSWORD"
  export ADMIN_WEB_DEMO_PLATFORM_JAR="$PLATFORM_JAR"
  export ADMIN_WEB_DEMO_ENGINES_FILE="$ENGINES_FILE"
  export MJY_PLATFORM_EVENTS_SECRET=""
  if [[ -f "$EVENT_SECRET_FILE" ]]; then
    require_private "$EVENT_SECRET_FILE"
    MJY_PLATFORM_EVENTS_SECRET="$(<"$EVENT_SECRET_FILE")"
    export MJY_PLATFORM_EVENTS_SECRET
  fi
  python3 - "$ENGINES_FILE" <<'PY'
import json
import os
import stat
import sys

path = sys.argv[1]
descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
    json.dump({
        "admin-web-demo-engine-01": {
            "rpcUrl": "http://engine/index.php/admin/remotecontrol",
            "user": os.environ["DEMO_ENGINE_ADMIN_USER"],
            "passwordEnv": "PUBGW_ENGINE_ADMIN_WEB_PASSWORD",
        }
    }, handle)
    handle.write("\n")
PY
  require_private "$ENGINES_FILE"
}

service_url() {
  local service="$1" port="$2" binding
  binding="$("${COMPOSE[@]}" port "$service" "$port" | head -n1)"
  [[ "$binding" == 127.0.0.1:* ]] || fail "$service is not bound to a random loopback port"
  printf 'http://%s\n' "$binding"
}

wait_url() {
  local label="$1" url="$2" attempt
  for ((attempt = 1; attempt <= 90; attempt++)); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  fail "$label endpoint did not become healthy"
}

wait_container_healthy() {
  local service="$1" id status attempt
  id="$("${COMPOSE[@]}" ps -q "$service")"
  [[ -n "$id" ]] || fail "$service container is absent"
  for ((attempt = 1; attempt <= 90; attempt++)); do
    status="$(docker inspect -f '{{.State.Health.Status}}' "$id" 2>/dev/null || true)"
    [[ "$status" == "healthy" ]] && return 0
    [[ "$status" == "unhealthy" ]] && fail "$service container is unhealthy"
    sleep 2
  done
  fail "$service database did not become healthy"
}

sync_database_credentials() {
  local engine_db_id platform_db_id
  engine_db_id="$("${COMPOSE[@]}" ps -q engine-db)"
  platform_db_id="$("${COMPOSE[@]}" ps -q platform-db)"
  [[ -n "$engine_db_id" && -n "$platform_db_id" ]] || fail "demo databases are absent"
  run_quiet docker exec -e ENGINE_DB_ROOT_PASSWORD "$engine_db_id" sh -c '
    MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb -uroot <<SQL
ALTER USER '\''root'\''@'\''localhost'\'' IDENTIFIED BY '\''$ENGINE_DB_ROOT_PASSWORD'\'';
CREATE USER IF NOT EXISTS '\''root'\''@'\''%'\'' IDENTIFIED BY '\''$ENGINE_DB_ROOT_PASSWORD'\'';
ALTER USER '\''root'\''@'\''%'\'' IDENTIFIED BY '\''$ENGINE_DB_ROOT_PASSWORD'\'';
GRANT ALL PRIVILEGES ON *.* TO '\''root'\''@'\''%'\'' WITH GRANT OPTION;
SQL
  '
  run_quiet docker exec -e PLATFORM_DB_SUPERUSER_PASSWORD -e PLATFORM_DB_APP_PASSWORD \
    -e PLATFORM_DB_OWNER_PASSWORD "$platform_db_id" sh -c '
    psql --set=ON_ERROR_STOP=1 --username postgres --dbname postgres <<'\''SQL'\''
\getenv superuser_password PLATFORM_DB_SUPERUSER_PASSWORD
\getenv app_password PLATFORM_DB_APP_PASSWORD
\getenv owner_password PLATFORM_DB_OWNER_PASSWORD
SELECT format('\''ALTER ROLE postgres PASSWORD %L'\'', :'\''superuser_password'\'')
\gexec
SELECT format('\''ALTER ROLE platform_app PASSWORD %L'\'', :'\''app_password'\'')
\gexec
SELECT format('\''ALTER ROLE platform_owner PASSWORD %L'\'', :'\''owner_password'\'')
\gexec
SQL
  '
}

prepare_existing_database_credentials() {
  local engine_db_id platform_db_id
  engine_db_id="$("${COMPOSE[@]}" ps -aq engine-db)"
  platform_db_id="$("${COMPOSE[@]}" ps -aq platform-db)"
  if [[ -z "$engine_db_id" && -z "$platform_db_id" ]]; then
    return 0
  fi
  [[ -n "$engine_db_id" && -n "$platform_db_id" ]] || fail "demo database state is incomplete"
  run_quiet docker start "$engine_db_id" "$platform_db_id"
  wait_container_healthy platform-db
  wait_container_healthy engine-db
  sync_database_credentials
}

build_platform() {
  if [[ -f "$PLATFORM_JAR" ]]; then
    return 0
  fi
  run_quiet docker run --rm \
    -v "$REPO_ROOT/platform/services/business:/workspace" \
    -v "${PLATFORM_MAVEN_REPO:-platform-maven-repo}:/root/.m2/repository" \
    -v "$REPO_ROOT/platform/deploy/platform-dev/maven-settings.xml:/root/.m2/settings.xml:ro" \
    -w /workspace "$JAVA_IMAGE" mvn -B -q -DskipTests package
  [[ -f "$PLATFORM_JAR" ]] || fail "platform jar was not built"
}

build_engine_if_missing() {
  if ! docker image inspect survey-web >/dev/null 2>&1; then
    run_quiet "${COMPOSE[@]}" build engine
  fi
}

install_engine() {
  local engine_id db_id installed
  engine_id="$("${COMPOSE[@]}" ps -q engine)"
  db_id="$("${COMPOSE[@]}" ps -q engine-db)"
  docker exec "$engine_id" sh -c \
    'mkdir -p application/runtime tmp/runtime tmp/assets tmp/upload && chmod -R 777 application/runtime tmp'
  export MYSQL_PWD="$ENGINE_DB_ROOT_PASSWORD"
  installed="$(docker exec -e MYSQL_PWD "$db_id" mariadb -uroot -N -e \
    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='limesurvey' AND table_name='lime_users'")"
  if [[ "$installed" == "0" ]]; then
    run_quiet docker exec "$engine_id" php application/commands/console.php installDemo
  fi
  run_quiet docker exec -e MYSQL_PWD "$db_id" mariadb -uroot limesurvey -e \
    "DELETE FROM lime_settings_global WHERE stg_name='RPCInterface'; INSERT INTO lime_settings_global (stg_name, stg_value) VALUES ('RPCInterface', 'json');"
  unset MYSQL_PWD
  run_quiet docker exec "$engine_id" php platform/tools/engine-theme/install-survey-theme.php zh-business
  run_quiet docker exec "$engine_id" php platform/tests/e2e/install_question_themes.php mjy-collapsible
  docker exec "$engine_id" rm -rf tmp/runtime/cache
}

enable_bridge() {
  local engine_id db_id
  engine_id="$("${COMPOSE[@]}" ps -q engine)"
  db_id="$("${COMPOSE[@]}" ps -q engine-db)"
  export MYSQL_PWD="$ENGINE_DB_ROOT_PASSWORD"
  run_quiet docker exec -e MYSQL_PWD "$db_id" mariadb -uroot limesurvey -e \
    "DELETE FROM lime_plugins WHERE name='MjyPlatformBridge'; INSERT INTO lime_plugins (name, plugin_type, active, priority, version, load_error) VALUES ('MjyPlatformBridge', 'user', 1, 0, '0.1.0', 0);"
  unset MYSQL_PWD
  docker exec "$engine_id" rm -rf tmp/runtime/cache
}

start_demo() {
  local platform_url engine_url gateway_url admin_url
  ensure_runtime
  build_platform
  build_engine_if_missing
  prepare_existing_database_credentials
  run_quiet "${COMPOSE[@]}" up -d platform-db engine-db
  wait_container_healthy platform-db
  wait_container_healthy engine-db
  sync_database_credentials
  run_quiet "${COMPOSE[@]}" up -d platform engine
  platform_url="$(service_url platform 8080)"
  engine_url="$(service_url engine 80)"
  wait_url platform "$platform_url/actuator/health"
  wait_url engine "$engine_url/"
  install_engine

  python3 "$SUPPORT" start \
    --platform-url "$platform_url" \
    --engine-internal-url http://engine \
    --engine-operations-url "$engine_url" \
    --admin-web-url "" \
    --metadata-file "$METADATA_FILE" \
    --event-secret-file "$EVENT_SECRET_FILE" \
    --access-file "$ACCESS_FILE" \
    --prepare-only
  require_private "$EVENT_SECRET_FILE"
  MJY_PLATFORM_EVENTS_SECRET="$(<"$EVENT_SECRET_FILE")"
  export MJY_PLATFORM_EVENTS_SECRET
  run_quiet "${COMPOSE[@]}" up -d --force-recreate engine
  engine_url="$(service_url engine 80)"
  wait_url engine "$engine_url/"
  enable_bridge

  run_quiet "${COMPOSE[@]}" build gateway admin-web
  run_quiet "${COMPOSE[@]}" up -d gateway admin-web
  gateway_url="$(service_url gateway 8080)"
  admin_url="$(service_url admin-web 80)"
  wait_url gateway "$gateway_url/healthz"
  wait_url admin-web "$admin_url/actuator/health"
  python3 "$SUPPORT" start \
    --platform-url "$platform_url" \
    --engine-internal-url http://engine \
    --engine-operations-url "$engine_url" \
    --admin-web-url "$admin_url" \
    --metadata-file "$METADATA_FILE" \
    --event-secret-file "$EVENT_SECRET_FILE" \
    --access-file "$ACCESS_FILE"
  require_private "$ACCESS_FILE"
}

status_demo() {
  local platform_url engine_url gateway_url admin_url
  ensure_runtime
  wait_container_healthy platform-db
  wait_container_healthy engine-db
  platform_url="$(service_url platform 8080)"
  engine_url="$(service_url engine 80)"
  gateway_url="$(service_url gateway 8080)"
  admin_url="$(service_url admin-web 80)"
  wait_url platform "$platform_url/actuator/health"
  wait_url engine "$engine_url/"
  wait_url gateway "$gateway_url/healthz"
  wait_url admin-web "$admin_url/actuator/health"
  require_private "$ACCESS_FILE"
  printf '%s\n' "$ACCESS_FILE"
}

refresh_token() {
  local platform_url
  ensure_runtime
  platform_url="$(service_url platform 8080)"
  wait_url platform "$platform_url/actuator/health"
  python3 "$SUPPORT" refresh-token --metadata-file "$METADATA_FILE" --access-file "$ACCESS_FILE"
  require_private "$ACCESS_FILE"
}

stop_demo() {
  local purge="${1:-}"
  if [[ "$purge" == "--purge" ]]; then
    [[ -t 0 ]] || fail "--purge requires interactive confirmation"
    local confirmation
    read -r -p "Type PURGE adminweb-demo to delete demo volumes: " confirmation
    [[ "$confirmation" == "PURGE adminweb-demo" ]] || fail "purge cancelled"
    ensure_runtime
    run_quiet "${COMPOSE[@]}" down -v --remove-orphans
    return 0
  fi
  [[ -z "$purge" ]] || fail "usage: $0 stop [--purge]"
  ensure_runtime
  run_quiet "${COMPOSE[@]}" down --remove-orphans
}

main() {
  case "${1:-}" in
    start|status|refresh-token) [[ $# == 1 ]] || fail "usage: $0 start|status|refresh-token" ;;
    stop) [[ $# == 1 || ( $# == 2 && "$2" == "--purge" ) ]] || fail "usage: $0 stop [--purge]" ;;
    *) fail "usage: $0 start|stop [--purge]|status|refresh-token" ;;
  esac
  case "${1:-}" in
    start)
      start_demo
      ;;
    stop)
      stop_demo "${2:-}"
      ;;
    status)
      status_demo
      ;;
    refresh-token)
      refresh_token
      ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
