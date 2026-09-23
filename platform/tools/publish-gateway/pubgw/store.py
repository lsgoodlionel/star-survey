"""幂等结果存储（SQLite）与在途发布锁（进程内）。

- ``ResultStore``：``requestId`` → 首次响应。先写者赢，之后的写入一律返回首次结果；
  落在 ``PUBGW_STATE_DIR`` 下的 SQLite 文件里，重启不丢。
- ``Retention``：留存有界（契约 v1.3）。完整回执只留 ``result_seconds``，之后只剩一块
  **墓碑**（指纹＋原状态码＋落库时刻＋引擎 sid，正文丢弃），墓碑再留
  ``tombstone_seconds`` 后整行删除。窗口内重放逐字节还原首次应答；墓碑期内重放是
  410 ``result_expired``——一个明确的终局，平台不会误判成"没发过"而重复发布。
- ``PruneScheduler``：后台定期清理（只删行，不重写文件）。
- ``InFlight``：``(实例, definition.uuid)`` 同一时刻只允许一个发布，拿不到锁立即失败
  （不排队）——排队会让平台侧的超时与重试更难推理。

判过期**不**依赖清理有没有跑：``get`` 自己按 ``created_at`` 算，所以留存期是语义，
清理只是让库里不再留着那些行。

``reclaim``（VACUUM）刻意**不**在自动路径上：它要独占锁并重写整个文件，与正常请求抢锁时
``put`` 会等满 30 秒后抛错——而发布成功之后 ``put`` 失败就等于结果没落库，平台用同一
``requestId`` 重试会重复发布。回收磁盘因此是运维显式触发的维护动作
（``pubgw prune-results``），在维护窗口里做。

在途锁只在进程内有效：网关按单实例部署，多副本需要换成共享锁（见 README）。
"""

import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Hashable, Mapping, Optional, Protocol, Set, Union

