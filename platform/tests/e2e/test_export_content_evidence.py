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
from unittest import mock
import zipfile

import export_content_evidence as evidence

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


def pdf_fixture(path, pages):
    """Small independent PDF with Unicode mapping; no production writer/fonts."""
    objects = [b'', b'',
               b'<< /Type /Font /Subtype /Type0 /BaseFont /Fixture /Encoding /Identity-H /DescendantFonts [4 0 R] /ToUnicode 5 0 R >>',
               b'<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Fixture /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /DW 600 >>']
    chars = sorted(set(''.join(cell for page in pages for row in page for cell in row)))
    mapping = {char: index + 1 for index, char in enumerate(chars)}
    cmap = ('/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n'
            '/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n'
            '/CMapName /Fixture def /CMapType 2 def\n1 begincodespacerange <0000> <ffff> endcodespacerange\n')
    for offset in range(0, len(chars), 100):
        group = chars[offset:offset + 100]
        cmap += str(len(group)) + ' beginbfchar\n'
        cmap += ''.join(f'<{mapping[c]:04x}> <{c.encode("utf-16-be").hex()}>\n' for c in group)
        cmap += 'endbfchar\n'
    cmap += 'endcmap CMapName currentdict /CMap defineresource pop end end'

    def stream(data):
        return f'<< /Length {len(data)} >>\nstream\n'.encode() + data + b'\nendstream'

    objects.append(stream(cmap.encode()))
    page_ids = []
    for page in pages:
        page_id = len(objects) + 1
        page_ids.append(page_id)
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 850] /Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>'.encode())
        commands = []
        for index, row in enumerate(page):
            for column, cell in enumerate(row):
                encoded = ''.join(f'{mapping[c]:04x}' for c in cell)
                commands.append(f'BT /F1 9 Tf 1 0 0 1 {60 + column * 180} {800 - index * 22} Tm <{encoded}> Tj ET')
        objects.append(stream('\n'.join(commands).encode()))
    objects[0] = b'<< /Type /Catalog /Pages 2 0 R >>'
    objects[1] = f'<< /Type /Pages /Count {len(page_ids)} /Kids [{" ".join(str(i) + " 0 R" for i in page_ids)}] >>'.encode()
    data = bytearray(b'%PDF-1.7\n')
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f'{index} 0 obj\n'.encode() + obj + b'\nendobj\n')
    start = len(data)
    data.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode())
    data.extend(''.join(f'{offset:010d} 00000 n \n' for offset in offsets[1:]).encode())
    data.extend(f'trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode())
    path.write_bytes(data)


def fixture(path, fmt, actual, mutation=None):
    # Lazy format imports permit explicit document-only runs; the full suite
    # still fails (never skips) if any required reader dependency is absent.
    codes = FIXED + list(actual['labels'])
    labels = LABELS + list(actual['labels'].values())
    rows = [cells(row, codes) for row in actual['rows']]
    fields = [FIELDS] + [[scalar(field[key]) for key in FIELDS] for field in actual['fields']]
    if fmt == 'xlsx':
        from openpyxl import Workbook
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
    elif fmt == 'pdf':
        pages = [[['答卷 ' + row[2]], ['答卷信息']]
                 + [[label, value] for label, value in zip(LABELS, row[:8])]
                 + [['作答']] + [[label, value] for label, value in zip(labels[8:], row[8:])]
                 for row in rows]
        if mutation == 'section':
            pages[0][-1], pages[1][-1] = pages[1][-1], pages[0][-1]
        elif mutation == 'heading':
            pages[0][0] = ['答卷 999']
        elif mutation == 'extra_section':
            pages[0].append(['附件'])
        elif mutation in ('generation_wrap', 'generation_wrong', 'generation_missing', 'generation_multiline',
                          'generation_misaligned', 'generation_cross_field', 'generation_extra', 'generation_empty_prefix'):
            for page in pages:
                value = page[5][1]
                page[5][1] = value[:24]
                page.insert(6, ['', value[24:]])
            if mutation == 'generation_wrong':
                suffix = pages[0][6][1]
                pages[0][6][1] = ('f' if suffix[0] != 'f' else 'e') + suffix[1:]
            elif mutation == 'generation_missing':
                pages[0][6][1] = pages[0][6][1][:-1]
            elif mutation == 'generation_multiline':
                suffix = pages[0][6][1]
                pages[0][6:7] = [['', suffix[:6]], ['', suffix[6:]]]
            elif mutation == 'generation_misaligned':
                pages[0][6] = [pages[0][6][1]]
            elif mutation == 'generation_cross_field':
                pages[0][6], pages[0][7] = pages[0][7], pages[0][6]
            elif mutation == 'generation_extra':
                pages[0].insert(7, ['', 'abcdef'])
            elif mutation == 'generation_empty_prefix':
                pages[0][6][1] = pages[0][5][1] + pages[0][6][1]
                pages[0][5][1] = ''
        elif mutation in ('timestamp_wrap', 'timestamp_wrong', 'timestamp_extra', 'timestamp_misaligned',
                          'timestamp_cross_field', 'timestamp_excess_continuations', 'timestamp_malformed'):
            for page in pages:
                for index in (8, 7):
                    value = page[index][1]
                    page[index][1] = value[:11]
                    page.insert(index + 1, ['', value[11:]])
            if mutation == 'timestamp_wrong':
                pages[0][8][1] = '09:00:00Z'
            elif mutation == 'timestamp_extra':
                pages[0].insert(9, ['', '09:00:00Z'])
            elif mutation == 'timestamp_misaligned':
                pages[0][8] = [pages[0][8][1]]
            elif mutation == 'timestamp_cross_field':
                pages[0][8], pages[0][10] = pages[0][10], pages[0][8]
            elif mutation == 'timestamp_excess_continuations':
                pages[0][7][1] = '2026-'
                pages[0][8:9] = [['', '11-'], ['', '07T00:'], ['', '00:00Z']]
            elif mutation == 'timestamp_malformed':
                pages[0][8][1] = 'PRIVATE'
        pdf_fixture(path, pages)
    elif fmt == 'report-docx':
        from docx import Document
        doc = Document()
        doc.add_heading('答卷统计报告', level=1)

        def table(data):
            target = doc.add_table(rows=0, cols=len(data[0]))
            for values in data:
                for cell, value in zip(target.add_row().cells, values):
                    cell.text = str(value)

        table([['指标', '数值'], ['答卷总数', len(rows)]])
        for code, label in actual['labels'].items():
            values = [scalar(row['answers'][code]) for row in actual['rows']]
            valid = [value for value in values if value.strip()]
            doc.add_heading(label, level=2)
            table([['统计项', '数量'], ['有效值', len(valid)], ['空值', len(values) - len(valid)]])
            table([['值', '频数', '比例']] + [[value, valid.count(value), f'{100 * valid.count(value) / len(valid):.2f}%']
                                             for value in sorted(set(valid))])
        if mutation == 'section':
            doc.paragraphs[1].text, doc.paragraphs[2].text = doc.paragraphs[2].text, doc.paragraphs[1].text
        elif mutation == 'duplicate_section':
            doc.paragraphs[2].text = doc.paragraphs[1].text
        elif mutation == 'wrong_label':
            doc.paragraphs[1].text = '错误标签'
        elif mutation == 'missing_section':
            doc._element.body.remove(doc.tables[-1]._element)
        elif mutation == 'extra_section':
            doc.add_heading('额外问题', level=2)
        elif mutation == 'missing_value':
            doc.tables[2]._tbl.remove(doc.tables[2].rows[-1]._tr)
        elif mutation == 'extra_value':
            for cell, value in zip(doc.tables[2].add_row().cells, ['A9', '1', '50.00%']):
                cell.text = value
        elif mutation in ('no_grid', 'no_grid_wrong_width', 'no_grid_merged'):
            if mutation == 'no_grid_wrong_width':
                target = doc.tables[2].rows[1]._tr
                target.remove(target.tc_lst[-1])
            elif mutation == 'no_grid_merged':
                doc.tables[2].cell(0, 0).merge(doc.tables[2].cell(0, 1))
            for target in doc.tables:
                target._tbl.remove(target._tbl.tblGrid)
        elif mutation:
            target, row, column, value = {
                'total': (0, 1, 1, '3'), 'valid': (1, 1, 1, '0'),
                'missing': (1, 2, 1, '2'), 'column': (2, 0, 1, '数量'),
                'frequency': (2, 1, 1, '2'), 'percent': (2, 1, 2, '100.00%'),
                'value': (2, 1, 0, 'A9'), 'duplicate_value': (2, 2, 0, 'A1'),
            }[mutation]
            doc.tables[target].cell(row, column).text = value
        doc.save(path)
    elif fmt == 'docx':
        from docx import Document
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
        import pandas as pd
        import pyreadstat
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
        for fmt in ('xlsx', 'sav', 'docx', 'pdf', 'report-docx'):
            with self.subTest(fmt=fmt):
                self.assertEqual(self.check(fmt), dict(ok=True, format=fmt, rows=2,
                                                      fields=0 if fmt in ('docx', 'pdf', 'report-docx') else 4))

    def test_pdf_and_report_are_supported(self):
        for fmt in ('pdf', 'report-docx'):
            with self.subTest(fmt=fmt):
                self.assertEqual(self.check(fmt), dict(ok=True, format=fmt, rows=2, fields=0))

    def test_pdf_response_association_and_structure(self):
        for mutation in ('section', 'heading', 'extra_section'):
            with self.subTest(mutation=mutation), self.assertRaises(evidence.EvidenceError):
                self.check('pdf', mutation=mutation)

    def test_pdf_wrapped_timestamps_remain_exact_and_bounded(self):
        for row in self.expected['rows']:
            row['startedat'] = '2026-11-07T00:00:00Z'
            row['completedat'] = '2026-11-07T00:01:00Z'
        self.assertTrue(self.check('pdf', mutation='timestamp_wrap')['ok'])
        for mutation in ('timestamp_wrong', 'timestamp_extra', 'timestamp_misaligned', 'timestamp_cross_field',
                         'timestamp_excess_continuations', 'timestamp_malformed'):
            with self.subTest(mutation=mutation), self.assertRaises(evidence.EvidenceError):
                self.check('pdf', mutation=mutation)

    def test_pdf_wrapped_generation_requires_exact_known_uuid(self):
        # Fixed independent UUIDv4 identities. The real-writer reproduction
        # splits mixed-hex UUIDs after 24 characters, unlike the old short IDs.
        for row, value in zip(self.expected['rows'], (
                '0d84a2e1-7b3c-4fa9-8cde-123456789abc',
                'b5e9183a-8c2d-4a6f-9e01-fedcba987654')):
            row['generation'] = value
        self.assertTrue(self.check('pdf', mutation='generation_wrap')['ok'])
        for mutation in ('generation_wrong', 'generation_missing', 'generation_multiline',
                         'generation_misaligned', 'generation_cross_field', 'generation_extra', 'generation_empty_prefix'):
            with self.subTest(mutation=mutation), self.assertRaises(evidence.EvidenceError):
                self.check('pdf', mutation=mutation)
        self.expected['rows'][0]['generation'] = 'x' * 36
        with self.assertRaises(evidence.EvidenceError):
            self.check('pdf', mutation='generation_wrap')

    def test_report_without_grid_preserves_cell_width_checks(self):
        self.assertTrue(self.check('report-docx', mutation='no_grid')['ok'])
        for mutation in ('no_grid_wrong_width', 'no_grid_merged'):
            with self.subTest(mutation=mutation), self.assertRaises(evidence.EvidenceError):
                self.check('report-docx', mutation=mutation)

    def test_report_exact_statistics_and_section_associations(self):
        for mutation in ('section', 'duplicate_section', 'wrong_label', 'missing_section', 'extra_section',
                         'missing_value', 'extra_value', 'total', 'valid', 'missing',
                         'column', 'frequency', 'percent', 'value', 'duplicate_value'):
            with self.subTest(mutation=mutation), self.assertRaises(evidence.EvidenceError):
                self.check('report-docx', mutation=mutation)

    def test_report_missing_values_and_nonuniform_frequencies(self):
        self.expected['rows'][1]['answers']['Q2'] = ''
        self.expected['rows'][1]['answers']['Q1'] = 'A1'
        self.assertTrue(self.check('report-docx')['ok'])
        self.expected['rows'][0]['answers']['Q2'] = ''
        self.assertTrue(self.check('report-docx')['ok'])
        third = copy.deepcopy(self.expected['rows'][0])
        third['responseid'] = 2
        third['answers']['Q1'] = 'A2'
        self.expected['rows'].append(third)
        self.assertTrue(self.check('report-docx')['ok'])

    def test_pdf_bounds_timeout_dependency_and_invalid_extraction(self):
        path = self.root / 'bounded.pdf'
        path.write_bytes(b'%PDF-1.7\n' + b'x' * 100)
        with mock.patch.object(evidence, 'MAX_FILE_BYTES', 64), self.assertRaises(evidence.EvidenceError):
            evidence.verify('pdf', path, self.expected)
        with mock.patch.object(evidence.subprocess, 'Popen', side_effect=FileNotFoundError('PRIVATE')):
            with self.assertRaisesRegex(evidence.EvidenceError, '^dependency$'):
                evidence.verify('pdf', path, self.expected)
        launch = subprocess.Popen
        for script, category in [('import time; time.sleep(10)', 'reader'),
                                 ('import sys; sys.stdout.write("X" * 4096)', 'structure'),
                                 ('import sys; sys.exit(1)', 'reader')]:
            def fake_poppler(*args, **kwargs):
                return launch([sys.executable, '-c', script], **kwargs)
            with self.subTest(category=category), mock.patch.object(evidence, 'PDF_TIMEOUT_SECONDS', 0.5), \
                    mock.patch.object(evidence, 'MAX_PDF_TEXT_BYTES', 1024), \
                    mock.patch.object(evidence.subprocess, 'Popen', side_effect=fake_poppler):
                with self.assertRaisesRegex(evidence.EvidenceError, '^' + category + '$'):
                    evidence.verify('pdf', path, self.expected)
        for text in ('', 'PRIVATE', '\f', '\f\f\f'):
            with self.subTest(extraction='invalid'), mock.patch.object(evidence, 'pdf_text', return_value=text), \
                    self.assertRaises(evidence.EvidenceError):
                evidence.verify('pdf', path, self.expected)

    def test_document_complex_scope_fails_closed(self):
        for fmt in ('pdf', 'report-docx'):
            path = fixture(self.root / ('scope.' + fmt), fmt, self.expected)
            for value in ('two\nlines', '=1+1', ' padded ', 'x' * 101):
                expected = copy.deepcopy(self.expected)
                expected['rows'][0]['answers']['Q1'] = value
                with self.subTest(fmt=fmt), self.assertRaisesRegex(evidence.EvidenceError, '^expectation$'):
                    evidence.verify(fmt, path, expected)

    def test_pdf_and_report_cli_failure_is_private(self):
        expected = self.root / 'PRIVATE_EXPECTATION.json'
        expected.write_text(json.dumps(self.expected), encoding='utf8')
        for fmt, category in [('pdf', 'structure'), ('report-docx', 'archive')]:
            path = self.root / ('PRIVATE_DOWNLOAD.' + fmt)
            path.write_bytes(b'PRIVATE_ANSWER malformed')
            result = subprocess.run([sys.executable, str(Path(evidence.__file__)), fmt, str(path), str(expected)],
                                    capture_output=True, text=True, timeout=15, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout), {'ok': False, 'category': category})
            self.assertEqual(result.stderr, '')

    def test_swapped_answers_rejected_even_when_all_values_present(self):
        for fmt in ('xlsx', 'sav', 'docx', 'pdf'):
            actual = copy.deepcopy(self.expected)
            actual['rows'][0]['answers'], actual['rows'][1]['answers'] = (
                actual['rows'][1]['answers'], actual['rows'][0]['answers'])
            with self.subTest(fmt=fmt), self.assertRaises(evidence.EvidenceError):
                self.check(fmt, actual)

    def test_wrong_identity_generation_status_and_duplicate_identity_rejected(self):
        for fmt in ('xlsx', 'sav', 'docx', 'pdf'):
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
        for fmt in ('xlsx', 'sav', 'docx', 'pdf', 'report-docx'):
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
        for fmt in ('xlsx', 'sav', 'docx', 'pdf', 'report-docx'):
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
        for fmt in ('xlsx', 'sav', 'docx', 'pdf'):
            actual = copy.deepcopy(self.expected)
            self.expected['rows'][0]['startedat'] = '2026-11-07T00:00:00Z'
            with self.subTest(fmt=fmt), self.assertRaises(evidence.EvidenceError):
                self.check(fmt, actual)
            del self.expected['rows'][0]['startedat']

    def test_bounded_archive_rejects_malformed_traversal_duplicate_and_excess_parts(self):
        for fmt in ('xlsx', 'sav', 'docx', 'report-docx'):
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
