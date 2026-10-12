#!/usr/bin/env python3
"""Production lifecycle controller for a single-host Survey installation."""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import secrets
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Any, Callable
import urllib.error
import urllib.request
import uuid


EXIT_SUCCESS, EXIT_INPUT, EXIT_ENVIRONMENT, EXIT_RUNTIME, EXIT_INTEGRITY = 0, 2, 3, 4, 5
MIN_DOCKER_VERSION = (24, 0, 0)
MIN_COMPOSE_VERSION = (2, 20, 0)
MIN_DISK_BYTES = 20 * 1024**3
NATIVE_ACCEPTANCE_DISK_BYTES = 8 * 1024**3
MIN_MEMORY_BYTES = 4 * 1024**3
SEMVER_PATTERN = r"\d+\.\d+\.\d+(?:-rc\.\d+)?"
SCHEMA_PATTERN = r"[A-Za-z0-9._-]{1,64}"
IMAGE_KEYS = {
    "CADDY_IMAGE", "ADMIN_IMAGE", "PLATFORM_IMAGE", "PUBLISH_GATEWAY_IMAGE",
    "ENGINE_IMAGE", "POSTGRES_IMAGE", "MARIADB_IMAGE",
}
LONG_RUNNING_SERVICES = {
    "edge", "admin-web", "platform", "publish-gateway", "engine", "platform-db", "engine-db",
}
ONE_SHOT_SERVICES = {"engine-init"}
SECRET_NAMES = (
    "engine_admin_password_hash", "engine_admin_password", "engine_db_password",
    "engine_db_root_password", "platform_db_superuser_password", "platform_db_owner_password",
    "platform_db_app_password", "platform_jwt_hmac_secret", "platform_engine_events_secret",
    "platform_pubgw_secret", "pubgw_engine_admin_password", "backup_encryption_key",
    "engine_instance_events_secret",
)
REQUIRED_BUNDLE_FILES = {
    "compose.yml", "Caddyfile", "surveyctl", "surveyctl.py", "release.schema.json",
    "env.example", "engines.example.json", "backup.py", "restore.py", "doctor.py",
}


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise IntegrityError(f"cannot read integrity-protected file: {path.name}") from exc
    return digest.hexdigest()


def _validate_semver(value: Any, label: str = "version") -> str:
    if not isinstance(value, str) or re.fullmatch(SEMVER_PATTERN, value) is None:
        raise InputError(f"invalid {label}")
    return value


def _version_key(value: str) -> tuple[int, int, int, int, int]:
    _validate_semver(value)
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?", value)
    assert match is not None
    major, minor, patch, rc = match.groups()
    return int(major), int(minor), int(patch), 1 if rc is None else 0, int(rc or 0)


