"""Use native PDF table cells when a complete detail table can be reconciled.

Scans and unsupported tables keep the OCR path; partial native tables never
replace an entire document's details. No company or project is hard coded.
"""
import re
import unicodedata
from collections import Counter
from markup_rules import line_amount


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def number(value):
    text = compact(value).replace(",", "")
    return float(text) if re.fullmatch(r"-?\d+(?:\.\d+)?", text) else None


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


def reconcile_native_pdf(path, records, summaries):
    if not str(path).lower().endswith(".pdf"):
        return records, summaries
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
