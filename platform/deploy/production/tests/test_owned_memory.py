"""Workspace contract tests. The injected portable FD backend is not memfd proof."""
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


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

    def test_private_live_reference_pins_identity_and_is_released_on_rejection(self):
        workspace = self.module.AnonymousWorkspace(_backend=PortableFDBackend())
        path = workspace / 'payload'
        with path.open('xb') as handle: handle.write(b'owned')
        original, anchor = path._entry['fd'], path._entry.get('anchor')
        self.assertIsInstance(anchor, int)
        self.assertNotEqual(original, anchor)
        self.assertFalse(os.get_inheritable(anchor))
        identity = os.fstat(anchor).st_dev, os.fstat(anchor).st_ino
        os.close(original)
        foreign = PortableFDBackend().create('foreign')
        if foreign != original: os.dup2(foreign, original, inheritable=False)
        try:
            os.pwrite(original, b'foreign', 0)
            self.assertEqual(b'owned', os.pread(anchor, 5, 0))
            self.assertNotEqual(identity, (os.fstat(original).st_dev, os.fstat(original).st_ino))
            for operation in (path.read_bytes, path.unlink, workspace.close, workspace.close):
                with self.assertRaises(self.module.WorkspaceError): operation()
            self.assertEqual(b'foreign', os.pread(original, 7, 0))
            with self.assertRaises(OSError): os.fstat(anchor)
        finally:
            if foreign != original: os.close(original)
            os.close(foreign)

    def test_unlink_and_normal_close_release_both_noninheritable_descriptors(self):
        workspace = self.workspace()
        for number in range(8):
            path = workspace / str(number)
            with path.open('xb') as handle: handle.write(b'owned')
            fds = path._entry['fd'], path._entry.get('anchor')
            self.assertTrue(all(type(fd) is int and not os.get_inheritable(fd) for fd in fds))
            path.unlink()
            for fd in fds:
                with self.assertRaises(OSError): os.fstat(fd)
        path = workspace / 'last'
        with path.open('xb') as handle: handle.write(b'owned')
        fds = path._entry['fd'], path._entry['anchor']
        workspace.close()
        for fd in fds:
            with self.assertRaises(OSError): os.fstat(fd)

    def test_reference_allocation_failure_releases_original_descriptor(self):
        fd = PortableFDBackend().create('allocation-fault')
        backend = mock.Mock()
        backend.create.return_value = fd
        workspace = self.module.AnonymousWorkspace(_backend=backend)
        with mock.patch.object(self.module.os, 'dup', side_effect=OSError('synthetic allocation failure')):
            with self.assertRaises(OSError): (workspace / 'payload').open('xb')
        with self.assertRaises(OSError): os.fstat(fd)
        workspace.close()

    def test_reference_protection_failure_releases_both_allocated_descriptors(self):
        fd = PortableFDBackend().create('protection-fault')
        backend = mock.Mock()
        backend.create.return_value = fd
        duplicated, duplicate = [], os.dup
        def record_duplicate(value):
            result = duplicate(value)
            duplicated.append(result)
            return result
        workspace = self.module.AnonymousWorkspace(_backend=backend)
        with mock.patch.object(self.module.os, 'dup', side_effect=record_duplicate), \
             mock.patch.object(self.module.os, 'set_inheritable', side_effect=[None, OSError('synthetic protection failure')]):
            with self.assertRaises(OSError): (workspace / 'payload').open('xb')
        self.assertEqual(1, len(duplicated))
        for value in (fd, *duplicated):
            with self.assertRaises(OSError): os.fstat(value)
        workspace.close()

    def test_anchor_close_failure_blocks_recreation_and_never_retries_close(self):
        for operation in ('unlink', 'close'):
            for replaced in (False, True):
                with self.subTest(operation=operation, replaced=replaced):
                    workspace = self.module.AnonymousWorkspace(_backend=PortableFDBackend())
                    path = workspace / 'payload'
                    with path.open('xb') as handle: handle.write(b'owned')
                    public, anchor = path._entry['fd'], path._entry['anchor']
                    identity, close, attempts = path._entry['identity'], os.close, []
                    with tempfile.TemporaryFile() as foreign:
                        def fail_reference(fd):
                            if fd == anchor:
                                attempts.append(fd)
                                if replaced:
                                    close(fd)
                                    os.dup2(foreign.fileno(), fd, inheritable=False)
                                raise OSError('synthetic uncertain close')
                            close(fd)
                        try:
                            with mock.patch.object(self.module.os, 'close', side_effect=fail_reference):
                                with self.assertRaises(self.module.WorkspaceError):
                                    (path.unlink if operation == 'unlink' else workspace.close)()
                                self.assertTrue(workspace._close_failed)
                                self.assertIsNone(path._entry['fd'])
                                self.assertEqual(anchor, path._entry['anchor'])
                                self.assertEqual(identity, path._entry['identity'])
                                with mock.patch.object(workspace._backend, 'create') as allocate:
                                    for action in (lambda: path.open('xb'), lambda: workspace / 'other', workspace.close):
                                        with self.assertRaises(self.module.WorkspaceError): action()
                                    with self.assertRaises(self.module.WorkspaceError):
                                        with path.export_for_child(write=True): pass
                                    allocate.assert_not_called()
                                self.assertEqual([anchor], attempts)
                            os.fstat(anchor)  # Unknown outcome is not a licence to close again.
                            if replaced:
                                os.pwrite(anchor, b'foreign', 0)
                                self.assertEqual(b'foreign', os.pread(foreign.fileno(), 7, 0))
                            with self.assertRaises(OSError): os.fstat(public)
                        finally:
                            close(anchor)  # Independent fault-fixture disposal, not workspace recovery.

    def test_allocation_cleanup_unknown_preserves_references_without_retry(self):
        workspace = self.module.AnonymousWorkspace(_backend=PortableFDBackend())
        path = workspace / 'payload'
        close, attempts = os.close, []
        def fail_close(fd):
            attempts.append(fd)
            raise OSError('synthetic cleanup failure')
        # Backend allocation happens first; fault injection then concerns only
        # the workspace's own duplicate and its error-cleanup path.
        fd = PortableFDBackend().create('allocation-cleanup-fault')
        workspace._backend = mock.Mock()
        workspace._backend.create.return_value = fd
        try:
            with mock.patch.object(self.module.os, 'set_inheritable', side_effect=[None, OSError('protection failure')]), \
                 mock.patch.object(self.module.os, 'close', side_effect=fail_close):
                with self.assertRaises(self.module.WorkspaceError): path.open('xb')
                self.assertTrue(workspace._close_failed)
                self.assertEqual(fd, path._entry['fd'])
                self.assertEqual([path._entry['anchor']], attempts)
                for action in (lambda: path.open('xb'), workspace.close):
                    with self.assertRaises(self.module.WorkspaceError): action()
                self.assertEqual(1, len(attempts))
        finally:
            close(path._entry['anchor'])
            close(fd)

    def test_new_allocation_after_known_retirement_has_its_own_close_attempt(self):
        workspace = self.workspace()
        path = workspace / 'payload'
        with path.open('xb') as handle: handle.write(b'owned')
        path.unlink()
        fd = PortableFDBackend().create('new-allocation')
        backend = mock.Mock()
        backend.create.return_value = fd
        workspace._backend = backend
        with mock.patch.object(self.module.os, 'set_inheritable', side_effect=[None, OSError('protection failure')]):
            with self.assertRaises(OSError): path.open('xb')
        self.assertFalse(workspace._close_failed)
        self.assertIsNone(path._entry['anchor'])
        with self.assertRaises(OSError): os.fstat(fd)

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
