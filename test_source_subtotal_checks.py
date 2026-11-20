"""Regression coverage for independent printed subtotal checks."""
import unittest
from unittest.mock import patch

from source_amounts import _lines, read_amounts
from source_subtotal_checks import combine_checks, evaluate_subtotals, manual_clean_candidate, read_native_checks


def original_table(rows):
    import pymupdf
    pdf = pymupdf.open()
    cover = pdf.new_page()
    cover.insert_text((20, 40), "見積金額 138,000", fontname="japan")
    page = pdf.new_page()
    all_rows = [("品名", "数量", "単位", "単価", "金額"), *rows]
    xs = (30, 170, 230, 290, 355, 430)
    for y in range(50, 50 + 22 * (len(all_rows) + 1), 22):
        page.draw_line((xs[0], y), (xs[-1], y))
    for x in xs:
        page.draw_line((x, 50), (x, 50 + 22 * len(all_rows)))
    for index, row in enumerate(all_rows):
        for x, value in zip(xs, row):
            if value:
                page.insert_text((x + 4, 64 + index * 22), value, fontsize=10, fontname="japan")
    data = pdf.tobytes()
    pdf.close()
    return data


def item(name, amount):
    return (name, "1", "式", str(amount), str(amount))


def subtotal(label, amount):
    return (label, "", "", "", str(amount))


def ocr_rows(values, page=2, source="fixture.pdf", group=""):
    return [dict(__source_name=source, __page_number=page, 品名=name, 金額=price, 部位=group,
                 抽出元テキスト範囲=f"{name} {price}")
            for name, price in values]


def ocr_summary(scope="ページ", label="小計", value=111000, section="", page=2):
    return dict(__source_name="fixture.pdf", __page_number=1, __page_role="cover_summary_page",
                印字小計=[{"ページ": page, "範囲": scope, "印字ラベル": label, "金額": value,
                         "区分名": section, "原文": f"{label} {value}"}])


