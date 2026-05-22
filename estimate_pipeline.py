import math
import re
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd


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
