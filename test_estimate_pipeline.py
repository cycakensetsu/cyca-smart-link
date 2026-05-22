import unittest

from estimate_pipeline import (
    NUMBERS_OUTPUT_COLUMNS,
    OUTPUT_COLUMNS,
    build_intermediate_dataframe,
    output_dataframe,
    numbers_detail_dataframe,
    parse_money,
    split_quantity_unit,
    validate_intermediate,
)


DANJYO_RECORDS = [
    {"No": 1, "見積元": "DANJYO", "PDF小計": 1750000, "消費税": 175000, "税込合計": 1925000, "品名": "仮設足場 W600組ブラケット足場 外手摺一本 60日間", "数量": "665架㎡", "単価": "550円", "金額": "365,750円"},
    {"No": 2, "見積元": "DANJYO", "PDF小計": 1750000, "消費税": 175000, "税込合計": 1925000, "品名": "仮設足場 W600組 本足場 外・内手摺各一本 60日間", "数量": "869架㎡", "単価": "750円", "金額": "651,750円"},
    {"No": 3, "見積元": "DANJYO", "PDF小計": 1750000, "消費税": 175000, "税込合計": 1925000, "品名": "飛散防止ネット 防炎II類", "数量": "1,534架㎡", "単価": "150円", "金額": "230,100円"},
    {"No": 4, "見積元": "DANJYO", "PDF小計": 1750000, "消費税": 175000, "税込合計": 1925000, "品名": "昇降階段/2箇所", "数量": "10基", "単価": "5,000円", "金額": "50,000円"},
    {"No": 5, "見積元": "DANJYO", "PDF小計": 1750000, "消費税": 175000, "税込合計": 1925000, "品名": "壁繋ぎ", "数量": "1,534架㎡", "単価": "100円", "金額": "153,400円"},
    {"No": 6, "見積元": "DANJYO", "PDF小計": 1750000, "消費税": 175000, "税込合計": 1925000, "品名": "場内小運搬 架け・払い", "数量": "1,534架㎡", "単価": "100円", "金額": "153,400円"},
    {"No": 7, "見積元": "DANJYO", "PDF小計": 1750000, "消費税": 175000, "税込合計": 1925000, "品名": "資材運搬費 架け・払い 糸島-柳川", "数量": "1,534架㎡", "単価": "120円", "金額": "184,080円"},
    {"No": 8, "見積元": "DANJYO", "PDF小計": 1750000, "消費税": 175000, "税込合計": 1925000, "品名": "値引き", "数量": "1式", "単価": "-38,480円", "金額": "-38,480円"},
]


class EstimatePipelineTest(unittest.TestCase):
    def test_split_quantity_unit(self):
        self.assertEqual(split_quantity_unit("665架㎡"), (665.0, "架㎡"))
        self.assertEqual(split_quantity_unit("1,534架㎡"), (1534.0, "架㎡"))
        self.assertEqual(split_quantity_unit("10基"), (10.0, "基"))
        self.assertEqual(split_quantity_unit("1式"), (1.0, "式"))

    def test_parse_negative_money(self):
        self.assertEqual(parse_money("-38,480円"), -38480.0)

    def test_danjyo_records_are_preserved_and_validated(self):
        df, totals = build_intermediate_dataframe(DANJYO_RECORDS)
        self.assertEqual(len(df), 8)
        self.assertEqual(int(df["原価金額"].sum()), 1750000)
        self.assertEqual(totals["小計"], 1750000)
        self.assertEqual(totals["消費税"], 175000)
        self.assertEqual(totals["税込合計"], 1925000)
        self.assertTrue((df["品名"] == "値引き").any())
        discount = df[df["品名"] == "値引き"].iloc[0]
        self.assertEqual(int(discount["原価金額"]), -38480)
        self.assertEqual(validate_intermediate(df, totals), [])

    def test_output_columns_are_fixed(self):
        df, _ = build_intermediate_dataframe(DANJYO_RECORDS)
        self.assertEqual(output_dataframe(df).columns.tolist(), OUTPUT_COLUMNS)

    def test_numbers_detail_keeps_all_rows(self):
        df, _ = build_intermediate_dataframe(DANJYO_RECORDS)
        detail, issues = numbers_detail_dataframe(df)
        self.assertEqual(issues, [])
        self.assertEqual(len(detail), 8)
        self.assertEqual(detail.columns.tolist(), NUMBERS_OUTPUT_COLUMNS)
        self.assertEqual(detail["工事品目"].tolist(), df["品名"].tolist())
        self.assertIn("場内小運搬 架け・払い", detail["工事品目"].tolist())
        self.assertIn("資材運搬費 架け・払い 糸島-柳川", detail["工事品目"].tolist())
        self.assertIn("値引き", detail["工事品目"].tolist())
        self.assertEqual(detail["単価"].tolist(), df["見積単価"].tolist())
        self.assertEqual(detail["金額"].tolist(), df["見積金額"].tolist())
        for internal_col in ["見積元", "品名", "原価単価", "原価金額", "上乗せ額", "見積単価", "見積金額"]:
            self.assertNotIn(internal_col, detail.columns)

    def test_subtotal_mismatch_blocks_output(self):
        bad = [dict(r) for r in DANJYO_RECORDS]
        bad[0]["金額"] = "1円"
        df, totals = build_intermediate_dataframe(bad)
        issues = validate_intermediate(df, totals)
        self.assertTrue(any(issue["レベル"] == "停止" for issue in issues))


if __name__ == "__main__":
    unittest.main()
