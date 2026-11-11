"""Workspace contract tests. The injected portable FD backend is not memfd proof."""
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'owned_memory.py'


class PortableFDBackend:
    def create(self, name):
        with tempfile.TemporaryFile() as handle:
            return os.dup(handle.fileno())
    def seal(self, fd):
        pass  # Native kernel sealing is exercised separately on Linux.


class AnonymousWorkspaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('owned_memory_test', SOURCE)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def workspace(self, **kwargs):
        value = self.module.AnonymousWorkspace(_backend=PortableFDBackend(), **kwargs)
        self.addCleanup(value.close)
        return value

    def test_binary_paths_support_dump_archive_and_cipher_operations(self):
        workspace = self.workspace(max_bytes=64)
        path = workspace / 'postgres.dump'
        with path.open('xb') as output:
            self.assertEqual(5, output.write(b'PGDMP'))
        self.assertEqual(5, path.stat().st_size)
        self.assertEqual(b'PGDMP', path.read_bytes())
        self.assertEqual('.dump', path.suffix)
        self.assertEqual('postgres.dump.cipher', path.with_suffix('.dump.cipher').name)
        with self.assertRaises(FileExistsError):
            path.open('xb')

    def test_names_cannot_escape_the_anonymous_namespace(self):
        workspace = self.workspace()
        for name in ('', '.', '..', '../payload', '/payload', 'x/y', 'x\\y', 'x\x00y'):
            with self.subTest(name=name), self.assertRaises(self.module.WorkspaceError):
                workspace / name

    def test_quota_is_checked_before_any_partial_write(self):
        workspace = self.workspace(max_bytes=8)
        with (workspace / 'one').open('xb') as first:
            first.write(b'12345')
        with (workspace / 'two').open('xb') as second:
            with self.assertRaises(self.module.WorkspaceError):
                second.write(b'6789')
        self.assertEqual(b'', (workspace / 'two').read_bytes())
        self.assertEqual(b'12345', (workspace / 'one').read_bytes())

    def test_readers_have_independent_offsets(self):
        path = self.workspace() / 'payload'
        with path.open('xb') as output:
            output.write(b'abcdef')
        with path.open('rb') as first, path.open('rb') as second:
            self.assertEqual(b'ab', first.read(2))
            self.assertEqual(b'abcd', second.read(4))
            self.assertEqual(2, first.tell())
            first.seek(-1, io.SEEK_END)
            self.assertEqual(b'f', first.read())

    def test_closed_workspace_does_not_reopen_or_fall_back_to_disk(self):
        workspace = self.workspace()
        path = workspace / 'payload'
        with path.open('xb') as output:
            output.write(b'fixture')
        workspace.close()
        with self.assertRaises(self.module.WorkspaceError):
            path.read_bytes()
        with self.assertRaises(self.module.WorkspaceError):
            workspace / 'new'

    def test_plaintext_descriptor_is_private_anonymous_and_not_inheritable(self):
        path = self.workspace() / 'payload'
        with path.open('xb') as output:
            output.write(b'fixture')
        with path.export_for_child(write=False) as lease:
            self.assertEqual(0, os.fstat(lease.fd).st_nlink)
            self.assertEqual(0o600, os.fstat(lease.fd).st_mode & 0o777)
            self.assertFalse(os.get_inheritable(lease.fd))
            self.assertEqual('/proc/self/fd/' + str(lease.fd), lease.path)
            self.assertEqual(7, lease.max_file_bytes)

    def test_active_export_and_open_writer_prevent_false_cleanup_success(self):
        workspace = self.workspace()
        path = workspace / 'payload'
        with path.open('xb') as writer:
            with self.assertRaises(self.module.WorkspaceError):
                workspace.close()
            writer.write(b'fixture')
        with path.export_for_child(write=False):
            with self.assertRaises(self.module.WorkspaceError):
                workspace.close()
            with self.assertRaises(self.module.WorkspaceError):
                (workspace / 'other').open('xb')
        workspace.close()

    def test_child_output_has_a_budget_and_cannot_grow_other_files(self):
        workspace = self.workspace(max_bytes=16)
        existing, output = workspace / 'input', workspace / 'output'
        with existing.open('xb') as handle:
            handle.write(b'12345')
        with output.export_for_child(write=True) as lease:
            self.assertEqual(11, lease.max_file_bytes)
            os.pwrite(lease.fd, b'12345678901', 0)
        self.assertEqual(b'12345678901', output.read_bytes())
        with (workspace / 'full').open('xb') as handle:
            with self.assertRaises(self.module.WorkspaceError):
                handle.write(b'1')

    def test_child_exceeding_reserved_budget_is_not_accepted(self):
        workspace = self.workspace(max_bytes=8)
        output = workspace / 'output'
        with self.assertRaises(self.module.WorkspaceError):
            with output.export_for_child(write=True) as lease:
                os.pwrite(lease.fd, b'123456789', 0)  # Fake child ignored its RLIMIT.
        output.unlink()

    def test_descriptor_reuse_never_closes_the_unrelated_replacement(self):
        # Deliberately corrupt ownership; this instance must keep reporting the
        # failed close rather than being registered as a successful cleanup.
        workspace = self.module.AnonymousWorkspace(_backend=PortableFDBackend())
        path = workspace / 'payload'
        with path.open('xb') as handle:
            handle.write(b'owned')
        with path.export_for_child(write=False) as lease:
            original = lease.fd
        os.close(original)  # Inject an internal descriptor-ownership violation.
        with tempfile.TemporaryFile() as foreign:
            os.dup2(foreign.fileno(), original)
            try:
                with self.assertRaises(self.module.WorkspaceError):
                    workspace.close()
                os.fstat(original)  # The unrelated descriptor must remain open.
            finally:
                if original != foreign.fileno():
                    os.close(original)

    def test_no_disk_fallback_when_linux_memfd_is_unavailable(self):
        from unittest import mock
        with mock.patch.object(self.module.os, 'memfd_create', None, create=True):
            with self.assertRaises(self.module.WorkspaceError):
                self.module.AnonymousWorkspace()

    def test_child_argument_cannot_be_used_as_a_normal_filesystem_path(self):
        path = self.workspace() / 'payload'
        argument = path.child_argument()
        self.assertIs(path, argument.file)
        with self.assertRaises(TypeError):
            os.fspath(argument)
        for value in (path, argument):
            with self.assertRaises(self.module.WorkspaceError):
                str(value)

    def test_multiple_inputs_and_one_output_share_a_single_bounded_child_lease(self):
        workspace = self.workspace(max_bytes=16)
        first, second, output = workspace / 'first', workspace / 'second', workspace / 'output'
        for path in (first, second):
            with path.open('xb') as handle:
                handle.write(b'123')
        with workspace.export_files(inputs=(first, second), output=output) as leases:
            self.assertEqual([3, 3, 10], [lease.max_file_bytes for lease in leases])
            self.assertEqual(3, len({lease.fd for lease in leases}))
        with self.assertRaises(self.module.WorkspaceError):
            with first.open('wb'):
                pass

    def test_export_cannot_reference_a_different_workspace_or_alias_an_input(self):
        workspace, other = self.workspace(), self.workspace()
        path, foreign = workspace / 'input', other / 'input'
        for value in (path, foreign):
            with value.open('xb') as handle:
                handle.write(b'123')
        for inputs, output in (((path,), path), ((foreign,), None)):
            with self.assertRaises(self.module.WorkspaceError):
                with workspace.export_files(inputs=inputs, output=output):
                    pass


if __name__ == '__main__':
    unittest.main()
