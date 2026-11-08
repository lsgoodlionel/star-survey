#!/usr/bin/env bash
set -Eeuo pipefail

# Do not touch the host until containment, recovery and cleanup are verified.
printf 'Native lifecycle blocked before host changes: recovery and cleanup are not verified.\n' >&2
exit 1

[[ "${NATIVE_ACCEPTANCE_REAL:-}" == "1" ]]
[[ "${GITHUB_ACTIONS:-}" == "true" ]]
[[ "${SURVEY_NATIVE_ACCEPTANCE:-}" == "1" ]]

required_variables=(
  CANDIDATE_DIR CANDIDATE_IDENTITY CANDIDATE_MANIFEST_SHA256
  CANDIDATE_BUNDLE_SHA256 ADMIN_IMAGE_DIGEST PLATFORM_IMAGE_DIGEST
  PUBLISH_GATEWAY_IMAGE_DIGEST ENGINE_IMAGE_DIGEST EXPECTED_UBUNTU EXPECTED_ARCHITECTURE
  BASELINE_MODE SURVEY_ACCEPTANCE_PUBLIC_HOST SURVEY_ACCEPTANCE_ADMIN_EMAIL EVIDENCE_DIR
)
for variable in "${required_variables[@]}"; do
  if [[ -z "${!variable:-}" ]]; then
    printf 'required native acceptance variable is missing: %s\n' "$variable" >&2
    exit 2
  fi
done
if [[ "$BASELINE_MODE" != predecessor && "$BASELINE_MODE" != bootstrap ]]; then
  exit 2
fi
[[ "$SURVEY_ACCEPTANCE_PUBLIC_HOST" == *.test ]]
[[ "${SURVEY_ACCEPTANCE_EXPECTED_PUBLIC_ADDRESS:-}" == "127.0.0.1" ]]

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
evidence_helper="$script_dir/native_acceptance_evidence.py"
[[ -f "$evidence_helper" ]]
python3 "$evidence_helper" clean-host
export DOCKER_HOST=unix:///var/run/docker.sock
export COMPOSE_PROJECT_NAME=survey-production
candidate_root="$(realpath "$CANDIDATE_DIR")"
identity_path="$(realpath "$CANDIDATE_IDENTITY")"
evidence_root="$(realpath -m "$EVIDENCE_DIR")"
work_root="$(mktemp -d "${RUNNER_TEMP:-/tmp}/survey-native-acceptance.XXXXXX")"
target="$work_root/primary"
runtime="$work_root/runtime"
candidate_release_dir="$candidate_root/candidate"
baseline_dir="$candidate_root/baseline"
admin_user="native-acceptance"
backup_name="native-before-upgrade"
acceptance_status="failed"
last_step="identity"
upgrade_verified=false
hosts_marker="survey-native-acceptance-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-0}"
mkdir -p "$evidence_root" "$runtime"
[[ -f "$evidence_helper" ]]

run_ctl() {
  "$ctl_bin" --target "$target" "$@"
}

evidence_secret_args() {
  if [[ -d "$target/shared/secrets" ]]; then
    printf '%s\n' --secrets-dir "$target/shared/secrets"
  fi
}

