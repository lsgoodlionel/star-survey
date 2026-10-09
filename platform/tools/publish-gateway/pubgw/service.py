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
import re
import time
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
from .publish import PublishResult, Publisher
from .request import InvalidRequest, PublishRequest, parse_request
from .rpc import HttpTransport, RemoteControlClient, RpcError, Transport
from .store import ExpiredResult, InFlight, Lookup, ResultStore

log = logging.getLogger("pubgw.service")

TransportFactory = Callable[[EngineConfig], Transport]
PolicyProbeFactory = Callable[[EngineConfig], Optional[PolicyProbe]]

REDACTED = "***"
_REJECTED_STAGES = frozenset({"validate", "compile"})
_PREVIEW_FIELDS = frozenset({"requestId", "engineInstanceId", "definition", "generation", "expiresAt"})
_PREVIEW_GENERATION = re.compile(r"\Apreview-[a-z0-9][a-z0-9-]{2,55}\Z")
_UUID_TEXT = re.compile(r"\A[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes


@dataclass(frozen=True)
class _Attempt:
    """一次发布的应答，外加要写进墓碑的引擎 sid（回执过期后只剩它可查）。"""

    response: Response
    survey_id: Optional[int] = None


@dataclass(frozen=True)
class _PreviewRequest:
    request_id: str
    engine_instance_id: str
    definition: Mapping[str, Any]
    generation: str
    expires_at: str
    fingerprint: str


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

    def preview(self, headers: Mapping[str, str], body: bytes) -> Response:
        """``POST /v1/preview``：在独立 SID 上运行草稿，不登记正式发布绑定。"""
        rejected = self._authenticate("preview", headers, body)
        if rejected is not None:
            return rejected
        try:
            request = _parse_preview_request(body, self._now())
        except InvalidRequest as error:
            log.info("rejected preview request: %s", error)
            return self._invalid()
        return self._preview(request)

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
        stored = self._store.get(request.request_id)
        if stored is not None:
            return self._replay_preview(request, stored)
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

        keys = [("request", request.request_id), ("preview", engine.instance_id, request.generation)]
        acquired = self._acquire_all(keys)
        if acquired is None:
            return self._json(409, {"status": "conflict", "error": "preview_in_progress"})
        try:
            stored = self._store.get(request.request_id)
            if stored is not None:
                return self._replay_preview(request, stored)
            attempt = self._run_preview(request, engine, definition)
            stored = self._store.put(request.request_id, request.fingerprint, attempt.response.status,
                                     attempt.response.body, attempt.survey_id)
            return Response(stored.status, stored.body)
        finally:
            for key in acquired:
                self._locks.release(key)

    def _run_preview(self, request: _PreviewRequest, engine: EngineConfig,
                     definition: SurveyDefinition) -> _Attempt:
        client = LazyLoginClient(self._transport_factory(engine), engine.user, engine.password)
        try:
            result = Publisher(
                client, engine_instance=engine.instance_id, policy_probe=self._policy_probe_factory(engine)
            ).publish(definition)
        except Exception:  # noqa: BLE001
            log.exception("request %s: unexpected preview failure on %s", request.request_id, engine.instance_id)
            return _Attempt(self._json(500, {"error": "internal_error"}))
        finally:
            _logout(client, request.request_id)

        status, label = classify(result)
        if result.ok and result.survey_id is not None:
            invitation = result.invitations[0]["token"] if result.invitations else ""
            preview_url = _preview_url(engine, result.survey_id, request.generation, request.expires_at,
                                       definition.language, invitation, self._secret)
            body = {
                "status": "ready",
                "result": {
                    "surveyId": result.survey_id,
                    "engineInstanceId": engine.instance_id,
                    "generation": request.generation,
                    "expiresAt": request.expires_at,
                    "previewUrl": preview_url,
                    "binding": result.binding.to_dict() if result.binding is not None else None,
                },
            }
            return _Attempt(self._json(200, body), result.survey_id)
        return _Attempt(self._json(status, {"status": label, "result": result.to_dict()}),
                        surviving_survey_id(result))

    def _replay_preview(self, request: _PreviewRequest, stored: Lookup) -> Response:
        if stored.fingerprint != request.fingerprint:
            return self._invalid()
        if isinstance(stored, ExpiredResult):
            return self._json(410, {"status": "expired", "error": "result_expired"})
        return Response(stored.status, stored.body)

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
    instance_id = payload["engineInstanceId"]
    generation = payload["generation"]
    expires_at = payload["expiresAt"]
    definition = payload["definition"]
    if not isinstance(request_id, str) or not _UUID_TEXT.match(request_id):
        raise InvalidRequest("requestId must be a UUID")
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
        {"engineInstanceId": instance_id, "definition": definition, "generation": generation,
         "expiresAt": expires_at},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return _PreviewRequest(
        request_id.lower(), instance_id, definition, generation, expires_at,
        hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def _preview_url(engine: EngineConfig, survey_id: int, generation: str, expires_at: str,
                 language: str, invitation: str, secret: bytes) -> str:
    marker = "/index.php/admin/remotecontrol"
    base = engine.rpc_url.split(marker, 1)[0].rstrip("/")
    signed = "{}.{}.{}".format(generation, survey_id, expires_at).encode("utf-8")
    token = hmac.new(secret, signed, hashlib.sha256).hexdigest()
    query = urlencode({"newtest": "Y", "lang": language, "token": invitation,
                       "generation": generation, "expires": expires_at, "preview": token})
    return "{}/index.php/{}?{}".format(base, survey_id, query)


def _logout(client: RemoteControlClient, request_id: str) -> None:
    try:
        client.logout()
    except Exception:  # noqa: BLE001 — 释放会话失败不影响发布结论
        log.warning("request %s: releasing the engine session failed", request_id, exc_info=True)
