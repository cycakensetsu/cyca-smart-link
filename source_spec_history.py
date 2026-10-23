"""Compare immutable readings of the same original; never choose a correction."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import json
import re

from estimate_review import digest

_SHA = re.compile(r'[a-f0-9]{64}')
_MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
_MAX_HISTORY_BYTES = 32 * 1024 * 1024


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _sources(snapshot):
    # Only capture_extraction's unedited raw schema is eligible. Approval and
    # conversation snapshots, edited rows, and earlier incompatible schemas
    # provide no evidence here.
    if not isinstance(snapshot, dict) or set(snapshot) != {'rows', 'summary', 'sources', 'records'}:
        return None
    if (not isinstance(snapshot['summary'], dict)
            or not all(isinstance(snapshot[key], list) for key in ('rows', 'sources', 'records'))):
        return None
    sources = {}
    for source in snapshot['sources']:
        if not isinstance(source, dict):
            return None
        sid, pages = source.get('id'), source.get('pages')
        if (not isinstance(sid, str) or not _SHA.fullmatch(sid) or sid in sources
                # Older capture_extraction snapshots omitted this flag; the
                # raw schema and unedited row checks still apply to them.
                or type(pages) is not int or pages < 1 or source.get('confirmed', False) is not False):
            return None
        sources[sid] = pages
    row_ids = set()
    for row in snapshot['rows']:
        if (not isinstance(row, dict) or row.get('採用') is not True
                or any(row.get(key) not in ('', None) for key in ('訂正理由', '除外理由', '顧客備考'))):
            return None
        row_id, sid = row.get('行ID'), row.get('原本ID')
        if (not isinstance(row_id, str) or not row_id or row_id in row_ids
                or not isinstance(sid, str) or sid not in sources):
            return None
        row_ids.add(row_id)
    return sources


def _row_key(row, sources):
    sid = row.get('原本ID')
    page = _number(row.get('ページ'))
    if (not isinstance(sid, str) or not _SHA.fullmatch(sid) or sid not in sources
            or page is None or page != page.to_integral_value() or not 1 <= page <= sources[sid]):
        return None
    name, unit, vendor = row.get('品名'), row.get('単位'), row.get('見積元')
    if not all(isinstance(value, str) and value for value in (name, unit, vendor)):
        return None
    values = tuple(_number(row.get(key)) for key in ('数量', '原価単価', '原価金額'))
    if (any(value is None for value in values) or values[0] <= 0
            or values[1] < 0 or values[2] < 0 or row.get('集計のみ')):
        return None
    return (sid, int(page), name, unit, vendor, *values)


def compare_spec_history(current, snapshots):
    """Return only uniquely corresponding specification differences.

    snapshots contains (content-addressed snapshot ID, raw snapshot) pairs.
    Exact names and units are intentional: changed labels/quantities and a
    first reading have no comparison coverage. No majority vote is taken.
    """
    result = {'method': 'same_original_reading_history', 'first_read_covered': False,
              'changed_names_or_quantities_covered': False, 'changed_vendor_covered': False,
              'nonempty_specs_only': True,
              'automatic_changes': False, 'current_schema_eligible': False,
              'checked_snapshots': 0, 'skipped_snapshots': 0, 'findings': []}
    sources = _sources(current)
    if sources is None:
        return result
    try:
        current_id = digest(current)
    except (ValueError, TypeError):
        return result
    result['current_schema_eligible'] = True
    current_index = defaultdict(list)
    for row in current['rows']:
        key = _row_key(row, sources)
        if key is not None:
            current_index[key].append(row)
    found = {}
    for snapshot_id, snapshot in snapshots:
        try:
            valid_id = isinstance(snapshot_id, str) and _SHA.fullmatch(snapshot_id) and digest(snapshot) == snapshot_id
        except (ValueError, TypeError):
            valid_id = False
        old_sources = _sources(snapshot) if valid_id else None
        if old_sources is None:
            result['skipped_snapshots'] += 1
            continue
        if snapshot_id == current_id or not set(sources).intersection(old_sources):
            continue
        result['checked_snapshots'] += 1
        old_index = defaultdict(list)
        for row in snapshot['rows']:
            key = _row_key(row, old_sources)
            if key is not None:
                old_index[key].append(row)
        for key, current_rows in current_index.items():
            previous = old_index.get(key, [])
            if len(current_rows) != 1 or len(previous) != 1:
                continue
            row, old = current_rows[0], previous[0]
            spec, old_spec = row.get('仕様'), old.get('仕様')
            if (not isinstance(spec, str) or not spec.strip() or not isinstance(old_spec, str)
                    or not old_spec.strip() or spec == old_spec or not row.get('行ID')):
                continue
            finding = found.setdefault(row['行ID'], {
                '原本ID': key[0], 'ページ': key[1], '行ID': row['行ID'], '品名': row['品名'],
                '今回の仕様': spec, '過去の表記': []})
            candidate = next((item for item in finding['過去の表記'] if item['仕様'] == old_spec), None)
            if candidate is None:
                candidate = {'仕様': old_spec, '根拠': []}
                finding['過去の表記'].append(candidate)
            proof = {'snapshot_id': snapshot_id, '行ID': old.get('行ID')}
            if proof not in candidate['根拠']:
                candidate['根拠'].append(proof)
    result['findings'] = list(found.values())
    return result


def review_vault_spec_history(store, current):
    """Read only this authenticated user's raw snapshots, once per import."""
    inspected_bytes = 0
    limited = False
    def snapshots():
        nonlocal inspected_bytes, limited
        for name, size, read in store.entries():
            match = re.fullmatch(r'extractions/([a-f0-9]{64})\.json', name)
            if match is None:
                continue
            if size > _MAX_SNAPSHOT_BYTES or inspected_bytes + size > _MAX_HISTORY_BYTES:
                limited = True
                continue
            inspected_bytes += size
            try:
                raw = json.loads(read())
            except (ValueError, TypeError, OSError):
                continue
            yield match[1], raw
    try:
        result = compare_spec_history(current, snapshots())
        result['history_unavailable'] = False
    except (ValueError, OSError):
        # Comparing past readings is optional. An unreadable history must not
        # erase a successful current extraction or imply an agreement.
        result = compare_spec_history(current, [])
        result['history_unavailable'] = True
    result['history_limits_applied'] = limited
    return result


def ensure_spec_history(state, store, current=None):
    """Initialize once per extraction/user, including an already-open session."""
    extraction_id = state.get('_review_extraction_id')
    if not isinstance(extraction_id, str) or not _SHA.fullmatch(extraction_id):
        return None
    scope = (str(store.root), extraction_id, 1)
    if state.get('_review_spec_history_scope') == scope:
        return state.get('_review_spec_history')
    try:
        if current is None:
            current = json.loads(store.get_entry(f'extractions/{extraction_id}.json'))
        if digest(current) != extraction_id:
            raise ValueError('snapshot mismatch')
    except (ValueError, TypeError, OSError):
        current = None
    result = review_vault_spec_history(store, current)
    if current is None:
        result['history_unavailable'] = True
    result['extraction_id'] = extraction_id
    state['_review_spec_history'] = result
    state['_review_spec_history_scope'] = scope
    return result
