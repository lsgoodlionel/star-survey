#!/usr/bin/env python3
"""Render deterministic human-readable notes from a release manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

#: Owned-runtime controllers (surveyctl OWNED_RUNTIME_FILES) refuse to adopt an
#: installation that lacks their recorded runtime ownership. Keep this text identical
#: in render_notes.py and render_publication.py; the publication renderer detects it.
OWNERSHIP_GUIDANCE = (
    "This controller upgrades only installations that carry its recorded runtime ownership. "
    "Installations created by earlier controllers without that record are refused, not adopted: "
    "keep them on their current release and preserve their data, operation markers and backups. "
    "While recovery is disabled, `surveyctl restore` cannot move their data into a new installation."
)

#: A bootstrap candidate is built with minimum source version 0.0.0 and no predecessor.
FRESH_INSTALL_GUIDANCE = (
    "This candidate is a fresh-install baseline: it was not built against a verified predecessor, "
    "so no upgrade into it has been verified and it cannot be promoted to stable. Install it on a "
    "new host for evaluation; do not use it to replace an existing installation."
)
BOOTSTRAP_MINIMUM_SOURCE_VERSION = "0.0.0"


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
    upgrade_limits = OWNERSHIP_GUIDANCE
    if minimum == BOOTSTRAP_MINIMUM_SOURCE_VERSION:
        upgrade_limits = FRESH_INSTALL_GUIDANCE + "\n\n" + upgrade_limits
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

Create and verify a backup before upgrading. Verify the target release assets, then extract the matching architecture bundle into a separate directory and run its `./surveyctl` from that directory. Do not use the installed release's older CLI: it may not understand the target topology.

```bash
./surveyctl backup --output before-{version}
./surveyctl upgrade --manifest /path/to/verified/release.json
./surveyctl doctor
```

This release declares rollback policy `{rollback}`. For `restore-only`, recovery is currently disabled. Preserve the installation, operation marker and verified pre-upgrade backup; do not start older images against a migrated database or bypass the block with an older controller or helper.

{upgrade_limits}

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
- The `{rollback}` policy applies. For `restore-only`, recovery remains disabled: preserve the installation, operation marker and verified pre-upgrade backup. Do not bypass the block with an older controller or helper.
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
