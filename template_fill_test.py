from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
from openpyxl import Workbook, load_workbook

from estimate_pipeline import normalize_text, parse_money


TEMPLATE_FILL_TEST_NAME = "Numbersテンプレート直接流し込みテスト"
TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "cyca_estimate_template_v2.xlsx"
OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "template_fill_test"

QUOTE_INFO_SHEET = "シート1 - 表1-1"
CUSTOMER_SHEET = "シート1 - 表2-1"
SUMMARY_SHEET = "シート1 - 工　事　内　容"
DETAIL_SHEET_1 = "シート1 - 工　事　内　容　明　細　１"

TEMPLATE_SUMMARY_START_ROW = 3
TEMPLATE_SUMMARY_END_ROW = 14
TEMPLATE_DETAIL_START_ROW = 3
TEMPLATE_DETAIL_END_ROW = 14


def _safe_sheet(wb: Workbook, name: str):
    if name not in wb.sheetnames:
        raise ValueError(f"テンプレート内に必要なシートがありません: {name}")
    return wb[name]


def _set_value(ws, cell: str, value):
    ws[cell].value = value


def _clear_table_values(ws, start_row: int, end_row: int):
    for row in range(start_row, end_row + 1):
        for col in range(1, 9):
            ws.cell(row=row, column=col).value = None


def _write_template_table(ws, rows: List[Dict], start_row: int, end_row: int) -> List[Dict]:
    capacity = max(0, end_row - start_row + 1)
    in_template = rows[:capacity]
    overflow = rows[capacity:]
    _clear_table_values(ws, start_row, end_row)
    for offset, row in enumerate(in_template):
        excel_row = start_row + offset
        ws.cell(excel_row, 1).value = offset + 1
        ws.cell(excel_row, 2).value = row.get("工事項目", "")
        ws.cell(excel_row, 3).value = row.get("仕様", "")
        ws.cell(excel_row, 4).value = row.get("数量", "")
        ws.cell(excel_row, 5).value = row.get("単位", "")
        ws.cell(excel_row, 6).value = row.get("単価", "")
        ws.cell(excel_row, 7).value = row.get("金額", "")
        ws.cell(excel_row, 8).value = row.get("備考", "")
    return overflow


def _row_from_series(row: pd.Series, amount_col: str = "見積金額", unit_col: str = "見積単価") -> Dict:
    return {
        "工事項目": normalize_text(row.get("品名", "")),
        "仕様": "",
        "数量": row.get("数量", ""),
        "単位": normalize_text(row.get("単位", "")),
        "単価": int(round(parse_money(row.get(unit_col)) or 0)),
        "金額": int(round(parse_money(row.get(amount_col)) or 0)),
        "備考": normalize_text(row.get("備考", "")),
    }


def _summary_rows_for_vendor(vendor_name: str, subtotal: int) -> List[Dict]:
    return [{
        "工事項目": f"{vendor_name} 工事一式",
        "仕様": "",
        "数量": 1,
        "単位": "式",
        "単価": subtotal,
        "金額": subtotal,
        "備考": "TEST直接流し込み",
    }]


def _detail_rows_for_vendor(detail_df: pd.DataFrame, vendor_name: str) -> List[Dict]:
    if detail_df is None or detail_df.empty:
        return []
    rows = []
    vendor_df = detail_df[detail_df["見積元"].astype(str) == str(vendor_name)]
    for _, row in vendor_df.iterrows():
        rows.append(_row_from_series(row))
    return rows


def _add_overflow_sheet(wb: Workbook, title: str, rows: List[Dict]):
    if not rows:
        return
    clean_title = title[:31]
    if clean_title in wb.sheetnames:
        ws = wb[clean_title]
        for row in ws.iter_rows():
            for cell in row:
                cell.value = None
    else:
        ws = wb.create_sheet(clean_title)
    headers = ["No", "工事項目", "仕様", "数量", "単位", "単価", "金額", "備考"]
    for col, header in enumerate(headers, start=1):
        ws.cell(1, col).value = header
    for idx, row in enumerate(rows, start=2):
        ws.cell(idx, 1).value = idx - 1
        ws.cell(idx, 2).value = row.get("工事項目", "")
        ws.cell(idx, 3).value = row.get("仕様", "")
        ws.cell(idx, 4).value = row.get("数量", "")
        ws.cell(idx, 5).value = row.get("単位", "")
        ws.cell(idx, 6).value = row.get("単価", "")
        ws.cell(idx, 7).value = row.get("金額", "")
        ws.cell(idx, 8).value = row.get("備考", "")


