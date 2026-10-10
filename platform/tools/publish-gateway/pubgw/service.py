"""``POST /v1/publish`` 的业务语义，与 HTTP 外壳解耦（外壳见 server.py）。

处理顺序（每一步失败都不会走到下一步，更不会碰引擎）：

1. HMAC 认证 → 401
2. Content-Type 与请求体结构 → 400
3. 幂等：``requestId`` 已有结果则原样返回（内容不同则 400）
4. 实例解析 → 404
5. 定义结构 → 400
6. 在途锁：同一 ``requestId`` 或同一 ``(实例, definition.uuid)`` 正在发布 → 409
7. 持锁再查一次幂等（首个请求可能刚刚完成）
8. 发布：200 / 422 / 502；网关自身的意外异常 → 500（不带堆栈）

第 8 步的任何结果都落库，同一 ``requestId`` 之后只重放、不再发布——
包括 500：那时引擎状态未知，重复发布只会多留一个问卷。

存档是**有界**的（契约 v1.3，见 store.py）：完整回执过了留存期只剩墓碑，此时重放是
410 ``result_expired``，而不是当作没发过——后者会让平台再发布一次，在引擎里多出一份问卷。

口令防线：响应体在出门前把所有已配置的引擎口令（原文、repr 与 JSON 转义形式）替换成
``***``，即使引擎把口令写进了错误信息也不会外泄。

契约 v1.2 增补的 ``POST /v1/close`` 与 ``POST /v1/drift-check``、v1.4 增补的
``POST /v1/participants/revoke`` 走同样的认证、实例解析与口令防线；三者天然幂等
（收口重复无害、漂移检查只读、撤销一个已经不在引擎里的码同样是"已撤销"），
都不进 requestId 结果存档。
"""

import hashlib
import hmac
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import urlencode

from .auth import SIGNATURE_HEADER, TIMESTAMP_HEADER, AuthError, verify
from .close import CloseError, close_survey
from .drift_check import check_published_survey
from .engines import EngineConfig
from .model import DefinitionError, SurveyDefinition
from .participants import RevokeError, revoke_participant
from .policy.probe import HttpPolicyProbe, PolicyProbe
from .ops_request import parse_close_request, parse_drift_request, parse_revoke_request
from .binding import BindingRecord
from .fieldmap import FINGERPRINT_VERSION
from .logic.scoring import expand_scoring
from .publish import PublishResult, Publisher, StageStep
from .request import InvalidRequest, PublishRequest, parse_request
from .rpc import HttpTransport, RemoteControlClient, RpcError, Transport
from .store import ExpiredResult, InFlight, Lookup, ResultStore

log = logging.getLogger("pubgw.service")

TransportFactory = Callable[[EngineConfig], Transport]
PolicyProbeFactory = Callable[[EngineConfig], Optional[PolicyProbe]]

