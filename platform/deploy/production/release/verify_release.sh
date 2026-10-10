#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  printf 'Usage: %s --assets DIR --repository OWNER/REPO --target-tag TAG --source-tag TAG [--target-commit SHA]\n' "$0" >&2
  exit 2
}

assets=""
repository=""
target_tag=""
source_tag=""
target_commit=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --assets) assets="${2:-}"; shift 2 ;;
    --repository) repository="${2:-}"; shift 2 ;;
    --target-tag) target_tag="${2:-}"; shift 2 ;;
    --source-tag) source_tag="${2:-}"; shift 2 ;;
    --target-commit) target_commit="${2:-}"; shift 2 ;;
    *) usage ;;
  esac
done

[[ -n "$assets" && -n "$repository" && -n "$target_tag" && -n "$source_tag" ]] || usage
[[ "$repository" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || usage
tag_pattern='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-rc\.(0|[1-9][0-9]*))?$'
[[ "$target_tag" =~ $tag_pattern && "$source_tag" =~ $tag_pattern ]] || usage
[[ -z "$target_commit" || "$target_commit" =~ ^[0-9a-f]{40}$ ]] || usage
assets="$(cd "$assets" && pwd -P)"

python3 - "$assets" "$target_tag" "$source_tag" "$target_commit" <<'PY'
import hashlib
import json
from pathlib import Path
import re
import stat
import sys
import unicodedata

root, target_tag, source_tag, target_commit = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
sha = re.compile(r"[0-9a-f]{64}")
digest = re.compile(r"sha256:[0-9a-f]{64}")
rc = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-rc\.(0|[1-9][0-9]*)")
stable = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")

def fail(message: str) -> None:
    raise SystemExit(message)

def regular(name: str) -> Path:
    if Path(name).name != name or name in {".", ".."}:
        fail(f"unsafe release asset name: {name}")
    path = root / name
    try:
        metadata = path.lstat()
    except OSError:
        fail(f"missing release asset: {name}")
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        fail(f"release asset is not an unlinked regular file: {name}")
    return path

def load(name: str) -> dict:
    try:
        value = json.loads(regular(name).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"invalid JSON release asset {name}: {exc}")
    if not isinstance(value, dict):
        fail(f"JSON release asset must be an object: {name}")
    return value

manifest_path = regular("release.json")
manifest = load("release.json")
version = manifest.get("version")
if source_tag != f"v{version}" or rc.fullmatch(source_tag) is None:
    fail("release source must be the matching verified RC")
target_rc = rc.fullmatch(target_tag)
target_stable = stable.fullmatch(target_tag)
if target_rc is not None:
    if target_tag != source_tag:
        fail("RC publication cannot substitute another source tag")
elif target_stable is not None:
    source_rc = rc.fullmatch(source_tag)
    if source_rc is None or target_stable.groups() != source_rc.groups()[:3]:
        fail("stable tag and RC source version differ")
else:
    fail("unsupported target release tag")
if manifest.get("channel") != "candidate":
    fail("publication assets must originate from the immutable candidate channel")
if manifest.get("schemaVersion") != 1 or re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("commit", ""))) is None:
    fail("release manifest schema or commit is invalid")
if target_commit and manifest.get("commit") != target_commit:
    fail("tag commit differs from the tested RC commit")
if manifest.get("supportedHosts") != {"ubuntu": ["22.04", "24.04"], "architectures": ["amd64", "arm64"]}:
    fail("release host support matrix is incomplete")
if manifest.get("nativeAcceptance") != {
    "profile": "github-actions-native-v1", "minimumFreeBytes": 8 * 1024**3,
    "tlsMode": "local-ca", "diskEvidence": "separately-tested",
}:
    fail("release native acceptance profile is invalid")

bundles = manifest.get("assets", {}).get("bundles")
if not isinstance(bundles, dict) or set(bundles) != {"amd64", "arm64"}:
    fail("release manifest does not contain both architecture bundles")
bundle_names: set[str] = set()
for architecture, record in bundles.items():
    if not isinstance(record, dict) or set(record) != {"name", "sha256"}:
        fail(f"invalid bundle record: {architecture}")
    name, expected = record["name"], record["sha256"]
    if not isinstance(expected, str) or sha.fullmatch(expected) is None:
        fail(f"invalid bundle checksum: {architecture}")
    if architecture not in name or hashlib.sha256(regular(name).read_bytes()).hexdigest() != expected:
        fail(f"bundle identity mismatch: {architecture}")
    bundle_names.add(name)

