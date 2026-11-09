#!/usr/bin/env bash
# Hosted CI fixture preparation: synthetic data; do not source in production.
set -euo pipefail
[[ "${GITHUB_RUN_ID:-}" =~ ^[0-9]{1,20}$ && "${GITHUB_RUN_ATTEMPT:-}" =~ ^[0-9]{1,10}$ ]] || exit 2
[[ "${FIXTURE_ENGINE:-}" == mariadb || "${FIXTURE_ENGINE:-}" == postgresql ]] || exit 2
[[ "${FIXTURE_SCENARIO:-}" == reminder || "${FIXTURE_SCENARIO:-}" == crash ]] || exit 2
name="survey-crosslayer-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${FIXTURE_ENGINE}-${FIXTURE_SCENARIO}"
label="survey.hosted.crosslayer=$name"
container="$name-prep"
log_dir="${RUNNER_TEMP:?}/survey-crosslayer-private-$name"

cleanup() {
  local found cid owner attached marker
  marker="$log_dir/owned-volume"
  if [[ ! -f "$marker" || -L "$marker" ]]; then
    # A failed setup may have created a resource before recording ownership.
    # Report it without deleting anything unless both inventories are empty.
    found="$(docker ps -aq --filter "name=^/$container$")" || return 1
    [[ -z "$found" ]] || return 1
    found="$(docker volume ls --format '{{.Name}}' --filter "name=^${name}$")" || return 1
    [[ -z "$found" ]] || return 1
    return 0
  fi
  [[ "$(cat "$marker")" == "$name" ]] || return 1
  found="$(docker ps -aq --filter "name=^/$container$")" || return 1
  if [[ -n "$found" ]]; then
    [[ "$found" =~ ^[a-f0-9]{12,64}$ ]] || return 1
    cid="$(docker inspect -f '{{.Id}}' "$found")" || return 1
    owner="$(docker inspect -f '{{ index .Config.Labels "survey.hosted.crosslayer" }}' "$cid")" || return 1
    [[ "$cid" =~ ^[a-f0-9]{64}$ && "$owner" == "$name" ]] || return 1
    docker rm -f "$cid" >/dev/null || return 1
  fi
  found="$(docker volume ls --format '{{.Name}}' --filter "name=^${name}$")" || return 1
  [[ -z "$found" ]] && return 0
  [[ "$found" == "$name" ]] || return 1
  owner="$(docker volume inspect -f '{{ index .Labels "survey.hosted.crosslayer" }}' "$name")" || return 1
  [[ "$owner" == "$name" ]] || return 1
  attached="$(docker ps -aq --filter "volume=$name")" || return 1
  [[ -z "$attached" ]] || return 1
  docker volume rm "$name" >/dev/null || return 1
  found="$(docker volume ls --format '{{.Name}}' --filter "name=^${name}$")" || return 1
  [[ -z "$found" ]]
}

case "${1:-}" in
  cleanup) cleanup ;;
  prepare)
    [[ $# == 1 && -n "${GITHUB_OUTPUT:-}" ]] || exit 2
    umask 077
    mkdir -m 700 "$log_dir"
    found="$(docker volume ls --format '{{.Name}}' --filter "name=^${name}$")"
    [[ -z "$found" ]] || { echo 'Refusing an existing volume' >&2; exit 1; }
    found="$(docker ps -aq --filter "name=^/$container$")"
    [[ -z "$found" ]] || { echo 'Refusing an existing container' >&2; exit 1; }
    printf 'cache=%s\n' "$name" >> "$GITHUB_OUTPUT"
    docker volume create --label "$label" "$name" >/dev/null
    owner="$(docker volume inspect -f '{{ index .Labels "survey.hosted.crosslayer" }}' "$name")"
    [[ "$owner" == "$name" ]] || exit 1
    printf '%s\n' "$name" > "$log_dir/owned-volume"
    # Preparation is online; all subsequent fixture containers are offline on
    # an internal network and the warmed repository is mounted read-only.
    for image in mariadb:10.11 postgres:16-alpine python:3.11-slim maven:3.9-eclipse-temurin-21 selenium/standalone-firefox:latest; do
      docker pull "$image" >>"$log_dir/prepare.log" 2>&1
    done
    docker compose -f docker-compose.dev.yml build web >>"$log_dir/prepare.log" 2>&1
    docker build -t "$name-gateway" platform/tools/publish-gateway >>"$log_dir/prepare.log" 2>&1
    for mode in online offline; do
      offline=(); [[ "$mode" != offline ]] || offline=(-o)
      docker run --pull never --rm --name "$container" --label "$label" \
        -v "$name:/root/.m2/repository" -v "$PWD/platform/services/business:/workspace" \
        -v "$PWD/platform/deploy/platform-dev/maven-settings.xml:/root/.m2/settings.xml:ro" \
        -w /workspace maven:3.9-eclipse-temurin-21 mvn "${offline[@]}" -B -q -Dtest=ProbePayloadPolicyTest test >>"$log_dir/prepare.log" 2>&1
    done
    gateway="$(docker image inspect -f '{{.Id}}' "$name-gateway")"
    engine="$(docker image inspect -f '{{.Id}}' survey-web)"
    [[ "$gateway" =~ ^sha256:[a-f0-9]{64}$ && "$engine" =~ ^sha256:[a-f0-9]{64}$ ]] || exit 1
    printf 'gateway=%s\nengine=%s\n' "$gateway" "$engine" >> "$GITHUB_OUTPUT"
    ;;
  *) exit 2 ;;
esac
