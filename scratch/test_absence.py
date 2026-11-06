import subprocess
import unittest
from exclusive_probe import require_container_absent

class AbsenceTest(unittest.TestCase):
    def test_only_same_daemon_explicit_not_found_is_accepted(self):
        cid = 'a' * 64
        healthy = subprocess.CompletedProcess([], 0, 'same\n', '')
        missing = subprocess.CompletedProcess([], 1, '', 'Error: No such object: ' + cid + '\n')
        require_container_absent(missing, cid, 'same', healthy, healthy)
        for result, before, after in (
            (subprocess.CompletedProcess([], 1, '', 'Cannot connect to the Docker daemon'), healthy, healthy),
            (subprocess.CompletedProcess([], 1, '', 'Error: No such object: ' + 'b'*64), healthy, healthy),
            (subprocess.CompletedProcess([], 0, '[]', ''), healthy, healthy),
            (missing, subprocess.CompletedProcess([], 1, '', 'error'), healthy),
            (missing, healthy, subprocess.CompletedProcess([], 0, 'other', '')),
        ):
            with self.subTest(result=result, before=before, after=after), self.assertRaises(RuntimeError):
                require_container_absent(result, cid, 'same', before, after)

if __name__ == '__main__': unittest.main()
