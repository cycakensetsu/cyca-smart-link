"""Opt-in invoice preview from the existing approved estimate archive.
Explicit confirmation only; Gmail is disabled without private runtime settings.
"""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def render_invoice_workflow(st, store, approval_id):
    try:
        config = st.secrets.get('invoice_workflow', {})
    except (FileNotFoundError, KeyError):
        return
    if not config.get('enabled', False):
        return
    actor = st.session_state.get('username', '')
    permissions = config.get('member_permissions', {}).get(actor, []) if config.get('company_id') else ['read','prepare','approve','send']
    if 'read' not in permissions or 'prepare' not in permissions:
        st.warning('請求書作成の権限がありません。')
        return
    from invoice_documents import build_invoice, render_invoice_pdf, invoice_filename, invoice_fingerprint
    approval = None
    for name, _, read in store.entries():
        if name.startswith('approvals/'):
            candidate = json.loads(read())
            if candidate.get('id') == approval_id:
                approval = candidate
                break
    if approval is None:
        st.error('承認済み見積の記録がありません。')
        return
    prefix = 'invoice_' + approval_id
    st.subheader('この見積から請求書を作成')
    st.caption('請求書は工事名・1式・見積参照で作成します。確定後に宛先と本文を確認してから送信できます。')
    today = datetime.now(ZoneInfo('Asia/Tokyo')).date()
    next_month = (today.replace(day=28)+timedelta(days=4)).replace(day=1)
    due = (next_month.replace(day=28)+timedelta(days=4)).replace(day=1)-timedelta(days=1)
    number = st.text_input('請求書番号（既存の番号と重複しない番号）', key=prefix+'_number')
    issue = st.date_input('発行日', value=today, key=prefix+'_issue')
    deadline = st.date_input('支払期限', value=due, key=prefix+'_due')
    estimate_date = st.text_input('元見積の日付（YYYY-MM-DD）', key=prefix+'_estimate_date')
    if st.button('請求書の確認用PDFを作る', key=prefix+'_build'):
        try:
            invoice = build_invoice(approval, invoice_number=number, issue_date=issue.isoformat(),
                                    due_date=deadline.isoformat(), issuer=dict(config.get('issuer', {})),
                                    estimate_date=estimate_date or None)
            st.session_state[prefix+'_preview'] = invoice
            st.session_state[prefix+'_inputs'] = (number,issue,deadline,estimate_date)
        except (ValueError, KeyError) as exc:
            st.error(str(exc))
    invoice = st.session_state.get(prefix+'_preview')
    if invoice and st.session_state.get(prefix+'_inputs') == (number,issue,deadline,estimate_date):
        st.download_button('確認用PDF', render_invoice_pdf(invoice), file_name=invoice_filename(invoice),key=prefix+'_preview_download')
        st.info('宛名・工事名・金額・発行日・支払期限・振込先をPDFで確認してください。')
        try:
            from workflow_runtime import configured_store, company_scope
            from invoice_registry import InvoiceRegistry
            registry = InvoiceRegistry(configured_store(config))
        except (ValueError, OSError):
            st.warning('正式保存には非公開の永続保存先の設定が必要です。')
            return
        fingerprint = invoice_fingerprint(invoice)
        owner = st.session_state.get('username', '')
        try:
            company = company_scope(config, owner)
        except ValueError:
            st.error('会社の利用権限を確認してください。')
            return
        if 'approve' not in permissions:
            st.info('正式確定には承認権限のある担当者の確認が必要です。')
            return
        confirmed = st.checkbox('請求書PDFの内容を確認しました', key=prefix+'_confirm_'+fingerprint)
        if confirmed and st.button('請求書を確定してPDF保存', key=prefix+'_finalize_'+fingerprint):
            try:
                pdf = registry.finalize(company, invoice, fingerprint, owner)
                st.session_state[prefix+'_final'] = (fingerprint, pdf)
            except ValueError as exc:
                st.error(str(exc))
        final = st.session_state.get(prefix+'_final')
        if final and final[0] == fingerprint:
            st.download_button('正式な請求書PDFをダウンロード', final[1], file_name=invoice_filename(invoice), key=prefix+'_final_download')
            from document_delivery_ui import render_delivery
            render_delivery(st, config, owner=owner, document_id=fingerprint,
                            filename=invoice_filename(invoice), pdf=final[1], customer=invoice['customer'])
