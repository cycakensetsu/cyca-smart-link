"""Compare original printed detail subtotals without changing original amounts."""
import re
import unicodedata
from collections import Counter

from source_amounts import money


def _text(value):
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _key(value):
    return re.sub(r"\s+", "", _text(value))


def _page(value):
    n = money(value)
    return n if n is not None and n > 0 else None


_PAGE = r"ページ(?:小計|計|合計)|当ページ(?:小計|計|合計)|内訳(?:小計|合計|計)"
_BASIC = r"小計"
_NOT_DETAIL = r"前頁|前ページ|前頁より|繰越|累計|改小計|値引後|税込|消費税|総合計|税抜(?:小計|合計)"


def _label(value):
    label = _key(value)
    if not label or re.search(_NOT_DETAIL, label):
        return None, ""
    if re.fullmatch(_PAGE, label):
        return "page", ""
    if label == _BASIC:
        return "unknown", ""
    match = re.fullmatch(r"(.{1,50}?)(?:の)?小計", label)
    if match:
        return "section", match[1]
    return None, ""


def _candidate(line):
    # A label and a distinct integral currency value on the same physical line.
    match = re.fullmatch(r"(.{1,75}?)[\s:：]*[¥￥]?([\d,]+)(?:\s*円)?", _text(line))
    if not match:
        return None
    scope, section = _label(match[1])
    amount = money(match[2])
    if not scope or amount is None or amount < 0:
        return None
    return scope, section, amount, _text(match[1])


def _result(page, scope, section, printed, rows, *, verified, reason, evidence, method):
    prices = [r["amount"] for r in rows]
    computed = sum(prices) if prices and None not in prices else None
    difference = computed - printed if computed is not None and printed is not None else None
    status = "matched" if verified and difference == 0 else "variance" if verified and difference is not None else "unverified"
    return dict(status=status, blocking=status != "matched", page=page, scope=scope, section=section,
                printed_subtotal=printed, computed_subtotal=computed, difference=difference, reason=reason,
                printed_evidence=evidence, row_evidence=rows, method=method)


def _ocr_row(row, page):
    return dict(page=page, name=_text(row.get("品名")), amount=money(row.get("金額")),
                evidence=_text(row.get("抽出元テキスト範囲"))[:240])


def _role(summary):
    return summary.get("__page_role") or summary.get("ページ種別") or ""


def _summary_role(summary):
    return _role(summary) in ("cover_summary_page", "summary_page")


def evaluate_subtotals(source_name, records, summaries):
    """Evaluate OCR candidates; one AI reading never verifies a match or mismatch."""
    summaries = [s for s in summaries if s.get("__source_name", s.get("元ファイル")) == source_name]
    records = [r for r in records if r.get("__source_name", r.get("元ファイル")) == source_name]
    output = []
    seen = set()
    for summary in summaries:
        page = _page(summary.get("__page_number", summary.get("ページ")))
        hints = summary.get("印字小計")
        hints = [h for h in hints if isinstance(h, dict)] if isinstance(hints, list) else []
        if _role(summary) == "detail_page" and not hints and money(summary.get("小計")) is not None:
            hints = [{"ページ": page, "印字ラベル": "小計", "金額": summary["小計"],
                      "原文": summary.get("抽出元テキスト範囲", "")}]
        for hint in hints:
            if _key(hint.get("範囲")) in ("見積全体", "表紙", "cover", "document"):
                continue
            where = _page(hint.get("ページ")) or (page if _role(summary) == "detail_page" else None)
            printed_label = _text(hint.get("印字ラベル"))
            scope, section = _label(printed_label)
            if not scope or _summary_role(summary) and where == page and scope == "unknown":
                continue
            stated = _key(hint.get("範囲"))
            if scope == "unknown":
                scope = {"ページ": "page", "区分": "section"}.get(stated, "unknown")
            if scope == "section" and not section:
                section = _key(hint.get("区分名"))
            if scope == "section" and not section:
                scope = "unknown"
            amount = money(hint.get("金額"))
            evidence = _text(hint.get("原文"))[:240]
            sig = (where, scope, section, amount, printed_label)
            if sig in seen:
                continue
            seen.add(sig)
            relevant = [r for r in records if where is not None and _page(r.get("__page_number", r.get("ページ"))) == where
                        and not r.get("__summary_only")]
            if scope == "section":
                relevant = [r for r in relevant if _key(r.get("部位")) == section]
            if scope == "unknown":
                relevant = []
            rows = [_ocr_row(r, where) for r in relevant]
            reason = "原本で小計の範囲と全明細行を照合してください。OCRだけでは確定できません。"
            if not where or scope == "unknown":
                reason = "原本の小計ページまたは対象区分を確定できません。"
            elif amount is None or not evidence or not rows or any(r["amount"] is None for r in rows):
                reason = "印字金額または対象明細の読み取りが不足しています。"
            output.append(_result(where, scope, section, amount, rows, verified=False, reason=reason,
                                  evidence=evidence, method="ocr"))
    return output


