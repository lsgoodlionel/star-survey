#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "$SCRIPT_DIR/browser-relay-common.sh"
init_fixture "$@"
prepare_test_stack
# The private tmpfs hides checkout upload directories; restore the empty runtime layout.
docker exec "$CONTAINER" mkdir -p /var/www/html/upload/{admintheme,global,labels,plugins,surveys,themes/survey/generalfiles,twig/extensions}
enable_remote_control
db_query "INSERT INTO lime_plugins (name, plugin_type, active, priority, version, load_error) VALUES ('MjyPlatformBridge', 'user', 1, 0, '0.1.0', 0)" >/dev/null
docker exec "$CONTAINER" rm -rf tmp/runtime/cache
network=$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}' "$CONTAINER")
[[ "$(docker network inspect -f '{{.Internal}}' "$network")" == true ]]
ENGINE_CONTAINER="$CONTAINER" ENGINE_DB_CONTAINER="$prefix-$DB_SERVICE" EVIDENCE_FILE="$work/evidence.json" TEST_DB="$TEST_DB" python3 "$REPO_ROOT/platform/tests/e2e/browser_relay_prepare.py"
printf '{"isolated-engine":{"rpcUrl":"http://%s/index.php/admin/remotecontrol","user":"admin","passwordEnv":"ENGINE_PASSWORD"}}\n' "$CONTAINER" > "$work/engines.json"
chmod 644 "$work/engines.json"
image="$gateway_image"
docker create --pull never --label "survey.fixture.run=$prefix" --name "$prefix-gateway" --network "$network" --network-alias real-gateway \
 -e PUBGW_SHARED_SECRET=synthetic-gateway-secret-0123456789abcdef -e PUBGW_ENGINES_CONFIG=/app/engines.json \
 -e ENGINE_PASSWORD=password "$image" >/dev/null
docker cp "$work/engines.json" "$prefix-gateway:/app/engines.json"
docker start "$prefix-gateway" >/dev/null
for i in {1..60}; do [[ "$(docker inspect -f '{{.State.Health.Status}}' "$prefix-gateway" 2>/dev/null || true)" == healthy ]] && break; sleep 1; done
[[ "$(docker inspect -f '{{.State.Health.Status}}' "$prefix-gateway")" == healthy ]]
docker run --pull never -d --label "survey.fixture.run=$prefix" --name "$prefix-proxy" --network "$network" --network-alias fault-proxy \
 -v "$REPO_ROOT/platform/tests/e2e/real_engine_reminder_fault_proxy.py:/fixture/fault-proxy.py:ro" python:3.11-slim python /fixture/fault-proxy.py >/dev/null
docker run --pull never -d --label "survey.fixture.run=$prefix" --name "$prefix-platform-db" --network "$network" --tmpfs /var/lib/postgresql/data \
 -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=postgres \
 -v "$REPO_ROOT/platform/deploy/platform-dev/init-db.sql:/docker-entrypoint-initdb.d/10-roles.sql:ro" postgres:16-alpine >/dev/null
for i in {1..60}; do docker exec "$prefix-platform-db" pg_isready -U postgres -d platform >/dev/null 2>&1 && break; sleep 1; done
docker exec "$prefix-platform-db" pg_isready -U postgres -d platform >/dev/null
# The platform JVM exposes its real event endpoint only on this internal test network.
docker run --pull never -d --label "survey.fixture.run=$prefix" --name "$prefix-platform" --network "$network" --network-alias platform \
 -e "PLATFORM_DB_URL=jdbc:postgresql://$prefix-platform-db:5432/platform" \
 -e SURVEY_BROWSER_RELAY_FIXTURE=/fixture/evidence.json -e SURVEY_CRASH_PHASE=first -v "$work:/fixture" \
 -v "$REPO_ROOT/platform/services/business:/workspace" -v "${maven_cache}:/root/.m2/repository:ro" \
 -v "$REPO_ROOT/platform/deploy/platform-dev/maven-settings.xml:/root/.m2/settings.xml:ro" \
 -w /workspace maven:3.9-eclipse-temurin-21 mvn -o -B -q -Dtest=DeliveryBrowserRelayCrashIT test >/dev/null
for i in {1..180}; do
  [[ -f "$work/ready" ]] && break
  [[ "$(docker inspect -f '{{.State.Running}}' "$prefix-platform")" == true ]] || { docker logs "$prefix-platform" >&2; exit 1; }
  sleep 1
done
[[ -f "$work/ready" ]] || { echo 'platform test readiness timed out' >&2; exit 1; }
docker run --pull never --rm --network "container:$CONTAINER" --user 1200:1201 --cap-drop ALL --security-opt no-new-privileges --shm-size 1g \
 -v "$work/evidence.json:/fixture/evidence.json:ro" -v "$REPO_ROOT/platform/tests/e2e/browser_relay_firefox.py:/fixture/browser.py:ro" \
 --entrypoint python3 selenium/standalone-firefox:latest /fixture/browser.py
