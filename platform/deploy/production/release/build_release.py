#!/usr/bin/env python3
"""Build reproducible production runtime bundles and their release manifest."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile
import tempfile
from typing import Iterable

from render_notes import render_notes


ARCHITECTURES = ("amd64", "arm64")
UBUNTU_VERSIONS = ("22.04", "24.04")
IMAGE_KEYS = (
    "CADDY_IMAGE",
    "ADMIN_IMAGE",
    "PLATFORM_IMAGE",
    "PUBLISH_GATEWAY_IMAGE",
    "ENGINE_IMAGE",
    "POSTGRES_IMAGE",
    "MARIADB_IMAGE",
)
RUNTIME_FILES = (
    "Caddyfile",
    "README.md",
    "backup.py",
    "compose.yml",
    "doctor.py",
    "engines.example.json",
    "env.example",
    "init/ProductionInitCommand.php",
    "init/config.production.php",
    "init/engine-init.sh",
    "init/init-platform-db.sh",
    "init/production-health.php",
    "init/run-with-secrets.sh",
    "init/start-edge.sh",
    "release.schema.json",
    "restore.py",
    "surveyctl",
    "surveyctl.py",
)
SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-rc\.[0-9]+)?")
STABLE_SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
COMMIT = re.compile(r"[0-9a-f]{40}")
SCHEMA = re.compile(r"[A-Za-z0-9._-]{1,64}")
IMAGE = re.compile(r"[^@\s]+@sha256:[0-9a-f]{64}")


class ReleaseBuildError(ValueError):
    pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bytes_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def parse_images(values: Iterable[str]) -> dict[str, str]:
    images: dict[str, str] = {}
    for value in values:
        key, separator, reference = value.partition("=")
        if not separator or key not in IMAGE_KEYS or key in images or IMAGE.fullmatch(reference) is None:
            raise ReleaseBuildError(f"invalid or duplicate fixed-digest image: {value}")
        images[key] = reference
    missing = set(IMAGE_KEYS) - set(images)
    if missing:
        raise ReleaseBuildError(f"missing fixed-digest images: {', '.join(sorted(missing))}")
    return {key: images[key] for key in IMAGE_KEYS}


def read_version(path: Path, channel: str) -> str:
    try:
        version = path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ReleaseBuildError(f"cannot read version file: {path}") from exc
    if SEMVER.fullmatch(version) is None:
        raise ReleaseBuildError("VERSION must contain a supported semantic version")
    if channel == "stable" and STABLE_SEMVER.fullmatch(version) is None:
        raise ReleaseBuildError("stable channel cannot publish a release candidate version")
    if channel == "candidate" and "-rc." not in version:
        raise ReleaseBuildError("candidate channel requires an rc version")
    return version


def collect_runtime(source: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for name in RUNTIME_FILES:
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise ReleaseBuildError(f"required production runtime file is unavailable: {name}")
        files[name] = path.read_bytes()
    return files


def bundle_metadata(version: str, architecture: str, files: dict[str, bytes]) -> bytes:
    metadata = {
        "architecture": architecture,
        "files": {name: bytes_sha256(files[name]) for name in sorted(files)},
        "schemaVersion": 1,
        "version": version,
    }
    return (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8")


def normalized_mode(name: str) -> int:
    return 0o755 if name in {"surveyctl", "surveyctl.py"} or name.endswith(".sh") else 0o644


def build_tarball(destination: Path, files: dict[str, bytes], epoch: int) -> None:
    with destination.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=raw, mtime=epoch) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                for name in sorted(files):
                    pure = PurePosixPath(name)
                    if pure.is_absolute() or ".." in pure.parts:
                        raise ReleaseBuildError(f"unsafe bundle path: {name}")
                    payload = files[name]
                    info = tarfile.TarInfo(name)
                    info.size = len(payload)
                    info.mtime = epoch
                    info.uid = 0
                    info.gid = 0
                    info.uname = "root"
                    info.gname = "root"
                    info.mode = normalized_mode(name)
                    info.type = tarfile.REGTYPE
                    archive.addfile(info, io.BytesIO(payload))


def write_json(path: Path, value: object) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def validate_args(args: argparse.Namespace) -> tuple[str, dict[str, str]]:
    if not args.source.is_dir():
        raise ReleaseBuildError("production source directory does not exist")
    if args.output.exists():
        raise ReleaseBuildError("output directory already exists")
    if COMMIT.fullmatch(args.commit) is None:
        raise ReleaseBuildError("commit must be a full lowercase Git SHA")
    if STABLE_SEMVER.fullmatch(args.minimum_source_version) is None:
        raise ReleaseBuildError("minimum source version must be a stable semantic version")
    if SCHEMA.fullmatch(args.database_schema) is None:
        raise ReleaseBuildError("database schema is invalid")
    if args.backup_schema != 2:
        raise ReleaseBuildError("backup schema must match the supported production backup schema (2)")
    compatible = args.compatible_source_schema
    if not compatible or any(SCHEMA.fullmatch(value) is None for value in compatible) or len(set(compatible)) != len(compatible):
        raise ReleaseBuildError("compatible source schemas must be non-empty, valid, and unique")
    if args.source_date_epoch < 0:
        raise ReleaseBuildError("source date epoch cannot be negative")
    version = read_version(args.version_file, args.channel)
    return version, parse_images(args.image)


def build_release(args: argparse.Namespace) -> None:
    version, images = validate_args(args)
    runtime = collect_runtime(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{args.output.name}-", dir=args.output.parent))
    try:
        bundles: dict[str, dict[str, str]] = {}
        for architecture in ARCHITECTURES:
            files = dict(runtime)
            files["bundle-metadata.json"] = bundle_metadata(version, architecture, runtime)
            name = f"survey-{version}-linux-{architecture}.tar.gz"
            path = staging / name
            build_tarball(path, files, args.source_date_epoch)
            bundles[architecture] = {"name": name, "sha256": file_sha256(path)}

        manifest = {
            "schemaVersion": 1,
            "version": version,
            "channel": args.channel,
            "commit": args.commit,
            "supportedHosts": {"ubuntu": list(UBUNTU_VERSIONS), "architectures": list(ARCHITECTURES)},
            "minimumSourceVersion": args.minimum_source_version,
            "database": {
                "schema": args.database_schema,
                "backupSchema": args.backup_schema,
                "rollback": args.rollback,
                "compatibleSourceSchemas": args.compatible_source_schema,
            },
            "images": images,
            "assets": {"bundles": bundles},
        }
        write_json(staging / "release.json", manifest)
        with (staging / "release-notes.md").open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(render_notes(manifest))
        assets = sorted(path for path in staging.iterdir() if path.is_file())
        checksums = "".join(f"{file_sha256(path)}  {path.name}\n" for path in assets)
        with (staging / "SHA256SUMS").open("w", encoding="ascii", newline="\n") as handle:
            handle.write(checksums)
        os.replace(staging, args.output)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--source", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--version-file", type=Path, required=True)
    result.add_argument("--commit", required=True)
    result.add_argument("--channel", choices=("stable", "candidate"), required=True)
    result.add_argument("--minimum-source-version", required=True)
    result.add_argument("--database-schema", required=True)
    result.add_argument("--backup-schema", type=int, required=True)
    result.add_argument("--rollback", choices=("compatible", "restore-only", "none"), required=True)
    result.add_argument("--compatible-source-schema", action="append", default=[], required=True)
    result.add_argument("--source-date-epoch", type=int, required=True)
    result.add_argument("--image", action="append", default=[], required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        build_release(args)
    except (OSError, ReleaseBuildError) as exc:
        print(f"release build failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