def validate_template_fill_test(detail_rows: List[Dict], original_subtotal: int, markup_amount: int, subtotal: int) -> List[str]:
    issues: List[str] = []
    detail_sum = int(round(sum(parse_money(row.get("金額")) or 0 for row in detail_rows)))
    if detail_sum != subtotal:
        issues.append(f"明細金額の合計({detail_sum:,}円)と小計({subtotal:,}円)が一致しません。")
    if original_subtotal + markup_amount != subtotal:
        issues.append(f"原価({original_subtotal:,}円)+上乗せ({markup_amount:,}円)が上乗せ後小計({subtotal:,}円)と一致しません。")
    for idx, row in enumerate(detail_rows, start=1):
        qty = parse_money(row.get("数量")) or 0
        unit_price = parse_money(row.get("単価")) or 0
        amount = parse_money(row.get("金額")) or 0
        if qty and unit_price and int(round(qty * unit_price)) != int(round(amount)):
            issues.append(f"No.{idx} 数量×単価と金額が一致しません。")
    return issues


def build_template_fill_test_workbook(
    *,
    detail_df: pd.DataFrame,
    cost_df: pd.DataFrame,
    vendor_name: Optional[str] = None,
    metadata: Optional[Dict] = None,
    template_path: Path = TEMPLATE_PATH,
) -> Tuple[bytes, str, List[str]]:
    """Copy the template and fill only cell values for one vendor test output."""
    if not template_path.exists():
        raise FileNotFoundError(f"テスト用テンプレートが見つかりません: {template_path}")
    if cost_df is None or cost_df.empty:
        raise ValueError("集計対象データがありません。")

    metadata = metadata or {}
    vendor_names = [normalize_text(v) for v in cost_df["見積元"].dropna().astype(str).unique().tolist()]
    selected_vendor = normalize_text(vendor_name) or (vendor_names[0] if vendor_names else "")
    if not selected_vendor:
        raise ValueError("見積元が取得できません。")

    cost_vendor_df = cost_df[cost_df["見積元"].astype(str) == str(selected_vendor)]
    detail_vendor_df = detail_df[detail_df["見積元"].astype(str) == str(selected_vendor)] if detail_df is not None and not detail_df.empty else pd.DataFrame()
    original_subtotal = int(round(pd.to_numeric(detail_vendor_df["原価金額"], errors="coerce").fillna(0).sum()))
    subtotal = int(round(pd.to_numeric(cost_vendor_df["見積金額"], errors="coerce").fillna(0).sum()))
    markup_amount = subtotal - original_subtotal
    summary_rows = _summary_rows_for_vendor(selected_vendor, subtotal)
    detail_rows = _detail_rows_for_vendor(detail_df, selected_vendor)
    issues = validate_template_fill_test(detail_rows, original_subtotal, markup_amount, subtotal)

    wb = load_workbook(template_path)
    ws_customer = _safe_sheet(wb, CUSTOMER_SHEET)
    ws_info = _safe_sheet(wb, QUOTE_INFO_SHEET)
    ws_summary = _safe_sheet(wb, SUMMARY_SHEET)
    ws_detail = _safe_sheet(wb, DETAIL_SHEET_1)

    _set_value(ws_customer, "A1", metadata.get("宛名", ""))
    _set_value(ws_info, "B1", metadata.get("工事名称", metadata.get("件名", "")))
    _set_value(ws_info, "B2", metadata.get("工事場所", ""))
    _set_value(ws_info, "B3", metadata.get("工事期間", ""))
    _set_value(ws_info, "B4", metadata.get("支払条件", "ご相談の上"))
    _set_value(ws_info, "B5", metadata.get("有効期限", "お打ち合わせの上"))
    _set_value(ws_info, "B6", metadata.get("見積担当", "中村哲也"))

    summary_overflow = _write_template_table(ws_summary, summary_rows, TEMPLATE_SUMMARY_START_ROW, TEMPLATE_SUMMARY_END_ROW)
    detail_overflow = _write_template_table(ws_detail, detail_rows, TEMPLATE_DETAIL_START_ROW, TEMPLATE_DETAIL_END_ROW)
    _add_overflow_sheet(wb, "TEST_明細データ", summary_overflow + detail_overflow)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_part = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_vendor = selected_vendor.replace("/", "").replace("\\", "")
    file_name = f"TEST_彩架建設見積テンプレート流し込み_{safe_vendor}_{date_part}.xlsx"
    output_path = OUTPUT_DIR / file_name
    output = BytesIO()
    wb.save(output)
    data = output.getvalue()
    output_path.write_bytes(data)
    return data, file_name, issues
