#!/usr/bin/env bash
# P1 gate end to end: three real processes, one minimal vertical slice.
#
#   host driver --JWT--> platform (Spring Boot jar, PostgreSQL 16)
#                          |  HMAC            ^  signed engine events
#                          v                  |
#                        publish gateway --RemoteControl--> engine (LimeSurvey + MjyPlatformBridge)
#
# All three share a private network created for this run (p1e2e-net); the platform
# also joins platform-dev_default for its database. Only the platform and gateway
# ports are published, on 127.0.0.1, for the host driver.
#
# Flow (every step asserted, first failure stops the run):
#   operator: tenant -> plan (member.seats) -> onboarding -> active -> engine instance
#             hd-engine-01 -> per-instance event secret (never printed)
#   engine:   fresh test stack with MjyPlatformBridge on, configured with that secret
#   owner:    project via POST /v1/projects -> survey under it -> draft = fixture ->
#             publish 200 -> versions, public route, survey active in the engine
#   respondent: completes the survey over HTTP; engine cron relays the events and the
#             platform projects the response as engine_completed
#   idempotency: publish again -> 409 already_published, gateway not called again
#
# Secrets (JWT, engine-events master, gateway shared secret, engine admin password)
# are random per run and travel only through environment variables.
#
# Usage: [TEST_DB=mysql|pgsql] platform/deploy/test/run-p1-e2e.sh
#   P1_E2E_KEEP=1   leave everything running after the run (debugging only)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TEST_DB="${TEST_DB:-mysql}"
# Always a fresh engine database: undelivered events left by other runs (other
# engine instance ids) would make the platform reject every relay batch.
is_fresh=true
# The test stack's container names are fixed; share the compose project of the
# main checkout so a run from any worktree reuses them instead of colliding.
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-survey}"
# shellcheck source=lib.sh
source "$REPO_ROOT/platform/deploy/test/lib.sh"
COMPOSE=(docker compose -f "$REPO_ROOT/docker-compose.dev.yml" -f "$TEST_DIR/p1-e2e.compose.yml" --profile test)

INSTANCE_ID=hd-engine-01
NETWORK=p1e2e-net
PLATFORM_CONTAINER=p1e2e-platform
GATEWAY_CONTAINER=p1e2e-pubgw
PLATFORM_DEV_DIR="$REPO_ROOT/platform/deploy/platform-dev"
PLATFORM_DB_CONTAINER=platform-db
PLATFORM_DB_NETWORK=platform-dev_default
export PLATFORM_DB_NAME=platform_p1_e2e
PLATFORM_JAR="$REPO_ROOT/platform/services/business/target/business-0.1.0-SNAPSHOT.jar"
# The maven image mvn.sh already uses ships a Java 21 runtime: no extra image to pull.
JAVA_IMAGE=maven:3.9-eclipse-temurin-21
GATEWAY_DIR="$REPO_ROOT/platform/tools/publish-gateway"
GATEWAY_IMAGE=survey-publish-gateway:test
DRIVER="$REPO_ROOT/platform/tests/e2e/p1_gate.py"
DEFINITION="$REPO_ROOT/platform/tests/fixtures/surveys/publish-gateway.json"
PLATFORM_HEALTH_ATTEMPTS=90
GATEWAY_HEALTH_ATTEMPTS=30
INGESTION_ATTEMPTS=20
INGESTION_INTERVAL_SECONDS=2

ok() { echo "  [ok] $*" >&2; }
fail() {
  echo "  [FAIL] $*" >&2
  exit 1
}
step() { echo "== $*" >&2; }

random_secret() { python3 -c 'import secrets; print(secrets.token_urlsafe(48))'; }

# ------------------------------------------------------------------ teardown

is_engine_started=false
WORK_DIR=""

dump_diagnostics() {
  echo "--- platform log (tail) ---" >&2
  docker logs --tail 80 "$PLATFORM_CONTAINER" >&2 2>&1 || true
  echo "--- gateway log (tail) ---" >&2
  docker logs --tail 40 "$GATEWAY_CONTAINER" >&2 2>&1 || true
}

# Detach whatever is still attached (e.g. a kept engine container), then remove.
remove_network() {
  local attached
  attached="$(docker network inspect -f '{{range .Containers}}{{.Name}} {{end}}' "$NETWORK" 2>/dev/null)" || return 0
  for container in $attached; do
    docker network disconnect -f "$NETWORK" "$container" >/dev/null 2>&1 || true
  done
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
}

