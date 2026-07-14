from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import MergedCell

from estimate_pipeline import normalize_text, parse_money


TEMPLATE_FILL_TEST_NAME = "Numbersテンプレート直接流し込みテスト"
TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "cyca_estimate_template_v2.xlsx"
OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "template_fill_test"

QUOTE_SHEET = "見積書"
SUMMARY_SHEET = "工事内容"
DETAIL_SHEET_1 = "工事内容明細1"

TEMPLATE_SUMMARY_START_ROW = 4
TEMPLATE_SUMMARY_END_ROW = 23
TEMPLATE_DETAIL_START_ROW = 5
TEMPLATE_DETAIL_END_ROW = 24


def _safe_sheet(wb: Workbook, name: str):
    if name not in wb.sheetnames:
        raise ValueError(f"テンプレート内に必要なシートがありません: {name}")
    return wb[name]


def _set_value(ws, cell: str, value):
    if isinstance(ws[cell], MergedCell):
        return
    ws[cell].value = value


def _clear_table_values(ws, start_row: int, end_row: int):
    for row in range(start_row, end_row + 1):
        for col in range(1, 9):
            cell = ws.cell(row=row, column=col)
            if not isinstance(cell, MergedCell):
                cell.value = None


def _write_template_table(ws, rows: List[Dict], start_row: int, end_row: int) -> List[Dict]:
    capacity = max(0, end_row - start_row + 1)
    in_template = rows[:capacity]
    overflow = rows[capacity:]
    _clear_table_values(ws, start_row, end_row)
    for offset, row in enumerate(in_template):
        excel_row = start_row + offset
        values = [
            offset + 1,
            row.get("工事項目", ""),
            row.get("仕様", ""),
            row.get("数量", ""),
            row.get("単位", ""),
            row.get("単価", ""),
            row.get("金額", ""),
            row.get("備考", ""),
        ]
        for col, value in enumerate(values, start=1):
            cell = ws.cell(excel_row, col)
            if not isinstance(cell, MergedCell):
                cell.value = value
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


def _remove_non_template_sheets(wb: Workbook):
    keep = {QUOTE_SHEET, SUMMARY_SHEET, DETAIL_SHEET_1}
    for ws in list(wb.worksheets):
        if ws.title not in keep:
            wb.remove(ws)


def _clear_detail_sheet(ws):
    _set_value(ws, "A4", "")
    _clear_table_values(ws, TEMPLATE_DETAIL_START_ROW, TEMPLATE_DETAIL_END_ROW)
    _set_value(ws, "G27", None)


def _detail_sheet_title(index: int) -> str:
    return f"工事内容明細{index}"


def _write_detail_pages(wb: Workbook, detail_rows: List[Dict], vendor_name: str) -> List[Dict]:
    base_ws = _safe_sheet(wb, DETAIL_SHEET_1)
    capacity = TEMPLATE_DETAIL_END_ROW - TEMPLATE_DETAIL_START_ROW + 1
    if capacity <= 0:
        return detail_rows

    page_count = max(1, (len(detail_rows) + capacity - 1) // capacity)
    for idx in range(2, page_count + 1):
        title = _detail_sheet_title(idx)
        if title not in wb.sheetnames:
            new_ws = wb.copy_worksheet(base_ws)
            new_ws.title = title
            _set_value(new_ws, "A1", f"工　事　内　容　明　細　{idx}")

    for idx in range(1, page_count + 1):
        ws = _safe_sheet(wb, _detail_sheet_title(idx))
        _clear_detail_sheet(ws)
        _set_value(ws, "A4", f"【 {vendor_name} 明細 】")
        start = (idx - 1) * capacity
        page_rows = detail_rows[start:start + capacity]
        _write_template_table(ws, page_rows, TEMPLATE_DETAIL_START_ROW, TEMPLATE_DETAIL_END_ROW)
        detail_total = int(round(sum(parse_money(row.get("金額")) or 0 for row in page_rows)))
        _set_value(ws, "G27", detail_total)

    return []


def _write_quote_header(ws, metadata: Dict, subtotal: int):
    tax = int(round(subtotal * 0.10))
    total = subtotal + tax
    _set_value(ws, "H5", metadata.get("見積日", datetime.now().strftime("%Y年%m月%d日")))
    _set_value(ws, "B8", metadata.get("宛名", metadata.get("顧客名", "")))
    _set_value(ws, "C23", metadata.get("工事名称", metadata.get("件名", "")))
    _set_value(ws, "C24", metadata.get("工事場所", ""))
    _set_value(ws, "C25", metadata.get("工事期間", ""))
    _set_value(ws, "C26", metadata.get("支払条件", "ご相談の上"))
    _set_value(ws, "C27", metadata.get("有効期限", "お打ち合わせの上"))
    _set_value(ws, "C28", metadata.get("見積担当", "中村哲也"))
    _set_value(ws, "D16", total)


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
    _remove_non_template_sheets(wb)
    ws_quote = _safe_sheet(wb, QUOTE_SHEET)
    ws_summary = _safe_sheet(wb, SUMMARY_SHEET)

    _write_quote_header(ws_quote, metadata, subtotal)
    _write_template_table(ws_summary, summary_rows, TEMPLATE_SUMMARY_START_ROW, TEMPLATE_SUMMARY_END_ROW)
    _set_value(ws_summary, "F26", subtotal)
    _write_detail_pages(wb, detail_rows, selected_vendor)

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
