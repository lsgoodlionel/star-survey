#!/usr/bin/env bash
# Real admin-web authoring gate:
# browser -> same-origin nginx -> platform -> gateway -> LimeSurvey.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TEST_DB="${TEST_DB:-mysql}"
is_fresh=false
E2E_SUITE="${E2E_SUITE:-full}"

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
random_loopback_port() {
  python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

WORK_DIR=""
ENGINE_RELAY_PID=""
stop_engine_relay() {
  if [[ -n "$ENGINE_RELAY_PID" ]]; then
    kill "$ENGINE_RELAY_PID" >/dev/null 2>&1 || true
    wait "$ENGINE_RELAY_PID" >/dev/null 2>&1 || true
    ENGINE_RELAY_PID=""
  fi
}

cleanup_run() {
  local status="$1"
  set +e
  stop_engine_relay
  if [[ -n "${ADMIN_WEB_JWT_FILE:-}" && -f "$ADMIN_WEB_JWT_FILE" ]]; then
    sanitize_test_artifacts "$([[ "$status" == "0" ]] && printf false || printf true)" >/dev/null 2>&1 || true
  fi
  if ((status != 0)); then
    # Access logs can contain signed asset/preview URLs. Report container state only.
    local container state
    for container in "$PLATFORM_CONTAINER" "$GATEWAY_CONTAINER" "$ADMIN_WEB_CONTAINER"; do
      state="$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null || printf unavailable)"
      echo "  [state] ${container}: ${state}" >&2
    done
  fi
  if [[ -n "$WORK_DIR" ]]; then
    rm -rf "$WORK_DIR"
  fi
  if [[ "${ADMIN_WEB_E2E_KEEP:-}" == "1" ]]; then
    echo "ADMIN_WEB_E2E_KEEP=1: leaving only compose resources running; private files were removed" >&2
    return 0
  fi
  if [[ "${ADMIN_WEB_RELAY_DIAGNOSTIC:-}" == "1" ]]; then
    if ! "${COMPOSE[@]}" --profile relay-test down -v --remove-orphans >/dev/null 2>&1; then
      echo "relay diagnostic cleanup: compose down failed" >&2
      return 1
    fi
    local remaining
    remaining="$(docker ps -aq --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")" || return 1
    [[ -z "$remaining" ]] || { echo "relay diagnostic cleanup: containers remain" >&2; return 1; }
    remaining="$(docker volume ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")" || return 1
    [[ -z "$remaining" ]] || { echo "relay diagnostic cleanup: volumes remain" >&2; return 1; }
    remaining="$(docker network ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")" || return 1
    [[ -z "$remaining" ]] || { echo "relay diagnostic cleanup: networks remain" >&2; return 1; }
    echo "relay diagnostic cleanup: project resources absent" >&2
  else
    "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  fi
  return 0
}

cleanup() {
  local status=$?
  cleanup_run "$status" || status=1
  # Returning from an EXIT trap preserves the original exit code; explicitly
  # exit so failed diagnostic cleanup cannot be reported as a passing run.
  trap - EXIT
  exit "$status"
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
  local actor_id="${1:-}" jwt_file="${2:-$ADMIN_WEB_JWT_FILE}"
  if [[ -n "$actor_id" ]]; then
    python3 "$GATE" --base-url "$PLATFORM_URL" issue-browser-token \
      --metadata-file "$ADMIN_WEB_METADATA_FILE" \
      --jwt-file "$jwt_file" \
      --actor-id "$actor_id"
  else
    python3 "$GATE" --base-url "$PLATFORM_URL" issue-browser-token \
      --metadata-file "$ADMIN_WEB_METADATA_FILE" \
      --jwt-file "$jwt_file"
  fi
  [[ "$(private_mode "$jwt_file")" == "600" ]] || fail "JWT file is not mode 0600"
}

