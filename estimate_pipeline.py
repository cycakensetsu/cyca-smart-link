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

SIMPLE_DETAIL_COLUMNS = [
    "商品名・工事名",
    "数量",
    "単位",
    "単価（円）",
    "金額（円）",
    "備考",
]

COPY_TABLE_COLUMNS = ["工事項目", "数量", "単位", "単価（円）", "金額（円）", "備考"]

SUMMARY_ITEM_KEYS = ["工事費内訳", "工事内容のまとめ", "工事項目単位の集計", "summary_items", "work_items", "items", "工事項目"]
EXPENSE_KEYWORDS = ["諸経費", "福利", "厚生", "法定福利", "運搬", "処分", "荷揚げ", "現場管理"]
PREFERRED_MARKUP_KEYWORDS = ["工事", "施工", "補修", "取付", "取り付け", "塗装", "防水", "板金", "屋根", "外壁"]
EXPENSE_MARKUP_KEYWORDS = ["諸経費", "廃材", "処分", "運搬", "クレーン", "高所作業車", "法定福利", "福利", "安全", "養生", "交通費", "雑費"]
LIGHT_WORK_KEYWORDS = ["養生", "清掃", "軽作業"]
MATERIAL_KEYWORDS = ["材料", "材", "鋼鈑", "板", "シート", "面戸", "副資材"]

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


