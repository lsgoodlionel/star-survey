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
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Callable, Iterable

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
NUMERIC_SCHEMA = re.compile(r"[0-9]+")
SEMVER_SCHEMA = re.compile(r"([0-9]+)\.([0-9]+)\.([0-9]+)")
IMAGE = re.compile(r"[^@\s]+@sha256:[0-9a-f]{64}")


class ReleaseBuildError(ValueError):
    pass


ReadValidationHook = Callable[[Path, os.stat_result], None]


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


def require_directory(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReleaseBuildError(f"required production runtime directory is unavailable: {label}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ReleaseBuildError(f"required production runtime directory is not a real directory: {label}")


def normalized_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def require_unsymlinked_directory_chain(path: Path, label: str) -> None:
    absolute = normalized_absolute(path)
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        require_directory(current, f"{label}:{current}")


def discover_trusted_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise ReleaseBuildError("trusted repository root cannot be discovered; pass --trusted-root")
    return Path(result.stdout.strip())


def validate_source_boundary(source: Path, trusted_root: Path) -> tuple[Path, Path]:
    source_absolute = normalized_absolute(source)
    trusted_absolute = normalized_absolute(trusted_root)
    expected_source = trusted_absolute / "platform" / "deploy" / "production"
    if source_absolute != expected_source:
        raise ReleaseBuildError("production source must be the expected subtree of the trusted repository root")
    require_unsymlinked_directory_chain(trusted_absolute, "trusted repository root")
    require_unsymlinked_directory_chain(source_absolute, "trusted production source")
    try:
        resolved_root = trusted_absolute.resolve(strict=True)
        resolved_source = source_absolute.resolve(strict=True)
        resolved_source.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise ReleaseBuildError("production source escapes the trusted repository root") from exc
    if resolved_source != resolved_root / "platform" / "deploy" / "production":
        raise ReleaseBuildError("resolved production source is not the expected trusted subtree")
    return resolved_source, resolved_root


def metadata_fingerprint(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def read_regular_unlinked_file(
    path: Path,
    label: str,
    root: Path,
    after_open: ReadValidationHook | None = None,
) -> bytes:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ReleaseBuildError(f"required production runtime file escapes its source root: {label}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ReleaseBuildError(f"required production runtime file is not an unlinked regular file: {label}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            before = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or metadata_fingerprint(before) != metadata_fingerprint(metadata)
            ):
                raise ReleaseBuildError(f"required production runtime file changed during validation: {label}")
            if after_open:
                after_open(path, before)
            payload = handle.read()
            after = os.fstat(handle.fileno())
            if metadata_fingerprint(after) != metadata_fingerprint(before) or len(payload) != before.st_size:
                raise ReleaseBuildError(f"required production runtime file changed while being read: {label}")
            return payload
    except OSError as exc:
        raise ReleaseBuildError(f"required production runtime file cannot be read safely: {label}") from exc


def collect_runtime(
    source: Path,
    trusted_root: Path,
    after_open: ReadValidationHook | None = None,
) -> dict[str, bytes]:
    source, _ = validate_source_boundary(source, trusted_root)
    canonical_root = source
    files: dict[str, bytes] = {}
    for name in RUNTIME_FILES:
        current = source
        for component in PurePosixPath(name).parts[:-1]:
            current /= component
            require_directory(current, current.relative_to(source).as_posix())
            try:
                current.resolve(strict=True).relative_to(canonical_root)
            except (OSError, ValueError) as exc:
                raise ReleaseBuildError(f"required production runtime directory escapes its source root: {name}") from exc
        path = source / name
        files[name] = read_regular_unlinked_file(path, name, canonical_root, after_open)
    return files


def canonicalize_schemas(values: Iterable[str]) -> list[str]:
    normalized: set[tuple[int, tuple[int, ...], str]] = set()
    for value in values:
        if NUMERIC_SCHEMA.fullmatch(value):
            number = int(value)
            normalized.add((0, (number,), str(number)))
            continue
        match = SEMVER_SCHEMA.fullmatch(value)
        if match:
            components = tuple(int(component) for component in match.groups())
            canonical = ".".join(str(component) for component in components)
            normalized.add((1, components, canonical))
            continue
        raise ReleaseBuildError("compatible source schemas must be numeric or semantic versions")
    if not normalized:
        raise ReleaseBuildError("compatible source schemas cannot be empty")
    return [canonical for _, _, canonical in sorted(normalized)]


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


def validate_args(args: argparse.Namespace) -> tuple[str, dict[str, str], list[str]]:
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
    compatible = canonicalize_schemas(args.compatible_source_schema)
    if args.source_date_epoch < 0:
        raise ReleaseBuildError("source date epoch cannot be negative")
    version = read_version(args.version_file, args.channel)
    return version, parse_images(args.image), compatible


def build_release(args: argparse.Namespace) -> None:
    version, images, compatible_schemas = validate_args(args)
    trusted_root = args.trusted_root or discover_trusted_root()
    runtime = collect_runtime(args.source, trusted_root)
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
                "compatibleSourceSchemas": compatible_schemas,
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
    result.add_argument("--trusted-root", type=Path)
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
