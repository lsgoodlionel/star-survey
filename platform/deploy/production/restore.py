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
import tempfile

from backup import AuthenticatedOpenSSLCipher, BackupError, BackupManager, VOLUME_SOURCES, safe_absolute


EXPECTED_PAYLOADS = {
    "postgres.dump.enc", "mariadb.sql.enc",
    *(f"{name}.enc" for name in VOLUME_SOURCES),
    "SHA256SUMS",
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
    def __init__(self, target, runner=None, cipher=None):
        try:
            self.target = safe_absolute(target, "target path")
        except BackupError as exc:
            raise RestoreIntegrityError(str(exc)) from exc
        self.runner = runner or CommandRunner()
        self.cipher = cipher or AuthenticatedOpenSSLCipher()

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
        return version, release_dir, target_schema, compatible

    def _verify(self, backup: Path, compatible: set[str]):
        if backup.is_symlink() or not backup.is_dir():
            raise RestoreIntegrityError("backup must be a real directory")
        manifest_path = backup / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise RestoreIntegrityError("backup manifest is unavailable")
        try:
            metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RestoreIntegrityError("backup manifest is invalid") from exc
        if not isinstance(metadata, dict) or set(metadata) != {"schemaVersion", "version", "databaseSchema", "files"}:
            raise RestoreIntegrityError("backup manifest shape is invalid")
        if type(metadata["schemaVersion"]) is not int or metadata["schemaVersion"] != 1:
            raise RestoreIntegrityError("backup metadata version is invalid")
        if not isinstance(metadata["version"], str) or re.fullmatch(r"\d+\.\d+\.\d+(?:-rc\.\d+)?", metadata["version"]) is None:
            raise RestoreIntegrityError("backup application version is invalid")
        if not isinstance(metadata["databaseSchema"], str) or re.fullmatch(r"[A-Za-z0-9._-]{1,64}", metadata["databaseSchema"]) is None:
            raise RestoreIntegrityError("backup database schema is invalid")
        if metadata["databaseSchema"] not in compatible:
            raise RestoreIntegrityError("backup database schema is incompatible with this release")
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
                if stat.S_ISREG(mode) and relative != "manifest.json":
                    actual.add(relative)
        if actual != set(files):
            raise RestoreIntegrityError("backup payload inventory does not match files")
        for name, expected in files.items():
            if PurePosixPath(name).name != name or not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
                raise RestoreIntegrityError("backup checksum entry is unsafe")
            path = backup / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
                raise RestoreIntegrityError("backup payload is missing or truncated")
            if not secrets.compare_digest(sha256(path), expected):
                raise RestoreIntegrityError(f"backup checksum mismatch: {name}")
        sums = {}
        try:
            for line in (backup / "SHA256SUMS").read_text(encoding="ascii").splitlines():
                digest, name = line.split("  ", 1)
                sums[name] = digest
        except (OSError, ValueError) as exc:
            raise RestoreIntegrityError("SHA256SUMS is invalid") from exc
        expected_sums = {name: digest for name, digest in files.items() if name != "SHA256SUMS"}
        if sums != expected_sums:
            raise RestoreIntegrityError("SHA256SUMS does not match the backup manifest")
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

    def _restore_payload(self, backup: Path, compose, temporary: Path, key_file: Path):
        decrypted = {}
        for encrypted in sorted(backup.glob("*.enc")):
            plain = temporary / encrypted.name.removesuffix(".enc")
            try:
                self.cipher.decrypt(encrypted, plain, key_file)
            except (BackupError, ValueError, OSError) as exc:
                raise RestoreIntegrityError("backup decryption or authentication failed") from exc
            decrypted[plain.name] = plain
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
        version, release, _, compatible = self._load_current()
        self._verify(backup, compatible)
        try:
            key_file = safe_absolute(self.target / "shared" / "secrets" / "backup_encryption_key", "backup encryption key path")
        except BackupError as exc:
            raise RestoreIntegrityError(str(exc)) from exc
        if key_file.is_symlink() or not key_file.is_file() or key_file.stat().st_mode & 0o077:
            raise RestoreIntegrityError("backup encryption key is unavailable or unsafe")
        candidate_project = f"survey-restore-{secrets.token_hex(6)}"
        candidate = self._compose(release, candidate_project)
        production = self._compose(release, "survey-production")
        safety = self.target / "backups" / f"pre-restore-safety-{secrets.token_hex(6)}"
        try:
            with tempfile.TemporaryDirectory(dir=self.target) as temporary_name:
                temporary = Path(temporary_name)
                self._run(candidate + ["create"], "isolated restore project creation failed", capture_output=True, text=True)
                self._restore_payload(backup, candidate, temporary, key_file)
                self._run(candidate + ["up", "-d", *APP_SERVICES], "isolated restored stack startup failed", capture_output=True, text=True)
                self._assert_healthy(candidate)
            try:
                BackupManager(self.target, runner=self.runner, cipher=self.cipher).create(safety, version)
            except BackupError as exc:
                raise RestoreRuntimeError("pre-restore safety backup failed") from exc
            try:
                self._apply_production(backup, production, key_file)
            except RestoreError as failure:
                try:
                    self._run(production + ["down", "--remove-orphans"], "failed restore cleanup failed", capture_output=True, text=True)
                    self._apply_production(safety, production, key_file)
                except RestoreError as recovery:
                    raise RestoreRuntimeError("production restore failed and automatic safety recovery failed") from recovery
                raise RestoreRuntimeError("production restore failed; previous stack recovered from safety backup") from failure
        finally:
            try:
                self.runner.run(candidate + ["down", "--volumes", "--remove-orphans"], capture_output=True, text=True)
            except (OSError, subprocess.CalledProcessError):
                pass

    def _apply_production(self, backup, compose, key_file):
        self._run(compose + ["down", "--remove-orphans"], "production stack could not enter restore mode", capture_output=True, text=True)
        with tempfile.TemporaryDirectory(dir=self.target) as temporary_name:
            self._run(compose + ["create"], "production restore project creation failed", capture_output=True, text=True)
            self._restore_payload(backup, compose, Path(temporary_name), key_file)
            self._run(compose + ["up", "-d", "--remove-orphans"], "restored production startup failed", capture_output=True, text=True)
            self._assert_healthy(compose)


def build_parser():
    parser = argparse.ArgumentParser(description="Restore a verified Survey production backup")
    sub = parser.add_subparsers(dest="command", required=True)
    restore = sub.add_parser("restore")
    restore.add_argument("--target", required=True)
    restore.add_argument("--backup", required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        RestoreManager(args.target).restore(args.backup)
        return 0
    except RestoreIntegrityError as exc:
        print(f"restore failed: {exc}", file=sys.stderr)
        return 5
    except RestoreError as exc:
        print(f"restore failed: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
