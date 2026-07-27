"""DANJYOU床張替2.pdf 回帰テスト:
1枚目=FAX送付案内(表紙, NET金額180,000), 2枚目=見積本体(計167,000/税16,700/計183,700)
"""
import unittest, pandas as pd
from estimate_pipeline import (
    build_cost_basis_dataframe, apply_company_profit_to_details,
    build_vendor_work_summary_dataframe, build_intermediate_dataframe,
)

SRC = "DANJYOU床張替2.pdf"
VENDOR = "津村 タイル"

def make_detail_df():
    records = [
        {"見積元": VENDOR, "品名": "現状床タイル撤去", "仕様": "撤去材 処理込", "数量": 1, "単位": "式",
         "単価": 40000, "金額": 40000, "__source_name": SRC, "__page_number": 2},
        {"見積元": VENDOR, "品名": "新規床タイル貼り", "仕様": "192*192*9mm ダントー", "数量": 6.5, "単位": "㎡",
         "単価": 18000, "金額": 117000, "__source_name": SRC, "__page_number": 2},
        {"見積元": VENDOR, "品名": "諸経費", "数量": 1, "単位": "式",
         "単価": 10000, "金額": 10000, "__source_name": SRC, "__page_number": 2},
    ]
    df, _ = build_intermediate_dataframe(records)
    df["元ファイル"] = SRC
    return df

def make_summary_data():
    return {"summary_sources": [
        # 1枚目: FAX送付案内(表紙) — NET金額180,000 が一式として拾われた幻の見積
        {"見積元": VENDOR, "元ファイル": SRC, "工事名称": "玄関ポーチ床タイル 張替え工事",
         "工事項目": [{"工事項目": "玄関ポーチ床タイル 張替え工事", "金額": 180000}],
         "小計": 180000, "__source_name": SRC, "__page_number": 1},
        # 2枚目: 見積本体
        {"見積元": VENDOR, "元ファイル": SRC, "工事名称": "玄関ポーチ床タイル 張替え工事",
         "工事項目": [
             {"工事項目": "現状床タイル撤去", "金額": 40000},
             {"工事項目": "新規床タイル貼り", "金額": 117000},
             {"工事項目": "諸経費", "金額": 10000}],
         "小計": 167000, "消費税": 16700, "工事費計": 183700,
         "__source_name": SRC, "__page_number": 2},
    ]}

class DanjyouTest(unittest.TestCase):
    def test_cover_page_is_not_double_counted(self):
        detail_df = make_detail_df()
        cost_df, vendor_summaries = build_cost_basis_dataframe(make_summary_data(), detail_df)
        self.assertEqual(len(vendor_summaries), 1, "表紙と本体で2件になってはいけない")
        self.assertEqual(int(cost_df["原価金額"].sum()), 167000)

    def test_totals_match_pdf_without_markup(self):
        detail_df = make_detail_df()
        cost_df, vendor_summaries = build_cost_basis_dataframe(make_summary_data(), detail_df)
        detail_p, cost_p = apply_company_profit_to_details(detail_df, cost_df, {VENDOR: 0})
        self.assertEqual(int(cost_p["見積金額"].sum()), 167000)
        _, totals = build_vendor_work_summary_dataframe(vendor_summaries, cost_p)
        self.assertEqual(totals["改小計"], 167000)
        self.assertEqual(totals["消費税"], 16700)
        self.assertEqual(totals["工事費計"], 183700)

    def test_markup_is_applied_once(self):
        detail_df = make_detail_df()
        cost_df, vendor_summaries = build_cost_basis_dataframe(make_summary_data(), detail_df)
        detail_p, cost_p = apply_company_profit_to_details(detail_df, cost_df, {VENDOR: 100000})
        self.assertEqual(int(cost_p["見積金額"].sum()), 267000)
        self.assertEqual(int(detail_p["見積金額"].sum()), 267000)
        _, totals = build_vendor_work_summary_dataframe(vendor_summaries, cost_p)
        self.assertEqual(totals["改小計"], 267000)
        self.assertEqual(totals["工事費計"], 267000 + 26700)

    def test_two_different_works_in_one_pdf_are_kept(self):
        """同一PDF・同一業者でも工事名が違えば別工事として両方残す（誤削除の防止）。"""
        summary = {"summary_sources": [
            {"見積元": VENDOR, "元ファイル": SRC, "工事名称": "玄関ポーチ床タイル 張替え工事",
             "工事項目": [{"工事項目": "玄関ポーチ", "金額": 167000}], "小計": 167000},
            {"見積元": VENDOR, "元ファイル": SRC, "工事名称": "浴室床タイル 張替え工事",
             "工事項目": [{"工事項目": "浴室", "金額": 250000}], "小計": 250000},
        ]}
        cost_df, vendor_summaries = build_cost_basis_dataframe(summary, pd.DataFrame())
        self.assertEqual(len(vendor_summaries), 2, "工事名が違う別工事は残すべき")
        self.assertEqual(int(cost_df["原価金額"].sum()), 417000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
