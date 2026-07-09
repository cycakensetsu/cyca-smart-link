import logging
import math
import re
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd


LOGGER = logging.getLogger(__name__)


OUTPUT_COLUMNS = [
    "No",
    "見積元",
    "品名",
    "数量",
    "単位",
    "原価単価",
    "原価金額",
    "上乗せ額",
    "見積単価",
    "見積金額",
    "備考",
]

QUOTE_SHEET_NAME = "見積書"
WORK_SUMMARY_SHEET_NAME = "工事別まとめ"
DETAIL_SHEET_NAME = "明細データ"

QUOTE_SUMMARY_COLUMNS = [
    "No",
    "工事項目",
    "仕様",
    "数量",
    "単位",
    "単価",
    "金額",
    "備考",
]

NUMBERS_OUTPUT_COLUMNS = [
    "No",
    "工事品目",
    "仕様",
    "数量",
    "単位",
    "単価",
    "金額",
    "備考",
]

SUMMARY_ITEM_KEYS = ["工事費内訳", "工事内容のまとめ", "工事項目単位の集計", "summary_items", "work_items", "items", "工事項目"]
EXPENSE_KEYWORDS = ["諸経費", "福利", "厚生", "法定福利", "運搬", "処分", "荷揚げ", "現場管理"]

SUMMARY_KEYWORDS = [
    "小計",
    "合計",
    "消費税",
    "税込",
    "税抜",
    "見積額",
    "見積金額",
    "総合計",
    "内訳合計",
    "改小計",
]

UNKNOWN_VENDOR_NAMES = {"", "不明", "不明な会社", "unknown", "none", "null", "未取得"}
UNKNOWN_DUPLICATE_REL_TOLERANCE = 0.08
UNKNOWN_DUPLICATE_MIN_TOLERANCE = 10000


