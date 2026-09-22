"""幂等结果存储（SQLite）与在途发布锁（进程内）。

- ``ResultStore``：``requestId`` → 首次响应。先写者赢，之后的写入一律返回首次结果；
  落在 ``PUBGW_STATE_DIR`` 下的 SQLite 文件里，重启不丢。
- ``InFlight``：``(实例, definition.uuid)`` 同一时刻只允许一个发布，拿不到锁立即失败
  （不排队）——排队会让平台侧的超时与重试更难推理。

在途锁只在进程内有效：网关按单实例部署，多副本需要换成共享锁（见 README）。
"""

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Hashable, Optional, Set

_SCHEMA = """
CREATE TABLE IF NOT EXISTS publish_results (
    request_id  TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    status      INTEGER NOT NULL,
    body        BLOB NOT NULL,
    created_at  INTEGER NOT NULL
)
"""
_BUSY_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class StoredResult:
    fingerprint: str
    status: int
    body: bytes


class ResultStore:
    def __init__(self, path: str):
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        self._path = path
        self._write_lock = threading.Lock()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(_SCHEMA)

    def get(self, request_id: str) -> Optional[StoredResult]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT fingerprint, status, body FROM publish_results WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        return StoredResult(fingerprint=row[0], status=int(row[1]), body=bytes(row[2]))

    def put(self, request_id: str, fingerprint: str, status: int, body: bytes) -> StoredResult:
        """写入首次结果；已存在则保持原样。返回库里最终的那一份。"""
        with self._write_lock, self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO publish_results "
                "(request_id, fingerprint, status, body, created_at) VALUES (?, ?, ?, ?, ?)",
                (request_id, fingerprint, status, sqlite3.Binary(body), int(time.time())),
            )
        stored = self.get(request_id)
        if stored is None:
            raise RuntimeError("result for {} vanished right after being stored".format(request_id))
        return stored

    def _connect(self) -> sqlite3.Connection:
        # 每次操作一个连接：sqlite3 连接不能跨线程共用，ThreadingHTTPServer 每请求一线程。
        # 连接作为上下文管理器只管事务提交，不会关闭连接，所以另包一层 closing。
        return _ClosingConnection(sqlite3.connect(self._path, timeout=_BUSY_TIMEOUT_SECONDS))


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
