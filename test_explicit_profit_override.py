import copy
import unittest

from estimate_pipeline import _allocate_group_markup
from estimate_review import Target, allocate, digest
from markup_rules import line_amount
from test_markup_rules import frame


class ExplicitProfitOverrideTest(unittest.TestCase):
    def test_default_still_rejects_and_explicit_target_reaches_the_exact_requested_profit(self):
        detail, _ = frame([('外壁塗装', 20, 'm²', 1500), ('養生諸経費', 1, '式', 50000),
                           ('原本値引き', 1, '式', -1000)])
        rows = detail.to_dict('records')
        before = copy.deepcopy(rows)
        with self.assertRaises(ValueError):
            allocate(rows, [Target('業者別', 'テスト工務店', '上乗せ額', 200000)])
        result = allocate(rows, [Target('業者別', 'テスト工務店', '上乗せ額', 200000, True)])
        self.assertEqual(result['上乗せ額'].sum(), 200000)
        self.assertEqual(rows, before)
        for i, row in result.iterrows():
            self.assertEqual(row['数量'], rows[i]['数量'])
            self.assertEqual(row['原価単価'], rows[i]['原価単価'])
            self.assertEqual(row['原価金額'], rows[i]['原価金額'])
            self.assertEqual(line_amount(row['数量'], row['見積単価']), row['見積金額'])
        self.assertEqual(result.iloc[2]['上乗せ額'], 0)
        self.assertLessEqual(result.iloc[1]['上乗せ額'], 25000)
        self.assertEqual(result.iloc[1]['上乗せ額'] % 5000, 0)

    def test_exception_never_relaxes_lump_amount_or_increment_limits(self):
        for cost, add in [(50000, 30000), (100000, 55000), (100000, 5001)]:
            with self.subTest(cost=cost, add=add):
                detail, _ = frame([('一式工事', 1, '式', cost)])
                with self.assertRaises(ValueError):
                    _allocate_group_markup(detail, cost + add, allow_low_price_cap_override=True)

    def test_only_the_selected_vendor_can_exceed_default_caps(self):
        detail, _ = frame([('工事A', 20, 'm²', 1500), ('工事B', 10, 'm', 500)])
        detail.loc[1, '見積元'] = '別の業者'
        rows = detail.to_dict('records')
        result = allocate(rows, [Target('業者別', 'テスト工務店', '上乗せ額', 150000, True)])
        self.assertEqual(result.iloc[1]['見積金額'], rows[1]['原価金額'])
        with self.assertRaises(ValueError):
            allocate(rows, [Target('業者別', 'テスト工務店', '上乗せ額', 150000, True),
                            Target('業者別', '別の業者', '上乗せ額', 50000)])

    def test_pooling_same_work_still_preserves_shared_customer_unit_price(self):
        detail, _ = frame([('2階仕上工事', 10, 'm', 500), ('3階仕上工事', 20, 'm', 500)])
        result = _allocate_group_markup(detail, 105000, allow_low_price_cap_override=True)
        self.assertEqual(result.iloc[0]['見積単価'], result.iloc[1]['見積単価'])
        self.assertEqual(result['見積金額'].sum(), 105000)

    def test_exception_flag_is_boolean_and_part_of_the_recorded_target(self):
        detail, _ = frame([('実数量工事', 10, 'm', 500)])
        for flag in (1, 'true', None):
            with self.subTest(flag=flag):
                with self.assertRaises(ValueError):
                    allocate(detail.to_dict('records'), [Target('全体', '全体', '上乗せ額', 1000, flag)])
        normal = Target('全体', '全体', '上乗せ額', 1000)
        explicit = Target('全体', '全体', '上乗せ額', 1000, True)
        self.assertNotEqual(digest(normal.__dict__), digest(explicit.__dict__))

    def test_price_changes_clear_live_and_retained_overrides_but_resume_keeps_same_price(self):
        from review_ui import prepare_cap_overrides
        key = 'review_cap_override_' + 'a'*64
        state = {key: True, '_work_draft_inputs': {key: True}}
        prepare_cap_overrides(state, 'price-A')
        self.assertTrue(state[key])
        prepare_cap_overrides(state, 'price-B')
        self.assertFalse(state[key])
        self.assertNotIn(key, state['_work_draft_inputs'])
        prepare_cap_overrides(state, 'price-A')
        self.assertFalse(state[key])


if __name__ == '__main__':
    unittest.main()
