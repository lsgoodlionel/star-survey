import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[5]
SPEC = importlib.util.spec_from_file_location('native_integrity', ROOT / 'platform/deploy/production/release/verify_native_orchestrator.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class NativeCandidateIntegrityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.candidate = self.root / 'candidate'
        self.candidate.mkdir()
        self.trusted = self.root / 'trusted'
        self.trusted.mkdir()
        self.files = {name: (ROOT / 'platform/deploy/production/release' / name).read_bytes() for name in MODULE.FILES}
        for name, data in self.files.items():
            (self.trusted / name).write_bytes(data)
        self.identity = self.root / 'identity.json'

    def build(self, entries=None):
        path = self.candidate / 'fixture.tar.gz'
        with tarfile.open(path, 'w:gz') as archive:
            for name, data, kind in entries or [('release/' + n, b, 'file') for n, b in self.files.items()]:
                info = tarfile.TarInfo(name)
                if kind == 'symlink':
                    info.type, info.linkname = tarfile.SYMTYPE, '/tmp/irrelevant'
                    archive.addfile(info)
                else:
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
        self.identity.write_text(json.dumps({'bundles': {'amd64': {'name': path.name,
                                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}}}))
        return path

    def verify(self):
        MODULE.verify(self.identity, self.candidate, 'amd64', self.trusted)

    def test_current_matching_candidate_is_accepted_for_preflight_only(self):
        self.build()
        self.verify()

    def test_older_candidate_and_rehashed_tamper_are_rejected(self):
        for replacement in (b'legacy script without guards', self.files[MODULE.FILES[0]] + b'\n# tampered\n'):
            with self.subTest(replacement=replacement[-20:]):
                entries = [('release/' + MODULE.FILES[0], replacement, 'file'),
                           ('release/' + MODULE.FILES[1], self.files[MODULE.FILES[1]], 'file')]
                self.build(entries)
                with self.assertRaises(MODULE.CandidateRejected):
                    self.verify()

    def test_missing_or_invalid_proof_rejected(self):
        self.build()
        self.identity.unlink()
        with self.assertRaises(MODULE.CandidateRejected): self.verify()
        self.build([('release/' + MODULE.FILES[0], self.files[MODULE.FILES[0]], 'file')])
        with self.assertRaises(MODULE.CandidateRejected): self.verify()
        self.build()
        self.identity.write_text(json.dumps({'bundles': {'amd64': {'name': '../fixture.tar.gz', 'sha256': '0' * 64}}}))
        with self.assertRaises(MODULE.CandidateRejected): self.verify()

    def test_duplicate_link_and_digest_tamper_rejected(self):
        for entries in [
            [('release/' + MODULE.FILES[0], b'', 'symlink'), ('release/' + MODULE.FILES[1], self.files[MODULE.FILES[1]], 'file')],
            [('release/' + n, self.files[n], 'file') for n in MODULE.FILES] + [('release/' + MODULE.FILES[0], self.files[MODULE.FILES[0]], 'file')],
        ]:
            with self.subTest(entries=len(entries)):
                self.build(entries)
                with self.assertRaises(MODULE.CandidateRejected): self.verify()
        path = self.build()
        with path.open('ab') as stream: stream.write(b'tamper')
        with self.assertRaises(MODULE.CandidateRejected): self.verify()

    def test_candidate_symlink_is_rejected(self):
        path = self.build()
        target = self.root / 'elsewhere.tar.gz'
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaises(MODULE.CandidateRejected): self.verify()

    def test_missing_trust_anchor_and_wrong_architecture_are_rejected(self):
        self.build()
        (self.trusted / MODULE.FILES[1]).unlink()
        with self.assertRaises(MODULE.CandidateRejected): self.verify()
        with self.assertRaises(MODULE.CandidateRejected):
            MODULE.verify(self.identity, self.candidate, 'riscv64', self.trusted)
