#!/usr/bin/env python3
"""Verify and restore a production backup through an isolated Compose project."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

from backup import (
    AuthenticatedOpenSSLCipher, BackupError, BackupManager, IMAGE_KEYS, VOLUME_SOURCES,
    canonical_manifest, control_authenticator, safe_absolute,
)


EXPECTED_PAYLOADS = {
    "postgres.dump.enc", "mariadb.sql.enc",
    *(f"{name}.enc" for name in VOLUME_SOURCES),
}
CONTROL_FILES = {"manifest.json", "SHA256SUMS", "CONTROL-HMAC"}
DEFAULT_ARCHIVE_LIMITS = {
    "max_members": 100_000,
    "max_file_bytes": 8 * 1024**3,
    "max_total_bytes": 64 * 1024**3,
    "max_expansion_ratio": 100,
    "expansion_slack_bytes": 1024 * 1024,
}
APP_SERVICES = ("admin-web", "platform", "publish-gateway", "engine-init", "engine")
HEALTHY_SERVICES = {"admin-web", "platform", "publish-gateway", "engine", "platform-db", "engine-db"}


class RestoreError(Exception):
    pass


class RestoreIntegrityError(RestoreError):
    pass


class RestoreRuntimeError(RestoreError):
    pass


class CommandRunner:
    def run(self, command, **kwargs):
        kwargs.setdefault("check", True)
        kwargs.setdefault("text", True)
        return subprocess.run(command, **kwargs)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise RestoreIntegrityError("backup payload is unreadable") from exc
    return digest.hexdigest()


class RestoreManager:
    def __init__(self, target, runner=None, cipher=None, archive_limits=None):
        try:
            self.target = safe_absolute(target, "target path")
        except BackupError as exc:
            raise RestoreIntegrityError(str(exc)) from exc
        self.runner = runner or CommandRunner()
        self.cipher = cipher or AuthenticatedOpenSSLCipher()
        self.archive_limits = dict(DEFAULT_ARCHIVE_LIMITS)
        if archive_limits:
            self.archive_limits.update(archive_limits)

    def _load_current(self):
        current = self.target / "current"
        if current.is_symlink():
            raise RestoreIntegrityError("current version pointer is unsafe")
        try:
            version = current.read_text(encoding="ascii").strip()
        except OSError as exc:
            raise RestoreIntegrityError("current version pointer is unavailable") from exc
        if re.fullmatch(r"\d+\.\d+\.\d+(?:-rc\.\d+)?", version) is None:
            raise RestoreIntegrityError("current version is invalid")
        release_dir = self.target / "releases" / version
        if release_dir.is_symlink() or not release_dir.is_dir():
            raise RestoreIntegrityError("current release is unavailable")
        for name in ("release.json", "compose.yml", ".env"):
            path = release_dir / name
            if path.is_symlink() or not path.is_file():
                raise RestoreIntegrityError(f"current release {name} is unavailable or unsafe")
        try:
            release = json.loads((release_dir / "release.json").read_text(encoding="utf-8"))
            database = release["database"]
            target_schema = str(database["schema"])
            compatible = {str(value) for value in database.get("compatibleSourceSchemas", [])}
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RestoreIntegrityError("release compatibility metadata is invalid") from exc
        compatible.add(target_schema)
        minimum = release.get("minimumSourceVersion")
        if not isinstance(minimum, str) or re.fullmatch(r"\d+\.\d+\.\d+", minimum) is None:
            raise RestoreIntegrityError("release version compatibility metadata is invalid")
        return version, release_dir, target_schema, compatible, minimum

    @staticmethod
    def _version_key(version):
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?", version)
        if match is None:
            raise RestoreIntegrityError("backup application version is invalid")
        major, minor, patch, rc = match.groups()
        return int(major), int(minor), int(patch), 1 if rc is None else 0, int(rc or 0)

    def _verify(self, backup: Path, target_version: str, compatible: set[str], minimum_version: str):
        if backup.is_symlink() or not backup.is_dir():
            raise RestoreIntegrityError("backup must be a real directory")
        manifest_path = backup / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise RestoreIntegrityError("backup manifest is unavailable")
        for name in CONTROL_FILES:
            path = backup / name
            if path.is_symlink() or not path.is_file():
                raise RestoreIntegrityError("backup control files are unavailable or unsafe")
        try:
            manifest_bytes = manifest_path.read_bytes()
            sums_bytes = (backup / "SHA256SUMS").read_bytes()
            supplied_hmac = (backup / "CONTROL-HMAC").read_text(encoding="ascii").strip()
            metadata = json.loads(manifest_bytes)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RestoreIntegrityError("backup manifest is invalid") from exc
        if not isinstance(metadata, dict) or set(metadata) != {
            "schemaVersion", "version", "databaseSchema", "releaseManifestSha256", "images", "files"
        }:
            raise RestoreIntegrityError("backup manifest shape is invalid")
        if type(metadata["schemaVersion"]) is not int or metadata["schemaVersion"] != 2:
            raise RestoreIntegrityError("backup metadata version is invalid")
        if manifest_bytes != canonical_manifest(metadata):
            raise RestoreIntegrityError("backup manifest is not canonical")
        if re.fullmatch(r"[0-9a-f]{64}", supplied_hmac or "") is None:
            raise RestoreIntegrityError("backup control authentication is invalid")
        try:
            expected_hmac = control_authenticator(manifest_bytes, sums_bytes, self._key_file())
        except BackupError as exc:
            raise RestoreIntegrityError(str(exc)) from exc
        if not secrets.compare_digest(supplied_hmac, expected_hmac):
            raise RestoreIntegrityError("backup control authentication failed")
        if not isinstance(metadata["version"], str) or re.fullmatch(r"\d+\.\d+\.\d+(?:-rc\.\d+)?", metadata["version"]) is None:
            raise RestoreIntegrityError("backup application version is invalid")
        if not isinstance(metadata["databaseSchema"], str) or re.fullmatch(r"[A-Za-z0-9._-]{1,64}", metadata["databaseSchema"]) is None:
            raise RestoreIntegrityError("backup database schema is invalid")
        if metadata["databaseSchema"] not in compatible:
            raise RestoreIntegrityError("backup database schema is incompatible with this release")
        if not (self._version_key(minimum_version) <= self._version_key(metadata["version"]) <= self._version_key(target_version)):
            raise RestoreIntegrityError("backup application version is incompatible with this release")
        release_digest = metadata["releaseManifestSha256"]
        images = metadata["images"]
        image_pattern = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
        if re.fullmatch(r"[0-9a-f]{64}", release_digest or "") is None or not isinstance(images, dict) \
                or set(images) != IMAGE_KEYS or any(not isinstance(value, str) or image_pattern.fullmatch(value) is None for value in images.values()):
            raise RestoreIntegrityError("backup release identity is invalid")
        source_release_path = self.target / "releases" / metadata["version"] / "release.json"
        if source_release_path.is_symlink() or not source_release_path.is_file():
            raise RestoreIntegrityError("backup source release is not installed")
        try:
            source_bytes = source_release_path.read_bytes()
            source_release = json.loads(source_bytes)
        except (OSError, json.JSONDecodeError) as exc:
            raise RestoreIntegrityError("backup source release identity is unavailable") from exc
        if not secrets.compare_digest(hashlib.sha256(source_bytes).hexdigest(), release_digest) \
                or source_release.get("images") != images:
            raise RestoreIntegrityError("backup release identity does not match the installed source release")
        files = metadata["files"]
        if not isinstance(files, dict) or set(files) != EXPECTED_PAYLOADS:
            raise RestoreIntegrityError("backup payload inventory is incomplete")
        actual = set()
        for root, directories, names in os.walk(backup, followlinks=False):
            root_path = Path(root)
            for name in directories + names:
                path = root_path / name
                relative = path.relative_to(backup).as_posix()
                mode = path.lstat().st_mode
                if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                    raise RestoreIntegrityError("backup contains unsafe filesystem entries")
                if stat.S_ISDIR(mode):
                    raise RestoreIntegrityError("backup payload inventory contains an unexpected directory")
                if stat.S_ISREG(mode):
                    actual.add(relative)
        if actual != set(files) | CONTROL_FILES:
            raise RestoreIntegrityError("backup payload inventory does not match files")
        for name, expected in files.items():
            if PurePosixPath(name).name != name or not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
                raise RestoreIntegrityError("backup checksum entry is unsafe")
            path = backup / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
                raise RestoreIntegrityError("backup payload is missing or truncated")
            if not secrets.compare_digest(sha256(path), expected):
                raise RestoreIntegrityError(f"backup checksum mismatch: {name}")
        try:
            sums = {}
            for line in sums_bytes.decode("ascii").splitlines():
                digest, name = line.split("  ", 1)
                if name in sums:
                    raise ValueError("duplicate checksum")
                sums[name] = digest
        except (UnicodeDecodeError, ValueError) as exc:
            raise RestoreIntegrityError("SHA256SUMS is invalid") from exc
        expected_sums = dict(sorted(files.items()))
        if sums != expected_sums:
            raise RestoreIntegrityError("SHA256SUMS does not match the backup manifest")
        return metadata

    def _key_file(self):
        try:
            key_file = safe_absolute(
                self.target / "shared" / "secrets" / "backup_encryption_key", "backup encryption key path"
            )
        except BackupError as exc:
            raise RestoreIntegrityError(str(exc)) from exc
        if key_file.is_symlink() or not key_file.is_file() or key_file.stat().st_mode & 0o077:
            raise RestoreIntegrityError("backup encryption key is unavailable or unsafe")
        return key_file

    def _validate_archive(self, archive_path: Path):
        limits = self.archive_limits
        count = 0
        total = 0
        names = set()
        folded = set()
        types = {}
        try:
            with tarfile.open(archive_path, mode="r:*") as archive:
                for member in archive:
                    count += 1
                    if count > limits["max_members"]:
                        raise RestoreIntegrityError("volume archive member count exceeds the safety limit")
                    raw = member.name
                    path = PurePosixPath(raw)
                    if not raw or "\\" in raw or path.is_absolute() or ".." in path.parts \
                            or (raw != "." and path.as_posix() != raw.rstrip("/")):
                        raise RestoreIntegrityError("volume archive contains an unsafe path")
                    canonical = path.as_posix()
                    if canonical in names or canonical.casefold() in folded:
                        raise RestoreIntegrityError("volume archive contains duplicate paths")
                    if not (member.isfile() or member.isdir()):
                        raise RestoreIntegrityError("volume archive contains an unsupported entry type")
                    for parent in path.parents:
                        if parent.as_posix() in types and types[parent.as_posix()] == "file":
                            raise RestoreIntegrityError("volume archive path descends through a regular file")
                    names.add(canonical)
                    folded.add(canonical.casefold())
                    types[canonical] = "file" if member.isfile() else "dir"
                    if member.isfile():
                        if member.size < 0 or member.size > limits["max_file_bytes"]:
                            raise RestoreIntegrityError("volume archive member exceeds the safety limit")
                        total += member.size
                        if total > limits["max_total_bytes"]:
                            raise RestoreIntegrityError("volume archive content exceeds the safety limit")
            archive_size = max(1, archive_path.stat().st_size)
            if total > archive_size * limits["max_expansion_ratio"] + limits["expansion_slack_bytes"]:
                raise RestoreIntegrityError("volume archive expansion ratio exceeds the safety limit")
        except RestoreIntegrityError:
            raise
        except (OSError, tarfile.TarError) as exc:
            raise RestoreIntegrityError("volume archive is invalid") from exc

    def verify_backup(self, backup: Path, temporary: Path):
        target_version, _, _, compatible, minimum = self._load_current()
        metadata = self._verify(backup, target_version, compatible, minimum)
        key_file = self._key_file()
        decrypted = {}
        for name in sorted(EXPECTED_PAYLOADS):
            encrypted = backup / name
            plain = temporary / name.removesuffix(".enc")
            try:
                self.cipher.decrypt(encrypted, plain, key_file)
            except (BackupError, ValueError, OSError) as exc:
                raise RestoreIntegrityError("backup decryption or authentication failed") from exc
            decrypted[plain.name] = plain
        if not decrypted["postgres.dump"].read_bytes().startswith(b"PGDMP"):
            raise RestoreIntegrityError("PostgreSQL dump format is invalid")
        if not decrypted["mariadb.sql"].read_bytes().lstrip().startswith(b"--"):
            raise RestoreIntegrityError("MariaDB dump format is invalid")
        for name in VOLUME_SOURCES:
            self._validate_archive(decrypted[name])
        return metadata, decrypted

    def verify_only(self, backup):
        try:
            backup = safe_absolute(backup, "backup path")
        except BackupError as exc:
            raise RestoreIntegrityError(str(exc)) from exc
        if backup.parent != self.target / "backups" or re.fullmatch(r"[A-Za-z0-9._-]{1,128}", backup.name) is None:
            raise RestoreIntegrityError("backup must be one safe child of the managed backups directory")
        with tempfile.TemporaryDirectory(dir=self.target) as temporary_name:
            metadata, _ = self.verify_backup(backup, Path(temporary_name))
        return metadata

    def _compose(self, release: Path, project: str):
        return ["docker", "compose", "-f", str(release / "compose.yml"), "--env-file", str(release / ".env"), "-p", project]

    def _run(self, command, message, **kwargs):
        try:
            return self.runner.run(command, **kwargs)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RestoreRuntimeError(message) from exc

    def _container(self, compose, service):
        result = self._run(compose + ["ps", "-aq", service], "restore container is unavailable", capture_output=True, text=True)
        return (result.stdout or "").strip() or service

    def _copy_archive(self, archive: Path, compose, service: str, destination: str):
        self._run(
            compose + [
                "run", "--rm", "--no-deps", "--entrypoint", "/bin/sh", service,
                "-c", 'find "$1" -mindepth 1 -maxdepth 1 -exec rm -rf {} +', "restore-clean", destination,
            ],
            "volume cleanup before restore failed",
            capture_output=True,
            text=True,
        )
        container = self._container(compose, service)
        try:
            with archive.open("rb") as source:
                self._run(["docker", "cp", "-", f"{container}:{destination}"], "volume restore failed", stdin=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as exc:
            raise RestoreRuntimeError("volume restore payload is unreadable") from exc

    def _restore_payload(self, decrypted, compose):
        self._run(compose + ["up", "-d", "platform-db", "engine-db"], "restore database startup failed", capture_output=True, text=True)
        with decrypted["postgres.dump"].open("rb") as source:
            self._run(compose + ["exec", "-T", "platform-db", "pg_restore", "-U", "postgres", "-d", "platform", "--clean", "--if-exists", "--no-owner"], "PostgreSQL restore failed", stdin=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with decrypted["mariadb.sql"].open("rb") as source:
            self._run(compose + ["exec", "-T", "engine-db", "sh", "-c", "exec mariadb -u root --password=\"$(cat /run/secrets/engine_db_root_password)\" limesurvey"], "MariaDB restore failed", stdin=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for name, (service, destination) in VOLUME_SOURCES.items():
            self._copy_archive(decrypted[name], compose, service, destination)

    def _assert_healthy(self, compose):
        result = self._run(compose + ["ps", "--format", "json"], "restore health query failed", capture_output=True, text=True)
        try:
            text = (result.stdout or "").strip()
            data = json.loads(text) if text.startswith("[") else [json.loads(line) for line in text.splitlines() if line]
            rows = data if isinstance(data, list) else [data]
        except (AttributeError, json.JSONDecodeError) as exc:
            raise RestoreRuntimeError("restore health output is invalid") from exc
        health = {row.get("Service"): row for row in rows if isinstance(row, dict)}
        for service in HEALTHY_SERVICES:
            row = health.get(service)
            if not row or row.get("State") != "running" or row.get("Health") != "healthy":
                raise RestoreRuntimeError(f"restored service is unhealthy: {service}")

    def restore(self, backup):
        try:
            backup = safe_absolute(backup, "backup path")
        except BackupError as exc:
            raise RestoreIntegrityError(str(exc)) from exc
        if backup.parent != self.target / "backups" or re.fullmatch(r"[A-Za-z0-9._-]{1,128}", backup.name) is None:
            raise RestoreIntegrityError("backup must be one safe child of the managed backups directory")
        version, release, _, _, _ = self._load_current()
        candidate_project = f"survey-restore-{secrets.token_hex(6)}"
        candidate = self._compose(release, candidate_project)
        production = self._compose(release, "survey-production")
        safety = self.target / "backups" / f"pre-restore-safety-{secrets.token_hex(6)}"
        candidate_created = False
        try:
            with tempfile.TemporaryDirectory(dir=self.target) as temporary_name:
                temporary = Path(temporary_name)
                _, decrypted = self.verify_backup(backup, temporary)
                self._run(candidate + ["create"], "isolated restore project creation failed", capture_output=True, text=True)
                candidate_created = True
                self._restore_payload(decrypted, candidate)
                self._run(candidate + ["up", "-d", *APP_SERVICES], "isolated restored stack startup failed", capture_output=True, text=True)
                self._assert_healthy(candidate)
            try:
                BackupManager(self.target, runner=self.runner, cipher=self.cipher).create(safety, version)
            except BackupError as exc:
                raise RestoreRuntimeError("pre-restore safety backup failed") from exc
            try:
                self._apply_production(backup, production)
            except RestoreError as failure:
                try:
                    self._run(production + ["down", "--remove-orphans"], "failed restore cleanup failed", capture_output=True, text=True)
                    self._apply_production(safety, production)
                except RestoreError as recovery:
                    raise RestoreRuntimeError("production restore failed and automatic safety recovery failed") from recovery
                raise RestoreRuntimeError("production restore failed; previous stack recovered from safety backup") from failure
        finally:
            if candidate_created:
                try:
                    self.runner.run(candidate + ["down", "--volumes", "--remove-orphans"], capture_output=True, text=True)
                except (OSError, subprocess.CalledProcessError):
                    pass

    def _apply_production(self, backup, compose):
        with tempfile.TemporaryDirectory(dir=self.target) as temporary_name:
            _, decrypted = self.verify_backup(backup, Path(temporary_name))
            self._run(compose + ["down", "--remove-orphans"], "production stack could not enter restore mode", capture_output=True, text=True)
            self._run(compose + ["create"], "production restore project creation failed", capture_output=True, text=True)
            self._restore_payload(decrypted, compose)
            self._run(compose + ["up", "-d", "--remove-orphans"], "restored production startup failed", capture_output=True, text=True)
            self._assert_healthy(compose)


def build_parser():
    parser = argparse.ArgumentParser(description="Restore a verified Survey production backup")
    sub = parser.add_subparsers(dest="command", required=True)
    restore = sub.add_parser("restore")
    restore.add_argument("--target", required=True)
    restore.add_argument("--backup", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--target", required=True)
    verify.add_argument("--backup", required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        manager = RestoreManager(args.target)
        if args.command == "verify":
            manager.verify_only(args.backup)
        else:
            manager.restore(args.backup)
        return 0
    except RestoreIntegrityError as exc:
        print(f"restore failed: {exc}", file=sys.stderr)
        return 5
    except RestoreError as exc:
        print(f"restore failed: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
