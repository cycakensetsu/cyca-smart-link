"""Original, items, totals, then the human check in a stable reading order."""
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
from html import escape
import math
from source_amounts import read_amounts, net_from_gross


def _turn_page(state, key, change, pages):
    state[key] = max(1, min(pages, state.get(key, 1) + change))


def _preview_body(st, source, store, *, default_page=1, large=False):
    pages = max(1, int(source.get('pages', 1)))
    page_key = ('review_source_large_page_' if large else 'review_source_page_') + source['id']
    if page_key not in st.session_state:
        st.session_state[page_key] = max(1, min(default_page, pages))
    page = max(1, min(pages, st.session_state[page_key]))
    data = _preview_data(st, source, store, page)
    if pages > 1:
        nav='source_navigation_'+source['id']
        st.html(f'<style>.st-key-{nav} [data-testid="stHorizontalBlock"]{{flex-wrap:nowrap!important}}'
                f'.st-key-{nav} [data-testid="stColumn"]{{min-width:0!important;flex:1 1 0!important}}'
                f'.st-key-{nav} p{{font-size:13px;text-align:center}}</style>')
        with st.container(key=nav):
            previous, position, following = st.columns([1, 1.2, 1],vertical_alignment='center',gap='small')
            previous.button('← 前へ', key=page_key+'_previous', disabled=page == 1, on_click=_turn_page,
                            args=(st.session_state, page_key, -1, pages), width='stretch')
            position.markdown(f'**{page} / 全{pages}ページ**')
            following.button('次へ →', key=page_key+'_next', disabled=page == pages, on_click=_turn_page,
                             args=(st.session_state, page_key, 1, pages), width='stretch')
    else:
        st.caption('1 / 全1ページ')
    if data is not None:
        # Changing this container's identity also replaces the old image node,
        # so a new page number cannot briefly label the previous bitmap.
        with st.container(key='source_image_'+source['id']+'_'+str(page)):
            st.image(data, width='stretch')
    else:
        st.info('原本を表示できませんでした。元のファイルを開いて確認してください。')


def _preview_data(st, source, store, page):
    key = (source['id'], page, 'large-v2')
    cache = st.session_state.setdefault('_review_source_previews', {})
    try:
        if key not in cache:
            from pdf_worker import render_original
            suffix = Path(source['name']).suffix.lower().lstrip('.')
            data = render_original(store.read(source['id']), suffix, page)
            if len(cache) >= 8:
                cache.pop(next(iter(cache)))
            cache[key] = data
        return cache[key]
    except (ValueError, RuntimeError, OSError, IndexError):
        return None


def preview(st, source, store, *, default_page=1, large=False):
    # Page turns update this fragment only, keeping the surrounding form intact.
    fragment = getattr(st, 'fragment', lambda function: function)
    return fragment(_preview_body)(st, source, store, default_page=default_page, large=large)


def source_title(source, metadata, source_count):
    names = list(dict.fromkeys(str(item.get('工事名称') or '').strip()
                  for item in metadata.get('summary_sources', [])
                  if item.get('元ファイル') == source['name'] and str(item.get('工事名称') or '').strip()))
    if len(names) == 1:
        return names[0]
    if not names and source_count == 1:
        return str(metadata.get('工事名称') or '').strip()
    return ''


def source_vendor(source, metadata, rows):
    candidates=[str(r.get('見積元') or '').strip() for r in rows if r.get('原本ID')==source['id']]
    candidates += [str(s.get('見積元') or '').strip() for s in metadata.get('summary_sources',[])
                   if s.get('元ファイル')==source['name']]
    known=list(dict.fromkeys(v for v in candidates if v and v.lower() not in ('不明','unknown','不明な会社','none','nan')))
    return known[0] if len(known)==1 else ('業者名を確認' if known else '業者名の記載なし')


def _number(value):
    try:
        value = Decimal(str(value))
        if not value.is_finite(): return '要確認'
        return f'{value:,.0f}' if value == value.to_integral_value() else f'{value:,f}'.rstrip('0').rstrip('.')
    except Exception:
        return '要確認'


