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

口令防线：响应体在出门前把所有已配置的引擎口令（原文、repr 与 JSON 转义形式）替换成
``***``，即使引擎把口令写进了错误信息也不会外泄。

契约 v1.2 增补的 ``POST /v1/close`` 与 ``POST /v1/drift-check`` 走同样的认证、实例解析与口令防线；
两者天然幂等（收口重复无害、漂移检查只读），不进 requestId 结果存档。
"""

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from .auth import SIGNATURE_HEADER, TIMESTAMP_HEADER, AuthError, verify
from .close import CloseError, close_survey
from .drift_check import check_published_survey
from .engines import EngineConfig
from .model import DefinitionError, SurveyDefinition
from .policy.probe import HttpPolicyProbe, PolicyProbe
from .ops_request import parse_close_request, parse_drift_request
from .publish import PublishResult, Publisher
from .request import InvalidRequest, PublishRequest, parse_request
from .rpc import HttpTransport, RemoteControlClient, RpcError, Transport
from .store import InFlight, ResultStore, StoredResult

log = logging.getLogger("pubgw.service")

TransportFactory = Callable[[EngineConfig], Transport]
PolicyProbeFactory = Callable[[EngineConfig], Optional[PolicyProbe]]

REDACTED = "***"
_REJECTED_STAGES = frozenset({"validate", "compile"})


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes


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
    ):
        self._engines = engines
        self._store = store
        self._secret = secret
        self._transport_factory = transport_factory
        self._now = now
        self._locks = locks or InFlight()
        self._policy_probe_factory = policy_probe_factory
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
        except CloseError as error:
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

    def _close(self, client: RemoteControlClient, survey_id: int) -> Response:
        now = datetime.fromtimestamp(self._now(), tz=timezone.utc)
        result = close_survey(client, survey_id, now)
        log.info("closed sid=%s (expires=%s, alreadyClosed=%s)", survey_id, result.expires, result.already_closed)
        return self._json(200, {"status": "closed", "result": result.to_dict()})

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
            response = self._run(request, engine, definition)
            stored = self._store.put(request.request_id, request.fingerprint, response.status, response.body)
            return Response(stored.status, stored.body)
        finally:
            for key in acquired:
                self._locks.release(key)

    def _run(self, request: PublishRequest, engine: EngineConfig, definition: SurveyDefinition) -> Response:
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
            return self._json(500, {"error": "internal_error"})
        finally:
            _logout(client, request.request_id)

        status, label = classify(result)
        self._log_outcome(request, engine, definition, result, status, time.monotonic() - started)
        return self._json(status, {"status": label, "result": result.to_dict()})

    def _replay(self, request: PublishRequest, stored: StoredResult) -> Response:
        if stored.fingerprint != request.fingerprint:
            log.warning("request %s: requestId reused with a different body", request.request_id)
            return self._invalid()
        log.info("request %s: replaying stored %s", request.request_id, stored.status)
        return Response(stored.status, stored.body)

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


def _logout(client: RemoteControlClient, request_id: str) -> None:
    try:
        client.logout()
    except Exception:  # noqa: BLE001 — 释放会话失败不影响发布结论
        log.warning("request %s: releasing the engine session failed", request_id, exc_info=True)

