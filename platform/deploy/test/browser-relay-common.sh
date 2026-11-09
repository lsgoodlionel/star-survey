#!/usr/bin/env bash
# Synthetic-only integration fixture; source and call init_fixture.

fixture_stage() {
  case "$1" in
    initialize|engine_install|engine_setup|survey_publish|gateway|platform_database|platform_db_create|platform_db_wait|platform_db_roles|platform_ready|browser|outbox|relay_first|relay_replay|worker_recovery|assertions) ;;
    *) return 1 ;;
  esac
  [[ -z "${SURVEY_CROSSLAYER_STAGE_FILE:-}" ]] || printf '%s\n' "$1" > "$SURVEY_CROSSLAYER_STAGE_FILE"
}

fixture_platform_state() {
  local status
  status="$(docker inspect -f '{{.State.Status}}' "$1" 2>/dev/null)" || status=unknown
  case "$status" in created|running|exited|dead|restarting|paused|removing) ;; *) status=unknown ;; esac
  [[ -z "${SURVEY_CROSSLAYER_PLATFORM_FILE:-}" ]] || printf '%s\n' "$status" > "$SURVEY_CROSSLAYER_PLATFORM_FILE"
}

fixture_docker_error() {
  [[ -z "${SURVEY_CROSSLAYER_ERROR_FILE:-}" ]] && return 0
  python3 "$REPO_ROOT/platform/deploy/test/hosted-crosslayer/classify_docker_error.py" "$1" > "$SURVEY_CROSSLAYER_ERROR_FILE"
}

fixture_probe_record() {
  local phase="$1" first="$2" count="$3" rc="$4" elapsed="$5"
  case "$phase" in start|loop|confirm) ;; *) return 1 ;; esac
  case "$first" in yes|no) ;; *) return 1 ;; esac
  [[ "$count" =~ ^[0-9]{1,2}$ && "$count" -le 60 ]] || return 1
  case "$rc" in 0|1|2|3|other|unknown) ;; *) return 1 ;; esac
  [[ "$elapsed" =~ ^[0-9]{1,4}$ && "$elapsed" -le 3600 ]] || return 1
  [[ -z "${SURVEY_CROSSLAYER_PROBE_FILE:-}" ]] || printf 'probe_phase=%s first_ready=%s attempt_count=%s pg_isready_rc=%s elapsed=%s\n' "$phase" "$first" "$count" "$rc" "$elapsed" > "$SURVEY_CROSSLAYER_PROBE_FILE"
}

fixture_wait_platform_database() {
  local container="$1" attempt=0 rc=unknown first=no started=$SECONDS elapsed=0
  fixture_stage platform_db_wait || return 1
  for attempt in {1..60}; do
    elapsed=$((SECONDS-started)); (( elapsed <= 3600 )) || elapsed=3600
    fixture_probe_record loop "$first" "$attempt" unknown "$elapsed" || return 1
    if docker exec "$container" pg_isready -h 127.0.0.1 -U postgres -d platform >/dev/null 2>&1; then
      rc=0; first=yes
      elapsed=$((SECONDS-started)); (( elapsed <= 3600 )) || elapsed=3600
      fixture_probe_record loop "$first" "$attempt" "$rc" "$elapsed" || return 1
      break
    else
      rc=$?
      case "$rc" in 0|1|2|3) ;; *) rc=other ;; esac
      elapsed=$((SECONDS-started)); (( elapsed <= 3600 )) || elapsed=3600
      fixture_probe_record loop "$first" "$attempt" "$rc" "$elapsed" || return 1
    fi
    sleep 1 || return 1
  done
  elapsed=$((SECONDS-started)); (( elapsed <= 3600 )) || elapsed=3600
  fixture_probe_record confirm "$first" "$attempt" unknown "$elapsed" || return 1
  if docker exec "$container" pg_isready -h 127.0.0.1 -U postgres -d platform >/dev/null; then
    rc=0
  else
    rc=$?
    case "$rc" in 0|1|2|3) ;; *) rc=other ;; esac
    elapsed=$((SECONDS-started)); (( elapsed <= 3600 )) || elapsed=3600
    fixture_probe_record confirm "$first" "$attempt" "$rc" "$elapsed" || return 1
    fixture_platform_state "$container" || :
    return 1
  fi
  elapsed=$((SECONDS-started)); (( elapsed <= 3600 )) || elapsed=3600
  fixture_probe_record confirm "$first" "$attempt" "$rc" "$elapsed" || return 1
  # pg_isready only checks server status; verify the synthetic database and
  # both identities used by the platform over the final TCP listener.
  fixture_stage platform_db_roles || return 1
  local role
  for role in platform_owner platform_app; do
    if ! docker exec -e "PGPASSWORD=$role" -e PGCONNECT_TIMEOUT=3 "$container" \
      psql -X -w -h 127.0.0.1 -U "$role" -d platform -v ON_ERROR_STOP=1 -Atqc 'SELECT 1' >/dev/null 2>&1; then
      fixture_platform_state "$container" || :
      return 1
    fi
  done
}

