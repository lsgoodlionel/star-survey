"""幂等结果存储与在途锁。"""

import os
import shutil
import tempfile
import threading
import unittest

from pubgw.store import InFlight, ResultStore, StoredResult


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
