#!/bin/bash
set -euo pipefail
umask 077
prefix="${SURVEY_TEST_PREFIX:?set a unique survey-delivery-crash- prefix}"
[[ "$prefix" =~ ^survey-delivery-crash-[a-z0-9][a-z0-9-]{0,35}$ ]] || { echo "invalid isolated fixture prefix" >&2; exit 1; }
repo=$(cd "$(dirname "$0")/../../.." && pwd -P)
for n in "$prefix-db" "$prefix-first" "$prefix-second"; do
  ! docker inspect "$n" >/dev/null 2>&1 || { echo 'fixture container exists' >&2; exit 1; }
done
! docker network inspect "$prefix-net" >/dev/null 2>&1 || { echo 'fixture network exists' >&2; exit 1; }
work=$(mktemp -d /tmp/survey-delivery-crash-XXXXXXXX)
cleanup() { docker rm -f "$prefix-first" "$prefix-second" "$prefix-db" >/dev/null 2>&1 || :; docker network rm "$prefix-net" >/dev/null 2>&1 || :; rm -rf "$work"; }
trap cleanup EXIT
docker network create --internal "$prefix-net" >/dev/null
[[ $(docker network inspect -f '{{.Internal}}' "$prefix-net") == true ]]
docker run -d --name "$prefix-db" --network "$prefix-net" --tmpfs /var/lib/postgresql/data \
 -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=postgres \
 -v "$repo/platform/deploy/platform-dev/init-db.sql:/docker-entrypoint-initdb.d/10-roles.sql:ro" postgres:16-alpine >/dev/null
for i in {1..90}; do docker exec "$prefix-db" pg_isready -U postgres -d platform >/dev/null 2>&1 && break; sleep 1; done
docker exec "$prefix-db" pg_isready -U postgres -d platform >/dev/null
run_phase() {
 local phase=$1
 docker run -d --name "$prefix-$phase" --network "$prefix-net" \
  -e "PLATFORM_DB_URL=jdbc:postgresql://$prefix-db:5432/platform" \
  -e SURVEY_PROCESS_CRASH_FIXTURE=/fixture -e "SURVEY_PROCESS_CRASH_PHASE=$phase" \
  -v "$work:/fixture" -v "$repo/platform/services/business:/workspace" \
  -v platform-maven-repo:/root/.m2/repository \
  -v "$repo/platform/deploy/platform-dev/maven-settings.xml:/root/.m2/settings.xml:ro" \
  -w /workspace maven:3.9-eclipse-temurin-21 mvn -o -B -q -Dtest=DeliveryProcessCrashIT test >/dev/null
}
run_phase first
for i in {1..180}; do
 [[ -f "$work/accepted" ]] && break
 [[ $(docker inspect -f '{{.State.Running}}' "$prefix-first") == true ]] || { docker logs "$prefix-first" >&2; exit 1; }
 sleep 1
done
[[ -f "$work/accepted" ]] || { echo 'first phase readiness timed out' >&2; exit 1; }
docker kill --signal KILL "$prefix-first" >/dev/null
[[ $(docker wait "$prefix-first") == 137 ]]
echo 'first isolated process killed after synthetic acceptance'
run_phase second
status=$(docker wait "$prefix-second")
docker logs "$prefix-second" >&2
[[ "$status" == 0 ]]
python3 - "$repo/platform/services/business/target/surefire-reports/TEST-cn.mjy.platform.delivery.DeliveryProcessCrashIT.xml" <<'PY'
import sys,xml.etree.ElementTree as ET
r=ET.parse(sys.argv[1]).getroot()
assert tuple(r.get(k) for k in ('tests','failures','errors','skipped'))==('1','0','0','0')
PY
echo 'process crash recovery passed'