sid=$(python3 -c 'import json,sys;x=json.load(open(sys.argv[1]));assert isinstance(x["sid"],int) and x["sid"]>0;print(x["sid"])' "$work/evidence.json")
completed=$(db_query "SELECT count(*) FROM lime_mjyplatformbridge_event_log WHERE survey_id=$sid AND response_id=1 AND event_type='response.completed'")
[[ "$completed" -ge 1 ]] || { echo 'real completion outbox event missing' >&2; exit 1; }
if ! docker exec "$CONTAINER" php application/commands/console.php plugin cron >"$work/relay-cron-1.log" 2>&1; then
  echo "engine relay failed; private diagnostics retained in $work" >&2
  exit 1
fi
count_events() { docker exec "$prefix-platform-db" psql -U postgres -d platform -Atqc "SELECT count(*) FROM engine_event_inbox WHERE engine_instance_id='isolated-engine' AND survey_id=$sid AND response_id=1"; }
first=$(count_events)
[[ "$first" -ge 1 ]] || { echo 'relay did not reach platform inbox' >&2; exit 1; }
db_query "UPDATE lime_mjyplatformbridge_event_log SET delivered_at=NULL WHERE survey_id=$sid AND response_id=1" >/dev/null
if ! docker exec "$CONTAINER" php application/commands/console.php plugin cron >"$work/relay-cron-2.log" 2>&1; then
  echo "engine relay failed; private diagnostics retained in $work" >&2
  exit 1
fi
second=$(count_events)
[[ "$first" = "$second" ]] || { echo 'replayed events duplicated platform inbox' >&2; exit 1; }
printf 'real relay delivery and replay idempotence passed\n'
touch "$work/browser-relay-complete"
for i in {1..180}; do
  [[ -f "$work/provider-accepted" ]] && break
  [[ "$(docker inspect -f '{{.State.Running}}' "$prefix-platform")" == true ]] || { docker logs "$prefix-platform" >&2; exit 1; }
  sleep 1
done
[[ -f "$work/provider-accepted" ]] || { echo 'fake acceptance timed out' >&2; exit 1; }
platform_id="$(docker inspect -f '{{.Id}}' "$prefix-platform")"
[[ "$platform_id" =~ ^[a-f0-9]{64}$ ]]
[[ "$(docker inspect -f '{{ index .Config.Labels "survey.fixture.run" }}' "$platform_id")" == "$prefix" ]]
docker kill --signal KILL "$platform_id" >/dev/null
[[ "$(docker wait "$platform_id")" == 137 ]]
docker rm "$platform_id" >/dev/null
echo 'killed worker after synthetic provider acceptance'
docker run --pull never -d --label "survey.fixture.run=$prefix" --name "$prefix-platform" --network "$network" --network-alias platform \
 -e "PLATFORM_DB_URL=jdbc:postgresql://$prefix-platform-db:5432/platform" \
 -e SURVEY_BROWSER_RELAY_FIXTURE=/fixture/evidence.json -e SURVEY_CRASH_PHASE=second -v "$work:/fixture" \
 -v "$REPO_ROOT/platform/services/business:/workspace" -v "${maven_cache}:/root/.m2/repository:ro" \
 -v "$REPO_ROOT/platform/deploy/platform-dev/maven-settings.xml:/root/.m2/settings.xml:ro" \
 -w /workspace maven:3.9-eclipse-temurin-21 mvn -o -B -q -Dtest=DeliveryBrowserRelayCrashIT test >/dev/null
exit_code=$(docker wait "$prefix-platform")
docker logs "$prefix-platform" >&2
[[ "$exit_code" == 0 ]]
python3 - "$REPO_ROOT/platform/services/business/target/surefire-reports/TEST-cn.mjy.platform.delivery.DeliveryBrowserRelayCrashIT.xml" <<'PYREPORT'
import sys, xml.etree.ElementTree as ET
r=ET.parse(sys.argv[1]).getroot()
if tuple(r.get(k) for k in ('tests','failures','errors','skipped')) != ('1','0','0','0'):
    raise SystemExit('integration test did not execute and pass exactly once')
PYREPORT
docker exec "$prefix-proxy" python -c 'import json,urllib.request; x=json.load(urllib.request.urlopen("http://127.0.0.1:8080/_fixture/metrics")); assert x=={"attempts":2,"forwarded":1,"successes":1}; print("fault proxy: one rejected read, one successful real-gateway read")'
echo 'browser relay crash recovery fixture passed'