cleanup() {
  local status=$?
  set +e
  if ((status != 0)); then
    dump_diagnostics
  fi
  if [[ "${P1_E2E_KEEP:-}" == "1" ]]; then
    echo "P1_E2E_KEEP=1: leaving containers, network and database $PLATFORM_DB_NAME in place" >&2
    return
  fi
  docker rm -fv "$PLATFORM_CONTAINER" "$GATEWAY_CONTAINER" >/dev/null 2>&1
  if $is_engine_started; then
    # test-web carries the event secret in its environment; test-db is tmpfs anyway.
    "${COMPOSE[@]}" rm -sfv test-web "$DB_SERVICE" >/dev/null 2>&1
  fi
  remove_network
  docker exec "$PLATFORM_DB_CONTAINER" psql -U platform_owner -d platform -qc \
    "DROP DATABASE IF EXISTS $PLATFORM_DB_NAME WITH (FORCE)" >/dev/null 2>&1
  # platform-db 是并行车道共用的开发服务：只删自己的库，不停容器（启动时是否在运行无法代表别的车道此刻是否在用）。
  if [[ -n "$WORK_DIR" ]]; then
    rm -rf "$WORK_DIR"
  fi
}
trap cleanup EXIT

# ------------------------------------------------------------------- helpers

# One statement inside the tenant's row-level-security scope, as platform_owner
# (FORCE ROW LEVEL SECURITY applies to it too). Prints bare rows.
platform_tenant_sql() {
  local tenant="$1" sql="$2"
  [[ "$tenant" =~ ^[0-9a-f-]{36}$ ]] || fail "malformed tenant id: $tenant"
  docker exec -i "$PLATFORM_DB_CONTAINER" psql -U platform_owner -d "$PLATFORM_DB_NAME" -v ON_ERROR_STOP=1 -tAq <<SQL
BEGIN;
SELECT set_config('app.tenant_id', '$tenant', true) \g /dev/null
$sql
COMMIT;
SQL
}

state_field() {
  python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$STATE" "$1"
}

wait_healthy() {
  local label="$1" url="$2" attempts="$3" container="$4"
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    if [[ "$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null)" != "true" ]]; then
      fail "$label container exited before becoming healthy"
    fi
    sleep 2
  done
  fail "$label never became healthy at $url"
}

gateway_publish_calls() {
  docker logs "$GATEWAY_CONTAINER" 2>&1 | grep -c '"POST /v1/publish' || true
}

run_engine_cron() {
  docker exec "$CONTAINER" php application/commands/console.php plugin cron >/dev/null 2>&1
}

# --------------------------------------------------------------------- setup

for tool in docker python3 curl; do
  command -v "$tool" >/dev/null || fail "$tool is required"
done
docker info >/dev/null 2>&1 || fail "docker daemon is not running (on macOS: open -a Docker)"

WORK_DIR="$(mktemp -d)"
STATE="$WORK_DIR/state.json"
SECRET_FILE="$WORK_DIR/event-secret"

export PLATFORM_JWT_HMAC_SECRET PLATFORM_ENGINE_EVENTS_SECRET PUBGW_SHARED_SECRET PLATFORM_PUBGW_SECRET
PLATFORM_JWT_HMAC_SECRET="$(random_secret)"
PLATFORM_ENGINE_EVENTS_SECRET="$(random_secret)"
PUBGW_SHARED_SECRET="$(random_secret)"
PLATFORM_PUBGW_SECRET="$PUBGW_SHARED_SECRET"
# lib.sh installs the engine with ADMIN_PASSWORD; the gateway reads it from this variable.
ADMIN_PASSWORD="$(random_secret)"
export PUBGW_ENGINE_HD01_PASSWORD="$ADMIN_PASSWORD"

step "platform: build, fresh database, start"
# Leftovers of an interrupted run hold connections that would block the drop below.
docker rm -fv "$PLATFORM_CONTAINER" "$GATEWAY_CONTAINER" >/dev/null 2>&1 || true
remove_network
# mvn.sh brings up platform-db; the build itself needs no database.
"$PLATFORM_DEV_DIR/mvn.sh" -DskipTests package
[[ -f "$PLATFORM_JAR" ]] || fail "platform jar was not built: $PLATFORM_JAR"
docker exec "$PLATFORM_DB_CONTAINER" psql -U platform_owner -d platform -qc \
  "DROP DATABASE IF EXISTS $PLATFORM_DB_NAME WITH (FORCE)"
docker exec "$PLATFORM_DB_CONTAINER" psql -U platform_owner -d platform -qc \
  "CREATE DATABASE $PLATFORM_DB_NAME OWNER platform_owner"
