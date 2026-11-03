import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch


PATH = Path(__file__).resolve().parents[1] / 'install_trivy.py'


class InstallTrivyTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('install_trivy', PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def archive(self, name='trivy', kind=tarfile.REGTYPE, duplicate=False):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = 6 if kind == tarfile.REGTYPE else 0
            archive.addfile(member, io.BytesIO(b'binary') if member.size else None)
            if duplicate:
                archive.addfile(member, io.BytesIO(b'binary'))
        return stream.getvalue()

    def install(self, data, checksums=None):
        checksum = hashlib.sha256(data).hexdigest()
        record = f'{checksum}  trivy_0.74.0_Linux-64bit.tar.gz\n'
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'bin'
            with patch.object(self.module, 'download', side_effect=[data, (record if checksums is None else checksums).encode()]) as download:
                self.module.install('v0.74.0', target)
            self.assertEqual(b'binary', (target / 'trivy').read_bytes())
            self.assertEqual(0o755, (target / 'trivy').stat().st_mode & 0o777)
            self.assertTrue(all(call.args[0].startswith('https://github.com/aquasecurity/trivy/releases/download/v0.74.0/') for call in download.call_args_list))

    def test_official_download_and_checksum_before_install(self):
        self.install(self.archive())

    def test_bad_checksum_records_reject(self):
        for checksums in ['', '0' * 64 + '  trivy_0.74.0_Linux-64bit.tar.gz\n', 'malformed\n']:
            with self.subTest(checksums=checksums), self.assertRaises(ValueError):
                self.install(self.archive(), checksums)
        data = self.archive()
        record = f'{hashlib.sha256(data).hexdigest()}  trivy_0.74.0_Linux-64bit.tar.gz\n'
        with self.assertRaises(ValueError):
            self.install(data, record * 2)

    def test_archive_alias_links_and_duplicates_reject(self):
        for name, kind in [('../trivy', tarfile.REGTYPE), ('trivy', tarfile.SYMTYPE), ('trivy', tarfile.LNKTYPE)]:
            with self.subTest(name=name, kind=kind), self.assertRaises(ValueError):
                self.install(self.archive(name, kind))
        with self.assertRaises(ValueError):
            self.install(self.archive(duplicate=True))

    def test_download_or_checksum_failure_preserves_existing_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            binary = target / 'trivy'
            binary.write_bytes(b'existing')
            for results in [[OSError('download failed')], [self.archive(), b'']]:
                with self.subTest(results=len(results)), patch.object(self.module, 'download', side_effect=results):
                    with self.assertRaises((OSError, ValueError)):
                        self.module.install('v0.74.0', target)
                self.assertEqual(b'existing', binary.read_bytes())
                self.assertEqual(['trivy'], [path.name for path in target.iterdir()])

    def test_invalid_version_never_downloads(self):
        with patch.object(self.module, 'download') as download, self.assertRaises(ValueError):
            self.module.install('../latest', Path('/unused'))
        download.assert_not_called()

    def test_official_binary_size_is_within_bound(self):
        data = self.archive()
        member = tarfile.TarInfo('trivy')
        member.size = 168456354  # Official v0.74.0 Linux amd64 artifact.
        with patch.object(self.module.tarfile.TarFile, 'getmembers', return_value=[member]), patch.object(self.module.tarfile.TarFile, 'extractfile', return_value=io.BytesIO(b'binary')):
            self.install(data)
        member.size = 256 * 1024 * 1024 + 1
        with patch.object(self.module.tarfile.TarFile, 'getmembers', return_value=[member]), self.assertRaises(ValueError):
            self.install(data)


if __name__ == '__main__':
    unittest.main()
