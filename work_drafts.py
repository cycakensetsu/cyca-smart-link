"""Owner-private, immutable checkpoints; never restore authentication or approval.

Each browser work session has its own ID. Resuming forks a new work ID, so two
people reopening the same checkpoint cannot overwrite each other's changes.
"""
import copy
from datetime import datetime, timezone
import json
import math
import os
import re
import tempfile
import uuid
from zoneinfo import ZoneInfo

from estimate_review import canonical, digest

MAX_DRAFT_BYTES = 8 * 1024 * 1024
_HEX = r'[a-f0-9]{64}'
_NAMES = {'review_customer', 'review_project', 'review_date', 'review_discount',
          'review_discount_reason', 'review_customer_tax', 'review_scope',
          'review_target_mode', 'review_page_breaks', 'review_instruction',
          '_review_assistant_rows', '_review_chat', '_review_requested_pages',
          '_review_conversation_record', 'review_pricing_choice', 'review_round_enabled',
          'review_round_step', 'review_round_limit', 'review_round_low_floor', 'review_round_welfare_reason'}
_KEYS = re.compile(rf'(?:review_(?:target|basis|tax|net|gross|tax_amount|tax_exists|'
                   rf'missing_gross|resolve_totals|amount_notes|reference_only|source_page)_{_HEX}'
                   rf'|review_resolution_group_{_HEX}(?:_both)?'
                   rf'|review_row_evidence_{_HEX}'
                   rf'|review_cap_override_{_HEX}'
                   rf'|review_round_(?:override|rate)_{_HEX}'
                   rf'|_review_reference_(?:previous|saved)_{_HEX})')


def allowed_key(key):
    return isinstance(key, str) and (key in _NAMES or bool(_KEYS.fullmatch(key)))


def reset_tracking(state):
    for key in list(state):
        if key.startswith('_work_draft_'):
            del state[key]


def hydrate_inputs(state):
    """Keep committed price/customer fields while their widgets are not shown."""
    if state.get('_work_draft_extraction') != state.get('_review_extraction_id'):
        return
    for key, value in state.get('_work_draft_inputs', {}).items():
        if key.startswith('review_') and allowed_key(key) and key not in state:
            state[key] = copy.deepcopy(value)


