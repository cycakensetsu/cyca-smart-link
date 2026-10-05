import unittest
from io import BytesIO
from unittest.mock import patch
from openpyxl import load_workbook
from estimate_pipeline import build_intermediate_dataframe, build_cost_basis_dataframe, apply_company_profit_to_details, apply_profit, validate_intermediate, vendor_detail_dataframe, simple_detail_dataframe
from horizontal_estimate_flow import build_horizontal_estimate
from source_table import reconcile_native_pdf
from markup_rules import line_amount


def frame(rows):
    records = [dict(見積元="テスト工務店", 品名=name, 数量=q, 単位=u, 単価=p, 金額=round(q*p)) for name, q, u, p in rows]
    detail, _ = build_intermediate_dataframe(records)
    cost, _ = build_cost_basis_dataframe({}, detail)
    return detail, cost


class MarkupRulesTest(unittest.TestCase):
    def test_half_yen_rounding_matches_excel(self):
        self.assertEqual(line_amount(1, 100.5), 101)
        self.assertEqual(line_amount(1, -100.5), -101)
        detail, _ = build_intermediate_dataframe([dict(品名="工事", 数量=1, 単位="㎡", 単価=100.5, 金額=101)])
        self.assertEqual(validate_intermediate(detail), [])

    def test_lump_examples_and_infeasible_amount(self):
        for original, add in ((50000, 10000), (50000, 15000), (100000, 50000)):
            detail, cost = frame([("諸経費", 1, "式", original)])
            result, summary = apply_company_profit_to_details(detail, cost, {"テスト工務店": add})
            self.assertEqual(result.iloc[0]["見積単価"], original + add)
            self.assertEqual(summary["上乗せ額"].sum(), add)
        for original, add in ((50000, 30000), (100000, 55000), (100000, 5001)):
            detail, cost = frame([("諸経費", 1, "式", original)])
            with self.assertRaises(ValueError):
                apply_company_profit_to_details(detail, cost, {"テスト工務店": add})

    def test_expenses_do_not_absorb_markup_or_residual(self):
        detail, cost = frame([("下地工事", 19, "㎡", 4250), ("クロス工事", 60, "m", 2200),
                              ("天井点検口", 1, "ヶ所", 25000), ("産廃費", 1, "式", 12000),
                              ("諸経費", 1, "式", 60000), ("補修工事", 1, "式", 30000)])
        for add in (100000, 100001, 50000):
            result, summary = apply_company_profit_to_details(detail, cost, {"テスト工務店": add})
            self.assertEqual(result["上乗せ額"].sum(), add)
            self.assertEqual(result["見積金額"].sum(), summary["見積金額"].sum())
            lumps = result[result["単位"] == "式"]
            for _, row in lumps.iterrows():
                self.assertEqual(row["上乗せ額"] % 5000, 0)
                self.assertLessEqual(row["上乗せ額"], min(row["原価金額"] * 0.5, 50000))
            self.assertGreater(result[result["単位"] != "式"]["上乗せ額"].sum(), lumps["上乗せ額"].sum())
            self.assertLessEqual(result[result["品名"] == "産廃費"]["上乗せ額"].sum(), 5000)
            for _, row in result.iterrows():
                self.assertEqual(round(row["数量"] * row["見積単価"]), row["見積金額"])

    def test_fixed_and_percent_modes_preserve_exact_total_across_vendors(self):
        detail, _ = frame([("高単価工事", 6.5, "㎡", 18000), ("諸経費", 1, "式", 10000)])
        detail.loc[1, "見積元"] = "別会社"
        cost, _ = build_cost_basis_dataframe({}, detail)
        for mode, value, add in (("固定金額（円）を全体に割り振る", 10001, 10001),
                                 ("パーセンテージ（%）で全体に乗せる", 10, 12700)):
            quoted = apply_profit(cost, mode, value, detail_df=detail)
            self.assertEqual(quoted["上乗せ額"].sum(), add)
            result = build_horizontal_estimate(detail, cost, quoted, {"工事名称": "今回の工事"})
            self.assertTrue(result.ok, result.errors)
            wb = load_workbook(BytesIO(result.data))
            quote, raw = wb["見積書"], wb["明細データ"]
            row = 65
            self.assertEqual(quote[f"G{row}"].value, f'=IF(COUNT(D{row},F{row})=2,ROUND(D{row}*F{row},0),"")')
            self.assertIn("'見積書'!F65", raw["G2"].value)
            self.assertIn("'見積書'!G65", raw["H2"].value)
            self.assertNotIn("AND(", quote[f"G{row}"].value)
            rate = quote[f"F{row}"].value
            self.assertEqual(quote[f"F{row}"].number_format, "#,##0" if float(rate).is_integer() else "#,##0.00")

    def test_missing_and_inconsistent_source_data_block_output(self):
        detail, cost = frame([("工事", 19, "㎡", 4250)])
        for field, value in (("数量", None), ("単位", ""), ("原価金額", 80751)):
            broken = detail.copy()
            broken.loc[0, field] = value
            self.assertTrue(any(i["レベル"] == "停止" for i in validate_intermediate(broken)))
            with self.assertRaises(ValueError):
                apply_company_profit_to_details(broken, cost, {"テスト工務店": 10000})

    def test_specs_and_attached_units_are_preserved(self):
        detail, _ = build_intermediate_dataframe([dict(品名="下地工事", 仕様="5.5mm", 数量="19㎡", 単位="式", 単価=4250, 金額=80750, 備考="一部")])
        self.assertEqual(detail.iloc[0]["品名"], "下地工事")
        self.assertEqual(detail.iloc[0]["仕様"], "5.5mm")
        self.assertEqual(detail.iloc[0]["単位"], "㎡")
        self.assertEqual(detail.iloc[0]["備考"], "一部")

    def test_same_work_on_different_floors_keeps_equal_rates(self):
        detail, cost = frame([("2階天壁 クロス", 60, "m", 2200), ("3階天壁 クロス", 30, "m", 2200),
                              ("下地工事", 19, "㎡", 4250), ("点検口", 1, "ヶ所", 25000)])
        result, summary = apply_company_profit_to_details(detail, cost, {"テスト工務店": 100000})
        self.assertEqual(result.iloc[0]["見積単価"], result.iloc[1]["見積単価"])
        self.assertEqual(result["上乗せ額"].sum(), 100000)

    def test_complete_native_table_corrects_ocr_without_partial_replacement(self):
        records = [dict(品名="下地工2 5.5", 見積元="テスト工務", 数量=19, 単位="式", 金額=80750, __source_name="test.pdf")]
        native = [dict(品名="2階ベニヤ下地工事", 仕様="5.5mm", 数量=19, 単位="㎡", 単価=4250, 金額=80750, __native_vendor="株式会社 テスト工務店")]
        with patch("source_table.read_native_tables", return_value=(native, [80750], "内装復旧工事")):
            corrected, summaries = reconcile_native_pdf("test.pdf", records, [dict(工事名称="内装工")])
        self.assertEqual(corrected[0]["単位"], "㎡")
        self.assertEqual(corrected[0]["品名"], "2階ベニヤ下地工事")
        self.assertEqual(summaries[0]["工事名称"], "内装復旧工事")
        self.assertEqual(summaries[0]["見積元"], "株式会社 テスト工務店")
        with patch("source_table.read_native_tables", return_value=(native, [], "")):
            extra = records + [dict(品名="別工事", 見積元="テスト工務", 金額=1000)]
            unchanged, _ = reconcile_native_pdf("test.pdf", extra, [])
            self.assertEqual(unchanged, extra)

    def test_numeric_spec_cannot_erase_floor_number(self):
        record = dict(品名="階天壁 クロス", 仕様="2", 見積元="テスト", 金額=132000)
        native = [dict(品名="2階天壁 クロス", 仕様="", 数量=60, 単位="m", 単価=2200, 金額=132000)]
        with patch("source_table.read_native_tables", return_value=(native, [132000], "")):
            corrected, _ = reconcile_native_pdf("test.pdf", [record], [])
        self.assertEqual(corrected[0]["品名"], "2階天壁 クロス")
        self.assertEqual(corrected[0]["仕様"], "")

    def test_visible_detail_preview_matches_allocated_file(self):
        detail, cost = frame([("工事", 19, "㎡", 4250)])
        detail['仕様'] = '5.5mm'
        allocated, _ = apply_company_profit_to_details(detail, cost, {"テスト工務店": 10000})
        preview, issues = vendor_detail_dataframe(allocated)
        self.assertEqual(issues, [])
        self.assertEqual(preview.iloc[1]['単価'], allocated.iloc[0]['見積単価'])
        self.assertEqual(preview.iloc[1]['金額'], allocated.iloc[0]['見積金額'])
        self.assertEqual(preview.iloc[1]['仕様'], '5.5mm')
        simple, _ = simple_detail_dataframe(allocated)
        self.assertEqual(simple.iloc[0]['単価（円）'], allocated.iloc[0]['見積単価'])
        self.assertEqual(simple.iloc[0]['金額（円）'], allocated.iloc[0]['見積金額'])


if __name__ == "__main__":
    unittest.main()
