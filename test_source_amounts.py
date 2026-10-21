"""Independent original-total reading with synthetic documents only."""
import unittest
from unittest.mock import patch
from source_amounts import read_amounts, money, net_from_gross


class SourceAmountsTest(unittest.TestCase):
    def read(self, lines, summary=None):
        with patch('source_amounts._lines', return_value=iter(lines)):
            return read_amounts.__wrapped__('a.pdf', b'synthetic', summary)

    def test_standard_net_tax_and_gross(self):
        result=self.read([(1,'税抜小計 100,000円'),(1,'消費税（10%） 10,000円'),(1,'税込合計 110,000円')])
        self.assertEqual([result[k] for k in ('net_total','tax','gross_total')],[100000,10000,110000])
        self.assertEqual(result['conflicts'],[])
        self.assertEqual(result['tax_basis'],'税抜')
        self.assertEqual(result['tax_rate'],.10)

    def test_discounted_total_has_priority_over_section_subtotals(self):
        result=self.read([(1,'小計 60,000'),(2,'小計 40,000'),(3,'改小計 99,000'),(3,'消費税 9,900'),(3,'税込合計 108,900')])
        self.assertEqual(result['net_total'],99000)
        self.assertEqual(result['page'],3)
        self.assertEqual(result['conflicts'],[])

    def test_repeated_same_total_is_not_a_conflict(self):
        result=self.read([(1,'税込合計 110,000'),(3,'税込合計 110,000')])
        self.assertEqual(result['gross_total'],110000)
        self.assertEqual(result['net_total'],100000)
        self.assertTrue(result['net_derived'])

    def test_conflicting_original_totals_are_not_arbitrarily_chosen(self):
        result=self.read([(1,'税込合計 110,000'),(2,'税込合計 220,000')])
        self.assertIsNone(result['gross_total'])
        self.assertIsNone(result['net_total'])
        self.assertIn('gross_total',result['conflicts'])

    def test_missing_amounts_are_not_zero_and_other_source_is_ignored(self):
        result=self.read([],{'summary_sources':[{'元ファイル':'other.pdf','改小計':900000,'工事費計':990000}]})
        self.assertIsNone(result['net_total'])
        self.assertIsNone(result['gross_total'])

    def test_scanned_original_uses_its_own_summary(self):
        result=self.read([],{'summary_sources':[{'元ファイル':'a.pdf','改小計':100000,'消費税':10000,'工事費計':110000}]})
        self.assertEqual(result['net_total'],100000)
        self.assertEqual(result['evidence'][0]['method'],'AIの読み取り')

    def test_native_text_wins_over_ocr_summary(self):
        result=self.read([(2,'税抜小計 100,000'),(2,'税込合計 110,000')],{'summary_sources':[{'元ファイル':'a.pdf','改小計':10000,'工事費計':11000}]})
        self.assertEqual(result['net_total'],100000)
        self.assertEqual(result['gross_total'],110000)

    def test_next_line_value_and_full_width_currency(self):
        result=self.read([(1,'税抜小計'),(1,'￥１００，０００'),(1,'消費税 １０，０００'),(1,'税込合計'),(1,'１１０，０００円')])
        self.assertEqual(result['net_total'],100000)
        self.assertEqual(result['gross_total'],110000)

    def test_generic_total_same_as_net_is_not_relabelled_gross(self):
        result=self.read([(1,'税抜小計 100,000'),(1,'消費税 10,000'),(1,'合計 100,000')])
        self.assertIsNone(result['gross_total'])

    def test_quotation_amount_without_tax_is_net_not_divided_by_1_1(self):
        result=self.read([(1,'見積金額 110,000'),(2,'消費税は別途です')])
        self.assertEqual(result['net_total'],110000)
        self.assertIsNone(result['gross_total'])

    def test_overall_quote_net_has_priority_over_page_subtotals(self):
        result=self.read([(1,'見積金額 110,000'),(2,'小計 60,000'),(3,'小計 50,000')])
        self.assertEqual(result['net_total'],110000)
        self.assertEqual(result['conflicts'],[])
        self.assertIsNone(result['gross_total'])

    def test_final_printed_net_after_discount_is_used(self):
        result=self.read([(1,'小計 100,000'),(1,'値引き -1,000'),(1,'合計 99,000'),(1,'消費税 9,900'),(1,'税込金額 108,900')])
        self.assertEqual(result['net_total'],99000)
        self.assertEqual(result['gross_total'],108900)

    def test_inconsistent_original_is_not_balanced_by_inventing_a_total(self):
        result=self.read([(1,'小計 100,000'),(1,'消費税 10,000'),(1,'税込金額 108,900')])
        self.assertEqual(result['net_total'],100000)
        self.assertEqual(result['gross_total'],108900)

    def test_common_quotation_spelling_is_net_and_ignores_page_subtotals(self):
        for label in ('御見積もり金額','御見積り金額','お見積もり金額'):
            result=self.read([(1,label),(1,'￥110,000'),(2,'小計 60,000'),(3,'合計 50,000')])
            self.assertEqual(result['net_total'],110000)
            self.assertIsNone(result['gross_total'])
            self.assertEqual(result['conflicts'],[])

    def test_scanned_cover_final_total_has_priority_over_native_page_subtotal(self):
        result=self.read([(2,'小計 60,000'),(3,'合計 50,000')],{'summary_sources':[{'元ファイル':'a.pdf','改小計':110000}]})
        self.assertEqual(result['net_total'],110000)
        self.assertIsNone(result['gross_total'])
        self.assertEqual(result['conflicts'],[])

    def test_header_total_and_independent_net_prove_inclusive_amount(self):
        result=self.read([(1,'御見積もり金額'),(1,'￥110,000')],{'summary_sources':[{'元ファイル':'a.pdf','改小計':100000,'工事費計':110000}]})
        self.assertEqual(result['net_total'],100000)
        self.assertEqual(result['gross_total'],110000)
        self.assertEqual(result['conflicts'],[])

    def test_printed_count_tax_and_inclusive_total_override_an_ocr_net_price(self):
        result=self.read([(1,'NET金額 108,000'),(2,'お見積り合計金額 ￥110,000（消費税込）'),
                          (2,'計 100,000'),(2,'消費税 10% 10,000'),(2,'小計 110,000')],
                         {'summary_sources':[{'元ファイル':'a.pdf','改小計':108000,'工事費計':110000}]})
        self.assertEqual(result['net_total'],100000)
        self.assertEqual(result['gross_total'],110000)
        self.assertEqual(result['amount_notes'][0]['value'],108000)

    def test_net_and_gross_discount_are_not_silently_ignored(self):
        result=self.read([(1,'NET金額 ￥108,000'),(2,'小計 100,000'),(2,'消費税 10,000'),(2,'合計 110,000')])
        self.assertEqual(result['net_total'],100000)
        self.assertEqual(result['amount_notes'][0]['value'],108000)
        self.assertEqual(result['amount_notes'][0]['page'],1)
        result=self.read([(1,'合計金額 ￥109,500 税込み'),(2,'小計 100,000'),(2,'消費税 10,000'),(2,'合計 110,000 ▼500')])
        self.assertEqual(result['gross_total'],109500)
        self.assertEqual(result['amount_notes'][0]['value'],-500)
        self.assertEqual(result['net_total'],100000,'do not manufacture a net adjustment')

    def test_blank_net_field_and_normal_net_equal_to_details_need_no_question(self):
        for label in ('NET:￥-(税別)','NET金額 100,000'):
            result=self.read([(1,label),(1,'小計 100,000'),(1,'消費税 10,000'),(1,'合計 110,000')])
            self.assertEqual(result['amount_notes'],[])

    def test_scanned_amount_notes_require_numeric_evidence_and_exact_source(self):
        summary={'summary_sources':[{'元ファイル':'a.pdf','改小計':100000,'消費税':10000,'工事費計':110000,
            '金額の要確認箇所':[{'種類':'税込後値引き','金額':-500,'ページ':2,'根拠':'税込合計 110,000 ▼500'},
                {'種類':'NET金額','金額':108000,'ページ':1,'根拠':'NET金額 未読'}]},
            {'元ファイル':'other.pdf','金額の要確認箇所':[{'種類':'NET金額','金額':9,'根拠':'NET9'}]}]}
        result=self.read([],summary)
        self.assertEqual(len(result['amount_notes']),1)
        self.assertEqual(result['amount_notes'][0]['value'],-500)

    def test_strict_amounts_and_explicit_tax_derivation(self):
        for value in (None,True,'abc','10%','100 200'):
            self.assertIsNone(money(value))
        self.assertEqual(money('￥１２３，４５６円'),123456)
        self.assertEqual(net_from_gross(110000,tax=10000),100000)
        self.assertEqual(net_from_gross(110000),100000)
