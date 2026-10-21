"""Keep negotiated totals and gross-only discounts separate from net details.

A matching detail sum does not prove that a supplier's negotiated price was
included. Never manufacture a net discount by dividing a gross adjustment.
"""
import re
import unicodedata


def find_amount_notes(lines, summaries=(), source_name='', totals=None):
    totals=totals or {}
    notes=[]
    def add(kind,value,page,text):
        entry={'kind':kind,'value':int(value),'page':page,'text':text[:220]}
        if not any(n['kind']==kind and n['value']==entry['value'] for n in notes):notes.append(entry)
    for page,raw in lines:
        text=re.sub(r'\s+','',unicodedata.normalize('NFKC',raw)).replace(',','')
        match=re.search(r'NET(?:金額)?[:：=]?[¥￥]?(\d+)',text,re.I)
        if match and int(match[1])>0 and int(match[1]) not in (totals.get('net_total'),totals.get('gross_total')):
            add('negotiated_total',match[1],page,raw)
        match=re.search(r'(?:合計|税込).*?[▼△▲]([0-9]+)',text)
        if match and int(match[1])>0:add('gross_discount',-int(match[1]),page,raw)
    for item in summaries:
        if item.get('元ファイル')!=source_name:continue
        for note in item.get('金額の要確認箇所') or []:
            if not isinstance(note,dict):continue
            kind=note.get('種類');text=str(note.get('根拠') or '')
            normalized=unicodedata.normalize('NFKC',text).replace(',','')
            try:value=int(str(note.get('金額')).replace(',',''))
            except (ValueError,TypeError):continue
            if str(abs(value)) not in normalized:continue
            page=note.get('ページ') or item.get('ページ')
            if kind=='NET金額' and re.search('NET',normalized,re.I) and value>0:
                if value not in (totals.get('net_total'),totals.get('gross_total')):add('negotiated_total',value,page,text)
            elif kind=='税込後値引き' and value<0 and re.search('値引|▼|△|▲',normalized):
                add('gross_discount',value,page,text)
    return notes


def notes_message(notes):
    items=[]
    for note in notes:
        page=f"{note['page']}ページ：" if note.get('page') else ''
        if note['kind']=='negotiated_total':items.append(f"{page}NET金額 {note['value']:,}円")
        else:items.append(f"{page}税込合計の後の値引き {abs(note['value']):,}円")
    return '／'.join(items)