def validate_draft(value, owner=None):
    if (not isinstance(value, dict) or set(value) !=
            {'schema', 'kind', 'owner', 'work_id', 'extraction_id', 'saved_at', 'state'}
            or value.get('schema') != 1 or value.get('kind') != 'working_draft'
            or not re.fullmatch(_HEX, str(value.get('owner', '')))
            or (owner is not None and value['owner'] != owner)
            or not re.fullmatch(r'[a-f0-9]{32}', str(value.get('work_id', '')))
            or not re.fullmatch(_HEX, str(value.get('extraction_id', '')))):
        raise ValueError('途中の保存データを確認できません。')
    when = datetime.fromisoformat(value['saved_at'])
    if when.tzinfo is None:
        raise ValueError('保存日時を確認できません。')
    state = value['state']
    if not isinstance(state, dict) or not all(allowed_key(k) for k in state):
        raise ValueError('復元できない入力が含まれています。')
    rows = state.get('_review_assistant_rows')
    if not isinstance(rows, list) or not rows or any(not isinstance(r, dict) for r in rows):
        raise ValueError('保存した品目を確認できません。')
    history = state.get('_review_chat', [])
    if (not isinstance(history, list) or len(history) > 20 or any(
            not isinstance(m, dict) or set(m) != {'role', 'content'}
            or m['role'] not in ('user', 'assistant') or not isinstance(m['content'], str)
            for m in history)):
        raise ValueError('保存した相談履歴を確認できません。')
    for name, options in [('review_scope', ('全体', '業者別', '工種別')),
                          ('review_target_mode', ('上乗せ額', '掛け率', '粗利益率')),
                          ('review_round_step', (10, 50, 100)),
                          ('review_round_low_floor', (0, 100, 150, 200))]:
        if name in state and state[name] not in options:
            raise ValueError('保存した金額設定を確認できません。')
    if 'review_round_limit' in state and (type(state['review_round_limit']) is not int
            or not 0 <= state['review_round_limit'] <= 1000000):
        raise ValueError('保存した値引き上限を確認できません。')
    for name, item in state.items():
        if name == 'review_pricing_choice':
            from pricing_options import valid_choice
            if not valid_choice(item):
                raise ValueError('保存した値引き案の選択を確認できません。')
        text = name in {'review_customer', 'review_project', 'review_date',
                        'review_discount_reason', 'review_instruction', '_review_conversation_record', 'review_round_welfare_reason'}
        text = text or bool(re.fullmatch(rf'review_(?:basis|tax|amount_notes)_{_HEX}', name))
        text = text or bool(re.fullmatch(rf'review_resolution_group_{_HEX}', name))
        text = text or bool(re.fullmatch(rf'review_row_evidence_{_HEX}', name))
        numeric = name in {'review_discount', 'review_customer_tax', 'review_round_step', 'review_round_limit', 'review_round_low_floor'} or bool(re.fullmatch(rf'review_round_rate_{_HEX}', name)) or bool(
            re.fullmatch(rf'review_(?:target|net|gross|tax_amount|missing_gross|source_page)_{_HEX}', name))
        boolean = name == 'review_round_enabled' or bool(re.fullmatch(rf'review_round_override_{_HEX}', name)) or bool(re.fullmatch(rf'review_(?:tax_exists|resolve_totals|reference_only)_{_HEX}', name)
                       or re.fullmatch(rf'review_cap_override_{_HEX}', name)
                       or re.fullmatch(rf'review_resolution_group_{_HEX}_both', name)
                       or re.fullmatch(rf'_review_reference_previous_{_HEX}', name))
        if ((text and not isinstance(item, str))
                or (numeric and item is not None and (type(item) not in (int, float) or not math.isfinite(item)))
                or (boolean and type(item) is not bool)):
            raise ValueError('保存した入力形式を確認できません。')
    breaks = state.get('review_page_breaks', [])
    requested = state.get('_review_requested_pages', {})
    if (not isinstance(breaks, list) or any(not isinstance(k, str) for k in breaks)
            or not isinstance(requested, dict) or any(not isinstance(k, str) or type(v) is not int
                or not 2 <= v <= 10 for k, v in requested.items())):
        raise ValueError('保存したページの区切りを確認できません。')
    if len(canonical(value).encode()) > MAX_DRAFT_BYTES:
        raise ValueError('途中の保存データが大きすぎます。')
    return value


def _original(store, extraction_id):
    data = store.get_entry(f'extractions/{extraction_id}.json')
    if data is None or len(data) > MAX_DRAFT_BYTES:
        raise ValueError('保存した原本の読み取り結果が見つかりません。')
    raw = json.loads(data)
    from saved_read_resume import _identity
    if (digest(raw) != extraction_id or not isinstance(raw, dict)
            or _identity(raw.get('sources')) is None
            or not isinstance(raw.get('summary'), dict)
            or not isinstance(raw.get('rows'), list) or not raw['rows']):
        raise ValueError('原本と保存した品目の対応を確認できません。')
    return raw


def _validate_rows(raw, rows):
    originals = {r['行ID']: r for r in raw['rows']}
    if len(originals) != len(raw['rows']) or len(rows) != len(originals):
        raise ValueError('保存した品目数が原本と一致しません。')
    seen = set()
    for row in rows:
        old = originals.get(row.get('行ID'))
        if (old is None or row['行ID'] in seen or any(row.get(k) != old.get(k)
                for k in ('原本ID', '元ファイル'))):
            raise ValueError('保存した品目と原本の対応が一致しません。')
        seen.add(row['行ID'])


def update_head(store, key, value):
    """Small atomic index; older immutable revisions never need scanning."""
    import fcntl
    validate_draft(value, store.root.name)
    if digest(value) != key:
        raise ValueError('途中データの記録が一致しません。')
    folder = store.root / '.draft_heads'
    folder.mkdir(mode=0o700, exist_ok=True)
    if folder.is_symlink():
        raise ValueError('途中データの保存先が不正です。')
    target = folder / (value['work_id'] + '.head')
    lock = folder / '.lock'
    if target.is_symlink() or lock.is_symlink():
        raise ValueError('途中データの保存先が不正です。')
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'rb') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        if target.is_file():
            previous = target.read_text()
            if not re.fullmatch(_HEX, previous):
                raise ValueError('途中データの一覧が不正です。')
            old = validate_draft(json.loads(store.get_entry(f'drafts/{previous}.json')), store.root.name)
            if old['work_id'] != value['work_id'] or digest(old) != previous:
                raise ValueError('途中データの一覧が一致しません。')
            if datetime.fromisoformat(old['saved_at']) > datetime.fromisoformat(value['saved_at']):
                return
        out, temporary = tempfile.mkstemp(dir=folder)
        try:
            with os.fdopen(out, 'w') as stream:
                stream.write(key)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def _committed_inputs(state):
    selected = copy.deepcopy(state.get('_work_draft_inputs', {}))
    selected.update({k: copy.deepcopy(state[k]) for k in state if allowed_key(k)})
    selected['_review_assistant_rows'] = copy.deepcopy(
        state.get('_review_assistant_rows', state['_review_original_rows']))
    return selected


