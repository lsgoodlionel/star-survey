#!/usr/bin/env bash
# Real admin-web authoring gate:
# browser -> same-origin nginx -> platform -> gateway -> LimeSurvey.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TEST_DB="${TEST_DB:-mysql}"
is_fresh=false

export SURVEY_TEST_PREFIX="${SURVEY_TEST_PREFIX:-adminweb}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$SURVEY_TEST_PREFIX}"
# shellcheck source=platform/deploy/test/lib.sh
source "$REPO_ROOT/platform/deploy/test/lib.sh"
COMPOSE=(docker compose -f "$REPO_ROOT/docker-compose.dev.yml" -f "$TEST_DIR/admin-web-e2e.compose.yml" --profile test)

INSTANCE_ID=admin-web-engine-01
PLATFORM_JAR="$REPO_ROOT/platform/services/business/target/business-0.1.0-SNAPSHOT.jar"
GATE="$REPO_ROOT/platform/tests/e2e/admin_web_gate.py"
ADMIN_WEB_DIR="$REPO_ROOT/platform/apps/admin-web"
JAVA_IMAGE=maven:3.9-eclipse-temurin-21
PLATFORM_CONTAINER="$SURVEY_TEST_PREFIX-admin-web-platform"
PLATFORM_DB_CONTAINER="$SURVEY_TEST_PREFIX-admin-web-platform-db"
GATEWAY_CONTAINER="$SURVEY_TEST_PREFIX-admin-web-gateway"
ADMIN_WEB_CONTAINER="$SURVEY_TEST_PREFIX-admin-web"
PLATFORM_HEALTH_ATTEMPTS=90
SERVICE_HEALTH_ATTEMPTS=60

ok() { echo "  [ok] $*" >&2; }
fail() { echo "  [FAIL] $*" >&2; exit 1; }
step() { echo "== $*" >&2; }
random_secret() { python3 -c 'import secrets; print(secrets.token_urlsafe(48))'; }

WORK_DIR=""
cleanup_run() {
  local status="$1"
  set +e
  if [[ -n "${ADMIN_WEB_JWT_FILE:-}" && -f "$ADMIN_WEB_JWT_FILE" ]]; then
    sanitize_test_artifacts "$([[ "$status" == "0" ]] && printf false || printf true)" >/dev/null 2>&1 || true
  fi
  if ((status != 0)); then
    echo "--- platform log (tail) ---" >&2
    docker logs --tail 80 "$PLATFORM_CONTAINER" >&2 2>&1 || true
    echo "--- gateway log (tail) ---" >&2
    docker logs --tail 60 "$GATEWAY_CONTAINER" >&2 2>&1 || true
    echo "--- admin web log (tail) ---" >&2
    docker logs --tail 40 "$ADMIN_WEB_CONTAINER" >&2 2>&1 || true
  fi
  if [[ -n "$WORK_DIR" ]]; then
    rm -rf "$WORK_DIR"
  fi
  if [[ "${ADMIN_WEB_E2E_KEEP:-}" == "1" ]]; then
    echo "ADMIN_WEB_E2E_KEEP=1: leaving only compose resources running; private files were removed" >&2
    return 0
  fi
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  return 0
}

cleanup() {
  local status=$?
  cleanup_run "$status"
  return "$status"
}

wait_healthy() {
  local label="$1" url="$2" attempts="$3" container="$4"
  local attempt
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    if [[ "$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null)" != "true" ]]; then
      fail "$label container exited before becoming healthy"
    fi
    sleep 2
  done
  fail "$label never became healthy"
}

service_url() {
  local service="$1" port="$2" binding
  binding="$("${COMPOSE[@]}" port "$service" "$port" | head -n1)"
  [[ "$binding" == 127.0.0.1:* ]] || fail "$service is not bound to a random loopback port"
  printf 'http://%s\n' "$binding"
}

private_mode() {
  if stat -f '%Lp' "$1" >/dev/null 2>&1; then
    stat -f '%Lp' "$1"
  else
    stat -c '%a' "$1"
  fi
}

ensure_node22() {
  local candidate
  if command -v node >/dev/null 2>&1 \
      && [[ "$(node -p 'process.versions.node.split(".")[0]')" == "22" ]]; then
    return 0
  fi
  for candidate in ${NODE22_BIN:+"$NODE22_BIN"} "$HOME"/.nvm/versions/node/v22*/bin; do
    if [[ -x "$candidate/node" ]] \
        && [[ "$("$candidate/node" -p 'process.versions.node.split(".")[0]')" == "22" ]]; then
      export PATH="$candidate:$PATH"
      return 0
    fi
  done
  fail "Node 22 is required for Playwright (set NODE22_BIN when it is not managed by NVM)"
}

