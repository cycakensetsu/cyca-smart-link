"""Live estimate storage. This path never opens an app vault or browser backup."""
import hashlib
import re
import uuid

from estimate_review import digest


_STORE = '_live_estimate_store'
_FLAG = '_live_estimate_session'
_RESET_KEYS = {
    '_detail_df', '_vendor_summaries', '_summary_data', '_categories_summary',
    '_horizontal_result', '_horizontal_signature', '_force_analyze',
    '_effects_source_checks', '_vault_archive', '_saved_read_notice',
    '_complete_document_cache', '_extraction_cache', '_extraction_reused_pages',
    '_reading_status', '_effects_events', '_effects_seen',
}
_RESET_PREFIXES = (
    '_review_', 'review_', '_work_', '_browser_backup', '_extracted_', '_legacy_',
    'cat_profit_', 'estimate_upload_',
)


class SessionOriginals:
    """Minimal in-memory original bytes, scoped to one authenticated session."""
    def __init__(self, username):
        if not str(username).strip():
            raise ValueError('ログインが必要です。')
        self.username = username
        self.data = {}

    def preserve(self, name, data):
        content = bytes(data)
        source_id = hashlib.sha256(content).hexdigest()
        self.data[source_id] = content
        return {'id': source_id, 'name': str(name), 'size': len(content)}

    def read(self, source_id):
        content = self.data.get(source_id)
        if not content or not re.fullmatch(r'[a-f0-9]{64}', source_id):
            raise ValueError('この見積の原本を開けません。PDF・写真を選び直してください。')
        if hashlib.sha256(content).hexdigest() != source_id:
            raise ValueError('原本のハッシュが一致しません。')
        return content

    def snapshot(self, kind, value):
        # Existing review/conversation code requires a deterministic identity, not
        # a stored copy. Do not make a second array of rows or receipt bytes here.
        if kind != 'extractions':
            raise ValueError('見積はアプリ内に保存しません。')
        return digest(value)


def is_session_estimate(state):
    return state.get(_FLAG) is True


def clear_work(state):
    for key in list(state):
        if key in _RESET_KEYS or key.startswith(_RESET_PREFIXES):
            del state[key]
    state.pop(_STORE, None)
    state['_upload_epoch'] = uuid.uuid4().hex
    state['_upload_signature'] = ()
    state['output_customer'] = ''
    state['output_project'] = ''


def estimate_originals(state):
    username = state.get('username', '')
    store = state.get(_STORE)
    # Compatible class identity across Streamlit hot reload: old objects are
    # already in-memory, but a different authenticated owner never reuses them.
    if store is None or store.username != username:
        store = SessionOriginals(username)
        state[_STORE] = store
    return store


def render_entry(st):
    state = st.session_state
    if not is_session_estimate(state):
        state[_FLAG] = True
        # A WebSocket open across rollout can carry prior durable-backed rows.
        # Never import those rows or touch durable storage from this workflow.
        if state.get('_review_extraction_id') or state.get('_work_resume_id'):
            clear_work(state)
            st.info('この画面では見積を保存しない仕組みに切り替わりました。必要な原本を選び直してください。以前の保存データは変更していません。')
    if state.pop('_live_estimate_clear_requested', False):
        clear_work(state)
        state['_live_estimate_tip'] = True
        state.pop('_live_estimate_confirm', None)

    active = bool(state.get('_review_original_rows') or state.get('_upload_signature'))
    if active:
        state.pop('_live_estimate_tip', None)

    def new():
        if active:
            state['_live_estimate_confirm'] = True
        else:
            state['_live_estimate_tip'] = True

    with st.container(key='estimate_entry_choices'):
        st.html('<style>.st-key-estimate_entry_choices [data-testid="stButton"] button{min-height:48px;font-weight:700}</style>')
        st.button('新しい見積を作る', key='session_estimate_new', type='secondary' if active else 'primary',
                  width='stretch', on_click=new)
        if state.get('_live_estimate_confirm'):
            st.warning('新しい見積に切り替えると、いまの入力は消えます。見積書ができている場合は、先にPDFをPCに保存してください。')
            yes, keep = st.columns(2)
            yes.button('今の入力を閉じて、新しく作る', key='session_estimate_confirm_new', width='stretch',
                       on_click=lambda: state.__setitem__('_live_estimate_clear_requested', True))
            keep.button('このまま続ける', key='session_estimate_cancel_new', width='stretch',
                        on_click=lambda: state.pop('_live_estimate_confirm', None))
        if state.get('_live_estimate_tip') and not active:
            st.info('下の「見積書のPDF・写真を選ぶ」から始めてください。')
        if not active:
            st.caption('作業はこの画面を開いている間だけです。できた見積書はPDFでお使いのPCに保存します。')



def render_invoice_route(st):
    """Read existing durable estimates only after an explicit invoice-mode choice."""
    state = st.session_state
    active = bool(state.get('_review_original_rows') or state.get('_upload_signature'))
    with st.sidebar:
        history = st.checkbox('過去の見積から請求書（必要なときだけ）', value=False,
                              key='show_historical_invoices', disabled=active)
        if active:
            st.caption('請求書を開く前に、この見積をPCに保存し、「新しい見積を作る」で作業を閉じてください。')
    if history and not active:
        st.subheader('過去の見積から請求書を作る')
        st.caption('見積に戻るには、サイドバーの「過去の見積から請求書」のチェックを外してください。')
        from review_ui import durable_vault
        from saved_quotes import render_historical_invoices
        render_historical_invoices(st, durable_vault(st))
        st.stop()
