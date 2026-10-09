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
  : >"$log"
  chmod 600 "$log"
  if ! "$@" >"$log" 2>&1; then
    fail "lifecycle command failed; details are in the private runtime log"
  fi
}

ensure_runtime() {
  umask 077
  mkdir -p "$RUNTIME_DIR"
  chmod 700 "$RUNTIME_DIR"
  if [[ ! -f "$RUNTIME_ENV" ]]; then
    {
      printf 'PLATFORM_JWT_HMAC_SECRET=%s\n' "$(random_secret)"
      printf 'PLATFORM_ENGINE_EVENTS_SECRET=%s\n' "$(random_secret)"
      printf 'PUBGW_SHARED_SECRET=%s\n' "$(random_secret)"
      printf 'DEMO_ENGINE_ADMIN_USER=admin\n'
      printf 'DEMO_ENGINE_ADMIN_PASSWORD=%s\n' "$(random_secret)"
    } >"$RUNTIME_ENV"
    chmod 600 "$RUNTIME_ENV"
  fi
  require_private "$RUNTIME_ENV"
  # shellcheck disable=SC1090
  source "$RUNTIME_ENV"
  export PLATFORM_JWT_HMAC_SECRET PLATFORM_ENGINE_EVENTS_SECRET PUBGW_SHARED_SECRET
  export DEMO_ENGINE_ADMIN_USER DEMO_ENGINE_ADMIN_PASSWORD
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
  installed="$(docker exec "$db_id" mariadb -uroot -proot -N -e \
    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='limesurvey' AND table_name='lime_users'")"
  if [[ "$installed" == "0" ]]; then
    run_quiet docker exec "$engine_id" php application/commands/console.php install \
      "$DEMO_ENGINE_ADMIN_USER" "$DEMO_ENGINE_ADMIN_PASSWORD" "Admin Web Demo" "demo-admin@example.invalid"
  fi
  run_quiet docker exec "$db_id" mariadb -uroot -proot limesurvey -e \
    "DELETE FROM lime_settings_global WHERE stg_name='RPCInterface'; INSERT INTO lime_settings_global (stg_name, stg_value) VALUES ('RPCInterface', 'json');"
  run_quiet docker exec "$engine_id" php platform/tools/engine-theme/install-survey-theme.php zh-business
  run_quiet docker exec "$engine_id" php platform/tests/e2e/install_question_themes.php mjy-collapsible
  docker exec "$engine_id" rm -rf tmp/runtime/cache
}

enable_bridge() {
  local engine_id db_id
  engine_id="$("${COMPOSE[@]}" ps -q engine)"
  db_id="$("${COMPOSE[@]}" ps -q engine-db)"
  run_quiet docker exec "$db_id" mariadb -uroot -proot limesurvey -e \
    "DELETE FROM lime_plugins WHERE name='MjyPlatformBridge'; INSERT INTO lime_plugins (name, plugin_type, active, priority, version, load_error) VALUES ('MjyPlatformBridge', 'user', 1, 0, '0.1.0', 0);"
  docker exec "$engine_id" rm -rf tmp/runtime/cache
}

start_demo() {
  local platform_url engine_url gateway_url admin_url
  ensure_runtime
  build_platform
  build_engine_if_missing
  run_quiet "${COMPOSE[@]}" up -d platform-db platform engine-db engine
  wait_container_healthy platform-db
  wait_container_healthy engine-db
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
  ensure_runtime
  if [[ "$purge" == "--purge" ]]; then
    [[ -t 0 ]] || fail "--purge requires interactive confirmation"
    local confirmation
    read -r -p "Type PURGE adminweb-demo to delete demo volumes: " confirmation
    [[ "$confirmation" == "PURGE adminweb-demo" ]] || fail "purge cancelled"
    run_quiet "${COMPOSE[@]}" down -v --remove-orphans
    return 0
  fi
  [[ -z "$purge" ]] || fail "usage: $0 stop [--purge]"
  run_quiet "${COMPOSE[@]}" down --remove-orphans
}

main() {
  case "${1:-}" in
    start)
      [[ $# == 1 ]] || fail "usage: $0 start"
      start_demo
      ;;
    stop)
      [[ $# -le 2 ]] || fail "usage: $0 stop [--purge]"
      stop_demo "${2:-}"
      ;;
    status)
      [[ $# == 1 ]] || fail "usage: $0 status"
      status_demo
      ;;
    refresh-token)
      [[ $# == 1 ]] || fail "usage: $0 refresh-token"
      refresh_token
      ;;
    *)
      fail "usage: $0 start|stop [--purge]|status|refresh-token"
      ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
