#!/bin/bash
set -euo pipefail
umask 077
# Synthetic-only integration: real worker -> injected fault -> gateway -> engine.
# The service email provider is asserted to be the in-memory test fake.
# Usage: TEST_DB=mysql|pgsql SURVEY_TEST_PREFIX=survey-reminder-it-<unique> $0
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TEST_DB="${TEST_DB:?set TEST_DB to mysql or pgsql}"
prefix="${SURVEY_TEST_PREFIX:?set a unique survey-reminder-it- prefix}"
[[ "$TEST_DB" == mysql || "$TEST_DB" == pgsql ]] || { echo 'invalid test database' >&2; exit 1; }
[[ "$prefix" =~ ^survey-reminder-it-[a-z0-9][a-z0-9-]{0,35}$ ]] || { echo 'invalid isolated fixture prefix' >&2; exit 1; }
for image in survey-web python:3.12.15-alpine3.24 python:3.11-slim postgres:16-alpine maven:3.9-eclipse-temurin-21; do
  docker image inspect "$image" >/dev/null 2>&1 || { echo "required local image unavailable: $image" >&2; exit 1; }
done
export COMPOSE_PROJECT_NAME="$prefix"
for name in "$prefix-test-web" "$prefix-test-db" "$prefix-test-pg" "$prefix-platform-db" "$prefix-gateway" "$prefix-proxy"; do if docker inspect "$name" >/dev/null 2>&1; then echo 'fixture name already exists' >&2; exit 1; fi; done
for kind in container volume network; do
 case "$kind" in container) found=$(docker ps -aq --filter "label=com.docker.compose.project=$prefix");; volume) found=$(docker volume ls -q --filter "label=com.docker.compose.project=$prefix");; network) found=$(docker network ls -q --filter "label=com.docker.compose.project=$prefix");; esac
 [[ -z "$found" ]] || { echo 'fixture project already exists' >&2; exit 1; }
done
work=$(mktemp -d /tmp/survey-real-reminder-XXXXXXXX)
export SURVEY_TEST_PREFIX="$prefix"
is_fresh=true
source "$REPO_ROOT/platform/deploy/test/lib.sh"
printf 'networks:\n  default:\n    internal: true\n' > "$work/internal.yml"
COMPOSE=(docker compose -f "$REPO_ROOT/docker-compose.dev.yml" -f "$work/internal.yml" --profile test)
cleanup() { docker rm -fv "$prefix-proxy" "$prefix-gateway" "$prefix-platform-db" >/dev/null 2>&1 || true; "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true; rm -rf "$work"; }
trap cleanup EXIT
prepare_test_stack
enable_remote_control
network=$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}' "$CONTAINER")
[[ "$(docker network inspect -f '{{.Internal}}' "$network")" == true ]]
ENGINE_CONTAINER="$CONTAINER" ENGINE_DB_CONTAINER="$prefix-$DB_SERVICE" EVIDENCE_FILE="$work/evidence.json" TEST_DB="$TEST_DB" python3 "$REPO_ROOT/platform/tests/e2e/real_engine_reminder_fixture.py"
printf '{"isolated-engine":{"rpcUrl":"http://%s/index.php/admin/remotecontrol","user":"admin","passwordEnv":"ENGINE_PASSWORD"}}\n' "$CONTAINER" > "$work/engines.json"
chmod 644 "$work/engines.json"
image="survey-real-reminder-gateway:${prefix}"
docker build --network none -q -t "$image" "$REPO_ROOT/platform/tools/publish-gateway" >/dev/null
docker create --name "$prefix-gateway" --network "$network" --network-alias real-gateway \
 -e PUBGW_SHARED_SECRET=synthetic-gateway-secret-0123456789abcdef -e PUBGW_ENGINES_CONFIG=/app/engines.json \
 -e ENGINE_PASSWORD=password "$image" >/dev/null
docker cp "$work/engines.json" "$prefix-gateway:/app/engines.json"
docker start "$prefix-gateway" >/dev/null
for i in {1..60}; do [[ "$(docker inspect -f '{{.State.Health.Status}}' "$prefix-gateway" 2>/dev/null || true)" == healthy ]] && break; sleep 1; done
[[ "$(docker inspect -f '{{.State.Health.Status}}' "$prefix-gateway")" == healthy ]]
docker run -d --name "$prefix-proxy" --network "$network" --network-alias fault-proxy \
 -v "$REPO_ROOT/platform/tests/e2e/real_engine_reminder_fault_proxy.py:/fixture/fault-proxy.py:ro" python:3.11-slim python /fixture/fault-proxy.py >/dev/null
docker run -d --name "$prefix-platform-db" --network "$network" --tmpfs /var/lib/postgresql/data \
 -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=postgres \
 -v "$REPO_ROOT/platform/deploy/platform-dev/init-db.sql:/docker-entrypoint-initdb.d/10-roles.sql:ro" postgres:16-alpine >/dev/null
for i in {1..60}; do docker exec "$prefix-platform-db" pg_isready -U postgres -d platform >/dev/null 2>&1 && break; sleep 1; done
docker exec "$prefix-platform-db" pg_isready -U postgres -d platform >/dev/null
# This test asserts that its only email provider is the in-memory fake; no external network exists.
docker run --rm --network "$network" -e "PLATFORM_DB_URL=jdbc:postgresql://$prefix-platform-db:5432/platform" \
 -e SURVEY_REAL_ENGINE_FIXTURE=/fixture/evidence.json -v "$work/evidence.json:/fixture/evidence.json:ro" \
 -v "$REPO_ROOT/platform/services/business:/workspace" -v platform-maven-repo:/root/.m2/repository \
 -v "$REPO_ROOT/platform/deploy/platform-dev/maven-settings.xml:/root/.m2/settings.xml:ro" \
 -w /workspace maven:3.9-eclipse-temurin-21 mvn -o -B -q -Dtest=DeliveryRealEngineReminderIT test
python3 - "$REPO_ROOT/platform/services/business/target/surefire-reports/TEST-cn.mjy.platform.delivery.DeliveryRealEngineReminderIT.xml" <<'PYREPORT'
import sys, xml.etree.ElementTree as ET
r=ET.parse(sys.argv[1]).getroot()
if tuple(r.get(k) for k in ('tests','failures','errors','skipped')) != ('1','0','0','0'):
    raise SystemExit('integration test did not execute and pass exactly once')
PYREPORT
docker exec "$prefix-proxy" python -c 'import json,urllib.request; x=json.load(urllib.request.urlopen("http://127.0.0.1:8080/_fixture/metrics")); assert x=={"attempts":2,"forwarded":1,"successes":1}; print("fault proxy: one rejected read, one successful real-gateway read")'
echo 'real-engine reminder fixture passed'
