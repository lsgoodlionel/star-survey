#!/usr/bin/env python3
"""Production lifecycle controller for a single-host Survey installation."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
from typing import Any
import urllib.error
import urllib.request


EXIT_SUCCESS = 0
EXIT_INPUT = 2
EXIT_ENVIRONMENT = 3
EXIT_RUNTIME = 4
EXIT_INTEGRITY = 5

IMAGE_KEYS = {
    "CADDY_IMAGE",
    "ADMIN_IMAGE",
    "PLATFORM_IMAGE",
    "PUBLISH_GATEWAY_IMAGE",
    "ENGINE_IMAGE",
    "POSTGRES_IMAGE",
    "MARIADB_IMAGE",
}
SECRET_NAMES = (
    "engine_admin_password_hash",
    "engine_admin_password",
    "engine_db_password",
    "engine_db_root_password",
    "platform_db_superuser_password",
    "platform_db_owner_password",
    "platform_db_app_password",
    "platform_jwt_hmac_secret",
    "platform_engine_events_secret",
    "platform_pubgw_secret",
    "pubgw_engine_admin_password",
)


class SurveyctlError(Exception):
    exit_code = EXIT_INPUT


class InputError(SurveyctlError):
    exit_code = EXIT_INPUT


class EnvironmentError(SurveyctlError):
    exit_code = EXIT_ENVIRONMENT


class RuntimeHealthError(SurveyctlError):
    exit_code = EXIT_RUNTIME


class IntegrityError(SurveyctlError):
    exit_code = EXIT_INTEGRITY


@dataclass(frozen=True)
class ReleaseManifest:
    path: Path
    raw: dict[str, Any]

    @property
    def version(self) -> str:
        return self.raw["version"]

    @property
    def database_schema(self) -> str:
        return self.raw["database"]["schema"]

    @property
    def rollback(self) -> str:
        return self.raw["database"]["rollback"]

    @property
    def compatible_source_schemas(self) -> list[str]:
        return self.raw["database"]["compatibleSourceSchemas"]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise IntegrityError(message)


def load_manifest(path: Path | str, expected_sha256: str | None = None) -> ReleaseManifest:
    manifest_path = Path(path).resolve()
    try:
        payload = manifest_path.read_bytes()
    except OSError as exc:
        raise InputError(f"cannot read release manifest: {manifest_path}") from exc
    actual = hashlib.sha256(payload).hexdigest()
    if expected_sha256 and not secrets.compare_digest(actual, expected_sha256.lower()):
        raise IntegrityError("release manifest checksum mismatch")
    try:
        data = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrityError("release manifest is not valid JSON") from exc
    _require(isinstance(data, dict), "release manifest must be an object")
    required = {"schemaVersion", "version", "channel", "commit", "supportedHosts", "minimumSourceVersion", "database", "images", "assets"}
    _require(required <= set(data), "release manifest is missing required fields")
    _require(data["schemaVersion"] == 1, "unsupported release manifest schema")
    _require(bool(re.fullmatch(r"\d+\.\d+\.\d+(?:-rc\.\d+)?", data["version"])), "invalid release version")
    _require(data["channel"] in {"stable", "candidate"}, "invalid release channel")
    _require(bool(re.fullmatch(r"[0-9a-f]{40}", data["commit"])), "invalid release commit")
    hosts = data["supportedHosts"]
    _require(isinstance(hosts, dict) and hosts.get("ubuntu") and hosts.get("architectures"), "invalid supported hosts")
    database = data["database"]
    _require(database.get("rollback") in {"compatible", "restore-only", "none"}, "invalid rollback policy")
    _require(isinstance(database.get("compatibleSourceSchemas"), list), "missing database compatibility range")
    images = data["images"]
    _require(isinstance(images, dict) and set(images) == IMAGE_KEYS, "release images do not match the production service set")
    for name, reference in images.items():
        _require(isinstance(reference, str) and re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", reference) is not None, f"{name} is not digest locked")
    bundle = data.get("assets", {}).get("bundle", {})
    _require(bool(bundle.get("name")), "release bundle name is missing")
    _require(re.fullmatch(r"[0-9a-f]{64}", str(bundle.get("sha256", ""))) is not None, "release bundle checksum is invalid")
    return ReleaseManifest(manifest_path, data)


class CommandRunner:
    def run(self, command, **kwargs):
        kwargs.setdefault("check", True)
        kwargs.setdefault("text", True)
        return subprocess.run(command, **kwargs)


class HostProbe:
    ARCHITECTURES = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}

    def _os_release(self) -> dict[str, str]:
        values = {}
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value.strip().strip('"')
        return values

    def _command_version(self, command: list[str]) -> str:
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise EnvironmentError(f"required command unavailable: {' '.join(command)}") from exc
        return result.stdout.strip() or result.stderr.strip()

    def _ports_available(self) -> bool:
        sockets = []
        try:
            for port in (80, 443):
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("0.0.0.0", port))
                sockets.append(sock)
            return True
        except OSError:
            return False
        finally:
            for sock in sockets:
                sock.close()

    def inspect(self, host: str) -> dict[str, Any]:
        release = self._os_release()
        try:
            dns = sorted({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
        except socket.gaierror:
            dns = []
        memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        synchronized = True
        if shutil.which("timedatectl"):
            result = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], capture_output=True, text=True)
            synchronized = result.returncode == 0 and result.stdout.strip().lower() == "yes"
        return {
            "os_id": release.get("ID", ""),
            "os_version": release.get("VERSION_ID", ""),
            "architecture": self.ARCHITECTURES.get(platform.machine(), platform.machine()),
            "docker_version": self._command_version(["docker", "version", "--format", "{{.Server.Version}}"]),
            "compose_version": self._command_version(["docker", "compose", "version", "--short"]),
            "free_bytes": shutil.disk_usage(Path.cwd()).free,
            "memory_bytes": memory,
            "ports_available": self._ports_available(),
            "dns_addresses": dns,
            "time_synchronized": synchronized,
        }


class GitHubReleaseClient:
    def __init__(self, repository: str = "lsgoodlionel/star-survey"):
        self.repository = repository

    def fetch_manifest(self, version: str) -> bytes:
        tag = version if version.startswith("v") else f"v{version}"
        asset_version = version.removeprefix("v")
        url = f"https://github.com/{self.repository}/releases/download/{tag}/survey-{asset_version}-release.json"
        request = urllib.request.Request(url, headers={"Accept": "application/octet-stream", "User-Agent": "surveyctl/1"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except (OSError, urllib.error.URLError) as exc:
            raise EnvironmentError("could not download the requested release manifest") from exc


class SurveyManager:
    def __init__(self, target: Path | str, runner=None, host_probe=None, release_client=None, source_dir=None):
        self.target = Path(target).expanduser().resolve()
        self.runner = runner or CommandRunner()
        self.host_probe = host_probe or HostProbe()
        self.release_client = release_client or GitHubReleaseClient()
        self.source_dir = Path(source_dir or Path(__file__).resolve().parent)
        self.control_dir = self.target / ".surveyctl"
        self.state_path = self.control_dir / "state.json"
        self.current_path = self.target / "current"

    def _assert_target_available(self) -> None:
        if not self.target.exists() or not any(self.target.iterdir()) or self.state_path.exists():
            return
        entries = set(self.target.iterdir())
        downloads = self.control_dir / "downloads"
        if entries == {self.control_dir} and self.control_dir.is_dir() and all(path == downloads for path in self.control_dir.iterdir()):
            return
        raise InputError("target directory is non-empty and is not owned by surveyctl")

    def _state(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise InputError(f"no managed installation at {self.target}") from exc
        except json.JSONDecodeError as exc:
            raise IntegrityError("installation state is corrupt") from exc

    def _atomic_write(self, path: Path, content: str, mode: int = 0o600) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _write_state(self, **values) -> None:
        state = self._state() if self.state_path.exists() else {}
        state.update(values)
        state["managedPaths"] = [str(self.control_dir), str(self.target / "releases"), str(self.target / "shared"), str(self.target / "backups"), str(self.current_path)]
        self._atomic_write(self.state_path, json.dumps(state, indent=2, sort_keys=True) + "\n")

    def _compose(self, version: str) -> list[str]:
        release = self.target / "releases" / version
        return ["docker", "compose", "--project-directory", str(self.target), "--env-file", str(release / ".env"), "-f", str(release / "compose.yml")]

    def _run(self, command: list[str], runtime_message: str) -> None:
        try:
            return self.runner.run(command, cwd=self.target)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeHealthError(runtime_message) from exc

    def _assert_healthy(self, compose: list[str], message: str) -> None:
        result = self._run(compose + ["ps", "--all", "--format", "json"], message)
        try:
            text = result.stdout.strip()
            records = json.loads(text) if text.startswith("[") else [json.loads(line) for line in text.splitlines() if line]
        except (AttributeError, json.JSONDecodeError) as exc:
            raise RuntimeHealthError(f"{message}: unreadable container status") from exc
        expected = {"edge", "admin-web", "platform", "publish-gateway", "engine", "platform-db", "engine-db"}
        by_service = {record.get("Service"): record for record in records}
        missing = expected - set(by_service)
        unhealthy = {
            service
            for service in expected & set(by_service)
            if by_service[service].get("State") != "running" or by_service[service].get("Health") not in {"healthy", ""}
        }
        if missing or unhealthy:
            raise RuntimeHealthError(f"{message}: missing={sorted(missing)}, unhealthy={sorted(unhealthy)}")

    def _validate_install_inputs(self, public_host: str, admin_user: str, admin_email: str) -> None:
        if re.fullmatch(r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", public_host) is None:
            raise InputError("public host must be a DNS hostname without a scheme or path")
        if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", admin_user) is None:
            raise InputError("administrator user contains unsupported characters")
        if "\n" in admin_email or "\r" in admin_email or re.fullmatch(r"[^@\s]+@[^@\s]+", admin_email) is None:
            raise InputError("administrator email is invalid")

    def _prepare_release(self, manifest: ReleaseManifest, host: str, admin_user: str, admin_email: str) -> Path:
        release = self.target / "releases" / manifest.version
        release.mkdir(parents=True, exist_ok=True)
        for name in ("compose.yml", "Caddyfile"):
            shutil.copy2(self.source_dir / name, release / name)
        shutil.copytree(self.source_dir / "init", release / "init", dirs_exist_ok=True)
        shutil.copy2(manifest.path, release / "release.json")
        engines = json.loads((self.source_dir / "engines.example.json").read_text(encoding="utf-8"))
        (release / "engines.json").write_text(json.dumps(engines, indent=2) + "\n", encoding="utf-8")
        secret_dir = self.target / "shared" / "secrets"
        secret_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(secret_dir, 0o700)
        for name in SECRET_NAMES:
            path = secret_dir / name
            if not path.exists():
                if name == "engine_admin_password_hash":
                    continue
                value = secrets.token_urlsafe(48)
                self._atomic_write(path, value + "\n", 0o600)
        lines = [f"PUBLIC_HOST={host}"]
        lines.extend(f"{key}={value}" for key, value in sorted(manifest.raw["images"].items()))
        lines.extend([
            "ENGINE_INSTANCE_ID=production-engine-01",
            f"ENGINE_ADMIN_USER={admin_user}",
            f"ENGINE_ADMIN_EMAIL={admin_email}",
            f"ENGINES_CONFIG_FILE={release / 'engines.json'}",
        ])
        env_keys = {
            "ENGINE_ADMIN_PASSWORD_HASH_FILE": "engine_admin_password_hash",
            "ENGINE_ADMIN_PASSWORD_FILE": "engine_admin_password",
            "ENGINE_DB_PASSWORD_FILE": "engine_db_password",
            "ENGINE_DB_ROOT_PASSWORD_FILE": "engine_db_root_password",
            "PLATFORM_DB_SUPERUSER_PASSWORD_FILE": "platform_db_superuser_password",
            "PLATFORM_DB_OWNER_PASSWORD_FILE": "platform_db_owner_password",
            "PLATFORM_DB_APP_PASSWORD_FILE": "platform_db_app_password",
            "PLATFORM_JWT_HMAC_SECRET_FILE": "platform_jwt_hmac_secret",
            "PLATFORM_ENGINE_EVENTS_SECRET_FILE": "platform_engine_events_secret",
            "PLATFORM_PUBGW_SECRET_FILE": "platform_pubgw_secret",
            "PUBGW_ENGINE_ADMIN_PASSWORD_FILE": "pubgw_engine_admin_password",
        }
        lines.extend(f"{key}={secret_dir / value}" for key, value in env_keys.items())
        self._atomic_write(release / ".env", "\n".join(lines) + "\n")
        return release

    def _ensure_admin_password_hash(self, caddy_image: str) -> None:
        secret_dir = self.target / "shared" / "secrets"
        hash_path = secret_dir / "engine_admin_password_hash"
        if hash_path.exists():
            return
        password = (secret_dir / "engine_admin_password").read_text(encoding="utf-8").strip()
        command = ["docker", "run", "--rm", "-i", caddy_image, "caddy", "hash-password"]
        try:
            result = self.runner.run(command, input=password + "\n", capture_output=True, cwd=self.target)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeHealthError("could not generate the edge administrator password hash") from exc
        password_hash = result.stdout.strip()
        if re.fullmatch(r"\$2[aby]\$\d{2}\$.{53}", password_hash) is None:
            raise IntegrityError("Caddy returned an invalid administrator password hash")
        self._atomic_write(hash_path, password_hash + "\n", 0o600)

    def preflight(self, manifest: ReleaseManifest, host: str, require_ports: bool = True) -> dict[str, Any]:
        values = self.host_probe.inspect(host)
        supported = manifest.raw["supportedHosts"]
        failures = []
        if values["os_id"] != "ubuntu" or values["os_version"] not in supported["ubuntu"]:
            failures.append("unsupported Ubuntu release")
        if values["architecture"] not in supported["architectures"]:
            failures.append("unsupported architecture")
        if not values.get("docker_version") or not values.get("compose_version"):
            failures.append("Docker Engine and Compose v2 are required")
        if values["free_bytes"] < 20 * 1024**3:
            failures.append("at least 20 GiB free disk is required")
        if values["memory_bytes"] < 4 * 1024**3:
            failures.append("at least 4 GiB memory is required")
        if require_ports and not values["ports_available"]:
            failures.append("ports 80 and 443 must be available")
        if not values["dns_addresses"]:
            failures.append("public host must resolve before TLS setup")
        if not values["time_synchronized"]:
            failures.append("system time must be synchronized")
        if failures:
            raise EnvironmentError("; ".join(failures))
        return values

    def install(self, manifest_path, public_host: str, admin_user: str, admin_email: str, expected_sha256=None) -> None:
        manifest = load_manifest(manifest_path, expected_sha256)
        self._validate_install_inputs(public_host, admin_user, admin_email)
        self._assert_target_available()
        if self.state_path.exists() and self._state().get("status") not in {"install_failed", "uninstalled"}:
            raise InputError("an installation already exists; use upgrade instead")
        self.preflight(manifest, public_host)
        self.target.mkdir(parents=True, exist_ok=True)
        self._prepare_release(manifest, public_host, admin_user, admin_email)
        self._write_state(status="installing", version=manifest.version, databaseSchema=manifest.database_schema, publicHost=public_host, adminUser=admin_user, adminEmail=admin_email)
        compose = self._compose(manifest.version)
        try:
            self._run(compose + ["pull"], "image pull failed")
            self._ensure_admin_password_hash(manifest.raw["images"]["CADDY_IMAGE"])
            self._run(compose + ["up", "-d", "--remove-orphans"], "installation start failed")
            self._assert_healthy(compose, "installation health check failed")
        except SurveyctlError:
            self._write_state(status="install_failed")
            raise
        self._atomic_write(self.current_path, manifest.version + "\n")
        self._write_state(status="healthy")

    def install_version(self, version: str, expected_sha256: str | None, public_host: str, admin_user: str, admin_email: str) -> None:
        if not expected_sha256:
            raise InputError("remote release installation requires --manifest-sha256")
        if re.fullmatch(r"v?\d+\.\d+\.\d+(?:-rc\.\d+)?", version) is None:
            raise InputError("invalid release version")
        self._assert_target_available()
        download = self.control_dir / "downloads" / f"release-{version.removeprefix('v')}.json"
        payload = self.release_client.fetch_manifest(version.removeprefix("v"))
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IntegrityError("downloaded release manifest is not UTF-8") from exc
        self._atomic_write(download, text)
        self.install(download, public_host, admin_user, admin_email, expected_sha256)

    def upgrade_version(self, version: str, expected_sha256: str | None) -> None:
        if not expected_sha256:
            raise InputError("remote release upgrade requires --manifest-sha256")
        if re.fullmatch(r"v?\d+\.\d+\.\d+(?:-rc\.\d+)?", version) is None:
            raise InputError("invalid release version")
        download = self.control_dir / "downloads" / f"release-{version.removeprefix('v')}.json"
        payload = self.release_client.fetch_manifest(version.removeprefix("v"))
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IntegrityError("downloaded release manifest is not UTF-8") from exc
        self._atomic_write(download, text)
        self.upgrade(download, expected_sha256)

    def backup(self, output: Path | str) -> None:
        state = self._state()
        output = Path(output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        helper = self.source_dir / "backup.py"
        self._run([sys.executable, str(helper), "backup", "--target", str(self.target), "--output", str(output), "--version", state["version"]], "backup failed")

    def restore(self, backup: Path | str) -> None:
        backup = Path(backup).resolve()
        if not (backup / "manifest.json").is_file():
            raise IntegrityError("backup manifest is missing")
        helper = self.source_dir / "restore.py"
        self._run([sys.executable, str(helper), "restore", "--target", str(self.target), "--backup", str(backup)], "restore failed")

    def upgrade(self, manifest_path, expected_sha256=None) -> None:
        state = self._state()
        previous = state["version"]
        manifest = load_manifest(manifest_path, expected_sha256)
        if _version_key(manifest.version) <= _version_key(previous):
            raise InputError("upgrade target must be newer than the installed version")
        if _version_key(previous) < _version_key(manifest.raw["minimumSourceVersion"]):
            raise InputError("installed version is below the release minimum source version")
        self.preflight(manifest, state["publicHost"], require_ports=False)
        self._prepare_release(manifest, state["publicHost"], state["adminUser"], state["adminEmail"])
        backup = self.target / "backups" / f"pre-upgrade-{previous}-to-{manifest.version}"
        self.backup(backup)
        compose = self._compose(manifest.version)
        try:
            self._write_state(status="upgrading", previousVersion=previous)
            self._run(compose + ["pull"], "upgrade image pull failed")
            self._run(compose + ["up", "-d", "--remove-orphans"], "upgrade migration or start failed")
            self._assert_healthy(compose, "upgrade health check failed")
            self._atomic_write(self.current_path, manifest.version + "\n")
            self._write_state(status="healthy", version=manifest.version, databaseSchema=manifest.database_schema, previousVersion=previous)
        except SurveyctlError:
            self._atomic_write(self.current_path, previous + "\n")
            self._write_state(status="upgrade_failed", version=previous)
            try:
                self.runner.run(self._compose(previous) + ["up", "-d", "--remove-orphans"], cwd=self.target)
            except (OSError, subprocess.CalledProcessError):
                pass
            raise

    def rollback(self, version: str | None = None) -> None:
        state = self._state()
        target_version = version or state.get("previousVersion")
        if not target_version:
            raise InputError("no rollback version is recorded")
        manifest = load_manifest(self.target / "releases" / target_version / "release.json")
        current_manifest = load_manifest(self.target / "releases" / state["version"] / "release.json")
        if current_manifest.rollback != "compatible" or state["databaseSchema"] not in manifest.compatible_source_schemas:
            raise InputError("database compatibility requires restore from backup; image rollback is blocked")
        compose = self._compose(target_version)
        self._run(compose + ["up", "-d", "--remove-orphans"], "rollback start failed")
        self._assert_healthy(compose, "rollback health check failed")
        previous = state["version"]
        self._atomic_write(self.current_path, target_version + "\n")
        self._write_state(status="healthy", version=target_version, databaseSchema=manifest.database_schema, previousVersion=previous)

    def status(self) -> None:
        state = self._state()
        self._run(self._compose(state["version"]) + ["ps"], "status check failed")

    def doctor(self) -> None:
        state = self._state()
        helper = self.source_dir / "doctor.py"
        self._run([sys.executable, str(helper), "--target", str(self.target), "--redact"], "doctor checks failed")

    def logs(self, service: str | None = None) -> None:
        state = self._state()
        command = self._compose(state["version"]) + ["logs", "--no-color"]
        if service:
            command.append(service)
        self._run(command, "log collection failed")

    def uninstall(self, purge_data: bool, confirmation: str | None) -> None:
        state = self._state()
        command = self._compose(state["version"]) + ["down", "--remove-orphans"]
        if purge_data:
            required = f"PURGE {self.target}"
            if confirmation != required:
                raise InputError(f"data purge requires exact confirmation: {required}")
            command.append("--volumes")
        self._run(command, "uninstall failed")
        self._write_state(status="uninstalled")
        if purge_data:
            shutil.rmtree(self.target / "shared", ignore_errors=True)
            shutil.rmtree(self.target / "backups", ignore_errors=True)


def _version_key(value: str) -> tuple[int, int, int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?", value)
    if not match:
        raise InputError(f"invalid version: {value}")
    major, minor, patch, rc = match.groups()
    return int(major), int(minor), int(patch), 1 if rc is None else 0, int(rc or 0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="surveyctl", description="Manage a single-host Survey production installation")
    parser.add_argument("--target", default="/opt/survey", help="installation directory (default: /opt/survey)")
    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser("install")
    install_source = install.add_mutually_exclusive_group(required=True)
    install_source.add_argument("--manifest")
    install_source.add_argument("--version")
    install.add_argument("--manifest-sha256")
    install.add_argument("--public-host", required=True)
    install.add_argument("--admin-user", required=True)
    install.add_argument("--admin-email", required=True)
    upgrade = sub.add_parser("upgrade")
    upgrade_source = upgrade.add_mutually_exclusive_group(required=True)
    upgrade_source.add_argument("--manifest")
    upgrade_source.add_argument("--version")
    upgrade.add_argument("--manifest-sha256")
    rollback = sub.add_parser("rollback")
    rollback.add_argument("--version")
    backup = sub.add_parser("backup")
    backup.add_argument("--output")
    restore = sub.add_parser("restore")
    restore.add_argument("backup")
    sub.add_parser("status")
    sub.add_parser("doctor")
    logs = sub.add_parser("logs")
    logs.add_argument("service", nargs="?")
    uninstall = sub.add_parser("uninstall")
    uninstall.add_argument("--purge-data", action="store_true")
    uninstall.add_argument("--confirm")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    manager = SurveyManager(args.target)
    try:
        if args.command == "install":
            if args.version:
                manager.install_version(args.version, args.manifest_sha256, args.public_host, args.admin_user, args.admin_email)
            else:
                manager.install(args.manifest, args.public_host, args.admin_user, args.admin_email, args.manifest_sha256)
        elif args.command == "upgrade":
            if args.version:
                manager.upgrade_version(args.version, args.manifest_sha256)
            else:
                manager.upgrade(args.manifest, args.manifest_sha256)
        elif args.command == "rollback":
            manager.rollback(args.version)
        elif args.command == "backup":
            manager.backup(args.output or manager.target / "backups" / "manual")
        elif args.command == "restore":
            manager.restore(args.backup)
        elif args.command == "status":
            manager.status()
        elif args.command == "doctor":
            manager.doctor()
        elif args.command == "logs":
            manager.logs(args.service)
        elif args.command == "uninstall":
            confirmation = args.confirm
            if args.purge_data and confirmation is None and sys.stdin.isatty():
                confirmation = input(f"Type PURGE {manager.target} to permanently delete data: ")
            manager.uninstall(args.purge_data, confirmation)
        return EXIT_SUCCESS
    except SurveyctlError as exc:
        print(f"surveyctl: {exc}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