class SubtotalTests(unittest.TestCase):
    def test_native_multirow_table_preserves_expenses_and_reports_actual_variance(self):
        detail = [item("資材搬入", 12000), item("工事A", 27000), item("工事B", 8000),
                  item("工事C", 29000), item("工事D", 47000)]
        pdf = original_table([*detail, subtotal("小計", 111000), item("諸経費", 27000)])
        checks = read_native_checks(pdf, list(_lines(pdf)), [])
        self.assertEqual(len(checks), 1, checks)
        check = checks[0]
        self.assertEqual((check["page"], check["status"], check["printed_subtotal"],
                          check["computed_subtotal"], check["difference"]), (2, "variance",111000,123000,12000))
        self.assertEqual([r["name"] for r in check["row_evidence"]], [d[0] for d in detail])
        reading = read_amounts("fixture.pdf", pdf)
        self.assertEqual(reading["net_total"], 138000)
        self.assertEqual(reading["subtotal_checks"], checks)
        self.assertTrue(check["blocking"])

    def test_section_subtotals_do_not_include_other_sections(self):
        pdf = original_table([subtotal("東棟", ""), item("塗装", 111000), subtotal("東棟小計", 111000),
                              subtotal("西棟", ""), item("下地", 20000), subtotal("西棟小計", 22000),
                              subtotal("ページ計",131000)])
        checks = read_native_checks(pdf, list(_lines(pdf)), [])
        self.assertEqual([(c["scope"], c["section"], c["status"], c["difference"]) for c in checks],
                         [("section","東棟","matched",0),("section","西棟","variance",-2000),("page","","matched",0)])

    def test_repeated_bare_subtotals_and_carryover_do_not_prove_scope(self):
        pdf = original_table([subtotal("前頁繰越", 100000), item("工事", 20000), subtotal("小計", 20000),
                              item("追加工事", 30000), subtotal("小計", 50000)])
        checks = read_native_checks(pdf, list(_lines(pdf)), [])
        self.assertEqual([c["status"] for c in checks], ["unverified", "unverified"])
        self.assertTrue(all(c["blocking"] for c in checks))

    def test_scanned_amount_ties_but_does_not_prove_complete_or_correct_ocr(self):
        rows = ocr_rows([("工事A", 64000), ("工事B", 47000)])
        rows.extend(ocr_rows([("諸経費",27000)], page=1))
        rows.extend(ocr_rows([("別原本",99999)], source="other.pdf"))
        results = evaluate_subtotals("fixture.pdf",rows,[ocr_summary()])
        self.assertEqual(len(results),1)
        self.assertEqual((results[0]["status"],results[0]["computed_subtotal"],results[0]["difference"]),
                         ("unverified",111000,0))
        self.assertTrue(results[0]["blocking"])
        self.assertTrue(manual_clean_candidate(results[0]))
        self.assertFalse(manual_clean_candidate({**results[0], "printed_evidence": "111,000"}))
        self.assertFalse(manual_clean_candidate({**results[0], "scope": "unknown"}))
        self.assertFalse(manual_clean_candidate({**results[0], "difference": 1}))

    def test_native_balances_but_ocr_missing_row_stays_blocking(self):
        pdf = original_table([item("工事甲", 64000), item("工事乙", 47000), subtotal("内訳計",111000)])
        raw = read_native_checks(pdf,list(_lines(pdf)),[])
        checks = combine_checks(raw,evaluate_subtotals("fixture.pdf",ocr_rows([("工事甲",64000)]),
                                                      [ocr_summary(label="内訳計")]))
        self.assertEqual(checks[0]["status"],"unverified")
        self.assertEqual(checks[0]["difference"],-47000)
        self.assertIn("OCR",checks[0]["reason"])

    def test_unverified_label_and_multiple_files_are_conservative(self):
        own = ocr_summary("不明","小計",value=None)
        other = {**ocr_summary(), "__source_name":"other.pdf"}
        checks = evaluate_subtotals("fixture.pdf",ocr_rows([("工事", 111000)]),[own,other])
        self.assertEqual(len(checks),1)
        self.assertEqual((checks[0]["status"],checks[0]["scope"],checks[0]["computed_subtotal"]),
                         ("unverified","unknown",None))

    def test_text_pdf_without_grid_requires_visual_confirmation_even_when_numerically_equal(self):
        import pymupdf
        pdf = pymupdf.open()
        page = pdf.new_page()
        page.insert_text((50,60), "見積金額 138,000", fontname="japan")
        page = pdf.new_page()
        page.insert_text((50,60), "工事A 111,000", fontname="japan")
        page.insert_text((50,80), "小計 111,000", fontname="japan")
        data = pdf.tobytes()
        pdf.close()
        ocr = evaluate_subtotals("fixture.pdf", ocr_rows([("工事A", 111000)]), [ocr_summary()])
        result = read_amounts("fixture.pdf", data, {"subtotal_checks_by_source":{"fixture.pdf": ocr}})
        self.assertEqual([r["status"] for r in result["subtotal_checks"]], ["unverified"])
        check = result["subtotal_checks"][0]
        self.assertEqual((check["method"],check["scope"],check["difference"]), ("native_text","page",0))
        self.assertTrue(manual_clean_candidate(check))
        without_ocr = read_amounts("fixture.pdf",data)["subtotal_checks"][0]
        self.assertTrue(without_ocr["blocking"])
        self.assertFalse(manual_clean_candidate(without_ocr))

    def test_normalized_ocr_evidence_is_separate_from_cover_accounting(self):
        from estimate_pipeline import normalize_summary_data
        checks = evaluate_subtotals("fixture.pdf",ocr_rows([("明細",111000)]),[ocr_summary()])
        summary = normalize_summary_data([{"__source_name":"fixture.pdf","__subtotal_checks":checks}])
        self.assertEqual(summary["summary_sources"], [])
        self.assertIsNone(summary["小計"])
        self.assertEqual(read_amounts("fixture.png", b"x", summary)["subtotal_checks"], [])
        self.assertEqual(read_amounts("fixture.pdf", b"x", summary)["subtotal_checks"], checks)


if __name__ == "__main__":
    unittest.main()
