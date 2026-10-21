"""Plain-language source checks beside the preserved original."""
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
from source_amounts import read_amounts, net_from_gross


def preview(st, source, store, *, default_page=1, large=False):
    pages = max(1, int(source.get('pages', 1)))
    page = 1
    if pages > 1:
        page = st.selectbox('見るページ', list(range(1, pages + 1)), index=max(0,min(default_page-1,pages-1)), key=('review_source_large_page_' if large else 'review_source_page_') + source['id'])
    key = (source['id'], page, 'large-v2')
    cache = st.session_state.setdefault('_review_source_previews', {})
    try:
        if key not in cache:
            import fitz
            suffix = Path(source['name']).suffix.lower().lstrip('.')
            with fitz.open(stream=store.read(source['id']), filetype=suffix) as document:
                original = document[page - 1]
                scale = min(1600 / max(original.rect.width, 1), 2200 / max(original.rect.height, 1), 3)
                data = original.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes('png')
            if len(cache) >= 8:
                cache.pop(next(iter(cache)))
            cache[key] = data
        st.image(cache[key], width='stretch')
    except (ValueError, RuntimeError, OSError, IndexError):
        st.info('画面に原本を表示できません。下のボタンから保存して確認してください。')
    if not large:
        st.download_button('原本ファイルを保存', store.read(source['id']), file_name=Path(source['name']).name,
                           key='review_original_' + source['id'])


