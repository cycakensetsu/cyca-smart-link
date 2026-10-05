"""横型見積書：Numbers/Excel互換テンプレートへ見積データを流し込む。

本番フローと検証から共通利用する出力処理です。
既存ファイルや既存テンプレートには書き込みません。

方針:
- Numbers由来テンプレートは使わない。
- 表紙・工事品目集計・明細を、印刷ページごとのシートへ分ける。
- 各区画はA4横1ページに収まり、印刷順は上から下へ進む。
- pandas.to_excel によるテンプレート全体の再生成はしない。
- 印刷対象のページだけを出力し、空の明細ページや確認用データは含めない。
- 明細金額から表紙金額まで数式で連動し、Numbers/Excelで編集後も再計算できる。
- 「書き出しの概要」「シート1 - 表◯」系の分解シートは作らない・許さない。
- 元テンプレートには絶対に上書きしない（別名保存＋バイト比較で検証）。
"""

from __future__ import annotations

import re
from copy import copy, deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from markup_rules import line_amount

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.pagebreak import Break, RowBreak

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BASE_DIR / "templates" / "test_cyca_estimate_single_sheet_template.xlsx"
OUTPUT_DIR = BASE_DIR / "output" / "template_fill_test_v2"

QUOTE_SHEET = "見積書"
SUMMARY_SHEET = "工事品目集計"
DETAIL_PAGE_SHEET_PREFIX = "明細"
DETAIL_SHEET = "明細データ"

TEMPLATE_FILL_TEST_V2_NAME = "横型：Numbers / Excel互換テンプレート流し込み"

# Mac(Numbers)/Windows(Excel) 双方で崩れにくい標準フォント。
FONT_NAME = "游ゴシック"

# ブランドカラー（ロゴの紺）
BRAND_NAVY = "1F3557"
BRAND_RULE = "8A97AB"
BRAND_TINT = "EDF1F6"
LOGO_PATH = BASE_DIR / "assets" / "cyca_logo.png"

# 自社情報（正規テンプレート準拠）
COMPANY = {
    "name": "彩架建設株式会社",
    "zip": "〒839-0841",
    "address": "福岡県久留米市御井旗崎1丁目7-41",
    "tel": "TEL 0942-65-3300",
    "fax": "FAX 0942-65-3301",
    "ceo": "代表取締役　中村 哲也",
    "reg": "T3290001074771",
}

# 見積書シートの固定レイアウト（1シート・単一テーブル）
# 列: A=No. B=工事品目 C=仕様 D=数量 E=単位 F=単価 G=金額 H=備考
COL_NO, COL_ITEM, COL_SPEC, COL_QTY, COL_UNIT, COL_PRICE, COL_AMOUNT, COL_REMARK = 1, 2, 3, 4, 5, 6, 7, 8
LAST_COL = 8

TITLE_ROW = 2             # 御見積書（紺バナー）
CUSTOMER_ROW = 5          # A:C 宛名 / D 御中
GREET_ROW = 7             # A:D ごあいさつ
ISSUE_DATE_ROW = 7        # F ラベル / G:H 値（ロゴの下）
REG_ROW = 8               # F ラベル / G:H 値
AMOUNT_LABEL_ROW = 13     # A:B ラベル / C:E 御見積金額（税込）
AMOUNT_END_ROW = 14
INFO_START_ROW = 25       # 工事名称 / 工事場所 / 工事期間 / 支払条件 / 有効期限 / 見積担当
INFO_LABELS = ["工事名称", "工事場所", "工事期間", "支払条件", "有効期限", "見積担当"]
INFO_END_ROW = INFO_START_ROW + len(INFO_LABELS) - 1
COMPANY_ROW = 25          # E:H 自社情報（25〜28）
COVER_END_ROW = 32

# 2ページ目：工事品目まとめ（必ず1ページ）
SUMMARY_BAND_ROW = 34
SUMMARY_HEADER_ROW = 35
SUMMARY_ITEM_START_ROW = 36
SUMMARY_ITEM_MAX_ROWS = 16
SUMMARY_ITEM_END_ROW = SUMMARY_ITEM_START_ROW + SUMMARY_ITEM_MAX_ROWS - 1
SUMMARY_NOTE_ROW = 53
SUMMARY_SUBTOTAL_ROW = 55
SUMMARY_DISCOUNT_ROW = 56
SUMMARY_NET_ROW = 57
SUMMARY_TAX_ROW = 58
SUMMARY_TOTAL_ROW = 59
SUMMARY_PAGE_END_ROW = 60

# 3ページ目以降：明細。各ページを同じ高さの固定ブロックにする。
DETAIL_PAGE_START_ROW = 62
DETAIL_PAGE_DATA_ROWS = 20
DETAIL_PAGE_BLOCK_ROWS = 24  # バンド1 + 見出し1 + 明細20 + 注記1 + ページ合計1
MAX_DETAIL_PAGES = 8
MIN_DETAIL_PAGES = 1
ITEM_MAX_ROWS = DETAIL_PAGE_DATA_ROWS * MAX_DETAIL_PAGES
WORK_BAND_ROW = DETAIL_PAGE_START_ROW
ITEM_HEADER_ROW = DETAIL_PAGE_START_ROW + 1
ITEM_START_ROW = DETAIL_PAGE_START_ROW + 2
ITEM_END_ROW = DETAIL_PAGE_START_ROW + DETAIL_PAGE_BLOCK_ROWS * MAX_DETAIL_PAGES - 1
REMARK_LABEL_ROW = ITEM_END_ROW

ITEM_FONT_SIZE = 9
ITEM_ROW_HEIGHT = 21

MONEY_FORMAT = "#,##0"
RATE_FORMAT = "#,##0.00"
YEN_FORMAT = '"¥"#,##0'

DISALLOWED_SHEET_NAMES = {"書き出しの概要"}
DISALLOWED_SHEET_PREFIXES = ("シート1 -", "シート1-")


# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------

@dataclass
class LineItem:
    name: str
    spec: str = ""
    qty: float = 0
    unit: str = ""
    unit_price: float = 0
    amount: int = 0
    remark: str = ""


@dataclass
class Category:
    name: str
    items: List[LineItem] = field(default_factory=list)

    @property
    def subtotal(self) -> int:
        return int(round(sum(i.amount for i in self.items)))


@dataclass
class Estimate:
    vendor: str            # 見積元
    customer: str          # 宛名
    project_name: str      # 工事名
    issue_date: str        # 発行日（見積日）
    valid_days: str        # 有効期限
    categories: List[Category] = field(default_factory=list)
    discount: int = 0      # 値引き（マイナス値で保持）
    tax_rate: float = 0.10

    @property
    def subtotal(self) -> int:
        return int(round(sum(c.subtotal for c in self.categories)))

    @property
    def net_total(self) -> int:
        # 税抜合計 = 小計 + 値引き（値引きは負数）
        return int(round(self.subtotal + self.discount))

    @property
    def tax(self) -> int:
        # 消費税は税抜合計に対して計算（切り捨て）
        return int(self.net_total * self.tax_rate)

    @property
    def grand_total(self) -> int:
        return int(round(self.net_total + self.tax))


# ---------------------------------------------------------------------------
# テストデータ（物置工事）
# ---------------------------------------------------------------------------