docker exec "$PLATFORM_DB_CONTAINER" psql -U platform_owner -d platform -qc \
  "GRANT CONNECT ON DATABASE $PLATFORM_DB_NAME TO platform_app"

docker network create "$NETWORK" >/dev/null

docker run -d --name "$PLATFORM_CONTAINER" --network "$PLATFORM_DB_NETWORK" -p 127.0.0.1::8080 \
  -v "$PLATFORM_JAR:/app/business.jar:ro" \
  -e PLATFORM_DB_URL="jdbc:postgresql://$PLATFORM_DB_CONTAINER:5432/$PLATFORM_DB_NAME" \
  -e PLATFORM_JWT_HMAC_SECRET -e PLATFORM_ENGINE_EVENTS_SECRET -e PLATFORM_PUBGW_SECRET \
  -e PLATFORM_PUBGW_URL="http://$GATEWAY_CONTAINER:8080" \
  "$JAVA_IMAGE" java -jar /app/business.jar >/dev/null
docker network connect "$NETWORK" "$PLATFORM_CONTAINER"
PLATFORM_URL="http://$(docker port "$PLATFORM_CONTAINER" 8080/tcp | head -n1)"
wait_healthy platform "$PLATFORM_URL/actuator/health" "$PLATFORM_HEALTH_ATTEMPTS" "$PLATFORM_CONTAINER"
ok "platform healthy at $PLATFORM_URL (database $PLATFORM_DB_NAME migrated)"

step "operator: tenant, plan, onboarding, engine instance, event secret"
python3 "$DRIVER" --base-url "$PLATFORM_URL" --state "$STATE" operator \
  --instance "$INSTANCE_ID" --engine-base-url "http://$CONTAINER" --secret-file "$SECRET_FILE"
TENANT_ID="$(state_field tenantId)"

step "engine: fresh test stack with MjyPlatformBridge"
export MJY_ENGINE_INSTANCE_ID="$INSTANCE_ID"
export MJY_PLATFORM_EVENTS_URL="http://$PLATFORM_CONTAINER:8080/internal/engine-events"
MJY_PLATFORM_EVENTS_SECRET="$(<"$SECRET_FILE")"
export MJY_PLATFORM_EVENTS_SECRET
rm -f "$SECRET_FILE"
is_engine_started=true
prepare_test_stack
enable_remote_control
db_query "DELETE FROM lime_plugins WHERE name = 'MjyPlatformBridge'" >/dev/null
db_query "INSERT INTO lime_plugins (name, plugin_type, active, priority, version, load_error)
          VALUES ('MjyPlatformBridge', 'user', 1, 0, '0.1.0', 0)" >/dev/null
docker network connect "$NETWORK" "$CONTAINER"
[[ "$(docker exec "$CONTAINER" printenv MJY_ENGINE_INSTANCE_ID)" == "$INSTANCE_ID" ]] \
  || fail "engine container does not carry MJY_ENGINE_INSTANCE_ID"
docker exec "$CONTAINER" sh -c 'test -n "$MJY_PLATFORM_EVENTS_SECRET"' \
  || fail "engine container does not carry MJY_PLATFORM_EVENTS_SECRET"
ok "engine ($TEST_DB) installed, RemoteControl on, MjyPlatformBridge active, instance $INSTANCE_ID configured"

step "gateway: start against the engine"
docker build -q -t "$GATEWAY_IMAGE" "$GATEWAY_DIR" >/dev/null
cat >"$WORK_DIR/engines.json" <<JSON
{
  "$INSTANCE_ID": {
    "rpcUrl": "http://$CONTAINER/index.php/admin/remotecontrol",
    "user": "$ADMIN_USER",
    "passwordEnv": "PUBGW_ENGINE_HD01_PASSWORD"
  }
}
JSON
docker create --name "$GATEWAY_CONTAINER" --network "$NETWORK" -p 127.0.0.1::8080 \
  -e PUBGW_SHARED_SECRET -e PUBGW_ENGINES_CONFIG=/app/engines.json -e PUBGW_ENGINE_HD01_PASSWORD \
  "$GATEWAY_IMAGE" >/dev/null
docker cp "$WORK_DIR/engines.json" "$GATEWAY_CONTAINER:/app/engines.json"
docker start "$GATEWAY_CONTAINER" >/dev/null
GATEWAY_URL="http://$(docker port "$GATEWAY_CONTAINER" 8080/tcp | head -n1)"
wait_healthy gateway "$GATEWAY_URL/healthz" "$GATEWAY_HEALTH_ATTEMPTS" "$GATEWAY_CONTAINER"
ok "gateway healthy"