def assign_unknown_vendors_to_pdf_vendor(records: List[Dict], summary_sources: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """同一PDF内に会社名付き見積がある場合、不明明細をその業者の明細として扱う。"""
    named_by_pdf: Dict[str, List[str]] = {}

    for source in summary_sources or []:
        if not isinstance(source, dict):
            continue
        source_pdf = normalize_text(source.get("__source_name", ""))
        vendor = _source_vendor(source)
        if source_pdf and vendor and not _is_unknown_vendor(vendor):
            named_by_pdf.setdefault(source_pdf, [])
            if vendor not in named_by_pdf[source_pdf]:
                named_by_pdf[source_pdf].append(vendor)

    for record in records or []:
        if not isinstance(record, dict):
            continue
        source_pdf = _record_source_pdf(record)
        vendor = _record_vendor(record)
        if source_pdf and vendor and not _is_unknown_vendor(vendor):
            named_by_pdf.setdefault(source_pdf, [])
            if vendor not in named_by_pdf[source_pdf]:
                named_by_pdf[source_pdf].append(vendor)

    reassigned: List[Dict] = []
    debug_rows: List[Dict] = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        out = dict(record)
        source_pdf = _record_source_pdf(out)
        original_vendor = _record_vendor(out)
        candidates = named_by_pdf.get(source_pdf, [])
        assigned_vendor = original_vendor
        if _is_unknown_vendor(original_vendor) and len(candidates) == 1:
            assigned_vendor = candidates[0]
            out["見積元"] = assigned_vendor
            out["__assigned_vendor_from_pdf"] = True
        reassigned.append(out)
        debug_rows.append({
            "source_pdf名": source_pdf,
            "original_company_name": original_vendor,
            "company_name": assigned_vendor,
            "assigned_from_same_pdf": assigned_vendor != original_vendor,
            "抽出元テキスト範囲": _record_text_range(out),
        })

    return reassigned, debug_rows


def _detail_vendor_totals(detail_df: pd.DataFrame) -> Dict[Tuple[str, str], int]:
    if detail_df is None or detail_df.empty:
        return {}
    source = detail_df.copy()
    if "元ファイル" not in source.columns:
        source["元ファイル"] = ""
    totals: Dict[Tuple[str, str], int] = {}
    for (_, row) in source.iterrows():
        vendor = normalize_text(row.get("見積元", "")) or "不明"
        source_pdf = normalize_text(row.get("元ファイル", ""))
        amount = parse_money(row.get("原価金額")) or 0
        totals[(source_pdf, vendor)] = totals.get((source_pdf, vendor), 0) + int(round(amount))
    return totals


def _detail_items_for_vendor(detail_df: pd.DataFrame, source_pdf: str, vendor: str) -> List[Dict]:
    if detail_df is None or detail_df.empty:
        return []
    source = detail_df.copy()
    if "元ファイル" not in source.columns:
        source["元ファイル"] = ""
    items: List[Dict] = []
    for (_, row) in source.iterrows():
        row_vendor = normalize_text(row.get("見積元", ""))
        row_pdf = normalize_text(row.get("元ファイル", ""))
        if row_vendor != vendor:
            continue
        if source_pdf and row_pdf and row_pdf != source_pdf:
            continue
        amount = int(round(parse_money(row.get("原価金額")) or 0))
        items.append({
            "工事項目": normalize_text(row.get("品名", "")),
            "仕様": "",
            "数量": row.get("数量", 1),
            "単位": normalize_text(row.get("単位", "式")) or "式",
            "単価": int(round(parse_money(row.get("原価単価")) or amount)),
            "金額": amount,
            "備考": normalize_text(row.get("備考", "")),
        })
    return items


def build_cost_basis_dataframe(summary_data: Dict, detail_df: pd.DataFrame, tax_rate: float = 0.10) -> Tuple[pd.DataFrame, List[Dict]]:
    """集計対象を業者ごとの最終税抜小計だけに限定した原価DFを作る。"""
    summary_data = summary_data or {}
    summary_sources = summary_data.get("summary_sources", []) or []
    detail_totals = _detail_vendor_totals(detail_df)
    represented_keys = set()
    vendor_summaries: List[Dict] = []

    for source in summary_sources:
        if not isinstance(source, dict):
            continue
        source_pdf = normalize_text(source.get("元ファイル", ""))
        vendor = normalize_text(source.get("見積元", "")) or "不明"
        if _is_unknown_vendor(vendor):
            candidates = [key_vendor for (key_pdf, key_vendor) in detail_totals if key_pdf == source_pdf and not _is_unknown_vendor(key_vendor)]
            if len(set(candidates)) == 1:
                vendor = candidates[0]
        items = [dict(item) for item in source.get("工事項目", []) if isinstance(item, dict)]
        item_sum = int(round(sum(parse_money(item.get("金額")) or 0 for item in items)))
        subtotal = source.get("改小計")
        if subtotal is None:
            subtotal = source.get("小計")
        if subtotal is None:
            subtotal = item_sum
        if subtotal is None or int(round(subtotal or 0)) == 0:
            subtotal = detail_totals.get((source_pdf, vendor), 0)
        subtotal = int(round(subtotal or 0))
        if subtotal == 0 and not items:
            continue
        rounding = int(round(source.get("端数調整") or 0))
        tax = int(round(source.get("消費税") if source.get("消費税") is not None else subtotal * tax_rate))
        total = int(round(source.get("工事費計") if source.get("工事費計") is not None else subtotal + tax))
        if not items:
            items = _detail_items_for_vendor(detail_df, source_pdf, vendor)
        vendor_summaries.append({
            "見積元": vendor,
            "元ファイル": source_pdf,
            "工事名称": normalize_text(source.get("工事名称", "")),
            "工事項目": items,
            "小計": subtotal,
            "端数調整": rounding,
            "改小計": subtotal,
            "消費税": tax,
            "工事費計": total,
            "明細合計": detail_totals.get((source_pdf, vendor), 0),
            "集計根拠": "summary",
        })
        represented_keys.add((source_pdf, vendor))

    for (source_pdf, vendor), detail_total in detail_totals.items():
        if (source_pdf, vendor) in represented_keys:
            continue
        if detail_total == 0:
            continue
        items = _detail_items_for_vendor(detail_df, source_pdf, vendor)
        tax = int(round(detail_total * tax_rate))
        vendor_summaries.append({
            "見積元": vendor,
            "元ファイル": source_pdf,
            "工事名称": "",
            "工事項目": items,
            "小計": detail_total,
            "端数調整": 0,
            "改小計": detail_total,
            "消費税": tax,
            "工事費計": detail_total + tax,
            "明細合計": detail_total,
            "集計根拠": "detail_fallback",
        })

    rows: List[Dict] = []
    for idx, summary in enumerate(vendor_summaries, start=1):
        vendor = normalize_text(summary.get("見積元", "")) or "不明"
        subtotal = int(round(summary.get("改小計") or summary.get("小計") or 0))
        rows.append({
            "No": idx,
            "見積元": vendor,
            "品名": f"{vendor} まとめ",
            "数量": 1,
            "単位": "式",
            "原価単価": subtotal,
            "原価金額": subtotal,
            "上乗せ額": 0,
            "見積単価": subtotal,
            "見積金額": subtotal,
            "備考": "集計対象: 業者見積の税抜小計",
        })

    df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    for col in ["数量", "原価単価", "原価金額", "上乗せ額", "見積単価", "見積金額"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df, vendor_summaries


def summary_data_from_cost_dataframe(summary_data: Dict, cost_df: pd.DataFrame) -> Dict:
    out = dict(summary_data or {})
    items: List[Dict] = []
    if cost_df is not None and not cost_df.empty:
        for (_, row) in cost_df.iterrows():
            amount = int(round(parse_money(row.get("見積金額")) or parse_money(row.get("原価金額")) or 0))
            items.append({
                "見積元": normalize_text(row.get("見積元", "")),
                "工事項目": normalize_text(row.get("品名", "")),
                "仕様": "",
                "数量": row.get("数量", 1),
                "単位": normalize_text(row.get("単位", "式")) or "式",
                "単価": amount,
                "金額": amount,
                "備考": normalize_text(row.get("備考", "")),
            })
    out["工事項目"] = items
    out["小計"] = int(round(sum(parse_money(item.get("金額")) or 0 for item in items)))
    out["端数調整"] = 0
    out["改小計"] = out["小計"]
    out["消費税"] = int(round(out["改小計"] * 0.10))
    out["工事費計"] = out["改小計"] + out["消費税"]
    return out


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


def build_vendor_work_summary_dataframe(vendor_summaries: List[Dict], cost_df: pd.DataFrame, tax_rate: float = 0.10) -> Tuple[pd.DataFrame, Dict[str, int]]:
    """業者ごとのまとめだけを2枚目に出す。明細合計は集計に使わない。"""
    cost_amount_by_vendor: Dict[str, int] = {}
    if cost_df is not None and not cost_df.empty:
        for vendor, group in cost_df.groupby("見積元", sort=False):
            cost_amount_by_vendor[normalize_text(vendor)] = int(round(pd.to_numeric(group["見積金額"], errors="coerce").fillna(0).sum()))

    rows: List[Dict] = []
    subtotal_total = 0
    for summary in vendor_summaries or []:
        vendor = normalize_text(summary.get("見積元", "")) or "不明"
        subtotal = cost_amount_by_vendor.get(vendor, int(round(summary.get("改小計") or summary.get("小計") or 0)))
        subtotal_total += subtotal
        tax = int(round(subtotal * tax_rate))
        total = subtotal + tax
        rows.append({"No": "", "工事品目": f"【{vendor} まとめ】", "仕様": normalize_text(summary.get("元ファイル", "")), "数量": "", "単位": "", "単価": "", "金額": "", "備考": summary.get("集計根拠", "")})
        items = [dict(item) for item in summary.get("工事項目", []) if isinstance(item, dict)]
        if not items:
            items = [{"工事項目": f"{vendor} 工事一式", "仕様": "", "数量": 1, "単位": "式", "単価": subtotal, "金額": subtotal, "備考": ""}]
        for item_idx, item in enumerate(items, start=1):
            amount = int(round(parse_money(item.get("金額")) or 0))
            rows.append({
                "No": item_idx,
                "工事品目": normalize_text(item.get("工事項目", "")),
                "仕様": normalize_text(item.get("仕様", "")),
                "数量": item.get("数量", 1),
                "単位": normalize_text(item.get("単位", "式")) or "式",
                "単価": int(round(parse_money(item.get("単価")) or amount)),
                "金額": amount,
                "備考": normalize_text(item.get("備考", "")),
            })
        rows.extend([
            {"No": "", "工事品目": f"{vendor} 小計", "仕様": "", "数量": "", "単位": "", "単価": "", "金額": subtotal, "備考": "集計対象"},
            {"No": "", "工事品目": f"{vendor} 消費税", "仕様": "", "数量": "", "単位": "", "単価": "", "金額": tax, "備考": ""},
            {"No": "", "工事品目": f"{vendor} 合計", "仕様": "", "数量": "", "単位": "", "単価": "", "金額": total, "備考": ""},
            {"No": "", "工事品目": "", "仕様": "", "数量": "", "単位": "", "単価": "", "金額": "", "備考": ""},
        ])

    total_tax = int(round(subtotal_total * tax_rate))
    totals = {
        "小計": subtotal_total,
        "端数調整": 0,
        "改小計": subtotal_total,
        "消費税": total_tax,
        "工事費計": subtotal_total + total_tax,
    }
    rows.extend([
        {"No": "", "工事品目": "全業者 小計", "仕様": "", "数量": "", "単位": "", "単価": "", "金額": totals["小計"], "備考": ""},
        {"No": "", "工事品目": "全業者 消費税", "仕様": "", "数量": "", "単位": "", "単価": "", "金額": totals["消費税"], "備考": ""},
        {"No": "", "工事品目": "全業者 合計", "仕様": "", "数量": "", "単位": "", "単価": "", "金額": totals["工事費計"], "備考": ""},
    ])
    return pd.DataFrame(rows, columns=NUMBERS_OUTPUT_COLUMNS), totals


def vendor_detail_dataframe(detail_df: pd.DataFrame) -> Tuple[pd.DataFrame, List[Dict[str, str]]]:
    """3枚目は業者ごとの確認用明細。ここは原価合計には使わない。"""
    if detail_df is None or detail_df.empty:
        return pd.DataFrame(columns=NUMBERS_OUTPUT_COLUMNS), []
    source = output_dataframe(detail_df)
    rows: List[Dict] = []
    for vendor, group in source.groupby("見積元", sort=False):
        vendor_name = normalize_text(vendor) or "不明"
        rows.append({"No": "", "工事品目": f"【{vendor_name} 明細】", "仕様": "", "数量": "", "単位": "", "単価": "", "金額": "", "備考": "確認用・集計対象外"})
        for item_idx, (_, row) in enumerate(group.iterrows(), start=1):
            rows.append({
                "No": item_idx,
                "工事品目": row.get("品名", ""),
                "仕様": "",
                "数量": row.get("数量", ""),
                "単位": row.get("単位", ""),
                "単価": row.get("原価単価", ""),
                "金額": row.get("原価金額", ""),
                "備考": row.get("備考", ""),
            })
        detail_total = int(round(pd.to_numeric(group["原価金額"], errors="coerce").fillna(0).sum()))
        rows.append({"No": "", "工事品目": f"{vendor_name} 明細合計", "仕様": "", "数量": "", "単位": "", "単価": "", "金額": detail_total, "備考": "確認用"})
        rows.append({"No": "", "工事品目": "", "仕様": "", "数量": "", "単位": "", "単価": "", "金額": "", "備考": ""})
    return pd.DataFrame(rows, columns=NUMBERS_OUTPUT_COLUMNS), []


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
                "元ファイル": normalize_text(record.get("__source_name", "")),
                "ページ": record.get("__page_number", ""),
            }
        )

    df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS + ["元ファイル", "ページ"])
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