def _table_checks(page, page_no):
    from source_table import native_header_columns, native_quantity_unit
    out = []
    for table_no, table in enumerate(page.find_tables().tables, 1):
        active = False
        rows, group = [], []
        section = ""
        for row_no, cells in enumerate(table.extract(), 1):
            cols = native_header_columns(cells)
            if cols:
                if active:  # Another table begins before this one is bounded.
                    rows.append(dict(page=page_no, amount=None, name="", evidence="列見出しが重複"))
                active = True
                q, u, p, a = cols
                continue
            if not active:
                continue
            name = " ".join(_text(c) for c in cells[:q] if _text(c))
            label = _text(cells[p]) if not name else name
            scope, named = _label(label)
            amount = money(cells[a])
            evidence = " / ".join(_text(c) for c in cells if _text(c))[:240]
            if scope:
                selected = group if scope == "section" else rows
                associated = scope != "section" or bool(section and section == named)
                # The exact footer cell is bounded by this native table. A bare
                # subtotal after unlabelled rows is not a named section.
                if scope == "unknown":
                    scope = "page"
                if associated and scope == "section":
                    group = []
                out.append(dict(page=page_no, scope=scope if associated else "unknown", section=named,
                                printed=amount, rows=list(selected) if associated else [], evidence=evidence,
                                associated=associated, label=label))
                continue
            if re.search(_NOT_DETAIL, _key(label)):
                if re.search(r"前頁|前ページ|繰越|累計", _key(label)):
                    suspect = dict(page=page_no, name=label, amount=None, evidence=evidence)
                    rows.append(suspect)
                    group.append(suspect)
                continue
            qty = native_quantity_unit(cells[q], cells[u] if u is not None else None)
            if name and not any(_key(cells[i]) for i in (q, p, a)):
                section = _key(name.strip("()（）【】[]"))
                group = []
                continue
            if amount is None and not any(_key(cells[i]) for i in (q, p, a)):
                continue
            row = dict(page=page_no, table=table_no, row=row_no, name=name,
                       amount=amount if name and qty else None, evidence=evidence)
            rows.append(row)
            group.append(row)
    return out


