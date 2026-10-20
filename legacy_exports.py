"""Keep existing simulation exports usable across Streamlit checkbox reruns."""
import hashlib


def queue_draft_export(st, signature, *args, **kwargs):
    params = dict(zip(('label', 'data', 'file_name', 'mime', 'key'), args))
    params.update(kwargs)
    raw = params['data']
    data = raw.encode() if isinstance(raw, str) else bytes(raw)
    key = str(params.get('key') or params.get('file_name') or params['label'])
    exports = dict(st.session_state.get('_legacy_exports', {}))
    exports[key] = {'signature': signature, 'params': params, 'data': data,
                    'digest': hashlib.sha256(data).hexdigest()}
    st.session_state['_legacy_exports'] = exports
    return False


def render_draft_exports(st, signature):
    entries = [(k, e) for k, e in st.session_state.get('_legacy_exports', {}).items()
               if e['signature'] == signature]
    if not entries:
        return
    st.markdown('### 既存形式の検算用ファイル（未承認）')
    st.caption('原価や未確認内容を含む確認用ファイルです。承認済みの顧客向け帳票は「承認付き統合」から作成してください。')
    for key, entry in entries:
        params = dict(entry['params'])
        checked = st.checkbox('この検算用ファイルの内容を確認した', key='draft_check_'+key+entry['digest'])
        params.update(data=entry['data'], key='draft_export_'+key, disabled=not checked)
        params['label'] = str(params['label'])+'（未承認・検算用）'
        params['file_name'] = '未承認_'+str(params.get('file_name') or '検算用ファイル')
        st.download_button(**params)