def _unit_price_factor(unit_price: float) -> float:
    if unit_price >= 50000:
        return 1.5
    if unit_price >= 10000:
        return 1.2
    if unit_price >= 3000:
        return 1.0
    if unit_price < 500:
        return 0.1
    if unit_price < 1000:
        return 0.3
    return 0.6


def _item_class_factor(name: str) -> float:
    text = normalize_text(name)
    if any(keyword in text for keyword in EXPENSE_MARKUP_KEYWORDS):
        return 0.2
    if any(keyword in text for keyword in LIGHT_WORK_KEYWORDS):
        return 0.6
    if any(keyword in text for keyword in PREFERRED_MARKUP_KEYWORDS):
        return 1.5
    if any(keyword in text for keyword in MATERIAL_KEYWORDS):
        return 1.0
    return 1.0


def _markup_unit_cap(unit_price: float) -> Optional[float]:
    if unit_price <= 0:
        return None
    if unit_price < 500:
        return unit_price * 1.2
    if unit_price < 1000:
        return unit_price * 2.0
    if unit_price < 3000:
        return unit_price * 3.0
    return None


def _round_unit_naturally(value: float) -> int:
    if value <= 0:
        return 0
    unit = 1000 if value >= 50000 else 100
    return int(math.ceil(value / unit) * unit)


