#!/usr/bin/env bash
set -Eeuo pipefail

[[ "${NATIVE_ACCEPTANCE_REAL:-}" == "1" ]]

required_variables=(
  CANDIDATE_DIR CANDIDATE_IDENTITY CANDIDATE_MANIFEST_SHA256
  CANDIDATE_BUNDLE_SHA256 ADMIN_IMAGE_DIGEST PLATFORM_IMAGE_DIGEST
  PUBLISH_GATEWAY_IMAGE_DIGEST EXPECTED_UBUNTU EXPECTED_ARCHITECTURE
  SURVEY_ACCEPTANCE_PUBLIC_HOST SURVEY_ACCEPTANCE_ADMIN_EMAIL EVIDENCE_DIR
)
for variable in "${required_variables[@]}"; do
  [[ -n "${!variable:-}" ]] || {
    printf 'required native acceptance variable is missing: %s\n' "$variable" >&2
    exit 2
  }
done

candidate_root="$(realpath "$CANDIDATE_DIR")"
identity_path="$(realpath "$CANDIDATE_IDENTITY")"
evidence_root="$(realpath -m "$EVIDENCE_DIR")"
work_root="$(mktemp -d "${RUNNER_TEMP:-/tmp}/survey-native-acceptance.XXXXXX")"
target="$work_root/primary"
runtime="$work_root/runtime"
baseline_dir="$candidate_root/baseline"
candidate_release_dir="$candidate_root/candidate"
admin_user="native-acceptance"
backup_name="native-before-upgrade"
acceptance_status="failed"
mkdir -p "$evidence_root" "$runtime"

redact() {
  sed -E \
    -e 's/((password|secret|token|private[_-]?key)[[:space:]]*[:=][[:space:]]*)[^[:space:]",}]+/\1[REDACTED]/Ig' \
    -e 's/(Authorization:[[:space:]]*(Bearer|Basic))[[:space:]]+[^[:space:]]+/\1 [REDACTED]/Ig'
}

run_ctl() {
  "$runtime/surveyctl" --target "$target" "$@"
}

capture_doctor() {
  local label="$1"
  local raw="$work_root/${label}.raw"
  run_ctl doctor >"$raw" 2>&1
  redact <"$raw" >"$evidence_root/${label}.json"
  rm -f "$raw"
  python3 - "$evidence_root/${label}.json" <<'PY'
import json
from pathlib import Path
import sys

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
checks = {item.get("id"): item for item in report.get("checks", []) if isinstance(item, dict)}
probe = checks.get("minimal-probe")
if report.get("status") != "ok" or not probe or probe.get("status") != "ok":
    raise SystemExit("doctor or minimal-probe did not pass")
PY
}

capture_logs() {
  local label="$1"
  local raw="$work_root/${label}.raw"
  set +e
  run_ctl logs >"$raw" 2>&1
  local result=$?
  set -e
  redact <"$raw" >"$evidence_root/${label}.log"
  rm -f "$raw"
  return "$result"
}

collect_evidence() {
  local result=$?
  trap - EXIT
  set +e
  if [[ "$result" -ne 0 && -f "$target/.surveyctl/state.json" ]]; then
    capture_logs "compose-logs-final"
    run_ctl doctor >"$work_root/doctor-final.raw" 2>&1
    redact <"$work_root/doctor-final.raw" >"$evidence_root/doctor-final.json"
    rm -f "$work_root/doctor-final.raw"
  fi
  docker ps --no-trunc --format '{{json .}}' >"$work_root/docker-ps.raw" 2>&1
  redact <"$work_root/docker-ps.raw" >"$evidence_root/docker-ps.jsonl"
  rm -f "$work_root/docker-ps.raw"
  python3 - "$evidence_root/summary.json" "$acceptance_status" "$result" <<'PY'
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

summary = {
    "schemaVersion": 1,
    "status": sys.argv[2],
    "exitCode": int(sys.argv[3]),
    "runner": {"ubuntu": os.environ["EXPECTED_UBUNTU"], "architecture": os.environ["EXPECTED_ARCHITECTURE"]},
    "candidate": {
        "manifestSha256": os.environ["CANDIDATE_MANIFEST_SHA256"],
        "bundleSha256": os.environ["CANDIDATE_BUNDLE_SHA256"],
        "images": {
            "admin": os.environ["ADMIN_IMAGE_DIGEST"],
            "platform": os.environ["PLATFORM_IMAGE_DIGEST"],
            "publish-gateway": os.environ["PUBLISH_GATEWAY_IMAGE_DIGEST"],
        },
    },
    "completedAt": datetime.now(timezone.utc).isoformat(),
}
Path(sys.argv[1]).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
  if [[ "$result" -ne 0 && -f "$target/.surveyctl/state.json" ]]; then
    run_ctl uninstall
  fi
  chmod -R a+rX "$evidence_root"
  rm -rf "$work_root"
  exit "$result"
}
trap collect_evidence EXIT