def _items_html(rows):
    table = []
    for number, row in enumerate(rows, 1):
        name = escape(str(row.get('品名') or '品名を確認してください'))
        part = str(row.get('部位') or '')
        context = part if part and part != str(row.get('工事種別') or '') else ''
        spec = escape(' ｜ '.join(v for v in (context, str(row.get('仕様') or '')) if v))
        quantity = escape(_number(row.get('数量')) + ' ' + str(row.get('単位') or ''))
        table.append(f'<tr><td><b>{number}. {name}</b><small>{spec}</small></td><td>{quantity}</td>'
                     f'<td>{escape(_number(row.get("原価単価")))}円</td><td>{escape(_number(row.get("原価金額")))}円</td></tr>')
    return ('<style>.cyca-source-items{width:100%;border-collapse:collapse;font-size:14px;table-layout:fixed}'
            '.cyca-source-items th,.cyca-source-items td{padding:10px 5px;border-bottom:1px solid #d9e4eb;overflow-wrap:anywhere;text-align:right}'
            '.cyca-source-items th:first-child,.cyca-source-items td:first-child{text-align:left;width:43%}'
            '.cyca-source-items small{display:block;font-size:12px;color:#647687;margin-top:4px}'
            '@media(max-width:600px){.cyca-source-items{font-size:12px}.cyca-source-items th:first-child,.cyca-source-items td:first-child{width:36%}}</style>'
            '<table class="cyca-source-items"><thead><tr><th>品目・仕様</th><th>数量</th><th>単価</th><th>金額</th></tr></thead><tbody>'
            + ''.join(table) + '</tbody></table>')


def _render_items(st, rows, sid):
    st.subheader('② 読み取った品目',anchor=False)
    table_slot = st.container()
    with st.expander('品目の読み取りが違うときだけ直す'):
        import pandas as pd
        from estimate_review import clean
        st.caption('原本を見ながら直してください。訂正理由も残します。合計に合わせるための架空の品目は追加できません。')
        columns = ['品名','仕様','数量','単位','原価単価','原価金額','訂正理由','ページ','見積元','工事種別','部位','顧客備考','集計のみ','採用','除外理由','行ID']
        edited = st.data_editor(pd.DataFrame(rows).reindex(columns=columns), hide_index=True, num_rows='fixed',
            key='review_source_items_'+sid+'_'+str(st.session_state.get('_review_editor_epoch',0)), width='stretch',
            disabled=['行ID'], column_config={'行ID':None, '原価単価':st.column_config.NumberColumn('単価', format='localized'),
                                             '原価金額':st.column_config.NumberColumn('金額', format='localized')})
        by_id = {r['行ID']:dict(r) for r in rows}
        for change in edited.to_dict('records'):
            by_id[change['行ID']].update({k:clean(v) for k,v in change.items()})
        updated = list(by_id.values())
    with table_slot:
        st.html(_items_html(updated))
    return updated