def render_source(st, source, store, index, detail_rows=None):
    sid = source['id']
    readings = st.session_state.setdefault('_review_amount_readings', {})
    if sid not in readings:
        readings[sid] = read_amounts(source['name'], store.read(sid), st.session_state.get('_review_metadata', {}))
    reading = readings[sid]
    source['amount_reading'] = reading
    defaults = st.session_state.setdefault('_review_amount_defaults', {})
    names = {'tax_basis':'review_basis_', 'tax_rate':'review_tax_', 'net_total':'review_net_', 'tax':'review_tax_amount_', 'gross_total':'review_gross_'}
    if sid not in defaults:
        defaults[sid] = True
        # Unconfirmed inputs from the old manual form are replaced by the read totals.
        keep = bool(st.session_state.get('review_source_ok_' + sid))
        for field, prefix in names.items():
            value = reading.get(field)
            if field == 'tax_rate': value='10%'
            if field == 'tax_basis': value='税抜'
            if not keep or prefix + sid not in st.session_state:
                st.session_state[prefix + sid] = value
        if not keep or 'review_tax_exists_' + sid not in st.session_state:
            st.session_state['review_tax_exists_' + sid] = reading['tax'] is not None or reading['gross_total'] is not None
    checked = bool(st.session_state.get('review_source_ok_' + sid))
    with st.expander(f"{index}. {source['name']} ｜ {'確認済み' if checked else '原本を確認する'}", expanded=not checked):
        st.markdown('**読み取った金額**')
        if st.session_state.get('review_net_' + sid) is None:
            st.info('原本の合計を読み取れませんでした。この金額だけ教えてください。')
            missing = st.number_input('原本の最後にある合計金額（円）', value=None, min_value=0, step=1, key='review_missing_gross_' + sid)
            if missing is not None:
                st.session_state['review_net_' + sid] = net_from_gross(missing)
                st.session_state['review_gross_' + sid] = missing
                st.session_state['review_tax_amount_' + sid] = missing - st.session_state['review_net_' + sid]
                st.session_state['review_tax_exists_' + sid] = True
                st.session_state['review_resolve_totals_' + sid] = True
        with st.expander('金額が違うとき・通常と異なる見積だけ修正'):
            st.caption('通常は明細が税抜・消費税10%です。この設定は普段は変更不要です。')
            source['tax_basis'] = st.selectbox('明細の金額の扱い', ['税抜', '税込', '非課税', '不明'], key='review_basis_' + sid)
            rate = st.selectbox('消費税率を変更', ['10%', '8%', '0%', '不明'], key='review_tax_' + sid)
            source['tax_rate'] = {'10%': .10, '8%': .08, '0%': 0}.get(rate)
            source['net_total'] = st.number_input('工事金額（税抜・円）', min_value=0, value=None, step=1, key='review_net_' + sid, format='%d')
            check_tax = st.checkbox('原本に消費税と合計の記載がある', key='review_tax_exists_' + sid)
            tax = st.number_input('原本の消費税（円）', min_value=0, value=None, step=1, key='review_tax_amount_' + sid)
            gross = st.number_input('原本の合計金額（円）', min_value=0, value=None, step=1, key='review_gross_' + sid)
            source['tax'] = tax if check_tax else None
            source['gross_total'] = gross if check_tax else None
            if reading['conflicts']:
                st.checkbox('原本の最後の合計を見て、この金額に修正しました', key='review_resolve_totals_' + sid)
        net = source['net_total']
        calc_tax = int((Decimal(net) * Decimal(str(source['tax_rate'] or 0))).quantize(Decimal(1), rounding=ROUND_HALF_UP)) if net is not None else None
        show_tax = source['tax'] if source['tax'] is not None else calc_tax
        show_gross = source['gross_total'] if source['gross_total'] is not None else (net + show_tax if net is not None else None)
        for col, label, value in zip(st.columns(3), ['工事金額', f"消費税（{source['tax_rate']:.0%}）" if source['tax_rate'] is not None else '消費税', '合計金額'], [net, show_tax, show_gross]):
            col.metric(label, f'{value:,}円' if value is not None else '読み取り待ち')
        rows = [r for r in (detail_rows or []) if r.get('原本ID') == sid]
        problems=[]
        if detail_rows is not None and not rows:
            problems.append('この原本の品目を読み取れませんでした。原本が見積書かを確認し、このファイルをもう一度読み込んでください。')
        if source.get('read_errors'):
            problems.append('読み取りが終わっていないページがあります。原本を確認し、もう一度読み込んでください。')
        try:
            from estimate_review import integer
            actual = sum(integer(r['原価金額']) for r in rows) if rows else None
        except (ValueError, TypeError, OverflowError):
            actual = None
            problems.append('読み取った明細に金額が不明な品目があります。下の品目を確認してください。')
        if rows:
            import math
            bad=[]
            for number,row in enumerate(rows,1):
                fields=[]
                for field,label in [('数量','数量'),('原価単価','単価'),('原価金額','金額')]:
                    try:
                        if row.get(field) in ('',None) or not math.isfinite(float(row[field])): fields.append(label)
                    except (ValueError,TypeError):fields.append(label)
                if fields:bad.append(f"{number}番目の品目の{'・'.join(fields)}")
            missing_pages=[str(n) for n,row in enumerate(rows,1) if not row.get('ページ')]
            if missing_pages and source.get('pages',1)>1:problems.append('原本のどのページか分からない品目があります。もう一度読み込むか、訂正画面で原本のページを指定してください。')
            if bad:problems.append('読み取れなかった項目があります：'+'、'.join(bad)+'。「読み取りが違う品目だけ直す」で原本どおりに入力してください。')
        if net is None: problems.append('原本の合計金額を確認すると次へ進めます。')
        if actual is not None and net is not None and actual != net:
            problems.append(f'原本の工事金額は{net:,}円、読み取った明細の合計は{actual:,}円です。品目の抜けや読み違いを確認してください。')
        if net is not None and show_tax is not None and source['gross_total'] is not None and net+show_tax != source['gross_total']:
            problems.append('工事金額と消費税を足した金額が、原本の合計と合いません。原本を確認してください。')
        if source['tax'] is not None and calc_tax is not None and abs(source['tax']-calc_tax)>1:
            problems.append(f'原本の消費税が設定の{rate}と合いません。「金額が違うとき」の設定で確認できます。')
        if reading['conflicts'] and not st.session_state.get('review_resolve_totals_' + sid):
            problems.append('原本に複数の合計があるため、自動では決められませんでした。「金額が違うとき」で最後の合計を確認してください。')
        if source['tax_basis'] not in ('税抜','非課税') or source['tax_rate'] is None:
            problems.append('通常と異なる見積です。「金額が違うとき」の設定で確認してください。')
        if problems:
            st.warning('\n\n'.join(dict.fromkeys(problems)))
        else:
            st.caption('原本と読み取り結果が合っていれば、下の確認にチェックするだけです。')
        with st.expander('原本を大きく見る', expanded=not checked):
            st.caption('ページを下へスクロールして原本を確認できます。画像右上の拡大ボタンでも大きく表示できます。')
            preview(st, source, store, default_page=reading.get('page',1))
        if rows:
            import pandas as pd
            st.markdown('**読み取った品目**')
            columns = [key for key in ('品名','数量','単位','原価単価','原価金額') if any(key in row for row in rows)]
            st.dataframe(pd.DataFrame(rows).reindex(columns=columns).rename(columns={'原価単価':'単価','原価金額':'金額'}), hide_index=True, width='stretch')
        from estimate_review import digest
        signature = digest([source.get(k) for k in ('tax_basis','tax_rate','net_total','tax','gross_total')] + [rows])
        last = st.session_state.get('_review_source_signature_' + sid)
        if last is not None and last != signature:
            st.session_state['review_source_ok_' + sid] = False
        st.session_state['_review_source_signature_' + sid] = signature
        source['confirmed'] = st.checkbox('この見積の金額・品名・数量は合っています', key='review_source_ok_' + sid, disabled=bool(problems)) and not problems
