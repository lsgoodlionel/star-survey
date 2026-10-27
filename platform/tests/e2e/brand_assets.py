#!/usr/bin/env python3
"""Wave2 Task2: disposable real RC/PHP/DB/HTTP brand asset acceptance.

Run via platform/deploy/test/run-brand-assets.sh [--db mysql|pgsql|all].
The platform materialization boundary is supplied as pinned test snapshots;
Java asset resolution and browser UI workflows are outside this acceptance.
"""

import argparse
import base64
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import logging
import os
from pathlib import Path
import re
import secrets
import struct
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urljoin, urlsplit
import uuid
import zlib

from publish_gateway import ADMIN_PASSWORD, ADMIN_USER, Database, DockerCurlTransport
from pubgw.auth import sign
from pubgw.brand_assets import BrandAssetError, BrandAssetInstaller
from pubgw.channel import derive_channel_secret
from pubgw.engines import EngineConfig
from pubgw.model import SurveyDefinition
from pubgw.publish import Publisher
from pubgw.request import parse_request
from pubgw.rpc import RemoteControlClient
from pubgw.service import PreviewOperationStore, PublishService
from pubgw.store import ResultStore

ROOT = Path(__file__).resolve().parents[3]
RPC_URL = "http://localhost/index.php/admin/remotecontrol"
SLOTS = {"logoAsset": ("mjy-brand-logo.png", "mjybrandlogofile"),
         "faviconAsset": ("mjy-brand-favicon.png", "mjybrandfavicon")}
AUTHOR_BRAND_MARKERS = ("#176B48", "Pinned brand assets", "Acceptance logo",
                        "image::survey::", "mjy-brand-logo.png", "mjy-brand-favicon.png")


class AcceptanceFailure(RuntimeError):
    """Only fixed, non-sensitive acceptance diagnostics belong here."""


def require(condition, label):
    if not condition:
        raise AcceptanceFailure(label)


def assert_no_author_brand_options(db, sid):
    """Check fixture-specific author values while allowing engine theme defaults.

    The isolated MySQL and PostgreSQL schemas both store options as text. SQL
    checks avoid client-specific escaping and NULL representations.
    """
    require(isinstance(sid, int) and sid > 0, "invalid rollback survey id")
    predicates = " OR ".join("options LIKE '%{}%'".format(marker) for marker in AUTHOR_BRAND_MARKERS)
    count = db.value("SELECT COUNT(*) FROM lime_template_configuration "
                     "WHERE sid={} AND ({})".format(sid, predicates))
    require(count == "0", "failed installation left author brand theme options behind")


def png(red, green, blue):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + bytes((red, green, blue)) * 2) * 2))
            + chunk(b"IEND", b""))


