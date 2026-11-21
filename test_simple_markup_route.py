"""No redundant choice for exact ordinary markup; substantive guards remain."""
import copy
import unittest
from unittest.mock import patch
import pandas as pd
from estimate_review import Target, build_draft, preview_allowed, approve, _natural_price_warnings
from test_profit_proposals import quote_fixture, draft_for
import test_profit_proposals as profit_fixture
from pricing_options import choice_for

class SimpleMarkupPolicy(unittest.TestCase):
    def test_only_cosmetic_rounding_allows_exact_no_discount_without_selection(self):
        d=draft_for(profit_fixture.ProfitProposalsTest.ODD,100001)
        self.assertTrue(d['pricing_options']['ordinary_exact'])
        self.assertTrue(d['pricing_options']['rounding_notes'])
        self.assertEqual(d['pricing_options']['blocking_warnings'],[])
        self.assertEqual(d['unresolved'],[])
        self.assertTrue(preview_allowed(d))
        self.assertEqual(d['payload']['合計']['粗利益'],100001)
        self.assertEqual(d['payload']['値引き'],0)
        self.assertIsNone(d['payload']['利益配分案'])
    def test_amount_grid_failure_still_requires_explicit_matching_proposal(self):
        d=draft_for([('大量材料',100000,'個',2)],19999)
        self.assertIsNone(d['pricing_options']['no_discount'])
        self.assertFalse(preview_allowed(d))
        with self.assertRaises(ValueError):approve(d,'fictional')
        selected=draft_for([('大量材料',100000,'個',2)],19999,choice=choice_for(d['pricing_options'],True))
        self.assertEqual(selected['unresolved'],[])
        self.assertEqual(selected['payload']['合計']['粗利益'],19999)
        self.assertEqual(selected['payload']['値引き'],-1)
    def test_explicit_cap_override_is_not_treated_as_cosmetic(self):
        rows,sources,meta=quote_fixture(profit_fixture.ProfitProposalsTest.NATURAL)
        d=build_draft(rows,rows,sources,[Target('全体','全体','上乗せ額',100000,True)],metadata=meta)
        self.assertTrue(d['pricing_options']['requires_confirmation'])
        self.assertTrue(any('上限' in x for x in d['pricing_options']['blocking_warnings']))
        self.assertFalse(preview_allowed(d))
    def test_profit_bias_low_prices_and_welfare_basis_remain_substantive(self):
        source=[dict(行ID='small',品名='小工事',数量=1,単位='式',原価単価=1000,原価金額=1000),
                dict(行ID='large',品名='主工事',数量=1,単位='式',原価単価=99000,原価金額=99000)]
        priced=pd.DataFrame([{**source[0], '見積単価':20000,'見積金額':20000},{**source[1],'見積単価':180000,'見積金額':180000}])
        self.assertTrue(any('利益が偏って' in x for x in _natural_price_warnings(source,priced,[],substantive_only=True)))
        for name,unit,cost,rate in [('資材','個',2,2.2),('法定福利費','式',20000,20123)]:
            row=dict(行ID='x',品名=name,数量=1,単位=unit,原価単価=cost,原価金額=cost)
            p=pd.DataFrame([{**row,'見積単価':rate,'見積金額':rate}])
            self.assertTrue(_natural_price_warnings([row],p,[],substantive_only=True))
    def test_unknown_typed_finding_is_blocking_by_default(self):
        rows,sources,meta=quote_fixture(profit_fixture.ProfitProposalsTest.NATURAL)
        with patch('round_pricing.price_findings',return_value=[dict(kind='future_risk',row_id=rows[0]['行ID'],message='未分類の重要な確認')]):
            d=build_draft(rows,rows,sources,[Target('全体','全体','上乗せ額',100000)],metadata=meta)
        self.assertTrue(d['pricing_options']['requires_confirmation'])
        self.assertFalse(preview_allowed(d))
    def test_source_mismatch_is_not_bypassed_by_ordinary_pricing(self):
        rows,sources,meta=quote_fixture(profit_fixture.ProfitProposalsTest.ODD)
        sources[0]['net_total']-=1
        d=build_draft(rows,rows,sources,[Target('全体','全体','上乗せ額',100001)],metadata=meta)
        self.assertFalse(preview_allowed(d))
        self.assertIn('原本小計',[i['種類'] for i in d['unresolved']])
    def test_comparison_is_inert_until_explicitly_adopted_and_stale_choice_fails(self):
        d=draft_for(profit_fixture.ProfitProposalsTest.ODD,100001)
        self.assertIsNotNone(d['pricing_options']['proposal'])
        self.assertEqual(d['payload']['値引き'],0)
        chosen=draft_for(profit_fixture.ProfitProposalsTest.ODD,100001,choice=choice_for(d['pricing_options'],True))
        self.assertLess(chosen['payload']['値引き'],0)
        self.assertEqual(chosen['payload']['合計']['粗利益'],100001)
        stale=draft_for(profit_fixture.ProfitProposalsTest.ODD,110001,choice=choice_for(d['pricing_options'],True))
        self.assertFalse(preview_allowed(stale))
