#!/usr/bin/env python3
"""Create an encrypted, checksummed production backup."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile


MIN_FREE_BYTES = 512 * 1024**2
WRITER_SERVICES = ("edge", "admin-web", "platform", "publish-gateway", "engine")
VOLUME_SOURCES = {
    "caddy-data.tar": ("edge", "/data"),
    "caddy-config.tar": ("edge", "/config"),
    "platform-assets.tar": ("platform", "/var/lib/survey/assets"),
    "platform-exports.tar": ("platform", "/var/lib/survey/exports"),
    "publish-gateway-state.tar": ("publish-gateway", "/var/lib/pubgw"),
    "engine-upload.tar": ("engine", "/var/www/html/upload"),
    "engine-runtime.tar": ("engine", "/var/www/html/application/runtime"),
}
IMAGE_KEYS = {
    "CADDY_IMAGE", "ADMIN_IMAGE", "PLATFORM_IMAGE", "PUBLISH_GATEWAY_IMAGE",
    "ENGINE_IMAGE", "POSTGRES_IMAGE", "MARIADB_IMAGE",
}
CONTROL_MAGIC = b"SURVEY-CONTROL-V2\x00"


class BackupError(Exception):
    pass


def safe_absolute(path, label: str) -> Path:
    absolute = Path(os.path.abspath(os.fspath(Path(path).expanduser())))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current = current / component
        if current.is_symlink():
            raise BackupError(f"{label} contains a symbolic link")
        if not current.exists():
            break
    return absolute


class CommandRunner:
    def run(self, command, **kwargs):
        kwargs.setdefault("check", True)
        return subprocess.run(command, **kwargs)


class AuthenticatedOpenSSLCipher:
    MAGIC = b"SURVEYBK1"

    def _key(self, key_file: Path) -> bytes:
        try:
            key = key_file.read_bytes().strip()
        except OSError as exc:
            raise BackupError("backup encryption key is unavailable") from exc
        if len(key) < 32:
            raise BackupError("backup encryption key is invalid")
        return key

    def encrypt(self, source: Path, destination: Path, key_file: Path) -> None:
        key = self._key(key_file)
        mac_key = hashlib.sha256(key + b"\x00survey-backup-hmac").digest()
        temporary = destination.with_suffix(destination.suffix + ".cipher")
        command = [
            "openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt",
            "-in", str(source), "-out", str(temporary), "-pass", f"file:{key_file}",
        ]
        try:
            subprocess.run(command, check=True, capture_output=True)
            authenticator = hmac.new(mac_key, self.MAGIC, hashlib.sha256)
            with temporary.open("rb") as ciphertext, destination.open("xb") as output:
                output.write(self.MAGIC)
                for chunk in iter(lambda: ciphertext.read(1024 * 1024), b""):
                    authenticator.update(chunk)
                    output.write(chunk)
                output.write(authenticator.digest())
            os.chmod(destination, 0o600)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise BackupError("backup payload encryption failed") from exc
        finally:
            temporary.unlink(missing_ok=True)

    def decrypt(self, source: Path, destination: Path, key_file: Path) -> None:
        key = self._key(key_file)
        mac_key = hashlib.sha256(key + b"\x00survey-backup-hmac").digest()
        try:
            size = source.stat().st_size
        except OSError as exc:
            raise BackupError("encrypted backup payload is unreadable") from exc
        if size <= len(self.MAGIC) + 32:
            raise BackupError("encrypted backup payload is truncated")
        temporary = destination.with_suffix(destination.suffix + ".cipher")
        try:
            remaining = size - len(self.MAGIC) - 32
            authenticator = hmac.new(mac_key, self.MAGIC, hashlib.sha256)
            with source.open("rb") as encrypted, temporary.open("xb") as ciphertext:
                if encrypted.read(len(self.MAGIC)) != self.MAGIC:
                    raise BackupError("encrypted backup payload is truncated")
                while remaining:
                    chunk = encrypted.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise BackupError("encrypted backup payload is truncated")
                    remaining -= len(chunk)
                    authenticator.update(chunk)
                    ciphertext.write(chunk)
                supplied = encrypted.read(32)
            if len(supplied) != 32 or not secrets.compare_digest(supplied, authenticator.digest()):
                raise BackupError("encrypted backup payload authentication failed")
            subprocess.run([
                "openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000",
                "-in", str(temporary), "-out", str(destination), "-pass", f"file:{key_file}",
            ], check=True, capture_output=True)
        except BackupError:
            destination.unlink(missing_ok=True)
            raise
        except (OSError, subprocess.CalledProcessError) as exc:
            destination.unlink(missing_ok=True)
            raise BackupError("backup payload decryption failed") from exc
        finally:
            temporary.unlink(missing_ok=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_key(key_file: Path) -> bytes:
    try:
        key = key_file.read_bytes().strip()
    except OSError as exc:
        raise BackupError("backup encryption key is unavailable") from exc
    if len(key) < 32:
        raise BackupError("backup encryption key is invalid")
    return key


def canonical_manifest(manifest: dict) -> bytes:
    return (json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")


def control_authenticator(manifest_bytes: bytes, sums_bytes: bytes, key_file: Path) -> str:
    mac_key = hashlib.sha256(_backup_key(key_file) + b"\x00survey-backup-control-v2").digest()
    payload = CONTROL_MAGIC + manifest_bytes + b"\x00" + sums_bytes
    return hmac.new(mac_key, payload, hashlib.sha256).hexdigest()


def rewrite_authenticated_control(directory: Path, key_file: Path, manifest=None) -> None:
    """Write the canonical inventory and its domain-separated trust-root HMAC."""
    directory = Path(directory)
    if manifest is None:
        try:
            manifest = json.loads((directory / "manifest.json").read_text(encoding="ascii"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackupError("backup manifest is invalid") from exc
    payloads = sorted(directory.glob("*.enc"), key=lambda path: path.name)
    files = {path.name: sha256(path) for path in payloads}
    manifest = dict(manifest, files=files)
    sums_bytes = "".join(f"{digest}  {name}\n" for name, digest in sorted(files.items())).encode("ascii")
    manifest_bytes = canonical_manifest(manifest)
    (directory / "manifest.json").write_bytes(manifest_bytes)
    (directory / "SHA256SUMS").write_bytes(sums_bytes)
    (directory / "CONTROL-HMAC").write_text(
        control_authenticator(manifest_bytes, sums_bytes, key_file) + "\n", encoding="ascii"
    )
    for name in ("manifest.json", "SHA256SUMS", "CONTROL-HMAC"):
        os.chmod(directory / name, 0o600)


class BackupManager:
    def __init__(self, target, runner=None, cipher=None, disk_usage=None):
        self.target = safe_absolute(target, "target path")
        self.runner = runner or CommandRunner()
        self.cipher = cipher or AuthenticatedOpenSSLCipher()
        self.disk_usage = disk_usage or shutil.disk_usage

    def _release_dir(self, version: str) -> Path:
        release = safe_absolute(self.target / "releases" / version, "release path")
        if release.is_symlink() or not release.is_dir():
            raise BackupError("installed release is unavailable")
        return release

    def _state(self, version: str) -> dict:
        state_path = safe_absolute(self.target / ".surveyctl" / "state.json", "installation state path")
        if state_path.is_symlink():
            raise BackupError("installation state path is unsafe")
        try:
            state = json.loads(state_path.read_text())
        except OSError:
            state = {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackupError("installation state is invalid") from exc
        if not isinstance(state, dict):
            raise BackupError("installation state is invalid")
        if state and state.get("version") != version:
            raise BackupError("requested backup version is not current")
        release_path = self._release_dir(version) / "release.json"
        if release_path.is_symlink() or not release_path.is_file():
            raise BackupError("release identity metadata is unavailable or unsafe")
        try:
            release_bytes = release_path.read_bytes()
            release = json.loads(release_bytes)
            schema = state.get("databaseSchema") or release["database"]["schema"]
            images = release["images"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise BackupError("release identity metadata is unavailable") from exc
        schema = str(schema)
        if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", schema) is None:
            raise BackupError("database schema metadata is invalid")
        image_pattern = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
        if not isinstance(images, dict) or set(images) != IMAGE_KEYS or any(
            not isinstance(value, str) or image_pattern.fullmatch(value) is None for value in images.values()
        ):
            raise BackupError("release image identity metadata is invalid")
        return {
            "databaseSchema": schema,
            "releaseManifestSha256": hashlib.sha256(release_bytes).hexdigest(),
            "images": images,
        }

    def _compose(self, version: str) -> list[str]:
        release = self._release_dir(version)
        for name in ("compose.yml", ".env"):
            path = release / name
            if path.is_symlink() or not path.is_file():
                raise BackupError(f"installed release {name} is unavailable or unsafe")
        return ["docker", "compose", "-f", str(release / "compose.yml"), "--env-file", str(release / ".env"), "-p", "survey-production"]

    def _run_to_file(self, command: list[str], destination: Path) -> None:
        try:
            with destination.open("xb") as output:
                self.runner.run(command, stdout=output, stderr=subprocess.PIPE)
            if destination.stat().st_size == 0:
                raise BackupError("backup command produced an empty payload")
        except subprocess.CalledProcessError as exc:
            destination.unlink(missing_ok=True)
            raise BackupError("backup command failed") from exc
        except OSError as exc:
            raise BackupError("backup payload could not be written") from exc

    def _container_id(self, compose: list[str], service: str) -> str:
        try:
            result = self.runner.run(compose + ["ps", "-q", service], capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            raise BackupError("could not resolve backup container") from exc
        value = (result.stdout or "").strip()
        return value or service

    def create(self, output, version: str) -> Path:
        if not isinstance(version, str) or re.fullmatch(r"\d+\.\d+\.\d+(?:-rc\.\d+)?", version) is None:
            raise BackupError("backup version is invalid")
        output = safe_absolute(output, "backup output path")
        expected_parent = self.target / "backups"
        if output.parent != expected_parent or re.fullmatch(r"[A-Za-z0-9._-]{1,128}", output.name) is None:
            raise BackupError("backup output must be one safe child of the managed backups directory")
        if output.exists() or output.is_symlink():
            raise BackupError("backup destination already exists")
        output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.disk_usage(output.parent).free < MIN_FREE_BYTES:
            raise BackupError("insufficient free space for backup")
        state = self._state(version)
        key_file = safe_absolute(self.target / "shared" / "secrets" / "backup_encryption_key", "backup encryption key path")
        if key_file.is_symlink() or not key_file.is_file() or key_file.stat().st_mode & 0o077:
            raise BackupError("backup encryption key permissions are unsafe")
        compose = self._compose(version)
        staging = output.with_name(f".{output.name}.partial-{secrets.token_hex(6)}")
        paused = False
        try:
            staging.mkdir(mode=0o700)
            self.runner.run(compose + ["pause", *WRITER_SERVICES], capture_output=True, text=True)
            paused = True
            with tempfile.TemporaryDirectory(dir=staging) as temporary_name:
                temporary = Path(temporary_name)
                sources = {
                    "postgres.dump": compose + ["exec", "-T", "platform-db", "pg_dump", "-U", "postgres", "--format=custom", "--serializable-deferrable", "platform"],
                    "mariadb.sql": compose + ["exec", "-T", "engine-db", "sh", "-c", "exec mariadb-dump --single-transaction --quick --skip-lock-tables -u root --password=\"$(cat /run/secrets/engine_db_root_password)\" limesurvey"],
                }
                for name, command in sources.items():
                    plain = temporary / name
                    self._run_to_file(command, plain)
                    self.cipher.encrypt(plain, staging / f"{name}.enc", key_file)
                for name, (service, path) in VOLUME_SOURCES.items():
                    plain = temporary / name
                    container = self._container_id(compose, service)
                    self._run_to_file(["docker", "cp", f"{container}:{path}/.", "-"], plain)
                    self.cipher.encrypt(plain, staging / f"{name}.enc", key_file)
            manifest = {
                "schemaVersion": 2,
                "version": version,
                "databaseSchema": state["databaseSchema"],
                "releaseManifestSha256": state["releaseManifestSha256"],
                "images": state["images"],
                "files": {},
            }
            rewrite_authenticated_control(staging, key_file, manifest)
            os.replace(staging, output)
            return output
        except BackupError:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        except (OSError, subprocess.CalledProcessError) as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise BackupError("backup creation failed") from exc
        finally:
            if paused:
                try:
                    self.runner.run(compose + ["unpause", *WRITER_SERVICES], capture_output=True, text=True)
                    self.runner.run(compose + ["up", "-d", "--remove-orphans"], capture_output=True, text=True)
                except (OSError, subprocess.CalledProcessError) as exc:
                    if output.exists():
                        shutil.rmtree(output, ignore_errors=True)
                    raise BackupError("services could not be resumed after backup") from exc


def build_parser():
    parser = argparse.ArgumentParser(description="Create an encrypted Survey production backup")
    sub = parser.add_subparsers(dest="command", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("--target", required=True)
    backup.add_argument("--output", required=True)
    backup.add_argument("--version", required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        BackupManager(args.target).create(args.output, args.version)
        return 0
    except BackupError as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
