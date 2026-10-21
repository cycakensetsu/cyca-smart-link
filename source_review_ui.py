"""Plain-language source checks beside the preserved original."""
from pathlib import Path


def preview(st, source, store):
    pages = max(1, int(source.get('pages', 1)))
    page = 1
    if pages > 1:
        page = st.selectbox('原本の見るページ', list(range(1, pages + 1)), key='review_source_page_' + source['id'])
    key = (source['id'], page)
    cache = st.session_state.setdefault('_review_source_previews', {})
    try:
        if key not in cache:
            import fitz
            suffix = Path(source['name']).suffix.lower().lstrip('.')
            with fitz.open(stream=store.read(source['id']), filetype=suffix) as document:
                original = document[page - 1]
                scale = min(760 / max(original.rect.width, 1), 760 / max(original.rect.height, 1), 1.5)
                data = original.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes('png')
            if len(cache) >= 8:
                cache.pop(next(iter(cache)))
            cache[key] = data
        st.image(cache[key], width='stretch')
    except (ValueError, RuntimeError, OSError, IndexError):
        st.info('画面に原本を表示できません。下のボタンから保存して確認してください。')
    st.download_button('元の見積書を保存', store.read(source['id']), file_name=Path(source['name']).name,
                       key='review_original_' + source['id'])


def render_source(st, source, store, index):
    sid = source['id']
    checked = bool(st.session_state.get('review_source_ok_' + sid))
    with st.expander(f"{index}. {source['name']} ｜ {'確認済み' if checked else '原本を確認する'}", expanded=not checked):
        st.caption('左の元見積を見ながら、右の3項目を確認します。下の明細表では品名・数量・単価も照合してください。')
        original, checks = st.columns([1, 1.15])
        with original:
            st.markdown('**元の見積書**')
            preview(st, source, store)
        with checks:
            st.markdown('**原本に書かれている金額を確認**')
            source['tax_basis'] = st.selectbox('① 明細の単価・金額は税込？ 税抜？', ['不明', '税抜', '税込', '非課税'], key='review_basis_' + sid)
            st.caption('消費税を別に足す見積は「税抜」。単価に税を含む場合は「税込」。判断できなければ「不明」のまま確認を保留します。')
            rate = st.selectbox('② 消費税率', ['不明', '10%', '8%', '0%'], key='review_tax_' + sid)
            source['tax_rate'] = {'10%': .10, '8%': .08, '0%': 0}.get(rate)
            source['net_total'] = st.number_input('③ 原本の税抜合計（値引き後・円）', min_value=0, value=0, step=1, key='review_net_' + sid, format='%d')
            st.caption('原本の「税抜合計」または「小計」を入力します。消費税を含む最終総額ではありません。')
            with st.expander('原本に消費税額・税込合計も書いてある場合'):
                check_tax = st.checkbox('税額と税込合計も照合する', key='review_tax_exists_' + sid)
                if check_tax:
                    source['tax'] = st.number_input('原本の消費税額（円）', min_value=0, value=0, step=1, key='review_tax_amount_' + sid)
                    source['gross_total'] = st.number_input('原本の税込合計（円）', min_value=0, value=0, step=1, key='review_gross_' + sid)
            source['confirmed'] = st.checkbox('原本と明細・金額・税の扱いを確認した', key='review_source_ok_' + sid)
            st.caption('わからない箇所があるときは、チェックせず確認してください。')