fixture_cleanup_record() {
  case "$1" in verified|unverified) ;; *) return 1 ;; esac
  [[ -z "${SURVEY_CROSSLAYER_CLEANUP_FILE:-}" ]] || printf '%s\n' "$1" > "$SURVEY_CROSSLAYER_CLEANUP_FILE"
}

validate_fixture_args() {
  [[ "$#" == 4 ]] || { echo 'usage: <mysql|pgsql> <maven-volume> <gateway-image-id> <engine-image-id>' >&2; return 2; }
  [[ "$1" == mysql || "$1" == pgsql ]] || { echo 'unsupported engine' >&2; return 2; }
  [[ "$2" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]] || { echo 'invalid Maven volume' >&2; return 2; }
  [[ "$3" =~ ^sha256:[a-f0-9]{64}$ && "$4" =~ ^sha256:[a-f0-9]{64}$ ]] || { echo 'local image IDs required' >&2; return 2; }
}

inspect_fixture_image() {
  local image="$1" inspection
  shift
  inspection="$(docker image inspect "$image")" || return 1
  python3 -c '
import json, re, sys
try:
    data = json.load(sys.stdin)
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise ValueError("unexpected image inspection")
    item = data[0]
    image_id = item["Id"]
    config = item["Config"]
    if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) or not isinstance(config, dict):
        raise ValueError("invalid image identity or config")
    declared = config.get("Volumes")
    if declared is None:
        declared = {}
    if not isinstance(declared, dict) or any(not isinstance(path, str) or not path.startswith("/") for path in declared):
        raise ValueError("invalid volume declarations")
    if not set(declared).issubset(set(sys.argv[1:])):
        raise ValueError("uncontrolled image volume")
    print(image_id)
except (ValueError, KeyError, TypeError):
    print("image volume inspection failed", file=sys.stderr)
    sys.exit(1)
' "$@" <<< "$inspection"
}

browser_relay_make_synthetic_config_readable() {
  # Only these audited, fixed test templates can be made readable. Never
  # chmod a production or dynamically generated config or the evidence tree.
  python3 - "$1" "$2" "$3" <<'PYCONFIG'
import hashlib, os, pathlib, stat, sys
expected = {
    "mysql": "47fe777e2387d941b7a0f33b3e12d25a9958c13f405785513963bfb8e1060607",
    "pgsql": "b41d9c6644a9dc4eed412dded9b458a9283ad4292918a1f4b229d32ee5faf668",
}
try:
    source, target, engine = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
    if engine not in expected or source.name != f"config.{engine}.php" or target.name != "config.php" or target.parent.name != ".runtime":
        raise ValueError("unexpected config location")
    for path in (source, target):
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise ValueError("unsafe config file")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected[engine]:
            raise ValueError("config is not the audited synthetic template")
    os.chmod(target, 0o644, follow_symlinks=False)
    if stat.S_IMODE(target.lstat().st_mode) != 0o644:
        raise ValueError("config permissions were not applied")
except (OSError, ValueError):
    print("synthetic configuration validation failed", file=sys.stderr)
    sys.exit(1)
PYCONFIG
}

