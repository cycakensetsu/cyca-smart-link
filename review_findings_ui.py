"""Present actionable findings once; an unfinished step is not an error."""
import copy

WORKFLOW = {'原本確認','税区分','原本小計'}
LABELS = {'共通費重複疑い':'同じ費用が重なっていないか確認', '明細重複疑い':'同じ品目が重なっていないか確認',
          '税率混在':'異なる税率の見積が含まれています', '明細検算':'品目の計算を確認', '訂正理由':'読み取りを直した理由を入力'}
DUPLICATES = {'共通費重複疑い', '明細重複疑い'}


def _duplicate_choice(st, payload, row_ids, key):
    answer = ''
    if st.checkbox('施工範囲が別なので、すべて使う', key=key+'_both'):
        answer = '原本を確認し、施工範囲が別なので、すべて使う'
    reviewed = payload.get('確認明細', [])
    available = {r['行ID']: (number, r) for number, r in enumerate(reviewed, 1)
                 if r.get('行ID') in row_ids and r.get('採用', True)}
    if available:
        def label(row_id):
            number, row = available[row_id]
            context = [str(row.get(field) or '') for field in ('見積元', '元ファイル')]
            if row.get('ページ'): context.append(str(row['ページ'])+'ページ')
            return f"No.{number} {row.get('品名') or '品名なし'} ｜ " + ' ｜ '.join(v for v in context if v)
        st.caption('同じ施工内容なら、使わない方を選んで外せます。元の原本と品目の記録は残ります。')
        selected = st.multiselect('今回使わない品目を選ぶ', list(available), format_func=label,
                                  key=key+'_exclude')
        if st.button('選んだ品目を外す', key=key+'_apply_exclusion', disabled=not selected):
            updated = copy.deepcopy(reviewed)
            for row in updated:
                if row.get('行ID') in selected and row.get('行ID') in available:
                    row['採用'] = False
                    row['除外理由'] = '原本を見て重複のため除外'
            st.session_state['_review_assistant_rows'] = updated
            st.session_state['_review_editor_epoch'] = st.session_state.get('_review_editor_epoch', 0) + 1
            for state_key in ('_review_result', '_review_approval', '_review_preview', '_vault_archive', '_review_proposal'):
                st.session_state.pop(state_key, None)
            st.session_state['_review_export_ready'] = False
            st.session_state['_review_assistant_notice'] = '選んだ品目を見積から外しました。原本は残しています。変更した内容を確認してください。'
            st.rerun()
    return answer


def render_findings(st, draft, ready):
    resolutions={}
    if not ready:
        return resolutions
    groups={}
    for item in draft['payload']['指摘']:
        key=(item['種類'],item['内容'])
        groups.setdefault(key,[]).append(item)
    if not groups:
        return resolutions
    from estimate_review import digest
    prepared = []
    remaining = 0
    for (kind, message), items in groups.items():
        warnings = [item for item in items if item['レベル'] != '停止']
        key = 'review_resolution_group_'+digest([kind,message,[item['id'] for item in warnings]])
        answer = (st.session_state.get(key+'_both', False) if kind in DUPLICATES
                  else str(st.session_state.get(key, '') or '').strip())
        if not answer or any(item['レベル'] == '停止' for item in items):
            remaining += 1
        prepared.append((kind, message, items, warnings, key))
    if remaining:
        st.warning(f'確認したいことが{remaining}件あります。該当の品目だけ確認してください。')
    else:
        st.caption('確認事項はすべて確認済みです')
    # Collapsing changes only presentation. Every widget is still rendered so
    # Streamlit retains answers and the approval checks receive all resolutions.
    with st.expander('確認する品目と内容' if remaining else '確認した内容を見る',expanded=bool(remaining)):
        for kind, message, items, warnings, key in prepared:
            st.markdown('**'+LABELS.get(kind,'内容の確認')+'**')
            st.write(message)
            rows=list(dict.fromkeys(row for item in items for row in item['行']))
            if rows:
                names={row['行ID']:row['品名'] for row in draft['payload'].get('計算明細',[]) if '行ID' in row}
                st.caption('対象：'+'、'.join(names.get(row,'該当品目') for row in rows))
            if warnings:
                if kind in DUPLICATES:
                    warning_rows = list(dict.fromkeys(row for item in warnings for row in item['行']))
                    answer = _duplicate_choice(st, draft['payload'], warning_rows, key)
                else:
                    answer=st.text_input('上の品目すべてについて確認した内容を入力',key=key)
                resolutions.update({item['id']:answer for item in warnings})
    return resolutions