def render_source(st, source, store, index, detail_rows=None):
    sid = source['id']
    readings = st.session_state.setdefault('_review_amount_readings', {})
    if sid not in readings:
        try:
            readings[sid] = read_amounts(source['name'], store.read(sid), st.session_state.get('_review_metadata', {}))
        except ValueError:
            readings[sid] = {'net_total':None,'tax':None,'gross_total':None,'tax_basis':'税抜','tax_rate':.10,'conflicts':[],'evidence':[],'page':1,'read_failed':True}
    reading = readings[sid]
    source['amount_reading'] = reading
    defaults = st.session_state.setdefault('_review_amount_defaults', {})
    names = {'tax_basis':'review_basis_', 'tax_rate':'review_tax_', 'net_total':'review_net_', 'tax':'review_tax_amount_', 'gross_total':'review_gross_'}
    if sid not in defaults:
        defaults[sid] = True
        keep = bool(st.session_state.get('review_source_ok_' + sid))
        for field, prefix in names.items():
            value = reading.get(field)
            if field == 'tax_rate': value = '10%'
            if field == 'tax_basis': value = '税抜'
            if not keep or prefix + sid not in st.session_state:
                st.session_state[prefix + sid] = value
        if not keep or 'review_tax_exists_' + sid not in st.session_state:
            st.session_state['review_tax_exists_' + sid] = reading['tax'] is not None or reading['gross_total'] is not None
    title = source_title(source, st.session_state.get('_review_metadata', {}), len(st.session_state.get('_review_sources', [source])))
    source['project_name'] = title
    vendor = source_vendor(source, st.session_state.get('_review_metadata', {}), detail_rows or [])
    source['vendor_name'] = vendor
    # Do not auto-collapse a confirmed source: that used to move the page.
    with st.expander(f"{index}. {vendor} ｜ {title or '工事名なし（無題）'}", expanded=True):
        st.caption('元ファイル：' + source['name'])
        st.subheader('① 元の見積書',anchor=False)
        st.caption('原本を先に確認してください。右上の拡大ボタンで大きく見られます。')
        preview(st, source, store)
        rows = [r for r in (detail_rows or []) if r.get('原本ID') == sid]
        if detail_rows is not None and not rows:
            source.update(tax_basis='税抜',tax_rate=.10,net_total=None,tax=None,gross_total=None,confirmed=False)
            st.warning('品目を読み取れませんでした。表紙だけでなく、品目・数量・単価が載った見積書を選んでください。')
            st.session_state['review_source_ok_'+sid] = False
            st.checkbox('原本と品目・数量・金額が合っています',key='review_source_ok_'+sid,disabled=True)
            return detail_rows
        if rows:
            rows = _render_items(st, rows, sid)
            changes = {r['行ID']:r for r in rows}
            detail_rows = [changes.get(r['行ID'], r) for r in detail_rows]
        st.subheader('③ 合計金額',anchor=False)
        if st.session_state.get('review_net_' + sid) is None:
            st.info('原本の最後にある合計だけ教えてください。')
            missing = st.number_input('原本の最後にある合計金額（円）', value=None, min_value=0, step=1, key='review_missing_gross_' + sid)
            if missing is not None:
                st.session_state['review_net_' + sid] = net_from_gross(missing)
                st.session_state['review_gross_' + sid] = missing
                st.session_state['review_tax_amount_' + sid] = missing - st.session_state['review_net_' + sid]
                st.session_state['review_tax_exists_' + sid] = True
                st.session_state['review_resolve_totals_' + sid] = True
        with st.expander('原本の合計と違うときだけ直す'):
            st.caption('普段は明細が税抜・消費税10%です。原本の記載に合わせるときだけ変更します。')
            source['tax_basis'] = st.selectbox('明細の金額の扱い', ['税抜', '税込', '非課税', '不明'], key='review_basis_' + sid)
            rate = st.selectbox('消費税率を変更', ['10%', '8%', '0%', '不明'], key='review_tax_' + sid)
            source['tax_rate'] = {'10%':.10,'8%':.08,'0%':0}.get(rate)
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
        # Full-width amounts do not truncate in a narrow Safari window.
        for label, value in zip(['工事金額（税抜）', '消費税', '合計（税込）'], [net, show_tax, show_gross]):
            st.markdown(f'**{label}　{value:,}円**' if value is not None else f'**{label}　未確認**')
        problems=[]
        if reading.get('read_failed'): problems.append('原本を読み取れませんでした。画像として保存した原本をもう一度読み込んでください。')
        if source.get('read_errors'): problems.append('読み取れていないページがあります。この見積をもう一度読み込んでください。')
        try:
            from estimate_review import integer
            actual = sum(integer(r['原価金額']) for r in rows) if rows else None
        except (ValueError, TypeError, OverflowError):
            actual = None
        if rows:
            bad=[]
            for number,row in enumerate(rows,1):
                fields=[]
                for field,label in [('数量','数量'),('原価単価','単価'),('原価金額','金額')]:
                    try:
                        if row.get(field) in ('',None) or not math.isfinite(float(row[field])): fields.append(label)
                    except (ValueError,TypeError): fields.append(label)
                if fields: bad.append(f"{number}番目の{'・'.join(fields)}")
                if not str(row.get('品名') or '').strip(): bad.append(f'{number}番目の品名')
            if any(not r.get('ページ') for r in rows) and source.get('pages',1)>1:
                problems.append('品目の原本ページが分かりません。品目の修正欄でページを指定してください。')
            if bad: problems.append('未確認：'+'、'.join(bad)+'。上の「品目の読み取りが違うときだけ直す」で直せます。')
        from source_rounding import printed_rounding
        rounded=[r for r in rows if printed_rounding(r) is not None]
        source['rounding_rows']=[r['行ID'] for r in rounded]
        if rounded:
            st.info('原本の数量表示の丸めによる計算差がある品目です：'+
                    '、'.join(str(rows.index(r)+1)+'番目' for r in rounded)+
                    '。仕入金額は原本どおりに保持します。お客様向けは、同じ数量で単価と金額の計算が合うよう別に設定します。')
        if reading.get('amount_notes'):
            from source_variants import notes_message
            st.info('明細とは別の金額が書かれています：'+notes_message(reading['amount_notes'])+'。自動では差し引きません。')
            with st.expander('この金額をどう扱うか確認する'):
                st.caption('NET金額が税込か税抜か、値引きがどの金額に対するものかを原本・業者に確認してください。必要なら上の合計・品目を訂正し、根拠を残します。')
                source['amount_notes_resolution']=st.text_input('採用する金額と確認した内容',key='review_amount_notes_'+sid,
                    placeholder='例：業者に確認済み。今回は明細記載の金額を採用する')
            if not str(source.get('amount_notes_resolution') or '').strip():
                problems.append('NET金額・税込後の値引きの扱いが未確認です。上の「この金額をどう扱うか確認する」を開いてください。')
        if net is None: problems.append('原本の合計金額を入力すると確認できます。')
        if actual is not None and net is not None and actual != net:
            problems.append(f'品目の合計と原本が{abs(actual-net):,}円違います。原本の値引き・諸経費や品目を確認し、上の修正欄で直してください。合計だけを合わせる変更はしません。')
            st.caption(f'品目の合計：{actual:,}円 ／ 原本の工事金額：{net:,}円')
        if net is not None and show_tax is not None and source['gross_total'] is not None and net+show_tax != source['gross_total']:
            problems.append('工事金額＋消費税が原本の合計と合いません。「原本の合計と違うときだけ直す」で確認してください。')
        if source['tax'] is not None and calc_tax is not None and abs(source['tax']-calc_tax)>1:
            problems.append(f'消費税が{rate}の計算と合いません。原本の金額を確認してください。')
        if reading['conflicts'] and not st.session_state.get('review_resolve_totals_' + sid):
            problems.append('原本に複数の合計があります。「原本の合計と違うときだけ直す」で最後の合計を確認してください。')
        if source['tax_basis'] not in ('税抜','非課税') or source['tax_rate'] is None:
            problems.append('通常と異なる見積です。原本の税の扱いを確認してください。')
        from estimate_review import digest
        signature = digest([source.get(k) for k in ('tax_basis','tax_rate','net_total','tax','gross_total','amount_notes_resolution')] + [rows])
        last = st.session_state.get('_review_source_signature_' + sid)
        if problems or (last is not None and last != signature):
            st.session_state['review_source_ok_' + sid] = False
        st.session_state['_review_source_signature_' + sid] = signature
        if problems:
            st.warning('まだ確認できない理由\n\n'+'\n\n'.join(dict.fromkeys(problems)))
        else:
            st.caption('原本と上の品目・数量・金額を見比べ、合っていればチェックしてください。')
        source['confirmed'] = st.checkbox('原本と品目・数量・金額が合っています', key='review_source_ok_' + sid, disabled=bool(problems)) and not problems
    return detail_rows