issue_browser_token() {
  python3 "$GATE" --base-url "$PLATFORM_URL" issue-browser-token \
    --metadata-file "$ADMIN_WEB_METADATA_FILE" \
    --jwt-file "$ADMIN_WEB_JWT_FILE"
  [[ "$(private_mode "$ADMIN_WEB_JWT_FILE")" == "600" ]] || fail "JWT file is not mode 0600"
}

sanitize_test_artifacts() {
  local remove_media="$1"
  local arguments=(
    --base-url "${PLATFORM_URL:-http://127.0.0.1}"
    scan-artifacts
    --jwt-file "$ADMIN_WEB_JWT_FILE"
    --path "$ADMIN_WEB_RESULT_FILE"
    --path "$ADMIN_WEB_TEST_RESULTS_DIR"
  )
  if [[ "$remove_media" == "true" ]]; then
    arguments+=(--remove-media)
  fi
  python3 "$GATE" "${arguments[@]}"
}

export_failure_artifacts() {
  [[ -n "${ADMIN_WEB_CI_ARTIFACT_DIR:-}" ]] || return 0
  python3 "$GATE" --base-url "${PLATFORM_URL:-http://127.0.0.1}" export-sanitized-evidence \
    --jwt-file "$ADMIN_WEB_JWT_FILE" \
    --source-dir "$ADMIN_WEB_TEST_RESULTS_DIR" \
    --output-dir "$ADMIN_WEB_CI_ARTIFACT_DIR" \
    --allowed-root "$ADMIN_WEB_DIR/test-results"
}

run_playwright() {
  (
    cd "$ADMIN_WEB_DIR"
    npm run e2e -- --project=chromium-desktop --project=chromium-mobile
  )
}

run_browser_tests() {
  rm -f "$ADMIN_WEB_JWT_FILE" "$ADMIN_WEB_RESULT_FILE"
  mkdir -p "$ADMIN_WEB_TEST_RESULTS_DIR"
  issue_browser_token
  if ! run_playwright; then
    export_failure_artifacts || true
    sanitize_test_artifacts true || true
    return 1
  fi
  [[ -f "$ADMIN_WEB_RESULT_FILE" ]] || fail "Playwright did not write the redacted result"
  sanitize_test_artifacts false
}

verify_and_archive_run_root() {
  local success_marker="$ADMIN_WEB_TEST_RESULTS_DIR/playwright-suite-success.txt"
  local root_id_file="$ADMIN_WEB_TEST_RESULTS_DIR/run-root-resource-id.txt"
  [[ -f "$success_marker" && "$(<"$success_marker")" == "passed" ]] \
    || fail "Playwright did not write a valid suite success marker"
  [[ -f "$root_id_file" ]] || fail "Playwright did not write run root evidence"

  python3 "$GATE" --base-url "$PLATFORM_URL" verify \
    --metadata-file "$ADMIN_WEB_METADATA_FILE" \
    --result-file "$ADMIN_WEB_RESULT_FILE" \
    --platform-db-container "$PLATFORM_DB_CONTAINER" \
    --platform-db-name platform \
    --engine-db-container "$TEST_PREFIX-$DB_SERVICE" \
    --engine-db-kind "$TEST_DB" \
    --gateway-container "$GATEWAY_CONTAINER"

  python3 "$GATE" --base-url "$PLATFORM_URL" archive-run-root \
    --jwt-file "$ADMIN_WEB_JWT_FILE" \
    --root-id-file "$root_id_file"
  ok "successful real-stack gate archived its run root"
}

main() {
case "${1:-}" in
  --fresh) is_fresh=true ;;
  "") ;;
  *) echo "usage: $0 [--fresh]" >&2; return 2 ;;
esac
trap cleanup EXIT

ensure_node22
for tool in docker python3 curl npm node; do
  command -v "$tool" >/dev/null 2>&1 || fail "$tool is required"
done
docker info >/dev/null 2>&1 || fail "docker daemon is not running"