def _best_adjustment_index(group: pd.DataFrame) -> Optional[int]:
    candidates = []
    for idx, row in group.iterrows():
        name = normalize_text(row.get("品名", ""))
        unit_price = float(row.get("原価単価") or 0)
        amount = float(row.get("原価金額") or 0)
        if any(keyword in name for keyword in EXPENSE_MARKUP_KEYWORDS):
            continue
        candidates.append((unit_price, amount, idx))
    if not candidates:
        for idx, row in group.iterrows():
            candidates.append((float(row.get("原価単価") or 0), float(row.get("原価金額") or 0), idx))
    if not candidates:
        return None
    return sorted(candidates, reverse=True)[0][2]


def _allocate_group_markup(detail_df: pd.DataFrame, target_total: int) -> pd.DataFrame:
    out = detail_df.copy()
    if out.empty:
        return out
    original_total = int(round(pd.to_numeric(out["原価金額"], errors="coerce").fillna(0).sum()))
    increment = int(round(target_total - original_total))
    out["上乗せ額"] = 0
    out["見積単価"] = pd.to_numeric(out["原価単価"], errors="coerce")
    out["見積金額"] = pd.to_numeric(out["原価金額"], errors="coerce")
    if increment <= 0:
        return out

    weights: Dict[int, float] = {}
    caps: Dict[int, Optional[int]] = {}
    for idx, row in out.iterrows():
        amount = float(row.get("原価金額") or 0)
        unit_price = float(row.get("原価単価") or 0)
        qty = float(row.get("数量") or 1)
        if amount <= 0 or qty <= 0 or unit_price <= 0:
            weights[idx] = 0.0
            caps[idx] = 0
            continue
        weight = amount * _unit_price_factor(unit_price) * _item_class_factor(row.get("品名", ""))
        weights[idx] = max(weight, 0.0)
        cap_unit = _markup_unit_cap(unit_price)
        caps[idx] = None if cap_unit is None else max(0, int(round(cap_unit * qty - amount)))

    remaining = increment
    active = {idx for idx, weight in weights.items() if weight > 0}
    allocations = {idx: 0 for idx in out.index}
    while remaining > 0 and active:
        total_weight = sum(weights[idx] for idx in active)
        if total_weight <= 0:
            break
        used_this_round = 0
        next_active = set()
        for idx in active:
            raw_add = remaining * (weights[idx] / total_weight)
            cap = caps.get(idx)
            available = remaining if cap is None else max(0, cap - allocations[idx])
            add = min(int(round(raw_add)), available)
            if add > 0:
                allocations[idx] += add
                used_this_round += add
            if cap is None or allocations[idx] < cap:
                next_active.add(idx)
        if used_this_round <= 0:
            break
        remaining -= used_this_round
        active = next_active

    adjustment_idx = _best_adjustment_index(out)
    if remaining > 0 and adjustment_idx is not None:
        allocations[adjustment_idx] += remaining

    for idx, row in out.iterrows():
        qty = float(row.get("数量") or 1)
        qty = qty if qty != 0 else 1
        original_amount = float(row.get("原価金額") or 0)
        estimate_amount = original_amount + allocations.get(idx, 0)
        estimate_unit = _round_unit_naturally(estimate_amount / qty)
        estimate_amount = int(round(estimate_unit * qty))
        cap = caps.get(idx)
        if cap is not None and estimate_amount - original_amount > cap:
            estimate_amount = int(round(original_amount + cap))
            estimate_unit = int(round(estimate_amount / qty))
        out.at[idx, "見積単価"] = estimate_unit
        out.at[idx, "見積金額"] = estimate_amount
        out.at[idx, "上乗せ額"] = int(round(estimate_amount - original_amount))

    rounded_total = int(round(pd.to_numeric(out["見積金額"], errors="coerce").fillna(0).sum()))
    diff = int(round(target_total - rounded_total))
    adjustment_idx = _best_adjustment_index(out)
    if diff != 0 and adjustment_idx is not None:
        qty = float(out.at[adjustment_idx, "数量"] or 1)
        qty = qty if qty != 0 else 1
        current_amount = float(out.at[adjustment_idx, "見積金額"] or 0)
        new_amount = int(round(current_amount + diff))
        new_unit = max(0, int(round(new_amount / qty)))
        out.at[adjustment_idx, "見積単価"] = new_unit
        out.at[adjustment_idx, "見積金額"] = int(round(new_unit * qty))
        out.at[adjustment_idx, "上乗せ額"] = int(round(out.at[adjustment_idx, "見積金額"] - float(out.at[adjustment_idx, "原価金額"] or 0)))
    return out