all_images = manifest.get("images")
required_images = {
    "CADDY_IMAGE", "ADMIN_IMAGE", "PLATFORM_IMAGE", "PUBLISH_GATEWAY_IMAGE",
    "ENGINE_IMAGE", "POSTGRES_IMAGE", "MARIADB_IMAGE",
}
if not isinstance(all_images, dict) or set(all_images) != required_images:
    fail("release manifest image inventory is incomplete")
if any(re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", value) is None for value in all_images.values() if isinstance(value, str)) or any(not isinstance(value, str) for value in all_images.values()):
    fail("release manifest contains a mutable or invalid image reference")

internal_lines = regular("SHA256SUMS").read_text(encoding="ascii").splitlines()
internal: dict[str, str] = {}
for line in internal_lines:
    match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._-]+)", line)
    if match is None or match.group(2) in internal:
        fail("SHA256SUMS is malformed or contains duplicate assets")
    internal[match.group(2)] = match.group(1)
expected_internal = bundle_names | {"release.json", "release-notes.md"}
if set(internal) != expected_internal:
    fail("SHA256SUMS asset inventory is incomplete or contains extras")
for name, expected in internal.items():
    if hashlib.sha256(regular(name).read_bytes()).hexdigest() != expected:
        fail(f"SHA256SUMS mismatch: {name}")

readiness = load("release-readiness.json")
readiness_keys = {
    "schemaVersion", "candidateVersion", "candidateCommit", "manifestSha256",
    "amd64BundleSha256", "arm64BundleSha256", "adminManifestDigest",
    "platformManifestDigest", "gatewayManifestDigest", "matrixEvidenceSha256",
    "baselineMode", "nativeMatrixCount", "promotable", "reason",
}
if set(readiness) != readiness_keys or readiness.get("schemaVersion") != 1:
    fail("native readiness schema is not fail closed")
matrix_hashes = readiness.get("matrixEvidenceSha256")
if not isinstance(matrix_hashes, dict) or set(matrix_hashes) != {
    "ubuntu-22-04-amd64", "ubuntu-24-04-amd64", "ubuntu-22-04-arm64", "ubuntu-24-04-arm64",
} or any(not isinstance(value, str) or sha.fullmatch(value) is None for value in matrix_hashes.values()):
    fail("native readiness evidence identity is incomplete")
manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
images = all_images
image_map = {
    "admin": ("ADMIN_IMAGE", "adminManifestDigest"),
    "platform": ("PLATFORM_IMAGE", "platformManifestDigest"),
    "publish-gateway": ("PUBLISH_GATEWAY_IMAGE", "gatewayManifestDigest"),
}
if readiness.get("candidateVersion") != version or readiness.get("candidateCommit") != manifest.get("commit"):
    fail("native readiness candidate identity differs from release manifest")
if readiness.get("manifestSha256") != manifest_sha or readiness.get("nativeMatrixCount") != 4:
    fail("native readiness does not bind the release manifest and complete matrix")
if readiness.get("amd64BundleSha256") != bundles["amd64"]["sha256"] or readiness.get("arm64BundleSha256") != bundles["arm64"]["sha256"]:
    fail("native readiness bundle identity differs from release manifest")
for name, (manifest_key, readiness_key) in image_map.items():
    reference = images.get(manifest_key, "")
    if not isinstance(reference, str) or "@" not in reference:
        fail(f"release image reference is invalid: {name}")
    image_digest = reference.rsplit("@", 1)[1]
    if digest.fullmatch(image_digest) is None or readiness.get(readiness_key) != image_digest:
        fail(f"native readiness image identity differs: {name}")
if target_stable is not None and (readiness.get("promotable") is not True or readiness.get("reason") != "upgrade-verified"):
    fail("stable promotion requires upgrade-verified native readiness")
if target_rc is not None and readiness.get("reason") not in {"upgrade-verified", "bootstrap-not-promotable"}:
    fail("RC readiness reason is invalid")

sboms = {f"sbom-{name}.spdx.json" for name in image_map}
cosign_bundles = {f"cosign-{name}.sigstore.json" for name in image_map}
for name in sboms:
    sbom = load(name)
    if sbom.get("spdxVersion") not in {"SPDX-2.2", "SPDX-2.3"} or sbom.get("SPDXID") != "SPDXRef-DOCUMENT":
        fail(f"invalid SPDX SBOM: {name}")
for name in cosign_bundles | {"cosign-release.sigstore.json"}:
    load(name)