sanitize_test_artifacts() {
  local remove_media="$1"
  local jwt_file arguments
  local jwt_files=("$ADMIN_WEB_JWT_FILE")
  if [[ -n "${ADMIN_WEB_EDITOR_JWT_FILE:-}" ]]; then
    jwt_files+=("$ADMIN_WEB_EDITOR_JWT_FILE")
  fi
  if [[ -n "${ADMIN_WEB_REVIEWER_JWT_FILE:-}" ]]; then
    jwt_files+=("$ADMIN_WEB_REVIEWER_JWT_FILE")
  fi
  if [[ -n "${ADMIN_WEB_DATA_JWT_FILE:-}" ]]; then
    jwt_files+=("$ADMIN_WEB_DATA_JWT_FILE")
  fi
  if [[ -n "${ADMIN_WEB_OPERATOR_JWT_FILE:-}" ]]; then
    jwt_files+=("$ADMIN_WEB_OPERATOR_JWT_FILE")
  fi
  if [[ -n "${ADMIN_WEB_PERSONNEL_JWT_FILE:-}" && -f "$ADMIN_WEB_PERSONNEL_JWT_FILE" ]]; then
    jwt_files+=("$ADMIN_WEB_PERSONNEL_JWT_FILE")
  fi
  for jwt_file in "${jwt_files[@]}"; do
    arguments=(
      --base-url "${PLATFORM_URL:-http://127.0.0.1}"
      scan-artifacts
      --jwt-file "$jwt_file"
      --path "$ADMIN_WEB_RESULT_FILE"
      --path "$ADMIN_WEB_TEST_RESULTS_DIR"
    )
    if [[ "$remove_media" == "true" ]]; then
      arguments+=(--remove-media)
    fi
    python3 "$GATE" "${arguments[@]}"
  done
}

export_failure_artifacts() {
  [[ -n "${ADMIN_WEB_CI_ARTIFACT_DIR:-}" ]] || return 0
  python3 "$GATE" --base-url "${PLATFORM_URL:-http://127.0.0.1}" export-sanitized-evidence \
    --jwt-file "$ADMIN_WEB_JWT_FILE" \
    --jwt-file "$ADMIN_WEB_EDITOR_JWT_FILE" \
    --jwt-file "$ADMIN_WEB_REVIEWER_JWT_FILE" \
    --jwt-file "$ADMIN_WEB_DATA_JWT_FILE" \
    --jwt-file "$ADMIN_WEB_OPERATOR_JWT_FILE" \
    --source-dir "$ADMIN_WEB_TEST_RESULTS_DIR" \
    --output-dir "$ADMIN_WEB_CI_ARTIFACT_DIR" \
    --allowed-root "$ADMIN_WEB_DIR/test-results"
}

run_playwright() {
  (
    cd "$ADMIN_WEB_DIR"
    if [[ "${ADMIN_WEB_RELAY_DIAGNOSTIC:-}" == "1" ]]; then
      npm run e2e -- --project=chromium-desktop --retries=0 --grep 'author filters stable resources, restores archive, and publishes the same survey'
    elif [[ "$E2E_SUITE" == "wave3" ]]; then
      npm run e2e -- --project=chromium-wave3-data
    else
      npm run e2e -- --project=chromium-desktop --project=chromium-template-branding --project=chromium-mobile
    fi
  )
}

