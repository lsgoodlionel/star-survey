#!/usr/bin/env python3
"""Redacted diagnostics and clean-host acceptance orchestration."""

from __future__ import annotations

import argparse
import base64
import http.cookiejar
import hashlib
import hmac
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import tempfile
import time
from typing import Callable
import urllib.error
import urllib.parse
import urllib.request
import uuid

from restore import RestoreIntegrityError, RestoreManager


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


class _SurveyFormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_form = False
        self.action = ""
        self.fields = {}
        self.moves = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "form" and values.get("id") == "limesurvey":
            self.in_form = True
            self.action = values.get("action", "")
        if not self.in_form:
            return
        name = values.get("name")
        if tag == "input" and name:
            kind = values.get("type", "text").lower()
            if kind == "hidden":
                self.fields[name] = values.get("value", "")
            elif kind == "text" and not re.search(r"(other|comment)$", name, re.I):
                self.fields[name] = "production acceptance"
            elif kind in {"radio", "checkbox"} and name not in self.fields:
                self.fields[name] = values.get("value") or "Y"
        elif tag == "button" and name == "move":
            self.moves.append(values.get("value", ""))

    def handle_endtag(self, tag):
        if tag == "form" and self.in_form:
            self.in_form = False


class ProductProbe:
    """One disposable production journey using only short-lived credentials."""

    def __init__(self, target, host, compose, runner, now=time.time, sleep=time.sleep):
        self.target = Path(target)
        self.host = host
        self.compose = compose
        self.runner = runner
        self.now = now
        self.sleep = sleep
        self.base_url = f"https://{host}"

    @staticmethod
    def _b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    def _configuration(self):
        path = self.target / "shared" / "product-probe.json"
        if path.is_symlink() or not path.is_file():
            raise DoctorRuntimeError("product probe identity is not configured")
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DoctorRuntimeError("product probe identity is invalid") from exc
        if not isinstance(config, dict) or set(config) != {"tenantId", "actorId"}:
            raise DoctorRuntimeError("product probe identity is invalid")
        try:
            uuid.UUID(config["tenantId"])
        except (ValueError, TypeError, KeyError) as exc:
            raise DoctorRuntimeError("product probe tenant is invalid") from exc
        actor = config.get("actorId")
        if not isinstance(actor, str) or re.fullmatch(r"[A-Za-z0-9@._:-]{1,128}", actor) is None:
            raise DoctorRuntimeError("product probe actor is invalid")
        return config

    def _token(self, config):
        path = self.target / "shared" / "secrets" / "platform_jwt_hmac_secret"
        try:
            secret = path.read_bytes().strip()
        except OSError as exc:
            raise DoctorRuntimeError("product probe signing key is unavailable") from exc
        if path.is_symlink() or len(secret) < 32:
            raise DoctorRuntimeError("product probe signing key is invalid")
        now = int(self.now())
        header = self._b64(b'{"alg":"HS256","typ":"JWT"}')
        claims = self._b64(json.dumps({
            "sub": config["actorId"], "tenant_id": config["tenantId"], "roles": [],
            "iat": now, "exp": now + 300,
        }, separators=(",", ":")).encode("utf-8"))
        signed = f"{header}.{claims}".encode("ascii")
        signature = self._b64(hmac.new(secret, signed, hashlib.sha256).digest())
        return signed.decode("ascii") + "." + signature

    def _api(self, token, method, path, body=None, raw=False):
        payload = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60, context=ssl.create_default_context()) as response:
                data = response.read(16 * 1024 * 1024)
                if raw:
                    return response.status, data
                return response.status, json.loads(data) if data else None
        except (OSError, urllib.error.URLError, ValueError) as exc:
            raise DoctorRuntimeError("product probe API request failed") from exc

    @staticmethod
    def _expect(status, expected, value, label):
        if status != expected or not isinstance(value, dict):
            raise DoctorRuntimeError(f"product probe {label} failed")
        return value

    def _answer(self, sid):
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        url = f"{self.base_url}/survey/index.php/{sid}?newtest=Y&lang=en"
        try:
            for _ in range(8):
                with opener.open(urllib.request.Request(url, headers={"User-Agent": "survey-product-probe/1"}), timeout=60) as response:
                    html = response.read(4 * 1024 * 1024).decode("utf-8", "replace")
                    url = response.geturl()
                if "completed-wrapper" in html:
                    return
                parser = _SurveyFormParser()
                parser.feed(html)
                move = "movesubmit" if "movesubmit" in parser.moves else "movenext" if "movenext" in parser.moves else None
                if not parser.action or move is None:
                    raise DoctorRuntimeError("product probe public survey form is invalid")
                data = urllib.parse.urlencode(dict(parser.fields, move=move)).encode("utf-8")
                url = urllib.parse.urljoin(url, parser.action)
                request = urllib.request.Request(url, data=data, headers={
                    "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "survey-product-probe/1",
                })
                with opener.open(request, timeout=60) as response:
                    html = response.read(4 * 1024 * 1024).decode("utf-8", "replace")
                    url = response.geturl()
                if "completed-wrapper" in html:
                    return
        except (OSError, urllib.error.URLError) as exc:
            raise DoctorRuntimeError("product probe public answer failed") from exc
        raise DoctorRuntimeError("product probe public answer did not complete")

    def _close_engine_survey(self, instance, sid):
        body = json.dumps({"requestId": str(uuid.uuid4()), "engineInstanceId": instance, "surveyId": sid},
                          separators=(",", ":"))
        script = (
            "import hashlib,hmac,os,sys,time,urllib.request;"
            "b=sys.argv[1].encode();t=str(int(time.time()));"
            "s=open('/run/secrets/platform_pubgw_secret','rb').read().strip();"
            "q=urllib.request.Request('http://127.0.0.1:8080/v1/close',data=b,method='POST',headers={"
            "'Content-Type':'application/json','X-Pubgw-Timestamp':t,'X-Pubgw-Signature':"
            "hmac.new(s,t.encode()+b'.'+b,hashlib.sha256).hexdigest()});"
            "r=urllib.request.urlopen(q,timeout=60);sys.exit(0 if r.status==200 else 1)"
        )
        try:
            self.runner.run(self.compose + ["exec", "-T", "publish-gateway", "python", "-c", script, body],
                            check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise DoctorRuntimeError("product probe engine cleanup failed") from exc

    def run(self):
        config = self._configuration()
        token = self._token(config)
        project_id = survey_id = instance = None
        sid = None
        export_id = None
        failure = None
        try:
            status, _ = self._api(token, "GET", "/v1/resources?limit=1")
            if status != 200:
                raise DoctorRuntimeError("product probe login failed")
            suffix = uuid.uuid4().hex[:12]
            status, project = self._api(token, "POST", "/v1/projects", {"name": "production-probe-" + suffix})
            project = self._expect(status, 201, project, "project creation")
            project_id = project.get("id")
            definition = {
                "definitionVersion": 1, "uuid": str(uuid.uuid4()), "title": "Production probe " + suffix,
                "language": "en", "settings": {"anonymized": "N", "datestamp": "Y", "format": "G"},
                "groups": [{"uuid": str(uuid.uuid4()), "title": "Probe", "questions": [{
                    "uuid": str(uuid.uuid4()), "code": "PROBE", "type": "S", "text": "Probe response",
                    "mandatory": True,
                }]}],
            }
            status, survey = self._api(token, "POST", "/v1/surveys", {"parentId": project_id, "definition": definition})
            survey = self._expect(status, 201, survey, "survey creation")
            survey_id = survey.get("id")
            status, outcome = self._api(token, "POST", f"/v1/surveys/{survey_id}/publish")
            outcome = self._expect(status, 200, outcome, "publish")
            version = outcome.get("version") or {}
            sid, instance = version.get("engineSid"), version.get("engineInstanceId")
            if not isinstance(sid, int) or sid <= 0 or not isinstance(instance, str):
                raise DoctorRuntimeError("product probe publish binding is invalid")
            self._answer(sid)
            for _ in range(30):
                self.runner.run(self.compose + ["exec", "-T", "engine", "php",
                                                "application/commands/console.php", "plugin", "cron"],
                                check=True, capture_output=True, text=True)
                status, responses = self._api(
                    token, "GET", f"/v1/surveys/{survey_id}/responses?state=engine_completed&limit=10"
                )
                if status == 200 and isinstance(responses, dict) and responses.get("items"):
                    break
                self.sleep(2)
            else:
                raise DoctorRuntimeError("product probe response projection failed")
            status, export = self._api(token, "POST", f"/v1/surveys/{survey_id}/exports", {
                "format": "csv", "filter": {"states": ["engine_completed"], "versions": [1]},
                "templateVersion": "default",
            })
            export = self._expect(status, 202, export, "export creation")
            export_id = export.get("jobId")
            for _ in range(30):
                status, job = self._api(token, "GET", f"/v1/exports/{export_id}")
                if status == 200 and isinstance(job, dict) and job.get("status") == "completed":
                    break
                if status != 200 or (isinstance(job, dict) and job.get("status") in {"failed", "cancelled", "expired"}):
                    raise DoctorRuntimeError("product probe export failed")
                self.sleep(2)
            else:
                raise DoctorRuntimeError("product probe export timed out")
            status, data = self._api(token, "GET", f"/v1/exports/{export_id}/download", raw=True)
            if status != 200 or not data:
                raise DoctorRuntimeError("product probe export download failed")
        except (DoctorRuntimeError, OSError, subprocess.CalledProcessError) as exc:
            failure = exc
        finally:
            try:
                if instance and sid:
                    self._close_engine_survey(instance, sid)
                if project_id:
                    status, _ = self._api(token, "POST", f"/v1/resources/{project_id}/archive")
                    if status != 200:
                        raise DoctorRuntimeError("product probe project cleanup failed")
            except DoctorRuntimeError as cleanup:
                failure = cleanup
        if failure is not None:
            raise DoctorRuntimeError("product probe failed") from failure


class HostListenerProbe:
    """Bind host listeners to the inspected edge container, not just port numbers."""

    def __init__(self, runner):
        self.runner = runner

    def audit(self, output, edge):
        networks = edge.get("NetworkSettings", {}).get("Networks", {}) or {}
        edge_addresses = {
            details.get("IPAddress") for details in networks.values()
            if isinstance(details, dict) and details.get("IPAddress")
        }
        by_port = {}
        for line in (output or "").splitlines():
            match = re.search(r":(\d+)\s", line)
            if match:
                by_port.setdefault(int(match.group(1)), []).append(line)
        bad = []
        for port in (80, 443):
            lines = by_port.get(port, [])
            if not lines:
                bad.append("edge-listeners")
                continue
            for line in lines:
                owner = re.search(r'users:\(\(\"([^\"]+)\",pid=(\d+)', line)
                if owner is None or owner.group(1) != "docker-proxy":
                    bad.append(f"edge-listener-owner-{port}")
                    continue
                try:
                    process = self.runner.run(
                        ["ps", "-p", owner.group(2), "-o", "args="],
                        check=True, capture_output=True, text=True,
                    )
                except (OSError, subprocess.CalledProcessError):
                    bad.append(f"edge-listener-owner-{port}")
                    continue
                args = process.stdout or ""
                host_ok = re.search(rf"(?:^|\s)-host-port\s+{port}(?:\s|$)", args)
                container_ok = re.search(rf"(?:^|\s)-container-port\s+{port}(?:\s|$)", args)
                address = re.search(r"(?:^|\s)-container-ip\s+(\S+)", args)
                if not host_ok or not container_ok or not address or address.group(1) not in edge_addresses:
                    bad.append(f"edge-listener-target-{port}")
        if {3306, 5432} & set(by_port):
            bad.append("database-listeners")
        return bad


class Doctor:
    def __init__(self, target, runner=None, tls_probe=None, product_probe=None, backup_verifier=None,
                 listener_probe=None):
        self.target = Path(os.path.abspath(os.fspath(Path(target).expanduser())))
        current = Path(self.target.anchor)
        for component in self.target.parts[1:]:
            current = current / component
            if current.is_symlink():
                raise DoctorRuntimeError("target path contains a symbolic link")
            if not current.exists():
                break
        self.runner = runner or CommandRunner()
        self.listener_probe = listener_probe or HostListenerProbe(self.runner)
        self.tls_probe = tls_probe or self._tls_probe
        self.product_probe = product_probe or self._run_product_probe
        self.backup_verifier = backup_verifier or RestoreManager(
            self.target, runner=self.runner
        )
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

    def _runtime_exposure_check(self, compose):
        ps = self._command(compose + ["ps", "--all", "--format", "json"])
        try:
            text = (ps.stdout or "").strip()
            payload = json.loads(text) if text.startswith("[") else [json.loads(line) for line in text.splitlines() if line]
            rows = payload if isinstance(payload, list) else [payload]
            ids = [row["ID"] for row in rows if isinstance(row, dict) and row.get("ID")]
        except (AttributeError, KeyError, json.JSONDecodeError) as exc:
            raise DoctorRuntimeError("runtime container inventory is invalid") from exc
        if len(ids) != len(rows) or not ids:
            return self._check("runtime-exposure", "blocked", "runtime container identity is incomplete")
        inspected = self._command(["docker", "inspect", *ids])
        listeners = self._command(["ss", "-H", "-ltnp"])
        try:
            containers = json.loads(inspected.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise DoctorRuntimeError("runtime container inspection is invalid") from exc
        allowed_networks = {
            "edge": {"edge"}, "admin-web": {"edge"}, "platform": {"edge", "internal"},
            "publish-gateway": {"internal"}, "engine": {"edge", "internal"},
            "engine-init": {"internal"}, "platform-db": {"internal"}, "engine-db": {"internal"},
        }
        bad = []
        inspected_services = set()
        for item in containers if isinstance(containers, list) else []:
            labels = item.get("Config", {}).get("Labels", {}) or {}
            service = labels.get("com.docker.compose.service")
            inspected_services.add(service)
            bindings = item.get("HostConfig", {}).get("PortBindings") or {}
            networks = {
                name.removeprefix("survey-production_")
                for name in (item.get("NetworkSettings", {}).get("Networks", {}) or {})
            }
            if service == "edge":
                actual_pairs = {
                    (port.split("/", 1)[0], binding.get("HostPort"))
                    for port, values in bindings.items() for binding in (values or [])
                }
                binding_values = [binding for values in bindings.values() for binding in (values or [])]
                if actual_pairs != {("80", "80"), ("443", "443")} or any(
                    binding.get("HostIp", "") not in {"", "0.0.0.0", "::"}
                    for binding in binding_values
                ):
                    bad.append("edge-bindings")
            elif bindings:
                bad.append(f"{service}-binding")
            if service not in allowed_networks or networks != allowed_networks[service]:
                bad.append(f"{service}-networks")
        if inspected_services != set(allowed_networks):
            bad.append("container-inspection-set")
        edge = next((item for item in containers if isinstance(item, dict)
                     and item.get("Config", {}).get("Labels", {}).get("com.docker.compose.service") == "edge"), {})
        bad.extend(self.listener_probe.audit(listeners.stdout, edge))
        return self._check(
            "runtime-exposure", "ok" if not bad else "blocked",
            "live bindings, networks and host listeners match the production contract"
            if not bad else "runtime exposure drift detected: " + ", ".join(sorted(set(bad))),
        )

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
            if directory.is_symlink() or not directory.is_dir():
                continue
            try:
                with tempfile.TemporaryDirectory(dir=self.target) as temporary:
                    self.backup_verifier.verify_backup(directory, Path(temporary))
                valid += 1
            except (OSError, RestoreIntegrityError):
                continue
        return self._check("backups", "ok" if valid else "warning", f"{valid} verified backup(s) available" if valid else "no readable verified backup exists")

    def _run_product_probe(self, host, compose):
        ProductProbe(self.target, host, compose, self.runner).run()

    def _probe_check(self, host, compose):
        try:
            self.product_probe(host, compose)
            return self._check("minimal-probe", "ok", "disposable login, publish, answer, query and export journey succeeded")
        except DoctorRuntimeError:
            return self._check("minimal-probe", "blocked", "disposable product journey failed and was cleaned up")

    def run(self):
        version, schema, host, compose = self._load_installation()
        checks = [*self._config_checks(compose), self._runtime_exposure_check(compose)]
        tls_ok = self.tls_probe(host)
        checks.extend([
            self._check("tls", "ok" if tls_ok else "blocked", "public TLS deployment marker verified" if tls_ok else "public TLS deployment marker failed"),
            self._container_check(compose),
            self._migration_check(compose, schema),
            self._volume_check(),
            self._backup_check(),
            self._probe_check(host, compose),
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
