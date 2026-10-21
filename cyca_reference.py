"""Read an existing eight-column CYCA estimate without OCR or remote calls.

Only full, arithmetically reconciled tables are accepted. Geometric character
ordering preserves decimal points in Numbers PDFs with fragmented text spans.
Unknown layouts return an explicit error rather than a guessed estimate.
"""
from dataclasses import dataclass
from decimal import Decimal
from pdf_worker import isolated_pdf

import re
import unicodedata

from markup_rules import line_amount


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def numeric_cell(page, rect):
    if rect is None:
        return None
    chars = [c for block in page.get_text("rawdict", clip=rect)["blocks"] if "lines" in block
             for line in block["lines"] for span in line["spans"] for c in span["chars"]]
    text = compact("".join(c["c"] for c in sorted(chars, key=lambda c: c["bbox"][0]))).replace(",", "")
    if not text:
        return None
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        return None
    value = Decimal(text)
    return int(value) if value == value.to_integral_value() else float(value)


def text_cell(page, rect):
    if rect is None:
        return ""
    chars = [c for block in page.get_text("rawdict", clip=rect)["blocks"] if "lines" in block
             for line in block["lines"] for span in line["spans"] for c in span["chars"]]
    lines = []
    for char in sorted(chars, key=lambda c: (c["origin"][1], c["bbox"][0])):
        y = char["origin"][1]
        group = next((g for g in lines if abs(g[0]-y)<1.5), None)
        if group is None:
            group = [y, []]
            lines.append(group)
        group[1].append(char)
    return " ".join("".join(c["c"] for c in sorted(g[1],key=lambda c:c["bbox"][0])).strip() for g in lines).strip()


@dataclass
class ReferenceQuote:
    detail: list
    summary: list
    subtotal: int
    discount: int
    net_total: int
    page_count: int
    discount_page: int = 0
    discount_evidence: str = ""


@isolated_pdf
def read_reference_quote(path=None, *, data=None):
    import fitz
    details, summaries = [], []
    subtotal = net_total = None
    discount = 0
    discount_page = 0
    discount_evidence = ""
    section = ""
    with (fitz.open(stream=data,filetype="pdf") if data is not None else fitz.open(path)) as doc:
        for page_no, page in enumerate(doc, 1):
            for table in page.find_tables().tables:
                raw = table.extract()
                if not raw or len(raw[0]) != 8:
                    continue
                headers = [compact(c) for c in raw[0]]
                if headers[1:4] != ["工事品目", "仕様", "数量"] or headers[5:7] != ["単価", "金額"]:
                    continue
                is_summary = any(compact(r[1]) == "小計" for r in raw[1:])
                if is_summary and summaries:
                    raise ValueError("複数の集計表があり、採用する見積を自動確定できません。")
                for index, cells in enumerate(raw[1:], 1):
                    rects = table.rows[index].cells
                    cells = [text_cell(page, rect) for rect in rects]
                    name = re.sub(r"\s+", " ", str(cells[1] or "")).strip()
                    if not name:
                        if is_summary and summaries and not compact(cells[0]) and compact(cells[2]):
                            if compact(cells[2]) not in compact(summaries[-1]["仕様"]):
                                summaries[-1]["仕様"] += " " + str(cells[2]).strip()
                        continue
                    quantity = numeric_cell(page, rects[3])
                    price = numeric_cell(page, rects[5])
                    amount = numeric_cell(page, rects[6])
                    label = compact(name)
                    if is_summary and label in ("小計", "合計", "値引き"):
                        if amount is None or int(amount) != amount:
                            raise ValueError("集計表の金額を確定できません。")
                        if label == "小計":
                            subtotal = int(amount)
                        elif label == "合計":
                            net_total = int(amount)
                        else:
                            sign = compact(cells[5])
                            if amount > 0 and sign not in ("-", "−", "△", "▲"):
                                raise ValueError("値引きの符号を確定できません。")
                            discount = -abs(int(amount))
                            discount_page = page_no
                            discount_evidence = " / ".join(str(c or "") for c in cells)
                        continue
                    if name.startswith("【") and "】" in name:
                        section = name.strip("【】 ")
                        continue
                    if quantity is None and price is None and amount is None:
                        continue
                    no = numeric_cell(page, rects[0])
                    if no is None:
                        continue  # Footers, tax notes and page totals are not items.
                    if quantity is None or price is None or amount is None or quantity <= 0:
                        raise ValueError("数量・単価・金額が確定できない原本行があります。")
                    if line_amount(quantity, price) != amount:
                        raise ValueError("原本に数量×単価と金額が一致しない行があります。")
                    item = {"No": int(no), "品名": name, "仕様": re.sub(r"\s+", " ", str(cells[2] or "")).strip(),
                            "数量": quantity, "単位": re.sub(r"\s+", "", str(cells[4] or "")), "単価": price, "金額": int(amount),
                            "工事種別": section, "__page_number": page_no, "備考": str(cells[7] or '').strip(),
                            "抽出元テキスト範囲": " / ".join(str(c or "") for c in cells)}
                    (summaries if is_summary else details).append(item)
        page_count = len(doc)
    if not summaries or not details or subtotal is None or net_total is None:
        raise ValueError("全明細と集計表を確認できる彩架建設の8列表ではありません。")
    if sum(r["金額"] for r in summaries) != subtotal or subtotal + discount != net_total:
        raise ValueError("集計表の明細・小計・値引き・合計が一致しません。")
    numbers = [r["No"] for r in details]
    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError("原本の明細連番に欠落・重複があります。")
    return ReferenceQuote(details, summaries, subtotal, discount, net_total, page_count, discount_page, discount_evidence)


def reference_estimate(quote, project, group_map=None):
    """Reproduce a reference for verification, with an explicit section mapping.

    This adapter makes no pricing decision and does not approve a business quote.
    Category label AND amount must match the source's summary. Customer identity
    is deliberately omitted from reference outputs.
    """
    from template_fill_test_v2 import Estimate, Category, LineItem
    group_map = dict(group_map or {})
    categories = []
    for row in quote.detail:
        section = row['工事種別']
        name = group_map.get(section, section)
        category = next((c for c in categories if c.name == name), None)
        if category is None:
            category = Category(name, [])
            categories.append(category)
        category.items.append(LineItem(row['品名'], row['仕様'], row['数量'], row['単位'], row['単価'], row['金額'],
                                       row.get('備考',''), section, row['__page_number']))
    remaining = list(quote.summary)
    for category in categories:
        matches = [r for r in remaining if r['金額'] == category.subtotal
                   and compact(r['品名']).removesuffix('一式') == compact(category.name)]
        if len(matches) != 1:
            raise ValueError('工種名・明細合計と原本の一式表を一意に照合できません。')
        remaining.remove(matches[0])
    for r in remaining:
        item = LineItem(r['品名'], r['仕様'], r['数量'], r['単位'], r['単価'], r['金額'], r.get('備考',''))
        categories.append(Category(item.name, [item], summary_only=True))
    estimate = Estimate('', '確認用（宛名非表示）', project, '2026/10/20', 'お打ち合わせの上',
                        categories, quote.discount, .10, reference_layout=True)
    if estimate.subtotal != quote.subtotal or estimate.net_total != quote.net_total:
        raise ValueError('原本の小計・値引き後金額を再現できません。')
    return estimate
