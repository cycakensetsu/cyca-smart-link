"""Read printed totals independently of extracted detail rows.

The user's normal supplier convention is net detail prices and 10% tax.
Never substitute the sum of OCR rows for a missing original total.
"""
from decimal import Decimal, ROUND_HALF_UP
from pdf_worker import isolated_pdf

import re
import unicodedata


def money(value):
    if value is None or isinstance(value, bool):
        return None
    text = unicodedata.normalize('NFKC', str(value)).replace(',', '').strip()
    if not re.fullmatch(r'[¥￥]?\s*-?\d+(?:\.0+)?\s*円?', text):
        return None
    return int(Decimal(re.sub(r'[¥￥円\s]', '', text)))


def net_from_gross(gross, rate=.10, tax=None):
    if tax is not None:
        return gross - tax
    return int((Decimal(gross) / (1 + Decimal(str(rate)))).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _lines(data):
    import fitz
    try:
        with fitz.open(stream=data, filetype='pdf') as document:
            for page_no, page in enumerate(document, 1):
                words = page.get_text('words')
                rows = []
                for word in sorted(words, key=lambda w:(round(w[3], 0), w[0])):
                    if rows and abs(rows[-1][0] - word[3]) <= 2:
                        rows[-1][1].append(word)
                    else:
                        rows.append([word[3], [word]])
                for _, row in rows:
                    yield page_no, ' '.join(w[4] for w in sorted(row, key=lambda w:w[0]))
    except (ValueError, RuntimeError, OSError):
        return


LABELS = {
    'net_total': [(5, r'改小計|値引後小計|値引き後小計|税抜合計|税抜小計|税抜金額|税抜価格'), (1, r'小計')],
    'tax': [(4, r'消費税額|消費税|税額')],
    'gross_total': [(5, r'税込合計|税込総額|税込金額|税込価格'), (3, r'総合計|工事費計|御見積もり金額|御見積り金額|お見積もり金額|お見積り金額|御見積金額|お見積金額|見積金額'), (1, r'合計金額|合計')],
}


@isolated_pdf
def read_amounts(name, data, summaries=None):
    candidates = {key: [] for key in LABELS}
    evidence = []
    lines = list(_lines(data)) if str(name).lower().endswith('.pdf') else []
    for pos, (page, raw) in enumerate(lines):
        text = re.sub(r'\s+', '', unicodedata.normalize('NFKC', raw))
        plain_net=re.fullmatch(r'計[:：=]?[¥￥]?(\d[\d,]*)(?:円)?',text)
        if plain_net:
            candidates['net_total'].append({'value':int(plain_net[1].replace(',','')),'priority':1,
                'page':page,'text':raw[:200],'method':'原本の文字'})
        inclusive=re.search(r'(?:合計金額|お見積り合計金額)[:：=]?[¥￥]?(\d[\d,]*)(?:円)?(?:税込み|税込|\(消費税込\))',text)
        if inclusive:
            candidates['gross_total'].append({'value':int(inclusive[1].replace(',','')),'priority':5,
                'page':page,'text':raw[:200],'method':'原本の文字'})
        for field, patterns in LABELS.items():
            if field == 'gross_total' and '税抜' in text:
                continue
            if field == 'tax' and any(word in text for word in ('税別', '税抜', '別途', '含む')):
                continue
            for priority, pattern in patterns:
                match = re.search(pattern, text)
                if not match:
                    continue
                tail = text[match.end():]
                tail = re.sub(r'\([^)]*%[^)]*\)|\d+(?:\.\d+)?%', '', tail)
                tail = tail.strip(':：=()（）')
                value = money(tail)
                if value is None and not tail and pos + 1 < len(lines) and lines[pos+1][0] == page:
                    value = money(lines[pos+1][1])
                if value is not None and value >= 0:
                    item = {'value':value, 'priority':priority, 'page':page, 'text':raw[:200], 'method':'原本の文字'}
                    candidates[field].append(item)
                    break
    note_indices={i for i,(_,text) in enumerate(lines) if re.search('上記に含|別途|対象外|見積範囲|施工範囲|含まれ|条件|※',text)}
    source_notes=list(dict.fromkeys(lines[i][1] for pos in sorted(note_indices)
        for i in range(max(0,pos-1),min(len(lines),pos+2))))
    # The application's eight-column original contains a validated final net
    # total and separate summary-only fees. Read those cells directly instead
    # of allowing an OCR subtotal or section total to override them.
    if lines:
        try:
            from cyca_reference import read_reference_quote, reference_estimate
            quote=read_reference_quote(data=data)
            reference_estimate(quote,'原本照合')
            candidates['net_total'].append({'value':quote.net_total,'priority':6,
                'page':quote.summary[0]['__page_number'],'text':'原本の小計・値引き・合計を照合','method':'原本の集計表'})
        except (ValueError,RuntimeError,AttributeError,OSError):
            pass
    from document_scopes import printed_scopes
    scoped_totals=printed_scopes((summaries or {}).get('summary_sources',[]),name)
    if scoped_totals:
        combined={'net_total':sum(s['net_total'] for s in scoped_totals),
                'tax':sum(s['tax'] for s in scoped_totals),
                'gross_total':sum(s['gross_calculated'] for s in scoped_totals),
                'tax_basis':'税抜','tax_rate':.10,'conflicts':[],
                'evidence':[{'field':'net_total','value':s['net_total'],'page':s['page'],
                             'method':'原本の個別見積合計','text':s['name']} for s in scoped_totals],
                'quote_sections':scoped_totals,'source_notes':source_notes,'amount_notes':[],'page':scoped_totals[0]['page']}
        from source_variants import find_amount_notes
        combined['amount_notes']=find_amount_notes(lines,(summaries or {}).get('summary_sources',[]),name,combined)
        return combined
    # OCR summaries are scoped to this exact original, never to another vendor.
    source_summaries=[s for s in (summaries or {}).get('summary_sources',[]) if s.get('元ファイル')==name]
    has_cover=any(s.get('ページ種別')=='cover_summary_page' for s in source_summaries)
    for item in source_summaries:
        if has_cover and item.get('ページ種別') not in ('cover_summary_page','summary_page'):
            continue
        if item.get('元ファイル') != name:
            continue
        for field, keys in {'net_total':('改小計','小計'), 'tax':('消費税',), 'gross_total':('工事費計',)}.items():
            for key in keys:
                value = money(item.get(key))
                if value is not None and value >= 0:
                    candidates[field].append({'value':value, 'priority':2 if key == '改小計' else 0, 'page':item.get('ページ') or 1, 'text':key, 'method':'AIの読み取り'})
                    break
    result = {'net_total':None, 'tax':None, 'gross_total':None, 'tax_basis':'税抜', 'tax_rate':.10, 'conflicts':[], 'evidence':evidence, 'source_notes':source_notes}
    chosen = {}
    for field, items in candidates.items():
        if not items:
            continue
        top = max(i['priority'] for i in items)
        best = [i for i in items if i['priority'] == top]
        values = {i['value'] for i in best}
        if len(values) == 1:
            chosen[field] = best[0]
            result[field] = best[0]['value']
            evidence.append({'field':field, **best[0]})
        else:
            result['conflicts'].append(field)
    # A final "合計" after a discount can be the net total while "小計"
    # above it is before discount. Accept only an amount actually printed and
    # consistent with an explicitly inclusive total and explicit tax.
    if (result['gross_total'] is not None and result['tax'] is not None
            and chosen.get('gross_total', {}).get('priority', 0) >= 5
            and chosen.get('tax', {}).get('priority', 0) >= 4
            and chosen.get('net_total', {}).get('priority', -1) < 5):
        expected_net = result['gross_total'] - result['tax']
        printed = [i for i in candidates['gross_total'] + candidates['net_total']
                   if 0 < i['priority'] < 5 and i['value'] == expected_net and i['method']=='原本の文字']
        if printed:
            result['net_total'] = expected_net
            chosen['net_total'] = printed[0]
            result['conflicts'] = [f for f in result['conflicts'] if f != 'net_total']
            evidence.append({'field':'net_total', **printed[0]})
    # Supplier quotation totals without an explicit tax amount are normally net.
    # "見積金額" or "合計" alone is not evidence of an inclusive total.
    generic = chosen.get('gross_total', {})
    coherent_gross = (result['net_total'] is not None and result['gross_total'] == result['net_total'] + int((Decimal(result['net_total']) * Decimal('0.10')).quantize(Decimal(1),rounding=ROUND_HALF_UP)))
    if result['gross_total'] is not None and generic.get('priority', 0) < 5 and result['tax'] is None and not coherent_gross:
        if chosen.get('net_total', {}).get('priority', -1) < generic.get('priority', 0):
            result['net_total'] = result['gross_total']
            chosen['net_total'] = generic
            result['conflicts'] = [f for f in result['conflicts'] if f != 'net_total']
            evidence.append({'field':'net_total', **generic})
        elif result['net_total'] is None and 'net_total' not in result['conflicts']:
            result['net_total'] = result['gross_total']
            evidence.append({'field':'net_total', **generic})
        elif (result['net_total'] != result['gross_total']
                and chosen.get('net_total', {}).get('priority', 0) <= generic.get('priority', 0)):
            result['conflicts'].append('net_total')
        result['gross_total'] = None
    # A generic "total" can be a pre-tax subtotal. Do not mislabel it as gross.
    if result['gross_total'] is not None and result['net_total'] == result['gross_total'] and result['tax']:
        if chosen.get('gross_total', {}).get('priority', 0) < 5:
            result['gross_total'] = None
    if result['net_total'] is None and result['gross_total'] is not None and 'net_total' not in result['conflicts']:
        result['net_total'] = net_from_gross(result['gross_total'], tax=result['tax'])
        result['net_derived'] = True
    from source_variants import find_amount_notes
    result['amount_notes'] = find_amount_notes(lines, (summaries or {}).get('summary_sources', []), name, result)
    result['page'] = next((e['page'] for e in evidence if e['field'] in ('net_total','gross_total')), 1)
    return result
