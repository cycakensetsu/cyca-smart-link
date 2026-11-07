"""Approval must distinguish natural exact markup from explicit pricing choices."""
import copy
import hashlib
import unittest

import pandas as pd

from estimate_pipeline import build_intermediate_dataframe
from estimate_review import (Target, _natural_price_warnings, approve, build_approved_estimate, build_draft,
                             estimate_for_output, preview_allowed, review_rows)
from review_ui import prepare_profit_choices


def quote_fixture(items):
    source = {'id': hashlib.sha256(b'profit-proposal-original').hexdigest(),
              'name': 'profit-proposal.pdf', 'size': 8, 'tax_basis': '税抜',
              'tax_rate': .10, 'confirmed': True,
              'net_total': sum(qty * rate for _, qty, _, rate in items)}
    records = [dict(品名=name, 数量=qty, 単位=unit, 単価=rate, 金額=qty * rate,
                    見積元='確認用工務店', 工事種別='塗装工事',
                    __source_name=source['name'], __page_number=1)
               for name, qty, unit, rate in items]
    detail, _ = build_intermediate_dataframe(records)
    rows = review_rows(detail, [source])
    metadata = {'宛名': '確認用 御中', '工事名称': '利益配分検証', '見積日': '2026/11/07'}
    return rows, [source], metadata


def draft_for(items, markup, *, choice=None, edited=None):
    rows, sources, metadata = quote_fixture(items)
    return build_draft(rows, edited or copy.deepcopy(rows), sources,
                       [Target('全体', '全体', '上乗せ額', markup)], 0, metadata,
                       pricing_choice=choice)


