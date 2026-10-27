"""Resume an immutable, owner-private read without another model request.

This restores evidence for a new review, never an approval or edited estimate.
"""
import copy
import json
from pathlib import Path
import re
import tempfile

from estimate_review import digest
from source_spec_history import _sources, _MAX_SNAPSHOT_BYTES, _MAX_HISTORY_BYTES


def _identity(sources):
    if not isinstance(sources, list) or not sources:
        return None
    result = []
    for source in sources:
        if not isinstance(source, dict):
            return None
        sid, name, pages = (source.get(k) for k in ('id', 'name', 'pages'))
        if (not isinstance(sid, str) or not re.fullmatch('[a-f0-9]{64}', sid)
                or not isinstance(name, str) or not name or type(pages) is not int or pages < 1):
            return None
        result.append((sid, name, pages))
    if len({s[0] for s in result}) != len(result) or len({s[1] for s in result}) != len(result):
        return None
    return frozenset(result)


def _eligible(raw, current_sources=None):
    sources = _sources(raw)
    if not sources or not raw['rows'] or not raw['records']:
        return False
    identity = _identity(raw['sources'])
    if identity is None or (current_sources and identity != _identity(current_sources)):
        return False
    if any(s.get('read_errors') or s.get('prepare_error') for s in raw['sources']):
        return False
    by_name = {s['name']: s for s in raw['sources']}
    represented = set()
    for record in raw['records']:
        if not isinstance(record, dict):
            return False
        name, page = record.get('__source_name'), record.get('__page_number')
        if not isinstance(name, str) or name not in by_name:
            return False
        try:
            number = float(page)
            valid = not isinstance(page, bool) and number.is_integer() and 1 <= number <= by_name[name]['pages']
        except (ValueError, TypeError, OverflowError):
            valid = False
        if not valid:
            return False
        represented.add(name)
    return represented == set(by_name)


def available_reads(store, current_sources=None):
    """Bounded, digest-verified candidates; source equality includes filename."""
    results, inspected = [], 0
    for name, size, read in store.entries():
        match = re.fullmatch(r'extractions/([a-f0-9]{64})\.json', name)
        if not match or size > _MAX_SNAPSHOT_BYTES or inspected + size > _MAX_HISTORY_BYTES:
            continue
        inspected += size
        try:
            raw = json.loads(read())
            if digest(raw) != match[1] or not _eligible(raw, current_sources):
                continue
        except (ValueError, TypeError, OSError):
            continue
        results.append({'id': match[1], 'files': len(raw['sources']), 'rows': len(raw['rows']),
                        'names': [s['name'] for s in raw['sources']]})
    return results


def resume_read(store, extraction_id, current_sources=None):
    """Reprocess source-backed ledgers/geometry; retain every original snapshot."""
    if not isinstance(extraction_id, str) or not re.fullmatch('[a-f0-9]{64}', extraction_id):
        raise ValueError('保存した読み取り結果を確認できません。')
    data = store.get_entry(f'extractions/{extraction_id}.json')
    if data is None or len(data) > _MAX_SNAPSHOT_BYTES:
        raise ValueError('保存した読み取り結果を確認できません。')
    raw = json.loads(data)
    if digest(raw) != extraction_id or not _eligible(raw, current_sources):
        raise ValueError('原本の対応または読み取り結果を確認できません。')
    from pdf_worker import page_count
    # Verify every original before producing replacement working data.
    originals = {}
    for source in raw['sources']:
        content = store.read(source['id'])
        if Path(source['name']).suffix.lower() == '.pdf' and page_count(content) != source['pages']:
            raise ValueError('保存した原本のページ数が一致しません。')
        originals[source['id']] = content
    raw = copy.deepcopy(raw)
    summary_sources = raw['summary'].get('summary_sources', [])
    if not isinstance(summary_sources, list) or any(not isinstance(s, dict) for s in summary_sources):
        raise ValueError('保存した合計の形式を確認できません。')
    # capture_extraction stores normalized summary keys. Restore their exact
    # source/page aliases only for the same production ledger reconciliation.
    summaries = [{**s, '__source_name': s.get('元ファイル'), '__page_number': s.get('ページ'),
                  '__page_role': s.get('ページ種別')} for s in summary_sources]
    from source_sections import reconcile_sections
    from source_table import attach_native_dimensions
    records = raw['records']
    for source in raw['sources']:
        records = reconcile_sections(records, summaries, source['name'])
        if Path(source['name']).suffix.lower() == '.pdf':
            scoped = [r for r in records if r.get('__source_name') == source['name']]
            with tempfile.TemporaryDirectory(prefix='cyca-saved-read-') as directory:
                path = Path(directory) / 'source.pdf'
                path.write_bytes(originals[source['id']])
                try:
                    enriched = attach_native_dimensions(str(path), scoped)
                except (ValueError, RuntimeError, AttributeError):
                    enriched = scoped
            iterator = iter(enriched)
            records = [next(iterator) if r.get('__source_name') == source['name'] else r for r in records]
    raw['records'] = records
    return raw


def apply_saved_read(st, extraction_id):
    """Prepare everything first; the shared capture resets all review approvals."""
    from review_ui import vault, capture_extraction
    from estimate_pipeline import build_intermediate_dataframe
    raw = resume_read(vault(st), extraction_id, st.session_state.get('_review_sources'))
    frame, _ = build_intermediate_dataframe(raw['records'])
    from estimate_review import review_rows
    review_rows(frame, raw['sources'])
    st.session_state['_review_sources'] = raw['sources']
    capture_extraction(st, frame, raw['summary'], raw['records'])
    st.session_state['_review_resumed_from'] = extraction_id


def render_saved_reads(st):
    from review_ui import vault
    try:
        reads = available_reads(vault(st), st.session_state.get('_review_sources'))
    except (ValueError, OSError):
        st.caption('保存した読み取り結果を確認できません。原本の保管先を確認してください。')
        return
    if not reads:
        return
    st.caption('読み取り済みの結果から、内容確認を再開できます。追加のAI読み取りは行いません。現在の訂正・宛名・金額設定は引き継がず、原本確認と作成前の確認はやり直します。')
    labels = {item['id']: f"{index + 1}. {item['files']}資料・{item['rows']}品目：" + '、'.join(item['names'])
              for index, item in enumerate(reads)}
    chosen = st.selectbox('保存した読み取り結果', list(labels), format_func=labels.get, key='saved_read_choice')
    def restore():
        # A callback runs before review widgets are created on the next rerun.
        # Resetting their values later in the script would be forbidden by
        # Streamlit and could leave the current review partly replaced.
        try:
            apply_saved_read(st, chosen)
        except (ValueError, TypeError, KeyError, OSError):
            st.session_state['_saved_read_error'] = '再開できませんでした。原本と保存データを確認してください。'
        else:
            st.session_state['_saved_read_notice'] = '保存した読み取り結果を戻しました。原本と内容を確認してください。'
    error = st.session_state.pop('_saved_read_error', '')
    if error:
        st.error(error)
    st.button('この読み取り結果で内容確認を再開', key='saved_read_resume', on_click=restore)
