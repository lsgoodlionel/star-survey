"""幂等结果存储与在途锁。"""

import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest

from pubgw.store import (
    MIN_INVITATION_TTL_SECONDS,
    MIN_RESULT_TTL_SECONDS,
    ExpiredResult,
    InFlight,
    PruneReport,
    PruneScheduler,
    Retention,
    ResultStore,
    StoredResult,
)


class ResultStoreTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.path = os.path.join(self.directory, "results.sqlite3")

    def tearDown(self):
        shutil.rmtree(self.directory)

    def test_an_unknown_request_id_has_no_result(self):
        self.assertIsNone(ResultStore(self.path).get("req-1"))

    def test_returns_what_was_stored(self):
        store = ResultStore(self.path)
        store.put("req-1", "fp", 200, b'{"status":"published"}')

        self.assertEqual(StoredResult("fp", 200, b'{"status":"published"}'), store.get("req-1"))

    def test_the_first_result_wins(self):
        store = ResultStore(self.path)
        first = store.put("req-1", "fp", 200, b"first")
        second = store.put("req-1", "fp", 502, b"second")

        self.assertEqual(first, second)
        self.assertEqual(b"first", store.get("req-1").body)

    def test_results_survive_a_restart(self):
        ResultStore(self.path).put("req-1", "fp", 422, b"kept")

        self.assertEqual(b"kept", ResultStore(self.path).get("req-1").body)

    def test_creates_the_state_directory(self):
        nested = os.path.join(self.directory, "a", "b", "results.sqlite3")

        ResultStore(nested).put("req-1", "fp", 200, b"x")

        self.assertTrue(os.path.exists(nested))

    def test_concurrent_writers_all_see_one_first_result(self):
        store = ResultStore(self.path)
        seen = []

        def writer(index):
            seen.append(store.put("req-1", "fp", 200, str(index).encode()))

        threads = [threading.Thread(target=writer, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(1, len(set(seen)))


DAY = 24 * 3600


class MovableClock:
    """测试用时钟：留存期是以天计的，不可能真等。"""

    def __init__(self, value=1_800_000_000):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class RetentionWindowTest(unittest.TestCase):
    """留存策略：完整回执一段、墓碑一段、之后整行消失。"""

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.path = os.path.join(self.directory, "results.sqlite3")
        self.clock = MovableClock()
        self.retention = Retention(result_seconds=7 * DAY, tombstone_seconds=90 * DAY)

    def tearDown(self):
        shutil.rmtree(self.directory)

    def store(self):
        return ResultStore(self.path, retention=self.retention, now=self.clock)

    def test_defaults_keep_receipts_for_a_week_and_tombstones_for_a_quarter(self):
        self.assertEqual(7 * DAY, Retention().result_seconds)
        self.assertEqual(90 * DAY, Retention().tombstone_seconds)

    def test_a_result_ttl_below_the_floor_is_refused(self):
        with self.assertRaises(ValueError):
            Retention(result_seconds=MIN_RESULT_TTL_SECONDS - 1, tombstone_seconds=90 * DAY)

    def test_a_tombstone_window_shorter_than_the_result_window_is_refused(self):
        with self.assertRaises(ValueError):
            Retention(result_seconds=7 * DAY, tombstone_seconds=7 * DAY - 1)

    def test_a_result_inside_the_window_replays_in_full(self):
        store = self.store()
        store.put("req-1", "fp", 200, b'{"status":"published"}', survey_id=42)

        self.clock.advance(7 * DAY - 1)

        self.assertEqual(StoredResult("fp", 200, b'{"status":"published"}'), store.get("req-1"))

    def test_once_the_window_passes_only_a_tombstone_is_left(self):
        store = self.store()
        store.put("req-1", "fp", 200, b'{"status":"published"}', survey_id=42)
        created_at = int(self.clock.value)

        self.clock.advance(7 * DAY)

        self.assertEqual(ExpiredResult("fp", 200, created_at, 42), store.get("req-1"))

    def test_expiry_does_not_wait_for_pruning(self):
        """先判过期、再清理：留存期不取决于清理线程什么时候跑。"""
        store = self.store()
        store.put("req-1", "fp", 200, b"secret-ish", survey_id=42)

        self.clock.advance(7 * DAY)
        expired = store.get("req-1")
        report = store.prune()

        self.assertIsInstance(expired, ExpiredResult)
        self.assertEqual(1, report.bodies_dropped)

    def test_pruning_drops_the_body_and_keeps_the_tombstone(self):
        store = self.store()
        store.put("req-1", "fp", 502, b'{"status":"failed"}')
        created_at = int(self.clock.value)
        self.clock.advance(7 * DAY)

        store.prune()

        self.assertEqual(ExpiredResult("fp", 502, created_at, None), store.get("req-1"))

    def test_pruning_twice_drops_nothing_the_second_time(self):
        store = self.store()
        store.put("req-1", "fp", 200, b"body")
        self.clock.advance(7 * DAY)
        store.prune()

        self.assertEqual(PruneReport(0, 0), store.prune())

    def test_pruning_keeps_a_result_that_is_still_inside_the_window(self):
        store = self.store()
        store.put("req-1", "fp", 200, b"body")

        self.clock.advance(7 * DAY - 1)

        self.assertEqual(PruneReport(0, 0), store.prune())
        self.assertEqual(b"body", store.get("req-1").body)

    def test_a_row_past_both_windows_only_counts_as_a_deletion(self):
        """一次清理里同时跨过两道线的行应当只被删掉，不该也算进"丢弃正文"。"""
        store = self.store()
        store.put("req-1", "fp", 200, b"body")

        self.clock.advance(90 * DAY)

        self.assertEqual(PruneReport(0, 1), store.prune())

    def test_the_tombstone_itself_ages_out(self):
        store = self.store()
        store.put("req-1", "fp", 200, b"body")

        self.clock.advance(90 * DAY)

        self.assertIsNone(store.get("req-1"))
        self.assertEqual(1, store.prune().rows_deleted)

    def test_a_request_id_whose_tombstone_aged_out_can_be_used_again(self):
        """整行消失之后 INSERT OR IGNORE 不能再被旧行挡住，否则回执会张冠李戴。"""
        store = self.store()
        store.put("req-1", "fp", 200, b"first")

        self.clock.advance(90 * DAY)
        store.put("req-1", "fp2", 422, b"second", survey_id=7)

        self.assertEqual(StoredResult("fp2", 422, b"second"), store.get("req-1"))

    def test_survives_a_restart_with_the_same_retention(self):
        ResultStore(self.path, retention=self.retention, now=self.clock).put("req-1", "fp", 200, b"kept")

        self.clock.advance(7 * DAY)

        self.assertIsInstance(self.store().get("req-1"), ExpiredResult)

    def test_a_database_written_before_retention_existed_still_opens(self):
        """P0 起就在跑的库没有 survey_id / pruned_at 两列，升级不能丢掉既有回执。"""
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "CREATE TABLE publish_results (request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, "
                "status INTEGER NOT NULL, body BLOB NOT NULL, created_at INTEGER NOT NULL)"
            )
            connection.execute(
                "INSERT INTO publish_results VALUES (?, ?, ?, ?, ?)",
                ("req-old", "fp", 200, sqlite3.Binary(b"legacy"), int(self.clock.value)),
            )

        self.assertEqual(StoredResult("fp", 200, b"legacy"), self.store().get("req-old"))


#: 带邀请码的回执长这样（只保留判定需要的形状）。token 是能直接进入问卷的凭据。
RECEIPT_WITH_CODES = (
    b'{"result":{"ok":true,"surveyId":42,'
    b'"invitations":[{"index":0,"ref":"contact-7","token":"a1b2c3d4e5f6g7h8","tid":"1"}]},'
    b'"status":"published"}'
)
RECEIPT_WITHOUT_CODES = b'{"result":{"ok":true,"surveyId":42},"status":"published"}'
TOKEN = b"a1b2c3d4e5f6g7h8"


class InvitationRetentionTest(unittest.TestCase):
    """带凭据的回执活得更短：邀请码是能直接进入问卷的凭据，不该陪着回执躺一周。"""

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.path = os.path.join(self.directory, "results.sqlite3")
        self.clock = MovableClock()

    def tearDown(self):
        shutil.rmtree(self.directory)

    def raw_file(self):
        with open(self.path, "rb") as handle:
            return handle.read()

    def store(self, invitation_seconds=DAY):
        return ResultStore(
            self.path,
            retention=Retention(result_seconds=7 * DAY, tombstone_seconds=90 * DAY,
                                invitation_seconds=invitation_seconds),
            now=self.clock,
        )

    def test_the_default_invitation_window_is_a_day(self):
        self.assertEqual(DAY, Retention().invitation_seconds)

    def test_an_invitation_window_below_the_floor_is_refused(self):
        with self.assertRaises(ValueError):
            Retention(invitation_seconds=MIN_INVITATION_TTL_SECONDS - 1)

    def test_an_invitation_window_longer_than_the_result_window_is_refused(self):
        with self.assertRaises(ValueError):
            Retention(result_seconds=2 * DAY, tombstone_seconds=90 * DAY, invitation_seconds=3 * DAY)

    def test_codes_replay_inside_the_invitation_window(self):
        store = self.store()
        store.put("req-1", "fp", 200, RECEIPT_WITH_CODES, survey_id=42)

        self.clock.advance(DAY - 1)

        self.assertEqual(RECEIPT_WITH_CODES, store.get("req-1").body)

    def test_a_receipt_holding_codes_expires_at_the_invitation_window(self):
        store = self.store()
        store.put("req-1", "fp", 200, RECEIPT_WITH_CODES, survey_id=42)
        created_at = int(self.clock.value)

        self.clock.advance(DAY)

        self.assertEqual(ExpiredResult("fp", 200, created_at, 42, held_codes=True), store.get("req-1"))

    def test_a_receipt_without_codes_keeps_the_full_result_window(self):
        store = self.store()
        store.put("req-1", "fp", 200, RECEIPT_WITHOUT_CODES, survey_id=42)

        self.clock.advance(7 * DAY - 1)

        self.assertEqual(RECEIPT_WITHOUT_CODES, store.get("req-1").body)

    def test_pruning_takes_the_codes_off_disk_at_the_invitation_window(self):
        store = self.store()
        store.put("req-1", "fp", 200, RECEIPT_WITH_CODES, survey_id=42)
        store.put("req-2", "fp", 200, RECEIPT_WITHOUT_CODES, survey_id=43)

        self.clock.advance(DAY)
        report = store.prune()

        self.assertEqual(1, report.bodies_dropped)
        self.assertNotIn(TOKEN, self.raw_file())
        # 没带码的那份还在窗口里，不该被连坐。
        self.assertEqual(RECEIPT_WITHOUT_CODES, store.get("req-2").body)

    def test_the_tombstone_remembers_that_the_receipt_held_codes(self):
        """排障时要能看出"为什么才一天就过期了"，而正文那时已经没了。"""
        store = self.store()
        store.put("req-1", "fp", 200, RECEIPT_WITH_CODES, survey_id=42)
        store.put("req-2", "fp", 200, RECEIPT_WITHOUT_CODES, survey_id=43)
        self.clock.advance(7 * DAY)
        store.prune()

        self.assertTrue(store.get("req-1").held_codes)
        self.assertFalse(store.get("req-2").held_codes)

    def test_the_tombstone_left_behind_holds_no_token(self):
        store = self.store()
        store.put("req-1", "fp", 200, RECEIPT_WITH_CODES, survey_id=42)
        self.clock.advance(DAY)
        store.prune()
        store.reclaim()

        self.assertIsInstance(store.get("req-1"), ExpiredResult)
        self.assertNotIn(TOKEN, self.raw_file())

    def test_a_row_written_before_this_window_existed_still_expires_early(self):
        """第六波已经在往存档里写邀请码了：升级后那些行也必须按短窗口算，不能只管新行。"""
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "CREATE TABLE publish_results (request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, "
                "status INTEGER NOT NULL, body BLOB NOT NULL, created_at INTEGER NOT NULL)"
            )
            connection.execute(
                "INSERT INTO publish_results VALUES (?, ?, ?, ?, ?)",
                ("legacy", "fp", 200, sqlite3.Binary(RECEIPT_WITH_CODES), int(self.clock.value)),
            )

        self.clock.advance(DAY)

        self.assertIsInstance(self.store().get("legacy"), ExpiredResult)


