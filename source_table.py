"""Use native PDF table cells when a complete detail table can be reconciled.

Scans and unsupported tables keep the OCR path; partial native tables never
replace an entire document's details. No company or project is hard coded.
"""
from pdf_worker import isolated_pdf

import re
import unicodedata
from collections import Counter
from markup_rules import line_amount


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def number(value):
    text = compact(value).replace(",", "")
    return float(text) if re.fullmatch(r"-?\d+(?:\.\d+)?", text) else None


@isolated_pdf
def attach_native_dimensions(path, records):
    """Retain printed W/H cells only beside an exact, unique priced row.

    Dimension columns must be explicitly labelled 寸法(mm), W, H. No OCR
    geometry or inferred size replaces a number, description or existing spec.
    """
    import fitz
    output=[dict(r) for r in records]
    if len({r.get('__source_name') for r in output})!=1:
        return output
    with fitz.open(path) as document:
        for page_number,page in enumerate(document,1):
            words=page.get_text('words')
            center=lambda w:((w[0]+w[2])/2,(w[1]+w[3])/2)
            headers=[]
            for w in words:
                if compact(w[4])!='W':continue
                for h in words:
                    if (compact(h[4])=='H' and abs(center(w)[1]-center(h)[1])<3
                            and 10<center(h)[0]-center(w)[0]<80
                            and any('寸法' in compact(t[4]) and 'mm' in compact(t[4]).lower()
                                and 0<w[1]-t[3]<25 and t[0]-10<w[0]<h[2]<t[2]+10 for t in words)):
                        headers.append((center(w)[0],center(h)[0],center(w)[1]))
            if len(headers)!=1:continue
            wx,hx,top=headers[0]
            relevant=[(i,r) for i,r in enumerate(output) if number(r.get('__page_number'))==page_number]
            anchors=[]
            ys=sorted({round(center(w)[1],1) for w in words if center(w)[0]>hx+10 and number(w[4]) is not None and center(w)[1]>top})
            for y in ys:
                if anchors and abs(y-anchors[-1][0])<3:continue
                cells=sorted((w for w in words if abs(center(w)[1]-y)<3 and center(w)[0]>hx+10 and number(w[4]) is not None),key=lambda w:w[0])
                values=[number(w[4]) for w in cells]
                matches=[i for i,r in relevant if len(values)>=3 and values[:3]==[number(r.get(k)) for k in ('数量','単価','金額')]]
                if len(matches)==1:anchors.append((y,matches[0]))
            found={}
            for w in words:
                width=number(w[4]);y=center(w)[1]
                if (width is None or not width.is_integer() or not 0<width<100000
                        or abs(center(w)[0]-wx)>7 or y<=top):continue
                heights=[h for h in words if abs(center(h)[1]-y)<3 and abs(center(h)[0]-hx)<7
                         and number(h[4]) is not None and 0<number(h[4])<100000 and number(h[4]).is_integer()]
                preceding=[a for a in anchors if a[0]<=y+3]
                if len(heights)!=1 or not preceding:continue
                anchor,index=preceding[-1]
                nearby=''.join(t[4] for t in words if t[2]<wx-8 and abs(center(t)[1]-y)<5)
                label='障子部 ' if '障子' in nearby else ('' if abs(anchor-y)<3 else '同じ製品欄 ')
                found.setdefault(index,[]).append(f'{label}W{int(width)}×H{int(number(heights[0][4]))}mm')
            for index,dimensions in found.items():
                dimensions=list(dict.fromkeys(dimensions))
                original=str(output[index].get('仕様') or '')
                additions=[d for d in dimensions if compact(d) not in compact(original)]
                if additions:
                    output[index]['仕様']=original+(' ／ ' if original else '')+f'原本{page_number}ページの寸法：'+'、'.join(additions)
                    output[index]['__native_dimension_evidence']={'page':page_number,'dimensions':dimensions,'basis':'labelled W/H columns and unique quantity/unit-price/amount row'}
    return output


@isolated_pdf
def read_native_tables(path):
    import fitz
    rows, subtotals, projects, issuers = [], [], set(), set()
    with fitz.open(path) as doc:
        for page_no, page in enumerate(doc, 1):
            text = page.get_text(sort=True)
            issuer_match = re.search(r"((?:株式会社|有限会社)[^\n]+)\n[^\n]*代表取締役", text)
            if issuer_match:
                issuers.add(re.sub(r"\s+", " ", issuer_match.group(1)).strip())
            match = re.search(r"工事名(?:称)?\s*([^\n]+?)(?:工期|\n)", text)
            if match:
                projects.add(match.group(1).strip())
            for table in page.find_tables().tables:
                columns = None
                section = ""
                for cells in table.extract():
                    headers = [compact(c) for c in cells]
                    if "数量" in headers and "単価" in headers and "金額" in headers:
                        columns = (headers.index("数量"), headers.index("単価"), headers.index("金額"))
                        continue
                    if columns is None:
                        continue
                    qcol, pcol, acol = columns
                    name = " ".join(str(c).replace("\n", " ") for c in cells[:qcol] if c).strip()
                    if not name:
                        continue
                    if compact(name) in ("小計", "税抜合計", "税抜小計"):
                        subtotal = number(cells[acol])
                        if subtotal is not None:
                            subtotals.append(int(subtotal))
                        continue
                    qtytext = compact(cells[qcol])
                    match = re.fullmatch(r"(\d+(?:\.\d+)?)(㎡|m2|m²|m|式|枚|ヶ所|箇所|個|本|台|基|人工|坪|kg|t)", qtytext)
                    if not match:
                        if name.startswith(("（", "(")):
                            section = name.strip("（）() ")
                        continue
                    qty, unit = float(match.group(1)), match.group(2)
                    unit = "㎡" if unit in ("m2", "m²") else unit
                    price, amount = number(cells[pcol]), number(cells[acol])
                    if amount is None or qty <= 0:
                        return [], [], ""
                    if price is None and qty == 1:
                        price = amount
                    if price is None or line_amount(qty, price) != round(amount):
                        return [], [], ""
                    remark = " ".join(str(c) for c in cells[acol + 1:] if c and number(c) is None)
                    rows.append({"品名": name, "仕様": "", "工事種別": section, "数量": qty,
                                 "単位": unit, "単価": price, "金額": amount, "備考": remark,
                                 "抽出元テキスト範囲": " / ".join(str(c) for c in cells if c),
                                 "__page_number": page_no})
    if len(issuers) == 1:
        issuer = next(iter(issuers))
        for row in rows:
            row["__native_vendor"] = issuer
    return rows, subtotals, next(iter(projects)) if len(projects) == 1 else ""


