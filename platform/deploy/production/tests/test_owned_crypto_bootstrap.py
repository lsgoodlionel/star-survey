"""An admitted companion source must not be replaced by timestamp-valid pyc."""
import importlib
import importlib.util
import os
from pathlib import Path
import py_compile
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class CryptoBootstrapTest(unittest.TestCase):
    def test_valid_source_is_loaded_instead_of_a_timestamp_valid_untrusted_pyc(self):
        module = importlib.import_module('owned_crypto_child')
        with tempfile.TemporaryDirectory() as name, patch.object(sys, 'pycache_prefix', None):
            root = Path(name)
            source, evil, marker = root / 'owned_children.py', root / 'evil.py', root / 'cache-executed'
            safe = 'def establish_parent_guard(*args): return "verified-source"\n'
            malicious = f'from pathlib import Path\nPath({str(marker)!r}).touch()\ndef establish_parent_guard(*args): return "unchecked-cache"\n'
            size = max(len(safe), len(malicious)) + 16
            source.write_text(safe + '#' * (size - len(safe)))
            evil.write_text(malicious + '#' * (size - len(malicious)))
            source.chmod(0o600)
            stamp = source.stat()
            os.utime(evil, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            py_compile.compile(str(evil), cfile=importlib.util.cache_from_source(str(source)), doraise=True)
            # Verify the attack fixture is effective in the ordinary loader.
            spec = importlib.util.spec_from_file_location('ordinary_guard_fixture', source)
            ordinary = importlib.util.module_from_spec(spec); spec.loader.exec_module(ordinary)
            self.assertTrue(marker.exists())
            self.assertEqual('unchecked-cache', ordinary.establish_parent_guard())
            marker.unlink()
            with patch.object(module, '__file__', str(root / 'owned_crypto_child.py')):
                self.assertEqual('verified-source', module.load_parent_guard()())
            self.assertFalse(marker.exists())

    def test_unsafe_companion_permissions_and_symlink_are_rejected(self):
        module = importlib.import_module('owned_crypto_child')
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / 'owned_children.py'
            source.write_text('raise AssertionError("must never execute")\n'); source.chmod(0o666)
            with patch.object(module, '__file__', str(root / 'owned_crypto_child.py')):
                with self.assertRaises(ValueError): module.load_parent_guard()
                source.unlink(); source.symlink_to(root / 'absent')
                with self.assertRaises((ValueError, OSError)): module.load_parent_guard()


if __name__ == '__main__': unittest.main()