write_doctor_evidence() {
  local label="$1"
  local raw="$work_root/${label}.json"
  local doctor_status=0
  if run_ctl doctor >"$raw"; then
    doctor_status=0
  else
    doctor_status=$?
  fi
  local args=()
  while IFS= read -r item; do args+=("$item"); done < <(evidence_secret_args)
  python3 "$evidence_helper" "${args[@]}" doctor --input "$raw" --output "$evidence_root/${label}.json"
  rm -f "$raw"
  if [[ "$doctor_status" -ne 0 ]]; then
    return "$doctor_status"
  fi
  python3 - "$evidence_root/${label}.json" "$label" <<'PY'
import json
from pathlib import Path
import sys

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
required = {"ports", "internal-exposure", "runtime-exposure", "tls", "containers",
            "migrations", "volumes", "backups", "minimal-probe"}
items = report.get("checks", [])
if (not isinstance(items, list) or len(items) != len(required)
        or any(not isinstance(item, dict) or item.get("id") not in required for item in items)):
    raise SystemExit("doctor required check set is invalid")
checks = {item["id"]: item for item in items}
if set(checks) != required:
    raise SystemExit("doctor required check set is missing or duplicated")
all_ok = report.get("status") == "ok" and all(item.get("status") == "ok" for item in items)
# A clean installation has no backup before the next lifecycle step creates it.
# This exception is exact and first-install-only; later checks must verify backup.
initial_backup_warning = (
    sys.argv[2] == "doctor-installed" and report.get("status") == "warning"
    and checks["backups"].get("status") == "warning"
    and checks["backups"].get("summary") == "no completed backup exists yet"
    and all(checks[name].get("status") == "ok" for name in required - {"backups"})
)
if not (all_ok or initial_backup_warning):
    raise SystemExit("doctor required health, TLS, product journey or backup checks did not pass")
PY
}

write_summary() {
  python3 "$evidence_helper" summary \
    --output "$evidence_root/summary.json" \
    --status "$acceptance_status" \
    --exit-code "$1" \
    --last-step "$last_step" \
    --upgrade-verified "$upgrade_verified" \
    --doctor-evidence "$evidence_root/doctor-installed.json" \
    --upgrade-data-evidence "$evidence_root/upgrade-data.json"
}

# Resource labels and fixture markers alone cannot prove that all writers and
# restore helpers stopped. Keep uninstall blocked until an independently
# verified containment and ownership protocol is implemented.
require_fixture_cleanup_proof() {
  printf 'Native fixture cleanup is blocked: exclusive ownership and restore quiescence are unverified.\n' >&2
  return 1
}

collect_evidence() {
  local result=$?
  trap - EXIT
  set +e
  # Preserve host and fixture state: cleanup cannot be safely authorized yet.
  if ! docker ps --format '{"id":"{{.ID}}","image":"{{.Image}}","name":"{{.Names}}","state":"{{.State}}","health":"{{.Status}}"}' >"$work_root/services.jsonl"; then
    result=5
    acceptance_status="failed"
  fi
  local args=()
  while IFS= read -r item; do args+=("$item"); done < <(evidence_secret_args)
  python3 "$evidence_helper" "${args[@]}" services --input "$work_root/services.jsonl" --output "$evidence_root/services.json"
  local services_result=$?
  if [[ "$services_result" -ne 0 ]]; then
    result=5
    acceptance_status="failed"
  fi
  if [[ "$result" -ne 0 ]] && docker inspect --format '{{json .State.Health}}' survey-production-engine-1 >"$work_root/engine-health.json" 2>/dev/null; then
    if ! python3 "$evidence_helper" engine-health --input "$work_root/engine-health.json" --output "$evidence_root/engine-health.json"; then
      result=5
    fi
  fi
  if [[ "$result" -ne 0 ]] && docker inspect survey-production-publish-gateway-1 >/dev/null 2>&1; then
    if ! docker logs --tail 200 survey-production-publish-gateway-1 2>&1 | python3 "$evidence_helper" gateway-diagnostic --output "$evidence_root/gateway-diagnostic.json"; then
      result=5
    fi
  fi
  if [[ "$result" -eq 0 && "$acceptance_status" != passed ]]; then result=5; fi
  if [[ "$result" -ne 0 ]]; then acceptance_status="failed"; fi
  write_summary "$result"
  local summary_result=$?
  if [[ "$summary_result" -ne 0 ]]; then result=5; fi
  python3 "$evidence_helper" "${args[@]}" scan --directory "$evidence_root"
  local scan_result=$?
  if [[ "$scan_result" -ne 0 ]]; then result=5; fi
  if [[ "$result" -ne 0 ]]; then
    acceptance_status="failed"
    if ! write_summary "$result"; then result=5; fi
  fi
  printf 'Native fixture and host state retained for investigation.\n' >&2
  exit "$result"
}
trap collect_evidence EXIT

