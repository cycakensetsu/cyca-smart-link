"""Review each original in one continuous, stable reading flow."""
import copy
from source_review_ui import render_source
from work_effects import confirmed_sources


def render_inputs(st, original, sources, store):
    # One section heading keeps the originals wider than nested expanders did.
    st.subheader('1. 元の見積書と読み取り結果を確認', anchor=False)
    st.caption('原本 → 読み取った品目 → 合計の順に並んでいます。合っていれば、一番下でチェックします。')
    rows = copy.deepcopy(st.session_state.get('_review_assistant_rows', original))
    for index, source in enumerate(sources, 1):
        rows = render_source(st, source, store, index, rows)
    confirmed_sources(st, sources)
    remaining = sum(not source.get('confirmed') for source in sources)
    if remaining:
        st.caption(f'確認できた見積：{len(sources)-remaining} / {len(sources)}件。あと{remaining}件を確認すると、お客様に出す金額の相談へ進めます。')
    return rows, sources
