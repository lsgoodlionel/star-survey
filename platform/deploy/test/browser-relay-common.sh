#!/usr/bin/env bash
# Synthetic-only integration fixture; source and call init_fixture.

validate_fixture_args() {
  [[ "$#" == 4 ]] || { echo 'usage: <mysql|pgsql> <maven-volume> <gateway-image-id> <engine-image-id>' >&2; return 2; }
  [[ "$1" == mysql || "$1" == pgsql ]] || { echo 'unsupported engine' >&2; return 2; }
  [[ "$2" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]] || { echo 'invalid Maven volume' >&2; return 2; }
  [[ "$3" =~ ^sha256:[a-f0-9]{64}$ && "$4" =~ ^sha256:[a-f0-9]{64}$ ]] || { echo 'local image IDs required' >&2; return 2; }
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
    printf 'Fixture cleanup unverified; original status=%s; resources and work retained: %s\n' "$original" "$work" >&2
    exit 5
  fi
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
  for image in "$gateway_image" "$web_image" survey-web python:3.11-slim postgres:16-alpine mariadb:10.11 maven:3.9-eclipse-temurin-21 selenium/standalone-firefox:latest; do
    docker image inspect "$image" >/dev/null 2>&1 || { echo 'required local test image unavailable' >&2; return 2; }
  done
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
  local event_secret
  event_secret="$(python3 -c 'import hashlib,hmac;print(hmac.new(b"synthetic-engine-master-secret-0123456789abcdef",b"mjy-engine-events/v1/isolated-engine",hashlib.sha256).hexdigest())')"
  cat > "$work/internal.yml" <<YAML
networks:
  default:
    internal: true
services:
  test-db:
    pull_policy: never
  test-pg:
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
