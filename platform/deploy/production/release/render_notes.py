#!/usr/bin/env python3
"""Render deterministic human-readable notes from a release manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def render_notes(manifest: dict[str, Any]) -> str:
    version = manifest["version"]
    channel = manifest["channel"]
    database = manifest["database"]
    minimum = manifest["minimumSourceVersion"]
    rollback = database["rollback"]
    hosts = ", ".join(f"Ubuntu {value}" for value in manifest["supportedHosts"]["ubuntu"])
    architectures = ", ".join(manifest["supportedHosts"]["architectures"])
    bundles = manifest["assets"]["bundles"]
    bundle_lines = "\n".join(f"- `{bundles[arch]['name']}` (`{arch}`)" for arch in sorted(bundles))
    return f"""# Star Survey {version}

Release channel: `{channel}`

## Compatibility

- Supported hosts: {hosts}
- Architectures: {architectures}
- Minimum source version: `{minimum}`
- Database schema: `{database['schema']}`
- Backup schema: `{database['backupSchema']}`
- Rollback policy: `{rollback}`

## Assets

{bundle_lines}

Verify `SHA256SUMS` before installation. Image references in `release.json` are pinned to manifest digests.

## Install

```bash
surveyctl install --manifest release.json --public-host survey.example.com --admin-user operations --admin-email operations@example.com
surveyctl setup-probe
surveyctl doctor
```

## Upgrade

Create and verify a backup before running:

```bash
surveyctl backup --output before-{version}
surveyctl upgrade --manifest release.json
surveyctl doctor
```

This release declares rollback policy `{rollback}`. When it is `restore-only`, recover with the verified pre-upgrade backup instead of starting older images against a migrated database.

## Verification

Verify every downloaded asset before installation:

```bash
release/verify_release.sh --assets . --repository lsgoodlionel/star-survey --target-tag v{version} --source-tag v{version}
gh attestation verify survey-{version}-linux-amd64.tar.gz --repo lsgoodlionel/star-survey
cosign verify <image@sha256:digest> --bundle cosign-admin.sigstore.json
```

## Known issues and limits

- This is a single-host deployment, not high availability. Planned host maintenance interrupts authoring and responses.
- Public ACME TLS and DNS ownership must be verified on the target server; CI uses a local trusted CA for the native matrix.
- Keep backup encryption keys outside the server failure domain. A lost key makes encrypted backups unrecoverable.
- The `{rollback}` policy applies. For `restore-only`, use the verified pre-upgrade backup; do not start older images against a migrated database.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    rendered = render_notes(manifest)
    if args.output:
        with args.output.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
