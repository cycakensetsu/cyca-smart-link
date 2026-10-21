"""Review each original in one continuous, stable reading flow."""
import copy
from source_review_ui import render_source
from work_effects import confirmed_sources


def render_inputs(st, original, sources, store):
    with st.expander('1. 元の見積書と読み取り結果を確認', expanded=True):
        st.caption('元の見積書 → 品目・数量 → 合計金額の順に見比べます。')
        rows = copy.deepcopy(st.session_state.get('_review_assistant_rows', original))
        for index, source in enumerate(sources, 1):
            rows = render_source(st, source, store, index, rows)
        confirmed_sources(st, sources)
        remaining = sum(not source.get('confirmed') for source in sources)
        if remaining:
            st.caption(f'あと{remaining}件の見積を確認してください。確認後、お客様に出す金額の相談へ進めます。')
    return rows, sources
