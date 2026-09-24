"""服务测试的共用零件：签名请求、带开关的假引擎、服务装配。"""

import json
import os
import threading
import uuid

from pubgw.auth import sign
from pubgw.engines import EngineConfig
from pubgw.service import PublishService
from pubgw.store import ResultStore

from .fakes import FakeEngine
from .fixtures import sample_definition, sample_payload

SECRET = b"0123456789abcdef0123456789abcdef-test"
NOW = 1_800_000_000
INSTANCE = "hd-engine-01"
#: 刻意带上 JSON 需要转义的字符：泄漏检查要同时覆盖原文与转义形式。
ENGINE_PASSWORD = 'Engine-Pw"9\\zq-7781'


class PasswordEchoEngine(FakeEngine):
    """最坏情况的引擎：登录失败时把收到的口令原样写进错误里。"""

    def _get_session_key(self, username, password, plugin=None):
        return {"status": "Invalid user name or password: {}".format(password)}


class BlockingEngine(FakeEngine):
    """import_survey 卡住直到测试放行，用来制造真实的并发窗口。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.entered = threading.Event()
        self.release = threading.Event()

    def _import_survey(self, key, data, kind, name=None):
        self.entered.set()
        if not self.release.wait(timeout=10):
            raise AssertionError("test never released the blocked import")
        return super()._import_survey(key, data, kind, name)


def engine_config(instance=INSTANCE):
    return EngineConfig(
        instance_id=instance,
        rpc_url="http://engine.invalid/index.php/admin/remotecontrol",
        user="admin",
        password=ENGINE_PASSWORD,
    )


class MovableClock:
    """可推进的时钟：留存期以天计，测试不可能真等。"""

    def __init__(self, value=NOW):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def make_store(state_dir, clock=None, retention=None):
    return ResultStore(
        os.path.join(state_dir, "results.sqlite3"), retention=retention, now=clock or (lambda: NOW)
    )


def make_service(engine, state_dir, clock=None, store=None):
    """store 单独传入：推进留存期的测试不能连带推进 HMAC 时间戳（±300 秒）。"""
    return PublishService(
        engines={INSTANCE: engine_config()},
        store=store if store is not None else make_store(state_dir),
        secret=SECRET,
        transport_factory=lambda config: engine.transport,
        now=clock or (lambda: NOW),
    )


def new_engine(engine_class=FakeEngine, **switches):
    return engine_class(sample_definition(), **switches)


def envelope(request_id=None, instance=INSTANCE, definition=None):
    return {
        "requestId": request_id or str(uuid.uuid4()),
        "engineInstanceId": instance,
        "definition": sample_payload() if definition is None else definition,
    }


def encode(payload):
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def signed_headers(body, timestamp=NOW, secret=SECRET):
    stamp = str(timestamp)
    return {
        "Content-Type": "application/json",
        "X-Pubgw-Timestamp": stamp,
        "X-Pubgw-Signature": sign(secret, stamp, body),
    }


def invalid_definition():
    payload = sample_payload()
    payload["groups"][0]["questions"][0]["answers"] = []
    return payload
