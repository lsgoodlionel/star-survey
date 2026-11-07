#!/usr/bin/env python3
"""Bounded test-only content evidence for a small, synthetic Wave 3 fixture.

CLI: python export_content_evidence.py {xlsx|sav|docx} DOWNLOAD EXPECTED_JSON
Expected: rows with version/sid/responseid/generation/state/answersstatus and
answers keyed by simple ASCII question code; optional exact startedat/completedat;
fields with all 12 exported dictionary columns; labels keyed in column order.
The expectation comes from independently observed submitted answers and published
field definitions, NEVER from another downloaded export. No native GUI or general
Excel/SPSS/Word compatibility claim follows from these independent reader checks.

Install export-evidence-requirements.txt only in a disposable venv. DOCX contains
no field dictionary, so its success fields count is zero. Only CLI JSON is public;
neither library exceptions, paths, labels, identities nor answer values are logged.
"""
import contextlib
import csv
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import warnings
import zipfile

FIXED_CODES = ['version', 'sid', 'responseid', 'generation', 'state',
               'startedat', 'completedat', 'answersstatus']
FIXED_LABELS = ['版本', '引擎问卷号', '答卷号', '代次', '状态', '开始时间', '完成时间', '作答状态']
FIELD_CODES = ['version', 'sid', 'column', 'fieldname', 'questioncode', 'type',
               'aid', 'scale', 'label', 'sensitive', 'masking', 'options']
VARIABLE_CODES = ['name', 'code', 'label', 'measure', 'width', 'valuelabels']
IDENTITY = FIXED_CODES[:4]
REQUIRED_ROW = IDENTITY + ['state', 'answersstatus']
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_PART_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 24 * 1024 * 1024
MAX_EXPECTED_BYTES = 1024 * 1024
MAX_PARTS = 128
MAX_ROWS = 100
MAX_FIELDS = 1000
MAX_COLUMNS = 128
MAX_CELL_CHARS = 32767


class EvidenceError(Exception):
    """Fixed categories only; never attach reader exceptions or artifact content."""


def require(condition, category):
    if not condition:
        raise EvidenceError(category)


