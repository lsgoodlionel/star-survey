#!/usr/bin/env python3
"""Render and validate the canonical human-facing GitHub Release identity."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata
from typing import Any


RC_TAG = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-rc\.(0|[1-9][0-9]*)$")
STABLE_TAG = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def fail(message: str) -> None:
    raise SystemExit(message)


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"invalid JSON {path}: {exc}")
    if not isinstance(value, dict):
        fail(f"JSON must be an object: {path}")
    return value


def normalize_title(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


def normalize_body(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    return normalized.rstrip() + "\n"


def body_sha256(value: str) -> str:
    return hashlib.sha256(normalize_body(value).encode("utf-8")).hexdigest()


def render_body(
    manifest: dict[str, Any], readiness: dict[str, Any], repository: str,
    target_tag: str, source_tag: str, target_commit: str,
) -> str:
    rollback = manifest.get("database", {}).get("rollback")
    minimum = manifest.get("minimumSourceVersion")
    bundles = manifest.get("assets", {}).get("bundles", {})
    images = manifest.get("images", {})
    if rollback != "restore-only" or not isinstance(minimum, str):
        fail("release manifest is missing the reviewed upgrade and rollback contract")
    if set(bundles) != {"amd64", "arm64"}:
        fail("release manifest bundle identity is incomplete")
    bundle_lines = "\n".join(
        f"- `{record['name']}`: `sha256:{record['sha256']}`"
        for _, record in sorted(bundles.items())
    )
    image_lines = "\n".join(
        f"- `{key}`: `{images[key]}`"
        for key in ("ADMIN_IMAGE", "PLATFORM_IMAGE", "PUBLISH_GATEWAY_IMAGE")
        if key in images
    )
    stable = STABLE_TAG.fullmatch(target_tag) is not None
    if stable:
        channel = f"""## Stable promotion

- Source RC: `{source_tag}`
- Promotion guarantee: same bytes and digests as the verified source RC; no rebuild occurred.
- upgradeVerified: `true`
- Stable tag commit: `{target_commit}`
"""
    else:
        channel = f"""## Release candidate

- Candidate source: `{source_tag}`
- Candidate commit: `{target_commit}`
- upgradeVerified: `{str(readiness.get('reason') == 'upgrade-verified').lower()}`
"""
    return normalize_body(f"""# Star Survey {target_tag}

{channel}
## Verified identity

{bundle_lines}
{image_lines}

Verify the downloaded bytes, attestations, SBOMs, image digests, native readiness identity, and tag commit before installation:

```bash
release/verify_release.sh --assets . --repository {repository} --target-tag {target_tag} --source-tag {source_tag} --target-commit {target_commit}
gh attestation verify survey-{manifest['version']}-linux-amd64.tar.gz --repo {repository}
cosign verify <image@sha256:digest> --bundle cosign-admin.sigstore.json
```

## Install

```bash
surveyctl install --manifest release.json --public-host survey.example.com --admin-user operations --admin-email operations@example.com
surveyctl setup-probe
surveyctl doctor
```

## Upgrade and rollback

Minimum source version: `{minimum}`. Create and verify a backup before upgrading:

```bash
surveyctl backup --output before-{target_tag.removeprefix('v')}
surveyctl upgrade --manifest release.json
surveyctl doctor
```

Rollback is `restore-only`: restore the verified pre-upgrade backup. Do not run older images against a migrated database.

## Known issues and limits