class ProfitProposalsTest(unittest.TestCase):
    NATURAL = [('塗装工事', 10, '㎡', 20000), ('下地補修', 30, '㎡', 5000),
               ('シーリング', 20, 'm', 5000), ('防水工事', 1, '式', 100000)]
    ODD = [('下地工事', 19, '㎡', 4250), ('クロス工事', 60, 'm', 2200),
           ('諸経費', 1, '式', 60000)]

    def test_350000_yen_natural_markup_needs_no_extra_choice(self):
        draft = draft_for(self.NATURAL, 350000)
        self.assertEqual(draft['unresolved'], [])
        self.assertFalse(draft['profit_plan']['requires_confirmation'])
        self.assertEqual(draft['payload']['合計']['粗利益'], 350000)
        self.assertEqual(draft['payload']['値引き'], 0)
        self.assertIsNone(draft['payload']['利益配分案'])

    def test_two_equal_lump_costs_share_large_global_markup_evenly(self):
        draft = draft_for([('工事A', 1, '式', 100000), ('工事B', 1, '式', 100000)], 120000)
        self.assertEqual(draft['unresolved'], [])
        self.assertFalse(draft['profit_plan']['requires_confirmation'])
        self.assertEqual([row['上乗せ額'] for row in draft['payload']['計算明細']], [60000, 60000])
        self.assertEqual(draft['payload']['合計']['粗利益'], 120000)

    def test_awkward_exact_amount_blocks_approval_and_offers_two_clear_choices(self):
        draft = draft_for(self.ODD, 100001)
        self.assertTrue(draft['profit_plan']['requires_confirmation'])
        self.assertIn('利益配分案', [issue['種類'] for issue in draft['unresolved']])
        self.assertFalse(preview_allowed(draft))
        with self.assertRaises(ValueError):
            approve(draft, '確認者')
        self.assertEqual(draft['payload']['値引き'], 0)
        options = {option['id']: option for option in draft['profit_plan']['options']}
        self.assertEqual(options['no_discount']['markup'], 100001)
        self.assertEqual(options['no_discount']['discount'], 0)
        self.assertEqual(options['no_discount']['final_profit'], 100001)
        self.assertTrue(options['no_discount']['warnings'])
        self.assertGreater(options['rounding_discount']['markup'], 100001)
        self.assertEqual(options['rounding_discount']['markup']+options['rounding_discount']['discount'],100001)
        self.assertEqual(options['rounding_discount']['final_profit'], 100001)
        self.assertEqual(options['rounding_discount']['warnings'], [])

    def test_explicit_no_discount_choice_keeps_the_exact_profit(self):
        pending = draft_for(self.ODD, 100001)
        choice = {'signature': pending['profit_plan']['signature'], 'id': 'no_discount'}
        chosen = draft_for(self.ODD, 100001, choice=choice)
        self.assertEqual(chosen['unresolved'], [])
        self.assertEqual(chosen['payload']['値引き'], 0)
        self.assertEqual(chosen['payload']['合計']['粗利益'], 100001)
        self.assertNotEqual(chosen['fingerprint'], pending['fingerprint'])

    def test_discount_choice_only_applies_after_selection_and_reaches_target(self):
        pending = draft_for(self.ODD, 100001)
        choice = {'signature': pending['profit_plan']['signature'], 'id': 'rounding_discount'}
        chosen = draft_for(self.ODD, 100001, choice=choice)
        self.assertEqual(chosen['unresolved'], [])
        option=next(o for o in pending['profit_plan']['options'] if o['id']=='rounding_discount')
        self.assertEqual(chosen['payload']['合計']['売価小計'] - chosen['payload']['合計']['仕入原価'], option['markup'])
        self.assertEqual(chosen['payload']['値引き'], option['discount'])
        self.assertEqual(chosen['payload']['合計']['粗利益'], 100001)
        self.assertIn('利用者が案を選択', chosen['payload']['表題']['値引き理由'])
        self.assertEqual(estimate_for_output(chosen).discount, option['discount'])
        result = build_approved_estimate(chosen, approve(chosen, '確認者'))
        self.assertTrue(result.ok, result.errors)
        self.assertTrue(result.data.startswith(b'PK'))
        self.assertTrue(result.pdf_data.startswith(b'%PDF'))

    def test_manual_discount_cannot_bypass_awkward_price_confirmation(self):
        rows, sources, metadata = quote_fixture(self.ODD)
        metadata['値引き理由'] = '利用者が入力した提案'
        target = [Target('全体', '全体', '上乗せ額', 100001)]
        pending = build_draft(rows, copy.deepcopy(rows), sources, target, -1, metadata)
        self.assertIn('利益配分案', [issue['種類'] for issue in pending['unresolved']])
        self.assertFalse(preview_allowed(pending))
        self.assertEqual(pending['payload']['値引き'], 0)
        self.assertEqual(pending['payload']['合計']['粗利益'], 100001)
        proposal = next(o for o in pending['profit_plan']['options'] if o['id']=='rounding_discount')
        self.assertEqual(proposal['final_profit'], 100001)
        self.assertEqual(proposal['markup']+proposal['discount'],100001)
        choice = {'signature':pending['profit_plan']['signature'],'id':'rounding_discount'}
        chosen = build_draft(rows, copy.deepcopy(rows), sources, target, -1, metadata,pricing_choice=choice)
        self.assertEqual(chosen['unresolved'], [])
        self.assertEqual(chosen['payload']['値引き'],-1)
        self.assertEqual(chosen['payload']['合計']['粗利益'],100001)
        changed = build_draft(rows, copy.deepcopy(rows), sources, target, -2, metadata,pricing_choice=choice)
        self.assertIn('利益配分案',[i['種類'] for i in changed['unresolved']])
        self.assertEqual(changed['payload']['値引き'],0)

    def test_old_choice_is_invalid_after_markup_or_detail_changes(self):
        pending = draft_for(self.ODD, 100001)
        old_choice = {'signature': pending['profit_plan']['signature'], 'id': 'rounding_discount'}
        changed_markup = draft_for(self.ODD, 100002, choice=old_choice)
        self.assertIn('利益配分案', [issue['種類'] for issue in changed_markup['unresolved']])
        rows, sources, metadata = quote_fixture(self.ODD)
        edited = copy.deepcopy(rows)
        edited[0]['品名'] = '下地補修工事'
        edited[0]['訂正理由'] = '原本の品名を再確認'
        changed_detail = build_draft(rows, edited, sources,
                                     [Target('全体', '全体', '上乗せ額', 100001)],
                                     0, metadata, pricing_choice=old_choice)
        self.assertIn('利益配分案', [issue['種類'] for issue in changed_detail['unresolved']])
        self.assertEqual(changed_detail['payload']['値引き'], 0)

    def test_choice_widget_state_is_cleared_and_not_restored_on_revert(self):
        key = 'review_profit_choice_' + 'a' * 64
        state = {key: 'rounding_discount', '_work_draft_inputs': {key: 'rounding_discount'}}
        prepare_profit_choices(state, 'A')
        prepare_profit_choices(state, 'B')
        self.assertNotIn(key, state)
        self.assertNotIn(key, state['_work_draft_inputs'])
        prepare_profit_choices(state, 'A')
        self.assertNotIn(key, state)

    def test_concentrated_small_item_is_flagged(self):
        source = [{'品名': '小工事', '数量': 1, '単位': '箇所', '原価単価': 10000, '原価金額': 10000},
                  {'品名': '主工事', '数量': 1, '単位': '箇所', '原価単価': 90000, '原価金額': 90000}]
        priced = pd.DataFrame([{**source[0], '見積単価': 80000, '見積金額': 80000},
                               {**source[1], '見積単価': 120000, '見積金額': 120000}])
        warnings = _natural_price_warnings(source, priced, [])
        self.assertTrue(any('利益が偏って' in warning for warning in warnings))

    def test_zero_markup_on_large_item_still_counts_toward_cost_share(self):
        source = [{'品名': '小工事', '数量': 1, '単位': '箇所', '原価単価': 10000, '原価金額': 10000},
                  {'品名': '主工事', '数量': 1, '単位': '箇所', '原価単価': 90000, '原価金額': 90000}]
        priced = pd.DataFrame([{**source[0], '見積単価': 80000, '見積金額': 80000},
                               {**source[1], '見積単価': 90000, '見積金額': 90000}])
        warnings = _natural_price_warnings(source, priced, [])
        self.assertTrue(any('利益が偏って' in warning for warning in warnings))

    def test_small_cost_item_with_nineteen_percent_of_profit_is_flagged(self):
        source = [{'品名': '小工事', '数量': 1, '単位': '式', '原価単価': 1000, '原価金額': 1000},
                  {'品名': '主工事', '数量': 1, '単位': '式', '原価単価': 99000, '原価金額': 99000}]
        priced = pd.DataFrame([{**source[0], '見積単価': 20000, '見積金額': 20000},
                               {**source[1], '見積単価': 180000, '見積金額': 180000}])
        self.assertTrue(any('利益が偏って' in warning
                            for warning in _natural_price_warnings(source, priced, [])))


if __name__ == '__main__':
    unittest.main()
