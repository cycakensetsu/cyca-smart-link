from copy import deepcopy
import hashlib
import unittest

from estimate_pipeline import build_intermediate_dataframe
from estimate_review import (approve, build_draft, preview_allowed, review_rows,
                             source_provisional_active, source_provisional_eligible,
                             source_subtotal_manual_eligible, source_subtotal_manual_active)


class SourceSubtotalGuardTests(unittest.TestCase):
    def setUp(self):
        self.source = dict(id=hashlib.sha256(b'fictional-source').hexdigest(), name='fiction.pdf',
                           pages=2, confirmed=False, tax_basis='税抜', tax_rate=.10,
                           net_total=138000, tax=13800, gross_total=151800,
                           printed_subtotal_checks=[dict(status='variance', page=2,
                               printed_subtotal=111000, computed_subtotal=123000, difference=12000)])
        records = [dict(品名=name, 数量=1, 単位='式', 単価=amount, 金額=amount,
                        見積元='架空社', 工事種別='配管', __source_name='fiction.pdf', __page_number=2)
                   for name, amount in zip(('運搬', '材料', '処分', '養生', '施工', '諸経費'),
                                           (12000, 27000, 8000, 29000, 47000, 27000))]
        self.rows = review_rows(build_intermediate_dataframe(records)[0], [self.source])

    def draft(self, rows=None):
        return build_draft(self.rows, self.rows if rows is None else rows, [self.source],
                           metadata={'宛名':'架空会社', '工事名称':'架空工事'})

    def test_provisional_choice_allows_internal_preview_but_never_approval(self):
        self.assertFalse(preview_allowed(self.draft()))
        self.source['source_variance_decision'] = source_provisional_eligible(self.source, self.rows)
        self.assertEqual(self.source['source_variance_decision']['chosen_total'], 150000)
        self.assertTrue(source_provisional_active(self.source, self.rows))
        draft = self.draft()
        self.assertTrue(preview_allowed(draft), draft['unresolved'])
        self.assertEqual(draft['payload']['合計']['仕入原価'], 150000)
        self.assertEqual(draft['payload']['原本'][0]['net_total'], 138000)
        with self.assertRaises(ValueError):
            approve(draft, '架空担当')
        self.source['confirmed'] = True
        with self.assertRaises(ValueError):
            approve(self.draft(), '架空担当')

    def test_uncertain_reading_and_unexplained_amount_cannot_be_accepted(self):
        for patch in (dict(status='unverified'),dict(difference=10000)):
            with self.subTest(patch=patch):
                old = deepcopy(self.source['printed_subtotal_checks'])
                self.source['printed_subtotal_checks'][0].update(patch)
                self.assertIsNone(source_provisional_eligible(self.source, self.rows))
                self.assertFalse(preview_allowed(self.draft()))
                self.source['printed_subtotal_checks'] = old

    def test_reading_drop_must_stop_even_when_totals_now_match(self):
        actual_rows = self.rows[1:]
        self.assertEqual(sum(r['原価金額'] for r in actual_rows), 138000)
        self.source['confirmed'] = True
        data = build_draft(actual_rows, actual_rows, [self.source])
        self.assertIn('原本差額の確認', [i['種類'] for i in data['unresolved']])
        self.assertFalse(preview_allowed(data))

    def test_changed_row_detail_invalidates_prior_choice_even_if_amount_unchanged(self):
        self.source['source_variance_decision'] = source_provisional_eligible(self.source, self.rows)
        rows = deepcopy(self.rows)
        rows[0]['品名'] = '異なる金額同一の明細'
        rows[0]['訂正理由'] = '原本を再照合'
        self.assertFalse(source_provisional_active(self.source, rows))
        self.assertFalse(preview_allowed(self.draft(rows)))

    def test_clean_scan_needs_exact_current_visual_confirmation(self):
        self.source.update(confirmed=True, net_total=150000, tax=15000, gross_total=165000)
        proof = [{'page':2, 'name':r['品名'], 'amount':int(r['原価金額']),
                  'evidence':f"{r['品名']} {int(r['原価金額'])}"} for r in self.rows]
        self.source['printed_subtotal_checks'] = [dict(status='unverified', method='ocr', scope='page',
            page=2, row_evidence=proof, printed_evidence='ページ小計：150000円',
            printed_subtotal=150000, computed_subtotal=150000, difference=0)]
        self.assertFalse(preview_allowed(self.draft()))
        decision = source_subtotal_manual_eligible(self.source, self.rows)
        self.assertIsNotNone(decision)
        self.source['source_subtotal_manual_decision'] = decision
        self.assertTrue(source_subtotal_manual_active(self.source, self.rows))
        self.assertTrue(preview_allowed(self.draft()))
        self.assertIsNotNone(approve(self.draft(), '架空担当'))
        self.rows[0]['原価金額'] += 1
        self.rows[1]['原価金額'] -= 1
        self.assertFalse(source_subtotal_manual_active(self.source, self.rows))
        self.assertFalse(preview_allowed(self.draft()))


if __name__ == '__main__':
    unittest.main()