run_browser_tests() {
  export ADMIN_WEB_EDITOR_JWT_FILE="${ADMIN_WEB_EDITOR_JWT_FILE:-$(dirname "$ADMIN_WEB_JWT_FILE")/editor.jwt}"
  export ADMIN_WEB_REVIEWER_JWT_FILE="${ADMIN_WEB_REVIEWER_JWT_FILE:-$(dirname "$ADMIN_WEB_JWT_FILE")/reviewer.jwt}"
  export ADMIN_WEB_DATA_JWT_FILE="${ADMIN_WEB_DATA_JWT_FILE:-$(dirname "$ADMIN_WEB_JWT_FILE")/data.jwt}"
  export ADMIN_WEB_OPERATOR_JWT_FILE="${ADMIN_WEB_OPERATOR_JWT_FILE:-$(dirname "$ADMIN_WEB_JWT_FILE")/operator.jwt}"
  rm -f "$ADMIN_WEB_JWT_FILE" "$ADMIN_WEB_EDITOR_JWT_FILE" "$ADMIN_WEB_REVIEWER_JWT_FILE" \
    "$ADMIN_WEB_DATA_JWT_FILE" "$ADMIN_WEB_OPERATOR_JWT_FILE" \
    "$ADMIN_WEB_RESULT_FILE"
  mkdir -p "$ADMIN_WEB_TEST_RESULTS_DIR"
  issue_browser_token
  issue_browser_token admin-web-e2e-editor "$ADMIN_WEB_EDITOR_JWT_FILE"
  issue_browser_token admin-web-e2e-reviewer "$ADMIN_WEB_REVIEWER_JWT_FILE"
  issue_browser_token admin-web-e2e-data "$ADMIN_WEB_DATA_JWT_FILE"
  issue_browser_token admin-web-e2e-operator "$ADMIN_WEB_OPERATOR_JWT_FILE"
  if [[ "${ADMIN_WEB_RELAY_DIAGNOSTIC:-}" == "1" ]]; then
    "${COMPOSE[@]}" --profile relay-test up -d --no-deps test-relay >/dev/null
    export ADMIN_WEB_RELAY_CONTAINER ADMIN_WEB_PLATFORM_CONTAINER
    ADMIN_WEB_RELAY_CONTAINER="$("${COMPOSE[@]}" --profile relay-test ps -q test-relay)"
    ADMIN_WEB_PLATFORM_CONTAINER="$("${COMPOSE[@]}" ps -q platform)"
    [[ "$ADMIN_WEB_RELAY_CONTAINER" =~ ^[0-9a-f]{64}$ && "$ADMIN_WEB_PLATFORM_CONTAINER" =~ ^[0-9a-f]{64}$ ]] || fail "diagnostic container identity is invalid"
    # The first preview assertion reads the event table. Do not race the
    # worker's first startup; record only bounded classifications on failure.
    local ready=false attempt relay_state
    for ((attempt = 1; attempt <= 45; attempt++)); do
      if db_query 'SELECT COUNT(*) FROM lime_mjyplatformbridge_event_log' >/dev/null 2>&1 \
          && docker exec "$ADMIN_WEB_RELAY_CONTAINER" test -f /tmp/survey-event-relay-heartbeat >/dev/null 2>&1; then
        ready=true
        break
      fi
      relay_state="$(docker inspect -f '{{.State.Status}}' "$ADMIN_WEB_RELAY_CONTAINER" 2>/dev/null || printf unavailable)"
      [[ "$relay_state" == running ]] || break
      sleep 2
    done
    if [[ "$ready" != true ]]; then
      relay_state="$(docker inspect -f '{{.State.Status}}' "$ADMIN_WEB_RELAY_CONTAINER" 2>/dev/null || printf unavailable)"
      docker logs "$ADMIN_WEB_RELAY_CONTAINER" >"$WORK_DIR/relay-private.log" 2>&1 || true
      python3 - "$WORK_DIR/relay-private.log" "$relay_state" <<'PY'
import pathlib, sys
text = pathlib.Path(sys.argv[1]).read_text(errors='replace').lower()
categories = ['event bridge is not active', 'event bridge could not be loaded',
              'event reconciliation failed', 'event relay failed', 'unknown command',
              'not defined', 'no such file or directory', 'fatal error',
              'database connection failed']
print('relay diagnostic state:', sys.argv[2])
print('relay diagnostic categories:', ','.join(x for x in categories if x in text) or 'unclassified')
PY
      fail "automatic relay did not complete its first iteration with initialized event schema"
    fi
  else
    (
      while true; do
        docker exec "$CONTAINER" php application/commands/console.php plugin cron >/dev/null 2>&1 || true
        sleep 2
      done
    ) &
    ENGINE_RELAY_PID=$!
  fi
  if ! run_playwright; then
    if [[ "${ADMIN_WEB_RELAY_DIAGNOSTIC:-}" == "1" ]]; then
      local event_counts
      event_counts="$(db_query "SELECT COUNT(*), SUM(CASE WHEN delivered_at IS NULL THEN 1 ELSE 0 END), SUM(CASE WHEN delivered_at IS NOT NULL THEN 1 ELSE 0 END) FROM lime_mjyplatformbridge_event_log" 2>/dev/null)" || event_counts="unavailable"
      if [[ "$event_counts" =~ ^[0-9[:space:]\|NULL]+$ ]]; then
        echo "relay diagnostic event counts (total/pending/delivered): $event_counts" >&2
      else
        echo "relay diagnostic event counts: unavailable" >&2
      fi
    fi
    stop_engine_relay
    export_failure_artifacts || true
    sanitize_test_artifacts true || true
    return 1
  fi
  stop_engine_relay
  [[ -f "$ADMIN_WEB_RESULT_FILE" ]] || fail "Playwright did not write the redacted result"
  if ! export_failure_artifacts; then
    echo "relay diagnostic: sanitized evidence export failed" >&2
    return 1
  fi
  if ! sanitize_test_artifacts false; then
    echo "relay diagnostic: artifact sanitization failed" >&2
    return 1
  fi
}

