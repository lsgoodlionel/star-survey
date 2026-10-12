#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  printf 'Usage: %s --assets DIR --metadata FILE --body FILE --repository OWNER/REPO\n' "$0" >&2
  exit 2
}

assets=""
metadata_file=""
body_file=""
repository=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --assets) assets="${2:-}"; shift 2 ;;
    --metadata) metadata_file="${2:-}"; shift 2 ;;
    --body) body_file="${2:-}"; shift 2 ;;
    --repository) repository="${2:-}"; shift 2 ;;
    *) usage ;;
  esac
done
[[ -d "$assets" && -f "$metadata_file" && -f "$body_file" ]] || usage
[[ "$repository" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || usage

release_dir="$(cd "$(dirname "$0")" && pwd -P)"
assets="$(cd "$assets" && pwd -P)"
metadata_file="$(cd "$(dirname "$metadata_file")" && pwd -P)/$(basename "$metadata_file")"
body_file="$(cd "$(dirname "$body_file")" && pwd -P)/$(basename "$body_file")"

read_metadata() {
  python3 - "$metadata_file" <<'PY'
import json
from pathlib import Path
import sys

value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for key in ("tagName", "name", "isPrerelease", "targetCommitish"):
    if key not in value:
        raise SystemExit(f"missing publication metadata: {key}")
print(value["tagName"])
print(value["name"])
print("true" if value["isPrerelease"] is True else "false")
print(value["targetCommitish"])
PY
}
identity=()
while IFS= read -r value; do identity+=("$value"); done < <(read_metadata)
tag="${identity[0]}"
title="${identity[1]}"
prerelease="${identity[2]}"
target_commit="${identity[3]}"
[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-rc\.[0-9]+)?$ && "$target_commit" =~ ^[0-9a-f]{40}$ ]] || exit 1

desired=()
while IFS= read -r path; do desired+=("$path"); done < <(find "$assets" -mindepth 1 -maxdepth 1 -type f -links 1 -print | LC_ALL=C sort)
[[ "${#desired[@]}" -gt 0 ]]
actual_file="$(mktemp)"
existing_dir="$(mktemp -d)"
trap 'rm -f "$actual_file"; rm -rf "$existing_dir"' EXIT

query_release() {
  gh release view "$tag" --repo "$repository" \
    --json tagName,name,body,isDraft,isPrerelease,targetCommitish,assets >"$actual_file"
}

repair_or_verify_metadata() {
  local status=0
  if python3 "$release_dir/render_publication.py" --compare \
    --expected "$metadata_file" --actual "$actual_file" --body "$body_file" --allow-draft-repair; then
    return 0
  else
    status=$?
  fi
  [[ "$status" -eq 10 ]] || return "$status"
  gh release edit "$tag" --repo "$repository" --title "$title" --notes-file "$body_file" \
    --prerelease="$prerelease"
  query_release
  python3 "$release_dir/render_publication.py" --compare \
    --expected "$metadata_file" --actual "$actual_file" --body "$body_file"
}

reconcile_assets() {
  local name names_file path
  names_file="$(mktemp)"
  existing_names=()
  python3 - "$actual_file" >"$names_file" <<'PY'
import json
from pathlib import Path
import sys

value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
names = [asset.get("name") for asset in value.get("assets", [])]
if any(not isinstance(name, str) or "/" in name or name in {"", ".", ".."} for name in names):
    raise SystemExit("unsafe existing Release asset inventory")
print("\n".join(sorted(names)))
PY
  while IFS= read -r name; do existing_names+=("$name"); done <"$names_file"
  rm -f "$names_file"
  for name in "${existing_names[@]}"; do
    [[ -n "$name" ]]
    path="$assets/$name"
    [[ -f "$path" && ! -L "$path" ]]
    gh release download "$tag" --repo "$repository" --pattern "$name" --dir "$existing_dir"
    cmp --silent "$path" "$existing_dir/$name"
  done
  for path in "${desired[@]}"; do
    name="${path##*/}"
    if ! printf '%s\n' "${existing_names[@]}" | grep -Fxq "$name"; then
      if ! gh release upload "$tag" "$path" --repo "$repository"; then
        query_release
        python3 - "$actual_file" "$name" <<'PY'
import json
from pathlib import Path
import sys

value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if sum(asset.get("name") == sys.argv[2] for asset in value.get("assets", [])) != 1:
    raise SystemExit("asset upload outcome is not uniquely recoverable")
PY
        gh release download "$tag" --repo "$repository" --pattern "$name" --dir "$existing_dir"
        cmp --silent "$path" "$existing_dir/$name"
      fi
    fi
  done
  query_release
  [[ "$(python3 - "$actual_file" <<'PY'
import json
from pathlib import Path
import sys
print(len(json.loads(Path(sys.argv[1]).read_text())["assets"]))
PY
  )" -eq "${#desired[@]}" ]]
}

if query_release; then
  repair_or_verify_metadata
else
  prerelease_args=()
  [[ "$prerelease" == true ]] && prerelease_args=(--prerelease)
  if ! gh release create "$tag" "${desired[@]}" --repo "$repository" --verify-tag --draft \
    --target "$target_commit" --title "$title" --notes-file "$body_file" --latest=false \
    "${prerelease_args[@]}"; then
    query_release
  fi
  query_release
  repair_or_verify_metadata
fi

reconcile_assets
query_release
repair_or_verify_metadata
if [[ "$(python3 - "$actual_file" <<'PY'
import json
from pathlib import Path
import sys
print("true" if json.loads(Path(sys.argv[1]).read_text())["isDraft"] is True else "false")
PY
)" == true ]]; then
  gh release edit "$tag" --repo "$repository" --draft=false
fi
query_release
python3 "$release_dir/render_publication.py" --compare \
  --expected "$metadata_file" --actual "$actual_file" --body "$body_file" --require-published