log = logging.getLogger("pubgw.store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS publish_results (
    request_id  TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    status      INTEGER NOT NULL,
    body        BLOB NOT NULL,
    created_at  INTEGER NOT NULL,
    survey_id   INTEGER,
    pruned_at   INTEGER
)
"""
_BUSY_TIMEOUT_SECONDS = 30

#: 完整回执的缺省留存期。要盖住平台的整条重试／核对链路：自动核对最多
#: ``max-attempts`` 次、退避合计约 1.5 小时（PublishReconciliationProperties 缺省值），
#: 之后转人工复核——一周给人留出反应时间。
DEFAULT_RESULT_TTL_SECONDS = 7 * 24 * 3600
#: 墓碑的缺省留存期。必须长于平台任何可能的重试视野：墓碑没了，同一 ``requestId``
#: 就会被当成新请求再发布一次。
DEFAULT_TOMBSTONE_TTL_SECONDS = 90 * 24 * 3600
#: 回执留存期的下限。配置写小了等于把幂等关掉，所以宁可拒绝启动也不静默接受。
MIN_RESULT_TTL_SECONDS = 24 * 3600
#: 后台清理的缺省间隔。留存期以天计，一小时一次足够。
DEFAULT_PRUNE_INTERVAL_SECONDS = 3600

#: 存档文件名（``PUBGW_STATE_DIR`` 下）。
RESULTS_FILE = "publish-results.sqlite3"
RESULT_TTL_ENV = "PUBGW_RESULT_TTL_SECONDS"
TOMBSTONE_TTL_ENV = "PUBGW_TOMBSTONE_TTL_SECONDS"


@dataclass(frozen=True)
class Retention:
    """两段留存窗口，单位秒。"""

    result_seconds: int = DEFAULT_RESULT_TTL_SECONDS
    tombstone_seconds: int = DEFAULT_TOMBSTONE_TTL_SECONDS

    def __post_init__(self) -> None:
        if self.result_seconds < MIN_RESULT_TTL_SECONDS:
            raise ValueError(
                "the result retention window must be at least {} seconds, got {}".format(
                    MIN_RESULT_TTL_SECONDS, self.result_seconds
                )
            )
        if self.tombstone_seconds < self.result_seconds:
            raise ValueError(
                "the tombstone retention window ({}s) must not be shorter than the result "
                "window ({}s)".format(self.tombstone_seconds, self.result_seconds)
            )


def retention_from_env(env: Mapping[str, str]) -> Retention:
    """从环境变量读留存配置。不合法一律 ``ValueError``，由调用方决定怎么报。"""
    result = _seconds(env, RESULT_TTL_ENV, DEFAULT_RESULT_TTL_SECONDS)
    tombstone = _seconds(env, TOMBSTONE_TTL_ENV, DEFAULT_TOMBSTONE_TTL_SECONDS)
    try:
        return Retention(result_seconds=result, tombstone_seconds=tombstone)
    except ValueError as error:
        raise ValueError(
            "{} / {}: {}".format(RESULT_TTL_ENV, TOMBSTONE_TTL_ENV, error)
        ) from None


def _seconds(env: Mapping[str, str], name: str, default: int) -> int:
    value = env.get(name, "")
    if not value:
        return default
    if not value.isdigit() or int(value) <= 0:
        raise ValueError("{} must be a positive number of seconds".format(name))
    return int(value)


@dataclass(frozen=True)
class StoredResult:
    """窗口内的首次应答，可以逐字节重放。"""

    fingerprint: str
    status: int
    body: bytes


@dataclass(frozen=True)
class ExpiredResult:
    """正文已过期、只剩墓碑。``survey_id`` 是当时引擎里那份问卷（可能没有）。"""

    fingerprint: str
    original_status: int
    created_at: int
    survey_id: Optional[int]


@dataclass(frozen=True)
class PruneReport:
    bodies_dropped: int
    rows_deleted: int

    def __bool__(self) -> bool:
        return bool(self.bodies_dropped or self.rows_deleted)


Lookup = Union[StoredResult, ExpiredResult]


class ResultStore:
    def __init__(
        self,
        path: str,
        retention: Optional[Retention] = None,
        now=time.time,
    ):
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        self._path = path
        self._retention = retention or Retention()
        self._now = now
        self._write_lock = threading.Lock()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(_SCHEMA)
            _migrate(connection)
        # 刻意不在这里清理：构造函数不该改数据，而且留存语义由 get 保证，不靠清理跑过。
        # 服务进程由 PruneScheduler 立刻清第一轮；维护命令自己显式调 prune。

    @property
    def retention(self) -> Retention:
        return self._retention

    def get(self, request_id: str) -> Optional[Lookup]:
        """窗口内返回首次应答；过期返回墓碑；墓碑也到期则视同没有（不看清理跑没跑）。"""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT fingerprint, status, body, created_at, survey_id, pruned_at "
                "FROM publish_results WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        created_at = int(row[3])
        now = self._now()
        if created_at + self._retention.tombstone_seconds <= now:
            return None
        survey_id = None if row[4] is None else int(row[4])
        if row[5] is not None or created_at + self._retention.result_seconds <= now:
            return ExpiredResult(row[0], int(row[1]), created_at, survey_id)
        return StoredResult(fingerprint=row[0], status=int(row[1]), body=bytes(row[2]))

    def put(
        self,
        request_id: str,
        fingerprint: str,
        status: int,
        body: bytes,
        survey_id: Optional[int] = None,
    ) -> StoredResult:
        """写入首次结果；已存在则保持原样。返回库里最终的那一份。"""
        created_at = int(self._now())
        with self._write_lock, self._connect() as connection:
            # 墓碑已到期的行必须先删掉：否则 INSERT OR IGNORE 会被它挡住，
            # 而 get 又当它不存在——重放就会拿到上一次的回执。
            connection.execute(
                "DELETE FROM publish_results WHERE request_id = ? AND created_at <= ?",
                (request_id, created_at - self._retention.tombstone_seconds),
            )
            connection.execute(
                "INSERT OR IGNORE INTO publish_results "
                "(request_id, fingerprint, status, body, created_at, survey_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (request_id, fingerprint, status, sqlite3.Binary(body), created_at, survey_id),
            )
        stored = self.get(request_id)
        if not isinstance(stored, StoredResult):
            raise RuntimeError("result for {} vanished right after being stored".format(request_id))
        return stored

    def prune(self) -> PruneReport:
        """删除到期墓碑、丢弃过期正文。只改行，不重写文件（回收磁盘见 ``reclaim``）。"""
        now = int(self._now())
        with self._write_lock, self._connect() as connection:
            # 先删后改：同时跨过两道线的行只算一次删除，日志里的"丢弃正文"才是真实存量。
            rows = connection.execute(
                "DELETE FROM publish_results WHERE created_at <= ?",
                (now - self._retention.tombstone_seconds,),
            ).rowcount
            bodies = connection.execute(
                "UPDATE publish_results SET body = X'', pruned_at = ? "
                "WHERE pruned_at IS NULL AND created_at <= ?",
                (now, now - self._retention.result_seconds),
            ).rowcount
        report = PruneReport(max(bodies, 0), max(rows, 0))
        if report:
            log.info(
                "pruned the publish result store: %d body(ies) dropped, %d tombstone(s) deleted",
                report.bodies_dropped, report.rows_deleted,
            )
        return report

    def reclaim(self) -> bool:
        """VACUUM：把 ``prune`` 腾出来的页真正交还文件系统，顺带覆写掉里面的旧正文字节。

        **只在维护窗口里显式调用**（``pubgw prune-results``），绝不放进自动路径：VACUUM 要
        独占锁并重写整个文件，与并发的 ``put`` 抢锁会让后者等满 30 秒后抛错——而发布成功之后
        ``put`` 失败等于结果没落库，平台用同一 ``requestId`` 重试就会重复发布。
        """
        started = time.monotonic()
        try:
            connection = sqlite3.connect(self._path, timeout=_BUSY_TIMEOUT_SECONDS, isolation_level=None)
            try:
                connection.execute("VACUUM")
            finally:
                connection.close()
        except sqlite3.Error:
            # 回收磁盘失败不影响留存语义（正文已经不会再被返回），只记日志。
            log.warning("could not vacuum the publish result store", exc_info=True)
            return False
        log.info("vacuumed the publish result store in %.1fs", time.monotonic() - started)
        return True

    def _connect(self) -> sqlite3.Connection:
        # 每次操作一个连接：sqlite3 连接不能跨线程共用，ThreadingHTTPServer 每请求一线程。
        # 连接作为上下文管理器只管事务提交，不会关闭连接，所以另包一层 closing。
        return _ClosingConnection(sqlite3.connect(self._path, timeout=_BUSY_TIMEOUT_SECONDS))


def _migrate(connection: sqlite3.Connection) -> None:
    """P0 起就在跑的库没有留存用的两列；补列而不是重建表，既有回执一份不丢。"""
    columns = {row[1] for row in connection.execute("PRAGMA table_info(publish_results)")}
    for column, definition in (("survey_id", "INTEGER"), ("pruned_at", "INTEGER")):
        if column not in columns:
            connection.execute(
                "ALTER TABLE publish_results ADD COLUMN {} {}".format(column, definition)
            )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS publish_results_created_at ON publish_results (created_at)"
    )


class _ClosingConnection:
    """``with`` 结束时提交（或回滚）并关闭连接。"""

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def __enter__(self) -> sqlite3.Connection:
        return self._connection

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            if exc_type is None:
                self._connection.commit()
            else:
                self._connection.rollback()
        finally:
            self._connection.close()


class Prunable(Protocol):
    """``PruneScheduler`` 只需要这一个方法——刻意不含 ``reclaim``（见其文档）。"""

    def prune(self) -> PruneReport:
        ...


class PruneScheduler:
    """后台定期清理。第一轮立刻跑，之后每 ``interval_seconds`` 一轮。

    单实例网关一个守护线程就够。清理失败只记日志——留存语义由 ``get`` 保证，
    清理只负责让库里不再留着那些行，不能因为一次磁盘异常就不再尝试。

    **只调 ``prune``，不调 ``reclaim``**：VACUUM 会和正常请求抢锁，理由见 ``ResultStore.reclaim``。
    """

    def __init__(self, store: Prunable, interval_seconds: float = DEFAULT_PRUNE_INTERVAL_SECONDS):
        if interval_seconds <= 0:
            raise ValueError("the prune interval must be positive")
        self._store = store
        self._interval = interval_seconds
        self._stopped = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        # stop() 之后还能再 start()：不清掉这个标志，新线程会一轮都不跑就静默退出。
        self._stopped.clear()
        self._thread = threading.Thread(target=self._loop, name="pubgw-prune", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stopped.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stopped.is_set():
            try:
                self._store.prune()
            except Exception:  # noqa: BLE001 — 清理失败不能让线程死掉
                log.warning("scheduled prune of the publish result store failed", exc_info=True)
            self._stopped.wait(self._interval)


class InFlight:
    """非阻塞的键级互斥。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._held: Set[Hashable] = set()

    def try_acquire(self, key: Hashable) -> bool:
        with self._lock:
            if key in self._held:
                return False
            self._held.add(key)
            return True

    def release(self, key: Hashable) -> None:
        with self._lock:
            self._held.discard(key)