last_step="identity"
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
profile = {
    "profile": "github-actions-native-v1",
    "minimumFreeBytes": 8 * 1024**3,
    "tlsMode": "local-ca",
    "diskEvidence": "separately-tested",
}
if identity.get("schemaVersion") != 1 or manifest.get("nativeAcceptance") != profile:
    raise SystemExit("candidate is not declared for the fixed native acceptance profile")
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
expected = {
    "admin": ("ADMIN_IMAGE", os.environ["ADMIN_IMAGE_DIGEST"]),
    "platform": ("PLATFORM_IMAGE", os.environ["PLATFORM_IMAGE_DIGEST"]),
    "publish-gateway": ("PUBLISH_GATEWAY_IMAGE", os.environ["PUBLISH_GATEWAY_IMAGE_DIGEST"]),
    "engine": ("ENGINE_IMAGE", os.environ["ENGINE_IMAGE_DIGEST"]),
}
for name, (manifest_key, digest) in expected.items():
    value = identity.get("images", {}).get(name, {})
    if value.get("digest") != digest or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise SystemExit(f"candidate image identity mismatch: {name}")
    if manifest.get("images", {}).get(manifest_key) != value.get("reference"):
        raise SystemExit(f"candidate manifest image differs from shared identity: {name}")
baseline = identity.get("baseline")
if not isinstance(baseline, dict) or baseline.get("mode") != os.environ["BASELINE_MODE"]:
    raise SystemExit("baseline policy and candidate identity differ")
if baseline["mode"] == "predecessor":
    baseline_path = identity_path.parent / "baseline" / "release.json"
    baseline_payload = baseline_path.read_bytes()
    if baseline.get("manifestSha256") != hashlib.sha256(baseline_payload).hexdigest():
        raise SystemExit("predecessor manifest checksum does not match shared identity")
    baseline_manifest = json.loads(baseline_payload)
    for key in ("version", "commit", "images"):
        if baseline.get(key) != baseline_manifest.get(key):
            raise SystemExit(f"predecessor identity mismatch: {key}")
    if baseline.get("bundles") != baseline_manifest.get("assets", {}).get("bundles"):
        raise SystemExit("predecessor bundle identity mismatch")
elif set(baseline) != {"mode"}:
    raise SystemExit("bootstrap identity must not contain a synthetic predecessor")
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
candidate_bundle="$(jq -er --arg architecture "$EXPECTED_ARCHITECTURE" '.bundles[$architecture].name' "$identity_path")"
[[ "$(sha256sum "$candidate_release_dir/$candidate_bundle" | awk '{print $1}')" == "$CANDIDATE_BUNDLE_SHA256" ]]
python3 "$evidence_helper" fixture-owner-create --work "$work_root" --marker "$work_root/owner.json"
python3 "$evidence_helper" fixture-owner-verify --work "$work_root" --marker "$work_root/owner.json"

last_step="image-manifests"
python3 - "$identity_path" <<'PY' >"$work_root/image-references"
import json, sys
identity = json.load(open(sys.argv[1], encoding="utf-8"))
for value in identity["images"].values():
    print(value["reference"])
if identity["baseline"]["mode"] == "predecessor":
    for value in identity["baseline"]["images"].values():
        print(value)
PY
while IFS= read -r image_reference; do
  docker buildx imagetools inspect "$image_reference" >/dev/null
done <"$work_root/image-references"
rm -f "$work_root/image-references"

tar -xzf "$candidate_release_dir/$candidate_bundle" -C "$runtime"
chmod 0755 "$runtime/surveyctl" "$runtime/surveyctl.py"
grep -q 'survey-restore-' "$runtime/restore.py"