def reconcile_cyca_reference(path, records, summaries):
    """Use a fully validated original CYCA table, including summary-only rows.

    No residual amount becomes a new fee. Every added row must exist in the
    original eight-column table, and its full table must reconcile beforehand.
    """
    from cyca_reference import read_reference_quote, reference_estimate
    vendors = {r.get('見積元') for r in records if r.get('見積元')}
    if len(vendors) != 1:
        return None
    try:
        quote = read_reference_quote(path)
        estimate = reference_estimate(quote, '原本照合')
    except (ValueError, RuntimeError, AttributeError, OSError):
        return None
    source = records[0].get('__source_name', '')
    vendor = next(iter(vendors))
    native = [dict(row) for row in quote.detail]
    for category in estimate.categories:
        if not category.summary_only:
            continue
        for item in category.items:
            found = [row for row in quote.summary if row['品名'] == item.name and row['金額'] == item.amount]
            if len(found) != 1:
                return None
            native.append(dict(found[0], 工事種別=category.name, __summary_only=True))
    if quote.discount:
        if not quote.discount_page or not quote.discount_evidence:
            return None
        native.append({'品名':'値引き','仕様':'原本に記載された値引き','数量':1,'単位':'式',
                       '単価':quote.discount,'金額':quote.discount,'工事種別':'値引き',
                       '__page_number':quote.discount_page,'__summary_only':True,
                       '抽出元テキスト範囲':quote.discount_evidence})
    if sum(row['金額'] for row in native) != quote.net_total:
        return None
    for index,row in enumerate(native,1):
        row.update({'No':index,'見積元':vendor,'__source_name':source})
    # Preserve original summary metadata; details now include its printed fees.
    return native, summaries


def reconcile_native_pdf(path, records, summaries):
    if not str(path).lower().endswith(".pdf"):
        return records, summaries
    reference = reconcile_cyca_reference(path, records, summaries)
    if reference is not None:
        return reference
    try:
        native, subtotals, project = read_native_tables(path)
    except (ValueError, RuntimeError, AttributeError):
        return records, summaries  # Unsupported native table: keep the OCR path.
    if not native or not records:
        return records, summaries
    vendors = {r.get("見積元") for r in records if r.get("見積元")}
    if len(vendors) != 1:
        return records, summaries
    native_total = round(sum(r["金額"] for r in native))
    ai_amounts = [number(r.get("金額", r.get("原価金額"))) for r in records]
    complete = native_total in subtotals or (
        len(native) == len(records) and None not in ai_amounts
        and Counter(r["金額"] for r in native) == Counter(ai_amounts)
    )
    if not complete:
        return records, summaries
    vendor = native[0].get("__native_vendor") or next(iter(vendors))
    source = records[0].get("__source_name", "")
    for idx, row in enumerate(native, 1):
        matches=[r for r in records if number(r.get('数量'))==row['数量']
                 and number(r.get('単価'))==row['単価'] and number(r.get('金額'))==row['金額']
                 and compact(r.get('品名'))==compact(row['品名'])]
        if len(matches)==1:
            for field in ('工事種別','部位'):
                if not row.get(field) and matches[0].get(field):row[field]=matches[0][field]
        # A numeric native table may omit an OCR-readable remarks column.
        # Restore only a unique same-original, same-physical-page row's words;
        # printed native remarks remain authoritative and prices never change.
        page = number(row.get('__page_number'))
        context_matches = [r for r in matches if r.get('__source_name') == source
                           and page is not None and page > 0
                           and number(r.get('__page_number')) == page]
        if len(context_matches) == 1 and not row.get('備考') and context_matches[0].get('備考'):
            row['備考'] = context_matches[0]['備考']
        row.update({"No": idx, "見積元": vendor, "__source_name": source})
        # Native table cells are authoritative. Re-splitting them using OCR
        # specifications can erase floor numbers or vary equal-work grouping.
        if row["品名"].startswith(("同上", "〃")) and idx > 1:
            row["仕様"] = f"対象：{native[idx - 2]['品名']}"
    summaries = [dict(s, 見積元=vendor, **({"工事名称": project} if project else {})) for s in summaries]
    return native, summaries