def save_draft(state, store, now=None):
    """Save only changed committed inputs. No credentials, uploads, AI or PDFs."""
    extraction = state.get('_review_extraction_id')
    if not extraction or not state.get('_review_original_rows'):
        return None
    if state.get('_work_draft_extraction') != extraction:
        reset_tracking(state)
        state['_work_draft_extraction'] = extraction
        state['_work_draft_id'] = uuid.uuid4().hex
    selected = _committed_inputs(state)
    fingerprint = digest([extraction, selected])
    if fingerprint == state.get('_work_draft_fingerprint'):
        return state.get('_work_draft_record')
    value = {'schema': 1, 'kind': 'working_draft', 'owner': store.root.name,
             'work_id': state['_work_draft_id'], 'extraction_id': extraction,
             'saved_at': (now or datetime.now(timezone.utc)).isoformat(), 'state': selected}
    validate_draft(value, store.root.name)
    _validate_rows(_original(store, extraction), selected['_review_assistant_rows'])
    key = digest(value)
    store._put(f'drafts/{key}.json', canonical(value).encode())
    update_head(store, key, value)
    state['_work_draft_inputs'] = selected
    state['_work_draft_fingerprint'] = fingerprint
    state['_work_draft_record'] = key
    state['_work_draft_saved_at'] = value['saved_at']
    state.pop('_vault_archive', None)
    return key


def available_drafts(store):
    latest = {}
    # Immutable revisions stay in the private vault. Only each work's latest
    # revision is offered and packed into new PC backups.
    heads = store.root / '.draft_heads'
    if heads.is_symlink():
        raise ValueError('途中データの保存先が不正です。')
    if hasattr(store, 'draft_entries'):
        # A host-local head index cannot know another host's new revisions.
        candidates = store.draft_entries()
    elif heads.is_dir():
        candidates = []
        for path in heads.glob('*.head'):
            if path.is_symlink() or not re.fullmatch(r'[a-f0-9]{32}\.head', path.name) or path.stat().st_size != 64:
                raise ValueError('途中データの一覧が不正です。')
            key = path.read_text()
            if not re.fullmatch(_HEX, key):
                raise ValueError('途中データの一覧が不正です。')
            content = store.get_entry(f'drafts/{key}.json')
            if content is None:
                raise ValueError('途中データが見つかりません。')
            candidates.append((f'drafts/{key}.json', len(content), lambda data=content: data))
    else:
        # Compatibility for portable restores made before a head was rebuilt.
        candidates = store.entries()
    for name, size, read in candidates:
        match = re.fullmatch(rf'drafts/({_HEX})\.json', name)
        if not match or size > MAX_DRAFT_BYTES:
            continue
        try:
            value = validate_draft(json.loads(read()), store.root.name)
            if digest(value) != match[1]:
                continue
            when = datetime.fromisoformat(value['saved_at'])
            current = latest.get(value['work_id'])
            if current is None or when > current[0]:
                latest[value['work_id']] = (when, match[1], value)
        except (ValueError, TypeError, KeyError):
            continue
    return [{'id': key, **value} for _, key, value in
            sorted(latest.values(), key=lambda item: (item[0], item[1]), reverse=True)]


