import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixture_browser_file import BrowserFixtureError, BrowserFixtureFile
from fixture_handoff import make_handoff


class BrowserFixtureFileTest(unittest.TestCase):
    def setUp(self):
        if os.getuid() == 0 or os.getgid() == 0: self.skipTest('requires an unprivileged test user')
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name).resolve()
        self.handoff = make_handoff(*(str(uuid.uuid4()) for _ in range(3)))

    def create(self):
        return BrowserFixtureFile.create(self.parent, self.handoff, os.getuid(), os.getgid())

    def test_private_one_use_file_and_cleanup(self):
        fixture = self.create(); path = fixture.directory / 'handoff.json'
        self.assertEqual(fixture.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(path.read_text()), self.handoff.browser_values())
        path.unlink(); fixture.cleanup()
        self.assertFalse(fixture.directory.exists())

    def test_cleanup_refuses_unknown_entry_and_symlink(self):
        fixture = self.create(); path = fixture.directory / 'handoff.json'
        extra = fixture.directory / 'unexpected'; extra.touch()
        with self.assertRaises(BrowserFixtureError): fixture.cleanup()
        self.assertTrue(extra.exists()); extra.unlink(); path.unlink()
        path.symlink_to(self.parent / 'unrelated')
        with self.assertRaises(BrowserFixtureError): fixture.cleanup()
        self.assertTrue(path.is_symlink()); path.unlink(); fixture.cleanup()

    def test_replaced_directory_is_never_removed(self):
        fixture = self.create(); old = fixture.directory.with_name(fixture.directory.name + '-old')
        fixture.directory.rename(old); fixture.directory.mkdir(mode=0o700)
        with self.assertRaises(BrowserFixtureError): fixture.cleanup()
        self.assertTrue(fixture.directory.is_dir())
        fixture.directory.rmdir(); old.rename(fixture.directory); fixture.cleanup()

    def test_unsafe_parent_is_rejected(self):
        self.parent.chmod(0o755)
        with self.assertRaises(BrowserFixtureError): self.create()

if __name__ == '__main__': unittest.main()