WORK_DIR="$(mktemp -d)"
export ADMIN_WEB_PLATFORM_JAR="$PLATFORM_JAR"
export ADMIN_WEB_ENGINES_FILE="$WORK_DIR/engines.json"
export ADMIN_WEB_JWT_FILE="$WORK_DIR/owner.jwt"
export ADMIN_WEB_METADATA_FILE="$WORK_DIR/metadata.json"
export ADMIN_WEB_RESULT_FILE="$WORK_DIR/result.json"
export ADMIN_WEB_TEST_RESULTS_DIR="$WORK_DIR/test-results"
if [[ -n "${ADMIN_WEB_CI_ARTIFACT_DIR:-}" && "$ADMIN_WEB_CI_ARTIFACT_DIR" != /* ]]; then
  export ADMIN_WEB_CI_ARTIFACT_DIR="$REPO_ROOT/$ADMIN_WEB_CI_ARTIFACT_DIR"
fi
EVENT_SECRET_FILE="$WORK_DIR/event-secret"

export PLATFORM_JWT_HMAC_SECRET PLATFORM_ENGINE_EVENTS_SECRET PUBGW_SHARED_SECRET PLATFORM_PUBGW_SECRET
PLATFORM_JWT_HMAC_SECRET="$(random_secret)"
PLATFORM_ENGINE_EVENTS_SECRET="$(random_secret)"
PUBGW_SHARED_SECRET="$(random_secret)"
PLATFORM_PUBGW_SECRET="$PUBGW_SHARED_SECRET"
ADMIN_PASSWORD="$(random_secret)"
export PUBGW_ENGINE_ADMIN_WEB_PASSWORD="$ADMIN_PASSWORD"

cat >"$ADMIN_WEB_ENGINES_FILE" <<JSON
{
  "$INSTANCE_ID": {
    "rpcUrl": "http://test-web/index.php/admin/remotecontrol",
    "user": "$ADMIN_USER",
    "passwordEnv": "PUBGW_ENGINE_ADMIN_WEB_PASSWORD"
  }
}
JSON

if $is_fresh; then
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
fi

step "platform: build jar without touching the shared platform-db"
docker run --rm \
  -v "$REPO_ROOT/platform/services/business:/workspace" \
  -v "${PLATFORM_MAVEN_REPO:-platform-maven-repo}:/root/.m2/repository" \
  -v "$REPO_ROOT/platform/deploy/platform-dev/maven-settings.xml:/root/.m2/settings.xml:ro" \
  -w /workspace "$JAVA_IMAGE" mvn -B -q -DskipTests package
[[ -f "$PLATFORM_JAR" ]] || fail "platform jar was not built"

step "platform: start with its isolated PostgreSQL"
"${COMPOSE[@]}" up -d admin-web-platform-db platform >/dev/null
PLATFORM_URL="$(service_url platform 8080)"
wait_healthy platform "$PLATFORM_URL/actuator/health" "$PLATFORM_HEALTH_ATTEMPTS" "$PLATFORM_CONTAINER"
ok "platform healthy on a random loopback port"

step "seed: tenant, owner, engine instance and non-sensitive metadata"
python3 "$GATE" --base-url "$PLATFORM_URL" prepare \
  --instance "$INSTANCE_ID" \
  --engine-base-url "http://test-web" \
  --metadata-file "$ADMIN_WEB_METADATA_FILE" \
  --event-secret-file "$EVENT_SECRET_FILE"

step "engine: fresh database, RemoteControl and platform bridge"
export MJY_ENGINE_INSTANCE_ID="$INSTANCE_ID"
export MJY_PLATFORM_EVENTS_URL="http://platform:8080/internal/engine-events"
MJY_PLATFORM_EVENTS_SECRET="$(<"$EVENT_SECRET_FILE")"
export MJY_PLATFORM_EVENTS_SECRET
rm -f "$EVENT_SECRET_FILE"
prepare_test_stack
docker exec "$CONTAINER" rm -rf tmp/runtime/cache
enable_remote_control
db_query "DELETE FROM lime_plugins WHERE name = 'MjyPlatformBridge'" >/dev/null
db_query "INSERT INTO lime_plugins (name, plugin_type, active, priority, version, load_error)
          VALUES ('MjyPlatformBridge', 'user', 1, 0, '0.1.0', 0)" >/dev/null
ok "engine installed and RemoteControl enabled"

step "gateway and same-origin admin web: build and start"
"${COMPOSE[@]}" up -d --build gateway admin-web >/dev/null
GATEWAY_URL="$(service_url gateway 8080)"
wait_healthy gateway "$GATEWAY_URL/healthz" "$SERVICE_HEALTH_ATTEMPTS" "$GATEWAY_CONTAINER"
export ADMIN_WEB_BASE_URL
ADMIN_WEB_BASE_URL="$(service_url admin-web 80)"
wait_healthy admin-web "$ADMIN_WEB_BASE_URL/actuator/health" "$SERVICE_HEALTH_ATTEMPTS" "$ADMIN_WEB_CONTAINER"
ok "gateway and admin web healthy"

step "browser: issue a fresh 10-minute token, then run desktop and mobile checks"
run_browser_tests

step "gate: platform API, platform DB, gateway and engine DB"
verify_and_archive_run_root

echo "Admin web real-stack e2e passed ($TEST_DB); private credentials and temporary results will now be removed" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