def default_test_estimate() -> Estimate:
    return Estimate(
        vendor="株式会社ティズプラス",
        customer="株式会社 貞清工務店 御中",
        project_name="物置工事",
        issue_date="2026/7/18",
        valid_days="30日",
        discount=-5192,
        tax_rate=0.10,
        categories=[
            Category("仮設工事", [
                LineItem("水盛り・やり方", "", 1, "式", 15000, 15000),
                LineItem("清掃・養生・片付け", "", 1, "式", 20000, 20000),
            ]),
            Category("基礎ブロック工事", [
                LineItem("ブロック積み", "", 9, "m", 8000, 72000),
            ]),
            Category("物置工事", [
                LineItem("FF-2618HDL-2", "定価604,000円", 1, "式", 440920, 440920),
                LineItem("組立費", "", 1, "式", 160000, 160000),
            ]),
            Category("諸経費・経費", [
                LineItem("諸経費・経費", "", 1, "式", 70000, 70000),
            ]),
        ],
    )


# ---------------------------------------------------------------------------
# フラット行 ⇔ Estimate 変換（Streamlit の手入力/補正用）
# ---------------------------------------------------------------------------

DETAIL_COLUMNS = ["工事分類", "工事項目", "仕様", "数量", "単位", "単価", "金額", "備考"]


def estimate_to_rows(estimate: Estimate) -> List[Dict]:
    rows: List[Dict] = []
    for cat in estimate.categories:
        for item in cat.items:
            rows.append({
                "工事分類": cat.name,
                "工事項目": item.name,
                "仕様": item.spec,
                "数量": item.qty,
                "単位": item.unit,
                "単価": item.unit_price or 0,
                "金額": int(round(item.amount or 0)),
                "備考": item.remark,
            })
    return rows


