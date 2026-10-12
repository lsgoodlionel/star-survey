"""LOCAL TEST ONLY: deterministic image bundle packaging smoke helper.

This validates a local manifest-to-theme-directory experiment. It does not
send image bytes through the production GatewayRequest or publish flow.
"""

import argparse
import base64
import hashlib
import json
import struct
import zlib
from pathlib import Path
from typing import Dict, Optional

LOGO_ID = "11111111-1111-4111-8111-111111111111"
FAVICON_ID = "22222222-2222-4222-8222-222222222222"


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)


def _png_fixture() -> bytes:
    # Deterministic 1x1 RGB PNG (one opaque red pixel).
    pixel = b"\x00\xff\x00\x00"
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(pixel, level=9))
        + _png_chunk(b"IEND", b"")
    )


def _jpeg_fixture() -> bytes:
    # Minimal deterministic JPEG marker stream for a local byte-copy smoke.
    # The bundle test validates transport metadata and bytes, not image decoding.
    return bytes.fromhex("FFD8 FFE0 0004 4A46 FFD9")


def _asset(asset_id: str, slot: str, content_type: str, filename: str, data: bytes) -> Dict[str, object]:
    return {
        "assetId": asset_id,
        "slot": slot,
        "contentType": content_type,
        "sizeBytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytesBase64": base64.b64encode(data).decode("ascii"),
        "fileName": filename,
    }


def run_smoke(bundle_dir: Path, theme_dir: Path) -> Dict[str, object]:
    """Create a local manifest and materialized files from fixed image bytes."""
    bundle_dir.mkdir(parents=True, exist_ok=True)
    files_dir = theme_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    assets = [
        _asset(LOGO_ID, "logo", "image/png", "mjy-brand-logo.png", _png_fixture()),
        _asset(FAVICON_ID, "favicon", "image/jpeg", "mjy-brand-favicon.jpg", _jpeg_fixture()),
    ]
    for asset in assets:
        data = base64.b64decode(str(asset["bytesBase64"]), validate=True)
        (files_dir / str(asset["fileName"])).write_bytes(data)

    manifest_path = bundle_dir / "brand-assets.local-test-only.json"
    manifest_path.write_text(
        json.dumps({"scope": "local-test-only", "assets": assets}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "scope": "local-test-only",
        "manifestPath": str(manifest_path),
        "themeDir": str(theme_dir),
        "branding": {
            "brandingVersion": 1,
            "logoAsset": {
                "url": "https://survey.example/a/local/11111111-1111-4111-8111-111111111111/1?exp=1&sig=local-test-only",
                "assetVersion": 1,
                "contentType": "image/png",
                "sizeBytes": len(_png_fixture()),
                "fileName": "mjy-brand-logo.png",
            },
            "faviconAsset": {
                "url": "https://survey.example/a/local/22222222-2222-4222-8222-222222222222/1?exp=1&sig=local-test-only",
                "assetVersion": 1,
                "contentType": "image/jpeg",
                "sizeBytes": len(_jpeg_fixture()),
                "fileName": "mjy-brand-favicon.jpg",
            },
        },
    }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="LOCAL TEST ONLY: exercise a temporary brand-image bundle")
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--theme-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    result = run_smoke(args.bundle_dir, args.theme_dir)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
