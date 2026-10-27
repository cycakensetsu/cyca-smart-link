"""Keyboard-accessible alternative to editing the wide canvas table."""
import copy
import math


def _numeric(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def apply_correction(state, current_rows, row_id, fields):
    allowed = {'品名','仕様','数量','単位','原価単価','原価金額','部位','ページ','見積元','訂正理由'}
    if set(fields) != allowed or not str(fields.get('訂正理由') or '').strip():
        raise ValueError('原本を確認して直した理由を入力してください。')
    by_id = {r['行ID']: copy.deepcopy(r) for r in current_rows}
    if row_id not in by_id or len(by_id) != len(current_rows):
        raise ValueError('訂正する品目を確認してください。')
    full = copy.deepcopy(state.get('_review_assistant_rows',state.get('_review_original_rows',[])))
    if row_id not in {r['行ID'] for r in full}:
        raise ValueError('訂正する原本を確認してください。')
    by_id[row_id].update(fields)
    state['_review_assistant_rows'] = [by_id.get(r['行ID'],r) for r in full]
    state['_review_editor_epoch'] = state.get('_review_editor_epoch',0)+1
    state['review_source_ok_'+by_id[row_id]['原本ID']] = False
    for key in ('_review_preview','_review_approval','_review_result','_vault_archive','_review_proposal'):
        state.pop(key,None)
    state['_review_export_ready'] = False


def render_item_correction(st, rows, sid):
    with st.expander('1品ずつ入力して直す（表の操作が難しいとき）'):
        options = {r['行ID']:r for r in rows}
        if not options:
            return
        chosen = st.selectbox('直す品目',list(options),
            format_func=lambda k: f"{list(options).index(k)+1}. {options[k]['品名']}",
            key='review_single_choice_'+sid)
        row = options[chosen]
        epoch = st.session_state.get('_review_editor_epoch',0)
        prefix = 'review_single_'+sid+'_'+str(epoch)+'_'+chosen
        st.caption('原本の記載どおりに直し、理由を残してください。数量や金額が不明なままでも保存できますが、確認・出力はできません。')
        with st.form(prefix):
            fields = {}
            for field,label in [('品名','品名'),('仕様','仕様・寸法'),('部位','施工場所・部位'),('見積元','見積書の業者名')]:
                widget = st.text_area if field=='仕様' else st.text_input
                fields[field] = widget(label,value=str(row.get(field) or ''),key=prefix+'_'+field)
            for field,label in [('数量','数量'),('原価単価','仕入単価'),('原価金額','仕入金額'),('ページ','原本のページ')]:
                fields[field] = st.number_input(label,value=_numeric(row.get(field)),step=1.0,key=prefix+'_'+field)
            fields['単位'] = st.text_input('単位',value=str(row.get('単位') or ''),key=prefix+'_単位')
            fields['訂正理由'] = st.text_input('直した理由（必須）',value=str(row.get('訂正理由') or ''),key=prefix+'_訂正理由')
            if st.form_submit_button('この品目の訂正を保存'):
                try:
                    apply_correction(st.session_state,rows,chosen,fields)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