python3 - "$identity_path" "$candidate_release_dir/release.json" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys

identity_path, manifest_path = map(Path, sys.argv[1:])
identity = json.loads(identity_path.read_text(encoding="utf-8"))
manifest_payload = manifest_path.read_bytes()
manifest = json.loads(manifest_payload)
expected = {
    "admin": os.environ["ADMIN_IMAGE_DIGEST"],
    "platform": os.environ["PLATFORM_IMAGE_DIGEST"],
    "publish-gateway": os.environ["PUBLISH_GATEWAY_IMAGE_DIGEST"],
}
if identity.get("schemaVersion") != 1:
    raise SystemExit("unsupported candidate identity")
if hashlib.sha256(manifest_payload).hexdigest() != os.environ["CANDIDATE_MANIFEST_SHA256"]:
    raise SystemExit("candidate manifest checksum mismatch")
if identity.get("manifestSha256") != os.environ["CANDIDATE_MANIFEST_SHA256"]:
    raise SystemExit("shared candidate identity does not match manifest")
architecture = os.environ["EXPECTED_ARCHITECTURE"]
bundle = identity.get("bundles", {}).get(architecture, {})
if bundle.get("sha256") != os.environ["CANDIDATE_BUNDLE_SHA256"]:
    raise SystemExit("shared candidate identity does not match bundle")
if manifest.get("assets", {}).get("bundles", {}).get(architecture) != bundle:
    raise SystemExit("candidate manifest and identity bundle differ")
for name, digest in expected.items():
    value = identity.get("images", {}).get(name, {})
    if value.get("digest") != digest or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise SystemExit(f"candidate image identity mismatch: {name}")
    manifest_key = {"admin": "ADMIN_IMAGE", "platform": "PLATFORM_IMAGE", "publish-gateway": "PUBLISH_GATEWAY_IMAGE"}[name]
    if manifest.get("images", {}).get(manifest_key) != value.get("reference"):
        raise SystemExit(f"candidate manifest image differs from shared identity: {name}")
machine = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine())
if machine != architecture:
    raise SystemExit("runner architecture does not match native matrix")
release = {}
for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
    if "=" in line:
        key, value = line.split("=", 1)
        release[key] = value.strip().strip('"')
if release.get("ID") != "ubuntu" or release.get("VERSION_ID") != os.environ["EXPECTED_UBUNTU"]:
    raise SystemExit("runner Ubuntu version does not match native matrix")
PY

(cd "$candidate_release_dir" && sha256sum --check SHA256SUMS)
(cd "$baseline_dir" && sha256sum --check SHA256SUMS)
candidate_bundle="$(python3 - "$identity_path" <<'PY'
import json, os, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["bundles"][os.environ["EXPECTED_ARCHITECTURE"]]["name"])
PY
)"
[[ "$(sha256sum "$candidate_release_dir/$candidate_bundle" | awk '{print $1}')" == "$CANDIDATE_BUNDLE_SHA256" ]]

for image in admin platform publish-gateway; do
  image_reference="$(python3 - "$identity_path" "$image" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["images"][sys.argv[2]]["reference"])
PY
)"
  docker buildx imagetools inspect "$image_reference" >/dev/null
done

tar -xzf "$candidate_release_dir/$candidate_bundle" -C "$runtime"
chmod 0755 "$runtime/surveyctl" "$runtime/surveyctl.py"
grep -q 'survey-restore-' "$runtime/restore.py"

baseline_manifest_sha="$(sha256sum "$baseline_dir/release.json" | awk '{print $1}')"
expected_address_args=()
if [[ -n "${SURVEY_ACCEPTANCE_EXPECTED_PUBLIC_ADDRESS:-}" ]]; then
  expected_address_args=(--expected-public-address "$SURVEY_ACCEPTANCE_EXPECTED_PUBLIC_ADDRESS")
fi

# install
run_ctl install \
  --manifest "$baseline_dir/release.json" \
  --manifest-sha256 "$baseline_manifest_sha" \
  --public-host "$SURVEY_ACCEPTANCE_PUBLIC_HOST" \
  --admin-user "$admin_user" \
  --admin-email "$SURVEY_ACCEPTANCE_ADMIN_EMAIL" \
  "${expected_address_args[@]}"

# setup-probe
run_ctl setup-probe

# doctor and minimal-probe
capture_doctor "doctor-installed"

# backup
run_ctl backup --output "$backup_name"

# upgrade
run_ctl upgrade \
  --manifest "$candidate_release_dir/release.json" \
  --manifest-sha256 "$CANDIDATE_MANIFEST_SHA256"

# doctor and minimal-probe after upgrade
capture_doctor "doctor-upgraded"

# restore-target: surveyctl restore.py creates and verifies a survey-restore-* Compose project first.
run_ctl restore "$backup_name"

# doctor and minimal-probe after restore
capture_doctor "doctor-restored"
capture_logs "compose-logs-success"

# uninstall
run_ctl uninstall
acceptance_status="passed"
