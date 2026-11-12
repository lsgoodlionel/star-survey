"""Evidence is independently checked, not inferred from process exit zero."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('crypto_native_evidence',
    Path(__file__).parent / 'integration' / 'owned_crypto_native.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CryptoNativeEvidenceTest(unittest.TestCase):
    def test_false_missing_or_invalid_proof_cannot_pass_with_zero_exit(self):
        hashes = {'fixture': 'a' * 64}
        report = {'exitCode': 0, 'containerCleanup': 'verified', 'sourceAfter': hashes,
            'evidence': dict({key: True for key in MODULE.CHECKS}, sourceHashes=hashes, roundTripExitCodes=[0, 0])}
        self.assertTrue(MODULE.passed(report, hashes))
        for key in MODULE.CHECKS:
            for value in (False, None, 1):
                bad = deepcopy(report)
                bad['evidence'][key] = value
                self.assertFalse(MODULE.passed(bad, hashes))
        for codes in ([0, 1], [False, 0], [], None):
            bad = deepcopy(report)
            bad['evidence']['roundTripExitCodes'] = codes
            self.assertFalse(MODULE.passed(bad, hashes))
        for key, value in (('exitCode', False), ('containerCleanup', 'unknown'), ('sourceAfter', {})):
            bad = deepcopy(report)
            bad[key] = value
            self.assertFalse(MODULE.passed(bad, hashes))


if __name__ == '__main__':
    unittest.main()
