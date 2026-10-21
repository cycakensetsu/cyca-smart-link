"""One vertical reading check, with the next action only after confirmation."""
import copy
import pandas as pd
from estimate_review import clean
from source_review_ui import render_source
from work_effects import confirmed_sources


def render_inputs(st, original, sources, store):
    checked = all(st.session_state.get('review_source_ok_' + s['id']) for s in sources)
    with st.expander('1. 原本と読み取った内容を確認', expanded=not checked):
        st.caption('原本と金額・品目が合っていれば、確認にチェックするだけです。')
        source_slot = st.container()
        columns = ['採用', '品名', '仕様', '数量', '単位', '原価単価', '原価金額', '見積元', '工事種別', '部位', '顧客備考', '元ファイル', 'ページ', '集計のみ', '訂正理由', '除外理由', '備考', '行ID']
        view = pd.DataFrame(st.session_state.get('_review_assistant_rows', original))[columns]
        with st.expander('読み取りが違う品目だけ直す'):
            st.caption('読み違いを直す場合は「訂正理由」、品目を外す場合は「除外理由」を入力してください。元の見積書は変更しません。')
            edited = st.data_editor(view, disabled=['行ID','元ファイル','備考'], hide_index=True, num_rows='fixed',
                key='review_rows_' + str(st.session_state.get('_review_editor_epoch',0)), width='stretch',
                column_config={'行ID':None, '原価単価':st.column_config.NumberColumn('単価',format='localized'),
                               '原価金額':st.column_config.NumberColumn('金額',format='localized')})
        by_id = {r['行ID']:copy.deepcopy(r) for r in original}
        rows=[]
        for changes in edited.to_dict('records'):
            row=by_id[changes['行ID']]
            row.update({key:clean(value) for key,value in changes.items()})
            rows.append(row)
        with source_slot:
            for index, source in enumerate(sources,1):
                render_source(st,source,store,index,rows)
        confirmed_sources(st,sources)
    return rows, sources
