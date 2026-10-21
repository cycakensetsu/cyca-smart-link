"""Present actionable findings once; an unfinished step is not an error."""

WORKFLOW = {'原本確認','税区分','原本小計'}
LABELS = {'共通費重複疑い':'同じ費用が重なっていないか確認', '明細重複疑い':'同じ品目が重なっていないか確認',
          '税率混在':'異なる税率の見積が含まれています', '明細検算':'品目の計算を確認', '訂正理由':'読み取りを直した理由を入力'}


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
    st.warning(f'確認したいことが{len(groups)}件あります。該当の品目だけ確認してください。')
    with st.expander('確認する品目と内容',expanded=True):
        for (kind, message), items in groups.items():
            st.markdown('**'+LABELS.get(kind,'内容の確認')+'**')
            st.write(message)
            rows=list(dict.fromkeys(row for item in items for row in item['行']))
            if rows:
                names={row['行ID']:row['品名'] for row in draft['payload'].get('計算明細',[]) if '行ID' in row}
                st.caption('対象：'+'、'.join(names.get(row,'該当品目') for row in rows))
            for item in items:
                if item['レベル']!='停止':
                    resolutions[item['id']]=st.text_input('確認した内容をひとこと入力',key='review_resolution_'+item['id'])
    return resolutions
