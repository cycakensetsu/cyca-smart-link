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
        row.update({"No": idx, "見積元": vendor, "__source_name": source})
        # Native table cells are authoritative. Re-splitting them using OCR
        # specifications can erase floor numbers or vary equal-work grouping.
        if row["品名"].startswith(("同上", "〃")) and idx > 1:
            row["仕様"] = f"対象：{native[idx - 2]['品名']}"
    summaries = [dict(s, 見積元=vendor, **({"工事名称": project} if project else {})) for s in summaries]
    return native, summaries