def restore_draft(state, store, key):
    """Validate every source before mutating state; recheck sources and preview."""
    if not isinstance(key, str) or not re.fullmatch(_HEX, key):
        raise ValueError('途中データの指定が不正です。')
    data = store.get_entry(f'drafts/{key}.json')
    if data is None or len(data) > MAX_DRAFT_BYTES:
        raise ValueError('途中の保存データが見つかりません。')
    value = validate_draft(json.loads(data), store.root.name)
    if digest(value) != key:
        raise ValueError('途中の保存データが変更されています。')
    raw = _original(store, value['extraction_id'])
    _validate_rows(raw, value['state']['_review_assistant_rows'])
    for source in raw['sources']:
        store.read(source['id'])
    # No user/server state is touched until all verification above has passed.
    for name in list(state):
        if name.startswith(('_review_', 'review_', '_work_draft_')):
            del state[name]
    restored = copy.deepcopy(value['state'])
    state.update(restored)
    state['_review_sources'] = copy.deepcopy(raw['sources'])
    for source in state['_review_sources']:
        source.update(confirmed=False, reference_confirmed=False)
        state['review_source_ok_' + source['id']] = False
    state['_review_original_rows'] = copy.deepcopy(raw['rows'])
    state['_review_metadata'] = copy.deepcopy(raw['summary'])
    state['_review_extraction_id'] = value['extraction_id']
    # Retain user-corrected source totals even though confirmations are reset.
    required = ('basis', 'tax', 'net', 'tax_amount', 'gross', 'tax_exists')
    state['_review_amount_defaults'] = {s['id']: True for s in raw['sources'] if
        all('review_' + k + '_' + s['id'] in restored for k in required)}
    state['_review_export_ready'] = False
    state['_work_draft_extraction'] = value['extraction_id']
    state['_work_draft_id'] = uuid.uuid4().hex
    state['_work_draft_inputs'] = restored
    state['_work_draft_saved_at'] = value['saved_at']
    state.pop('_vault_archive', None)


_INTAKE_KEYS = {
    '_detail_df', '_vendor_summaries', '_summary_data', '_categories_summary',
    '_horizontal_result', '_horizontal_signature', '_force_analyze',
    '_effects_source_checks', '_vault_archive', '_saved_read_notice',
}


def _retire_upload(state):
    # Rotate the widget only on a confirmed change. Old uploaded bytes are not
    # reused against a new or restored set of original rows.
    for key in list(state):
        if (key in _INTAKE_KEYS or key.startswith(('_extracted_', 'cat_profit_', '_legacy_', 'estimate_upload_'))):
            del state[key]
    state['_upload_epoch'] = uuid.uuid4().hex
    state['_upload_signature'] = ()
    state['output_customer'] = ''
    state['output_project'] = ''


def _has_review(state):
    return bool(state.get('_review_extraction_id') or state.get('_review_original_rows'))


def _is_checkpointed(state):
    extraction = state.get('_review_extraction_id')
    if not (extraction and state.get('_review_original_rows') and state.get('_work_draft_record')
            and state.get('_work_draft_extraction') == extraction):
        return False
    try:
        return digest([extraction, _committed_inputs(state)]) == state.get('_work_draft_fingerprint')
    except (ValueError, TypeError, KeyError):
        return False


def begin_new_estimate(state):
    """Leave current inputs only after a normal checkpoint. Never write to vault."""
    if _has_review(state) and not _is_checkpointed(state):
        return False
    for key in list(state):
        if key.startswith(('_review_', 'review_', '_work_draft_')):
            del state[key]
    _retire_upload(state)
    state.pop('_work_resume_id', None)
    state.pop('_work_resume_confirm', None)
    state['_work_entry_view'] = 'new'
    state['_work_new_tip'] = True
    return True