def _regular_file(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise InputError(f"{label} is not readable: {path}") from exc
    if path.is_symlink() or not path.is_file():
        raise InputError(f"{label} must be a regular file: {path}")
    if metadata.st_size <= 0:
        raise IntegrityError(f"{label} is empty")


def _schema_error(location: str, message: str) -> None:
    raise IntegrityError(f"release manifest schema error at {location}: {message}")


def _resolve_schema_ref(root: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        _schema_error("$ref", "only local references are supported")
    value: Any = root
    for component in reference[2:].split("/"):
        if not isinstance(value, dict) or component not in value:
            _schema_error("$ref", "reference cannot be resolved")
        value = value[component]
    if not isinstance(value, dict):
        _schema_error("$ref", "reference target is not a schema")
    return value


def _validate_json_schema(value: Any, schema: dict[str, Any], root: dict[str, Any], location: str = "$") -> None:
    if "$ref" in schema:
        _validate_json_schema(value, _resolve_schema_ref(root, schema["$ref"]), root, location)
        return
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        _schema_error(location, "constant value does not match")
    if "enum" in schema and not any(type(value) is type(item) and value == item for item in schema["enum"]):
        _schema_error(location, "value is not in the allowed set")
    expected = schema.get("type")
    matches = {
        "object": isinstance(value, dict), "array": isinstance(value, list),
        "string": isinstance(value, str), "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
    }
    if expected and not matches.get(expected, False):
        _schema_error(location, f"expected {expected}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                _schema_error(location, f"missing required key {key}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(properties)
            if extras:
                _schema_error(location, f"unexpected keys: {', '.join(sorted(extras))}")
        for key, child in value.items():
            if key in properties:
                _validate_json_schema(child, properties[key], root, f"{location}.{key}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            _schema_error(location, "array is too short")
        if schema.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            _schema_error(location, "array items must be unique")
        if "items" in schema:
            for index, item in enumerate(value):
                _validate_json_schema(item, schema["items"], root, f"{location}[{index}]")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            _schema_error(location, "string is too short")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            _schema_error(location, "string does not match the required pattern")


@dataclass(frozen=True)
class ReleaseManifest:
    path: Path
    raw: dict[str, Any]
    sha256: str

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

    def bundle_for(self, architecture: str) -> dict[str, str]:
        try:
            return self.raw["assets"]["bundles"][architecture]
        except KeyError as exc:
            raise IntegrityError(f"release has no bundle for architecture {architecture}") from exc


def load_manifest(path: Path | str, expected_sha256: str | None = None) -> ReleaseManifest:
    manifest_path = Path(path)
    _regular_file(manifest_path, "release manifest")
    try:
        payload = manifest_path.read_bytes()
    except OSError as exc:
        raise InputError(f"cannot read release manifest: {manifest_path}") from exc
    actual = hashlib.sha256(payload).hexdigest()
    if expected_sha256:
        if re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256) is None:
            raise InputError("manifest SHA-256 must be 64 hexadecimal characters")
        if not secrets.compare_digest(actual, expected_sha256.lower()):
            raise IntegrityError("release manifest checksum mismatch")
    try:
        data = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrityError("release manifest is not valid UTF-8 JSON") from exc
    schema_path = Path(__file__).resolve().parent / "release.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrityError("release schema is unavailable or invalid") from exc
    _validate_json_schema(data, schema, schema)
    assert isinstance(data, dict)
    architectures = data["supportedHosts"]["architectures"]
    bundles = data["assets"]["bundles"]
    if set(architectures) != set(bundles):
        raise IntegrityError("release bundles must exactly match supported architectures")
    for architecture, asset in bundles.items():
        expected_name = f"survey-{data['version']}-linux-{architecture}.tar.gz"
        if asset["name"] != expected_name:
            raise IntegrityError(f"bundle name does not match release version and architecture: {architecture}")
    if set(data["images"]) != IMAGE_KEYS:
        raise IntegrityError("release images do not match the production service set")
    return ReleaseManifest(manifest_path, data, actual)


class CommandRunner:
    def run(self, command, **kwargs):
        kwargs.setdefault("check", True)
        kwargs.setdefault("text", True)
        return subprocess.run(command, **kwargs)


class HostProbe:
    ARCHITECTURES = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}

    def _os_release(self) -> dict[str, str]:
        try:
            lines = Path("/etc/os-release").read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise EnvironmentError("cannot read /etc/os-release") from exc
        values = {}
        for line in lines:
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

    def _local_addresses(self) -> list[str]:
        addresses = set()
        try:
            addresses.update(item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None))
        except socket.gaierror:
            pass
        for family, destination in (
            (socket.AF_INET, ("8.8.8.8", 53)),
            (socket.AF_INET6, ("2001:4860:4860::8888", 53)),
        ):
            sock = socket.socket(family, socket.SOCK_DGRAM)
            try:
                sock.connect(destination)
                addresses.add(sock.getsockname()[0])
            except OSError:
                pass
            finally:
                sock.close()
        return sorted(
            str(address)
            for value in addresses
            if not (
                (address := ipaddress.ip_address(value.split("%", 1)[0])).is_loopback
                or address.is_link_local
                or address.is_unspecified
            )
        )

    def inspect(self, host: str, target: Path) -> dict[str, Any]:
        release = self._os_release()
        try:
            dns = sorted({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
        except socket.gaierror:
            dns = []
        dns_public = bool(dns) and all(
            not (address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified)
            for address in (ipaddress.ip_address(item) for item in dns)
        )
        memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        synchronized = False
        if shutil.which("timedatectl"):
            result = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], capture_output=True, text=True)
            synchronized = result.returncode == 0 and result.stdout.strip().lower() == "yes"
        systemd = False
        if Path("/run/systemd/system").is_dir() and shutil.which("systemctl") is not None:
            result = subprocess.run(["systemctl", "is-system-running"], capture_output=True, text=True)
            systemd = result.stdout.strip() in {"running", "degraded"}
        anchor = target
        while not anchor.exists() and anchor != anchor.parent:
            anchor = anchor.parent
        return {
            "os_id": release.get("ID", ""), "os_version": release.get("VERSION_ID", ""),
            "architecture": self.ARCHITECTURES.get(platform.machine(), platform.machine()),
            "docker_version": self._command_version(["docker", "version", "--format", "{{.Server.Version}}"]),
            "compose_version": self._command_version(["docker", "compose", "version", "--short"]),
            "free_bytes": shutil.disk_usage(anchor).free, "memory_bytes": memory,
            "ports_available": self._ports_available(), "dns_addresses": dns, "dns_public": dns_public,
            "local_addresses": self._local_addresses(),
            "time_synchronized": synchronized, "systemd": systemd,
        }

    def verify_tls(self, host: str, ca_file: Path | None = None) -> bool:
        request = urllib.request.Request(
            f"https://{host}/.well-known/survey-health",
            headers={"User-Agent": "surveyctl/1"},
        )
        try:
            context = ssl.create_default_context(cafile=str(ca_file)) if ca_file else ssl.create_default_context()
            with urllib.request.urlopen(request, timeout=15, context=context) as response:
                marker = response.headers.get("X-Survey-Deployment", "")
                body = response.read(64).decode("ascii", errors="replace").strip()
                return response.status == 200 and marker == "survey-production-v1" and body == marker
        except (OSError, urllib.error.URLError, ssl.SSLError):
            return False


class GitHubReleaseClient:
    def __init__(self, repository: str = "lsgoodlionel/star-survey"):
        self.repository = repository

    def _fetch(self, version: str, asset: str) -> bytes:
        url = f"https://github.com/{self.repository}/releases/download/v{version}/{asset}"
        request = urllib.request.Request(url, headers={"Accept": "application/octet-stream", "User-Agent": "surveyctl/1"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except (OSError, urllib.error.URLError) as exc:
            raise EnvironmentError("could not download the requested release asset") from exc

    def fetch_manifest(self, version: str) -> bytes:
        return self._fetch(version, f"survey-{version}-release.json")

    def fetch_asset(self, version: str, name: str) -> bytes:
        return self._fetch(version, name)


def _safe_tar_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    if len(members) > 4096:
        raise IntegrityError("archive contains too many entries")
    total, seen = 0, set()
    for member in members:
        pure = PurePosixPath(member.name)
        normalized = pure.as_posix().rstrip("/")
        if not member.name or pure.is_absolute() or ".." in pure.parts or "\\" in member.name:
            raise IntegrityError("archive contains an unsafe path")
        if not normalized or normalized in seen:
            raise IntegrityError("archive contains duplicate or empty paths")
        seen.add(normalized)
        if not (member.isfile() or member.isdir()):
            raise IntegrityError("archive links and special files are forbidden")
        total += member.size
        if total > 2 * 1024**3:
            raise IntegrityError("archive uncompressed size exceeds the safety limit")
    return members


def _extract_safe_tar(archive_path: Path, destination: Path) -> None:
    _regular_file(archive_path, "release package")
    try:
        with tarfile.open(archive_path, "r:*") as archive:
            members = _safe_tar_members(archive)
            destination.mkdir(mode=0o700, parents=True, exist_ok=False)
            for member in members:
                target = destination.joinpath(*PurePosixPath(member.name).parts)
                if member.isdir():
                    target.mkdir(mode=member.mode & 0o755 or 0o700, parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise IntegrityError("archive file cannot be read")
                with target.open("xb") as output:
                    shutil.copyfileobj(source, output)
                os.chmod(target, member.mode & 0o755 or 0o600)
    except SurveyctlError:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    except (tarfile.TarError, OSError) as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise IntegrityError("release package cannot be safely extracted") from exc


class SurveyManager:
    def __init__(
        self,
        target: Path | str,
        runner=None,
        host_probe=None,
        release_client=None,
        source_dir=None,
        expected_public_addresses=None,
    ):
        raw_target = Path(target).expanduser()
        if ".." in raw_target.parts:
            raise InputError("target path cannot contain '..'")
        self.target = Path(os.path.abspath(os.fspath(raw_target)))
        self.runner = runner or CommandRunner()
        self.host_probe = host_probe or HostProbe()
        self.release_client = release_client or GitHubReleaseClient()
        self.source_dir = Path(source_dir or Path(__file__).resolve().parent)
        self.control_dir = self.target / ".surveyctl"
        self.state_path = self.control_dir / "state.json"
        self.current_path = self.target / "current"
        self.expected_public_addresses = self._normalize_expected_addresses(expected_public_addresses or [])
        self._reject_symlink_components(self.target)

    @staticmethod
    def _normalize_expected_addresses(values) -> list[str]:
        if not isinstance(values, (list, tuple)):
            raise InputError("expected public addresses must be a list")
        try:
            normalized = sorted({str(ipaddress.ip_address(value)) for value in values if isinstance(value, str)})
        except ValueError as exc:
            raise InputError("expected public address is invalid") from exc
        if len(normalized) != len(set(values)) or any(not isinstance(value, str) for value in values):
            raise InputError("expected public address is invalid")
        return normalized

    def _reject_symlink_components(self, path: Path) -> None:
        absolute = Path(os.path.abspath(os.fspath(path)))
        current = Path(absolute.anchor)
        for component in absolute.parts[1:]:
            current = current / component
            if current.is_symlink():
                raise InputError(f"managed path contains a symbolic link: {current}")
            if not current.exists():
                break

    def _managed(self, relative: str | Path, *, must_exist: bool = False) -> Path:
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise InputError("managed paths must be relative and cannot escape the target")
        candidate = self.target / relative_path
        if os.path.commonpath([self.target, candidate]) != str(self.target):
            raise InputError("managed path escapes the target")
        self._reject_symlink_components(candidate)
        if must_exist and not candidate.exists():
            raise InputError(f"managed path does not exist: {relative_path}")
        return candidate

    def _mkdir(self, relative: str | Path, mode: int = 0o700) -> Path:
        path = self._managed(relative)
        path.mkdir(parents=True, exist_ok=True, mode=mode)
        self._reject_symlink_components(path)
        if not path.is_dir():
            raise InputError(f"managed path is not a directory: {relative}")
        os.chmod(path, mode)
        return path

    def _assert_target_available(self) -> None:
        self._reject_symlink_components(self.target)
        if not self.target.exists() or not any(self.target.iterdir()) or self.state_path.exists():
            return
        entries, downloads = set(self.target.iterdir()), self.control_dir / "downloads"
        if (
            entries == {self.control_dir} and self.control_dir.is_dir() and not self.control_dir.is_symlink()
            and set(self.control_dir.iterdir()) <= {downloads}
            and (not downloads.exists() or (downloads.is_dir() and not downloads.is_symlink()))
        ):
            return
        raise InputError("target directory is non-empty and is not owned by surveyctl")

    def _read_state(self, require_current: bool = False) -> dict[str, Any]:
        state_path = self._managed(".surveyctl/state.json", must_exist=True)
        _regular_file(state_path, "installation state")
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IntegrityError("installation state is corrupt") from exc
        if not isinstance(state, dict):
            raise IntegrityError("installation state must be an object")
        for key in ("status", "version", "databaseSchema", "publicHost", "adminUser", "adminEmail"):
            if not isinstance(state.get(key), str) or not state[key]:
                raise IntegrityError(f"installation state field is invalid: {key}")
        try:
            _validate_semver(state["version"], "state version")
        except InputError as exc:
            raise IntegrityError("installation state version is invalid") from exc
        if re.fullmatch(SCHEMA_PATTERN, state["databaseSchema"]) is None:
            raise IntegrityError("installation database schema is invalid")
        if "previousVersion" in state:
            try:
                _validate_semver(state["previousVersion"], "previous version")
            except InputError as exc:
                raise IntegrityError("installation previous version is invalid") from exc
        if "expectedPublicAddresses" in state:
            try:
                state["expectedPublicAddresses"] = self._normalize_expected_addresses(state["expectedPublicAddresses"])
            except InputError as exc:
                raise IntegrityError("installation expected public addresses are invalid") from exc
        if require_current and self._read_current() != state["version"]:
            raise IntegrityError("current version pointer does not match installation state")
        return state

    def _read_current(self) -> str:
        current = self._managed("current", must_exist=True)
        _regular_file(current, "current version pointer")
        try:
            return _validate_semver(current.read_text(encoding="utf-8").strip(), "current version pointer")
        except (OSError, UnicodeDecodeError, InputError) as exc:
            raise IntegrityError("current version pointer is invalid") from exc

    def _atomic_write(self, path: Path, content: str, mode: int = 0o600) -> None:
        relative = path.relative_to(self.target) if path.is_absolute() else path
        path = self._managed(relative)
        parent = self._mkdir(path.parent.relative_to(self.target))
        if path.is_symlink():
            raise InputError(f"refusing to replace symbolic link: {path}")
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
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
        state = self._read_state() if self.state_path.exists() else {}
        state.update(values)
        state["managedPaths"] = [
            str(self.control_dir), str(self.target / "releases"), str(self.target / "shared"),
            str(self.target / "backups"), str(self.current_path),
        ]
        self._atomic_write(self.state_path, json.dumps(state, indent=2, sort_keys=True) + "\n")

    def _release_dir(self, version: str, must_exist: bool = True) -> Path:
        _validate_semver(version, "release version")
        release = self._managed(Path("releases") / version, must_exist=must_exist)
        if must_exist and (not release.is_dir() or release.is_symlink()):
            raise IntegrityError("release directory is invalid")
        return release

    def _release_manifest(self, version: str) -> ReleaseManifest:
        release = self._release_dir(version)
        manifest = load_manifest(release / "release.json")
        if manifest.version != version:
            raise IntegrityError("release directory and manifest versions do not match")
        return manifest

    def _compose(self, version: str) -> list[str]:
        release = self._release_dir(version)
        self._verify_staged_release(release, version)
        for name in (".env", "compose.yml"):
            _regular_file(release / name, f"release {name}")
            self._reject_symlink_components(release / name)
        return [
            "docker", "compose", "--project-directory", str(self.target),
            "--env-file", str(release / ".env"), "-f", str(release / "compose.yml"),
        ]

    def _verify_staged_release(self, release: Path, version: str) -> None:
        self._reject_symlink_components(release)
        for path in release.rglob("*"):
            if path.is_symlink():
                raise IntegrityError("staged release contains a symbolic link")
        manifest = load_manifest(release / "release.json")
        if manifest.version != version:
            raise IntegrityError("staged release version does not match its directory")
        metadata_path = release / "bundle-metadata.json"
        integrity_path = release / "deployment-integrity.json"
        _regular_file(metadata_path, "bundle metadata")
        _regular_file(integrity_path, "deployment integrity metadata")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            integrity = json.loads(integrity_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IntegrityError("staged release integrity metadata is invalid") from exc
        files = metadata.get("files") if isinstance(metadata, dict) else None
        if not isinstance(files, dict) or not files:
            raise IntegrityError("staged bundle file inventory is invalid")
        for name, expected in files.items():
            path = release.joinpath(*PurePosixPath(name).parts)
            _regular_file(path, "staged bundle file")
            if not secrets.compare_digest(_sha256(path), expected):
                raise IntegrityError(f"staged bundle file checksum mismatch: {name}")
        expected_integrity_keys = {"schemaVersion", "manifestSha256", "architecture", "bundleFile", "generatedFiles"}
        if not isinstance(integrity, dict) or set(integrity) != expected_integrity_keys or integrity.get("schemaVersion") != 1:
            raise IntegrityError("deployment integrity metadata shape is invalid")
        if not secrets.compare_digest(str(integrity["manifestSha256"]), manifest.sha256):
            raise IntegrityError("staged manifest checksum does not match deployment metadata")
        architecture = integrity["architecture"]
        if architecture not in manifest.raw["supportedHosts"]["architectures"]:
            raise IntegrityError("deployment architecture is not supported by the release")
        retained_bundle = release / integrity["bundleFile"]
        if integrity["bundleFile"] != ".release-bundle.tar.gz":
            raise IntegrityError("deployment bundle path is invalid")
        _regular_file(retained_bundle, "retained release bundle")
        if not secrets.compare_digest(_sha256(retained_bundle), manifest.bundle_for(architecture)["sha256"]):
            raise IntegrityError("retained release bundle checksum does not match manifest")
        try:
            with tarfile.open(retained_bundle, "r:*") as archive:
                members = _safe_tar_members(archive)
                metadata_members = [member for member in members if member.name == "bundle-metadata.json" and member.isfile()]
                if len(metadata_members) != 1:
                    raise IntegrityError("retained release bundle metadata is missing")
                source = archive.extractfile(metadata_members[0])
                if source is None or not secrets.compare_digest(hashlib.sha256(source.read()).hexdigest(), _sha256(metadata_path)):
                    raise IntegrityError("extracted bundle metadata does not match retained bundle")
        except tarfile.TarError as exc:
            raise IntegrityError("retained release bundle is invalid") from exc
        generated = integrity["generatedFiles"]
        if not isinstance(generated, dict) or set(generated) != {".env", "engines.json"}:
            raise IntegrityError("generated configuration inventory is invalid")
        for name, expected in generated.items():
            path = release / name
            _regular_file(path, "generated release configuration")
            if not secrets.compare_digest(_sha256(path), str(expected)):
                raise IntegrityError(f"generated release configuration checksum mismatch: {name}")

    def _run(self, command: list[str], runtime_message: str, **kwargs):
        try:
            return self.runner.run(command, cwd=self.target, **kwargs)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeHealthError(runtime_message) from exc

    def _run_helper(self, command: list[str], runtime_message: str, **kwargs):
        try:
            return self.runner.run(command, cwd=self.target, **kwargs)
        except subprocess.CalledProcessError as exc:
            if exc.returncode == EXIT_INTEGRITY:
                raise IntegrityError(runtime_message) from exc
            raise RuntimeHealthError(runtime_message) from exc
        except OSError as exc:
            raise RuntimeHealthError(runtime_message) from exc

    def _assert_healthy(self, compose: list[str], message: str) -> None:
        result = self._run(compose + ["ps", "--all", "--format", "json"], message, capture_output=True)
        try:
            text = result.stdout.strip()
            records = json.loads(text) if text.startswith("[") else [json.loads(line) for line in text.splitlines() if line]
        except (AttributeError, json.JSONDecodeError) as exc:
            raise RuntimeHealthError(f"{message}: unreadable container status") from exc
        if not isinstance(records, list) or not records or not all(isinstance(item, dict) for item in records):
            raise RuntimeHealthError(f"{message}: container status is empty or invalid")
        services, expected = [record.get("Service") for record in records], LONG_RUNNING_SERVICES | ONE_SHOT_SERVICES
        if set(services) != expected or len(services) != len(expected):
            raise RuntimeHealthError(f"{message}: service set does not match production compose")
        by_service = {record["Service"]: record for record in records}
        unhealthy = {
            service for service in LONG_RUNNING_SERVICES
            if by_service[service].get("State") != "running" or by_service[service].get("Health") != "healthy"
        }
        init = by_service["engine-init"]
        raw_exit_code = init.get("ExitCode", 1)
        if isinstance(raw_exit_code, bool) or not (
            isinstance(raw_exit_code, int)
            or (isinstance(raw_exit_code, str) and re.fullmatch(r"\d+", raw_exit_code) is not None)
        ):
            raise RuntimeHealthError(f"{message}: engine-init status is invalid")
        exit_code = int(raw_exit_code)
        init_ok = init.get("State") in {"exited", "completed"} and exit_code == 0
        if unhealthy or not init_ok:
            raise RuntimeHealthError(f"{message}: unhealthy={sorted(unhealthy)}, engine-init-ok={init_ok}")

    def _validate_install_inputs(self, public_host: str, admin_user: str, admin_email: str) -> None:
        hostname = r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
        if not isinstance(public_host, str) or re.fullmatch(hostname, public_host) is None:
            raise InputError("public host must be a DNS hostname without a scheme or path")
        if not isinstance(admin_user, str) or re.fullmatch(r"[A-Za-z0-9._-]{1,64}", admin_user) is None:
            raise InputError("administrator user contains unsupported characters")
        if not isinstance(admin_email, str) or re.fullmatch(r"[^@\s]+@[^@\s]+", admin_email) is None:
            raise InputError("administrator email is invalid")

    @staticmethod
    def _native_acceptance_profile(manifest: ReleaseManifest) -> dict[str, Any] | None:
        requested = os.environ.get("SURVEY_NATIVE_ACCEPTANCE") == "1"
        on_actions = os.environ.get("GITHUB_ACTIONS") == "true"
        declared = manifest.raw.get("nativeAcceptance")
        if requested and on_actions:
            if declared is None and os.environ.get("SURVEY_NATIVE_ACCEPTANCE_PROFILE_MANIFEST"):
                expected_sha = os.environ.get("SURVEY_NATIVE_ACCEPTANCE_PROFILE_MANIFEST_SHA256", "")
                if re.fullmatch(r"[0-9a-f]{64}", expected_sha) is None:
                    raise EnvironmentError("native acceptance profile manifest checksum is required")
                profile_manifest = load_manifest(
                    os.environ["SURVEY_NATIVE_ACCEPTANCE_PROFILE_MANIFEST"],
                    expected_sha,
                )
                declared = profile_manifest.raw.get("nativeAcceptance")
            expected = {
                "profile": "github-actions-native-v1",
                "minimumFreeBytes": NATIVE_ACCEPTANCE_DISK_BYTES,
                "tlsMode": "local-ca",
                "diskEvidence": "separately-tested",
            }
            if declared != expected:
                raise EnvironmentError("native acceptance requires the fixed manifest profile")
            return declared
        return None

    def preflight(
        self,
        manifest: ReleaseManifest,
        host: str,
        require_ports: bool = True,
        expected_addresses=None,
    ) -> dict[str, Any]:
        try:
            values = self.host_probe.inspect(host, self.target)
        except TypeError:
            values = self.host_probe.inspect(host)
        except SurveyctlError:
            raise
        except (OSError, KeyError, ValueError) as exc:
            raise EnvironmentError("host preflight could not inspect the environment") from exc
        supported, failures = manifest.raw["supportedHosts"], []
        native_profile = self._native_acceptance_profile(manifest)
        required_disk = native_profile["minimumFreeBytes"] if native_profile else MIN_DISK_BYTES
        if values.get("os_id") != "ubuntu" or values.get("os_version") not in supported["ubuntu"]:
            failures.append("unsupported Ubuntu release")
        if values.get("architecture") not in supported["architectures"]:
            failures.append("unsupported architecture")
        if not values.get("systemd"):
            failures.append("systemd is required")
        if _parse_tool_version(values.get("docker_version")) < MIN_DOCKER_VERSION:
            failures.append("Docker Engine 24.0.0 or newer is required")
        if _parse_tool_version(values.get("compose_version")) < MIN_COMPOSE_VERSION:
            failures.append("Docker Compose 2.20.0 or newer is required")
        if values.get("free_bytes", 0) < required_disk:
            failures.append(f"at least {required_disk // 1024**3} GiB free disk is required on the target filesystem")
        if values.get("memory_bytes", 0) < MIN_MEMORY_BYTES:
            failures.append("at least 4 GiB memory is required")
        if require_ports and not values.get("ports_available"):
            failures.append("ports 80 and 443 must be available")
        local_acceptance_dns = bool(
            native_profile
            and host.endswith(".test")
            and set(values.get("dns_addresses", [])) == {"127.0.0.1"}
        )
        if not values.get("dns_addresses") or (not values.get("dns_public") and not local_acceptance_dns):
            failures.append("public host DNS must resolve to a public address")
        try:
            dns_addresses = {str(ipaddress.ip_address(value)) for value in values.get("dns_addresses", [])}
            configured = expected_addresses if expected_addresses is not None else self.expected_public_addresses
            endpoint_addresses = set(self._normalize_expected_addresses(configured or values.get("local_addresses", [])))
        except (TypeError, ValueError, InputError):
            dns_addresses, endpoint_addresses = set(), set()
        if not endpoint_addresses:
            failures.append("no local or explicitly expected deployment address is available")
        elif not dns_addresses.intersection(endpoint_addresses):
            failures.append("public host DNS does not resolve to this deployment endpoint")
        if not values.get("time_synchronized"):
            failures.append("system time must be synchronized")
        if failures:
            raise EnvironmentError("; ".join(failures))
        values["required_disk_bytes"] = required_disk
        values["production_required_disk_bytes"] = MIN_DISK_BYTES
        values["native_acceptance_profile"] = bool(native_profile)
        return values

    def _verify_public_tls(self, host: str) -> None:
        ca_file = None
        try:
            state = self._read_state()
            manifest = self._release_manifest(state["version"])
            if self._native_acceptance_profile(manifest):
                ca_file = self._managed(".surveyctl/native-acceptance-ca.crt")
                self._run(
                    self._compose(state["version"]) + [
                        "cp", "edge:/data/caddy/pki/authorities/local/root.crt", str(ca_file),
                    ],
                    "native acceptance CA export failed",
                )
                os.chmod(ca_file, 0o644)
            valid = self.host_probe.verify_tls(host, ca_file=ca_file)
        except (OSError, ValueError) as exc:
            raise RuntimeHealthError("public HTTPS verification failed") from exc
        if not valid:
            raise RuntimeHealthError("public HTTPS certificate or route verification failed")

    def _write_download(self, name: str, payload: bytes) -> Path:
        if Path(name).name != name or not name:
            raise IntegrityError("release asset name is unsafe")
        self._mkdir(".surveyctl/downloads")
        path = self._managed(Path(".surveyctl/downloads") / name)
        if path.exists() or path.is_symlink():
            raise InputError(f"download destination already exists: {name}")
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise InputError(f"cannot write downloaded release asset: {name}") from exc
        return path

    def _verify_bundle_payload(self, directory: Path, manifest: ReleaseManifest, architecture: str) -> None:
        metadata_path = directory / "bundle-metadata.json"
        _regular_file(metadata_path, "bundle metadata")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IntegrityError("bundle metadata is invalid") from exc
        if not isinstance(metadata, dict) or set(metadata) != {"schemaVersion", "version", "architecture", "files"}:
            raise IntegrityError("bundle metadata shape is invalid")
        if metadata["schemaVersion"] != 1 or metadata["version"] != manifest.version or metadata["architecture"] != architecture:
            raise IntegrityError("bundle metadata does not match release version and architecture")
        files = metadata["files"]
        if not isinstance(files, dict) or not files:
            raise IntegrityError("bundle metadata file inventory is empty")
        actual = set()
        for path in directory.rglob("*"):
            if path.is_symlink():
                raise IntegrityError("bundle payload contains a symbolic link")
            if path.is_file() and path != metadata_path:
                actual.add(path.relative_to(directory).as_posix())
        if actual != set(files):
            raise IntegrityError("bundle file inventory does not match extracted payload")
        reserved = {"release.json", ".env", "engines.json", "deployment-integrity.json", ".release-bundle.tar.gz"}
        if actual & reserved:
            raise IntegrityError("bundle payload contains a reserved installer path")
        if not REQUIRED_BUNDLE_FILES <= actual or not any(name.startswith("init/") for name in actual):
            raise IntegrityError("bundle is missing required production runtime files")
        for name, expected in files.items():
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts or re.fullmatch(r"[0-9a-f]{64}", str(expected)) is None:
                raise IntegrityError("bundle metadata contains an unsafe file entry")
            path = directory.joinpath(*pure.parts)
            _regular_file(path, "bundle payload file")
            if not secrets.compare_digest(_sha256(path), expected):
                raise IntegrityError(f"bundle payload checksum mismatch: {name}")

    def _stage_release(self, manifest: ReleaseManifest, architecture: str, host: str, admin_user: str, admin_email: str, bundle_path=None, asset_loader=None) -> Path:
        asset = manifest.bundle_for(architecture)
        if bundle_path is None:
            bundle_path = self._write_download(asset["name"], asset_loader(asset["name"])) if asset_loader else manifest.path.parent / asset["name"]
        bundle_path = Path(bundle_path)
        try:
            _regular_file(bundle_path, "release bundle")
        except InputError as exc:
            raise IntegrityError("declared release bundle is missing or is not a regular file") from exc
        if bundle_path.name != asset["name"] or not secrets.compare_digest(_sha256(bundle_path), asset["sha256"]):
            raise IntegrityError("release bundle filename or checksum mismatch")
        releases, final = self._mkdir("releases"), self._managed(Path("releases") / manifest.version)
        if final.exists() or final.is_symlink():
            raise InputError("release version is already staged")
        staging = releases / f".staging-{manifest.version}-{uuid.uuid4().hex}"
        try:
            _extract_safe_tar(bundle_path, staging)
            self._verify_bundle_payload(staging, manifest, architecture)
            shutil.copyfile(bundle_path, staging / ".release-bundle.tar.gz", follow_symlinks=False)
            os.chmod(staging / ".release-bundle.tar.gz", 0o600)
            shutil.copyfile(manifest.path, staging / "release.json", follow_symlinks=False)
            os.chmod(staging / "release.json", 0o600)
            os.replace(staging, final)
            self._configure_release(final, manifest, architecture, host, admin_user, admin_email)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            if final.exists():
                shutil.rmtree(final, ignore_errors=True)
            if releases.exists() and not any(releases.iterdir()):
                releases.rmdir()
            raise
        return final

    def _configure_release(self, release: Path, manifest: ReleaseManifest, architecture: str, host: str, admin_user: str, admin_email: str) -> None:
        example = release / "engines.example.json"
        _regular_file(example, "engine configuration template")
        try:
            engines = json.loads(example.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IntegrityError("verified engine configuration template is invalid") from exc
        self._atomic_write(release / "engines.json", json.dumps(engines, indent=2) + "\n")
        secret_dir = self._mkdir("shared/secrets")
        for name in SECRET_NAMES:
            path = secret_dir / name
            if not path.exists() and name not in {"engine_admin_password_hash", "engine_instance_events_secret"}:
                self._atomic_write(path, secrets.token_urlsafe(48) + "\n")
        master = (secret_dir / "platform_engine_events_secret").read_bytes().strip()
        instance_secret = hmac.new(
            master, b"mjy-engine-events/v1/production-engine-01", hashlib.sha256
        ).hexdigest()
        instance_path = secret_dir / "engine_instance_events_secret"
        if not instance_path.exists():
            self._atomic_write(instance_path, instance_secret + "\n")
        elif not secrets.compare_digest(instance_path.read_text(encoding="ascii").strip(), instance_secret):
            raise IntegrityError("engine instance event secret does not match the platform trust root")
        lines = [f"PUBLIC_HOST={host}"] + [f"{key}={value}" for key, value in sorted(manifest.raw["images"].items())]
        lines += [
            "ENGINE_INSTANCE_ID=production-engine-01", f"ENGINE_ADMIN_USER={admin_user}",
            f"ENGINE_ADMIN_EMAIL={admin_email}", f"ENGINES_CONFIG_FILE={release / 'engines.json'}",
        ]
        if self._native_acceptance_profile(manifest):
            lines.append("CADDY_TLS_DIRECTIVE=tls internal")
        env_keys = {
            "ENGINE_ADMIN_PASSWORD_HASH_FILE": "engine_admin_password_hash", "ENGINE_ADMIN_PASSWORD_FILE": "engine_admin_password",
            "ENGINE_DB_PASSWORD_FILE": "engine_db_password", "ENGINE_DB_ROOT_PASSWORD_FILE": "engine_db_root_password",
            "PLATFORM_DB_SUPERUSER_PASSWORD_FILE": "platform_db_superuser_password", "PLATFORM_DB_OWNER_PASSWORD_FILE": "platform_db_owner_password",
            "PLATFORM_DB_APP_PASSWORD_FILE": "platform_db_app_password", "PLATFORM_JWT_HMAC_SECRET_FILE": "platform_jwt_hmac_secret",
            "PLATFORM_ENGINE_EVENTS_SECRET_FILE": "platform_engine_events_secret", "PLATFORM_PUBGW_SECRET_FILE": "platform_pubgw_secret",
            "ENGINE_INSTANCE_EVENTS_SECRET_FILE": "engine_instance_events_secret",
            "PUBGW_ENGINE_ADMIN_PASSWORD_FILE": "pubgw_engine_admin_password",
        }
        lines += [f"{key}={secret_dir / value}" for key, value in env_keys.items()]
        self._atomic_write(release / ".env", "\n".join(lines) + "\n")
        deployment_integrity = {
            "schemaVersion": 1,
            "manifestSha256": manifest.sha256,
            "architecture": architecture,
            "bundleFile": ".release-bundle.tar.gz",
            "generatedFiles": {
                ".env": _sha256(release / ".env"),
                "engines.json": _sha256(release / "engines.json"),
            },
        }
        self._atomic_write(release / "deployment-integrity.json", json.dumps(deployment_integrity, indent=2, sort_keys=True) + "\n")

    def _ensure_admin_password_hash(self, caddy_image: str) -> None:
        secret_dir, hash_path = self._managed("shared/secrets", must_exist=True), self._managed("shared/secrets/engine_admin_password_hash")
        if hash_path.exists():
            _regular_file(hash_path, "administrator password hash")
            return
        password_path = secret_dir / "engine_admin_password"
        _regular_file(password_path, "administrator password")
        result = self._run(
            ["docker", "run", "--rm", "-i", caddy_image, "caddy", "hash-password"],
            "could not generate the edge administrator password hash",
            input=password_path.read_text(encoding="utf-8").strip() + "\n", capture_output=True,
        )
        password_hash = result.stdout.strip()
        if re.fullmatch(r"\$2[aby]\$\d{2}\$.{53}", password_hash) is None:
            raise IntegrityError("Caddy returned an invalid administrator password hash")
        self._atomic_write(hash_path, password_hash + "\n")

    def install(self, manifest_path, public_host, admin_user, admin_email, expected_sha256=None, bundle_path=None, asset_loader=None) -> None:
        manifest = load_manifest(manifest_path, expected_sha256)
        self._validate_install_inputs(public_host, admin_user, admin_email)
        self._assert_target_available()
        if self.state_path.exists() and self._read_state().get("status") not in {"install_failed", "uninstalled"}:
            raise InputError("an installation already exists; use upgrade instead")
        values = self.preflight(manifest, public_host)
        self._mkdir(".")
        self._stage_release(manifest, values["architecture"], public_host, admin_user, admin_email, bundle_path, asset_loader)
        self._write_state(
            status="installing",
            version=manifest.version,
            databaseSchema=manifest.database_schema,
            publicHost=public_host,
            adminUser=admin_user,
            adminEmail=admin_email,
            expectedPublicAddresses=self.expected_public_addresses,
        )
        compose = self._compose(manifest.version)
        try:
            self._run(compose + ["pull"], "image pull failed")
            self._ensure_admin_password_hash(manifest.raw["images"]["CADDY_IMAGE"])
            self._run(compose + ["up", "-d", "--remove-orphans"], "installation start failed")
            self._assert_healthy(compose, "installation health check failed")
            self._verify_public_tls(public_host)
        except SurveyctlError:
            self._write_state(status="install_failed")
            raise
        self._atomic_write(self.current_path, manifest.version + "\n")
        self._write_state(status="healthy")

    def _download_manifest(self, version: str, expected_sha256: str | None) -> Path:
        version = _validate_semver(version.removeprefix("v"), "release version")
        if not expected_sha256:
            raise InputError("remote release operation requires --manifest-sha256")
        path = self._write_download(f"release-{version}.json", self.release_client.fetch_manifest(version))
        manifest = load_manifest(path, expected_sha256)
        if manifest.version != version:
            raise IntegrityError("requested version does not match downloaded manifest")
        return path

    def install_version(self, version, expected_sha256, public_host, admin_user, admin_email) -> None:
        self._validate_install_inputs(public_host, admin_user, admin_email)
        self._assert_target_available()
        self._mkdir(".")
        path = self._download_manifest(version, expected_sha256)
        manifest = load_manifest(path, expected_sha256)
        self.install(path, public_host, admin_user, admin_email, expected_sha256, asset_loader=lambda name: self.release_client.fetch_asset(manifest.version, name))

    def install_offline(self, package, public_host, admin_user, admin_email, expected_sha256=None) -> None:
        self._validate_install_inputs(public_host, admin_user, admin_email)
        package_path = Path(package)
        _regular_file(package_path, "offline package")
        self._assert_target_available()
        self._mkdir(".")
        self._mkdir(".surveyctl/downloads")
        destination = self._managed(Path(".surveyctl/downloads") / f"offline-{uuid.uuid4().hex}")
        _extract_safe_tar(package_path, destination)
        manifest_path = destination / "release.json"
        manifest = load_manifest(manifest_path, expected_sha256)
        try:
            values = self.host_probe.inspect(public_host, self.target)
        except TypeError:
            values = self.host_probe.inspect(public_host)
        asset = manifest.bundle_for(values["architecture"])
        self.install(manifest_path, public_host, admin_user, admin_email, expected_sha256, destination / asset["name"])

    def _backup_path(self, value) -> Path:
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts or len(relative.parts) != 1 or re.fullmatch(r"[A-Za-z0-9._-]{1,128}", relative.name) is None:
            raise InputError("backup name must be one safe relative path component")
        return self._managed(Path("backups") / relative.name)

    def _verify_backup(
        self,
        backup: Path,
        expected_version: str | None = None,
        expected_database_schema: str | None = None,
    ) -> dict[str, Any]:
        self._reject_symlink_components(backup)
        if backup.is_symlink() or not backup.is_dir():
            raise IntegrityError("backup artifact must be a real directory")
        manifest_path = backup / "manifest.json"
        _regular_file(manifest_path, "backup manifest")
        try:
            metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IntegrityError("backup manifest is invalid") from exc
        required = {"schemaVersion", "version", "databaseSchema", "releaseManifestSha256", "images", "files"}
        if not isinstance(metadata, dict) or set(metadata) != required or metadata.get("schemaVersion") != 2:
            raise IntegrityError("backup metadata shape is invalid")
        try:
            _validate_semver(metadata["version"], "backup version")
        except InputError as exc:
            raise IntegrityError("backup version is invalid") from exc
        if expected_version and metadata["version"] != expected_version:
            raise IntegrityError("backup version does not match installed version")
        if not isinstance(metadata["databaseSchema"], str) or re.fullmatch(SCHEMA_PATTERN, metadata["databaseSchema"]) is None:
            raise IntegrityError("backup database schema is invalid")
        if expected_database_schema is not None and metadata["databaseSchema"] != expected_database_schema:
            raise IntegrityError("backup database schema does not match installed version")
        files = metadata["files"]
        if not isinstance(files, dict) or not files:
            raise IntegrityError("backup file inventory is empty")
        declared, allowed_directories = set(), set()
        for name, expected in files.items():
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts or re.fullmatch(r"[0-9a-f]{64}", str(expected)) is None:
                raise IntegrityError("backup inventory contains an unsafe entry")
            declared.add(pure.as_posix())
            allowed_directories.update(PurePosixPath(*pure.parts[:index]).as_posix() for index in range(1, len(pure.parts)))
        actual = set()
        for root, directories, filenames in os.walk(backup, followlinks=False):
            root_path = Path(root)
            for name in directories + filenames:
                path = root_path / name
                relative = path.relative_to(backup).as_posix()
                try:
                    mode = path.lstat().st_mode
                except OSError as exc:
                    raise IntegrityError("backup artifact cannot be inspected") from exc
                if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                    raise IntegrityError(f"backup contains an unsafe filesystem entry: {relative}")
                if stat.S_ISDIR(mode):
                    if relative not in allowed_directories:
                        raise IntegrityError(f"backup contains an unlisted directory: {relative}")
                elif path != manifest_path and relative not in {"SHA256SUMS", "CONTROL-HMAC"}:
                    actual.add(relative)
        if actual != declared:
            raise IntegrityError("backup file inventory does not match artifact")
        for name, expected in files.items():
            pure = PurePosixPath(name)
            path = backup.joinpath(*pure.parts)
            _regular_file(path, "backup payload")
            self._reject_symlink_components(path)
            if not secrets.compare_digest(_sha256(path), expected):
                raise IntegrityError(f"backup checksum mismatch: {name}")
        for name in ("SHA256SUMS", "CONTROL-HMAC"):
            path = backup / name
            _regular_file(path, f"backup {name}")
            self._reject_symlink_components(path)
        state = self._read_state(require_current=True)
        helper = self._release_dir(state["version"]) / "restore.py"
        self._run_helper(
            [sys.executable, str(helper), "verify", "--target", str(self.target), "--backup", str(backup)],
            "backup restore-grade verification failed",
        )
        return metadata

    def backup(self, name) -> Path:
        state = self._read_state(require_current=True)
        output = self._backup_path(name)
        if output.exists() or output.is_symlink():
            raise InputError("backup destination already exists")
        self._mkdir("backups")
        helper = self._release_dir(state["version"]) / "backup.py"
        self._run_helper([sys.executable, str(helper), "backup", "--target", str(self.target), "--output", str(output), "--version", state["version"]], "backup failed")
        self._verify_backup(output, state["version"], state["databaseSchema"])
        return output

    def restore(self, name) -> None:
        state = self._read_state(require_current=True)
        backup = self._backup_path(name)
        metadata = self._verify_backup(backup)
        release = self._release_manifest(state["version"])
        compatible_schemas = set(release.compatible_source_schemas) | {release.database_schema}
        if metadata["databaseSchema"] not in compatible_schemas:
            raise IntegrityError("backup database schema is incompatible with the installed release")
        helper = self._release_dir(state["version"]) / "restore.py"
        self._run_helper([sys.executable, str(helper), "restore", "--target", str(self.target), "--backup", str(backup)], "restore failed")

    @staticmethod
    def _jwt(secret: bytes, actor: str, tenant: str, roles: list[str]) -> str:
        encode = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        now = int(time.time())
        header = encode(b'{"alg":"HS256","typ":"JWT"}')
        claims = encode(json.dumps({
            "sub": actor, "tenant_id": tenant, "roles": roles, "iat": now, "exp": now + 300,
        }, separators=(",", ":")).encode("utf-8"))
        signed = f"{header}.{claims}".encode("ascii")
        return signed.decode("ascii") + "." + encode(hmac.new(secret, signed, hashlib.sha256).digest())

    def setup_probe(self, api_call=None) -> None:
        state = self._read_state(require_current=True)
        host = state["publicHost"]
        jwt_path = self._managed("shared/secrets/platform_jwt_hmac_secret", must_exist=True)
        event_path = self._managed("shared/secrets/engine_instance_events_secret", must_exist=True)
        jwt_secret = jwt_path.read_bytes().strip()
        if jwt_path.is_symlink() or event_path.is_symlink() or len(jwt_secret) < 32:
            raise IntegrityError("probe setup secrets are unavailable or unsafe")
        token = self._jwt(jwt_secret, "production-probe-operator", str(uuid.uuid4()), ["platform_operator"])

        def request(call_token, method, path, body=None):
            payload = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers = {"Authorization": "Bearer " + call_token, "Accept": "application/json"}
            if payload is not None:
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(f"https://{host}{path}", data=payload, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=60, context=ssl.create_default_context()) as response:
                    data = response.read(1024 * 1024)
                    return response.status, json.loads(data) if data else None
            except (OSError, urllib.error.URLError, ValueError) as exc:
                raise RuntimeHealthError("probe setup API request failed") from exc

        call = api_call or request
        suffix = uuid.uuid4().hex[:12]

        def expect(method, path, body, status):
            actual, value = call(token, method, path, body)
            if actual != status or not isinstance(value, dict):
                raise RuntimeHealthError("probe setup API rejected a provisioning step")
            return value

        tenant = expect("POST", "/v1/platform/tenants", {"code": "production-probe-" + suffix,
                        "name": "Production probe"}, 201)
        tenant_id = tenant.get("id")
        try:
            uuid.UUID(tenant_id)
        except (ValueError, TypeError) as exc:
            raise RuntimeHealthError("probe setup returned an invalid tenant identity") from exc
        plan = expect("POST", "/v1/platform/plans", {
            "planCode": "production-probe-" + suffix,
            "capabilities": ["survey.read", "survey.write", "response.collect", "response.export"],
            "quotas": {"member.seats": 1, "response.valid_completed": 100}, "exportWindowDays": 1,
        }, 201)
        owner = "production-probe-owner"
        expect("POST", f"/v1/platform/tenants/{tenant_id}/onboarding", {
            "planVersionId": plan.get("id"), "kind": "TRIAL", "days": 3650, "ownerActorId": owner,
        }, 200)
        expect("POST", f"/v1/platform/tenants/{tenant_id}/status", {"status": "active"}, 200)
        expect("POST", f"/v1/platform/tenants/{tenant_id}/engine-instances", {
            "id": "production-engine-01", "baseUrl": "http://engine",
        }, 201)
        issued = expect("POST", "/v1/platform/engine-instances/production-engine-01/event-secret", None, 200)
        expected_secret = event_path.read_text(encoding="ascii").strip()
        if not isinstance(issued.get("secret"), str) or not secrets.compare_digest(issued["secret"], expected_secret):
            raise IntegrityError("issued engine event secret does not match the configured engine")
        self._atomic_write(
            self._managed("shared/product-probe.json"),
            json.dumps({"tenantId": tenant_id, "actorId": owner}, sort_keys=True) + "\n",
        )

    def upgrade(self, manifest_path, expected_sha256=None, bundle_path=None, asset_loader=None) -> None:
        state = self._read_state(require_current=True)
        previous, manifest = state["version"], load_manifest(manifest_path, expected_sha256)
        if _version_key(manifest.version) <= _version_key(previous):
            raise InputError("upgrade target must be newer than the installed version")
        if _version_key(previous) < _version_key(manifest.raw["minimumSourceVersion"]):
            raise InputError("installed version is below the release minimum source version")
        values = self.preflight(
            manifest,
            state["publicHost"],
            require_ports=False,
            expected_addresses=state.get("expectedPublicAddresses"),
        )
        self._stage_release(manifest, values["architecture"], state["publicHost"], state["adminUser"], state["adminEmail"], bundle_path, asset_loader)
        backup_name = f"pre-upgrade-{previous}-to-{manifest.version}"
        backup = self.backup(backup_name)
        self._verify_backup(backup, previous, state["databaseSchema"])
        compose = self._compose(manifest.version)
        try:
            self._write_state(status="upgrading", previousVersion=previous, recoveryBackup=backup_name)
            self._run(compose + ["pull"], "upgrade image pull failed")
            self._run(compose + ["up", "-d", "--remove-orphans"], "upgrade migration or start failed")
            self._assert_healthy(compose, "upgrade health check failed")
            self._verify_public_tls(state["publicHost"])
            self._atomic_write(self.current_path, manifest.version + "\n")
            self._write_state(status="healthy", version=manifest.version, databaseSchema=manifest.database_schema, previousVersion=previous)
        except SurveyctlError as failure:
            self._recover_failed_upgrade(manifest, previous, backup_name, failure)

    def _recover_failed_upgrade(self, manifest, previous, backup_name, failure) -> None:
        new_compose = self._compose(manifest.version)
        if manifest.rollback == "compatible":
            try:
                old_compose = self._compose(previous)
                self._run(old_compose + ["up", "-d", "--remove-orphans"], "compatible rollback start failed")
                self._assert_healthy(old_compose, "compatible rollback health check failed")
                self._verify_public_tls(self._read_state()["publicHost"])
                self._atomic_write(self.current_path, previous + "\n")
                self._write_state(status="upgrade_failed", version=previous)
            except SurveyctlError as recovery_error:
                self._stop_after_failed_recovery(new_compose, previous, backup_name, recovery_error)
            raise failure
        if manifest.rollback == "restore-only":
            try:
                self.restore(backup_name)
                old_compose = self._compose(previous)
                self._run(old_compose + ["up", "-d", "--remove-orphans"], "restored rollback start failed")
                self._assert_healthy(old_compose, "restored rollback health check failed")
                self._verify_public_tls(self._read_state()["publicHost"])
                self._atomic_write(self.current_path, previous + "\n")
                self._write_state(status="restored_after_failed_upgrade", version=previous)
            except SurveyctlError as recovery_error:
                self._stop_after_failed_recovery(new_compose, previous, backup_name, recovery_error)
            raise failure
        self._stop_after_failed_recovery(new_compose, previous, backup_name, failure)

    def _stop_after_failed_recovery(self, compose, previous, backup_name, cause) -> None:
        try:
            self.runner.run(compose + ["stop"], cwd=self.target)
        except (OSError, subprocess.CalledProcessError):
            pass
        self._atomic_write(self.current_path, previous + "\n")
        self._write_state(status="recovery_required", version=previous, recoveryBackup=backup_name)
        raise RuntimeHealthError(f"upgrade recovery failed; services stopped; verified backup retained as {backup_name}") from cause

    def upgrade_version(self, version, expected_sha256) -> None:
        path = self._download_manifest(version, expected_sha256)
        manifest = load_manifest(path, expected_sha256)
        self.upgrade(path, expected_sha256, asset_loader=lambda name: self.release_client.fetch_asset(manifest.version, name))

    def rollback(self, version=None) -> None:
        state = self._read_state(require_current=True)
        target_version = version or state.get("previousVersion")
        if target_version is None:
            raise InputError("no rollback version is recorded")
        _validate_semver(target_version, "rollback version")
        manifest, current_manifest = self._release_manifest(target_version), self._release_manifest(state["version"])
        if current_manifest.rollback != "compatible" or state["databaseSchema"] not in manifest.compatible_source_schemas:
            raise InputError("database compatibility requires restore from backup; image rollback is blocked")
        compose = self._compose(target_version)
        self._run(compose + ["up", "-d", "--remove-orphans"], "rollback start failed")
        self._assert_healthy(compose, "rollback health check failed")
        self._verify_public_tls(state["publicHost"])
        previous = state["version"]
        self._atomic_write(self.current_path, target_version + "\n")
        self._write_state(status="healthy", version=target_version, databaseSchema=manifest.database_schema, previousVersion=previous)

    def status(self) -> None:
        state = self._read_state(require_current=True)
        self._assert_healthy(self._compose(state["version"]), "status check failed")

    def _redact(self, text: str) -> str:
        redacted, secret_dir = text, self._managed("shared/secrets")
        if secret_dir.is_dir() and not secret_dir.is_symlink():
            for path in secret_dir.iterdir():
                if path.is_file() and not path.is_symlink():
                    try:
                        value = path.read_text(encoding="utf-8").strip()
                    except OSError:
                        continue
                    if value:
                        redacted = redacted.replace(value, "[REDACTED]")
        return re.sub(r"(?i)(password|secret|token|private[_-]?key)(\s*[:=]\s*)\S+", r"\1\2[REDACTED]", redacted)

    def doctor(self) -> None:
        state = self._read_state(require_current=True)
        helper = self._release_dir(state["version"]) / "doctor.py"
        result = self._run([sys.executable, str(helper), "--target", str(self.target), "--redact"], "doctor checks failed", capture_output=True)
        if result.stdout:
            print(self._redact(result.stdout), end="")
        if result.stderr:
            print(self._redact(result.stderr), end="", file=sys.stderr)

    def logs(self, service=None) -> None:
        state = self._read_state(require_current=True)
        if service and (re.fullmatch(r"[a-z0-9-]+", service) is None or service not in LONG_RUNNING_SERVICES | ONE_SHOT_SERVICES):
            raise InputError("unknown service name")
        command = self._compose(state["version"]) + ["logs", "--no-color"]
        if service:
            command.append(service)
        self._run(command, "log collection failed")

    def uninstall(self, purge_data, confirmation) -> None:
        state = self._read_state(require_current=True)
        command = self._compose(state["version"]) + ["down", "--remove-orphans"]
        purge_paths = [self.target / "shared", self.target / "backups"]
        if purge_data:
            required = f"PURGE {self.target}"
            if confirmation != required:
                raise InputError(f"data purge requires exact confirmation: {required}")
            for path in purge_paths:
                if path.is_symlink():
                    self._write_state(status="purge_failed")
                    raise RuntimeHealthError(f"purge refused symbolic link: {path.relative_to(self.target)}")
                self._reject_symlink_components(path)
            command.append("--volumes")
            self._write_state(status="purging")
        try:
            self._run(command, "uninstall failed")
        except SurveyctlError:
            if purge_data:
                self._write_state(status="purge_failed")
            raise
        if purge_data:
            try:
                for path in purge_paths:
                    if path.exists():
                        shutil.rmtree(path)
                remaining = [path for path in purge_paths if path.exists() or path.is_symlink()]
                if remaining:
                    raise OSError("managed data remains")
            except OSError as exc:
                self._write_state(status="purge_failed")
                remaining = [str(path.relative_to(self.target)) for path in purge_paths if path.exists() or path.is_symlink()]
                raise RuntimeHealthError(f"purge failed; managed data remains: {', '.join(remaining)}") from exc
        self._write_state(status="uninstalled")


def _parse_tool_version(value: Any) -> tuple[int, int, int]:
    if not isinstance(value, str):
        return 0, 0, 0
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", value)
    return (0, 0, 0) if not match else (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="surveyctl", description="Manage a single-host Survey production installation")
    parser.add_argument("--target", default="/opt/survey", help="installation directory (default: /opt/survey)")
    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser("install")
    source = install.add_mutually_exclusive_group(required=True)
    source.add_argument("--manifest")
    source.add_argument("--version")
    source.add_argument("--offline")
    install.add_argument("--manifest-sha256")
    install.add_argument("--public-host", required=True)
    install.add_argument("--admin-user", required=True)
    install.add_argument("--admin-email", required=True)
    install.add_argument(
        "--expected-public-address",
        action="append",
        default=[],
        help="public IP expected for --public-host (repeatable; required behind NAT)",
    )
    sub.add_parser("setup-probe")
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
    try:
        manager_options = {}
        if getattr(args, "expected_public_address", None):
            manager_options["expected_public_addresses"] = args.expected_public_address
        manager = SurveyManager(args.target, **manager_options)
        if args.command == "install":
            if args.version:
                manager.install_version(args.version, args.manifest_sha256, args.public_host, args.admin_user, args.admin_email)
            elif args.offline:
                manager.install_offline(args.offline, args.public_host, args.admin_user, args.admin_email, args.manifest_sha256)
            else:
                manager.install(args.manifest, args.public_host, args.admin_user, args.admin_email, args.manifest_sha256)
        elif args.command == "setup-probe":
            manager.setup_probe()
        elif args.command == "upgrade":
            manager.upgrade_version(args.version, args.manifest_sha256) if args.version else manager.upgrade(args.manifest, args.manifest_sha256)
        elif args.command == "rollback":
            manager.rollback(args.version)
        elif args.command == "backup":
            manager.backup(args.output or f"manual-{uuid.uuid4().hex[:12]}")
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
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"surveyctl: environment operation failed: {type(exc).__name__}", file=sys.stderr)
        return EXIT_ENVIRONMENT
    except Exception as exc:
        print(f"surveyctl: internal runtime failure: {type(exc).__name__}", file=sys.stderr)
        return EXIT_RUNTIME


if __name__ == "__main__":
    raise SystemExit(main())