- This is a single-host deployment, not high availability. Planned host maintenance interrupts authoring and responses.
- Public ACME TLS and DNS ownership must be verified on the target server; CI uses a local trusted CA.
- Keep backup encryption keys outside the server failure domain.
""")


def render(args: argparse.Namespace) -> int:
    if REPOSITORY.fullmatch(args.repository) is None or COMMIT.fullmatch(args.target_commit) is None:
        fail("repository or target commit identity is invalid")
    source = RC_TAG.fullmatch(args.source_tag)
    target_rc = RC_TAG.fullmatch(args.target_tag)
    target_stable = STABLE_TAG.fullmatch(args.target_tag)
    if source is None:
        fail("publication source must be a canonical RC tag")
    if target_rc is not None:
        if args.target_tag != args.source_tag:
            fail("RC publication target must equal its source RC")
    elif target_stable is not None:
        if target_stable.groups() != source.groups()[:3]:
            fail("stable target version does not match the source RC")
    else:
        fail("publication target must be canonical RC or stable semver")

    manifest = load_object(args.assets / "release.json")
    readiness = load_object(args.assets / "release-readiness.json")
    if manifest.get("version") != args.source_tag.removeprefix("v"):
        fail("source tag does not match release manifest version")
    if manifest.get("commit") != args.target_commit or readiness.get("candidateCommit") != args.target_commit:
        fail("target tag, manifest, and native readiness commit identity differ")
    if target_stable is not None and not (
        readiness.get("promotable") is True and readiness.get("reason") == "upgrade-verified"
    ):
        fail("stable publication requires upgradeVerified native readiness")

    body = render_body(
        manifest, readiness, args.repository, args.target_tag, args.source_tag, args.target_commit,
    )
    metadata = {
        "schemaVersion": 1,
        "tagName": args.target_tag,
        "name": f"Star Survey {args.target_tag}",
        "isPrerelease": target_rc is not None,
        "targetCommitish": args.target_commit,
        "sourceTag": args.source_tag,
        "bodySha256": body_sha256(body),
    }
    args.output_body.parent.mkdir(parents=True, exist_ok=True)
    args.output_metadata.parent.mkdir(parents=True, exist_ok=True)
    with args.output_body.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(body)
    args.output_metadata.write_text(json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def compare(args: argparse.Namespace) -> int:
    expected = load_object(args.expected)
    actual = load_object(args.actual)
    body = normalize_body(args.body.read_text(encoding="utf-8"))
    required = {
        "schemaVersion", "tagName", "name", "isPrerelease", "targetCommitish", "sourceTag", "bodySha256",
    }
    if set(expected) != required or expected.get("schemaVersion") != 1:
        fail("canonical publication metadata has an invalid schema")
    if body_sha256(body) != expected.get("bodySha256"):
        fail("canonical publication body differs from its reviewed identity")
    if actual.get("tagName") != expected.get("tagName"):
        fail("existing Release tag identity differs")
    if actual.get("targetCommitish") != expected.get("targetCommitish"):
        fail("existing Release target commit differs")
    if actual.get("isDraft") is not True and actual.get("isDraft") is not False:
        fail("existing Release draft state is invalid")
    if args.require_published and actual.get("isDraft") is not False:
        fail("final Release is still a draft")
    drift = (
        normalize_title(str(actual.get("name", ""))) != normalize_title(str(expected.get("name", "")))
        or body_sha256(str(actual.get("body", ""))) != expected.get("bodySha256")
        or actual.get("isPrerelease") is not expected.get("isPrerelease")
    )
    if drift:
        if args.allow_draft_repair and actual.get("isDraft") is True:
            return 10
        fail("existing published Release metadata differs from the reviewed identity")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--compare":
        parser = argparse.ArgumentParser(description="Compare GitHub Release metadata")
        parser.add_argument("--compare", action="store_true")
        parser.add_argument("--expected", type=Path, required=True)
        parser.add_argument("--actual", type=Path, required=True)
        parser.add_argument("--body", type=Path, required=True)
        parser.add_argument("--allow-draft-repair", action="store_true")
        parser.add_argument("--require-published", action="store_true")
        return compare(parser.parse_args())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--target-tag", required=True)
    parser.add_argument("--source-tag", required=True)
    parser.add_argument("--target-commit", required=True)
    parser.add_argument("--output-body", type=Path, required=True)
    parser.add_argument("--output-metadata", type=Path, required=True)
    return render(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
