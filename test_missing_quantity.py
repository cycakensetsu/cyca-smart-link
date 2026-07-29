"""数量・金額が未取得（NaN）の明細があっても上乗せ計算が落ちないことの回帰テスト。

瀧上工業の見積のように「一式」表記で数量が拾えない行があると、
`NaN or 1` が NaN のままになり int(math.ceil(NaN)) で ValueError になっていた。
"""
import unittest
import pandas as pd

from estimate_pipeline import (
    apply_company_profit_to_details,
    build_cost_basis_dataframe,
    build_intermediate_dataframe,
    build_vendor_work_summary_dataframe,
)

SRC = "瀧上工業.pdf"
VENDOR = "瀧上工業"


def _detail_df():
    records = [
        # 数量・単位・単価が取れなかった行（実際のOCRで頻出）
        {"見積元": VENDOR, "品名": "防水工事一式", "金額": 1150000,
         "__source_name": SRC, "__page_number": 1},
        {"見積元": VENDOR, "品名": "諸経費", "数量": 1, "単位": "式",
         "単価": 65000, "金額": 65000, "__source_name": SRC, "__page_number": 1},
        {"見積元": VENDOR, "品名": "厚生福利費", "数量": 1, "単位": "式",
         "単価": 35000, "金額": 35000, "__source_name": SRC, "__page_number": 1},
    ]
    df, _ = build_intermediate_dataframe(records)
    df["元ファイル"] = SRC
    return df


class MissingQuantityTest(unittest.TestCase):
    def test_detail_has_missing_quantity(self):
        df = _detail_df()
        self.assertTrue(df["数量"].isna().any(), "数量がNaNの行が前提のテスト")

    def test_markup_does_not_crash_on_missing_quantity(self):
        detail_df = _detail_df()
        summary = {"summary_sources": [
            {"見積元": VENDOR, "元ファイル": SRC, "工事名称": "防水工事",
             "工事項目": [{"工事項目": "防水工事一式", "金額": 1250000}],
             "小計": 1250000},
        ]}
        cost_df, vendor_summaries = build_cost_basis_dataframe(summary, detail_df)
        # ここで以前は ValueError: cannot convert float NaN to integer が出ていた
        detail_p, cost_p = apply_company_profit_to_details(detail_df, cost_df, {VENDOR: 550000})
        self.assertEqual(int(cost_p["見積金額"].sum()), 1800000)
        _, totals = build_vendor_work_summary_dataframe(vendor_summaries, cost_p)
        self.assertEqual(totals["改小計"], 1800000)
        self.assertEqual(totals["工事費計"], 1800000 + 180000)

    def test_all_quantities_missing(self):
        records = [
            {"見積元": VENDOR, "品名": "本体工事", "金額": 800000, "__source_name": SRC},
            {"見積元": VENDOR, "品名": "付帯工事", "金額": 200000, "__source_name": SRC},
        ]
        detail_df, _ = build_intermediate_dataframe(records)
        detail_df["元ファイル"] = SRC
        cost_df, _ = build_cost_basis_dataframe({"summary_sources": []}, detail_df)
        detail_p, cost_p = apply_company_profit_to_details(detail_df, cost_df, {VENDOR: 200000})
        self.assertEqual(int(cost_p["見積金額"].sum()), 1200000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
