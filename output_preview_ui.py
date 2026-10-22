"""Review the actual template pages before consenting to final PDF output."""
from estimate_review import build_preview, estimate_for_output
from conversation_assistant import print_rows


def page_controls(st, rows, metadata):
    printed = print_rows(rows)
    labels = {r['row_id']: f"No.{r['no']} {r['trade']}／{r['name']}" for r in printed}
    options = list(labels)
    if st.session_state.pop('_review_reset_pages', False):
        st.session_state['review_page_breaks'] = []
        st.session_state.pop('_review_requested_pages', None)
        st.session_state.pop('_review_preview', None)
    # A row excluded since the previous preview is no longer a page boundary.
    current = st.session_state.get('review_page_breaks', [])
    if any(k not in options for k in current):
        st.session_state['review_page_breaks'] = [k for k in current if k in options]
        st.session_state['_review_requested_pages'] = {k: v for k, v in st.session_state.get('_review_requested_pages', {}).items() if k in options}
    def manual_change():
        st.session_state.pop('_review_requested_pages', None)
    with st.expander('ページの区切りを変える（必要なときだけ）'):
        st.caption('通常は工種ごとに分けます。2行以下の小さな工種は他の明細と同じページにします。一式まとめでは工種を別々に載せます。')
        selected = st.multiselect('ここから新しいページにする品目', options,
            format_func=lambda k: labels[k], key='review_page_breaks', on_change=manual_change)
        st.caption('区切りを指定した場合は、その位置を優先します。1ページ20行に収まらない場合は追加で改ページします。「足場工事から4ページ目にして」と声で相談することもできます。')
        if st.button('自動で見やすく分け直す', key='review_auto_pages'):
            st.session_state['_review_reset_pages'] = True
            st.rerun()
    metadata['改ページ'] = selected
    metadata['希望ページ'] = st.session_state.get('_review_requested_pages', {})
    return metadata


def render_preview(st, draft, valid):
    from workflow_steps_ui import render_step_heading
    render_step_heading(st, 4)
    st.caption('まだ確定しません。実際の流し込みデザインで、表紙から最後の明細まで確認できます。')
    if draft['unresolved'] and valid:
        st.info('重複の可能性がある品目を含む確認用です。仕上がりは先に見られます。提出用の確定前に、上の重複確認で採用する内容を決めてください。')
    st.caption('今やること：下のボタンで、見積書の仕上がりを確認します。')
    if st.button('完成イメージを見る', key='review_preview', disabled=not valid, type='primary'):
        st.session_state.pop('_review_preview', None)
        try:
            from pour_layout import describe
            from pdf_worker import render_pages
            with st.spinner('ページの完成イメージを作っています…'):
                result = build_preview(draft)
                if not result.ok:
                    raise ValueError(' / '.join(result.errors))
                pages = describe(estimate_for_output(draft))
                images=render_pages(result.pdf_data)
                if len(images) != len(pages):raise ValueError('ページ数を確認できません。')
                st.session_state['_review_preview'] = {'fingerprint': draft['fingerprint'], 'images': images, 'pages': pages}
                from work_effects import cue
                cue(st, 'preview')
        except (ValueError, OSError) as exc:
            st.error('完成イメージを作れませんでした。' + str(exc))
    preview = st.session_state.get('_review_preview')
    ready = bool(preview and preview['fingerprint'] == draft['fingerprint'] and valid)
    if ready:
        pages = preview['pages']
        st.success(f"全{len(pages)}ページです。施工業者名はお客様向け帳票に載せません。")
        index = st.selectbox('見たいページを選ぶ', range(len(pages)),
            format_func=lambda i: f"{pages[i]['page']}ページ目：{pages[i]['label']}",
            key='review_preview_page_' + draft['fingerprint'])
        details = pages[index]['rows']
        if details:
            st.caption(f"No.{details[0]['no']}〜No.{details[-1]['no']}（{len(details)}品目）")
        st.image(preview['images'][index], width='stretch')
        if details:
            with st.expander('このページの品目を確認する'):
                for row in details:
                    st.write(f"No.{row['no']} {row['name']}")
        st.info('直したい場合は、上の「ページの区切りを変える」か音声相談を使い、完成イメージをもう一度確認してください。')
        st.write('このページ分けでPDFを作成してよいですか？')
        st.caption('よければ、下の「内容とページ分けは、これでOKです」にチェックし、「この内容で見積書を作成」を押してください。')
    elif preview:
        st.info('内容やページの区切りが変わりました。「完成イメージを見る」を押して、最新の配置を確認してください。')
    return ready