verify_and_archive_run_root() {
  local success_marker="$ADMIN_WEB_TEST_RESULTS_DIR/playwright-suite-success.txt"
  local root_id_file="$ADMIN_WEB_TEST_RESULTS_DIR/run-root-resource-id.txt"
  local expected_publish_calls=4
  if [[ "$E2E_SUITE" == "wave3" ]]; then expected_publish_calls=3; fi
  [[ -f "$success_marker" && "$(<"$success_marker")" == "passed" ]] \
    || fail "Playwright did not write a valid suite success marker"
  [[ -f "$root_id_file" ]] || fail "Playwright did not write run root evidence"

  python3 "$GATE" --base-url "$PLATFORM_URL" verify \
    --metadata-file "$ADMIN_WEB_METADATA_FILE" \
    --result-file "$ADMIN_WEB_RESULT_FILE" \
    --wave2-evidence-file "$ADMIN_WEB_TEST_RESULTS_DIR/wave2-scenarios.json" \
    --platform-db-container "$PLATFORM_DB_CONTAINER" \
    --platform-db-name platform \
    --engine-db-container "$TEST_PREFIX-$DB_SERVICE" \
    --engine-db-kind "$TEST_DB" \
    --gateway-container "$GATEWAY_CONTAINER" \
    --expected-gateway-publish-calls "$expected_publish_calls"

  python3 "$GATE" --base-url "$PLATFORM_URL" archive-run-root \
    --jwt-file "$ADMIN_WEB_JWT_FILE" \
    --root-id-file "$root_id_file"
  ok "successful real-stack gate archived its run root"
}

run_personnel_status_test() {
  export ADMIN_WEB_PERSONNEL_HELPER="$REPO_ROOT/platform/tests/e2e/personnel_status_gate.py"
  export ADMIN_WEB_PERSONNEL_JWT_FILE="$WORK_DIR/personnel.jwt"
  export ADMIN_WEB_PERSONNEL_METADATA_FILE="$WORK_DIR/personnel.json"
  python3 "$ADMIN_WEB_PERSONNEL_HELPER" seed
  [[ "$(private_mode "$ADMIN_WEB_PERSONNEL_JWT_FILE")" == "600" ]] || fail "personnel JWT file is not private"
  export ADMIN_WEB_PERSONNEL_MODE=true
  export ADMIN_WEB_PERSONNEL_PROBE_SECRET ADMIN_WEB_PERSONNEL_INSTALLATION_ID
  ADMIN_WEB_PERSONNEL_PROBE_SECRET="$(random_secret)"
  ADMIN_WEB_PERSONNEL_INSTALLATION_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
  "${COMPOSE[@]}" up -d --no-deps --force-recreate platform >/dev/null
  PLATFORM_URL="$(service_url platform 8080)"
  wait_healthy platform "$PLATFORM_URL/actuator/health" "$PLATFORM_HEALTH_ATTEMPTS" "$PLATFORM_CONTAINER"
  "${COMPOSE[@]}" up -d --no-deps --force-recreate admin-web >/dev/null
  wait_healthy admin-web "$ADMIN_WEB_BASE_URL/actuator/health" "$SERVICE_HEALTH_ATTEMPTS" "$ADMIN_WEB_CONTAINER"
  (cd "$ADMIN_WEB_DIR" && npm run e2e -- --project=chromium-personnel-plans --reporter=line)
  (cd "$ADMIN_WEB_DIR" && npm run e2e -- --project=chromium-personnel-status --reporter=line)
  sanitize_test_artifacts false
}

main() {
while (($#)); do
  case "$1" in
    --fresh) is_fresh=true ;;
    --wave3) E2E_SUITE=wave3 ;;
    *) echo "usage: $0 [--fresh] [--wave3]" >&2; return 2 ;;
  esac
  shift
done
trap cleanup EXIT

ensure_node22
for tool in docker python3 curl npm node unzip; do
  command -v "$tool" >/dev/null 2>&1 || fail "$tool is required"
done
docker info >/dev/null 2>&1 || fail "docker daemon is not running"