validate_gateway_user() {
  [[ "$1" =~ ^([1-9][0-9]{0,5}):([1-9][0-9]{0,5})$ ]] || { echo 'gateway must declare a non-root numeric user and group' >&2; return 1; }
  gateway_uid="${BASH_REMATCH[1]}"
  gateway_gid="${BASH_REMATCH[2]}"
}

validate_gateway_mount() {
  # Inspect without printing environment values or gateway configuration.
  local inspection
  inspection="$(docker inspect "$1")" || return 1
  python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    if not isinstance(data, list) or len(data) != 1:
        raise ValueError("unexpected inspect result")
    item = data[0]
    tmpfs = item["HostConfig"]["Tmpfs"]
    mounts = item["Mounts"]
    if not isinstance(tmpfs, dict) or tmpfs.get("/var/lib/pubgw") != sys.argv[1]:
        raise ValueError("gateway tmpfs missing")
    if not isinstance(mounts, list) or any(m.get("Destination") == "/var/lib/pubgw" and m.get("Type") != "tmpfs" for m in mounts):
        raise ValueError("gateway path has a non-tmpfs mount")
except (ValueError, KeyError, TypeError, AttributeError):
    print("gateway mount validation failed", file=sys.stderr)
    sys.exit(1)
' "uid=$gateway_uid,gid=$gateway_gid,mode=0700" <<< "$inspection"
}

cleanup_owned_fixture() {
  local manual containers volumes networks name id label
  manual="$(docker ps -a --no-trunc --filter "label=survey.fixture.run=$prefix" --format '{{.Names}}')" || return 1
  containers="$(docker ps -a --no-trunc --filter "label=com.docker.compose.project=$prefix" --format '{{.Names}}')" || return 1
  volumes="$(docker volume ls --filter "label=com.docker.compose.project=$prefix" --format '{{.Name}}')" || return 1
  networks="$(docker network ls --no-trunc --filter "label=com.docker.compose.project=$prefix" --format '{{.ID}} {{.Name}}')" || return 1
  while IFS= read -r name; do
    [[ -z "$name" || "$name" == "$prefix-platform" || "$name" == "$prefix-proxy" || "$name" == "$prefix-gateway" || "$name" == "$prefix-platform-db" || "$name" == "$prefix-browser" ]] || return 1
    if [[ -n "$name" ]]; then
      id="$(docker inspect -f '{{.Id}}' "$name")" || return 1
      label="$(docker inspect -f '{{ index .Config.Labels "survey.fixture.run" }}' "$id")" || return 1
      [[ "$id" =~ ^[a-f0-9]{64}$ && "$label" == "$prefix" ]] || return 1
    fi
  done <<< "$manual"
  while IFS= read -r name; do
    [[ -z "$name" || "$name" == "$prefix-test-web" || "$name" == "$prefix-test-db" || "$name" == "$prefix-test-pg" ]] || return 1
    if [[ -n "$name" ]]; then
      id="$(docker inspect -f '{{.Id}}' "$name")" || return 1
      label="$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' "$id")" || return 1
      [[ "$id" =~ ^[a-f0-9]{64}$ && "$label" == "$prefix" ]] || return 1
    fi
  done <<< "$containers"
  while IFS= read -r name; do
    [[ -z "$name" || "$name" == "${prefix}_test-tmp" ]] || return 1
    if [[ -n "$name" ]]; then
      label="$(docker volume inspect -f '{{ index .Labels "com.docker.compose.project" }}' "$name")" || return 1
      [[ "$label" == "$prefix" ]] || return 1
    fi
  done <<< "$volumes"
  while read -r id name; do
    [[ -z "$id" ]] && continue
    [[ "$id" =~ ^[a-f0-9]{64}$ && "$name" == "${prefix}_default" ]] || return 1
    label="$(docker network inspect -f '{{ index .Labels "com.docker.compose.project" }}' "$id")" || return 1
    [[ "$label" == "$prefix" ]] || return 1
  done <<< "$networks"
  # All inventories were inspected before any removal. Recheck the immutable
  # container/network ID and label immediately before touching each resource.
  while IFS= read -r name; do
    [[ -z "$name" ]] && continue
    id="$(docker inspect -f '{{.Id}}' "$name")" || return 1
    label="$(docker inspect -f '{{ index .Config.Labels "survey.fixture.run" }}' "$id")" || return 1
    [[ "$id" =~ ^[a-f0-9]{64}$ && "$label" == "$prefix" ]] || return 1
    docker rm -f "$id" >/dev/null || return 1
  done <<< "$manual"
  while IFS= read -r name; do
    [[ -z "$name" ]] && continue
    id="$(docker inspect -f '{{.Id}}' "$name")" || return 1
    label="$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' "$id")" || return 1
    [[ "$id" =~ ^[a-f0-9]{64}$ && "$label" == "$prefix" ]] || return 1
    docker rm -f "$id" >/dev/null || return 1
  done <<< "$containers"
  while IFS= read -r name; do
    [[ -z "$name" ]] && continue
    label="$(docker volume inspect -f '{{ index .Labels "com.docker.compose.project" }}' "$name")" || return 1
    [[ "$name" == "${prefix}_test-tmp" && "$label" == "$prefix" ]] || return 1
    docker volume rm "$name" >/dev/null || return 1
  done <<< "$volumes"
  while read -r id name; do
    [[ -z "$id" ]] && continue
    label="$(docker network inspect -f '{{ index .Labels "com.docker.compose.project" }}' "$id")" || return 1
    [[ "$id" =~ ^[a-f0-9]{64}$ && "$name" == "${prefix}_default" && "$label" == "$prefix" ]] || return 1
    docker network rm "$id" >/dev/null || return 1
  done <<< "$networks"
  manual="$(docker ps -aq --filter "label=survey.fixture.run=$prefix")" || return 1
  containers="$(docker ps -aq --filter "label=com.docker.compose.project=$prefix")" || return 1
  volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=$prefix")" || return 1
  networks="$(docker network ls -q --filter "label=com.docker.compose.project=$prefix")" || return 1
  [[ -z "$manual$containers$volumes$networks" ]] || return 1
}