top_lines = regular("RELEASE_SHA256SUMS").read_text(encoding="ascii").splitlines()
top: dict[str, str] = {}
for line in top_lines:
    match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._-]+)", line)
    if match is None or match.group(2) in top:
        fail("RELEASE_SHA256SUMS is malformed or contains duplicate assets")
    top[match.group(2)] = match.group(1)
expected_top = expected_internal | {"SHA256SUMS", "release-readiness.json"} | sboms | cosign_bundles
if set(top) != expected_top:
    fail("RELEASE_SHA256SUMS inventory is incomplete or contains extras")
for name, expected in top.items():
    if hashlib.sha256(regular(name).read_bytes()).hexdigest() != expected:
        fail(f"RELEASE_SHA256SUMS mismatch: {name}")
expected_files = expected_top | {"RELEASE_SHA256SUMS", "cosign-release.sigstore.json"}
actual_files: set[str] = set()
actual_dirs: set[str] = set()
collision_keys: dict[str, str] = {}
pending = [root]
while pending:
    directory = pending.pop()
    for path in directory.iterdir():
        relative = path.relative_to(root).as_posix()
        key = unicodedata.normalize("NFC", relative).casefold()
        previous = collision_keys.get(key)
        if previous is not None and previous != relative:
            fail(f"release directory contains a case or Unicode path collision: {previous}, {relative}")
        collision_keys[key] = relative
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            fail(f"release directory contains a symlink: {relative}")
        if stat.S_ISDIR(metadata.st_mode):
            actual_dirs.add(relative)
            pending.append(path)
        elif stat.S_ISREG(metadata.st_mode):
            if metadata.st_nlink != 1:
                fail(f"release directory contains a hard-linked file: {relative}")
            actual_files.add(relative)
        else:
            fail(f"release directory contains a special entry: {relative}")
if actual_dirs or actual_files != expected_files:
    fail("release directory contains missing or untracked assets")
PY

(cd "$assets" && sha256sum --check SHA256SUMS)
(cd "$assets" && sha256sum --check RELEASE_SHA256SUMS)

workflow_identity="https://github.com/${repository}/.github/workflows/release.yml@refs/tags/${source_tag}"
issuer="https://token.actions.githubusercontent.com"
source_commit="$(jq -er '.commit' "$assets/release.json")"
source_ref="refs/tags/$source_tag"

cosign verify-blob \
  --bundle "$assets/cosign-release.sigstore.json" \
  --certificate-identity "$workflow_identity" \
  --certificate-oidc-issuer "$issuer" \
  "$assets/RELEASE_SHA256SUMS"

for name in admin platform publish-gateway; do
  key="ADMIN_IMAGE"
  [[ "$name" == platform ]] && key="PLATFORM_IMAGE"
  [[ "$name" == publish-gateway ]] && key="PUBLISH_GATEWAY_IMAGE"
  reference="$(jq -er --arg key "$key" '.images[$key]' "$assets/release.json")"
  cosign verify \
    --bundle "$assets/cosign-$name.sigstore.json" \
    --certificate-identity "$workflow_identity" \
    --certificate-oidc-issuer "$issuer" \
    "$reference" >/dev/null
  gh attestation verify "oci://$reference" \
    --repo "$repository" \
    --signer-workflow "$repository/.github/workflows/release.yml" \
    --source-ref "$source_ref" --source-digest "$source_commit" >/dev/null
  gh attestation verify "oci://$reference" \
    --repo "$repository" \
    --signer-workflow "$repository/.github/workflows/release.yml" \
    --source-ref "$source_ref" --source-digest "$source_commit" \
    --predicate-type https://spdx.dev/Document/v2.3 >/dev/null
  expected_digest="${reference##*@}"
  actual_digest="sha256:$(docker buildx imagetools inspect "$reference" --raw | sha256sum | awk '{print $1}')"
  [[ "$actual_digest" == "$expected_digest" ]]
done

while IFS= read -r name; do
  gh attestation verify "$assets/$name" \
    --repo "$repository" \
    --signer-workflow "$repository/.github/workflows/release.yml" \
    --source-ref "$source_ref" --source-digest "$source_commit" >/dev/null
done < <(python3 - "$assets" <<'PY'
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
manifest = json.loads((root / "release.json").read_text(encoding="utf-8"))
names = [record["name"] for record in manifest["assets"]["bundles"].values()]
names.extend([
    "release.json", "SHA256SUMS", "RELEASE_SHA256SUMS", "release-readiness.json",
    "sbom-admin.spdx.json", "sbom-platform.spdx.json", "sbom-publish-gateway.spdx.json",
])
print("\n".join(sorted(names)))
PY
)
