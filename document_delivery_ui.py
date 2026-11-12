"""Review and explicit send panel. No send on rerun, upload, or preview."""
import hashlib
from document_delivery import address
from workflow_runtime import configured_service


def render_delivery(st, config, *, owner, document_id, filename, pdf, customer):
    prefix = 'delivery_' + hashlib.sha256((document_id+filename).encode()).hexdigest()[:16]
    st.subheader('PDFをGmailで送信')
    st.caption('本文に見積額は記載しません。宛先・本文・添付の内容をご確認ください。')
    recipient = st.text_input('宛先メールアドレス', key=prefix+'_to')
    subject = st.text_input('件名',value=filename.removesuffix('.pdf'),key=prefix+'_subject')
    body = st.text_area('本文',value=f'{customer}\n\nいつもお世話になっております。\n添付の書類をご確認ください。\nどうぞよろしくお願いいたします。',key=prefix+'_body')
    try:
        workflow = configured_service(config,owner)
    except (ValueError, ImportError, OSError):
        st.warning('永続保存先またはGoogle連携が未設定です。PDFの確認・保存はできますが、送信はできません。')
        return
    history=workflow.store.history(workflow.scope,document_id)
    if history:
        with st.expander('この書類の送信履歴（再接続後も保持）'):
            for item in history:
                st.write(item['updated_at']+' ｜ '+item['payload']['recipient']+' ｜ '+item['state'])
                if st.button('この記録を確認・再開',key=prefix+'_restore_'+item['id']):
                    st.session_state[prefix+'_restored']=item['id']
    restored=st.session_state.get(prefix+'_restored')
    if restored:
        old=workflow.review(restored)
        st.info('保存済み記録：'+old['payload']['recipient']+' ／ '+old['payload']['filename']+' ／ '+old['state'])
        st.write(old['payload']['subject']); st.text(old['payload']['body'])
        if old['state'] in ('sending','unknown') and st.button('保存済み記録をGmailと照合',key=prefix+'_restore_check_'+restored):
            try:
                result=workflow.reconcile_email(restored)
                st.write('照合結果：'+result['state'])
            except Exception: st.error('照合できません。再送していません。')
        if old['state'] in ('review','approved'):
            if st.button('未送信のこの記録を取り消す',key=prefix+'_cancel_'+restored):
                try:
                    workflow.cancel_email(restored)
                    st.success('未送信記録を取り消しました。新たな内容で確認できます。')
                    st.rerun()
                except ValueError: st.error('送信開始済みのため取り消せません。')
            resume_confirm=st.checkbox('保存済み記録の宛先・本文・添付PDFを確認しました',key=prefix+'_restore_ok_'+restored)
            st.download_button('保存済み添付PDFを確認',workflow.store.get(workflow.scope,restored)['pdf'],file_name=old['payload']['filename'],key=prefix+'_restore_pdf_'+restored)
            if resume_confirm and st.button('保存済み記録を送信する',key=prefix+'_restore_send_'+restored):
                try:
                    if workflow.transport is None: raise ValueError()
                    if old['state']=='review': workflow.approve_email(restored,old['fingerprint'])
                    result=workflow.send_email(restored)
                    st.success('Gmail送信を確認しました。メールID：'+result['gmail_id'])
                except (ValueError,RuntimeError): st.error('送信未完了または結果未確認です。自動再送していません。')
    inputs=(recipient,subject,body,hashlib.sha256(pdf).hexdigest())
    if st.button('宛先・本文・添付を確定して送信前確認へ',key=prefix+'_prepare'):
        try:
            address(recipient)
            import uuid
            record = workflow.prepare_email(uuid.uuid4().hex,sender=config.get('sender',''),recipient=recipient,
                subject=subject,body=body,filename=filename,pdf=pdf,document_id=document_id)
            st.session_state[prefix+'_record']=record['id']
            st.session_state[prefix+'_inputs']=inputs
        except ValueError as exc: st.error(str(exc))
    key=st.session_state.get(prefix+'_record')
    if not key or st.session_state.get(prefix+'_inputs')!=inputs: return
    record=workflow.review(key)
    st.write('送信状態：'+record['state'])
    st.write('宛先：'+record['payload']['recipient'])
    st.write('添付：'+record['payload']['filename'])
    confirmed=st.checkbox('宛先・本文・添付PDFの中身を確認しました',key=prefix+'_confirmed_'+key)
    if record['state']=='review' and confirmed and st.button('確認した内容で送信する',key=prefix+'_send_'+key):
        if workflow.transport is None:
            st.error('Gmail認可が未設定です。送信していません。')
        else:
            try:
                workflow.approve_email(key,record['fingerprint'])
                sent=workflow.send_email(key)
                st.success('Gmail送信を確認しました。メールID：'+sent['gmail_id'])
            except (ValueError,RuntimeError):
                st.error('送信を完了できていないか、結果が未確認です。履歴を確認し、自動再送は行いません。')
    elif record['state']=='approved':
        st.warning('承認済みですが送信処理はまだ開始されていません。')
        if confirmed and st.button('承認済みメールを送信',key=prefix+'_resume_'+key):
            try:
                result=workflow.send_email(key)
                st.success('Gmail送信を確認しました。メールID：'+result['gmail_id'])
            except (ValueError,RuntimeError): st.error('送信を完了できていないか、結果未確認です。再送していません。')
    elif record['state'] in ('sending','unknown'):
        st.warning('送信結果が未確認です。重複送信防止のため再送しません。')
        if st.button('Gmail送信済みを照合する',key=prefix+'_reconcile_'+key):
            try:
                result=workflow.reconcile_email(key)
                if result['state']=='sent': st.success('送信済みを確認しました。')
                else: st.warning('まだ確定できません。再送していません。')
            except Exception: st.error('照合できません。再送していません。')