fixture_exit() {
  local original=$? cleanup_status=0
  trap - EXIT
  set +e
  cleanup_owned_fixture || cleanup_status=$?
  if [[ "$cleanup_status" != 0 ]]; then
    fixture_cleanup_record unverified || :
    printf 'Fixture cleanup unverified; original status=%s; resources and work retained: %s\n' "$original" "$work" >&2
    exit 5
  fi
  fixture_cleanup_record verified || :
  printf 'Fixture resources absent; private work retained: %s\n' "$work" >&2
  exit "$original"
}

init_fixture() {
  validate_fixture_args "$@"
  export TEST_DB="$1"
  maven_cache="$2"; gateway_image="$3"; web_image="$4"
  [[ "${SURVEY_DISPOSABLE_CHECKOUT:-}" == YES ]] || { echo 'disposable checkout acknowledgement required' >&2; return 2; }
  [[ -z "${DOCKER_HOST+x}" && -z "${DOCKER_CONTEXT+x}" ]] || { echo 'Docker endpoint overrides are forbidden' >&2; return 2; }
  REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
  REPO_ROOT="$(cd "$REPO_ROOT" && pwd -P)"; export REPO_ROOT
  [[ "$REPO_ROOT" != *:* && "$REPO_ROOT" != *$'\n'* && "$REPO_ROOT" != *$'\r'* ]] || return 2
  local status endpoint image found
  status="$(git -C "$REPO_ROOT" status --porcelain --untracked-files=normal)" || return 2
  [[ -z "$status" ]] || { echo 'checkout must be clean and disposable' >&2; return 2; }
  for name in .env enabletests application/config/config.php application/config/allowed_hosts.php platform/deploy/test/.runtime; do
    [[ ! -e "$REPO_ROOT/$name" && ! -L "$REPO_ROOT/$name" ]] || { echo 'pre-existing runtime state; refusing fixture' >&2; return 2; }
  done
  endpoint="$(docker context inspect --format '{{.Endpoints.docker.Host}}')" || return 2
  [[ "$endpoint" == unix:///* ]] || { echo 'local Unix Docker endpoint required' >&2; return 2; }
  docker info >/dev/null || return 2
  docker volume inspect "$maven_cache" >/dev/null || return 2
  # Every declared image volume must be exactly covered by a fixture mount.
  # Resolve mutable local tags to immutable IDs before creating any resources.
  gateway_image="$(inspect_fixture_image "$gateway_image" /var/lib/pubgw)" || return 2
  web_image="$(inspect_fixture_image "$web_image" /var/www/html /var/www/html/tmp /var/www/html/tests/tmp /var/www/html/upload /var/www/html/application/config/config.php /var/www/html/plugins/FaultInjector)" || return 2
  mariadb_image="$(inspect_fixture_image mariadb:10.11 /var/lib/mysql)" || return 2
  postgres_image="$(inspect_fixture_image postgres:16-alpine /var/lib/postgresql/data)" || return 2
  python_image="$(inspect_fixture_image python:3.11-slim)" || return 2
  maven_image="$(inspect_fixture_image maven:3.9-eclipse-temurin-21)" || return 2
  firefox_image="$(inspect_fixture_image selenium/standalone-firefox:latest)" || return 2
  # lib.sh checks this tag before it will use the supplied web image.
  docker image inspect survey-web >/dev/null 2>&1 || return 2
  local gateway_user
  gateway_user="$(docker image inspect -f '{{.Config.User}}' "$gateway_image")" || return 2
  validate_gateway_user "$gateway_user" || return 2
  prefix="survey-browser-relay-it-$(python3 -c 'import secrets; print(secrets.token_hex(12))')"
  [[ "$prefix" =~ ^survey-browser-relay-it-[a-f0-9]{24}$ ]] || return 2
  export SURVEY_TEST_PREFIX="$prefix" COMPOSE_PROJECT_NAME="$prefix"
  found="$(docker ps -aq --filter "name=^/$prefix-")" || return 2; [[ -z "$found" ]] || return 2
  found="$(docker volume ls -q --filter "name=^${prefix}_")" || return 2; [[ -z "$found" ]] || return 2
  found="$(docker network ls -q --filter "name=^${prefix}_")" || return 2; [[ -z "$found" ]] || return 2
  for name in container volume network; do
    case "$name" in
      container) found="$(docker ps -aq --filter "label=com.docker.compose.project=$prefix")" || return 2 ;;
      volume) found="$(docker volume ls -q --filter "label=com.docker.compose.project=$prefix")" || return 2 ;;
      network) found="$(docker network ls -q --filter "label=com.docker.compose.project=$prefix")" || return 2 ;;
    esac
    [[ -z "$found" ]] || return 2
  done
  umask 077
  work="$(mktemp -d "${TMPDIR:-/tmp}/survey-browser-relay.XXXXXXXX")"
  [[ "$work" == /* && "$work" != *:* && "$work" != *$'\n'* && "$work" != *$'\r'* ]] || return 2
  is_fresh=true
  source "$REPO_ROOT/platform/deploy/test/lib.sh"
  SURVEY_BROWSER_RELAY_SYNTHETIC_CONFIG=1
  local event_secret
  event_secret="$(python3 -c 'import hashlib,hmac;print(hmac.new(b"synthetic-engine-master-secret-0123456789abcdef",b"mjy-engine-events/v1/isolated-engine",hashlib.sha256).hexdigest())')"
  cat > "$work/internal.yml" <<YAML
networks:
  default:
    internal: true
services:
  test-db:
    image: "$mariadb_image"
    pull_policy: never
  test-pg:
    image: "$postgres_image"
    pull_policy: never
  test-web:
    image: "$web_image"
    pull_policy: never
    environment:
      MJY_ENGINE_INSTANCE_ID: isolated-engine
      MJY_PLATFORM_EVENTS_URL: http://platform:8080/internal/engine-events
      MJY_PLATFORM_EVENTS_SECRET: "$event_secret"
    volumes:
      - type: tmpfs
        target: /var/www/html/tests/tmp
      - type: tmpfs
        target: /var/www/html/upload
YAML
  COMPOSE=(docker compose -f "$REPO_ROOT/docker-compose.dev.yml" -f "$work/internal.yml" --profile test)
  trap fixture_exit EXIT
}
