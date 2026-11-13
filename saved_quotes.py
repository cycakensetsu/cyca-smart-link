"""Open already completed outputs without AI or creating a new approval."""
from datetime import datetime
import json
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo

from estimate_review import digest, verify_approval


def completed_quotes(store):
    entries = list(store.entries())
    exports = {}
    for name, _, _ in entries:
        parts = PurePosixPath(name).parts
        if parts[0] == 'exports':
            exports.setdefault(parts[1], {}).setdefault(PurePosixPath(name).suffix[1:], []).append(name)
    result = []
    for name, _, read in entries:
        if not name.startswith('approvals/'):
            continue
        value = json.loads(read())
        if not isinstance(value, dict) or not isinstance(value.get('payload'), dict):
            continue
        try:
            verify_approval({'unresolved': [], 'fingerprint': digest(value['payload']),
                             'payload': value['payload']}, value)
            when = datetime.fromisoformat(value['approved_at'])
            if when.tzinfo is None:
                continue
        except (ValueError, KeyError, TypeError):
            continue
        files = exports.get(value['id'], {})
        if 'pdf' not in files:
            continue
        header = value['payload'].get('表題') or {}
        if not isinstance(header, dict):
            continue
        result.append({'id': value['id'], 'when': when,
                       'customer': str(header.get('宛名') or ''),
                       'title': str(header.get('工事名称') or header.get('project_name') or header.get('project') or '保存した見積書'),
                       'files': files})
    return sorted(result, key=lambda v: (v['when'], v['id']), reverse=True)


def render_saved_quotes(st, store):
    try:
        quotes = completed_quotes(store)
    except (OSError, ValueError, KeyError, TypeError):
        st.warning('保存した完成見積の一覧を確認できません。接続を確認してから開き直してください。')
        return
    if not quotes:
        return
    with st.expander('保存済みの完成見積を開く'):
        st.caption('作成済みのPDF・Numbers用をそのまま開けます。追加のAI読み取りや再作成は行いません。')
        by_id = {v['id']: v for v in quotes}
        def label(key):
            q = by_id[key]
            return f"{q['when'].astimezone(ZoneInfo('Asia/Tokyo')):%m/%d %H:%M} ｜ {q['title']} ｜ {key[-4:]}"
        chosen = st.selectbox('開く完成見積', list(by_id), format_func=label, key='saved_quote_choice')
        from invoice_workflow_ui import render_invoice_workflow
        render_invoice_workflow(st, store, chosen)
        # Do not download a large output on every rerun or expose a public link.
        if st.button('この完成見積を開く', key='saved_quote_open'):
            selected = by_id[chosen]
            try:
                files = {ext: store.get_entry(sorted(paths)[-1]) for ext, paths in selected['files'].items()}
                if not files.get('pdf'):
                    raise ValueError
            except (ValueError, OSError):
                st.error('保存した完成ファイルを読み込めません。接続と保存先を確認してください。')
            else:
                st.session_state['_saved_quote_files'] = (str(store.root), chosen, files)
        ready = st.session_state.get('_saved_quote_files')
        if ready and ready[:2] == (str(store.root), chosen):
            st.success('保存した完成ファイルを開けます。')
            st.download_button('保存済みPDFを開く・保存', ready[2]['pdf'],
                               file_name='CYCA_保存済み見積_'+chosen[:12]+'.pdf', key='saved_quote_pdf')
            try:
                delivery_config = st.secrets.get('invoice_workflow', {})
            except (FileNotFoundError, KeyError):
                delivery_config = {}
            if delivery_config.get('enabled', False):
                from document_delivery_ui import render_delivery
                import re
                title = re.sub(r'[\\/:*?"<>|]', '_', by_id[chosen]['title'])[:80]
                day = by_id[chosen]['when'].astimezone(ZoneInfo('Asia/Tokyo')).strftime('%Y%m%d')
                render_delivery(st, delivery_config, owner=st.session_state.get('username',''),
                                document_id=chosen, filename=f'{title}見積書_{day}.pdf',
                                pdf=ready[2]['pdf'], customer=by_id[chosen]['customer'])
            if ready[2].get('xlsx'):
                st.download_button('保存済みNumbers用を開く・保存', ready[2]['xlsx'],
                                   file_name='CYCA_保存済み見積_'+chosen[:12]+'.xlsx', key='saved_quote_xlsx')
