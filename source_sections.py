"""Reconcile cover-only financial lines and verified aggregate trade headings.

No fee is generated from a residual. All decisions retain the source/page and
printed evidence. Ambiguous aggregates and shared expenses remain for review.
"""
import re
from decimal import Decimal, InvalidOperation


def _integer(value):
    try:
        n=Decimal(str(value).replace(',',''))
        return int(n) if n.is_finite() and n==n.to_integral_value() else None
    except (InvalidOperation,ValueError): return None


def _name(value):
    return re.sub(r'\s|一式','',str(value or ''))


def _item_key(value):
    # "工事" / "工事費" are the same heading, not different ledger entries.
    return re.sub(r'費$', '', re.sub(r'[（）()・、]', '', _name(value)))


def reconcile_printed_totals(summary):
    """Keep a printed discounted net total, repairing labels only with evidence."""
    import unicodedata
    out=dict(summary)
    text=re.sub(r'\s+','',unicodedata.normalize('NFKC',str(summary.get('抽出元テキスト範囲') or ''))).replace(',','')
    gross=[int(v) for v in re.findall(r'税込(?:み)?(?:合計|金額)?[:：]?[¥￥]?(\d+)',text)]
    nets=[int(v) for v in re.findall(r'(?:改小計|税抜合計|工事費計|(?<!小)合計)[:：]?[¥￥]?(\d+)',text)]
    tax=_integer(summary.get('消費税'));before=_integer(summary.get('小計'));discount=_integer(summary.get('端数調整'))
    declared=_integer(summary.get('工事費計'))
    printed_net=None
    if before is not None and discount is not None and discount<0:
        candidate=before+discount
        before_printed=bool(re.search(r'(?:小計|工事費合計|合計)[:：]?[¥￥]?'+str(before)+r'(?!\d)',text))
        if (candidate in nets and before_printed and _printed_discount(summary,discount)
                and (not gross or (tax is not None and candidate+tax in gross))):
            printed_net=candidate
            # A separately printed inclusive total is required to relabel it.
            # The original net figure is never promoted to a gross by arithmetic.
            if declared==candidate or (tax is not None and declared==candidate+tax and declared in nets):
                out['改小計']=candidate
    if not gross or not nets or tax is None:return out
    n=printed_net if printed_net is not None else next((v for v in nets if v+tax in gross),None)
    if n is None:return out
    g=n+tax
    # The explicit inclusive figure and explicit tax must independently agree.
    # When a discount exists, both its printed amount and subtotal must agree too.
    coherent=(g in gross and (discount in (None,0) or (before is not None and before+discount==n
              and str(abs(discount)) in text and bool(re.search('値引|調整|出精',text)))))
    if coherent:
        out.update(改小計=n,工事費計=g)
    return out


def _printed_discount(summary, adjustment):
    """An explicitly negative, labelled source figure; never a residual."""
    import unicodedata
    text=re.sub(r'\s+','',unicodedata.normalize('NFKC',str(summary.get('抽出元テキスト範囲') or ''))).replace(',','')
    return bool(re.search(r'(?:値引き?|端数調整|出精|調整)[^\d\-−▲△]{0,15}[-−▲△]'+str(abs(adjustment))+r'(?!\d)',text))


