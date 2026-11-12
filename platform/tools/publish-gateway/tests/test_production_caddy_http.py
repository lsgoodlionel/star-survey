"""Exercise the production edge with a real gateway and synthetic engine.

Docker host networking here is limited to disposable Linux CI runners.  This
does not replace a candidate browser journey or a real engine integration.
"""
import dataclasses
import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

from pubgw.server import build_server
from .fixtures import sample_payload
from .gateway_support import INSTANCE, encode, engine_config, make_service, new_engine, signed_headers


class StubHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"synthetic-upstream")

    def log_message(self, *args):
        pass


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux Docker host networking required")
class ProductionCaddyHttpTest(unittest.TestCase):
    def test_signed_access_redirect_and_nonpreview_routing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = make_service(new_engine(), temporary, clock=time.time)
            service._engines = {INSTANCE: dataclasses.replace(engine_config(),
                rpc_url="http://engine/index.php/admin/remotecontrol")}
            gateway = build_server(service, "127.0.0.1", 0)
            stub = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
            threads = [threading.Thread(target=x.serve_forever, daemon=True) for x in (gateway, stub)]
            for thread in threads:
                thread.start()
            edge_port, health_port = free_port(), free_port()
            while health_port == edge_port:
                health_port = free_port()
            source = Path(__file__).resolve().parents[3] / "deploy/production/Caddyfile"
            config = source.read_text().replace("publish-gateway:8080", f"127.0.0.1:{gateway.server_port}")
            config = config.replace("engine:80", f"127.0.0.1:{stub.server_port}")
            config = config.replace("platform:8080", f"127.0.0.1:{stub.server_port}")
            config = config.replace("admin-web:80", f"127.0.0.1:{stub.server_port}")
            config = config.replace("http://127.0.0.1 {", f"http://127.0.0.1:{health_port} {{")
            (root / "Caddyfile").write_text(config)
            admin_hash = (source.parent / "tests/fixtures/secrets/engine_admin_hash").read_text().strip()
            cidfile = root / "cid"
            command = ["docker", "run", "--rm", "--network", "host", "--cidfile", str(cidfile),
                       "-e", f"PUBLIC_HOST=http://127.0.0.1:{edge_port}",
                       "-e", "ENGINE_ADMIN_USER=fixture", "-e", f"ENGINE_ADMIN_PASSWORD_HASH={admin_hash}",
                       "-v", f"{root / 'Caddyfile'}:/etc/caddy/Caddyfile:ro", "caddy:2.11.9-alpine@sha256:0bbb055bda6793c999bde78b1b5ce3e89f829fb031af2cf50056ea1246559fee",
                       "caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    try:
                        with socket.create_connection(("127.0.0.1", edge_port), timeout=0.2):
                            break
                    except OSError:
                        if process.poll() is not None:
                            self.fail("Caddy container exited before readiness")
                        time.sleep(0.1)
                else:
                    self.fail("Caddy did not become ready")
                payload = {"tenantId": str(uuid.uuid4()), "sessionId": str(uuid.uuid4()),
                           "requestId": str(uuid.uuid4()), "engineInstanceId": INSTANCE,
                           "generation": "preview-fixture", "expiresAt": time.strftime(
                               "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 600)),
                           "definition": sample_payload()}
                body = encode(payload)
                self.assertEqual(200, service.preview(signed_headers(body, timestamp=int(time.time())), body).status)
                body = encode({k: payload[k] for k in ("tenantId", "sessionId", "requestId")})
                activated = service.activate_preview(signed_headers(body, timestamp=int(time.time())), body)
                self.assertEqual(200, activated.status)
                url = urlsplit(json.loads(activated.body)["result"]["previewUrl"])
                query = parse_qs(url.query)

                def request(method, path):
                    conn = http.client.HTTPConnection("127.0.0.1", edge_port, timeout=5)
                    try:
                        conn.request(method, path)
                        response = conn.getresponse()
                        return response.status, dict(response.getheaders()), response.read()
                    finally:
                        conn.close()

                status, headers, _ = request("GET", url.path + "?" + url.query)
                self.assertEqual(302, status)
                self.assertTrue(headers["Location"].startswith("/survey/index.php/"))
                self.assertIn("token=", headers["Location"])
                self.assertEqual(200, request("GET", headers["Location"])[0])
                query["sid"] = [str(int(query["sid"][0]) + 1)]
                self.assertEqual(403, request("GET", url.path + "?" + urlencode(query, doseq=True))[0])
                self.assertEqual(200, request("GET", "/v1/not-preview")[0])
                self.assertEqual(200, request("GET", "/v1/preview/access-extra")[0])
            finally:
                if cidfile.exists():
                    cid = cidfile.read_text().strip()
                    if len(cid) == 64 and all(c in "0123456789abcdef" for c in cid):
                        subprocess.run(["docker", "stop", "--time", "2", cid], capture_output=True, timeout=10)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                for server in (gateway, stub):
                    server.shutdown()
                    server.server_close()
                for thread in threads:
                    thread.join(timeout=5)