def render_recovery(st, *, allow_resume=True):
    """Show the new-estimate entry by default; restoring a draft is explicit."""
    from review_ui import vault
    state = st.session_state
    if state.pop('_work_start_new_requested', False):
        if not begin_new_estimate(state):
            st.warning('今の入力をまだ保存できていないため、見積を切り替えていません。画面下の保存状況をご確認ください。')
        state.pop('_work_confirm_start_new', None)

    pending = state.pop('_work_resume_id', None)
    if pending and allow_resume and not _has_review(state):
        try:
            restore_draft(state, vault(st), pending)
        except (ValueError, TypeError, KeyError, OSError):
            st.error('途中データを戻せませんでした。今の入力は変更していません。原本と保存先を確認してください。')
        else:
            _retire_upload(state)
            state['_work_entry_view'] = 'new'
            state.pop('_work_new_tip', None)
            st.success('途中の訂正・宛名・金額を戻しました。原本と完成イメージを確認してから作成してください。')

    hydrate_inputs(state)
    active = _has_review(state)
    selected_files = bool(state.get('_upload_signature'))
    if active or selected_files:
        state.pop('_work_new_tip', None)
    resume_view = allow_resume and state.get('_work_entry_view') == 'resume'
    drafts = []
    if allow_resume and not active:
        try:
            drafts = available_drafts(vault(st))
        except (ValueError, OSError):
            st.warning('前回の作業一覧を読み込めません。新しい見積は作れます。必要に応じて保存先をご確認ください。')
    def request_new():
        if active or (selected_files and not resume_view):
            state['_work_confirm_start_new'] = True
        else:
            state['_work_entry_view'] = 'new'
            if not selected_files:
                state['_work_new_tip'] = True
            state.pop('_work_confirm_start_new', None)

    def show_resume():
        state['_work_entry_view'] = 'resume'
        state.pop('_work_new_tip', None)
        state.pop('_work_confirm_start_new', None)

    with st.container(key='estimate_entry_choices'):
        st.html('<style>.st-key-estimate_entry_choices [data-testid="stButton"] button{min-height:48px;font-weight:700}</style>')
        st.button('新しい見積を作る', key='work_begin_new', type='secondary' if active else 'primary', width='stretch',
                  on_click=request_new)
        if drafts and not resume_view:
            st.button('前回の続きから再開', key='work_show_resume', width='stretch',
                      on_click=show_resume)
        if state.get('_work_confirm_start_new'):
            if active:
                if _is_checkpointed(state):
                    st.caption('保存済みの見積は残ります。品目の訂正フォームに入力中の内容がある場合は、先に「この品目の訂正を保存」を押してください。')
                else:
                    st.warning('今の入力の保存が確認できていません。見積を切り替えず、画面下の保存状況をご確認ください。')
            else:
                st.caption('選んだファイルを画面から外します。お使いの端末にあるファイルや、保存済みの見積は削除しません。')
            yes, keep = st.columns(2)
            yes.button('切り替えて新しい見積を作る', key='work_confirm_new',
                       on_click=lambda: state.__setitem__('_work_start_new_requested', True),
                       disabled=active and not _is_checkpointed(state), width='stretch')
            keep.button('今の作業を続ける', key='work_cancel_new', width='stretch',
                        on_click=lambda: state.pop('_work_confirm_start_new', None))

    if state.get('_work_new_tip') and not (active or selected_files or resume_view):
        st.info('下の「見積書のPDF・写真を選ぶ」から、新しい見積を始めてください。')

    if not drafts or not resume_view:
        return
    shown = drafts[:30]

    def label(key):
        value = next(v for v in shown if v['id'] == key)
        title = value['state'].get('review_project') or '工事名の入力前'
        when = datetime.fromisoformat(value['saved_at']).astimezone(ZoneInfo('Asia/Tokyo'))
        return f"{when:%m/%d %H:%M} ｜ {title} ｜ 作業{value['work_id'][-4:]}"

    st.subheader('前回の続きから再開')
    chosen = st.selectbox('再開する作業を選ぶ', [v['id'] for v in shown], format_func=label, key='work_resume_choice')
    st.caption('選んだ作業の入力を開きます。保存済みの見積や、ほかの人の作業は上書きしません。')
    if selected_files:
        st.info('この作業を開くと、いま選んでいるファイルは画面から外れます。端末にあるファイルは残ります。')
    label = '選んだファイルを外して、この作業を開く' if selected_files else 'この作業を開く'
    st.button(label, on_click=lambda: state.__setitem__('_work_resume_id', chosen),
              key='work_resume', type='primary', width='stretch')


def autosave(st):
    from review_ui import vault
    if not st.session_state.get('_review_extraction_id'):
        return
    try:
        save_draft(st.session_state, vault(st))
    except (ValueError, TypeError, KeyError, OSError):
        st.warning('途中の入力を自動保存できていません。この画面を閉じず、保存先を確認してください。')
        return
    stamp = st.session_state.get('_work_draft_saved_at')
    if stamp:
        when = datetime.fromisoformat(stamp).astimezone(ZoneInfo('Asia/Tokyo'))
        destination = '共通保存先に' if getattr(vault(st), 'shared', False) else ''
        st.caption(f'途中の入力を{destination}自動保存済み（{when:%H:%M}）。再ログイン後に「前回の続きから再開」で戻せます。')
        st.caption('入力欄はEnterか欄の外を押すと保存されます。1品ずつの訂正は「この品目の訂正を保存」を押してください。')
