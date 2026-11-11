"""Proposed sixth-gate behavior using only fictional matrices."""
import os
from pathlib import Path
from unittest import mock
from reqtrace import check, index
from tests.test_check import GatesGoRedTest, MATRIX_STUB

MATRIX_FILE = Path('platform/docs/traceability/02-全量需求追踪矩阵.md')

class MatrixRequiredCandidate(GatesGoRedTest):
    def test_default_missing_matrix_is_an_error(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(index, "DEFAULT_MATRIX", self.root / "missing-outside") :
            report = check.run(self.root)
        self.assertFalse(report.matrix_checked)
        self.assertTrue(any('02 矩阵' in problem for problem in report.problems), report.problems)

    def test_default_reads_fixture_repository_matrix(self):
        self._write(MATRIX_FILE, MATRIX_STUB)
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(index, "DEFAULT_MATRIX", self.root / "missing-outside") :
            report = check.run(self.root)
        self.assertTrue(report.matrix_checked)
        self.assertEqual([], report.problems)

    def test_default_detects_new_requirement(self):
        self._write(MATRIX_FILE, MATRIX_STUB + '| R01-03 | 虚构条目 | C | 断言 |\n')
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(index, "DEFAULT_MATRIX", self.root / "missing-outside") :
            report = check.run(self.root)
        self.assertTrue(report.matrix_checked)
        self.assertTrue(any('R01-03' in p for p in report.problems), report.problems)

    def test_explicit_override_reads_only_selected_fixture(self):
        self._write(MATRIX_FILE, MATRIX_STUB + '| R01-03 | 虚构条目 | C | 断言 |\n')
        override = self.root / 'fixture-matrix.md'
        override.write_text(MATRIX_STUB, encoding='utf-8')
        with mock.patch.dict(os.environ, {'REQUIREMENT_MATRIX': str(override)}, clear=True):
            report = check.run(self.root)
        self.assertTrue(report.matrix_checked)
        self.assertEqual([], report.problems)

    def test_invalid_override_fails_even_if_repository_matrix_exists(self):
        self._write(MATRIX_FILE, MATRIX_STUB)
        with mock.patch.dict(os.environ, {'REQUIREMENT_MATRIX': str(self.root / 'absent')}, clear=True):
            report = check.run(self.root)
        self.assertFalse(report.matrix_checked)
        self.assertTrue(any('02 矩阵' in p for p in report.problems), report.problems)

    def test_sync_uses_repository_matrix(self):
        from reqtrace import cli
        self._write(MATRIX_FILE, MATRIX_STUB)
        with mock.patch.object(cli, 'REPO_ROOT', self.root), mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(index, 'DEFAULT_MATRIX', self.root / 'missing-outside'):
            self.assertEqual(0, cli._sync())
        self.assertTrue((self.root / check.INDEX_FILE).is_file())

    def test_sync_missing_matrix_fails_without_modifying_index(self):
        from reqtrace import cli
        before = (self.root / check.INDEX_FILE).read_bytes()
        with mock.patch.object(cli, 'REPO_ROOT', self.root), mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(index, 'DEFAULT_MATRIX', self.root / 'missing-outside'):
            self.assertNotEqual(0, cli._sync())
        self.assertEqual(before, (self.root / check.INDEX_FILE).read_bytes())
