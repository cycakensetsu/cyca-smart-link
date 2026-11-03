"""Compare independent printed row quotations; never select a correction.

Acquisition completion is distinct from agreement. A complete independent
reading with differences may be cached, while those differences remain human
review findings. Agreement of two model readings is not proof of accuracy.
"""
from collections import defaultdict
import hashlib
import json
import re


SCHEMA_VERSION = 2
ROW_FIELDS = ('ページ', '見積区分', '品名', '仕様', '備考', '原文')
_SHA = re.compile(r'[a-f0-9]{64}')


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def parse_row_evidence(payload, name, total_pages, checked_pages):
    """Validate source-bound descriptive evidence, retaining valid quotations."""
    if not isinstance(payload, dict) or payload.get('元ファイル') != name:
        return [], ['invalid_row_source']
    if type(payload.get('schema_version')) is not int or payload['schema_version'] != SCHEMA_VERSION:
        return [], ['missing_row_evidence_version']
    evidence = payload.get('行照合')
    if not isinstance(evidence, list) or len(evidence) > 2000:
        return [], ['invalid_row_evidence']
    accepted, reasons = [], []
    for row in evidence:
        if not isinstance(row, dict) or set(row) != set(ROW_FIELDS):
            reasons.append('invalid_row_evidence')
            continue
        page = row['ページ']
        if type(page) is not int or not 1 <= page <= total_pages or page not in checked_pages:
            reasons.append('invalid_row_page')
            continue
        if (any(not isinstance(row[key], str) or len(row[key]) > 16384 for key in ROW_FIELDS[1:])
                or not row['品名'].strip() or not row['原文'].strip()):
            reasons.append('invalid_row_evidence')
            continue
        quotation = _text(row['原文'])
        if any(_text(row[key]) not in quotation for key in ('品名', '仕様', '備考') if row[key].strip()):
            reasons.append('unquoted_row_field')
            continue
        # Do not deduplicate: repeated quotations may identify different
        # printed rows and must remain ambiguous rather than appear unique.
        accepted.append(dict(row))
    return accepted, list(dict.fromkeys(reasons))


def _page(value, maximum):
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
        return int(numeric) if numeric.is_integer() and 1 <= numeric <= maximum else None
    except (TypeError, ValueError, OverflowError):
        return None


def _text(value):
    # Ignore line wrapping only. Never remove punctuation, repeated letters,
    # product-name characters, or equate a missing field with nonempty text.
    return re.sub(r'\s+', '', value) if isinstance(value, str) else None


def compare_row_evidence(rows, source):
    """Compare normalized review rows against this exact original's evidence.

    source supplies name/id/pages and amount_reading.annotation_reviews.
    Only unique exact source/page/name pairs are compared. Same-name rows on
    one page remain ambiguous even if their sections or financial values differ.
    Inputs, original prices, customer remarks and quotations are never edited.
    """
    name, sid, pages = source.get('name'), source.get('id'), source.get('pages')
    result = {'schema_version': SCHEMA_VERSION, 'status': 'missing', 'findings': []}
    selected = [row for row in rows if row.get('原本ID') == sid]

    def finding(reason, row=None, expected=None, fields=None, evidence=None, page=None, label=''):
        item = {'row_id': row.get('行ID') if row else None,
                'page': _page(row.get('ページ'), pages) if row and type(pages) is int else page,
                'name': str(row.get('品名') or '') if row else label,
                'fields': list(fields or ['仕様', '備考']), 'reason': reason,
                'expected': expected or {},
                'actual': {key: row.get(key) for key in ('仕様', '備考')} if row else {},
                'evidence_digest': _digest([sid, name, pages, reason, evidence])}
        result['findings'].append(item)

    if (not isinstance(name, str) or not isinstance(sid, str) or not _SHA.fullmatch(sid)
            or type(pages) is not int or pages < 1):
        finding('invalid_source')
        return result
    reading = source.get('amount_reading', {})
    reviews = reading.get('annotation_reviews', []) if isinstance(reading, dict) else []
    if not isinstance(reviews, list):
        finding('invalid_row_evidence')
        return result
    linked = [review for review in reviews if isinstance(review, dict)
              and review.get('source_name') == name and review.get('sha256') == sid]
    if not linked:
        finding('missing_row_evidence')
        return result
    valid, incomplete = [], False
    for review in linked:
        if type(review.get('schema_version')) is not int or review['schema_version'] != SCHEMA_VERSION:
            incomplete = True
            continue
        checked = review.get('checked_pages')
        checked_ok = (isinstance(checked, list) and all(type(p) is int for p in checked)
                      and sorted(checked) == list(range(1, pages + 1)))
        data = {'元ファイル': name, 'schema_version': SCHEMA_VERSION,
                '行照合': review.get('row_evidence')}
        evidence, reasons = parse_row_evidence(data, name, pages, checked if checked_ok else [])
        incomplete |= bool(reasons) or not checked_ok or review.get('status') != 'completed'
        valid.extend(evidence)
    if not valid and incomplete:
        finding('incomplete_row_evidence')
        return result
    result['status'] = 'needs_review'
    if incomplete:
        finding('incomplete_row_evidence')
    current, independent = defaultdict(list), defaultdict(list)
    for row in selected:
        page = _page(row.get('ページ'), pages)
        label = row.get('品名')
        if page is None or not isinstance(label, str) or not label.strip():
            finding('invalid_row_location', row)
            continue
        current[(page, label)].append(row)
    for evidence in valid:
        independent[(evidence['ページ'], evidence['品名'])].append(evidence)
    for key, candidates in current.items():
        matches = independent.get(key, [])
        if len(candidates) != 1 or len(matches) > 1:
            for row in candidates:
                finding('ambiguous_row', row, evidence=matches)
            continue
        row = candidates[0]
        if not matches:
            finding('missing_row', row)
            continue
        evidence = matches[0]
        fields = [field for field in ('仕様', '備考') if _text(row.get(field)) != _text(evidence[field])]
        if fields:
            finding('field_difference', row, {field: evidence[field] for field in fields},
                    fields, evidence)
    for key, matches in independent.items():
        if key not in current:
            finding('unmatched_evidence', expected={field: matches[0][field] for field in ('仕様', '備考')},
                    evidence=matches, page=key[0], label=key[1])
    if not selected:
        finding('missing_current_rows')
    if not result['findings']:
        result['status'] = 'completed'
    return result