def _to_number(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("円", "").replace("¥", "")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def rows_to_estimate(
    rows: List[Dict],
    *,
    vendor: str,
    customer: str,
    project_name: str,
    issue_date: str,
    valid_days: str,
    discount: int,
    tax_rate: float = 0.10,
) -> Estimate:
    categories: List[Category] = []
    index: Dict[str, Category] = {}
    for row in rows:
        name = str(row.get("工事項目", "")).strip()
        cat_name = str(row.get("工事分類", "")).strip() or "工事"
        if not name:
            continue
        qty = _to_number(row.get("数量"))
        unit_price = int(round(_to_number(row.get("単価"))))
        amount_raw = _to_number(row.get("金額"))
        amount = int(round(amount_raw if amount_raw else qty * unit_price))
        item = LineItem(
            name=name,
            spec=str(row.get("仕様", "") or ""),
            qty=int(qty) if float(qty).is_integer() else qty,
            unit=str(row.get("単位", "") or ""),
            unit_price=unit_price,
            amount=amount,
            remark=str(row.get("備考", "") or ""),
        )
        if cat_name not in index:
            cat = Category(cat_name, [])
            index[cat_name] = cat
            categories.append(cat)
        index[cat_name].items.append(item)
    return Estimate(
        vendor=vendor,
        customer=customer,
        project_name=project_name,
        issue_date=issue_date,
        valid_days=valid_days,
        categories=categories,
        discount=int(round(discount)),
        tax_rate=tax_rate,
    )


# ---------------------------------------------------------------------------
# セル書き込みヘルパー
# ---------------------------------------------------------------------------

def _set(ws, row: int, col: int, value, *, number_format: Optional[str] = None):
    cell = ws.cell(row=row, column=col)
    if isinstance(cell, MergedCell):
        return
    cell.value = value
    if number_format:
        cell.number_format = number_format


# ---------------------------------------------------------------------------
# テンプレート新規作成（openpyxl）
# ---------------------------------------------------------------------------

def build_single_sheet_template(path: Path = TEMPLATE_PATH) -> Path:
    """表紙・工事品目まとめ・明細をA4横の固定ページで持つ雛形を作る。"""
    wb = Workbook()
    ws = wb.active
    ws.title = QUOTE_SHEET
    ws.sheet_view.showGridLines = False

    widths = {"A": 4.5, "B": 34, "C": 30, "D": 8, "E": 6, "F": 14, "G": 16, "H": 32}
    for col, width in widths.items():
        ws.column_dimensions[col].width = width

    base_font = Font(name=FONT_NAME, size=10)
    navy_font = Font(name=FONT_NAME, size=10, bold=True, color=BRAND_NAVY)
    white_header_font = Font(name=FONT_NAME, size=10, bold=True, color="FFFFFF")
    item_font = Font(name=FONT_NAME, size=ITEM_FONT_SIZE)
    center = Alignment(horizontal="center", vertical="center")
    left = Alignment(horizontal="left", vertical="center", wrap_text=True)
    right = Alignment(horizontal="right", vertical="center", shrink_to_fit=True)
    rule = Side(style="thin", color=BRAND_RULE)
    medium = Side(style="medium", color=BRAND_NAVY)
    cell_border = Border(left=rule, right=rule, top=rule, bottom=rule)
    navy_box = Border(left=medium, right=medium, top=medium, bottom=medium)
    navy_fill = PatternFill("solid", fgColor=BRAND_NAVY)
    tint_fill = PatternFill("solid", fgColor=BRAND_TINT)

    def merge(r1, c1, r2, c2):
        ws.merge_cells(start_row=r1, start_column=c1, end_row=r2, end_column=c2)
        return ws.cell(r1, c1)

    def style_table_header(row: int, labels: List[str]) -> None:
        for col, text in enumerate(labels, start=1):
            cell = ws.cell(row, col, text)
            cell.font = white_header_font
            cell.fill = navy_fill
            cell.alignment = center
            cell.border = Border(left=rule, right=rule, top=medium, bottom=medium)
        ws.row_dimensions[row].height = 22

    def style_grid_row(row: int, *, height: int = 19) -> None:
        for col in range(1, LAST_COL + 1):
            cell = ws.cell(row, col)
            cell.font = item_font
            cell.border = cell_border
            if col in (COL_NO, COL_QTY, COL_UNIT):
                cell.alignment = center
            elif col in (COL_PRICE, COL_AMOUNT):
                cell.alignment = right
                cell.number_format = MONEY_FORMAT
            else:
                cell.alignment = left
        ws.row_dimensions[row].height = height

    # 1ページ目：表紙
    title = merge(TITLE_ROW, 1, TITLE_ROW, LAST_COL)
    title.value = "御　見　積　書"
    title.font = Font(name=FONT_NAME, size=24, bold=True, color="FFFFFF")
    title.fill = navy_fill
    title.alignment = center
    ws.row_dimensions[TITLE_ROW].height = 34

    if LOGO_PATH.exists():
        try:
            image = XLImage(str(LOGO_PATH))
            image.width, image.height = 186, 67
            ws.add_image(image, "F3")
        except Exception:
            pass

    customer = merge(CUSTOMER_ROW, COL_NO, CUSTOMER_ROW, COL_SPEC)
    customer.font = Font(name=FONT_NAME, size=15)
    customer.alignment = Alignment(horizontal="left", vertical="center")
    customer.border = Border(bottom=medium)
    honorific = ws.cell(CUSTOMER_ROW, COL_QTY, "御中")
    honorific.font = Font(name=FONT_NAME, size=12)
    honorific.alignment = left

    greeting = merge(GREET_ROW, COL_NO, GREET_ROW, COL_QTY)
    greeting.value = "下記の通りお見積もり申し上げます。"
    greeting.font = base_font
    greeting.alignment = left

    for row, label in ((ISSUE_DATE_ROW, "見積日"), (REG_ROW, "登録番号")):
        label_cell = ws.cell(row, COL_PRICE, label)
        label_cell.font = navy_font
        label_cell.alignment = left
        value_cell = merge(row, COL_AMOUNT, row, COL_REMARK)
        value_cell.font = base_font
        value_cell.alignment = left
        value_cell.border = Border(bottom=rule)
    ws.cell(REG_ROW, COL_AMOUNT).value = COMPANY["reg"]

    amount_label = merge(AMOUNT_LABEL_ROW, COL_NO, AMOUNT_END_ROW, COL_ITEM)
    amount_label.value = "御 見 積 金 額"
    amount_label.font = Font(name=FONT_NAME, size=14, bold=True, color="FFFFFF")
    amount_label.fill = navy_fill
    amount_label.alignment = center
    amount_value = merge(AMOUNT_LABEL_ROW, COL_SPEC, AMOUNT_END_ROW, COL_UNIT)
    amount_value.font = Font(name=FONT_NAME, size=22, bold=True, color=BRAND_NAVY)
    amount_value.alignment = right
    amount_value.number_format = YEN_FORMAT
    amount_value.border = navy_box
    tax_note = merge(AMOUNT_END_ROW + 1, COL_SPEC, AMOUNT_END_ROW + 1, COL_UNIT)
    tax_note.value = "（消費税込）"
    tax_note.font = Font(name=FONT_NAME, size=8, color="666666")
    tax_note.alignment = Alignment(horizontal="right", vertical="center")

    for offset, label in enumerate(INFO_LABELS):
        row = INFO_START_ROW + offset
        label_cell = merge(row, COL_NO, row, COL_ITEM)
        label_cell.value = label
        label_cell.font = navy_font
        label_cell.alignment = Alignment(horizontal="right", vertical="center", indent=1)
        value_cell = merge(row, COL_SPEC, row, COL_QTY)
        value_cell.font = base_font
        value_cell.alignment = Alignment(horizontal="left", vertical="center", shrink_to_fit=True)
        value_cell.border = Border(bottom=rule)
        ws.row_dimensions[row].height = 18
    ws.cell(INFO_START_ROW + 3, COL_SPEC).value = "ご相談の上"
    ws.cell(INFO_START_ROW + 5, COL_SPEC).value = "中村 哲也"

    company_lines = [
        (COMPANY["name"], Font(name=FONT_NAME, size=13, bold=True, color=BRAND_NAVY)),
        (f'{COMPANY["zip"]} {COMPANY["address"]}', Font(name=FONT_NAME, size=9)),
        (f'{COMPANY["tel"]}　{COMPANY["fax"]}', Font(name=FONT_NAME, size=9)),
        (COMPANY["ceo"], Font(name=FONT_NAME, size=9)),
    ]
    for offset, (text, font) in enumerate(company_lines):
        cell = merge(COMPANY_ROW + offset, COL_UNIT, COMPANY_ROW + offset, COL_REMARK)
        cell.value = text
        cell.font = font
        cell.alignment = left

    for row, height in {
        1: 10, 3: 8, 4: 18, 5: 25, 6: 18, 7: 20, 8: 16, 9: 18,
        10: 18, 11: 18, 12: 16, 13: 22, 14: 22, 15: 14,
        16: 12, 17: 12, 18: 12, 19: 12, 20: 12, 21: 12, 22: 12,
        23: 12, 24: 12, 31: 12, 32: 12,
    }.items():
        ws.row_dimensions[row].height = height

    # 2ページ目：工事品目まとめ
    summary_band = merge(SUMMARY_BAND_ROW, 1, SUMMARY_BAND_ROW, LAST_COL)
    summary_band.value = "工 事 品 目 ま と め"
    summary_band.font = Font(name=FONT_NAME, size=12, bold=True, color="FFFFFF")
    summary_band.fill = navy_fill
    summary_band.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[SUMMARY_BAND_ROW].height = 24
    style_table_header(
        SUMMARY_HEADER_ROW,
        ["No.", "工事品目", "仕様", "数量", "単位", "単価", "金額", "備考"],
    )
    for row in range(SUMMARY_ITEM_START_ROW, SUMMARY_ITEM_END_ROW + 1):
        style_grid_row(row, height=20)

    summary_note = merge(SUMMARY_NOTE_ROW, 1, SUMMARY_NOTE_ROW, 4)
    summary_note.value = "※工事内容明細には消費税が含まれておりません。"
    summary_note.font = Font(name=FONT_NAME, size=8, color="666666")
    summary_note.alignment = left

    for row, label, emphasize in (
        (SUMMARY_SUBTOTAL_ROW, "小　　計", False),
        (SUMMARY_DISCOUNT_ROW, "値 引 き", False),
        (SUMMARY_NET_ROW, "税抜合計", False),
        (SUMMARY_TAX_ROW, "消 費 税", False),
        (SUMMARY_TOTAL_ROW, "税込合計", True),
    ):
        label_cell = merge(row, COL_UNIT, row, COL_PRICE)
        label_cell.value = label
        label_cell.alignment = center
        label_cell.fill = navy_fill if emphasize else tint_fill
        label_cell.font = Font(
            name=FONT_NAME,
            size=11 if emphasize else 10,
            bold=True,
            color="FFFFFF" if emphasize else BRAND_NAVY,
        )
        label_cell.border = navy_box if emphasize else cell_border
        value_cell = ws.cell(row, COL_AMOUNT)
        value_cell.alignment = right
        value_cell.number_format = YEN_FORMAT if emphasize else MONEY_FORMAT
        value_cell.font = Font(name=FONT_NAME, size=11 if emphasize else 10, bold=emphasize, color=BRAND_NAVY)
        value_cell.border = navy_box if emphasize else cell_border
        ws.row_dimensions[row].height = 22 if emphasize else 19
    ws.row_dimensions[SUMMARY_PAGE_END_ROW].height = 12

    # 3ページ目以降：固定高さの明細ページを最大8ページ分用意する。
    for page_index in range(MAX_DETAIL_PAGES):
        page_start = DETAIL_PAGE_START_ROW + page_index * DETAIL_PAGE_BLOCK_ROWS
        band = merge(page_start, 1, page_start, LAST_COL)
        band.value = "工 事 内 容 明 細"
        band.font = Font(name=FONT_NAME, size=11, bold=True, color="FFFFFF")
        band.fill = navy_fill
        band.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        ws.row_dimensions[page_start].height = 22
        style_table_header(
            page_start + 1,
            ["No.", "工事品目", "仕様", "数量", "単位", "単価", "金額", "備考"],
        )
        for row in range(page_start + 2, page_start + 2 + DETAIL_PAGE_DATA_ROWS):
            style_grid_row(row, height=19)

        note_row = page_start + DETAIL_PAGE_BLOCK_ROWS - 2
        note = merge(note_row, 1, note_row, 5)
        note.value = "※工事内容明細には消費税が含まれておりません。"
        note.font = Font(name=FONT_NAME, size=8, color="666666")
        note.alignment = left
        note.border = Border(top=rule)
        ws.row_dimensions[note_row].height = 18

        total_row = page_start + DETAIL_PAGE_BLOCK_ROWS - 1
        for col in range(1, LAST_COL + 1):
            ws.cell(total_row, col).border = Border(top=rule, bottom=medium)
        total_label = ws.cell(total_row, COL_PRICE, "合　計")
        total_label.font = navy_font
        total_label.alignment = center
        total_value = ws.cell(total_row, COL_AMOUNT)
        total_value.font = Font(name=FONT_NAME, size=10, bold=True)
        total_value.alignment = right
        total_value.number_format = MONEY_FORMAT
        ws.row_dimensions[total_row].height = 22

    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins = PageMargins(left=0.32, right=0.32, top=0.35, bottom=0.35, header=0.18, footer=0.18)
    ws.print_area = f"A1:H{ITEM_END_ROW}"
    ws.row_breaks = RowBreak()
    ws.row_breaks.append(Break(id=COVER_END_ROW))
    ws.row_breaks.append(Break(id=SUMMARY_PAGE_END_ROW))
    for page_index in range(MAX_DETAIL_PAGES - 1):
        page_end = DETAIL_PAGE_START_ROW + (page_index + 1) * DETAIL_PAGE_BLOCK_ROWS - 1
        ws.row_breaks.append(Break(id=page_end))

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def ensure_template(path: Path = TEMPLATE_PATH) -> Path:
    if not path.exists():
        build_single_sheet_template(path)
    return path


# ---------------------------------------------------------------------------
# 流し込み
# ---------------------------------------------------------------------------

def _build_detail_rows(estimate: Estimate) -> List[Dict]:
    """分類見出し・明細・分類小計を、印刷用の論理行へ展開する。"""
    rows: List[Dict] = []
    no = 0
    for category_index, category in enumerate(estimate.categories, start=1):
        rows.append({"kind": "category", "index": category_index, "category": category})
        for item in category.items:
            no += 1
            rows.append({"kind": "item", "no": no, "item": item})
        rows.append({"kind": "subtotal", "category": category})
    return rows


def _paginate_detail_rows(rows: List[Dict]) -> List[List[Dict]]:
    """固定20行へ分割し、分類見出し・小計だけが孤立する改ページを避ける。"""
    if not rows:
        return [[]]
    pages: List[List[Dict]] = []
    cursor = 0
    while cursor < len(rows):
        take = min(DETAIL_PAGE_DATA_ROWS, len(rows) - cursor)
        if cursor + take < len(rows):
            if rows[cursor + take - 1]["kind"] == "category" and take > 1:
                take -= 1
            if rows[cursor + take]["kind"] == "subtotal" and take > 1:
                take -= 1
        pages.append(rows[cursor:cursor + take])
        cursor += take
    return pages


def _write_quote_sheet_legacy(ws, estimate: Estimate) -> int:
    """見積書シートへ値だけを書き込む。書き込んだ明細行数（テーブル使用行数）を返す。"""
    # テンプレート側に「御中」があるので、宛名末尾の敬称は取り除いて二重表記を防ぐ。
    customer = re.sub(r"[\s　]*(御中|様|殿)[\s　]*$", "", estimate.customer or "")
    _set(ws, CUSTOMER_ROW, COL_NO, customer)
    honorific = re.search(r"(御中|様|殿)[\s　]*$", estimate.customer or "")
    _set(ws, CUSTOMER_ROW, COL_QTY, honorific.group(1) if honorific else "御中")
    _set(ws, ISSUE_DATE_ROW, COL_AMOUNT, estimate.issue_date)
    _set(ws, AMOUNT_LABEL_ROW, COL_SPEC, estimate.grand_total, number_format=YEN_FORMAT)

    # 工事情報（工事名称 / 工事場所 / 工事期間 / 支払条件 / 有効期限 / 見積担当）
    _set(ws, INFO_START_ROW, COL_SPEC, estimate.project_name)
    _set(ws, INFO_START_ROW + 4, COL_SPEC, estimate.valid_days)

    cat_font = Font(name=FONT_NAME, size=10, bold=True, color=BRAND_NAVY)
    cat_fill = PatternFill("solid", fgColor=BRAND_TINT)
    sub_font = Font(name=FONT_NAME, size=10, bold=True)

    # 明細を書き出す。ページが変わる位置では見出し行を「実データとして」書き込み、
    # 明示的な改ページを入れる。Numbers は Excel の印刷タイトル行設定を無視するため、
    # 印刷設定に頼らずセルの中身として見出しを持たせないと2枚目が見出し無しになる。
    total_rows = sum((2 if len(estimate.categories) > 1 else 0) + len(c.items)
                     for c in estimate.categories)
    plan = _plan_pages(total_rows)
    state = {"row": ITEM_START_ROW, "on_page": 0, "page": 0}

    def emit(fill_row) -> None:
        if state["on_page"] >= plan[state["page"]] and state["page"] + 1 < len(plan):
            state["page"] += 1
            state["on_page"] = 0
            _copy_item_header(ws, state["row"])
            ws.row_breaks.append(Break(id=state["row"] - 1))
            state["row"] += 1
        fill_row(state["row"])
        state["row"] += 1
        state["on_page"] += 1

    no = 0
    multi = len(estimate.categories) > 1
    for idx, cat in enumerate(estimate.categories, start=1):
        if multi:
            def write_category(r, idx=idx, cat=cat):
                # 分類見出し行（複数業者・複数工種のときだけ出す）
                _set(ws, r, COL_ITEM, f"{idx}. {cat.name}")
                for col in range(1, LAST_COL + 1):
                    c = ws.cell(r, col)
                    if not isinstance(c, MergedCell):
                        c.fill = cat_fill
                ws.cell(r, COL_ITEM).font = cat_font
            emit(write_category)
        for item in cat.items:
            no += 1

            def write_item(r, item=item, no=no):
                _set(ws, r, COL_NO, no)
                _set(ws, r, COL_ITEM, item.name)
                _set(ws, r, COL_SPEC, item.spec)
                _set(ws, r, COL_QTY, item.qty)
                _set(ws, r, COL_UNIT, item.unit)
                _set(ws, r, COL_PRICE, item.unit_price, number_format=MONEY_FORMAT if float(item.unit_price).is_integer() else RATE_FORMAT)
                _set(ws, r, COL_AMOUNT, int(round(item.amount)), number_format=MONEY_FORMAT)
                _set(ws, r, COL_REMARK, item.remark)
            emit(write_item)
        if multi:
            def write_subtotal(r, cat=cat):
                # 分類小計行
                _set(ws, r, COL_PRICE, "小計")
                ws.cell(r, COL_PRICE).alignment = Alignment(horizontal="right", vertical="center")
                ws.cell(r, COL_PRICE).font = sub_font
                _set(ws, r, COL_AMOUNT, cat.subtotal, number_format=MONEY_FORMAT)
                ws.cell(r, COL_AMOUNT).font = sub_font
            emit(write_subtotal)

    row = state["row"]
    used_rows = row - ITEM_START_ROW

    # 使わなかった明細行は隠して、A4 1枚に収まるようにする。
    # （行を削除すると集計セルの位置がずれて検証できなくなるため、非表示にする）
    last_used = row - 1
    if last_used >= ITEM_START_ROW:
        for col in range(1, LAST_COL + 1):
            c = ws.cell(last_used, col)
            if not isinstance(c, MergedCell):
                old = c.border
                c.border = Border(left=old.left, right=old.right, top=old.top,
                                  bottom=Side(style="medium", color=BRAND_NAVY))
    for hidden_row in range(max(row, ITEM_START_ROW), ITEM_END_ROW + 1):
        ws.row_dimensions[hidden_row].hidden = True

    # 集計ブロック
    _set(ws, SUMMARY_SUBTOTAL_ROW, COL_AMOUNT, estimate.subtotal, number_format=MONEY_FORMAT)
    _set(ws, SUMMARY_DISCOUNT_ROW, COL_AMOUNT, estimate.discount, number_format=MONEY_FORMAT)
    _set(ws, SUMMARY_NET_ROW, COL_AMOUNT, estimate.net_total, number_format=MONEY_FORMAT)
    _set(ws, SUMMARY_TAX_ROW, COL_AMOUNT, estimate.tax, number_format=MONEY_FORMAT)
    _set(ws, SUMMARY_TOTAL_ROW, COL_AMOUNT, estimate.grand_total, number_format=YEN_FORMAT)

    # 複数ページになっても体裁が崩れないようにする。
    # ・明細ヘッダー行は毎ページの先頭で繰り返す（2枚目以降が見出しなしの表にならない）
    # ・フッターに工事名とページ番号を入れて、続きものだと分かるようにする
    ws.oddFooter.left.text = f"{COMPANY['name']}　{estimate.project_name}"
    ws.oddFooter.left.size = 8
    ws.oddFooter.left.color = "808080"
    ws.oddFooter.right.text = "Page &P / &N"
    ws.oddFooter.right.size = 8
    ws.oddFooter.right.color = "808080"

    # 横幅は常にA4横1ページに収め、縦方向は明示改ページに従う。
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    return used_rows


def _write_quote_sheet(ws, estimate: Estimate) -> int:
    """表紙・工事品目まとめ・明細を固定ページへ流し込む。"""
    customer = re.sub(r"[\s　]*(御中|様|殿)[\s　]*$", "", estimate.customer or "")
    _set(ws, CUSTOMER_ROW, COL_NO, customer)
    honorific = re.search(r"(御中|様|殿)[\s　]*$", estimate.customer or "")
    _set(ws, CUSTOMER_ROW, COL_QTY, honorific.group(1) if honorific else "御中")
    _set(ws, ISSUE_DATE_ROW, COL_AMOUNT, estimate.issue_date)
    _set(ws, AMOUNT_LABEL_ROW, COL_SPEC, estimate.grand_total, number_format=YEN_FORMAT)
    _set(ws, INFO_START_ROW, COL_SPEC, estimate.project_name)
    _set(ws, INFO_START_ROW + 4, COL_SPEC, estimate.valid_days)

    # 2ページ目：工事項目（分類）だけを1ページにまとめる。
    for row in range(SUMMARY_ITEM_START_ROW, SUMMARY_ITEM_END_ROW + 1):
        for col in range(1, LAST_COL + 1):
            _set(ws, row, col, None)
    for index, category in enumerate(estimate.categories, start=1):
        row = SUMMARY_ITEM_START_ROW + index - 1
        _set(ws, row, COL_NO, index)
        _set(ws, row, COL_ITEM, category.name)
        _set(ws, row, COL_SPEC, "工事一式")
        _set(ws, row, COL_QTY, 1)
        _set(ws, row, COL_UNIT, "式")
        _set(ws, row, COL_AMOUNT, category.subtotal, number_format=MONEY_FORMAT)

    _set(ws, SUMMARY_SUBTOTAL_ROW, COL_AMOUNT, estimate.subtotal, number_format=MONEY_FORMAT)
    _set(ws, SUMMARY_DISCOUNT_ROW, COL_AMOUNT, estimate.discount, number_format=MONEY_FORMAT)
    _set(ws, SUMMARY_NET_ROW, COL_AMOUNT, estimate.net_total, number_format=MONEY_FORMAT)
    _set(ws, SUMMARY_TAX_ROW, COL_AMOUNT, estimate.tax, number_format=MONEY_FORMAT)
    _set(ws, SUMMARY_TOTAL_ROW, COL_AMOUNT, estimate.grand_total, number_format=YEN_FORMAT)

    # 3ページ目以降：20行固定の明細ページ。未使用行にも罫線を残して、
    # 見本と同じようにA4の紙面を十分に使う。
    detail_rows = _build_detail_rows(estimate)
    pages = _paginate_detail_rows(detail_rows)
    visible_page_count = max(MIN_DETAIL_PAGES, len(pages))
    cat_font = Font(name=FONT_NAME, size=10, bold=True, color=BRAND_NAVY)
    cat_fill = PatternFill("solid", fgColor=BRAND_TINT)
    sub_font = Font(name=FONT_NAME, size=10, bold=True)

    for page_index in range(MAX_DETAIL_PAGES):
        page_start = DETAIL_PAGE_START_ROW + page_index * DETAIL_PAGE_BLOCK_ROWS
        page_end = page_start + DETAIL_PAGE_BLOCK_ROWS - 1
        is_used = page_index < visible_page_count
        for row in range(page_start, page_end + 1):
            ws.row_dimensions[row].hidden = not is_used
        if not is_used:
            continue

        page_amount = 0
        data_start = page_start + 2
        page_rows = pages[page_index] if page_index < len(pages) else []
        for row_offset, logical in enumerate(page_rows):
            row = data_start + row_offset
            kind = logical["kind"]
            if kind == "category":
                category = logical["category"]
                _set(ws, row, COL_ITEM, f'{logical["index"]}. {category.name}')
                for col in range(1, LAST_COL + 1):
                    cell = ws.cell(row, col)
                    if not isinstance(cell, MergedCell):
                        cell.fill = cat_fill
                ws.cell(row, COL_ITEM).font = cat_font
            elif kind == "item":
                item = logical["item"]
                _set(ws, row, COL_NO, logical["no"])
                _set(ws, row, COL_ITEM, item.name)
                _set(ws, row, COL_SPEC, item.spec)
                _set(ws, row, COL_QTY, item.qty)
                _set(ws, row, COL_UNIT, item.unit)
                _set(ws, row, COL_PRICE, item.unit_price, number_format=MONEY_FORMAT if float(item.unit_price).is_integer() else RATE_FORMAT)
                _set(ws, row, COL_AMOUNT, int(round(item.amount)), number_format=MONEY_FORMAT)
                _set(ws, row, COL_REMARK, item.remark)
                page_amount += int(round(item.amount))
            else:
                category = logical["category"]
                _set(ws, row, COL_PRICE, "小計")
                ws.cell(row, COL_PRICE).font = sub_font
                ws.cell(row, COL_PRICE).alignment = Alignment(horizontal="right", vertical="center")
                _set(ws, row, COL_AMOUNT, category.subtotal, number_format=MONEY_FORMAT)
                ws.cell(row, COL_AMOUNT).font = sub_font

        total_row = page_start + DETAIL_PAGE_BLOCK_ROWS - 1
        _set(ws, total_row, COL_AMOUNT, page_amount, number_format=MONEY_FORMAT)

    last_page_end = DETAIL_PAGE_START_ROW + visible_page_count * DETAIL_PAGE_BLOCK_ROWS - 1
    ws.print_area = f"A1:H{last_page_end}"
    ws.row_breaks = RowBreak()
    ws.row_breaks.append(Break(id=COVER_END_ROW))
    ws.row_breaks.append(Break(id=SUMMARY_PAGE_END_ROW))
    for page_index in range(visible_page_count - 1):
        page_end = DETAIL_PAGE_START_ROW + (page_index + 1) * DETAIL_PAGE_BLOCK_ROWS - 1
        ws.row_breaks.append(Break(id=page_end))

    ws.oddFooter.left.text = f"{COMPANY['name']}　{estimate.project_name}"
    ws.oddFooter.left.size = 8
    ws.oddFooter.left.color = "808080"
    ws.oddFooter.right.text = "Page &P / &N"
    ws.oddFooter.right.size = 8
    ws.oddFooter.right.color = "808080"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    return len(detail_rows)


def _split_quote_pages(wb: Workbook) -> None:
    """1シートを下へスクロールする構成で、各区画をA4横1ページの高さに揃える。"""
    source = wb[QUOTE_SHEET]
    # A4横の印刷可能高さ（余白・フッター込み）に余裕を持たせる。
    # Numbers/Excel/LibreOfficeで縮尺の解釈が違っても、合計欄が次ページへ
    # 押し出されない約506ptを基準にする。
    for row in range(16, 25):
        source.row_dimensions[row].height = 20
    source.row_dimensions[31].height = 18
    source.row_dimensions[32].height = 18
    source.row_dimensions[33].height = 2

    for row in range(SUMMARY_ITEM_START_ROW, SUMMARY_ITEM_END_ROW + 1):
        source.row_dimensions[row].height = 18
    source.row_dimensions[52].height = 20
    source.row_dimensions[SUMMARY_NOTE_ROW].height = 20
    source.row_dimensions[54].height = 20
    source.row_dimensions[SUMMARY_PAGE_END_ROW].height = 12
    source.row_dimensions[61].height = 2

    for page_index in range(MAX_DETAIL_PAGES):
        page_start = DETAIL_PAGE_START_ROW + page_index * DETAIL_PAGE_BLOCK_ROWS
        for row in range(page_start + 2, page_start + 2 + DETAIL_PAGE_DATA_ROWS):
            source.row_dimensions[row].height = 21

    source.page_setup.orientation = "landscape"
    source.page_setup.paperSize = source.PAPERSIZE_A4
    source.page_setup.fitToWidth = 1
    source.page_setup.fitToHeight = 0
    source.page_setup.pageOrder = "downThenOver"
    source.sheet_properties.pageSetUpPr.fitToPage = True


def _write_detail_sheet(wb: Workbook, estimate: Estimate):
    """2枚目「明細データ」シートを openpyxl のセル書き込みで作成する。"""
    if DETAIL_SHEET in wb.sheetnames:
        del wb[DETAIL_SHEET]
    ws = wb.create_sheet(DETAIL_SHEET)
    # 確認用の生データは保持するが、見積書と一緒に印刷されないよう非表示にする。
    ws.sheet_state = "hidden"
    ws.sheet_view.showGridLines = False

    thin = Side(style="thin", color=BRAND_RULE)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_fill = PatternFill("solid", fgColor=BRAND_NAVY)
    base_font = Font(name=FONT_NAME, size=10)
    label_font = Font(name=FONT_NAME, size=10, bold=True)

    headers = ["No.", "工事分類", "工事項目", "仕様", "数量", "単位", "単価", "金額", "備考"]
    widths = [4.5, 18, 34, 30, 8, 6, 14, 16, 32]
    for i, w in enumerate(widths):
        ws.column_dimensions[chr(ord("A") + i)].width = w
    for col, text in enumerate(headers, start=1):
        c = ws.cell(1, col, text)
        c.font = Font(name=FONT_NAME, size=10, bold=True, color="FFFFFF")
        c.fill = header_fill
        c.border = border
        c.alignment = Alignment(horizontal="center", vertical="center")

    row = 2
    no = 0
    for cat in estimate.categories:
        for item in cat.items:
            no += 1
            values = [no, cat.name, item.name, item.spec, item.qty, item.unit,
                      item.unit_price, int(round(item.amount)), item.remark]
            for col, value in enumerate(values, start=1):
                c = ws.cell(row, col, value)
                c.font = base_font
                c.border = border
                if col in (5, 7, 8):
                    c.alignment = Alignment(horizontal="right", vertical="center")
                elif col in (1, 6):
                    c.alignment = Alignment(horizontal="center", vertical="center")
                else:
                    c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
                if col in (7, 8):
                    c.number_format = RATE_FORMAT if col == 7 and not float(item.unit_price).is_integer() else MONEY_FORMAT
            ws.row_dimensions[row].height = 20
            row += 1

    # 明細データもA4横1ページ幅に収め、見出し行を各ページで繰り返す。
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_setup.pageOrder = "downThenOver"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins = PageMargins(left=0.4, right=0.4, top=0.5, bottom=0.5)
    ws.print_title_rows = "1:1"
    ws.freeze_panes = "A2"

    row += 1  # 1行空ける
    summary = [
        ("小計", estimate.subtotal),
        ("値引き", estimate.discount),
        ("税抜合計", estimate.net_total),
        ("消費税", estimate.tax),
        ("税込合計", estimate.grand_total),
    ]
    for text, value in summary:
        lc = ws.cell(row, 7, text)
        lc.fill = PatternFill("solid", fgColor=BRAND_NAVY if text == "税込合計" else BRAND_TINT)
        lc.font = Font(
            name=FONT_NAME,
            size=10,
            bold=True,
            color="FFFFFF" if text == "税込合計" else BRAND_NAVY,
        )
        lc.border = border
        lc.alignment = Alignment(horizontal="right", vertical="center")
        vc = ws.cell(row, 8, value)
        vc.font = label_font if text == "税込合計" else base_font
        vc.number_format = MONEY_FORMAT
        vc.border = border
        vc.alignment = Alignment(horizontal="right", vertical="center")
        row += 1


def _formula_number(value) -> str:
    number = _to_number(value)
    if float(number).is_integer():
        return str(int(number))
    return format(number, ".12g")


def _apply_dynamic_formulas(wb: Workbook, estimate: Estimate) -> None:
    """明細の編集が工事品目集計・表紙まで連動する数式を設定する。"""
    ws = wb[QUOTE_SHEET]
    current_category_items: List[int] = []
    category_subtotals: List[int] = []
    item_rows: List[int] = []

    for page_index in range(MAX_DETAIL_PAGES):
        page_start = DETAIL_PAGE_START_ROW + page_index * DETAIL_PAGE_BLOCK_ROWS
        if ws.row_dimensions[page_start].hidden:
            continue
        data_start = page_start + 2
        data_end = data_start + DETAIL_PAGE_DATA_ROWS - 1
        for row in range(data_start, data_end + 1):
            no = ws.cell(row, COL_NO).value
            item_name = ws.cell(row, COL_ITEM).value
            label = ws.cell(row, COL_PRICE).value
            if isinstance(no, (int, float)) and item_name:
                ws.cell(row, COL_AMOUNT).value = f'=IF(COUNT(D{row},F{row})=2,ROUND(D{row}*F{row},0),"")'
                item_rows.append(row)
                ws.cell(row, COL_AMOUNT).number_format = MONEY_FORMAT
                current_category_items.append(row)
            elif label == "小計":
                refs = ",".join(f"G{item_row}" for item_row in current_category_items)
                ws.cell(row, COL_AMOUNT).value = f"=SUM({refs})" if refs else "=0"
                ws.cell(row, COL_AMOUNT).number_format = MONEY_FORMAT
                category_subtotals.append(row)
                current_category_items = []

        # ページ合計はNo.が入った明細行だけを集計し、分類小計を二重計上しない。
        total_row = page_start + DETAIL_PAGE_BLOCK_ROWS - 1
        ws.cell(total_row, COL_AMOUNT).value = (
            f'=SUMIF(A{data_start}:A{data_end},">0",G{data_start}:G{data_end})'
        )
        ws.cell(total_row, COL_AMOUNT).number_format = MONEY_FORMAT

    for index, subtotal_row in enumerate(category_subtotals, start=0):
        summary_row = SUMMARY_ITEM_START_ROW + index
        ws.cell(summary_row, COL_AMOUNT).value = f"=G{subtotal_row}"
        ws.cell(summary_row, COL_AMOUNT).number_format = MONEY_FORMAT

    ws.cell(SUMMARY_SUBTOTAL_ROW, COL_AMOUNT).value = (
        f"=SUM(G{SUMMARY_ITEM_START_ROW}:G{SUMMARY_ITEM_END_ROW})"
    )
    ws.cell(SUMMARY_DISCOUNT_ROW, COL_AMOUNT).value = int(round(estimate.discount))
    ws.cell(SUMMARY_NET_ROW, COL_AMOUNT).value = (
        f"=G{SUMMARY_SUBTOTAL_ROW}+G{SUMMARY_DISCOUNT_ROW}"
    )
    ws.cell(SUMMARY_TAX_ROW, COL_AMOUNT).value = (
        f"=ROUNDDOWN(G{SUMMARY_NET_ROW}*{_formula_number(estimate.tax_rate)},0)"
    )
    ws.cell(SUMMARY_TOTAL_ROW, COL_AMOUNT).value = (
        f"=G{SUMMARY_NET_ROW}+G{SUMMARY_TAX_ROW}"
    )
    ws.cell(SUMMARY_TOTAL_ROW, COL_AMOUNT).number_format = YEN_FORMAT
    ws.cell(AMOUNT_LABEL_ROW, COL_SPEC).value = f"=G{SUMMARY_TOTAL_ROW}"
    ws.cell(AMOUNT_LABEL_ROW, COL_SPEC).number_format = YEN_FORMAT
    ws.page_setup.pageOrder = "downThenOver"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0

    # 非表示の明細データにも同じ計算式を残す。
    raw = wb[DETAIL_SHEET]
    item_count = sum(len(category.items) for category in estimate.categories)
    for row, quote_row in enumerate(item_rows, start=2):
        # 見積書の数量・単価を唯一の編集元にして二つのシートのズレを防ぐ。
        for raw_col, quote_col in ((3, "B"), (4, "C"), (5, "D"), (6, "E"), (7, "F"), (8, "G"), (9, "H")):
            ref = f"'{QUOTE_SHEET}'!{quote_col}{quote_row}"
            raw.cell(row, raw_col).value = f'=IF({ref}="","",{ref})'
    raw_summary_row = item_count + 3
    raw.cell(raw_summary_row, 8).value = f"=SUM(H2:H{item_count + 1})"
    raw.cell(raw_summary_row + 1, 8).value = f"='{QUOTE_SHEET}'!G{SUMMARY_DISCOUNT_ROW}"
    raw.cell(raw_summary_row + 2, 8).value = f"=H{raw_summary_row}+H{raw_summary_row + 1}"
    raw.cell(raw_summary_row + 3, 8).value = (
        f"=ROUNDDOWN(H{raw_summary_row + 2}*{_formula_number(estimate.tax_rate)},0)"
    )
    raw.cell(raw_summary_row + 4, 8).value = f"=H{raw_summary_row + 2}+H{raw_summary_row + 3}"

    wb.calculation.calcMode = "auto"
    wb.calculation.fullCalcOnLoad = True
    wb.calculation.forceFullCalc = True


# ---------------------------------------------------------------------------
# 検証
# ---------------------------------------------------------------------------

@dataclass
class FillResult:
    ok: bool
    errors: List[str]
    warnings: List[str]
    data: Optional[bytes]
    file_name: str
    sheet_names: List[str]
    summary: Dict
    pdf_data: Optional[bytes] = None


def _validate_estimate(estimate: Estimate) -> Tuple[List[str], List[str]]:
    """データそのものの整合性を検証。(errors, warnings) を返す。

    errors はダウンロードをブロックする致命的不整合。
    数量×単価と金額の不一致は出力をブロックする。
    """
    errors: List[str] = []
    warnings: List[str] = []
    detail_sum = int(round(sum(i.amount for c in estimate.categories for i in c.items)))
    if detail_sum != estimate.subtotal:
        errors.append(f"明細合計と小計が一致していません。（明細合計 {detail_sum:,}円 / 小計 {estimate.subtotal:,}円）")
    if estimate.net_total + estimate.tax != estimate.grand_total:
        errors.append(
            f"税抜合計＋消費税が税込合計と一致していません。"
            f"（{estimate.net_total:,} + {estimate.tax:,} ≠ {estimate.grand_total:,}）"
        )
    for idx, item in enumerate(
        (i for c in estimate.categories for i in c.items), start=1
    ):
        qty = _to_number(item.qty)
        if not item.name or not item.unit or qty <= 0:
            errors.append(f"No.{idx} の品名・数量・単位を確認してください。")
        if line_amount(qty, item.unit_price) != int(round(item.amount)):
            errors.append(f"No.{idx}「{item.name}」の数量×単価と金額が一致しません。")
    used = 0
    for cat in estimate.categories:
        used += 1 + len(cat.items) + 1  # 見出し + 明細 + 小計
    if used > ITEM_MAX_ROWS:
        errors.append("明細行がテンプレートの上限（1シート）を超えています。")
    if len(estimate.categories) > SUMMARY_ITEM_MAX_ROWS:
        errors.append(
            f"工事項目がまとめページの上限（{SUMMARY_ITEM_MAX_ROWS}項目）を超えています。"
        )
    return errors, warnings


def _validate_output_workbook(wb: Workbook, estimate: Estimate) -> List[str]:
    """出力ワークブックのシート構成・金額セルを検証。"""
    errors: List[str] = []
    names = wb.sheetnames

    if QUOTE_SHEET not in names:
        errors.append(f"出力検証に失敗しました。「{QUOTE_SHEET}」シートがありません。")
    if DETAIL_SHEET not in names:
        errors.append(f"出力検証に失敗しました。「{DETAIL_SHEET}」シートがありません。")
    for name in names:
        if name in DISALLOWED_SHEET_NAMES or any(name.startswith(p) for p in DISALLOWED_SHEET_PREFIXES):
            errors.append("出力検証に失敗しました。不要なNumbers分解シートが含まれています。")
            break

    if QUOTE_SHEET in names:
        q = wb[QUOTE_SHEET]
        formula_checks = [
            (AMOUNT_LABEL_ROW, COL_SPEC, "表紙金額"),
            (SUMMARY_SUBTOTAL_ROW, COL_AMOUNT, "小計"),
            (SUMMARY_NET_ROW, COL_AMOUNT, "税抜合計"),
            (SUMMARY_TAX_ROW, COL_AMOUNT, "消費税"),
            (SUMMARY_TOTAL_ROW, COL_AMOUNT, "税込合計"),
        ]
        for row, col, label in formula_checks:
            actual = q.cell(row, col).value
            if not (isinstance(actual, str) and actual.startswith("=")):
                errors.append(f"出力検証に失敗しました。{label}に数式がありません。")
        discount = q.cell(SUMMARY_DISCOUNT_ROW, COL_AMOUNT).value
        if int(round(_to_number(discount))) != int(round(estimate.discount)):
            errors.append("出力検証に失敗しました。値引き額が正しくありません。")

    if DETAIL_SHEET in names:
        d = wb[DETAIL_SHEET]
        row = 2
        while d.cell(row, 1).value is not None:
            amount_formula = d.cell(row, 8).value
            if not (isinstance(amount_formula, str) and amount_formula.startswith("=")):
                errors.append(f"出力検証に失敗しました。明細データH{row}に数式がありません。")
                break
            row += 1
    return errors


# ---------------------------------------------------------------------------
# メイン：流し込み実行
# ---------------------------------------------------------------------------

def fill_estimate_v2(
    estimate: Estimate,
    *,
    template_path: Path = TEMPLATE_PATH,
    save_to_disk: bool = True,
    print_ready: bool = True,
) -> FillResult:
    ensure_template(template_path)

    summary = {
        "見積元": estimate.vendor,
        "宛名": estimate.customer,
        "工事名": estimate.project_name,
        "小計": estimate.subtotal,
        "値引き": estimate.discount,
        "税抜合計": estimate.net_total,
        "消費税": estimate.tax,
        "税込合計": estimate.grand_total,
    }

    # 1) データ整合性の検証（致命的な errors があれば流し込まない）
    errors, warnings = _validate_estimate(estimate)

    date_part = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = lambda s: str(s).replace("/", "").replace("\\", "").replace(" ", "").strip()
    file_name = f"CYCA_横型見積書_{safe(estimate.project_name)}_{safe(estimate.vendor)}_{date_part}.xlsx"

    if errors:
        return FillResult(False, errors, warnings, None, file_name, [], summary)

    # 2) テンプレートを読み込み、値だけ書き込む（元テンプレートには絶対に触らない）
    template_before = template_path.read_bytes()
    wb = load_workbook(template_path)
    if wb.sheetnames[0] != QUOTE_SHEET:
        # 想定外のテンプレート構成
        return FillResult(
            False,
            [f"出力検証に失敗しました。テンプレートの1枚目が「{QUOTE_SHEET}」ではありません。"],
            [], None, file_name, list(wb.sheetnames), summary,
        )

    quote_ws = wb[QUOTE_SHEET]
    _write_quote_sheet(quote_ws, estimate)
    _split_quote_pages(wb)
    _write_detail_sheet(wb, estimate)
    _apply_dynamic_formulas(wb, estimate)

    # 3) 出力ワークブックの検証
    errors.extend(_validate_output_workbook(wb, estimate))

    # 4) 元テンプレートが更新されていないことを確認
    if template_path.read_bytes() != template_before:
        errors.append("出力検証に失敗しました。元テンプレートが更新されています。")

    if errors:
        return FillResult(False, errors, warnings, None, file_name, list(wb.sheetnames), summary)

    pdf_data = None
    if print_ready:
        from print_layout import split_print_pages, render_estimate_pdf
        split_print_pages(wb, estimate)
        pdf_data = render_estimate_pdf(estimate)

    # 5) 別名保存 ＆ バイト取得
    output = BytesIO()
    wb.save(output)
    data = output.getvalue()
    sheet_names = list(wb.sheetnames)

    if save_to_disk:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUTPUT_DIR / file_name).write_bytes(data)

    return FillResult(True, [], warnings, data, file_name, sheet_names, summary, pdf_data)