def apply_company_profit_to_details(detail_df: pd.DataFrame, cost_df: pd.DataFrame, company_profits: Optional[Dict[str, float]] = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """会社ごとの上乗せ額を明細行へ自然配分し、まとめ小計と明細合計を一致させる。"""
    company_profits = company_profits or {}
    detail_out = detail_df.copy()
    cost_out = cost_df.copy()
    if detail_out.empty or cost_out.empty:
        return detail_out, cost_out
    detail_out["上乗せ額"] = 0
    detail_out["見積単価"] = detail_out["原価単価"]
    detail_out["見積金額"] = detail_out["原価金額"]

    for company, cost_group in cost_out.groupby("見積元", sort=False):
        company_name = normalize_text(company)
        add = int(round(float(company_profits.get(company_name, 0) or 0)))
        base_total = int(round(pd.to_numeric(cost_group["原価金額"], errors="coerce").fillna(0).sum()))
        target_total = base_total + add
        mask = detail_out["見積元"].astype(str) == str(company_name)
        if not mask.any():
            continue
        allocated = _allocate_group_markup(detail_out.loc[mask], target_total)
        for col in ["上乗せ額", "見積単価", "見積金額"]:
            detail_out.loc[allocated.index, col] = allocated[col]
        actual_total = int(round(pd.to_numeric(allocated["見積金額"], errors="coerce").fillna(0).sum()))
        cost_mask = cost_out["見積元"].astype(str) == str(company_name)
        if cost_mask.any():
            idx = cost_out[cost_mask].index[0]
            cost_out.at[idx, "上乗せ額"] = actual_total - base_total
            cost_out.at[idx, "見積単価"] = actual_total
            cost_out.at[idx, "見積金額"] = actual_total
    return detail_out, cost_out


def _copy_table_from_rows(rows: List[Dict], amount_key: str = "見積金額", unit_key: str = "見積単価") -> pd.DataFrame:
    out_rows = []
    for row in rows:
        out_rows.append({
            "工事項目": normalize_text(row.get("品名", row.get("工事項目", ""))),
            "数量": row.get("数量", 1),
            "単位": normalize_text(row.get("単位", "式")) or "式",
            "単価（円）": int(round(parse_money(row.get(unit_key)) or parse_money(row.get("単価")) or parse_money(row.get(amount_key)) or 0)),
            "金額（円）": int(round(parse_money(row.get(amount_key)) or parse_money(row.get("金額")) or 0)),
            "備考": normalize_text(row.get("備考", "")),
        })
    return pd.DataFrame(out_rows, columns=COPY_TABLE_COLUMNS)


def _totals_table(subtotal: int, tax_rate: float = 0.10) -> pd.DataFrame:
    tax = int(round(subtotal * tax_rate))
    return pd.DataFrame([
        {"工事項目": "小計", "数量": subtotal, "単位": "", "単価（円）": "", "金額（円）": "", "備考": ""},
        {"工事項目": "消費税", "数量": tax, "単位": "", "単価（円）": "", "金額（円）": "", "備考": ""},
        {"工事項目": "合計", "数量": subtotal + tax, "単位": "", "単価（円）": "", "金額（円）": "", "備考": ""},
    ], columns=COPY_TABLE_COLUMNS)


def build_vendor_copy_sheets(vendor_summaries: List[Dict], detail_df: pd.DataFrame, cost_df: pd.DataFrame, tax_rate: float = 0.10) -> List[Tuple[str, pd.DataFrame]]:
    """業者ごとに まとめ/明細 の貼り付け用シートを作る。"""
    sheets: List[Tuple[str, pd.DataFrame]] = []
    if cost_df is None or cost_df.empty:
        return sheets
    detail_source = detail_df.copy() if detail_df is not None else pd.DataFrame()
    summary_by_vendor = {normalize_text(s.get("見積元", "")): s for s in vendor_summaries or []}
    used_names: Dict[str, int] = {}

    def sheet_name(base: str) -> str:
        clean = re.sub(r"[:\\\\/?*\\[\\]]", "", base)[:28] or "sheet"
        count = used_names.get(clean, 0)
        used_names[clean] = count + 1
        return clean if count == 0 else f"{clean[:25]}_{count + 1}"

    for vendor, cost_group in cost_df.groupby("見積元", sort=False):
        vendor_name = normalize_text(vendor) or "不明"
        subtotal = int(round(pd.to_numeric(cost_group["見積金額"], errors="coerce").fillna(0).sum()))
        summary = summary_by_vendor.get(vendor_name, {})
        summary_items = []
        if summary.get("集計根拠") != "detail_fallback":
            for item in summary.get("工事項目", []) or []:
                amount = int(round(parse_money(item.get("金額")) or 0))
                if not amount:
                    continue
                ratio = subtotal / max(int(round(summary.get("改小計") or summary.get("小計") or amount)), 1)
                estimate_amount = int(round(amount * ratio))
                qty = parse_money(item.get("数量")) or 1
                summary_items.append({
                    "工事項目": normalize_text(item.get("工事項目", "")),
                    "数量": qty,
                    "単位": normalize_text(item.get("単位", "式")) or "式",
                    "単価": int(round(estimate_amount / (qty or 1))),
                    "金額": estimate_amount,
                    "備考": normalize_text(item.get("備考", "")),
                })
        if not summary_items:
            for _, row in cost_group.iterrows():
                summary_items.append({
                    "工事項目": normalize_text(row.get("品名", f"{vendor_name} 工事一式")),
                    "数量": row.get("数量", 1),
                    "単位": normalize_text(row.get("単位", "式")) or "式",
                    "単価": int(round(parse_money(row.get("見積単価")) or subtotal)),
                    "金額": int(round(parse_money(row.get("見積金額")) or subtotal)),
                    "備考": normalize_text(row.get("備考", "")),
                })
        summary_table = _copy_table_from_rows(summary_items, amount_key="金額", unit_key="単価")
        summary_diff = subtotal - int(round(pd.to_numeric(summary_table["金額（円）"], errors="coerce").fillna(0).sum()))
        if summary_diff and not summary_table.empty:
            idx = summary_table["金額（円）"].astype(float).idxmax()
            qty = float(summary_table.at[idx, "数量"] or 1)
            summary_table.at[idx, "金額（円）"] = int(summary_table.at[idx, "金額（円）"] + summary_diff)
            summary_table.at[idx, "単価（円）"] = int(round(summary_table.at[idx, "金額（円）"] / (qty or 1)))
        summary_out = pd.concat([summary_table, pd.DataFrame([{}]), _totals_table(subtotal, tax_rate)], ignore_index=True)
        sheets.append((sheet_name(f"{vendor_name} まとめ"), summary_out))

        vendor_details = detail_source[detail_source["見積元"].astype(str) == str(vendor_name)] if not detail_source.empty else pd.DataFrame()
        detail_table = _copy_table_from_rows([row.to_dict() for _, row in vendor_details.iterrows()], amount_key="見積金額", unit_key="見積単価")
        detail_subtotal = int(round(pd.to_numeric(detail_table["金額（円）"], errors="coerce").fillna(0).sum())) if not detail_table.empty else 0
        if detail_subtotal != subtotal and not detail_table.empty:
            idx = detail_table["金額（円）"].astype(float).idxmax()
            diff = subtotal - detail_subtotal
            qty = float(detail_table.at[idx, "数量"] or 1)
            detail_table.at[idx, "金額（円）"] = int(detail_table.at[idx, "金額（円）"] + diff)
            detail_table.at[idx, "単価（円）"] = int(round(detail_table.at[idx, "金額（円）"] / (qty or 1)))
        detail_out = pd.concat([detail_table, pd.DataFrame([{}]), _totals_table(subtotal, tax_rate)], ignore_index=True)
        sheets.append((sheet_name(f"{vendor_name} 明細"), detail_out))
    return sheets


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


def simple_detail_dataframe(detail_df: pd.DataFrame) -> Tuple[pd.DataFrame, List[Dict[str, str]]]:
    """簡易工事見積用。Numbersテンプレートへ値だけ貼るための固定6列表。"""
    if detail_df is None or detail_df.empty:
        return pd.DataFrame(columns=SIMPLE_DETAIL_COLUMNS), []
    source = output_dataframe(detail_df)
    simple = pd.DataFrame(
        {
            "商品名・工事名": source["品名"],
            "数量": pd.to_numeric(source["数量"], errors="coerce"),
            "単位": source["単位"],
            "単価（円）": pd.to_numeric(source["原価単価"], errors="coerce"),
            "金額（円）": pd.to_numeric(source["原価金額"], errors="coerce"),
            "備考": source["備考"],
        },
        index=source.index,
    )
    return simple[SIMPLE_DETAIL_COLUMNS], []