if [[ "${ADMIN_WEB_RELAY_DIAGNOSTIC:-}" == "1" ]]; then
  [[ "${GITHUB_ACTIONS:-}" == "true" && "${RUNNER_ENVIRONMENT:-}" == "github-hosted" ]] || fail "relay diagnostic requires a GitHub-hosted runner"
  [[ "$COMPOSE_PROJECT_NAME" == relay-ci-* && "$SURVEY_TEST_PREFIX" == "$COMPOSE_PROJECT_NAME" ]] || fail "relay diagnostic requires its dedicated project"
  [[ -z "$(docker ps -aq --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")" ]] || fail "relay diagnostic project is not empty"
  [[ -z "$(docker volume ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")" ]] || fail "relay diagnostic volumes are not empty"
  [[ -z "$(docker network ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")" ]] || fail "relay diagnostic network is not empty"
fi

WORK_DIR="$(mktemp -d)"
export ADMIN_WEB_PORT="${ADMIN_WEB_PORT:-$(random_loopback_port)}"
export ADMIN_WEB_PUBLIC_URL="http://127.0.0.1:$ADMIN_WEB_PORT"
export ADMIN_WEB_PLATFORM_JAR="$PLATFORM_JAR"
export ADMIN_WEB_ENGINES_FILE="$WORK_DIR/engines.json"
export ADMIN_WEB_JWT_FILE="$WORK_DIR/owner.jwt"
export ADMIN_WEB_EDITOR_JWT_FILE="$WORK_DIR/editor.jwt"
export ADMIN_WEB_REVIEWER_JWT_FILE="$WORK_DIR/reviewer.jwt"
export ADMIN_WEB_DATA_JWT_FILE="$WORK_DIR/data.jwt"
export ADMIN_WEB_OPERATOR_JWT_FILE="$WORK_DIR/operator.jwt"
export ADMIN_WEB_METADATA_FILE="$WORK_DIR/metadata.json"
export ADMIN_WEB_RESULT_FILE="$WORK_DIR/result.json"
export ADMIN_WEB_TEST_RESULTS_DIR="$WORK_DIR/test-results"
export ADMIN_WEB_PLATFORM_DB_CONTAINER="$PLATFORM_DB_CONTAINER"
export ADMIN_WEB_ENGINE_DB_CONTAINER="$TEST_PREFIX-$DB_SERVICE"
export ADMIN_WEB_ENGINE_DB_KIND="$TEST_DB"
if [[ -n "${ADMIN_WEB_CI_ARTIFACT_DIR:-}" && "$ADMIN_WEB_CI_ARTIFACT_DIR" != /* ]]; then
  export ADMIN_WEB_CI_ARTIFACT_DIR="$REPO_ROOT/$ADMIN_WEB_CI_ARTIFACT_DIR"
fi
EVENT_SECRET_FILE="$WORK_DIR/event-secret"

export PLATFORM_JWT_HMAC_SECRET PLATFORM_ENGINE_EVENTS_SECRET PLATFORM_DELIVERY_SECRET PUBGW_SHARED_SECRET PLATFORM_PUBGW_SECRET PLATFORM_ASSET_SECRET
PLATFORM_JWT_HMAC_SECRET="$(random_secret)"
PLATFORM_ENGINE_EVENTS_SECRET="$(random_secret)"
PLATFORM_DELIVERY_SECRET="$(random_secret)"
PUBGW_SHARED_SECRET="$(random_secret)"
PLATFORM_PUBGW_SECRET="$PUBGW_SHARED_SECRET"
PLATFORM_ASSET_SECRET="$(random_secret)"
ADMIN_PASSWORD="$(random_secret)"
export PUBGW_ENGINE_ADMIN_WEB_PASSWORD="$ADMIN_PASSWORD"

cat >"$ADMIN_WEB_ENGINES_FILE" <<JSON
{
  "$INSTANCE_ID": {
    "rpcUrl": "http://test-web/index.php/admin/remotecontrol",
    "user": "$ADMIN_USER",
    "passwordEnv": "PUBGW_ENGINE_ADMIN_WEB_PASSWORD",
    "channelSecretEnv": "PUBGW_ENGINE_ADMIN_WEB_CHANNEL_SECRET"
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
  --engine-base-url "$ADMIN_WEB_PUBLIC_URL/survey" \
  --metadata-file "$ADMIN_WEB_METADATA_FILE" \
  --event-secret-file "$EVENT_SECRET_FILE"

step "engine: fresh database, RemoteControl and platform bridge"
export MJY_ENGINE_INSTANCE_ID="$INSTANCE_ID"
export MJY_PLATFORM_EVENTS_URL="http://platform:8080/internal/engine-events"
MJY_PLATFORM_EVENTS_SECRET="$(<"$EVENT_SECRET_FILE")"
export MJY_PLATFORM_EVENTS_SECRET
export PUBGW_ENGINE_ADMIN_WEB_CHANNEL_SECRET
PUBGW_ENGINE_ADMIN_WEB_CHANNEL_SECRET="$(PYTHONPATH="$REPO_ROOT/platform/tools/publish-gateway" python3 -c 'import os; from pubgw.channel import derive_channel_secret; print(derive_channel_secret(os.environ["MJY_PLATFORM_EVENTS_SECRET"], os.environ["MJY_ENGINE_INSTANCE_ID"]))')"
rm -f "$EVENT_SECRET_FILE"
prepare_test_stack
docker exec "$CONTAINER" rm -rf tmp/runtime/cache
enable_remote_control
db_query "DELETE FROM lime_plugins WHERE name = 'MjyPlatformBridge'" >/dev/null
db_query "INSERT INTO lime_plugins (name, plugin_type, active, priority, version, load_error)
          VALUES ('MjyPlatformBridge', 'user', 1, 0, '0.1.0', 0)" >/dev/null
db_query "DELETE FROM lime_plugins WHERE name = 'MjyQuestionExtensions'" >/dev/null
db_query "INSERT INTO lime_plugins (name, plugin_type, active, priority, version, load_error)
          VALUES ('MjyQuestionExtensions', 'user', 1, 0, '0.1.0', 0)" >/dev/null
if [[ "${ADMIN_WEB_RELAY_DIAGNOSTIC:-}" == "1" ]]; then
  # Mirror the real production plugin installation/schema step. This creates
  # fixture schema only; it does not dispatch cron or deliver any events.
  if ! docker exec -e YII_CONSOLE_COMMANDS=/var/www/html/platform/deploy/production/init \
      "$CONTAINER" php application/commands/console.php productionInit plugins >"$WORK_DIR/plugin-init-private.log" 2>&1; then
    fail "production plugin fixture initialization failed"
  fi
fi
docker exec "$CONTAINER" php platform/tools/engine-theme/install-survey-theme.php zh-business >/dev/null
docker exec "$CONTAINER" php platform/tests/e2e/install_question_themes.php mjy-collapsible mjy-cascading-select mjy-carousel >/dev/null
ok "engine installed and RemoteControl enabled"

step "gateway and same-origin admin web: build and start"
"${COMPOSE[@]}" up -d --build gateway admin-web >/dev/null
GATEWAY_URL="$(service_url gateway 8080)"
wait_healthy gateway "$GATEWAY_URL/healthz" "$SERVICE_HEALTH_ATTEMPTS" "$GATEWAY_CONTAINER"
export ADMIN_WEB_BASE_URL="$ADMIN_WEB_PUBLIC_URL"
wait_healthy admin-web "$ADMIN_WEB_BASE_URL/actuator/health" "$SERVICE_HEALTH_ATTEMPTS" "$ADMIN_WEB_CONTAINER"
ok "gateway and admin web healthy"

step "engine: seed an isolated RemoteControl baseline for first-preview reconciliation"
python3 "$TEST_DIR/seed-empty-engine.py" \
  --base-url "$ADMIN_WEB_BASE_URL/survey" \
  --fixture "$REPO_ROOT/platform/tests/fixtures/surveys/mjy-question-slice.lss"
ok "engine first-preview baseline ready"

step "browser: issue a fresh 10-minute token, then run desktop and mobile checks"
run_browser_tests

step "gate: platform API, platform DB, gateway and engine DB"
verify_and_archive_run_root

if [[ "$E2E_SUITE" == "full" && "${ADMIN_WEB_RELAY_DIAGNOSTIC:-}" != "1" ]]; then
  step "browser: personnel grant, tenant status and revocation under enforced guard"
  run_personnel_status_test
fi

echo "Admin web real-stack e2e passed ($TEST_DB); private credentials and temporary results will now be removed" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
