"""Review each original in one continuous, stable reading flow."""
import copy
from html import escape
from source_review_ui import render_source
from work_effects import confirmed_sources


def render_inputs(st, original, sources, store):
    from workflow_steps_ui import render_step_heading
    # One section heading keeps the originals wider than nested expanders did.
    render_step_heading(st, 2)
    st.caption('左の原本と、右の品目・合計を見比べます。合っていれば右下にチェック。見積書以外の資料は「参考資料として残す」を選びます。')
    rows = copy.deepcopy(st.session_state.get('_review_assistant_rows', original))
    for index, source in enumerate(sources, 1):
        rows = render_source(st, source, store, index, rows)
    # Retain manual corrections as working data before a reference choice or AI
    # proposal refreshes editor keys. The original extraction remains untouched.
    st.session_state['_review_assistant_rows'] = copy.deepcopy(rows)
    confirmed_sources(st, sources)
    remaining = [source for source in sources if not source.get('confirmed')]
    if remaining:
        st.info(f'あと{len(remaining)}件を確認すると、金額を決める工程へ進めます。下の名前を押すと、その資料に戻れます。')
        for source in remaining:
            name = source.get('project_name') or source.get('name') or '未確認の資料'
            vendor = source.get('vendor_name')
            label = ' ｜ '.join(v for v in (vendor, name) if v)
            st.html(f'<p><a href="#cyca-source-{source["id"]}">確認する：{escape(label)}</a></p>')
    else:
        from workflow_steps_ui import render_next_action
        st.success('確認できました。次はお客様に出す金額を決めます。')
        render_next_action(st, 3, '次へ：金額を決める')
    return rows, sources
