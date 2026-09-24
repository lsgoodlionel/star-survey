"""发布网关的 HTTP 入口：``python3 -m pubgw.server``。

契约见 platform/contracts/publish-gateway-v1.md（及 v1.2 增补）。本模块只做 HTTP 外壳：
路由、请求体大小限制、读配置；业务语义全部在 service.py。

环境变量：

==========================  ===============================================
``PUBGW_SHARED_SECRET``     与平台共享的 HMAC 密钥，至少 32 字节（必填）
``PUBGW_ENGINES_CONFIG``    引擎实例配置 JSON 的路径（必填，格式见 engines.py）
``PUBGW_STATE_DIR``         幂等结果 SQLite 所在目录（必填）
``PUBGW_HOST``              监听地址，缺省 127.0.0.1（容器里设为 0.0.0.0）
``PUBGW_PORT``              监听端口，缺省 8080
``PUBGW_RESULT_TTL_SECONDS``     完整回执的留存期，缺省 7 天，下限 24 小时
``PUBGW_TOMBSTONE_TTL_SECONDS``  墓碑的留存期，缺省 90 天，不得短于回执留存期
``PUBGW_INVITATION_TTL_SECONDS`` 带邀请码的回执的留存期，缺省 24 小时，下限 2 小时，
                                 不得长于回执留存期
==========================  ===============================================

任何一项不合法都拒绝启动（退出码 2）。收到 SIGTERM 时停止接新请求，
等在途发布做完再退出——半路被杀的发布会在引擎里留下孤儿问卷。

留存语义见 store.py 与契约 v1.3：留存期只是上界，判过期不依赖清理线程；
清理线程（``PruneScheduler``）只删行。回收磁盘（VACUUM）会与正常请求抢锁，
因此是运维显式触发的维护动作：``python3 -m pubgw.cli prune-results``。
"""

import json
import logging
import os
import signal
import sys
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Mapping, Optional
from urllib.parse import urlsplit

from .auth import check_secret
from .engines import ConfigError, EngineConfig, load_engines
from .responses import READ_PATH, ResponseReadService
from .service import PublishService, Response
from .store import RESULTS_FILE, PruneScheduler, ResultStore, Retention, retention_from_env

log = logging.getLogger("pubgw.server")

MAX_BODY_BYTES = 1024 * 1024
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
#: 单个连接读请求的超时；发布本身耗时由 RPC 超时约束，与此无关。
READ_TIMEOUT_SECONDS = 30
EXIT_CONFIG = 2

PUBLISH_PATH = "/v1/publish"
CLOSE_PATH = "/v1/close"
DRIFT_CHECK_PATH = "/v1/drift-check"
HEALTH_PATH = "/healthz"
#: POST 路径 → PublishService 上的处理方法名（契约 v1 与 v1.2）。
POST_ROUTES = {PUBLISH_PATH: "publish", CLOSE_PATH: "close", DRIFT_CHECK_PATH: "drift_check"}


class StartupError(Exception):
    """配置不完整或不合法，服务拒绝启动。消息里不含任何密钥或口令。"""


@dataclass(frozen=True)
class Settings:
    secret: bytes = field(repr=False)
    engines: Mapping[str, EngineConfig]
    state_dir: str
    host: str
    port: int
    retention: Retention


def load_settings(env: Mapping[str, str]) -> Settings:
    try:
        secret = check_secret(env.get("PUBGW_SHARED_SECRET", ""))
    except ValueError as error:
        raise StartupError("PUBGW_SHARED_SECRET: {}".format(error)) from None

    config_path = env.get("PUBGW_ENGINES_CONFIG", "")
    if not config_path:
        raise StartupError("PUBGW_ENGINES_CONFIG is not set")
    try:
        engines = load_engines(config_path, env)
    except ConfigError as error:
        raise StartupError(str(error)) from None

    state_dir = env.get("PUBGW_STATE_DIR", "")
    if not state_dir:
        raise StartupError("PUBGW_STATE_DIR is not set")

    return Settings(
        secret=secret,
        engines=engines,
        state_dir=state_dir,
        host=env.get("PUBGW_HOST", "") or DEFAULT_HOST,
        port=_port(env.get("PUBGW_PORT", "")),
        retention=_retention(env),
    )


def _retention(env: Mapping[str, str]) -> Retention:
    try:
        return retention_from_env(env)
    except ValueError as error:
        raise StartupError(str(error)) from None


def _port(value: str) -> int:
    if not value:
        return DEFAULT_PORT
    if not value.isdigit() or not 0 < int(value) < 65536:
        raise StartupError("PUBGW_PORT must be a TCP port number")
    return int(value)


def build_store(settings: Settings) -> ResultStore:
    return ResultStore(os.path.join(settings.state_dir, RESULTS_FILE), retention=settings.retention)


