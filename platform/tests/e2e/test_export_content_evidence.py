"""Synthetic independent-format fixtures, including preserved corrupt examples.

Run with the pinned disposable venv: python -m unittest discover -s
platform/tests/e2e -p test_export_content_evidence.py -v
No production writer is imported or used as the fixture oracle.
"""
import copy
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile

import export_content_evidence as evidence

import pandas as pd
import pyreadstat
from docx import Document
from openpyxl import Workbook

FIXED = ['version', 'sid', 'responseid', 'generation', 'state',
         'startedat', 'completedat', 'answersstatus']
LABELS = ['版本', '引擎问卷号', '答卷号', '代次', '状态', '开始时间', '完成时间', '作答状态']
FIELDS = ['version', 'sid', 'column', 'fieldname', 'questioncode', 'type',
          'aid', 'scale', 'label', 'sensitive', 'masking', 'options']
VARIABLES = ['name', 'code', 'label', 'measure', 'width', 'valuelabels']


def scalar(value):
    return str(value).lower() if isinstance(value, bool) else str(value)


def expectation():
    labels = {'Q1': '您选择的城市？', 'Q2': '补充说明'}
    rows, fields = [], []
    for version in (1, 2):
        sid = 700 + version
        rows.append(dict(version=version, sid=sid, responseid=1,
                         generation=1, state='engine_completed', answersstatus='available',
                         answers={'Q1': 'A1' if version == 1 else 'A2',
                                  'Q2': '独立核对甲' if version == 1 else '独立核对乙'}))
        for code, label in labels.items():
            fields.append(dict(version=version, sid=sid, column=code,
                               fieldname=f'{sid}X9X{10 if code == "Q1" else 11}',
                               questioncode=code, type='L' if code == 'Q1' else 'T',
                               aid='', scale=0, label=label, sensitive=False, masking='',
                               options='A1=北京; A2=上海' if code == 'Q1' else ''))
    return dict(rows=rows, fields=fields, labels=labels)


def cells(row, codes):
    return [scalar(row.get(code, '')) if code in FIXED else row['answers'][code]
            for code in codes]


def csv_bytes(rows):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerows(rows)
    return ('\ufeff' + stream.getvalue()).encode('utf-8')


def fixture(path, fmt, actual, mutation=None):
    codes = FIXED + list(actual['labels'])
    labels = LABELS + list(actual['labels'].values())
    rows = [cells(row, codes) for row in actual['rows']]
    fields = [FIELDS] + [[scalar(field[key]) for key in FIELDS] for field in actual['fields']]
    if fmt == 'xlsx':
        book = Workbook()
        sheet = book.active
        sheet.title = '答卷'
        for row in [codes, labels] + rows:
            sheet.append(row)
        for row in fields:
            if '字段字典' not in book.sheetnames:
                book.create_sheet('字段字典')
            book['字段字典'].append(row)
        book.create_sheet('附件清单').append(['version'])
        book.create_sheet('扩展表作答').append(['version'])
        if mutation == 'formula':
            sheet.cell(3, 9, '=1+1')
        book.save(path)
        book.close()
    elif fmt == 'docx':
        doc = Document()
        for index, row in enumerate(rows):
            if index:
                doc.add_page_break()
            doc.add_heading('答卷 ' + row[2], level=1)
            doc.add_heading('答卷信息', level=2)
            table = doc.add_table(rows=0, cols=2)
            for label, value in zip(LABELS, row[:8]):
                target = table.add_row().cells
                target[0].text, target[1].text = label, value
            doc.add_heading('作答', level=2)
            table = doc.add_table(rows=0, cols=2)
            for label, value in zip(labels[8:], row[8:]):
                target = table.add_row().cells
                target[0].text, target[1].text = label, value
        if mutation == 'section':
            doc.tables[1].cell(0, 1).text = 'A2'
        elif mutation == 'heading':
            doc.paragraphs[0].text = '答卷 999'
        doc.save(path)
    else:
        frame = pd.DataFrame(rows, columns=codes)
        for code in FIXED[:4]:
            frame[code] = frame[code].astype(float)
        sav_labels = list(labels)
        if mutation == 'sav_label':
            sav_labels[-2] = '错误的标签'
        # Keep the dataset separate even when the bundle itself ends in .sav.
        # The context also cleans up when the writer or read fails.
        with tempfile.TemporaryDirectory(prefix='sav-fixture-', dir=path.parent) as directory:
            sav_path = Path(directory) / 'responses.sav'
            pyreadstat.write_sav(frame, str(sav_path), column_labels=sav_labels)
            sav_bytes = sav_path.read_bytes()
        variables = [VARIABLES]
        for code, label in zip(codes, labels):
            numeric = code in FIXED[:4]
            width = 8 if numeric else max(1, *(len(str(x).encode('utf8')) for x in frame[code]))
            variables.append([code, code, label, 'numeric' if numeric else 'string', width, ''])
        if mutation == 'mapping':
            variables[-1][0], variables[-2][0] = variables[-2][0], variables[-1][0]
        elif mutation == 'duplicate_mapping':
            variables.append(variables[-1])
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('responses.sav', sav_bytes)
            archive.writestr('variables.csv', csv_bytes(variables))
            archive.writestr('fields.csv', csv_bytes(fields))
            archive.writestr('attachments.csv', csv_bytes([['version']]))
            archive.writestr('extensions.csv', csv_bytes([['version']]))
    return path


class ExportEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='export-evidence-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.expected = expectation()

    def check(self, fmt, actual=None, mutation=None):
        path = fixture(self.root / ('download.' + ('zip' if fmt == 'sav' else fmt)),
                       fmt, actual or self.expected, mutation)
        return evidence.verify(fmt, path, self.expected)

    def test_independent_readers_accept_exact_synthetic_content(self):
        for fmt in ('xlsx', 'sav', 'docx'):
            with self.subTest(fmt=fmt):
                self.assertEqual(self.check(fmt), dict(ok=True, format=fmt, rows=2,
                                                      fields=0 if fmt == 'docx' else 4))

    def test_swapped_answers_rejected_even_when_all_values_present(self):
        for fmt in ('xlsx', 'sav', 'docx'):
            actual = copy.deepcopy(self.expected)
            actual['rows'][0]['answers'], actual['rows'][1]['answers'] = (
                actual['rows'][1]['answers'], actual['rows'][0]['answers'])
            with self.subTest(fmt=fmt), self.assertRaises(evidence.EvidenceError):
                self.check(fmt, actual)

    def test_wrong_identity_generation_status_and_duplicate_identity_rejected(self):
        for fmt in ('xlsx', 'sav', 'docx'):
            for key, value in [('sid', 999), ('version', 9), ('responseid', 7),
                               ('generation', 4), ('state', 'deleted'), ('answersstatus', 'missing')]:
                actual = copy.deepcopy(self.expected)
                actual['rows'][0][key] = value
                with self.subTest(fmt=fmt, key=key), self.assertRaises(evidence.EvidenceError):
                    self.check(fmt, actual)
            actual = copy.deepcopy(self.expected)
            actual['rows'][1] = copy.deepcopy(actual['rows'][0])
            with self.subTest(fmt=fmt, key='duplicate'), self.assertRaises(evidence.EvidenceError):
                self.check(fmt, actual)

    def test_missing_and_extra_response_rejected(self):
        for fmt in ('xlsx', 'sav', 'docx'):
            for extra in (False, True):
                actual = copy.deepcopy(self.expected)
                if extra:
                    actual['rows'].append(copy.deepcopy(actual['rows'][0]))
                else:
                    actual['rows'].pop()
                with self.subTest(fmt=fmt, extra=extra), self.assertRaises(evidence.EvidenceError):
                    self.check(fmt, actual)

    def test_missing_extra_or_wrong_dictionary_field_rejected(self):
        for fmt in ('xlsx', 'sav'):
            for change in ('missing', 'extra', 'fieldname', 'label', 'options'):
                actual = copy.deepcopy(self.expected)
                if change == 'missing':
                    actual['fields'].pop()
                elif change == 'extra':
                    actual['fields'].append(copy.deepcopy(actual['fields'][0]))
                else:
                    actual['fields'][0][change] = 'incorrect'
                with self.subTest(fmt=fmt, change=change), self.assertRaises(evidence.EvidenceError):
                    self.check(fmt, actual)

    def test_wrong_or_missing_answer_column_label_rejected(self):
        for fmt in ('xlsx', 'sav', 'docx'):
            for missing in (False, True):
                actual = copy.deepcopy(self.expected)
                if missing:
                    del actual['labels']['Q2']
                else:
                    actual['labels']['Q1'] = '错误标签'
                with self.subTest(fmt=fmt, missing=missing), self.assertRaises(evidence.EvidenceError):
                    self.check(fmt, actual)

    def test_sav_mapping_and_embedded_label_disagreements_rejected(self):
        for mutation in ('mapping', 'duplicate_mapping', 'sav_label'):
            with self.subTest(mutation=mutation), self.assertRaises(evidence.EvidenceError):
                self.check('sav', mutation=mutation)

    def test_docx_section_value_and_heading_identity_must_match(self):
        for mutation in ('section', 'heading'):
            with self.subTest(mutation=mutation), self.assertRaises(evidence.EvidenceError):
                self.check('docx', mutation=mutation)

    def test_formula_is_not_a_literal_answer(self):
        with self.assertRaises(evidence.EvidenceError):
            self.check('xlsx', mutation='formula')

    def test_declared_timestamp_is_compared(self):
        for fmt in ('xlsx', 'sav', 'docx'):
            actual = copy.deepcopy(self.expected)
            self.expected['rows'][0]['startedat'] = '2026-11-07T00:00:00Z'
            with self.subTest(fmt=fmt), self.assertRaises(evidence.EvidenceError):
                self.check(fmt, actual)
            del self.expected['rows'][0]['startedat']

    def test_bounded_archive_rejects_malformed_traversal_duplicate_and_excess_parts(self):
        for fmt in ('xlsx', 'sav', 'docx'):
            for change in ('malformed', 'traversal', 'duplicate', 'excess', 'doctype', 'utf16_doctype'):
                path = fixture(self.root / ('bad.' + fmt), fmt, self.expected)
                if change == 'malformed':
                    path.write_bytes(b'not an archive')
                else:
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore', UserWarning)
                        with zipfile.ZipFile(path, 'a') as archive:
                            if change == 'traversal':
                                archive.writestr('../unexpected', b'x')
                            elif change == 'duplicate':
                                name = archive.namelist()[0]
                                archive.writestr(name, b'x')
                            elif change in ('doctype', 'utf16_doctype'):
                                xml = '<!DOCTYPE foo [<!ENTITY x "secret">]><foo/>'
                                archive.writestr('unsafe.xml', xml.encode('utf-16' if change == 'utf16_doctype' else 'utf-8'))
                            else:
                                for i in range(129):
                                    archive.writestr(f'extra{i}', b'x')
                with self.subTest(fmt=fmt, change=change), self.assertRaises(evidence.EvidenceError):
                    evidence.verify(fmt, path, self.expected)

    def test_cli_never_prints_private_paths_values_or_reader_tracebacks(self):
        expected = self.root / 'PRIVATE_EXPECTATION.json'
        expected.write_text(json.dumps(self.expected), encoding='utf8')
        path = self.root / 'PRIVATE_DOWNLOAD.xlsx'
        path.write_bytes(b'PRIVATE_ANSWER malformed')
        script = Path(evidence.__file__)
        result = subprocess.run([sys.executable, str(script), 'xlsx', str(path), str(expected)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout), {'ok': False, 'category': 'archive'})
        self.assertEqual(result.stderr, '')
        self.assertNotIn('PRIVATE', result.stdout)
        self.assertNotIn(str(self.root), result.stdout)

    def test_expected_schema_cannot_weaken_identity_or_answer_checks(self):
        path = fixture(self.root / 'valid.xlsx', 'xlsx', self.expected)
        for change in ('missing_status', 'missing_answer', 'duplicate_identity', 'unsupported_code'):
            expected = copy.deepcopy(self.expected)
            if change == 'missing_status':
                del expected['rows'][0]['state']
            elif change == 'missing_answer':
                del expected['rows'][0]['answers']['Q1']
            elif change == 'duplicate_identity':
                expected['rows'][1] = copy.deepcopy(expected['rows'][0])
            else:
                expected['labels']['Q[1]'] = expected['labels'].pop('Q1')
            with self.subTest(change=change), self.assertRaises(evidence.EvidenceError):
                evidence.verify('xlsx', path, expected)


if __name__ == '__main__':
    unittest.main()
