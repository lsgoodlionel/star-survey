#!/usr/bin/env bash
# Synthetic-only negative tests for the platform PostgreSQL fixture readiness.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "$SCRIPT_DIR/browser-relay-common.sh"
[[ "${SURVEY_SYNTHETIC_PG_NEGATIVE:-}" == YES ]] || { echo 'synthetic test acknowledgement required' >&2; exit 2; }
[[ -z "${DOCKER_HOST+x}" && -z "${DOCKER_CONTEXT+x}" ]] || exit 2
endpoint="$(docker context inspect --format '{{.Endpoints.docker.Host}}')"
[[ "$endpoint" == unix:///* ]] || exit 2
docker info >/dev/null
image="$(inspect_fixture_image postgres:16-alpine /var/lib/postgresql/data)"
prefix="survey-pg-negative-$(python3 -c 'import secrets; print(secrets.token_hex(12))')"
[[ "$prefix" =~ ^survey-pg-negative-[a-f0-9]{24}$ ]] || exit 2
existing="$(docker ps -aq --filter "label=survey.synthetic.pg-negative=$prefix")"
[[ -z "$existing" ]] || exit 2
existing="$(docker ps -aq --filter "name=^/$prefix-")"
[[ -z "$existing" ]] || exit 2
umask 077
work="$(mktemp -d "${TMPDIR:-/tmp}/survey-pg-negative.XXXXXXXX")"
mkdir -m 700 "$work/private"
# Only the generated SQL and standard synthetic test passwords are in this dir.
chmod 755 "$work"
docker volume ls -q | sort > "$work/private/volumes-before"
cleanup_negative() {
  local names name id owner
  names="$(docker ps -a --no-trunc --filter "label=survey.synthetic.pg-negative=$prefix" --format '{{.Names}}')" || return 1
  while IFS= read -r name; do
    [[ -z "$name" ]] && continue
    [[ "$name" == "$prefix-missing-database" || "$name" == "$prefix-missing-owner" || "$name" == "$prefix-missing-app" ]] || return 1
    id="$(docker inspect -f '{{.Id}}' "$name")" || return 1
    owner="$(docker inspect -f '{{ index .Config.Labels "survey.synthetic.pg-negative" }}' "$id")" || return 1
    [[ "$id" =~ ^[a-f0-9]{64}$ && "$owner" == "$prefix" ]] || return 1
  done <<< "$names"
  while IFS= read -r name; do
    [[ -z "$name" ]] && continue
    id="$(docker inspect -f '{{.Id}}' "$name")" || return 1
    owner="$(docker inspect -f '{{ index .Config.Labels "survey.synthetic.pg-negative" }}' "$id")" || return 1
    [[ "$id" =~ ^[a-f0-9]{64}$ && "$owner" == "$prefix" ]] || return 1
    docker rm -f "$id" >/dev/null || return 1
  done <<< "$names"
  names="$(docker ps -aq --filter "label=survey.synthetic.pg-negative=$prefix")" || return 1
  [[ -z "$names" ]]
}
finish() {
  local original=$? cleanup_status=0
  trap - EXIT
  set +e
  cleanup_negative || cleanup_status=$?
  if [[ "$cleanup_status" != 0 ]]; then
    printf 'Synthetic negative-test cleanup unverified; original status=%s; prefix=%s; work=%s\n' "$original" "$prefix" "$work" >&2
    exit 5
  fi
  docker volume ls -q | sort > "$work/private/volumes-after" || exit 5
  cmp -s "$work/private/volumes-before" "$work/private/volumes-after" || { echo 'Volume inventory changed; no unknown resources removed' >&2; exit 5; }
  printf 'Synthetic negative-test cleanup verified; work retained: %s\n' "$work"
  exit "$original"
}
trap finish EXIT

cat > "$work/missing-database.sql" <<'SQL'
CREATE ROLE platform_owner LOGIN PASSWORD 'platform_owner';
CREATE ROLE platform_app LOGIN PASSWORD 'platform_app';
SQL
cat > "$work/missing-owner.sql" <<'SQL'
CREATE ROLE platform_app LOGIN PASSWORD 'platform_app';
CREATE DATABASE platform;
GRANT CONNECT ON DATABASE platform TO platform_app;
SQL
cat > "$work/missing-app.sql" <<'SQL'
CREATE ROLE platform_owner LOGIN PASSWORD 'platform_owner';
CREATE DATABASE platform OWNER platform_owner;
SQL
chmod 644 "$work"/*.sql
for case_name in missing-database missing-owner missing-app; do
  case "$case_name" in
    missing-database) expected='0|1|1' ;;
    missing-owner) expected='1|0|1' ;;
    missing-app) expected='1|1|0' ;;
  esac
  name="$prefix-$case_name"
  docker run --pull never -d --name "$name" --network none --label "survey.synthetic.pg-negative=$prefix" \
    --tmpfs /var/lib/postgresql/data -e POSTGRES_PASSWORD=synthetic-test-only \
    -v "$work/$case_name.sql:/docker-entrypoint-initdb.d/10-synthetic.sql:ro" "$image" >/dev/null
  for i in {1..60}; do docker exec "$name" pg_isready -q -h 127.0.0.1 -U postgres -d postgres && break; sleep 1; done
  docker exec "$name" pg_isready -q -h 127.0.0.1 -U postgres -d postgres
  actual="$(docker exec -e PGPASSWORD=synthetic-test-only "$name" psql -X -w -h 127.0.0.1 -U postgres -d postgres -Atqc "SELECT (EXISTS(SELECT 1 FROM pg_database WHERE datname='platform'))::int || '|' || (EXISTS(SELECT 1 FROM pg_roles WHERE rolname='platform_owner'))::int || '|' || (EXISTS(SELECT 1 FROM pg_roles WHERE rolname='platform_app'))::int")"
  [[ "$actual" == "$expected" ]] || { echo 'Synthetic negative precondition mismatch' >&2; exit 1; }
  export SURVEY_CROSSLAYER_STAGE_FILE="$work/private/stage" SURVEY_CROSSLAYER_PROBE_FILE="$work/private/probe" SURVEY_CROSSLAYER_PLATFORM_FILE="$work/private/platform"
  if fixture_wait_platform_database "$name"; then echo 'Missing synthetic prerequisite was incorrectly accepted' >&2; exit 1; fi
  [[ "$(cat "$SURVEY_CROSSLAYER_STAGE_FILE")" == platform_db_roles ]] || exit 1
  python3 - "$SURVEY_CROSSLAYER_PROBE_FILE" <<'PY'
from pathlib import Path
import sys
fields = dict(part.split('=', 1) for part in Path(sys.argv[1]).read_text().split())
assert fields['first_ready'] == 'yes' and fields['pg_isready_rc'] == '0'
PY
  printf '%s rejected after final TCP readiness\n' "$case_name"
  cleanup_negative
done