def read_native_checks(data, lines, summaries):
    """Find independent native subtotal and row evidence; do not repair amounts."""
    import fitz

    roles = {_page(s.get("ページ", s.get("__page_number"))): _role(s) for s in summaries if _role(s)}
    physical = {}
    for page, line in lines:
        if (item := _candidate(line)) and (
            item[0] != "unknown" or roles.get(page) == "detail_page"
            or (page > 1 and roles.get(page) not in ("cover_summary_page", "summary_page"))
        ):
            physical.setdefault(page, []).append((item, line))
    out = []
    with fitz.open(stream=data, filetype="pdf") as document:
        for number, page in enumerate(document, 1):
            if number not in physical:
                # A subtotal may have its own merged cell not reconstructible from
                # single text lines. Scan the table only where the marker exists.
                if not re.search(r"小\s*計|内\s*訳\s*(?:合\s*)?計|ペ\s*ー\s*ジ\s*(?:合\s*)?計", page.get_text()):
                    continue
            try:
                candidates = _table_checks(page, number)
            except (AttributeError, ValueError, RuntimeError):
                candidates = []
            if roles.get(number) in ("cover_summary_page", "summary_page"):
                candidates = [c for c in candidates if _label(c["label"])[0] != "unknown"]
            section_counts = Counter(c["section"] for c in candidates if c["scope"] == "section")
            page_count = sum(c["scope"] == "page" for c in candidates)
            for c in candidates:
                # Distinguish separate explicitly named scopes. Repeated bare
                # subtotals do not identify a unique page-wide range.
                unique = (section_counts[c["section"]] == 1 if c["scope"] == "section" else page_count == 1)
                verified = (c["associated"] and unique and c["printed"] is not None
                            and c["rows"] and all(r["amount"] is not None for r in c["rows"]))
                reason = ("原本の印字と対象の明細行を照合しました。" if verified else
                          "原本の小計は見つかりましたが、対象範囲または明細行の読み取りを確定できません。")
                out.append(_result(number, c["scope"], c["section"], c["printed"], c["rows"], verified=verified,
                                   reason=reason, evidence=c["evidence"], method="native_table"))
            for (scope, section, amount, label), line in physical.get(number, ()):
                if any(c["printed"] == amount and _key(c["label"]) == _key(label) for c in candidates):
                    continue
                out.append(_result(number, scope, section, amount, [], verified=False,
                                   reason="原本の印字は確認できましたが、対象明細の範囲を機械判定できません。",
                                   evidence=line, method="native_text"))
    return out


def combine_checks(native, ocr):
    """Keep uncertainty not covered by independent native evidence."""
    counts = Counter((c["page"], c["printed_subtotal"]) for c in native)
    result = list(native)
    for check in ocr:
        matches = [i for i, c in enumerate(native)
                   if c["page"] == check["page"] and c["printed_subtotal"] == check["printed_subtotal"]
                   and counts[(c["page"], c["printed_subtotal"])] == 1
                   and (check["scope"] == "unknown" or c["scope"] == "unknown" or check["scope"] == c["scope"])
                   and (not check["section"] or check["section"] == c["section"])]
        if len(matches) == 1:
            i = matches[0]
            old = result[i]
            if old["status"] == "unverified" and not old["row_evidence"] and check["row_evidence"]:
                result[i] = {**old, "scope": check["scope"] if old["scope"] == "unknown" else old["scope"],
                             "section": old["section"] or check["section"], "row_evidence": check["row_evidence"],
                             "computed_subtotal": check["computed_subtotal"], "difference": check["difference"]}
            # Native physical readings can also flag extraction differences
            # even when the printed original balances and the cover still ties.
            if (old["status"] == "matched" and check["computed_subtotal"] is not None
                    and old["computed_subtotal"] != check["computed_subtotal"]):
                result[i] = {**check, "printed_evidence": old["printed_evidence"], "reason":
                             "原本の明細合計とOCRの読み取りが異なります。原本で読み取り漏れや誤りを確認してください。"}
        else:
            result.append(check)
    return sorted(result, key=lambda c: (c["page"] is None, c["page"] or 0))


def manual_clean_candidate(check):
    """A visually checked tied OCR result can be confirmed; uncertainty cannot."""
    if (check.get("method") not in ("ocr", "native_text") or check.get("status") != "unverified"
            or check.get("scope") not in ("page", "section")
            or check.get("scope") == "section" and not check.get("section")
            or _page(check.get("page")) is None):
        return False
    printed = money(check.get("printed_subtotal"))
    evidence = _candidate(check.get("printed_evidence"))
    if printed is None or not evidence or evidence[2] != printed:
        return False
    rows = check.get("row_evidence") or []
    amounts = [money(row.get("amount")) for row in rows]
    if not amounts or None in amounts or sum(amounts) != printed:
        return False
    for row, amount in zip(rows, amounts):
        name = _key(row.get("name"))
        line = _text(row.get("evidence")).replace(",", "")
        if (not name or name not in _key(line) or row.get("page") != check["page"]
                or not re.search(r"(?<![\d.,])" + re.escape(str(amount)) + r"(?![\d.,])", line)):
            return False
    return check.get("difference") == 0 and check.get("computed_subtotal") == printed