# ---------------------------------------------------------------------------
# 本番OCRデータ → Estimate 変換アダプタ
# ---------------------------------------------------------------------------

def _first(row, keys, default=""):
    for k in keys:
        if k in row and str(row.get(k, "")).strip() not in ("", "nan", "None"):
            return row.get(k)
    return default


def estimate_from_production(
    detail_df,
    cost_df,
    *,
    metadata: Optional[Dict] = None,
    vendors: Optional[List[str]] = None,
    valid_days: str = "30日",
    tax_rate: float = 0.10,
) -> Estimate:
    """本番フロー（extract_data.py）の detail_df / cost_df から Estimate を組み立てる。

    - 見積元（会社）ごとに 1 カテゴリにまとめる。
    - 金額と数量×単価の一致を出力前に検証し、不一致は出力を止める。
    - 値引きは本番フローに概念が無いため 0。消費税は税抜合計×tax_rate（切り捨て）。
    """
    metadata = metadata or {}

    if detail_df is None or getattr(detail_df, "empty", True):
        raise ValueError("明細データが空です。")

    all_vendors = [str(v) for v in detail_df["見積元"].dropna().astype(str).unique().tolist()]
    target_vendors = [str(v) for v in (vendors or all_vendors)]

    categories: List[Category] = []
    for vendor in target_vendors:
        group = detail_df[detail_df["見積元"].astype(str) == vendor]
        items: List[LineItem] = []
        for _, row in group.iterrows():
            row = dict(row)
            name = str(_first(row, ["品名", "工事項目", "工事品目", "名称"], "")).strip()
            amount = int(round(_to_number(_first(row, ["見積金額", "金額", "原価金額"], 0))))
            if not name and not amount:
                continue
            qty_raw = _to_number(_first(row, ["数量"], 0))
            unit_price = _to_number(_first(row, ["見積単価", "単価", "原価単価"], 0))
            items.append(LineItem(
                name=name or "（項目名なし）",
                spec=str(_first(row, ["仕様"], "") or ""),
                qty=int(qty_raw) if float(qty_raw).is_integer() else qty_raw,
                unit=str(_first(row, ["単位"], "") or ""),
                unit_price=unit_price,
                amount=amount,
                remark=str(_first(row, ["備考"], "") or ""),
            ))
        if items:
            project_label = str(_first(metadata, ["工事名称", "工事名", "件名", "案件名"], "") or "")
            label = project_label if len(target_vendors) == 1 and project_label else vendor
            categories.append(Category(label, items))

    if not categories:
        raise ValueError("対象の見積元に明細がありません。")

    vendor_label = "／".join(target_vendors)
    customer = str(_first(metadata, ["宛名", "顧客名", "取引先", "得意先"], "") or "")
    project = str(_first(metadata, ["工事名称", "工事名", "件名", "案件名"], "") or "")
    issue = str(_first(metadata, ["見積日", "発行日", "作成日"], datetime.now().strftime("%Y/%m/%d")) or "")

    return Estimate(
        vendor=vendor_label,
        customer=customer,
        project_name=project or "見積",
        issue_date=issue,
        valid_days=valid_days,
        categories=categories,
        discount=0,
        tax_rate=tax_rate,
    )


if __name__ == "__main__":
    # テンプレートを（再）生成し、テストデータで流し込みの自己確認を行う。
    tpath = build_single_sheet_template(TEMPLATE_PATH)
    print(f"[template] created: {tpath}")
    result = fill_estimate_v2(default_test_estimate())
    print(f"[fill] ok={result.ok}")
    print(f"[fill] sheets={result.sheet_names}")
    print(f"[fill] file={result.file_name}")
    print(f"[fill] summary={result.summary}")
    if result.errors:
        print("[fill] errors:")
        for e in result.errors:
            print("   -", e)