def build_service(settings: Settings, store: Optional[ResultStore] = None) -> PublishService:
    return PublishService(
        engines=settings.engines,
        store=store if store is not None else build_store(settings),
        secret=settings.secret,
    )


def build_response_service(settings: Settings) -> ResponseReadService:
    return ResponseReadService(engines=settings.engines, secret=settings.secret)


class GatewayServer(ThreadingHTTPServer):
    # 非守护线程 + server_close 时等待：停机时让在途发布做完。
    daemon_threads = False
    block_on_close = True

    def __init__(self, address, service: PublishService, responses: Optional[ResponseReadService] = None):
        super().__init__(address, GatewayHandler)
        self.service = service
        self.responses = responses


def build_server(
    service: PublishService, host: str, port: int, responses: Optional[ResponseReadService] = None
) -> GatewayServer:
    return GatewayServer((host, port), service, responses)


class GatewayHandler(BaseHTTPRequestHandler):
    server_version = "pubgw"
    sys_version = ""
    timeout = READ_TIMEOUT_SECONDS

    def do_GET(self) -> None:
        path = self._path()
        if path == HEALTH_PATH:
            self._send(Response(200, _json({"status": "ok"})))
        elif path in POST_ROUTES or path == READ_PATH:
            self._method_not_allowed("POST")
        else:
            self._not_found()

    def do_POST(self) -> None:
        path = self._path()
        if path == HEALTH_PATH:
            self._method_not_allowed("GET")
            return
        handler = self._post_handler(path)
        if handler is None:
            self._not_found()
            return
        body = self._read_body()
        if body is None:
            self.close_connection = True
            self._send(Response(400, _json({"error": "invalid_request"})))
            return
        try:
            response = handler(dict(self.headers.items()), body)
        except Exception:  # noqa: BLE001 — 兜底：对外绝不带堆栈
            log.exception("unhandled error in %s handler", path)
            response = Response(500, _json({"error": "internal_error"}))
        self._send(response)

    def _post_handler(self, path: str):
        operation = POST_ROUTES.get(path)
        if operation is not None:
            return getattr(self.server.service, operation)
        if path == READ_PATH and self.server.responses is not None:
            return self.server.responses.read
        return None

    # ------------------------------------------------------------ 零件

    def _path(self) -> str:
        return urlsplit(self.path).path

    def _read_body(self) -> Optional[bytes]:
        """只接受带 Content-Length 且不超过 1 MiB 的请求体；超限时一个字节都不读。"""
        if self.headers.get("Transfer-Encoding"):
            log.info("rejected request: Transfer-Encoding is not supported")
            return None
        raw_length = self.headers.get("Content-Length", "")
        if not raw_length.isdigit():
            log.info("rejected request: missing or invalid Content-Length")
            return None
        length = int(raw_length)
        if length > MAX_BODY_BYTES:
            log.info("rejected request: body of %d bytes exceeds %d", length, MAX_BODY_BYTES)
            return None
        body = self.rfile.read(length)
        if len(body) != length:
            log.info("rejected request: body truncated (%d of %d bytes)", len(body), length)
            return None
        return body

    def _not_found(self) -> None:
        self._send(Response(404, _json({"error": "not_found"})))

    def _method_not_allowed(self, allowed: str) -> None:
        self._send(Response(405, _json({"error": "method_not_allowed"})), {"Allow": allowed})

    def _send(self, response: Response, extra_headers: Optional[Mapping[str, str]] = None) -> None:
        self.send_response(response.status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(response.body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 — 覆盖基类签名
        log.info("%s %s", self.address_string(), format % args)


def _json(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


def serve(httpd: GatewayServer) -> None:
    """阻塞运行，SIGTERM/SIGINT 时优雅停机。"""

    def stop(signum, frame) -> None:
        log.info("signal %s received, draining in-flight requests", signum)
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


def main(env: Optional[Mapping[str, str]] = None) -> int:
    try:
        settings = load_settings(os.environ if env is None else env)
        store = build_store(settings)
        service = build_service(settings, store)
        httpd = build_server(service, settings.host, settings.port, build_response_service(settings))
    except (StartupError, OSError) as error:
        log.error("refusing to start: %s", error)
        return EXIT_CONFIG
    log.info(
        "publish gateway listening on %s:%s with %d engine instance(s); "
        "receipts are kept for %ds (%ds when they carry invitation codes), tombstones for %ds",
        settings.host, httpd.server_address[1], len(settings.engines),
        settings.retention.result_seconds, settings.retention.invitation_seconds,
        settings.retention.tombstone_seconds,
    )
    pruner = PruneScheduler(store)
    pruner.start()
    try:
        serve(httpd)
    finally:
        pruner.stop()
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    raise SystemExit(main())