class ReclaimTest(unittest.TestCase):
    """回收磁盘是一个独立的、由运维显式触发的动作（见 PruneSchedulerTest 里的理由）。"""

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.path = os.path.join(self.directory, "results.sqlite3")
        self.clock = MovableClock()

    def tearDown(self):
        shutil.rmtree(self.directory)

    def test_reclaim_shrinks_the_file_after_bodies_were_dropped(self):
        store = ResultStore(self.path, now=self.clock)
        for index in range(40):
            store.put("req-{}".format(index), "fp", 200, b"x" * 20_000)
        self.clock.advance(8 * DAY)
        store.prune()
        before = os.path.getsize(self.path)

        self.assertTrue(store.reclaim())
        self.assertLess(os.path.getsize(self.path), before)

    def test_reclaim_keeps_the_tombstones_readable(self):
        store = ResultStore(self.path, now=self.clock)
        store.put("req-1", "fp", 200, b"x" * 20_000, survey_id=42)
        created_at = int(self.clock.value)
        self.clock.advance(8 * DAY)
        store.prune()

        store.reclaim()

        self.assertEqual(ExpiredResult("fp", 200, created_at, 42), store.get("req-1"))


class PruneSchedulerTest(unittest.TestCase):
    class RecordingStore:
        def __init__(self):
            self.pruned = threading.Event()
            self.calls = 0

        def prune(self):
            self.calls += 1
            self.pruned.set()
            return PruneReport(0, 0)

    def test_the_first_sweep_does_not_wait_for_the_interval(self):
        store = self.RecordingStore()
        scheduler = PruneScheduler(store, interval_seconds=3600)

        scheduler.start()
        try:
            self.assertTrue(store.pruned.wait(timeout=5))
        finally:
            scheduler.stop()

    def test_the_scheduler_never_vacuums(self):
        """VACUUM 要独占锁并重写整个文件。每小时来一次会和正常请求抢锁：发布成功之后
        put 撞上锁等待失败，结果就没落库，平台用同一 requestId 重试会重复发布。"""

        class NoVacuum(self.RecordingStore):
            def reclaim(self):
                raise AssertionError("the scheduled sweep must not vacuum")

        store = NoVacuum()
        scheduler = PruneScheduler(store, interval_seconds=0.01)
        scheduler.start()
        try:
            deadline = time.monotonic() + 5
            while store.calls < 3 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertGreaterEqual(store.calls, 3)
        finally:
            scheduler.stop()

    def test_a_stopped_scheduler_can_be_started_again(self):
        store = self.RecordingStore()
        scheduler = PruneScheduler(store, interval_seconds=3600)
        scheduler.start()
        store.pruned.wait(timeout=5)
        scheduler.stop()
        store.pruned.clear()
        before = store.calls

        scheduler.start()
        try:
            self.assertTrue(store.pruned.wait(timeout=5))
            self.assertGreater(store.calls, before)
        finally:
            scheduler.stop()

    def test_stop_joins_the_thread(self):
        store = self.RecordingStore()
        scheduler = PruneScheduler(store, interval_seconds=3600)
        scheduler.start()
        store.pruned.wait(timeout=5)

        scheduler.stop()

        self.assertFalse(scheduler.is_running())

    def test_a_failing_sweep_does_not_kill_the_loop(self):
        class Broken(self.RecordingStore):
            def prune(self):
                super().prune()
                raise RuntimeError("disk on fire")

        store = Broken()
        scheduler = PruneScheduler(store, interval_seconds=0.01)
        scheduler.start()
        try:
            deadline = time.monotonic() + 5
            while store.calls < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertGreaterEqual(store.calls, 2)
        finally:
            scheduler.stop()


class InFlightTest(unittest.TestCase):
    def test_a_second_acquire_of_the_same_key_fails(self):
        locks = InFlight()

        self.assertTrue(locks.try_acquire(("i", "u")))
        self.assertFalse(locks.try_acquire(("i", "u")))

    def test_different_keys_do_not_block_each_other(self):
        locks = InFlight()

        self.assertTrue(locks.try_acquire(("i", "u1")))
        self.assertTrue(locks.try_acquire(("i", "u2")))
        self.assertTrue(locks.try_acquire(("j", "u1")))

    def test_release_frees_the_key(self):
        locks = InFlight()
        locks.try_acquire(("i", "u"))
        locks.release(("i", "u"))

        self.assertTrue(locks.try_acquire(("i", "u")))

    def test_only_one_of_many_threads_wins(self):
        locks = InFlight()
        start = threading.Barrier(8)
        wins = []

        def contender():
            start.wait()
            wins.append(locks.try_acquire(("i", "u")))

        threads = [threading.Thread(target=contender) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(1, wins.count(True))


if __name__ == "__main__":
    unittest.main()
