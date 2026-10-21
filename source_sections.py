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


def reconcile_printed_totals(summary):
    """Repair a net/gross label swap only when printed figures prove it."""
    import unicodedata
    out=dict(summary)
    text=re.sub(r'\s+','',unicodedata.normalize('NFKC',str(summary.get('抽出元テキスト範囲') or ''))).replace(',','')
    gross=re.search(r'税込(?:み)?(?:合計|金額)?[:：]?[¥￥]?(\d+)',text)
    net=re.search(r'(?:改小計|税抜合計|(?<!小)合計)[:：]?[¥￥]?(\d+)',text)
    tax=_integer(summary.get('消費税'));before=_integer(summary.get('小計'));discount=_integer(summary.get('端数調整'))
    if not gross or not net or tax is None:return out
    g,n=int(gross[1]),int(net[1])
    # The explicit inclusive figure and explicit tax must independently agree.
    # When a discount exists, both its printed amount and subtotal must agree too.
    coherent=(g==n+tax and (discount in (None,0) or (before is not None and before+discount==n
              and str(abs(discount)) in text and bool(re.search('値引|調整|出精',text)))))
    if coherent:
        out.update(改小計=n,工事費計=g)
    return out


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
        if summary.get('__source_name')!=source_name or summary.get('__page_role') not in ('cover_summary_page','summary_page'):continue
        page=_integer(summary.get('__page_number'))
        if page is None:continue
        items=summary.get('工事項目') or []
        if not isinstance(items,list):continue
        cover=[r for r in output if r.get('__source_name')==source_name and _integer(r.get('__page_number'))==page]
        later=[r for r in output if r.get('__source_name')==source_name and (_integer(r.get('__page_number')) or 0)>page]
        def common(name):return bool(re.search('諸経費|運搬|福利|処分|養生|値引|調整|荷揚',name))
        trade_items=[i for i in items if isinstance(i,dict) and not common(_name(i.get('工事項目',i.get('品名'))))]
        later_cover=any(s.get('__source_name')==source_name and (_integer(s.get('__page_number')) or 0)>page
                        and s.get('__page_role') in ('cover_summary_page','summary_page') for s in summaries)
        for row in cover:
            name=_name(row.get('品名'));amount=_integer(row.get('金額'))
            item=next((i for i in items if isinstance(i,dict)
                       and (_name(i.get('工事項目',i.get('品名')))==name
                            or re.sub(r'[（）()・、]','',_name(i.get('工事項目',i.get('品名'))))==
                               re.sub(r'[（）()・、]','',name+_name(row.get('仕様'))))
                       and _integer(i.get('金額'))==amount),None)
            if item is None or amount is None:continue
            details=[r for r in later if _name(r.get('工事種別'))==name]
            values=[_integer(r.get('金額')) for r in details]
            shared=common(name)
            if not shared and len(trade_items)==1 and not later_cover:
                all_values=[_integer(r.get('金額')) for r in later]
                if all_values and all(v is not None for v in all_values) and sum(all_values)==amount:
                    # One cover trade can include a hauling/other charge listed
                    # on its detail page under a different classification.
                    # Retain every such detail and remove only its verified sum.
                    details=later;values=all_values
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
        if adjustment is None or adjustment>=0 or before is None or after is None or before+adjustment!=after:continue
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
    return output