def scalar(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        require(math.isfinite(value), 'content')
        return str(int(value)) if value.is_integer() else str(value)
    require(isinstance(value, str) and len(value) <= MAX_CELL_CHARS, 'content')
    return value


def identity(row):
    return tuple(row[key] for key in IDENTITY)


def normalize_expected(expected):
    require(isinstance(expected, dict) and set(expected) == {'rows', 'fields', 'labels'}, 'expectation')
    rows, fields, labels = expected['rows'], expected['fields'], expected['labels']
    require(isinstance(rows, list) and 0 < len(rows) <= MAX_ROWS, 'expectation')
    require(isinstance(fields, list) and 0 < len(fields) <= MAX_FIELDS, 'expectation')
    require(isinstance(labels, dict) and 0 < len(labels) <= MAX_COLUMNS - len(FIXED_CODES), 'expectation')
    # Deliberately reject complex production naming rather than reimplement the
    # exporter's name sanitization and accidentally use it as our own oracle.
    require(all(isinstance(code, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', code)
                and code.lower() not in FIXED_CODES for code in labels), 'expectation')
    require(len({code.lower() for code in labels}) == len(labels), 'expectation')
    require(all(isinstance(label, str) and 0 < len(label) <= MAX_CELL_CHARS
                for label in labels.values()), 'expectation')
    normalized_rows = []
    for row in rows:
        require(isinstance(row, dict) and set(REQUIRED_ROW + ['answers']) <= set(row)
                and set(row) <= set(FIXED_CODES + ['answers']), 'expectation')
        require(isinstance(row['answers'], dict) and set(row['answers']) == set(labels), 'expectation')
        require(all(isinstance(value, (str, int, bool)) for key, value in row.items() if key != 'answers'),
                'expectation')
        require(all(isinstance(value, (str, int, bool)) for value in row['answers'].values()), 'expectation')
        clean = {key: scalar(value) for key, value in row.items() if key != 'answers'}
        require(all(clean[key] for key in REQUIRED_ROW), 'expectation')
        clean.update({code: scalar(row['answers'][code]) for code in labels})
        normalized_rows.append(clean)
    require(len({identity(row) for row in normalized_rows}) == len(rows), 'expectation')
    normalized_fields = []
    for field in fields:
        require(isinstance(field, dict) and set(field) == set(FIELD_CODES), 'expectation')
        require(all(isinstance(value, (str, int, bool)) for value in field.values()), 'expectation')
        clean = {key: scalar(value) for key, value in field.items()}
        require(clean['column'] in labels and clean['questioncode'] == clean['column']
                and clean['aid'] == '' and clean['scale'] == '0' and clean['sensitive'] == 'false'
                and clean['masking'] == '' and bool(clean['fieldname']), 'expectation')
        normalized_fields.append(clean)
    field_ids = {(field['version'], field['sid'], field['column']) for field in normalized_fields}
    require(len(field_ids) == len(fields), 'expectation')
    require(field_ids == {(row['version'], row['sid'], code)
                          for row in normalized_rows for code in labels}, 'expectation')
    return normalized_rows, normalized_fields, labels


def bounded_archive(path):
    """Read a frozen bounded snapshot; never extract paths from ZIP metadata."""
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        require(0 < len(raw) <= MAX_FILE_BYTES, 'archive')
        parts = {}
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            require(0 < len(infos) <= MAX_PARTS, 'archive')
            require(sum(info.file_size for info in infos) <= MAX_TOTAL_BYTES, 'archive')
            for info in infos:
                name = info.filename
                pure = PurePosixPath(name)
                require(name and '\\' not in name and ':' not in name and '\x00' not in name
                        and not pure.is_absolute() and '..' not in pure.parts
                        and str(pure) == name.rstrip('/') and name not in parts, 'archive')
                require(not stat.S_ISLNK(info.external_attr >> 16) and not info.flag_bits & 1
                        and info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                        and 0 <= info.file_size <= MAX_PART_BYTES, 'archive')
                with archive.open(info) as member:
                    data = member.read(MAX_PART_BYTES + 1)
                require(len(data) == info.file_size and len(data) <= MAX_PART_BYTES, 'archive')
                if name.endswith(('.xml', '.rels')):
                    # The synthetic producer contract is UTF-8 only. Reject
                    # UTF-16/32 (including BOM-less NUL-interleaved declarations)
                    # before checking DTDs, so byte encoding cannot hide entities.
                    xml = data.decode('utf-8-sig')
                    require('\x00' not in xml and '<!DOCTYPE' not in xml.upper()
                            and '<!ENTITY' not in xml.upper(), 'archive')
                    declaration = re.search(r'''<\?xml[^?]*\bencoding\s*=\s*["']([^"']+)''', xml, re.I)
                    require(declaration is None or declaration.group(1).lower() == 'utf-8', 'archive')
                parts[name] = data
        return raw, parts
    except EvidenceError:
        raise
    except Exception:
        raise EvidenceError('archive') from None


def compare_rows(actual, expected, codes, ordered=False):
    require(len(actual) == len(expected), 'rows')
    require(all(len(row) == len(codes) for row in actual), 'columns')
    actual_dicts = [dict(zip(codes, map(scalar, row))) for row in actual]
    require(len({identity(row) for row in actual_dicts}) == len(actual_dicts), 'rows')
    by_identity = {identity(row): row for row in actual_dicts}
    require(set(by_identity) == {identity(row) for row in expected}, 'rows')
    if ordered:
        require([identity(row) for row in actual_dicts] == [identity(row) for row in expected], 'rows')
    for row in expected:
        require(all(by_identity[identity(row)][key] == value for key, value in row.items()), 'content')


def compare_fields(table, expected):
    require(bool(table) and table[0] == FIELD_CODES, 'fields')
    require(len(table) == len(expected) + 1 and all(len(row) == len(FIELD_CODES) for row in table), 'fields')
    actual = [tuple(map(scalar, row)) for row in table[1:]]
    wanted = [tuple(field[key] for key in FIELD_CODES) for field in expected]
    require(len(set(actual)) == len(actual) and set(actual) == set(wanted), 'fields')


def csv_table(data, maximum):
    try:
        csv.field_size_limit(MAX_CELL_CHARS)
        reader = csv.reader(io.StringIO(data.decode('utf-8-sig'), newline=''), strict=True)
        result = []
        for row in reader:
            require(len(result) < maximum and len(row) <= MAX_COLUMNS, 'structure')
            result.append(row)
        return result
    except EvidenceError:
        raise
    except Exception:
        raise EvidenceError('structure') from None


def xlsx_content(raw, rows, fields, labels):
    from openpyxl import load_workbook
    book = load_workbook(io.BytesIO(raw), read_only=True, data_only=False, keep_links=False)
    try:
        require(book.sheetnames == ['答卷', '字段字典', '附件清单', '扩展表作答'], 'structure')

        def table(sheet, maximum):
            # Ignore worksheet-declared dimensions (they can hide data or force
            # huge allocations); bound actual XML iteration and each row instead.
            sheet.reset_dimensions()
            result = []
            for row in sheet.iter_rows():
                require(len(result) < maximum and len(row) <= MAX_COLUMNS, 'structure')
                require(all(cell.data_type != 'f' for cell in row), 'content')
                result.append([scalar(cell.value) for cell in row])
            return result

        responses = table(book['答卷'], MAX_ROWS + 2)
        codes = FIXED_CODES + list(labels)
        require(len(responses) >= 2 and responses[0] == codes
                and responses[1] == FIXED_LABELS + list(labels.values()), 'columns')
        compare_rows(responses[2:], rows, codes)
        compare_fields(table(book['字段字典'], MAX_FIELDS + 1), fields)
    finally:
        book.close()


def sav_content(parts, rows, fields, labels):
    import pyreadstat
    require(set(parts) == {'responses.sav', 'variables.csv', 'fields.csv',
                           'attachments.csv', 'extensions.csv'}, 'structure')
    codes = FIXED_CODES + list(labels)
    all_labels = FIXED_LABELS + list(labels.values())
    mapping = csv_table(parts['variables.csv'], MAX_COLUMNS + 1)
    require(len(mapping) == len(codes) + 1 and mapping[0] == VARIABLE_CODES
            and all(len(row) == len(VARIABLE_CODES) for row in mapping), 'mapping')
    # Simple-code fixture names must equal expected codes. A swapped sidecar
    # cannot remap data into looking correct or become the answer oracle.
    require([row[0] for row in mapping[1:]] == codes
            and [row[1] for row in mapping[1:]] == codes
            and [row[2] for row in mapping[1:]] == all_labels, 'mapping')
    compare_fields(csv_table(parts['fields.csv'], MAX_FIELDS + 1), fields)
    # Only this fixed filename is materialized, in a mode-0700 disposable folder.
    # Never use extract/extractall or a member-supplied destination path.
    with tempfile.TemporaryDirectory(prefix='export-evidence-sav-') as directory:
        dataset = Path(directory) / 'responses.sav'
        with dataset.open('xb') as stream:
            stream.write(parts['responses.sav'])
        dataset.chmod(0o600)
        _, meta = pyreadstat.read_sav(str(dataset), metadataonly=True,
                                      disable_datetime_conversion=True)
        require(meta.number_rows == len(rows) and meta.column_names == codes, 'rows')
        require(meta.column_labels == all_labels, 'mapping')
        frame, meta = pyreadstat.read_sav(str(dataset),
                                        apply_value_formats=False, disable_datetime_conversion=True,
                                        row_limit=MAX_ROWS + 1)
        data = frame.to_dict(orient='list')
        require(list(data) == codes and all(len(data[code]) == len(rows) for code in codes), 'rows')
        compare_rows([[data[code][index] for code in codes] for index in range(len(rows))], rows, codes)


def docx_content(raw, rows, labels):
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    doc = Document(io.BytesIO(raw))
    blocks = []
    for block in doc.iter_inner_content():
        if isinstance(block, Paragraph) and not block.text.strip():
            continue  # Empty paragraphs and page breaks emitted by the default writer.
        require(len(blocks) < MAX_ROWS * 5, 'structure')
        blocks.append(block)
    require(len(blocks) == len(rows) * 5, 'structure')

    def heading(block, text, style):
        require(isinstance(block, Paragraph) and block.text == text and block.style.style_id == style,
                'structure')

    def table_values(block, expected_labels):
        require(isinstance(block, Table) and len(block.rows) == len(expected_labels), 'structure')
        values = []
        for row, label in zip(block.rows, expected_labels):
            require(len(row.cells) == 2 and all(not cell.tables for cell in row.cells), 'structure')
            require(row.cells[0].text == label, 'columns')
            values.append(scalar(row.cells[1].text))
        return values

    actual = []
    for index, expected in enumerate(rows):
        title, info_title, info, answer_title, answers = blocks[index * 5:index * 5 + 5]
        heading(title, '答卷 ' + expected['responseid'], 'Heading1')
        heading(info_title, '答卷信息', 'Heading2')
        heading(answer_title, '作答', 'Heading2')
        actual.append(table_values(info, FIXED_LABELS) + table_values(answers, list(labels.values())))
    compare_rows(actual, rows, FIXED_CODES + list(labels), ordered=True)


def verify(fmt, path, expected):
    """Raise only sanitized EvidenceError categories; success exposes counts only."""
    try:
        require(fmt in ('xlsx', 'sav', 'docx'), 'arguments')
        rows, fields, labels = normalize_expected(expected)
        raw, parts = bounded_archive(path)
        with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            warnings.simplefilter('ignore')
            if fmt == 'xlsx':
                xlsx_content(raw, rows, fields, labels)
            elif fmt == 'sav':
                sav_content(parts, rows, fields, labels)
            else:
                docx_content(raw, rows, labels)
        return dict(ok=True, format=fmt, rows=len(rows), fields=0 if fmt == 'docx' else len(fields))
    except EvidenceError:
        raise
    except ImportError:
        raise EvidenceError('dependency') from None
    except Exception:
        raise EvidenceError('reader') from None


@contextlib.contextmanager
def silence_reader_descriptors():
    """Also suppress native ReadStat diagnostics, not just Python logging."""
    saved = [os.dup(1), os.dup(2)]
    try:
        with open(os.devnull, 'w') as sink:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            yield
            sys.stdout.flush()
            sys.stderr.flush()
    finally:
        for descriptor, backup in zip((1, 2), saved):
            os.dup2(backup, descriptor)
            os.close(backup)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        require(len(argv) == 3 and argv[0] in ('xlsx', 'sav', 'docx'), 'arguments')
        try:
            with open(argv[2], 'rb') as source:
                raw = source.read(MAX_EXPECTED_BYTES + 1)
            require(len(raw) <= MAX_EXPECTED_BYTES, 'expectation')
            expected = json.loads(raw.decode('utf-8'))
        except Exception:
            raise EvidenceError('expectation') from None
        with silence_reader_descriptors():
            result = verify(argv[0], argv[1], expected)
    except EvidenceError as error:
        result = dict(ok=False, category=str(error))
    except Exception:
        result = dict(ok=False, category='reader')
    print(json.dumps(result, separators=(',', ':')))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