step "owner: create, draft, publish, verify"
python3 "$DRIVER" --base-url "$PLATFORM_URL" --state "$STATE" owner-project
PROJECT_ID="$(state_field projectId)"
ok "project $PROJECT_ID created through POST /v1/projects"
engine_surveys_before="$(db_query "SELECT COUNT(*) FROM lime_surveys" | tr -d '[:space:]')"
python3 "$DRIVER" --base-url "$PLATFORM_URL" --state "$STATE" owner-publish --definition "$DEFINITION"
SURVEY_ID="$(state_field surveyId)"
SID="$(state_field engineSid)"
[[ "$SID" =~ ^[0-9]+$ ]] || fail "engine sid is not a number: $SID"
active="$(db_query "SELECT active FROM lime_surveys WHERE sid = $SID" | tr -d '[:space:]')"
[[ "$active" == "Y" ]] || fail "engine survey $SID active='$active', expected Y"
ok "engine survey $SID is active"
engine_surveys_after_publish="$(db_query "SELECT COUNT(*) FROM lime_surveys" | tr -d '[:space:]')"
((engine_surveys_after_publish == engine_surveys_before + 1)) \
  || fail "engine surveys went from $engine_surveys_before to $engine_surveys_after_publish, expected +1"
ok "exactly one engine survey created"

step "respondent: complete the survey over HTTP"
docker exec "$CONTAINER" php platform/tests/e2e/p1_respond.php "$SID" >/dev/null \
  || fail "respondent could not complete survey $SID"
RESPONSE_ID="$(db_query "SELECT id FROM lime_responses_$SID WHERE submitdate IS NOT NULL" | tr -d '[:space:]')"
[[ "$RESPONSE_ID" =~ ^[0-9]+$ ]] || fail "expected exactly one submitted response in lime_responses_$SID, got '$RESPONSE_ID'"
ok "response $RESPONSE_ID submitted (submitdate set)"

step "ingestion: engine cron relays, platform projects"
projection=""
for ((attempt = 1; attempt <= INGESTION_ATTEMPTS; attempt++)); do
  run_engine_cron || true
  projection="$(platform_tenant_sql "$TENANT_ID" "SELECT state FROM response_projection
    WHERE engine_instance_id = '$INSTANCE_ID' AND survey_id = $SID AND response_id = $RESPONSE_ID;" | tr -d '[:space:]')"
  if [[ "$projection" == "engine_completed" ]]; then
    break
  fi
  sleep "$INGESTION_INTERVAL_SECONDS"
done
[[ "$projection" == "engine_completed" ]] \
  || fail "platform projection of response $RESPONSE_ID is '$projection' after $INGESTION_ATTEMPTS relays, expected engine_completed"
ok "platform projection: $INSTANCE_ID sid $SID response $RESPONSE_ID -> engine_completed"
inbox_types="$(platform_tenant_sql "$TENANT_ID" "SELECT string_agg(DISTINCT event_type, ',' ORDER BY event_type)
  FROM engine_event_inbox WHERE engine_instance_id = '$INSTANCE_ID' AND survey_id = $SID;" | tr -d '[:space:]')"
[[ ",$inbox_types," == *",response.completed,"* && ",$inbox_types," == *",response.saved,"* ]] \
  || fail "platform inbox for sid $SID holds '$inbox_types', expected response.saved and response.completed"
ok "platform inbox holds $inbox_types for sid $SID"
undelivered="$(db_query "SELECT COUNT(*) FROM lime_mjyplatformbridge_event_log WHERE delivered_at IS NULL" | tr -d '[:space:]')"
((undelivered == 0)) || fail "$undelivered engine events still undelivered"
ok "engine event log fully delivered"

step "idempotency: publish again"
gateway_calls_before="$(gateway_publish_calls)"
((gateway_calls_before == 1)) || fail "gateway saw $gateway_calls_before publish calls so far, expected 1"
python3 "$DRIVER" --base-url "$PLATFORM_URL" --state "$STATE" republish
gateway_calls_after="$(gateway_publish_calls)"
((gateway_calls_after == gateway_calls_before)) \
  || fail "gateway publish calls went from $gateway_calls_before to $gateway_calls_after"
ok "gateway was not called again ($gateway_calls_after publish call in total)"
engine_surveys_final="$(db_query "SELECT COUNT(*) FROM lime_surveys" | tr -d '[:space:]')"
((engine_surveys_final == engine_surveys_after_publish)) || fail "engine survey count changed on re-publish"
ok "engine survey count unchanged"

echo "P1 e2e passed ($TEST_DB): survey $SURVEY_ID -> $INSTANCE_ID sid $SID, response $RESPONSE_ID ingested"