REDACTED = "***"
_REJECTED_STAGES = frozenset({"validate", "compile"})
_PREVIEW_FIELDS = frozenset({
    "tenantId", "sessionId", "requestId", "engineInstanceId", "definition", "generation", "expiresAt"
})
_PREVIEW_IDENTITY_FIELDS = frozenset({"tenantId", "sessionId", "requestId"})
_PREVIEW_GENERATION = re.compile(r"\Apreview-[a-z0-9][a-z0-9-]{2,55}\Z")
_UUID_TEXT = re.compile(r"\A[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    headers: Optional[Mapping[str, str]] = None


@dataclass(frozen=True)
class _Attempt:
    """一次发布的应答，外加要写进墓碑的引擎 sid（回执过期后只剩它可查）。"""

    response: Response
    survey_id: Optional[int] = None


@dataclass(frozen=True)
class _PreviewRequest:
    tenant_id: str
    session_id: str
    request_id: str
    engine_instance_id: str
    definition: Mapping[str, Any]
    generation: str
    expires_at: str
    fingerprint: str


@dataclass(frozen=True)
class _PreviewOperation:
    tenant_id: str
    session_id: str
    request_id: str
    fingerprint: str
    request_json: str
    state: str
    survey_id: Optional[int]
    invitation: Optional[str]
    preview_url: Optional[str]
    response_json: Optional[str]
    operation_owner: Optional[str]
    lease_until: Optional[float]
    fence_version: int


class PreviewFenceLost(Exception):
    """A stale preview worker attempted a new engine mutation after ownership changed."""


class _FencedTransport:
    _MUTATIONS = frozenset({
        "import_survey", "set_survey_properties", "activate_survey", "activate_tokens",
        "add_participants", "delete_survey",
    })

    def __init__(self, transport: Transport, owns: Callable[[], bool]):
        self._transport = transport
        self._owns = owns

    def __call__(self, payload: bytes) -> bytes:
        method = json.loads(payload.decode("utf-8")).get("method")
        if method in self._MUTATIONS and not self._owns():
            raise PreviewFenceLost("preview operation ownership changed")
        return self._transport(payload)


class PreviewOperationStore:
    """Durable preview operation registry, independent from formal publish receipts."""

    # Engine RPC transport permits 180 seconds; takeover starts only after a 30 second margin.
    LEASE_SECONDS = 210
    CLOSE_FOLLOW_SECONDS = 0.25

    def __init__(self, path: str, now: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        self._path = path
        self._now = now
        self._sleep = sleep
        self._lock = threading.Lock()
        self.fail_after_import_once = False  # deterministic crash boundary used by fault tests
        self.fail_after_activate_once = False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS preview_operation (
                    tenant_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    survey_id INTEGER,
                    invitation TEXT,
                    preview_url TEXT,
                    response_json TEXT,
                    operation_owner TEXT,
                    lease_until REAL,
                    fence_version INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (tenant_id, session_id, request_id)
                )
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(preview_operation)")}
            if "operation_owner" not in columns:
                db.execute("ALTER TABLE preview_operation ADD COLUMN operation_owner TEXT")
            if "lease_until" not in columns:
                db.execute("ALTER TABLE preview_operation ADD COLUMN lease_until REAL")
            if "fence_version" not in columns:
                db.execute("ALTER TABLE preview_operation ADD COLUMN fence_version INTEGER NOT NULL DEFAULT 0")

    def _connect(self):
        db = sqlite3.connect(self._path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def get(self, key: Tuple[str, str, str]) -> Optional[_PreviewOperation]:
        with self._connect() as db:
            row = db.execute("""
                SELECT tenant_id, session_id, request_id, fingerprint, request_json, state,
                       survey_id, invitation, preview_url, response_json, operation_owner, lease_until,
                       fence_version
                  FROM preview_operation
                 WHERE tenant_id=? AND session_id=? AND request_id=?
            """, key).fetchone()
        return _PreviewOperation(*row) if row else None

    def register(self, request: _PreviewRequest) -> _PreviewOperation:
        key = (request.tenant_id, request.session_id, request.request_id)
        request_json = json.dumps(request.__dict__, sort_keys=True, separators=(",", ":"))
        with self._lock, self._connect() as db:
            db.execute("""
                INSERT OR IGNORE INTO preview_operation
                    (tenant_id, session_id, request_id, fingerprint, request_json, state, updated_at)
                VALUES (?, ?, ?, ?, ?, 'creating', ?)
            """, key + (request.fingerprint, request_json, int(self._now())))
        return self.get(key)

    def claim(self, key: Tuple[str, str, str], allowed: Tuple[str, ...], running: str,
              owner: str) -> Optional[_PreviewOperation]:
        placeholders = ",".join("?" for _ in allowed)
        now = self._now()
        with self._lock, self._connect() as db:
            changed = db.execute("""
                UPDATE preview_operation
                   SET state=?, operation_owner=?, lease_until=?, fence_version=fence_version + 1,
                       updated_at=?
                 WHERE tenant_id=? AND session_id=? AND request_id=?
                   AND (state IN ({}) OR (state=? AND COALESCE(lease_until, 0) <= ?))
            """.format(placeholders),
                (running, owner, now + self.LEASE_SECONDS, int(now)) + key + allowed + (running, now,)
            ).rowcount
        return self.get(key) if changed == 1 else None

    def claim_close(self, key: Tuple[str, str, str], owner: str) -> Optional[_PreviewOperation]:
        now = self._now()
        with self._lock, self._connect() as db:
            changed = db.execute("""
                UPDATE preview_operation
                   SET state='closing', operation_owner=?, lease_until=?,
                       fence_version=fence_version + 1, updated_at=?
                 WHERE tenant_id=? AND session_id=? AND request_id=?
                   AND (state IN ('creating', 'ready', 'prepared', 'failed', 'cleanup_failed')
                        OR (state IN ('preparing', 'activating', 'closing')
                            AND COALESCE(lease_until, 0) <= ?))
            """, (owner, now + self.LEASE_SECONDS, int(now)) + key + (now,)).rowcount
        return self.get(key) if changed == 1 else None

    def owns(self, operation: _PreviewOperation, owner: str) -> bool:
        current = self.get((operation.tenant_id, operation.session_id, operation.request_id))
        return current is not None and current.operation_owner == owner \
            and current.fence_version == operation.fence_version

    def progress(self, operation: _PreviewOperation, owner: str, survey_id: int) -> _PreviewOperation:
        key = (operation.tenant_id, operation.session_id, operation.request_id)
        with self._lock, self._connect() as db:
            db.execute("""
                UPDATE preview_operation SET survey_id=?, updated_at=?
                 WHERE tenant_id=? AND session_id=? AND request_id=? AND operation_owner=?
                   AND fence_version=?
            """, (survey_id, int(self._now())) + key + (owner, operation.fence_version))
        return self.get(key)

    def settle(self, operation: _PreviewOperation, owner: str, state: str, survey_id=None,
               invitation=None, preview_url=None, response=None) -> _PreviewOperation:
        key = (operation.tenant_id, operation.session_id, operation.request_id)
        response_json = None if response is None else json.dumps(response, sort_keys=True, ensure_ascii=False)
        with self._lock, self._connect() as db:
            db.execute("""
                UPDATE preview_operation
                   SET state=?, survey_id=COALESCE(?, survey_id), invitation=COALESCE(?, invitation),
                       preview_url=COALESCE(?, preview_url), response_json=COALESCE(?, response_json),
                       operation_owner=NULL, lease_until=NULL, updated_at=?
                 WHERE tenant_id=? AND session_id=? AND request_id=? AND operation_owner=?
                   AND fence_version=?
            """, (state, survey_id, invitation, preview_url, response_json, int(self._now()))
                + key + (owner, operation.fence_version))
        return self.get(key)

    def abandon(self, operation: _PreviewOperation, owner: str) -> None:
        key = (operation.tenant_id, operation.session_id, operation.request_id)
        with self._lock, self._connect() as db:
            db.execute("""
                UPDATE preview_operation SET lease_until=0, updated_at=?
                 WHERE tenant_id=? AND session_id=? AND request_id=? AND operation_owner=?
                   AND fence_version=?
            """, (int(self._now()),) + key + (owner, operation.fence_version))

    def late_cleanup_failed(self, operation: _PreviewOperation, survey_id: int, detail: str) -> None:
        key = (operation.tenant_id, operation.session_id, operation.request_id)
        response = json.dumps({"status": "cleanup_failed", "error": "engine_error", "detail": detail},
                              sort_keys=True, ensure_ascii=False)
        with self._lock, self._connect() as db:
            db.execute("""
                UPDATE preview_operation
                   SET state='cleanup_failed', survey_id=?, response_json=?, updated_at=?
                 WHERE tenant_id=? AND session_id=? AND request_id=?
                   AND state='closed' AND operation_owner IS NULL
            """, (survey_id, response, int(self._now())) + key)

    def follow(self, key: Tuple[str, str, str], running: str) -> _PreviewOperation:
        deadline = time.monotonic() + self.LEASE_SECONDS + 5
        while time.monotonic() < deadline:
            operation = self.get(key)
            if operation.state != running or (operation.lease_until or 0) <= self._now():
                return operation
            self._sleep(0.01)
        return self.get(key)

    def follow_close(self, key: Tuple[str, str, str]) -> _PreviewOperation:
        deadline = time.monotonic() + self.CLOSE_FOLLOW_SECONDS
        operation = self.get(key)
        while operation.state == "closing" and time.monotonic() < deadline:
            self._sleep(0.01)
            operation = self.get(key)
        return operation


class LazyLoginClient(RemoteControlClient):
    """第一次真正需要会话时才登录：前置校验失败的发布一次引擎调用都没有。"""

    def __init__(self, transport: Transport, username: str, password: str):
        super().__init__(transport)
        self._username = username
        self._password = password

    def _session(self) -> str:
        if self.session_key is None:
            self.login(self._username, self._password)
        return super()._session()


def http_transport(engine: EngineConfig) -> Transport:
    return HttpTransport(engine.rpc_url, rpc_path="")


def http_policy_probe(engine: EngineConfig) -> Optional[PolicyProbe]:
    """访问策略的回读通道，从 RemoteControl 端点推出（ADR 0016 决定 4）。"""
    return HttpPolicyProbe.from_rpc_url(engine.rpc_url)


class PublishService:
    def __init__(
        self,
        engines: Mapping[str, EngineConfig],
        store: ResultStore,
        secret: bytes,
        transport_factory: TransportFactory = http_transport,
        now: Callable[[], float] = time.time,
        locks: Optional[InFlight] = None,
        policy_probe_factory: PolicyProbeFactory = http_policy_probe,
        preview_operations: Optional[PreviewOperationStore] = None,
        preview_public_url: str = "http://127.0.0.1:8080",
    ):
        self._engines = engines
        self._store = store
        self._secret = secret
        self._transport_factory = transport_factory
        self._now = now
        self._locks = locks or InFlight()
        self._policy_probe_factory = policy_probe_factory
        preview_path = os.path.join(os.path.dirname(getattr(store, "_path", "") or "."),
                                    "preview-operations.sqlite3")
        self._preview_operations = preview_operations or PreviewOperationStore(preview_path)
        self._preview_public_url = preview_public_url.rstrip("/")
        self._redactions = _redaction_forms(engine.password for engine in engines.values())

    # ------------------------------------------------------------ 入口

    def publish(self, headers: Mapping[str, str], body: bytes) -> Response:
        rejected = self._authenticate("publish", headers, body)
        if rejected is not None:
            return rejected
        try:
            request = parse_request(body)
        except InvalidRequest as error:
            log.info("rejected publish request: %s", error)
            return self._invalid()
        return self._publish(request)

    def preview(self, headers: Mapping[str, str], body: bytes) -> Response:
        """Prepare an inactive SID and durably register it before activation."""
        rejected = self._authenticate("preview", headers, body)
        if rejected is not None:
            return rejected
        try:
            request = _parse_preview_request(body, self._now())
        except InvalidRequest as error:
            log.info("rejected preview request: %s", error)
            return self._invalid()
        return self._preview(request)

    def activate_preview(self, headers: Mapping[str, str], body: bytes) -> Response:
        rejected = self._authenticate("preview/activate", headers, body)
        if rejected is not None:
            return rejected
        try:
            identity = _parse_preview_identity(body)
        except InvalidRequest:
            return self._invalid()
        return self._activate_preview(identity)

    def preview_status(self, headers: Mapping[str, str], body: bytes) -> Response:
        rejected = self._authenticate("preview/status", headers, body)
        if rejected is not None:
            return rejected
        try:
            identity = _parse_preview_identity(body)
        except InvalidRequest:
            return self._invalid()
        operation = self._preview_operations.get(identity)
        return self._operation_response(operation) if operation else self._json(404, {"error": "not_found"})

    def close_preview(self, headers: Mapping[str, str], body: bytes) -> Response:
        rejected = self._authenticate("preview/close", headers, body)
        if rejected is not None:
            return rejected
        try:
            identity = _parse_preview_identity(body)
        except InvalidRequest:
            return self._invalid()
        operation = self._preview_operations.get(identity)
        if operation is None:
            return self._json(404, {"error": "not_found"})
        if operation.state == "closed":
            return self._operation_response(operation)
        owner = str(uuid.uuid4())
        claimed = self._preview_operations.claim_close(identity, owner)
        if claimed is None:
            current = self._preview_operations.get(identity)
            if current is not None and current.state == "closing":
                current = self._preview_operations.follow_close(identity)
            if current is not None and current.state == "closed":
                return self._operation_response(current)
            return self._preview_busy(current or operation)
        return self._close_claimed_preview(claimed, owner)

    def preview_access(self, query: Mapping[str, List[str]]) -> Response:
        required = {"tenant", "session", "request", "sid", "expires", "sig"}
        if set(query) != required or any(len(query[name]) != 1 for name in required):
            return self._json(400, {"error": "invalid_request"})
        values = {name: query[name][0] for name in required}
        signed = _preview_access_payload(values)
        expected = hmac.new(self._secret, signed, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, values["sig"]):
            return self._json(403, {"error": "invalid_signature"})
        try:
            expiry = datetime.fromisoformat(values["expires"].replace("Z", "+00:00")).timestamp()
            sid = int(values["sid"])
        except (ValueError, TypeError):
            return self._json(400, {"error": "invalid_request"})
        if expiry <= self._now():
            return self._json(410, {"error": "preview_expired"})
        operation = self._preview_operations.get((values["tenant"], values["session"], values["request"]))
        if operation is None or operation.state != "ready" or operation.survey_id != sid:
            return self._json(410, {"error": "preview_unavailable"})
        request = _request_from_operation(operation)
        engine = self._engines[request.engine_instance_id]
        location = _engine_preview_url(engine, sid, request.definition.get("language", "zh-Hans"),
                                       operation.invitation or "")
        return Response(302, b"", {"Location": location})

    def close(self, headers: Mapping[str, str], body: bytes) -> Response:
        """``POST /v1/close``：让一份被取代的已发布问卷不再接收新答卷（设过期，不停用、不删除）。"""
        rejected = self._authenticate("close", headers, body)
        if rejected is not None:
            return rejected
        try:
            request = parse_close_request(body)
        except InvalidRequest as error:
            log.info("rejected close request: %s", error)
            return self._invalid()
        engine = self._engines.get(request.engine_instance_id)
        if engine is None:
            return self._json(404, {"error": "unknown_engine_instance"})

        key = ("close", engine.instance_id, request.survey_id)
        if not self._locks.try_acquire(key):
            return self._json(409, {"status": "conflict", "error": "close_in_progress"})
        try:
            return self._with_engine(engine, request.request_id, "close sid={}".format(request.survey_id),
                                     lambda client: self._close(client, request.survey_id))
        finally:
            self._locks.release(key)

    def revoke_participant(self, headers: Mapping[str, str], body: bytes) -> Response:
        """``POST /v1/participants/revoke``：删掉引擎里的那一行参与者，让邀请码立刻失效（契约 v1.4）。

        与 ``/v1/close`` 同样天然幂等（引擎里本来就没有那个码＝已撤销），因此不进
        ``requestId`` 结果存档。任何非 200 平台都必须当成"**没有**撤销"。
        """
        rejected = self._authenticate("participants/revoke", headers, body)
        if rejected is not None:
            return rejected
        try:
            request = parse_revoke_request(body)
        except InvalidRequest as error:
            log.info("rejected revoke request: %s", error)
            return self._invalid()
        engine = self._engines.get(request.engine_instance_id)
        if engine is None:
            return self._json(404, {"error": "unknown_engine_instance"})

        # 同一个码同一时刻只处理一个请求：两次并发撤销会让第二次看到"删了一半"。
        # 锁是**进程内**的（与 /v1/publish、/v1/close 同一个 InFlight，契约 v1.1
        # 「网关的并发锁在进程内，只能单副本运行」）。多副本下两个进程可能同时进来，
        # 后一个会拿到 502 而不是 409——结局仍然安全：删两次的第二次拿不到 Deleted，
        # 平台据此不写 revoked_at，重试即收敛。
        # 键里放摘要而不是令牌本身：进程级的集合不该长期握着一份凭据。
        key = ("revoke", engine.instance_id, request.survey_id,
               hashlib.sha256(request.participant_token.encode("utf-8")).hexdigest())
        if not self._locks.try_acquire(key):
            return self._json(409, {"status": "conflict", "error": "revoke_in_progress"})
        try:
            return self._with_engine(
                engine, request.request_id, "revoke sid={}".format(request.survey_id),
                lambda client: self._revoke(client, request.survey_id, request.participant_token))
        finally:
            self._locks.release(key)

    def drift_check(self, headers: Mapping[str, str], body: bytes) -> Response:
        """``POST /v1/drift-check``：只读回引擎，报告与期望指纹是否一致。"""
        rejected = self._authenticate("drift-check", headers, body)
        if rejected is not None:
            return rejected
        try:
            request = parse_drift_request(body)
        except InvalidRequest as error:
            log.info("rejected drift-check request: %s", error)
            return self._invalid()
        engine = self._engines.get(request.engine_instance_id)
        if engine is None:
            return self._json(404, {"error": "unknown_engine_instance"})

        def run(client: RemoteControlClient) -> Response:
            result = check_published_survey(
                client, request.survey_id, request.expected_fingerprint, request.binding)
            level = logging.WARNING if result.drifted else logging.INFO
            log.log(level, "drift-check %s sid=%s: %s -> %s (%d issue(s))", engine.instance_id,
                    request.survey_id, request.expected_fingerprint, result.current_fingerprint,
                    len(result.issues))
            status = "drift" if result.drifted else "match"
            return self._json(200, {"status": status, "result": result.to_dict()})

        return self._with_engine(engine, "-", "drift-check sid={}".format(request.survey_id), run)

    # ------------------------------------------------------------ 公共零件

    def _authenticate(self, operation: str, headers: Mapping[str, str], body: bytes) -> Optional[Response]:
        """HMAC 与 Content-Type；不通过时返回应答（401 / 400），通过时返回 None。"""
        lowered = {str(name).lower(): value for name, value in headers.items()}
        try:
            verify(
                self._secret,
                lowered.get(TIMESTAMP_HEADER.lower()),
                lowered.get(SIGNATURE_HEADER.lower()),
                body,
                self._now(),
            )
        except AuthError as error:
            log.warning("rejected unauthenticated %s request: %s", operation, error.reason)
            return self._json(401, {"error": error.reason})
        if not _is_json_media_type(lowered.get("content-type", "")):
            log.info("rejected %s request: content type %r", operation, lowered.get("content-type"))
            return self._invalid()
        return None

    def _with_engine(self, engine: EngineConfig, request_id: str, label: str,
                     action: Callable[[RemoteControlClient], Response]) -> Response:
        """在一次引擎会话里执行 action：引擎失败 502（脱敏），网关自身缺陷 500，会话总会释放。"""
        client = LazyLoginClient(self._transport_factory(engine), engine.user, engine.password)
        try:
            return action(client)
        except (CloseError, RevokeError) as error:
            log.warning("request %s: %s on %s failed: %s", request_id, label, engine.instance_id,
                        self._redact(str(error)))
            return self._json(502, {"status": "failed", "error": error.code, "detail": error.detail})
        except RpcError as error:
            log.warning("request %s: %s on %s failed: %s", request_id, label, engine.instance_id,
                        self._redact(str(error)))
            return self._json(502, {"status": "failed", "error": "engine_error", "detail": str(error)})
        except Exception:  # noqa: BLE001 — 网关自身缺陷：记日志，对外只说 internal_error
            log.exception("request %s: unexpected error during %s on %s", request_id, label, engine.instance_id)
            return self._json(500, {"error": "internal_error"})
        finally:
            _logout(client, request_id)

    def _revoke(self, client: RemoteControlClient, survey_id: int, token: str) -> Response:
        result = revoke_participant(client, survey_id, token)
        return self._json(200, {"status": "revoked", "result": result.to_dict()})

    def _close(self, client: RemoteControlClient, survey_id: int) -> Response:
        now = datetime.fromtimestamp(self._now(), tz=timezone.utc)
        result = close_survey(client, survey_id, now)
        log.info("closed sid=%s (expires=%s, alreadyClosed=%s)", survey_id, result.expires, result.already_closed)
        return self._json(200, {"status": "closed", "result": result.to_dict()})

    def _preview_client(self, engine: EngineConfig, operation: _PreviewOperation,
                        owner: str) -> LazyLoginClient:
        transport = _FencedTransport(
            self._transport_factory(engine),
            lambda: self._preview_operations.owns(operation, owner),
        )
        return LazyLoginClient(transport, engine.user, engine.password)

    def _preview_busy(self, operation: _PreviewOperation) -> Response:
        remaining = max(1, math.ceil((operation.lease_until or self._now() + 1) - self._now()))
        return Response(
            409,
            self._json(409, {
                "status": "busy", "error": "preview_in_progress", "operationState": operation.state,
            }).body,
            {"Retry-After": str(min(remaining, PreviewOperationStore.LEASE_SECONDS))},
        )

    def _close_claimed_preview(self, operation: _PreviewOperation, owner: str) -> Response:
        request = _request_from_operation(operation)
        engine = self._engines.get(request.engine_instance_id)
        if engine is None:
            return self._preview_cleanup_failed(operation, owner, "unknown_engine_instance")
        client = self._preview_client(engine, operation, owner)
        try:
            rows = client.list_surveys()
            marker = _preview_marker(request)
            marker_ids = [int(row["sid"]) for row in rows
                          if str(row.get("surveyls_title") or row.get("title") or "") == marker]
            if len(marker_ids) > 1:
                raise RpcError("list_surveys", "multiple surveys share preview marker")
            marker_sid = marker_ids[0] if marker_ids else None
            if operation.survey_id is not None and marker_sid is not None \
                    and operation.survey_id != marker_sid:
                raise RpcError("list_surveys", "durable SID does not match preview marker")
            survey_id = operation.survey_id or marker_sid
            visible_ids = {int(row["sid"]) for row in rows}
            if survey_id is None or survey_id not in visible_ids:
                operation = self._preview_operations.settle(
                    operation, owner, "closed", response={"status": "closed"}
                )
                return self._operation_response(operation)
            operation = self._preview_operations.progress(operation, owner, survey_id)
            if not self._preview_operations.owns(operation, owner):
                return self._preview_busy(operation)
            active = str(client.get_survey_properties(survey_id).get("active", "N")) == "Y"
            if active:
                participant_email = "preview-{}@invalid.local".format(request.session_id)
                try:
                    client.get_participant_properties(survey_id, {"email": participant_email}, ["token"])
                except RpcError:
                    pass
                self._close(client, survey_id)
            else:
                client.delete_survey(survey_id)
            operation = self._preview_operations.settle(
                operation, owner, "closed", response={"status": "closed"}
            )
            return self._operation_response(operation)
        except PreviewFenceLost:
            current = self._preview_operations.get(
                (operation.tenant_id, operation.session_id, operation.request_id)
            )
            return self._operation_response(current) if current.state == "closed" else self._preview_busy(current)
        except (CloseError, RpcError) as error:
            return self._preview_cleanup_failed(operation, owner, self._redact(str(error)))
        except Exception as error:  # noqa: BLE001 - persist a retryable cleanup state before hiding details
            log.exception("preview close reconciliation failed for %s", request.request_id)
            return self._preview_cleanup_failed(operation, owner, self._redact(str(error)))
        finally:
            _logout(client, request.request_id)

    def _preview_cleanup_failed(self, operation: _PreviewOperation, owner: str, detail: str) -> Response:
        payload = {"status": "cleanup_failed", "error": "engine_error", "detail": detail}
        operation = self._preview_operations.settle(
            operation, owner, "cleanup_failed", response=payload
        )
        if operation.state == "cleanup_failed":
            return self._json(502, payload)
        if operation.state == "closed":
            return self._operation_response(operation)
        return self._preview_busy(operation)

    # ------------------------------------------------------------ 流程

    def _publish(self, request: PublishRequest) -> Response:
        stored = self._store.get(request.request_id)
        if stored is not None:
            return self._replay(request, stored)

        engine = self._engines.get(request.engine_instance_id)
        if engine is None:
            log.info("request %s: unknown engine instance", request.request_id)
            return self._json(404, {"error": "unknown_engine_instance"})
        try:
            definition = SurveyDefinition.from_dict(request.definition)
        except DefinitionError as error:
            log.info("request %s: malformed definition: %s", request.request_id, error)
            return self._invalid()

        keys = [("request", request.request_id), ("definition", engine.instance_id, definition.uuid)]
        acquired = self._acquire_all(keys)
        if acquired is None:
            log.info(
                "request %s: publish of %s on %s already in progress",
                request.request_id, definition.uuid, engine.instance_id,
            )
            return self._json(409, {"status": "conflict", "error": "publish_in_progress"})
        try:
            stored = self._store.get(request.request_id)
            if stored is not None:
                return self._replay(request, stored)
            attempt = self._run(request, engine, definition)
            stored = self._store.put(request.request_id, request.fingerprint, attempt.response.status,
                                     attempt.response.body, attempt.survey_id)
            return Response(stored.status, stored.body)
        finally:
            for key in acquired:
                self._locks.release(key)

    def _preview(self, request: _PreviewRequest) -> Response:
        operation = self._preview_operations.register(request)
        if operation.fingerprint != request.fingerprint:
            return self._invalid()
        if operation.state in ("prepared", "ready", "failed", "closed"):
            return self._operation_response(operation)
        engine = self._engines.get(request.engine_instance_id)
        if engine is None:
            return self._json(404, {"error": "unknown_engine_instance"})
        try:
            preview_definition = dict(request.definition)
            preview_definition["participants"] = [{"ref": "preview-" + request.generation}]
            definition = SurveyDefinition.from_dict(preview_definition)
        except DefinitionError as error:
            log.info("request %s: malformed preview definition: %s", request.request_id, error)
            return self._invalid()

        key = (request.tenant_id, request.session_id, request.request_id)
        while True:
            owner = str(uuid.uuid4())
            claimed = self._preview_operations.claim(key, ("creating",), "preparing", owner)
            if claimed is not None:
                return self._prepare_preview(claimed, owner, request, engine, definition)
            operation = self._preview_operations.follow(key, "preparing")
            if operation.state != "preparing":
                return self._operation_response(operation)

    def _prepare_preview(self, operation: _PreviewOperation, owner: str, request: _PreviewRequest,
                         engine: EngineConfig, definition: SurveyDefinition) -> Response:
        client = self._preview_client(engine, operation, owner)
        try:
            publisher = Publisher(client, engine_instance=engine.instance_id,
                                  policy_probe=self._policy_probe_factory(engine))
            result = PublishResult(survey_id=operation.survey_id)
            compiled = publisher._stage_validate_and_compile(definition, result)
            if compiled is None:
                payload = {"status": "failed", "result": result.to_dict()}
                operation = self._preview_operations.settle(operation, owner, "failed", response=payload)
                return self._operation_response(operation, 422)
            definition = expand_scoring(definition)
            if result.survey_id is None:
                result.survey_id = _find_preview_marker(client, _preview_marker(request))
            if result.survey_id is None:
                result.survey_id = client.import_survey(compiled.lss, _preview_marker(request))
                if self._preview_operations.fail_after_import_once:
                    self._preview_operations.fail_after_import_once = False
                    return self._json(503, {"status": "creating", "error": "result_unknown"})
            if not self._preview_operations.owns(operation, owner):
                self._delete_late_preview(engine, operation, result.survey_id)
                return self._operation_response(self._preview_operations.get(
                    (operation.tenant_id, operation.session_id, operation.request_id)
                ))
            operation = self._preview_operations.progress(operation, owner, result.survey_id)
            if publisher._stage_apply(definition, compiled, result) is None:
                payload = {"status": "failed", "result": result.to_dict()}
                operation = self._preview_operations.settle(operation, owner, "failed", response=payload)
                return self._operation_response(operation, 502)
            payload = {"status": "prepared", "result": _preview_result(operation, request)}
            operation = self._preview_operations.settle(operation, owner, "prepared", response=payload)
            return self._operation_response(operation)
        except PreviewFenceLost:
            return self._operation_response(self._preview_operations.get(
                (operation.tenant_id, operation.session_id, operation.request_id)
            ))
        except RpcError as error:
            log.warning("preview prepare result unknown for %s: %s", request.request_id, self._redact(str(error)))
            self._preview_operations.abandon(operation, owner)
            return self._json(503, {"status": "creating", "error": "result_unknown"})
        except Exception:  # noqa: BLE001
            log.exception("preview prepare result unknown for %s", request.request_id)
            self._preview_operations.abandon(operation, owner)
            return self._json(503, {"status": "creating", "error": "result_unknown"})
        finally:
            _logout(client, request.request_id)

    def _delete_late_preview(self, engine: EngineConfig, operation: _PreviewOperation,
                             survey_id: int) -> None:
        client = LazyLoginClient(self._transport_factory(engine), engine.user, engine.password)
        try:
            client.delete_survey(survey_id)
        except RpcError as error:
            self._preview_operations.late_cleanup_failed(
                operation, survey_id, self._redact(str(error))
            )
        finally:
            _logout(client, operation.request_id)

    def _activate_preview(self, identity: Tuple[str, str, str]) -> Response:
        operation = self._preview_operations.get(identity)
        if operation is None:
            return self._json(404, {"error": "not_found"})
        while True:
            if operation.state in ("ready", "failed", "closed"):
                return self._operation_response(operation)
            owner = str(uuid.uuid4())
            claimed = self._preview_operations.claim(identity, ("prepared",), "activating", owner)
            if claimed is not None:
                operation = claimed
                break
            operation = self._preview_operations.follow(identity, operation.state)
            if operation.state == "creating":
                return self._operation_response(operation, 503)
        if operation.survey_id is None:
            self._preview_operations.abandon(operation, owner)
            return self._json(503, {"status": "creating", "error": "result_unknown"})
        request = _request_from_operation(operation)
        engine = self._engines.get(request.engine_instance_id)
        if engine is None:
            return self._json(404, {"error": "unknown_engine_instance"})
        client = self._preview_client(engine, operation, owner)
        try:
            preview_definition = dict(request.definition)
            participant_email = "preview-{}@invalid.local".format(request.session_id)
            preview_definition["participants"] = [{
                "ref": "preview-" + request.generation,
                "email": participant_email,
            }]
            raw_definition = SurveyDefinition.from_dict(preview_definition)
            publisher = Publisher(client, engine_instance=engine.instance_id,
                                  policy_probe=self._policy_probe_factory(engine))
            result = PublishResult(survey_id=operation.survey_id)
            compiled = publisher._stage_validate_and_compile(raw_definition, result)
            definition = expand_scoring(raw_definition)
            if compiled is None:
                payload = {"status": "failed", "result": result.to_dict()}
                operation = self._preview_operations.settle(operation, owner, "failed", response=payload)
                return self._operation_response(operation, 502)
            active = str(client.get_survey_properties(operation.survey_id).get("active", "N")) == "Y"
            if active:
                participant = client.get_participant_properties(
                    operation.survey_id, {"email": participant_email}, ["token"]
                )
                result.invitations = [{"ref": "preview-" + request.generation,
                                       "token": str(participant["token"])}]
                result.steps.append(StageStep("activate", True, "reconciled"))
            elif not publisher._stage_activate(definition, result):
                payload = {"status": "failed", "result": result.to_dict()}
                operation = self._preview_operations.settle(operation, owner, "failed", response=payload)
                return self._operation_response(operation, 502)
            if self._preview_operations.fail_after_activate_once:
                self._preview_operations.fail_after_activate_once = False
                return self._json(503, {"status": "creating", "error": "result_unknown"})
            verification = publisher._stage_verify(definition, compiled, result)
            if verification is None:
                payload = {"status": "failed", "result": result.to_dict()}
                operation = self._preview_operations.settle(operation, owner, "failed", response=payload)
                return self._operation_response(operation, 502)
            result.binding = BindingRecord(engine.instance_id, operation.survey_id, definition.uuid,
                                           compiled.compiler_version, FINGERPRINT_VERSION,
                                           verification.fingerprint, definition.language,
                                           datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                           verification.bindings)
            invitation = result.invitations[0]["token"] if result.invitations else ""
            url = _preview_url(self._preview_public_url, request, operation.survey_id, self._secret)
            payload = {"status": "ready", "result": dict(_preview_result(operation, request),
                                                              previewUrl=url,
                                                              binding=result.binding.to_dict())}
            operation = self._preview_operations.settle(operation, owner, "ready", invitation=invitation,
                                                        preview_url=url, response=payload)
            return self._operation_response(operation)
        except PreviewFenceLost:
            return self._operation_response(self._preview_operations.get(identity))
        except (RpcError, DefinitionError) as error:
            log.warning("preview activation result unknown for %s: %s", request.request_id,
                        self._redact(str(error)))
            self._preview_operations.abandon(operation, owner)
            return self._json(503, {"status": "creating", "error": "result_unknown"})
        finally:
            _logout(client, request.request_id)

    def _operation_response(self, operation: Optional[_PreviewOperation], status: int = 200) -> Response:
        if operation is None:
            return self._json(404, {"error": "not_found"})
        if operation.response_json:
            return Response(status, operation.response_json.encode("utf-8"))
        return self._json(status, {"status": operation.state})

    def _run(self, request: PublishRequest, engine: EngineConfig, definition: SurveyDefinition) -> _Attempt:
        started = time.monotonic()
        client = LazyLoginClient(self._transport_factory(engine), engine.user, engine.password)
        try:
            result = Publisher(
                client, engine_instance=engine.instance_id, policy_probe=self._policy_probe_factory(engine)
            ).publish(definition)
        except Exception:  # noqa: BLE001 — 网关自身缺陷：记日志，对外只说 internal_error
            log.exception(
                "request %s: unexpected error publishing %s on %s; engine state unknown",
                request.request_id, definition.uuid, engine.instance_id,
            )
            return _Attempt(self._json(500, {"error": "internal_error"}))
        finally:
            _logout(client, request.request_id)

        status, label = classify(result)
        self._log_outcome(request, engine, definition, result, status, time.monotonic() - started)
        return _Attempt(self._json(status, {"status": label, "result": result.to_dict()}),
                        surviving_survey_id(result))

    def _replay(self, request: PublishRequest, stored: Lookup) -> Response:
        """指纹先对：请求体不同一律 400，哪怕存档已经只剩墓碑。"""
        if stored.fingerprint != request.fingerprint:
            log.warning("request %s: requestId reused with a different body", request.request_id)
            return self._invalid()
        if isinstance(stored, ExpiredResult):
            return self._expired(request, stored)
        log.info("request %s: replaying stored %s", request.request_id, stored.status)
        return Response(stored.status, stored.body)

    def _expired(self, request: PublishRequest, stored: ExpiredResult) -> Response:
        """回执过了留存期：给一个明确的终局，平台不能把它当成"没发过"再发一次。"""
        log.error(
            "request %s: the stored result aged out (original http %s, engine sid=%s, stored at %s, "
            "carried invitation codes=%s); the platform has to settle this publish by hand",
            request.request_id, stored.original_status, stored.survey_id, _utc(stored.created_at),
            stored.held_codes,
        )
        return self._json(410, {
            "status": "expired",
            "error": "result_expired",
            "expired": {
                "originalStatus": stored.original_status,
                "createdAt": _utc(stored.created_at),
                "retainedSeconds": self._store.lifetime_of(stored.held_codes),
                "surveyId": stored.survey_id,
                # 带过邀请码的回执按短窗口过期（契约 v1.3）。平台据此知道
                # "码已经不在了、必须重新签发"，而不是"只是一份过期的回执"。
                "heldInvitationCodes": stored.held_codes,
            },
        })

    def _acquire_all(self, keys: List[Tuple[str, ...]]) -> Optional[List[Tuple[str, ...]]]:
        acquired = []
        for key in keys:
            if not self._locks.try_acquire(key):
                for held in acquired:
                    self._locks.release(held)
                return None
            acquired.append(key)
        return acquired

    # ------------------------------------------------------------ 响应

    def _invalid(self) -> Response:
        return self._json(400, {"error": "invalid_request"})

    def _json(self, status: int, payload: Dict[str, Any]) -> Response:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return Response(status, self._redact(text).encode("utf-8"))

    def _redact(self, text: str) -> str:
        for secret in self._redactions:
            text = text.replace(secret, REDACTED)
        return text

    def _log_outcome(
        self,
        request: PublishRequest,
        engine: EngineConfig,
        definition: SurveyDefinition,
        result: PublishResult,
        status: int,
        seconds: float,
    ) -> None:
        level = logging.INFO if status == 200 else logging.WARNING
        log.log(
            level,
            "request %s: %s on %s -> %s (stage=%s sid=%s rolledBack=%s orphan=%s) in %.1fs",
            request.request_id, definition.uuid, engine.instance_id, status,
            result.failed_stage, result.survey_id, result.rolled_back, result.orphan_survey_id, seconds,
        )
        for failure in result.failures:
            log.log(level, "request %s: %s", request.request_id, self._redact(failure))
        if result.orphan_survey_id is not None:
            log.error(
                "request %s: ORPHAN survey sid=%s left on %s, manual cleanup required",
                request.request_id, result.orphan_survey_id, engine.instance_id,
            )


def surviving_survey_id(result: PublishResult) -> Optional[int]:
    """回执过期后仍可能留在引擎里的那个 sid：发布成功的、或回滚也失败的孤儿。

    干净回滚掉的 502 不算——那份问卷已经不在了，报出来只会误导人工清理。
    """
    if result.orphan_survey_id is not None:
        return result.orphan_survey_id
    return result.survey_id if result.ok else None


def _utc(epoch_seconds: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_seconds))


def classify(result: PublishResult) -> Tuple[int, str]:
    """PublishResult → (HTTP 状态, status 标签)，见契约响应表。"""
    if result.ok:
        return 200, "published"
    if result.failed_stage in _REJECTED_STAGES and result.survey_id is None:
        return 422, "rejected"
    return 502, "failed"


def _redaction_forms(passwords: Iterable[str]) -> Tuple[str, ...]:
    forms = set()
    for password in passwords:
        if not password:
            continue
        # RpcError 的消息是引擎应答的 Python repr，所以 repr 形式也要盖住
        for form in (password, repr(password)[1:-1]):
            forms.add(form)
            forms.add(json.dumps(form, ensure_ascii=False)[1:-1])
            forms.add(json.dumps(form, ensure_ascii=True)[1:-1])
    # 长的先替换，免得短口令把长口令切碎后漏掉一部分
    return tuple(sorted(forms, key=len, reverse=True))


def _is_json_media_type(value: str) -> bool:
    return value.split(";", 1)[0].strip().lower() == "application/json"


def _parse_preview_request(body: bytes, now: float) -> _PreviewRequest:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise InvalidRequest("body is not UTF-8 JSON: {}".format(error)) from None
    if not isinstance(payload, dict) or set(payload) != _PREVIEW_FIELDS:
        raise InvalidRequest("preview body has unexpected fields")
    request_id = payload["requestId"]
    tenant_id = payload["tenantId"]
    session_id = payload["sessionId"]
    instance_id = payload["engineInstanceId"]
    generation = payload["generation"]
    expires_at = payload["expiresAt"]
    definition = payload["definition"]
    for name, value in (("tenantId", tenant_id), ("sessionId", session_id), ("requestId", request_id)):
        if not isinstance(value, str) or not _UUID_TEXT.match(value):
            raise InvalidRequest("{} must be a UUID".format(name))
    if not isinstance(instance_id, str) or not 0 < len(instance_id) <= 128:
        raise InvalidRequest("engineInstanceId must be a non-empty string")
    if not isinstance(generation, str) or not _PREVIEW_GENERATION.match(generation):
        raise InvalidRequest("generation must be a preview generation")
    if not isinstance(expires_at, str):
        raise InvalidRequest("expiresAt must be an RFC3339 timestamp")
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        raise InvalidRequest("expiresAt must be an RFC3339 timestamp") from None
    if expiry.tzinfo is None:
        raise InvalidRequest("expiresAt must include a timezone")
    remaining = expiry.timestamp() - now
    if remaining <= 0 or remaining > 3600:
        raise InvalidRequest("expiresAt must be in the next 3600 seconds")
    if not isinstance(definition, dict):
        raise InvalidRequest("definition must be a JSON object")
    canonical = json.dumps(
        {"tenantId": tenant_id, "sessionId": session_id, "requestId": request_id,
         "engineInstanceId": instance_id, "definition": definition, "generation": generation,
         "expiresAt": expires_at},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return _PreviewRequest(
        tenant_id.lower(), session_id.lower(), request_id.lower(), instance_id, definition, generation, expires_at,
        hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def _parse_preview_identity(body: bytes) -> Tuple[str, str, str]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise InvalidRequest(str(error)) from None
    if not isinstance(payload, dict) or set(payload) != _PREVIEW_IDENTITY_FIELDS:
        raise InvalidRequest("preview identity has unexpected fields")
    values = tuple(payload[name] for name in ("tenantId", "sessionId", "requestId"))
    if any(not isinstance(value, str) or not _UUID_TEXT.match(value) for value in values):
        raise InvalidRequest("preview identity values must be UUIDs")
    return tuple(value.lower() for value in values)


def _request_from_operation(operation: _PreviewOperation) -> _PreviewRequest:
    return _PreviewRequest(**json.loads(operation.request_json))


def _preview_marker(request: _PreviewRequest) -> str:
    return "preview:{}:{}:{}".format(request.tenant_id, request.session_id, request.request_id)


def _find_preview_marker(client: RemoteControlClient, marker: str) -> Optional[int]:
    matches = [row for row in client.list_surveys()
               if str(row.get("surveyls_title") or row.get("title") or "") == marker]
    if len(matches) > 1:
        raise RpcError("list_surveys", "multiple surveys share preview marker")
    if not matches:
        return None
    return int(matches[0].get("sid"))


def _preview_result(operation: _PreviewOperation, request: _PreviewRequest) -> Dict[str, Any]:
    return {
        "surveyId": operation.survey_id,
        "engineInstanceId": request.engine_instance_id,
        "generation": request.generation,
        "expiresAt": request.expires_at,
    }


def _preview_access_payload(values: Mapping[str, str]) -> bytes:
    return "\n".join(values[name] for name in ("tenant", "session", "request", "sid", "expires")).encode("utf-8")


def _preview_url(public_base: str, request: _PreviewRequest, survey_id: int, secret: bytes) -> str:
    values = {
        "tenant": request.tenant_id,
        "session": request.session_id,
        "request": request.request_id,
        "sid": str(survey_id),
        "expires": request.expires_at,
    }
    values["sig"] = hmac.new(secret, _preview_access_payload(values), hashlib.sha256).hexdigest()
    return public_base + "/v1/preview/access?" + urlencode(values)


def _engine_preview_url(engine: EngineConfig, survey_id: int, language: str, invitation: str) -> str:
    marker = "/index.php/admin/remotecontrol"
    base = engine.rpc_url.split(marker, 1)[0].rstrip("/")
    query = urlencode({"newtest": "Y", "lang": language, "token": invitation})
    return "{}/index.php/{}?{}".format(base, survey_id, query)


def _logout(client: RemoteControlClient, request_id: str) -> None:
    try:
        client.logout()
    except Exception:  # noqa: BLE001 — 释放会话失败不影响发布结论
        log.warning("request %s: releasing the engine session failed", request_id, exc_info=True)