def reconcile_sections(records, summaries, source_name):
    output=[dict(r) for r in records]
    for row in output:
        amount=_integer(row.get('金額'))
        if (row.get('__source_name')==source_name and amount is not None
                and _integer(row.get('数量'))==1 and str(row.get('単位') or '')=='式'
                and row.get('単価') in (None,'')):
            row['単価']=amount
            row['__normalization']='原本の数量1式・金額を単価へ転記'
    for summary in summaries:
        summary=reconcile_printed_totals(summary)
        if summary.get('__source_name')!=source_name:continue
        page=_integer(summary.get('__page_number'))
        if page is None or page<1:continue
        is_cover=summary.get('__page_role') in ('cover_summary_page','summary_page')
        next_cover=min((_integer(s.get('__page_number')) for s in summaries
                        if s.get('__source_name')==source_name
                        and s.get('__page_role') in ('cover_summary_page','summary_page')
                        and (_integer(s.get('__page_number')) or 0)>page),default=None)
        items=summary.get('工事項目') or []
        if not isinstance(items,list):continue
        cover=[r for r in output if r.get('__source_name')==source_name and _integer(r.get('__page_number'))==page]
        later=[r for r in output if r.get('__source_name')==source_name and (_integer(r.get('__page_number')) or 0)>page
               and (next_cover is None or (_integer(r.get('__page_number')) or 0)<next_cover)]
        def common(name):return bool(re.search('諸経費|運搬|福利|処分|養生|値引|調整|荷揚',name))
        trade_items=[i for i in items if isinstance(i,dict) and not common(_name(i.get('工事項目',i.get('品名'))))]
        for row in cover if is_cover else []:
            name=_name(row.get('品名'));amount=_integer(row.get('金額'))
            item=next((i for i in items if isinstance(i,dict)
                       and (_name(i.get('工事項目',i.get('品名')))==name
                            or _item_key(i.get('工事項目',i.get('品名')))==_item_key(row.get('品名'))
                            or re.sub(r'[（）()・、]','',_name(i.get('工事項目',i.get('品名'))))==
                               re.sub(r'[（）()・、]','',name+_name(row.get('仕様'))))
                       and _integer(i.get('金額'))==amount),None)
            if item is None or amount is None:continue
            details=[r for r in later if _name(r.get('工事種別'))==name]
            values=[_integer(r.get('金額')) for r in details]
            shared=common(name)
            if not shared and len(trade_items)==1:
                all_values=[_integer(r.get('金額')) for r in later]
                if all_values and all(v is not None for v in all_values) and sum(all_values)==amount:
                    # One cover trade can include a hauling/other charge listed
                    # on its detail page under a different classification.
                    # Retain every such detail and remove only its verified sum.
                    details=later;values=all_values
            elif (not shared and not details and all(any(
                    _integer(r.get('金額'))==_integer(i.get('金額'))
                    and _item_key(r.get('品名'))==_item_key(i.get('工事項目',i.get('品名')))
                    for r in cover) for i in trade_items)):
                # A broad AI trade label may differ from the printed heading.
                # A unique, complete physical detail page whose total exactly
                # matches this verified cover heading can prove its aggregate.
                # Never search arbitrary subsets of rows for a matching amount.
                pages={_integer(r.get('__page_number')) for r in later}
                groups=[[r for r in later if _integer(r.get('__page_number'))==p] for p in pages]
                matches=[g for g in groups if g and all(_integer(r.get('金額')) is not None for r in g)
                         and sum(_integer(r.get('金額')) for r in g)==amount]
                same_amount=sum(_integer(i.get('金額'))==amount for i in trade_items)
                if len(matches)==1 and same_amount==1:
                    details=matches[0];values=[_integer(r.get('金額')) for r in details]
            aggregate=(not shared and _integer(row.get('数量'))==1 and str(row.get('単位') or '')=='式'
                       and details and all(v is not None for v in values) and sum(values)==amount)
            if aggregate:
                # The original cover summary is already retained in summaries.
                output.remove(row)
            elif _integer(row.get('数量'))==1 and str(row.get('単位') or '')=='式':
                if row.get('単価') in (None,''):
                    row['単価']=amount
                    row['__normalization']='原本の数量1式・金額を単価へ転記'
                row['__summary_only']=True
        adjustment=next((_integer(summary[k]) for k in ('端数調整','値引き','値引','出精値引き') if summary.get(k) is not None),None)
        before=_integer(summary.get('小計'));after=_integer(summary.get('改小計'))
        discount_evidence=adjustment is not None and (_printed_discount(summary,adjustment) or any(
            _integer(r.get('金額'))==adjustment and _printed_discount(r,adjustment) for r in cover))
        if (adjustment is None or adjustment>=0 or before is None or after is None
                or before+adjustment!=after or not discount_evidence):continue
        discounts=[r for r in output if r.get('__source_name')==source_name and _integer(r.get('__page_number'))==page
                   and re.search('値引|調整|出精',str(r.get('品名') or '')) and _integer(r.get('金額'))==adjustment
                   and r.get('抽出元テキスト範囲')]
        for row in discounts:
            # A monetary discount is a ledger adjustment, not an invented work
            # quantity. Preserve every quantity/unit actually printed by source.
            if row.get('数量') is None and row.get('単位') in (None,''):
                row.update(数量=1,単位='式')
                row['__normalization']='原本の値引き金額を1件の金額調整として保持'
            if _integer(row.get('数量'))==1 and row.get('単位')=='式' and row.get('単価') is None:
                row['単価']=adjustment
            row['__summary_only']=True
        if discounts:continue
        scoped=[r for r in output if r.get('__source_name')==source_name
                and page<=(_integer(r.get('__page_number')) or 0)
                and (next_cover is None or (_integer(r.get('__page_number')) or 0)<next_cover)]
        values=[_integer(r.get('金額')) for r in scoped]
        if not scoped or any(v is None for v in values) or sum(values)!=before:continue
        if any(re.search('値引|調整|出精',str(r.get('品名') or '')) and _integer(r.get('金額'))==adjustment for r in scoped):continue
        vendors=list(dict.fromkeys(str(r.get('見積元') or '') for r in scoped if r.get('見積元')))
        vendor=summary.get('見積元') or (vendors[0] if len(vendors)==1 else None)
        output.append({'品名':'値引き','仕様':'原本に記載された値引き','数量':1,'単位':'式',
                       '単価':adjustment,'金額':adjustment,'見積元':vendor,'工事種別':'諸経費',
                       '__source_name':source_name,'__page_number':page,'__summary_only':True,
                       '抽出元テキスト範囲':summary.get('抽出元テキスト範囲')})
    return output