if [[ "$BASELINE_MODE" == predecessor ]]; then
  baseline_manifest_sha="$(sha256sum "$baseline_dir/release.json" | awk '{print $1}')"
  baseline_bundle="$(jq -er --arg architecture "$EXPECTED_ARCHITECTURE" '.baseline.bundles[$architecture].name' "$identity_path")"
  baseline_bundle_sha="$(jq -er --arg architecture "$EXPECTED_ARCHITECTURE" '.baseline.bundles[$architecture].sha256' "$identity_path")"
  [[ "$(sha256sum "$baseline_dir/$baseline_bundle" | awk '{print $1}')" == "$baseline_bundle_sha" ]]
  install_manifest="$baseline_dir/release.json"
  install_manifest_sha="$baseline_manifest_sha"
else
  install_manifest="$candidate_release_dir/release.json"
  install_manifest_sha="$CANDIDATE_MANIFEST_SHA256"
fi
ctl_bin="$runtime/surveyctl"
chmod 0755 "$ctl_bin"

last_step="preflight-disk"
actual_free_bytes="$(( $(df -Pk "$work_root" | awk 'NR==2 {print $4}') * 1024 ))"
profile_minimum_bytes="$(jq -er '.nativeAcceptance.minimumFreeBytes' "$candidate_release_dir/release.json")"
[[ "$actual_free_bytes" -ge "$profile_minimum_bytes" ]]
python3 "$evidence_helper" preflight \
  --output "$evidence_root/preflight.json" \
  --actual-free-bytes "$actual_free_bytes" \
  --required-free-bytes "$profile_minimum_bytes"

last_step="local-tls"
printf '127.0.0.1 %s # %s\n' "$SURVEY_ACCEPTANCE_PUBLIC_HOST" "$hosts_marker" >> /etc/hosts
export SSL_CERT_FILE="$target/.surveyctl/native-acceptance-ca.crt"
export SURVEY_NATIVE_ACCEPTANCE_PROFILE_MANIFEST="$candidate_release_dir/release.json"
export SURVEY_NATIVE_ACCEPTANCE_PROFILE_MANIFEST_SHA256="$CANDIDATE_MANIFEST_SHA256"

expected_address_args=(--expected-public-address "$SURVEY_ACCEPTANCE_EXPECTED_PUBLIC_ADDRESS")
last_step="install"
run_ctl install \
  --manifest "$install_manifest" \
  --manifest-sha256 "$install_manifest_sha" \
  --public-host "$SURVEY_ACCEPTANCE_PUBLIC_HOST" \
  --admin-user "$admin_user" \
  --admin-email "$SURVEY_ACCEPTANCE_ADMIN_EMAIL" \
  "${expected_address_args[@]}"

last_step="setup-probe"
run_ctl setup-probe

last_step="doctor-installed"
write_doctor_evidence "doctor-installed"

last_step="backup"
run_ctl backup --output "$backup_name"

if [[ "$BASELINE_MODE" == predecessor ]]; then
  last_step="upgrade-data-continuity"
  python3 "$evidence_helper" upgrade-data-probe \
    --runtime "$runtime" --ctl "$ctl_bin" --target "$target" --host "$SURVEY_ACCEPTANCE_PUBLIC_HOST" \
    --baseline-manifest "$baseline_dir/release.json" --baseline-sha "$baseline_manifest_sha" \
    --candidate-manifest "$candidate_release_dir/release.json" --candidate-sha "$CANDIDATE_MANIFEST_SHA256" \
    --output "$evidence_root/upgrade-data.json"
  ctl_bin="$target/releases/$(jq -er '.version' "$candidate_release_dir/release.json")/surveyctl"
  last_step="doctor-upgraded"
  write_doctor_evidence "doctor-upgraded"
  upgrade_verified=true
fi

last_step="restore-target-survey-restore-project"
run_ctl restore "$backup_name"

last_step="doctor-restored"
write_doctor_evidence "doctor-restored"

last_step="uninstall"
require_fixture_cleanup_proof
run_ctl uninstall
acceptance_status="passed"