def normalize_text(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_money(value) -> Optional[float]:
    text = normalize_text(value)
    if not text:
        return None
    text = text.translate(str.maketrans("０１２３４５６７８９．－−▲△", "0123456789.--__"))
    negative = False
    if "(" in text and ")" in text:
        negative = True
    if "▲" in str(value) or "△" in str(value):
        negative = True
    text = text.replace("_", "")
    text = re.sub(r"[¥￥円,税込税抜]", "", text)
    text = text.replace("−", "-")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    number = float(match.group(0))
    if negative and number > 0:
        number *= -1
    return number


def split_quantity_unit(quantity_value, unit_value: str = "") -> Tuple[Optional[float], str]:
    raw_qty = normalize_text(quantity_value)
    raw_unit = normalize_text(unit_value)
    if not raw_qty:
        return None, raw_unit

    normalized = raw_qty.translate(str.maketrans("０１２３４５６７８９．－，", "0123456789.-,"))
    match = re.match(r"^\s*(-?\d[\d,]*(?:\.\d+)?)\s*(.*)$", normalized)
    if not match:
        return parse_money(raw_qty), raw_unit

    qty = float(match.group(1).replace(",", ""))
    attached_unit = normalize_text(match.group(2))
    unit = raw_unit or attached_unit
    return qty, unit


def _first_present(record: Dict, keys: Iterable[str], default=""):
    for key in keys:
        if key in record and record.get(key) not in (None, ""):
            return record.get(key)
    return default


def _is_unknown_vendor(vendor: str) -> bool:
    return normalize_text(vendor).lower() in UNKNOWN_VENDOR_NAMES


def _record_vendor(record: Dict) -> str:
    return normalize_text(_first_present(record, ["見積元", "見積作成会社", "会社名", "発行会社", "vendor"], "不明")) or "不明"


def _record_source_pdf(record: Dict) -> str:
    return normalize_text(_first_present(record, ["__source_name", "source_pdf", "元ファイル", "source"], ""))


def _record_text_range(record: Dict) -> str:
    text_range = normalize_text(_first_present(
        record,
        ["抽出元テキスト範囲", "text_range", "source_text_range", "抽出範囲", "source_snippet"],
        "",
    ))
    if text_range:
        return text_range[:180]
    page = _first_present(record, ["__page_number", "ページ", "page"], "")
    name = normalize_text(_first_present(record, ["品名", "項目名", "工事品目", "名称・内容", "名称", "商品名・工事名"], ""))
    amount = _first_present(record, ["原価金額", "金額", "amount"], "")
    parts = []
    if page not in (None, ""):
        parts.append(f"page={page}")
    if name:
        parts.append(f"item={name[:80]}")
    if amount not in (None, ""):
        parts.append(f"amount={amount}")
    return " / ".join(parts)


def _records_amount_total(records: List[Dict]) -> int:
    total = 0
    for record in records:
        name = normalize_text(_first_present(record, ["品名", "項目名", "工事品目", "名称・内容", "名称", "商品名・工事名"]))
        if name and _is_summary_name(name):
            continue
        amount = parse_money(_first_present(record, ["原価金額", "金額", "amount"]))
        if amount is not None:
            total += int(round(amount))
    return total


def _records_declared_amount(records: List[Dict], keys: Iterable[str]) -> Optional[int]:
    for record in records:
        value = parse_money(_first_present(record, keys))
        if value is not None:
            return int(round(value))
    return None


def _amounts_are_near(left: Optional[int], right: Optional[int]) -> bool:
    if left is None or right is None:
        return False
    if left == right:
        return True
    larger = max(abs(left), abs(right))
    if larger <= 0:
        return False
    tolerance = max(UNKNOWN_DUPLICATE_MIN_TOLERANCE, int(round(larger * UNKNOWN_DUPLICATE_REL_TOLERANCE)))
    return abs(left - right) <= tolerance


def deduplicate_estimate_records(records: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """同一PDF内で会社名付き見積と近い金額の「不明」見積を二重集計しない。"""
    groups: Dict[Tuple[str, str], List[Dict]] = {}
    for record in records or []:
        if not isinstance(record, dict):
            continue
        source_pdf = _record_source_pdf(record)
        company_name = _record_vendor(record)
        groups.setdefault((source_pdf, company_name), []).append(record)

    group_infos: List[Dict] = []
    for (source_pdf, company_name), group_records in groups.items():
        subtotal = _records_declared_amount(group_records, ["PDF小計", "見積税抜合計", "小計", "税抜合計"])
        detail_total = _records_amount_total(group_records)
        subtotal_ex_tax = subtotal if subtotal is not None else detail_total
        total_in_tax = _records_declared_amount(group_records, ["税込合計", "合計", "総合計"])
        text_ranges = []
        for record in group_records[:3]:
            text_range = _record_text_range(record)
            if text_range:
                text_ranges.append(text_range)
        group_infos.append({
            "source_pdf名": source_pdf,
            "company_name": company_name,
            "subtotal_ex_tax": subtotal_ex_tax,
            "total_in_tax": total_in_tax,
            "抽出元テキスト範囲": " | ".join(text_ranges),
            "records": group_records,
            "excluded": False,
            "duplicate_reason": "",
            "duplicate_of": "",
        })

    named_by_pdf: Dict[str, List[Dict]] = {}
    for info in group_infos:
        if not _is_unknown_vendor(info["company_name"]):
            named_by_pdf.setdefault(info["source_pdf名"], []).append(info)

    excluded_ids = set()
    for info in group_infos:
        if not _is_unknown_vendor(info["company_name"]):
            continue
        duplicate_of = None
        for named in named_by_pdf.get(info["source_pdf名"], []):
            if (
                _amounts_are_near(info["subtotal_ex_tax"], named["subtotal_ex_tax"])
                or _amounts_are_near(info["subtotal_ex_tax"], named["total_in_tax"])
                or _amounts_are_near(info["total_in_tax"], named["subtotal_ex_tax"])
                or _amounts_are_near(info["total_in_tax"], named["total_in_tax"])
            ):
                duplicate_of = named
                break
        if duplicate_of:
            info["excluded"] = True
            info["duplicate_of"] = duplicate_of["company_name"]
            info["duplicate_reason"] = "same_pdf_unknown_near_named_amount"
            for record in info["records"]:
                excluded_ids.add(id(record))

    filtered = [record for record in records or [] if id(record) not in excluded_ids]
    debug_rows = []
    for info in group_infos:
        row = {
            "source_pdf名": info["source_pdf名"],
            "company_name": info["company_name"],
            "subtotal_ex_tax": info["subtotal_ex_tax"],
            "total_in_tax": info["total_in_tax"],
            "抽出元テキスト範囲": info["抽出元テキスト範囲"],
            "重複判定で除外": info["excluded"],
            "duplicate_of": info["duplicate_of"],
            "duplicate_reason": info["duplicate_reason"],
        }
        debug_rows.append(row)
        LOGGER.info(
            "estimate_dedupe source_pdf=%s company=%s subtotal_ex_tax=%s total_in_tax=%s text_range=%s excluded=%s duplicate_of=%s reason=%s",
            row["source_pdf名"],
            row["company_name"],
            row["subtotal_ex_tax"],
            row["total_in_tax"],
            row["抽出元テキスト範囲"],
            row["重複判定で除外"],
            row["duplicate_of"],
            row["duplicate_reason"],
        )
    return filtered, debug_rows


def split_extraction_payload(payload, source_name: str = "", page_number: Optional[int] = None) -> Tuple[List[Dict], List[Dict]]:
    """AI応答を summary_data と detail_data に分離する。旧配列形式も受け入れる。"""
    if isinstance(payload, list):
        return [], payload
    if not isinstance(payload, dict):
        return [], []

    summary_sources: List[Dict] = []
    detail_records: List[Dict] = []

    page_role = normalize_text(_first_present(payload, ["page_role", "role", "ページ種別"]))
    summary_data = _first_present(payload, ["summary_data", "summary", "見積表紙", "一式表", "工事費内訳"], None)
    if isinstance(summary_data, dict):
        summary_data = dict(summary_data)
        if source_name:
            summary_data.setdefault("__source_name", source_name)
        if page_number is not None:
            summary_data.setdefault("__page_number", page_number)
        summary_sources.append(summary_data)
    elif page_role in ("cover_summary_page", "summary_page") and payload:
        summary_source = dict(payload)
        if source_name:
            summary_source.setdefault("__source_name", source_name)
        if page_number is not None:
            summary_source.setdefault("__page_number", page_number)
        summary_sources.append(summary_source)

    for key in ("detail_data", "details", "明細データ", "工事明細", "records"):
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                record = dict(item)
                if source_name:
                    record.setdefault("__source_name", source_name)
                if page_number is not None:
                    record.setdefault("__page_number", page_number)
                detail_records.append(record)

    return summary_sources, detail_records


def _source_vendor(source: Dict) -> str:
    return normalize_text(_first_present(
        source,
        ["見積元", "見積作成会社", "会社名", "発行会社", "vendor", "__source_name"],
        "",
    ))


def _coerce_summary_item(item: Dict, source_vendor: str = "", source_name: str = "", page_number: Optional[int] = None) -> Optional[Dict]:
    if not isinstance(item, dict):
        return None
    name = normalize_text(_first_present(item, ["工事項目", "項目名", "品名", "name", "label"]))
    amount = parse_money(_first_present(item, ["金額", "amount", "小計", "税抜金額"]))
    if not name or amount is None:
        return None
    vendor = normalize_text(_first_present(item, ["見積元", "見積作成会社", "会社名", "vendor"], source_vendor))
    return {
        "見積元": vendor,
        "元ファイル": source_name,
        "ページ": page_number,
        "工事項目": name,
        "仕様": normalize_text(_first_present(item, ["仕様", "備考", "摘要"], "")),
        "数量": _first_present(item, ["数量"], 1) or 1,
        "単位": normalize_text(_first_present(item, ["単位"], "式")) or "式",
        "単価": int(round(amount)),
        "金額": int(round(amount)),
        "備考": normalize_text(_first_present(item, ["備考", "摘要"], "")),
    }


def normalize_summary_data(summary_sources: List[Dict]) -> Dict:
    merged: Dict = {}
    items: List[Dict] = []
    normalized_sources: List[Dict] = []
    page_roles: List[str] = []

    for source in summary_sources or []:
        if not isinstance(source, dict):
            continue
        source_vendor = _source_vendor(source)
        source_name = normalize_text(source.get("__source_name", ""))
        page_number = source.get("__page_number")
        role = normalize_text(_first_present(source, ["page_role", "role", "ページ種別"]))
        if role:
            page_roles.append(role)
        source_items: List[Dict] = []
        for key, value in source.items():
            if str(key).startswith("__"):
                continue
            if value not in (None, "", []):
                merged.setdefault(key, value)
        for key in SUMMARY_ITEM_KEYS:
            value = source.get(key)
            if isinstance(value, list):
                for item in value:
                    coerced = _coerce_summary_item(item, source_vendor, source_name, page_number)
                    if coerced:
                        items.append(coerced)
                        source_items.append(coerced)
        if source_items or any(source.get(key) not in (None, "", []) for key in ("小計", "改小計", "消費税", "工事費計")):
            normalized_sources.append({
                "見積元": source_vendor,
                "元ファイル": source_name,
                "ページ": page_number,
                "工事名称": normalize_text(_first_present(source, ["工事名称", "工事名", "件名", "project_name"], "")),
                "工事項目": source_items,
                "小計": parse_money(_first_present(source, ["小計", "subtotal"])),
                "端数調整": parse_money(_first_present(source, ["端数調整", "端末調整", "調整額", "rounding_adjustment"])),
                "改小計": parse_money(_first_present(source, ["改小計", "税抜合計", "revised_subtotal"])),
                "消費税": parse_money(_first_present(source, ["消費税", "税額", "tax"])),
                "工事費計": parse_money(_first_present(source, ["工事費計", "税込合計", "総合計", "total"])),
            })

    return {
        "宛名": normalize_text(_first_present(merged, ["宛名", "提出先", "御中", "client_name"], "")),
        "工事名称": normalize_text(_first_present(merged, ["工事名称", "工事名", "件名", "project_name"], "")),
        "工事場所": normalize_text(_first_present(merged, ["工事場所", "施工場所", "場所", "site_address"], "")),
        "支払条件": normalize_text(_first_present(merged, ["支払条件", "payment_terms"], "")),
        "有効期限": normalize_text(_first_present(merged, ["有効期限", "見積有効期限", "valid_until"], "")),
        "見積担当": normalize_text(_first_present(merged, ["見積担当", "担当", "estimator"], "")),
        "工事金額": parse_money(_first_present(merged, ["工事金額", "見積金額", "工事費計", "税込合計", "total"])),
        "小計": parse_money(_first_present(merged, ["小計", "subtotal"])),
        "端数調整": parse_money(_first_present(merged, ["端数調整", "端末調整", "調整額", "rounding_adjustment"])),
        "改小計": parse_money(_first_present(merged, ["改小計", "税抜合計", "revised_subtotal"])),
        "消費税": parse_money(_first_present(merged, ["消費税", "税額", "tax"])),
        "工事費計": parse_money(_first_present(merged, ["工事費計", "税込合計", "総合計", "total"])),
        "工事項目": items,
        "summary_sources": normalized_sources,
        "page_roles": page_roles,
    }


def _amount_series(df: pd.DataFrame) -> pd.Series:
    if "見積金額" in df.columns:
        return pd.to_numeric(df["見積金額"], errors="coerce").fillna(0)
    if "原価金額" in df.columns:
        return pd.to_numeric(df["原価金額"], errors="coerce").fillna(0)
    if "金額" in df.columns:
        return pd.to_numeric(df["金額"], errors="coerce").fillna(0)
    return pd.Series(dtype=float)


def _build_summary_items_and_totals(summary_data: Dict, detail_df: pd.DataFrame, tax_rate: float = 0.10) -> Tuple[List[Dict], Dict[str, int]]:
    summary_data = summary_data or {}
    items = [dict(item) for item in summary_data.get("工事項目", []) if isinstance(item, dict)]
    detail_total = int(round(_amount_series(detail_df).sum())) if detail_df is not None and not detail_df.empty else 0

    if not items and detail_total:
        items = [{"工事項目": "工事一式", "仕様": "", "数量": 1, "単位": "式", "単価": detail_total, "金額": detail_total, "備考": "明細合計から作成"}]

    original_main_total = sum(
        int(parse_money(item.get("金額")) or 0)
        for item in items
        if not any(keyword in normalize_text(item.get("工事項目")) for keyword in EXPENSE_KEYWORDS)
    )
    profit_changed = bool(detail_df is not None and "上乗せ額" in detail_df.columns and pd.to_numeric(detail_df["上乗せ額"], errors="coerce").fillna(0).sum() != 0)

    if items and detail_total and (profit_changed or original_main_total == detail_total):
        main_indexes = [
            idx for idx, item in enumerate(items)
            if not any(keyword in normalize_text(item.get("工事項目")) for keyword in EXPENSE_KEYWORDS)
        ]
        if main_indexes:
            target_idx = main_indexes[0]
            original_target = int(parse_money(items[target_idx].get("金額")) or 0)
            delta = detail_total - (original_main_total or original_target)
            new_amount = original_target + delta
            items[target_idx]["単価"] = new_amount
            items[target_idx]["金額"] = new_amount

    subtotal = int(round(sum(parse_money(item.get("金額")) or 0 for item in items)))
    rounding = int(round(summary_data.get("端数調整") or 0))
    if not profit_changed and summary_data.get("小計") is not None:
        subtotal = int(round(summary_data.get("小計") or subtotal))
    revised_subtotal = subtotal + rounding
    if not profit_changed and summary_data.get("改小計") is not None:
        revised_subtotal = int(round(summary_data.get("改小計") or revised_subtotal))
    tax = int(round(revised_subtotal * tax_rate))
    if not profit_changed and summary_data.get("消費税") is not None:
        tax = int(round(summary_data.get("消費税") or tax))
    total = revised_subtotal + tax
    if not profit_changed and summary_data.get("工事費計") is not None:
        total = int(round(summary_data.get("工事費計") or total))

    return items, {
        "小計": subtotal,
        "端数調整": rounding,
        "改小計": revised_subtotal,
        "消費税": tax,
        "工事費計": total,
    }


def build_quote_summary_dataframe(summary_data: Dict, detail_df: pd.DataFrame, tax_rate: float = 0.10) -> Tuple[pd.DataFrame, Dict[str, int]]:
    """見積表紙シート。総合金額を確認するための先頭シート。"""
    summary_data = summary_data or {}
    items, totals = _build_summary_items_and_totals(summary_data, detail_df, tax_rate)
    subtotal = totals["小計"]
    rounding = totals["端数調整"]
    revised_subtotal = totals["改小計"]
    tax = totals["消費税"]
    total = totals["工事費計"]

    rows = [
        ["", "彩架建設 見積書", "", "", "", "", "", ""],
        ["", "宛名", summary_data.get("宛名", ""), "", "", "", "", ""],
        ["", "工事名称", summary_data.get("工事名称", ""), "", "", "", "", ""],
        ["", "工事場所", summary_data.get("工事場所", ""), "", "", "", "", ""],
        ["", "支払条件", summary_data.get("支払条件", ""), "", "", "", "", ""],
        ["", "有効期限", summary_data.get("有効期限", ""), "", "", "", "", ""],
        ["", "見積担当", summary_data.get("見積担当", ""), "", "", "", "", ""],
        ["", "会社情報", "彩架建設", "", "", "", "", ""],
        ["", "工事内容まとめ", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", ""],
        QUOTE_SUMMARY_COLUMNS,
    ]
    for idx, item in enumerate(items, start=1):
        amount = int(round(parse_money(item.get("金額")) or 0))
        rows.append([
            idx,
            item.get("工事項目", ""),
            item.get("仕様", ""),
            item.get("数量", 1),
            item.get("単位", "式"),
            amount,
            amount,
            item.get("備考", ""),
        ])
    rows.extend([
        ["", "", "", "", "", "", "", ""],
        ["", "小計", "", "", "", "", subtotal, ""],
        ["", "端数調整", "", "", "", "", rounding, ""],
        ["", "改小計", "", "", "", "", revised_subtotal, ""],
        ["", "消費税", "", "", "", "", tax, ""],
        ["", "合計金額", "", "", "", "", total, ""],
    ])
    return pd.DataFrame(rows), totals


def _work_summary_fallback_items(detail_df: pd.DataFrame) -> List[Dict]:
    if detail_df is None or detail_df.empty:
        return []
    source = output_dataframe(detail_df)
    group_col = "見積元" if "見積元" in source.columns else None
    if not group_col:
        total = int(round(_amount_series(source).sum()))
        return [{"工事項目": "工事一式", "数量": 1, "単位": "式", "単価": total, "金額": total, "備考": "明細合計から作成"}]

    items: List[Dict] = []
    for company, group in source.groupby(group_col, sort=False):
        company_name = normalize_text(company) or "不明な会社"
        total = int(round(pd.to_numeric(group["見積金額"], errors="coerce").fillna(0).sum()))
        items.append({
            "見積元": company_name,
            "工事項目": f"{company_name} 工事一式",
            "仕様": "",
            "数量": 1,
            "単位": "式",
            "単価": total,
            "金額": total,
            "備考": "明細合計から作成",
        })
    return items


def build_work_summary_dataframe(summary_data: Dict, detail_df: pd.DataFrame, tax_rate: float = 0.10) -> Tuple[pd.DataFrame, Dict[str, int]]:
    """工事別まとめシート。各社のまとめページと明細ページを混ぜず、貼り付け用8列に整える。"""
    summary_data = summary_data or {}
    items, totals = _build_summary_items_and_totals(summary_data, detail_df, tax_rate)
    if not items:
        items = _work_summary_fallback_items(detail_df)
        fallback_total = int(round(sum(parse_money(item.get("金額")) or 0 for item in items)))
        totals = {
            "小計": fallback_total,
            "端数調整": 0,
            "改小計": fallback_total,
            "消費税": int(round(fallback_total * tax_rate)),
            "工事費計": fallback_total + int(round(fallback_total * tax_rate)),
        }

    rows: List[Dict] = []
    for idx, item in enumerate(items, start=1):
        vendor = normalize_text(item.get("見積元", ""))
        note = normalize_text(item.get("備考", ""))
        if vendor and vendor not in note:
            note = f"{vendor} / {note}".strip(" /")
        amount = int(round(parse_money(item.get("金額")) or 0))
        rows.append({
            "No": idx,
            "工事品目": item.get("工事項目", ""),
            "仕様": item.get("仕様", ""),
            "数量": item.get("数量", 1),
            "単位": item.get("単位", "式"),
            "単価": amount,
            "金額": amount,
            "備考": note,
        })

    total_rows = [
        ("小計", totals["小計"]),
        ("端数調整", totals["端数調整"]),
        ("改小計", totals["改小計"]),
        ("消費税", totals["消費税"]),
        ("合計金額", totals["工事費計"]),
    ]
    for label, amount in total_rows:
        rows.append({
            "No": "",
            "工事品目": label,
            "仕様": "",
            "数量": "",
            "単位": "",
            "単価": "",
            "金額": amount,
            "備考": "",
        })

    return pd.DataFrame(rows, columns=NUMBERS_OUTPUT_COLUMNS), totals


def _is_summary_name(name: str) -> bool:
    return any(keyword in name for keyword in SUMMARY_KEYWORDS)


def build_intermediate_dataframe(records: List[Dict]) -> Tuple[pd.DataFrame, Dict[str, int]]:
    """AI/PDF parser outputを安全な中間明細に正規化する。"""
    rows = []
    totals = {"小計": 0, "消費税": 0, "税込合計": 0}

    for idx, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            continue

        name = normalize_text(_first_present(record, ["品名", "項目名", "工事品目", "名称・内容", "名称", "商品名・工事名"]))
        spec = normalize_text(_first_present(record, ["仕様", "規格", "摘要", "内容"]))
        if spec and spec not in name:
            name = f"{name} {spec}".strip()

        amount = parse_money(_first_present(record, ["原価金額", "金額", "amount"]))
        unit_price = parse_money(_first_present(record, ["原価単価", "単価", "unit_price"]))
        quantity, unit = split_quantity_unit(
            _first_present(record, ["数量", "quantity"]),
            _first_present(record, ["単位", "unit"]),
        )

        declared_subtotal = parse_money(_first_present(record, ["PDF小計", "見積税抜合計", "小計", "税抜合計"]))
        declared_tax = parse_money(_first_present(record, ["消費税", "税額"]))
        declared_total = parse_money(_first_present(record, ["税込合計", "合計", "総合計"]))
        if declared_subtotal:
            totals["小計"] = int(round(declared_subtotal))
        if declared_tax:
            totals["消費税"] = int(round(declared_tax))
        if declared_total:
            totals["税込合計"] = int(round(declared_total))

        if name and _is_summary_name(name):
            continue

        note_parts = []
        if not name:
            note_parts.append("品名要確認")
        if quantity is None:
            note_parts.append("数量要確認")
        if not unit:
            note_parts.append("単位要確認")
        if unit_price is None:
            note_parts.append("単価要確認")
        if amount is None:
            note_parts.append("金額要確認")

        if quantity not in (None, 0) and unit_price is not None and amount is not None:
            expected = int(round(quantity * unit_price))
            actual = int(round(amount))
            if expected != actual:
                note_parts.append(f"単価×数量={expected:,}円")

        rows.append(
            {
                "No": _first_present(record, ["No", "No.", "番号"], idx) or idx,
                "見積元": normalize_text(_first_present(record, ["見積元", "会社名", "vendor"], "不明")),
                "品名": name,
                "数量": quantity if quantity is not None else pd.NA,
                "単位": unit,
                "原価単価": unit_price if unit_price is not None else pd.NA,
                "原価金額": amount if amount is not None else pd.NA,
                "上乗せ額": 0,
                "見積単価": unit_price if unit_price is not None else pd.NA,
                "見積金額": amount if amount is not None else pd.NA,
                "備考": " / ".join(note_parts),
            }
        )

    df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    for col in ["数量", "原価単価", "原価金額", "上乗せ額", "見積単価", "見積金額"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df, totals


def validate_intermediate(df: pd.DataFrame, totals: Optional[Dict[str, int]] = None) -> List[Dict[str, str]]:
    issues: List[Dict[str, str]] = []
    totals = totals or {}

    required = ["品名", "数量", "単位", "原価単価", "原価金額"]
    for idx, row in df.iterrows():
        missing = []
        for col in required:
            value = row.get(col)
            if pd.isna(value) or normalize_text(value) == "":
                missing.append(col)
        if missing:
            issues.append({"レベル": "確認", "内容": f"No {row.get('No', idx + 1)}: {', '.join(missing)} が未取得です。"})

        qty = row.get("数量")
        price = row.get("原価単価")
        amount = row.get("原価金額")
        if pd.notna(qty) and pd.notna(price) and pd.notna(amount):
            expected = int(round(float(qty) * float(price)))
            actual = int(round(float(amount)))
            if expected != actual:
                issues.append({"レベル": "確認", "内容": f"No {row.get('No', idx + 1)}: 単価×数量({expected:,}円)と原価金額({actual:,}円)が一致しません。"})

    subtotal = int(round(pd.to_numeric(df["原価金額"], errors="coerce").fillna(0).sum())) if not df.empty else 0
    pdf_subtotal = int(totals.get("小計") or 0)
    pdf_tax = int(totals.get("消費税") or 0)
    pdf_total = int(totals.get("税込合計") or 0)

    if pdf_subtotal and subtotal != pdf_subtotal:
        issues.append({"レベル": "停止", "内容": "PDFの小計と抽出明細の合計が一致していません。明細の読み取りにズレがある可能性があります。出力前に確認してください。"})
    if pdf_subtotal and pdf_tax and pdf_total and pdf_subtotal + pdf_tax != pdf_total:
        issues.append({"レベル": "停止", "内容": f"PDF小計+消費税({pdf_subtotal + pdf_tax:,}円)と税込合計({pdf_total:,}円)が一致しません。"})

    return issues


def apply_profit(df: pd.DataFrame, profit_mode: str, profit_val: float = 0, company_profits: Optional[Dict[str, float]] = None) -> pd.DataFrame:
    out = df.copy()
    out["上乗せ額"] = 0.0
    out["見積単価"] = out["原価単価"]
    out["見積金額"] = out["原価金額"]

    def apply_to_mask(mask, amount_to_add):
        base = pd.to_numeric(out.loc[mask, "原価金額"], errors="coerce").fillna(0)
        positive_mask = mask & (pd.to_numeric(out["原価金額"], errors="coerce").fillna(0) > 0)
        base_total = pd.to_numeric(out.loc[positive_mask, "原価金額"], errors="coerce").fillna(0).sum()
        if base_total <= 0 or amount_to_add <= 0:
            return
        for idx in out[positive_mask].index:
            orig_amount = float(out.at[idx, "原価金額"])
            qty = float(out.at[idx, "数量"]) if pd.notna(out.at[idx, "数量"]) and float(out.at[idx, "数量"]) != 0 else 1.0
            add = amount_to_add * (orig_amount / base_total)
            estimate_amount = orig_amount + add
            estimate_unit = math.ceil((estimate_amount / qty) / 10.0) * 10
            estimate_amount = int(round(estimate_unit * qty))
            out.at[idx, "上乗せ額"] = int(round(estimate_amount - orig_amount))
            out.at[idx, "見積単価"] = int(estimate_unit)
            out.at[idx, "見積金額"] = estimate_amount

    all_mask = pd.Series(True, index=out.index)
    if profit_mode == "固定金額（円）を全体に割り振る":
        apply_to_mask(all_mask, float(profit_val or 0))
    elif profit_mode == "パーセンテージ（%）で全体に乗せる":
        positive_total = pd.to_numeric(out.loc[pd.to_numeric(out["原価金額"], errors="coerce").fillna(0) > 0, "原価金額"], errors="coerce").fillna(0).sum()
        apply_to_mask(all_mask, positive_total * float(profit_val or 0) / 100.0)
    elif profit_mode == "見積元（会社）ごとに金額を指定する" and company_profits:
        for company, add in company_profits.items():
            mask = out["見積元"].astype(str) == str(company)
            apply_to_mask(mask, float(add or 0))

    return out[OUTPUT_COLUMNS]


def output_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in OUTPUT_COLUMNS:
        if col not in out.columns:
            out[col] = ""
    return out[OUTPUT_COLUMNS]


def numbers_detail_dataframe(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[Dict[str, str]]]:
    """Numbers貼り付け用明細。中間データの全行・全順序を保持し、最終列だけに整える。"""
    source = output_dataframe(df)
    detail = pd.DataFrame(
        {
            "No": source["No"],
            "工事品目": source["品名"],
            "仕様": "",
            "数量": source["数量"],
            "単位": source["単位"],
            "単価": source["見積単価"],
            "金額": source["見積金額"],
            "備考": source["備考"],
        },
        index=source.index,
    )
    detail = detail[NUMBERS_OUTPUT_COLUMNS]
    issues: List[Dict[str, str]] = []
    if len(detail) != len(df):
        issues.append({
            "レベル": "停止",
            "内容": f"抽出明細：{len(df)}行 / 3枚目用明細：{len(detail)}行。出力用明細で{len(df) - len(detail)}行欠落しています。",
        })
    return detail, issues