def snapshot(version):
    definition = {
        "definitionVersion": 1, "uuid": "123e4567-e89b-42d3-a456-426614174010",
        "title": "Brand assets acceptance", "language": "en", "theme": "zh-business",
        "settings": {
            "anonymized": "N", "datestamp": "Y", "savetimings": "N", "ipaddr": "N",
            "refurl": "N", "allowsave": "N", "allowprev": "Y",
            "alloweditaftercompletion": "N", "format": "G", "questionindex": "0",
        },
        "groups": [{"uuid": "brand-group", "title": "Brand assets",
                    "questions": [{"uuid": "brand-question", "code": "BRANDQ",
                                   "type": "S", "text": "Asset acceptance question"}]}],
        "branding": {"brandingVersion": 1, "primaryColor": "#176B48",
                     "pageTitle": {"en": "Pinned brand assets"}, "logoAlt": "Acceptance logo"},
    }
    assets = []
    for index, (slot, (filename, _)) in enumerate(SLOTS.items()):
        content = png(40 * version, 70 * (index + 1), 200 - version * 20)
        item = {"slot": slot, "fileName": filename, "contentType": "image/png",
                "sizeBytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                "contentBase64": base64.b64encode(content).decode("ascii")}
        assets.append(item)
        definition["branding"][slot] = {
            **{key: item[key] for key in ("fileName", "contentType", "sizeBytes", "sha256")},
            "assetVersion": version,
            "url": (
                "https://assets.invalid/a/test/123e4567-e89b-42d3-a456-42661417400{}/{}"
                "?exp=1800000000&sig={}"
            ).format(index, version, "a" * 64),
        }
    return definition, assets


class AssetElements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = {}

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "img" and "logo" in attrs.get("class", "").split():
            self.urls["logoAsset"] = attrs.get("src", "")
        if tag == "link" and "icon" in attrs.get("rel", "").split():
            self.urls["faviconAsset"] = attrs.get("href", "")


def asset_urls(html, sid):
    parser = AssetElements()
    parser.feed(html)
    require(set(parser.urls) == set(SLOTS), "rendered logo/favicon elements missing")
    resolved = {}
    for slot, value in parser.urls.items():
        url = urljoin("http://localhost/", value)
        parts = urlsplit(url)
        expected = "/upload/surveys/{}/images/{}".format(sid, SLOTS[slot][0])
        # The theme asset publisher may expose the configured survey image through
        # its same-origin /tmp/assets/<digest>/ URL (notably favicon).
        published = re.fullmatch(r"/tmp/assets/[0-9a-f]{1,8}/" + re.escape(SLOTS[slot][0]), parts.path)
        require(parts.scheme == "http" and parts.netloc == "localhost" and (parts.path == expected or published)
                and not parts.query and not parts.fragment,
                "rendered asset escaped survey directory: slot={} scheme={} host={} path={} query={} fragment={}".format(
                    slot, parts.scheme, parts.netloc, parts.path, bool(parts.query), bool(parts.fragment)
                ))
        resolved[slot] = url
    return resolved


def assert_asset_bytes(content, asset):
    require(content == base64.b64decode(asset["contentBase64"]), "HTTP asset bytes differ from pinned snapshot")
    require(len(content) == asset["sizeBytes"] and hashlib.sha256(content).hexdigest() == asset["sha256"],
            "HTTP asset size/digest differs from pinned snapshot")


def docker_post(container):
    def post(request, timeout):
        try:
            return DockerCurlTransport(container, request.full_url)(request.data)
        except Exception:
            raise BrandAssetError("isolated Docker brand asset POST failed") from None
    return post


def require_publish(result):
    stage = result.get("failedStage")
    allowed = {"validate", "compile", "import", "brand_assets", "apply", "activate", "verify", "bind"}
    require(result.get("ok") is True, "publish failed at " + (stage if stage in allowed else "unknown stage"))
    return int(result["surveyId"])


def command(args, label, env=None, timeout=180):
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise AcceptanceFailure(label + " could not finish") from None
    require(result.returncode == 0, label + " failed (raw output suppressed)")
    return result.stdout


class IsolatedStack:
    def __init__(self, driver):
        require(driver in ("mysql", "pgsql"), "unsupported database")
        self.driver = driver
        self.project = "brand-assets-" + driver + "-" + uuid.uuid4().hex[:10]
        self.env = dict(os.environ, BRAND_ASSETS_ROOT=str(ROOT), BRAND_ASSETS_DB=driver,
                        BRAND_ASSETS_PROJECT=self.project, MJY_ENGINE_INSTANCE_ID=self.project,
                        MJY_PLATFORM_EVENTS_SECRET=secrets.token_hex(32))
        self.compose = ["docker", "compose", "--project-name", self.project, "-f",
                        str(ROOT / "platform/deploy/test/compose.brand-assets.yml")]
        self.container = self.project + "-web"
        self.db_container = self.project + ("-mysql" if driver == "mysql" else "-pgsql")
        self.db = Database(driver, self.db_container)

    def start(self):
        command(self.compose + ["up", "-d", "--wait", "--wait-timeout", "120",
                               "brand-assets-" + self.driver, "brand-assets-web"],
                "isolated compose startup", self.env, 180)
        command(["docker", "exec", self.container, "php", "application/commands/console.php", "install",
                 ADMIN_USER, ADMIN_PASSWORD, "BrandAssetsTest", "test@invalid.local"], "isolated engine install")
        self.db.rows("INSERT INTO lime_settings_global (stg_name, stg_value) VALUES ('RPCInterface', 'json')")
        self.db.rows("INSERT INTO lime_plugins (name, plugin_type, active, priority, version, load_error) "
                     "VALUES ('MjyQuestionExtensions', 'user', 1, 0, '0.1.0', 0)")
        command(["docker", "exec", self.container, "php",
                 "platform/tools/engine-theme/install-survey-theme.php", "zh-business"], "isolated theme install")

    def stop(self):
        command(self.compose + ["down", "--volumes", "--remove-orphans"], "isolated compose cleanup", self.env)


class RealEngineAcceptance:
    def __init__(self, stack, state):
        self.stack, self.state = stack, Path(state)
        self.clock = [time.time()]
        self.secret = secrets.token_bytes(32)
        self.channel_key = derive_channel_secret(stack.env["MJY_PLATFORM_EVENTS_SECRET"], stack.project)
        self.methods = []
        self.installed_sids = []
        self.transport = DockerCurlTransport(stack.container)
        self.engine = EngineConfig(stack.project, RPC_URL, ADMIN_USER, ADMIN_PASSWORD, self.channel_key)

    def rpc(self, payload):
        self.methods.append(json.loads(payload)["method"])
        return self.transport(payload)

    def installer(self, bad_key=False):
        transport = docker_post(self.stack.container)

        def post(request, timeout):
            self.installed_sids.append(int(parse_qs(urlsplit(request.full_url).query)["sid"][0]))
            return transport(request, timeout)

        return BrandAssetInstaller.from_rpc_url(
            RPC_URL, self.stack.project, secrets.token_hex(32) if bad_key else self.channel_key, post=post)

    def publish(self, saved, bad_key=False):
        definition, assets = saved
        parsed = parse_request(json.dumps({"requestId": str(uuid.uuid4()), "engineInstanceId": self.stack.project,
                                           "definition": definition, "brandAssets": assets}).encode())
        client = RemoteControlClient(self.rpc)
        client.login(ADMIN_USER, ADMIN_PASSWORD)
        try:
            return Publisher(client, engine_instance=self.stack.project,
                             brand_asset_installer=self.installer(bad_key)).publish(
                                 SurveyDefinition.from_dict(parsed.definition), parsed.brand_assets).to_dict()
        finally:
            client.logout()

    def fetch(self, url):
        require(urlsplit(url).netloc == "localhost", "HTTP probe must stay in isolated engine")
        return command(["docker", "exec", self.stack.container, "curl", "--silent", "--fail", "--location",
                        "--max-time", "30", url], "respondent HTTP fetch", timeout=40)

    def verify_assets(self, sid, assets, page_url=None):
        raw = self.stack.db.value("SELECT options FROM lime_template_configuration "
                                  "WHERE template_name='zh-business' AND sid={}".format(sid))
        if self.stack.driver == "mysql":
            raw = raw.replace("\\\\", "\\")
        options = json.loads(raw)
        for slot, (filename, option) in SLOTS.items():
            require(options.get(option) == "image::survey::" + filename, "database theme option mismatch: " + slot)
        html = self.fetch(page_url or "http://localhost/index.php/{}?newtest=Y&lang=en".format(sid)).decode()
        urls = asset_urls(html, sid)
        for asset in assets:
            assert_asset_bytes(self.fetch(urls[asset["slot"]]), asset)
        return urls

    def service(self, bad_key=False, crash=False):
        operations = PreviewOperationStore(str(self.state / "preview.sqlite3"), now=lambda: self.clock[0])
        operations.fail_after_import_once = crash
        return PublishService(
            engines={self.stack.project: self.engine}, store=ResultStore(str(self.state / "results.sqlite3")),
            secret=self.secret, transport_factory=lambda cfg: self.rpc,
            brand_asset_installer_factory=lambda cfg: self.installer(bad_key),
            now=lambda: self.clock[0], preview_operations=operations)

    def send(self, service, method, payload):
        body = json.dumps(payload).encode()
        stamp = str(int(self.clock[0]))
        response = getattr(service, method)({"Content-Type": "application/json", "X-Pubgw-Timestamp": stamp,
                                           "X-Pubgw-Signature": sign(self.secret, stamp, body)}, body)
        return response.status, json.loads(response.body)

    def preview_payload(self, saved):
        return {"tenantId": str(uuid.uuid4()), "sessionId": str(uuid.uuid4()), "requestId": str(uuid.uuid4()),
                "engineInstanceId": self.stack.project, "generation": "preview-brand-assets",
                "expiresAt": datetime.fromtimestamp(self.clock[0] + 1800, timezone.utc).isoformat(),
                "definition": saved[0], "brandAssets": saved[1]}

    def survey_ids(self):
        return {int(row[0]) for row in self.stack.db.rows("SELECT sid FROM lime_surveys")}

    def preview_recovery(self, saved):
        payload = self.preview_payload(saved)
        before = self.survey_ids()
        imported = self.methods.count("import_survey")
        installed = len(self.installed_sids)
        code, first = self.send(self.service(crash=True), "preview", payload)
        require(code == 503 and first.get("error") == "result_unknown", "preview import crash was not exercised")
        created = self.survey_ids() - before
        require(len(created) == 1 and len(self.installed_sids) == installed, "preview crash boundary mismatch")
        sid = created.pop()
        self.clock[0] += PreviewOperationStore.LEASE_SECONDS + 1
        service = self.service()
        code, prepared = self.send(service, "preview", payload)
        require(code == 200 and prepared.get("status") == "prepared", "preview recovery failed")
        require(self.methods.count("import_survey") == imported + 1, "preview recovery imported twice")
        require(self.installed_sids[installed:] == [sid], "preview recovery did not use signed installer once")
        identity = {key: payload[key] for key in ("tenantId", "sessionId", "requestId")}
        code, ready = self.send(service, "activate_preview", identity)
        require(code == 200 and ready.get("status") == "ready", "preview activation failed")
        access = service.preview_access(parse_qs(urlsplit(ready["result"]["previewUrl"]).query))
        require(access.status == 302, "preview access did not redirect")
        self.verify_assets(sid, saved[1], access.headers["Location"])
        code, closed = self.send(service, "close_preview", identity)
        require(code == 200 and closed.get("status") == "closed", "preview close failed")
        # Activated previews retain their isolated response table; close sets an
        # expiry instead of deleting it, matching the gateway close contract.
        require(sid in self.survey_ids(), "preview close removed the retained survey")
        expiry = self.stack.db.value("SELECT expires FROM lime_surveys WHERE sid={}".format(sid))
        require(datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
                <= self.clock[0], "preview close did not expire the survey")
        require(service.preview_access(parse_qs(urlsplit(ready["result"]["previewUrl"]).query)).status == 410,
                "closed preview access remained available")
        require(self.stack.db.value("SELECT COUNT(*) FROM lime_responses_{} WHERE submitdate IS NOT NULL".format(sid)) == "0",
                "preview submitted a response in its isolated survey")

    def failed_install(self, saved, preview=False):
        before = self.survey_ids()
        if preview:
            code, outcome = self.send(self.service(bad_key=True), "preview", self.preview_payload(saved))
            require(code == 502 and outcome.get("status") == "failed", "preview installation did not fail")
            result = outcome["result"]
        else:
            result = self.publish(saved, bad_key=True)
        sid = self.installed_sids[-1]
        require(result.get("ok") is False and result.get("failedStage") == "brand_assets",
                "installation did not fail at brand_assets")
        require(any(step["stage"] == "rollback" and step["ok"] for step in result["steps"]),
                "failed installation did not report rollback")
        require(sid not in self.survey_ids() and self.survey_ids() == before, "failed installation left survey behind")
        # Survey::delete may retain a row populated with theme defaults. Its
        # presence and default mjybrand keys do not imply author data survived.
        assert_no_author_brand_options(self.stack.db, sid)
        command(["docker", "exec", self.stack.container, "sh", "-c",
                 "test ! -d /var/www/html/upload/surveys/" + str(sid)], "rollback brand file cleanup")

    def run(self):
        first = snapshot(1)
        sid1 = require_publish(self.publish(first))
        urls = self.verify_assets(sid1, first[1])
        print("  PASS publish-v1: RC import -> signed PHP -> DB options -> logo/favicon HTTP bytes", flush=True)
        second = snapshot(2)
        sid2 = require_publish(self.publish(second))
        require(sid1 != sid2, "second publish reused first survey")
        self.verify_assets(sid2, second[1])
        self.verify_assets(sid1, first[1])
        for asset in first[1]:
            assert_asset_bytes(self.fetch(urls[asset["slot"]]), asset)
        print("  PASS publish-v2: new bytes; original v1 URLs still return original bytes", flush=True)
        self.preview_recovery(first)
        print("  PASS preview: real import crash/restart -> same sid -> signed install -> HTTP bytes -> close", flush=True)
        self.failed_install(second)
        self.failed_install(second, preview=True)
        self.verify_assets(sid1, first[1])
        self.verify_assets(sid2, second[1])
        print("  PASS wrong channel key: publish + preview rollback deleted surveys; earlier versions intact", flush=True)


def shared_fingerprint():
    facts = []
    for container in ("survey-test-web", "survey-test-db"):
        result = subprocess.run(["docker", "inspect", container], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode == 0:
            value = json.loads(result.stdout)[0]
            facts.append({key: value[key] for key in ("Id", "Config", "Mounts")})
            facts.append(value["State"]["StartedAt"])
    for relative in ("application/config/config.php", "application/config/allowed_hosts.php",
                     "platform/deploy/test/.runtime/config.php", "platform/deploy/test/lib.sh",
                     "platform/deploy/test/run-brand-theme.sh"):
        path = ROOT / relative
        facts.append(hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
    return hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", choices=("mysql", "pgsql", "all"), default="all")
    args = parser.parse_args()
    # Service tracebacks can contain payloads; the acceptance emits its own fixed diagnostics.
    logging.disable(logging.CRITICAL)
    before = shared_fingerprint()
    for driver in (("mysql", "pgsql") if args.db == "all" else (args.db,)):
        stack = IsolatedStack(driver)
        print("Starting isolated brandAssets acceptance ({})".format(driver), flush=True)
        primary_error = None
        try:
            stack.start()
            with tempfile.TemporaryDirectory(prefix="brand-assets-state-") as state:
                RealEngineAcceptance(stack, state).run()
        except Exception as error:
            primary_error = error
        finally:
            try:
                stack.stop()
            except Exception as cleanup_error:
                if primary_error is None:
                    primary_error = cleanup_error
                else:
                    print("WARN: isolated compose cleanup also failed", file=sys.stderr)
            if shared_fingerprint() != before and primary_error is None:
                primary_error = AcceptanceFailure("shared test stack/configuration changed during run")
        if primary_error is not None:
            raise primary_error
        print("PASS {}: isolated stack removed; shared test stack/configuration unchanged".format(driver), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AcceptanceFailure as error:
        print("FAIL: " + str(error), file=sys.stderr)
        raise SystemExit(1)
    except Exception as error:
        print("FAIL: {} (raw details suppressed)".format(type(error).__name__), file=sys.stderr)
        raise SystemExit(1)
