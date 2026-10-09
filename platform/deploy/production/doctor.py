#!/usr/bin/env python3
"""Redacted diagnostics and clean-host acceptance orchestration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import subprocess
import sys
from typing import Callable
import urllib.error
import urllib.request


LONG_RUNNING = {"edge", "admin-web", "platform", "publish-gateway", "engine", "platform-db", "engine-db"}
EXPECTED_VOLUMES = {
    "caddy-data", "caddy-config", "platform-db-data", "platform-assets", "platform-exports",
    "publish-gateway-state", "engine-db-data", "engine-upload", "engine-runtime",
}


class DoctorError(Exception):
    pass


class DoctorRuntimeError(DoctorError):
    pass


class CommandRunner:
    def run(self, command, **kwargs):
        kwargs.setdefault("check", True)
        kwargs.setdefault("text", True)
        return subprocess.run(command, **kwargs)


class CleanHostScenario:
    STEPS = (
        "install", "doctor-before", "minimal-journey", "backup", "upgrade",
        "doctor-after", "restore-second-project", "uninstall",
    )

    def run(self, execute: Callable[[str], None]):
        for step in self.STEPS:
            execute(step)


class Doctor:
    def __init__(self, target, runner=None, tls_probe=None):
        self.target = Path(os.path.abspath(os.fspath(Path(target).expanduser())))
        current = Path(self.target.anchor)
        for component in self.target.parts[1:]:
            current = current / component
            if current.is_symlink():
                raise DoctorRuntimeError("target path contains a symbolic link")
            if not current.exists():
                break
        self.runner = runner or CommandRunner()
        self.tls_probe = tls_probe or self._tls_probe
        self._secrets = self._load_secrets()

    def _load_secrets(self):
        values = []
        directory = self.target / "shared" / "secrets"
        if directory.is_dir() and not directory.is_symlink():
            for path in directory.iterdir():
                if path.is_file() and not path.is_symlink():
                    try:
                        value = path.read_text(encoding="utf-8").strip()
                    except OSError:
                        continue
                    if value:
                        values.append(value)
        return values

    def _redact(self, value):
        if isinstance(value, dict):
            return {key: self._redact(child) for key, child in value.items()}
        if isinstance(value, list):
            return [self._redact(child) for child in value]
        if not isinstance(value, str):
            return value
        redacted = value
        for secret in self._secrets:
            redacted = redacted.replace(secret, "[REDACTED]")
        redacted = redacted.replace(str(self.target / "shared" / "secrets"), "[SECRET_DIR]")
        return re.sub(r"(?i)(password|secret|token|private[_-]?key)(\s*[:=]\s*)\S+", r"\1\2[REDACTED]", redacted)

    def _load_installation(self):
        current = self.target / "current"
        if current.is_symlink():
            raise DoctorRuntimeError("current version pointer is unsafe")
        state_path = self.target / ".surveyctl" / "state.json"
        if state_path.is_symlink():
            raise DoctorRuntimeError("installation state path is unsafe")
        try:
            version = current.read_text(encoding="ascii").strip()
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DoctorRuntimeError("installation metadata is unavailable") from exc
        if re.fullmatch(r"\d+\.\d+\.\d+(?:-rc\.\d+)?", version) is None:
            raise DoctorRuntimeError("current version pointer is invalid")
        release_dir = self.target / "releases" / version
        for name in ("release.json", "compose.yml", ".env"):
            path = release_dir / name
            if path.is_symlink() or not path.is_file():
                raise DoctorRuntimeError(f"installation {name} is unavailable or unsafe")
        try:
            manifest = json.loads((release_dir / "release.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DoctorRuntimeError("installation metadata is unavailable") from exc
        if release_dir.is_symlink() or not release_dir.is_dir() or state.get("version") != version or manifest.get("version") != version:
            raise DoctorRuntimeError("installation metadata is inconsistent")
        host = state.get("publicHost")
        schema = state.get("databaseSchema")
        if not isinstance(host, str) or not isinstance(schema, str) or re.fullmatch(r"[A-Za-z0-9._-]{1,64}", schema) is None:
            raise DoctorRuntimeError("installation state is incomplete")
        compose = ["docker", "compose", "-f", str(release_dir / "compose.yml"), "--env-file", str(release_dir / ".env"), "-p", "survey-production"]
        return version, schema, host, compose

    def _command(self, command):
        try:
            return self.runner.run(command, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise DoctorRuntimeError("diagnostic command failed") from exc

    def _tls_probe(self, host):
        request = urllib.request.Request(f"https://{host}/.well-known/survey-health", headers={"User-Agent": "surveyctl-doctor/1"})
        try:
            with urllib.request.urlopen(request, timeout=10, context=ssl.create_default_context()) as response:
                body = response.read(64).decode("ascii", errors="replace").strip()
                marker = response.headers.get("X-Survey-Deployment")
                return response.status == 200 and marker == "survey-production-v1" and body == marker
        except (OSError, urllib.error.URLError, ssl.SSLError):
            return False

    @staticmethod
    def _check(identifier, status, summary):
        return {"id": identifier, "status": status, "summary": summary}

    def _config_checks(self, compose):
        result = self._command(compose + ["config", "--format", "json"])
        try:
            config = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise DoctorRuntimeError("rendered Compose configuration is invalid") from exc
        services = config.get("services", {})
        exposed = {}
        for name, service in services.items():
            ports = service.get("ports", []) if isinstance(service, dict) else []
            if ports:
                exposed[name] = sorted(str(item.get("published")) for item in ports if isinstance(item, dict))
        ports_ok = exposed == {"edge": ["443", "80"]}
        internal = config.get("networks", {}).get("internal", {}).get("internal") is True
        db_internal = all(not services.get(name, {}).get("ports") and "internal" in services.get(name, {}).get("networks", []) for name in ("platform-db", "engine-db"))
        return (
            self._check("ports", "ok" if ports_ok else "blocked", "only the edge service publishes ports 80 and 443" if ports_ok else "unexpected host port exposure detected"),
            self._check("internal-exposure", "ok" if internal and db_internal else "blocked", "database services are isolated on an internal network" if internal and db_internal else "internal services are externally reachable"),
        )

    def _container_check(self, compose):
        result = self._command(compose + ["ps", "--all", "--format", "json"])
        try:
            text = (result.stdout or "").strip()
            payload = json.loads(text) if text.startswith("[") else [json.loads(line) for line in text.splitlines() if line]
            rows = payload if isinstance(payload, list) else [payload]
        except (AttributeError, json.JSONDecodeError) as exc:
            raise DoctorRuntimeError("container health output is invalid") from exc
        found = {row.get("Service"): row for row in rows if isinstance(row, dict)}
        bad = sorted(name for name in LONG_RUNNING if not found.get(name) or found[name].get("State") != "running" or found[name].get("Health") != "healthy")
        init = found.get("engine-init")
        raw_exit = init.get("ExitCode", -1) if init else -1
        valid_exit = not isinstance(raw_exit, bool) and (
            isinstance(raw_exit, int) or (isinstance(raw_exit, str) and re.fullmatch(r"\d+", raw_exit) is not None)
        )
        if not init or init.get("State") != "exited" or not valid_exit or int(raw_exit) != 0:
            bad.append("engine-init")
        return self._check("containers", "ok" if not bad else "blocked", "all production containers are healthy" if not bad else f"unhealthy services: {', '.join(bad)}")

    def _migration_check(self, compose, expected):
        pg = self._command(compose + ["exec", "-T", "platform-db", "psql", "-U", "postgres", "-d", "platform", "-Atqc", "SELECT version FROM flyway_schema_history WHERE success ORDER BY installed_rank DESC LIMIT 1"])
        engine = self._command(compose + ["exec", "-T", "engine", "sh", "-c", "php application/commands/console.php productionInit status"])
        actual = (pg.stdout or "").strip()
        engine_ok = "completed" in (engine.stdout or "").lower() or (engine.stdout or "").strip() == "0"
        ok = actual == expected and engine_ok
        return self._check("migrations", "ok" if ok else "blocked", "database migrations match the installed release" if ok else "database migration state does not match the installed release")

    def _volume_check(self):
        result = self._command(["docker", "volume", "inspect", *[f"survey-production_{name}" for name in sorted(EXPECTED_VOLUMES)]])
        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise DoctorRuntimeError("volume inspection output is invalid") from exc
        names = {item.get("Name") for item in payload if isinstance(item, dict)} if isinstance(payload, list) else set()
        expected = {f"survey-production_{name}" for name in EXPECTED_VOLUMES}
        ok = names == expected
        return self._check("volumes", "ok" if ok else "blocked", "all managed volumes are inspectable" if ok else "managed volume inspection failed")

    def _backup_check(self):
        root = self.target / "backups"
        if not root.exists():
            return self._check("backups", "warning", "no completed backup exists yet")
        valid = 0
        for directory in root.iterdir():
            manifest = directory / "manifest.json"
            if directory.is_symlink() or not directory.is_dir() or not manifest.is_file() or manifest.is_symlink():
                continue
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                files = data["files"]
                if files and all((directory / name).is_file() and self._sha256(directory / name) == digest for name, digest in files.items()):
                    valid += 1
            except (OSError, KeyError, TypeError, json.JSONDecodeError):
                continue
        return self._check("backups", "ok" if valid else "warning", f"{valid} verified backup(s) available" if valid else "no readable verified backup exists")

    @staticmethod
    def _sha256(path):
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _probe_check(self, compose):
        commands = [
            compose + ["exec", "-T", "platform", "sh", "-c", "echo probe && wget -q -O /dev/null http://127.0.0.1:8080/actuator/health"],
            compose + ["exec", "-T", "engine", "sh", "-c", "echo probe && php application/commands/console.php productionInit status >/dev/null"],
        ]
        try:
            for command in commands:
                self._command(command)
            return self._check("minimal-probe", "ok", "read-only application probes succeeded")
        except DoctorRuntimeError:
            return self._check("minimal-probe", "blocked", "read-only application probe failed")

    def run(self):
        version, schema, host, compose = self._load_installation()
        checks = [*self._config_checks(compose)]
        tls_ok = self.tls_probe(host)
        checks.extend([
            self._check("tls", "ok" if tls_ok else "blocked", "public TLS deployment marker verified" if tls_ok else "public TLS deployment marker failed"),
            self._container_check(compose),
            self._migration_check(compose, schema),
            self._volume_check(),
            self._backup_check(),
            self._probe_check(compose),
        ])
        status = "blocked" if any(item["status"] == "blocked" for item in checks) else "warning" if any(item["status"] == "warning" for item in checks) else "ok"
        return self._redact({"schemaVersion": 1, "status": status, "version": version, "checks": checks})


def build_parser():
    parser = argparse.ArgumentParser(description="Run redacted Survey production diagnostics")
    parser.add_argument("--target", required=True)
    parser.add_argument("--redact", action="store_true", help="retained for the surveyctl helper contract; output is always redacted")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        report = Doctor(args.target).run()
        print(json.dumps(report, sort_keys=True, ensure_ascii=True))
        return 4 if report["status"] == "blocked" else 0
    except DoctorError as exc:
        print(json.dumps({"schemaVersion": 1, "status": "blocked", "error": str(exc)}, sort_keys=True))
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
