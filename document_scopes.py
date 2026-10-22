"""Keep independently printed quotes in a multi-quote PDF distinct."""
from decimal import Decimal,InvalidOperation


def amount(value):
    try:
        n=Decimal(str(value).replace(',',''))
        return int(n) if n.is_finite() and n==n.to_integral_value() else None
    except (InvalidOperation,ValueError):return None


def printed_scopes(summaries,name):
    """Only distinct cover pages with coherent printed totals qualify.

    No detail sums or tax reverse-calculation fill a missing original amount.
    Ambiguous/missing covers return no aggregate and remain reviewable.
    """
    covers=[s for s in summaries if s.get('元ファイル',s.get('__source_name'))==name
        and (s.get('ページ種別',s.get('__page_role'))=='cover_summary_page'
             or (s.get('ページ種別',s.get('__page_role'))=='summary_page'
                 and s.get('見積区分確認',s.get('__quote_scope_verified'))))]
    by_page={}
    for s in covers:
        page=amount(s.get('ページ',s.get('__page_number')))
        net=amount(s.get('改小計'));tax=amount(s.get('消費税'));gross=amount(s.get('工事費計'))
        if page is None or net is None or net<0 or tax is None or tax<0:return []
        # Some printed forms call their net line 工事費計. Retain a calculated
        # gross separately; never pretend it was explicitly printed there.
        if gross==net:gross=None
        if gross is not None and gross!=net+tax:return []
        if abs(Decimal(tax)-Decimal(net)*Decimal('.1'))>1:return []
        item={'page':page,'name':str(s.get('工事名称') or s.get('工事名') or ''),
              'net_total':net,'tax':tax,'gross_total':gross,'gross_calculated':net+tax}
        if page in by_page and by_page[page]!=item:return []
        by_page[page]=item
    return [by_page[p] for p in sorted(by_page)] if len(by_page)>1 else []


def retain_scope_context(records,summaries,name):
    scopes=printed_scopes(summaries,name)
    if not scopes:return records
    out=[]
    for row in records:
        item=dict(row);page=amount(item.get('__page_number'))
        candidates=[s for s in scopes if page is not None and s['page']<=page]
        if item.get('__source_name')==name and candidates:
            title=candidates[-1]['name']
            if title:
                item['__quote_section']=title
                part=str(item.get('部位') or '')
                item['部位']=part if part==title or part.startswith(title+' ｜ ') else ' ｜ '.join(v for v in (title,part) if v)
        out.append(item)
    return out


def verify_scope_boundaries(records,summaries,name):
    """Generic summary labels qualify only with separate, complete scoped rows."""
    candidates=[s for s in summaries if s.get('__source_name')==name
        and s.get('__page_role') in ('cover_summary_page','summary_page')
        and amount(s.get('__page_number')) is not None]
    by_page={}
    for s in candidates:
        page=amount(s['__page_number'])
        if page in by_page:return summaries
        by_page[page]=s
    pages=sorted(by_page)
    if len(pages)<2:return summaries
    for i,page in enumerate(pages):
        end=pages[i+1] if i+1<len(pages) else float('inf')
        scoped=[r for r in records if r.get('__source_name')==name
            and amount(r.get('__page_number')) is not None and page<=amount(r['__page_number'])<end]
        values=[amount(r.get('金額')) for r in scoped]
        net=amount(by_page[page].get('改小計'))
        if not values or net is None or any(v is None for v in values) or sum(values)!=net:return summaries
    return [dict(s,__quote_scope_verified=True) if s in candidates else s for s in summaries]
