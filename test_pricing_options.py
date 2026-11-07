import copy
import hashlib
from io import BytesIO
import json
import tempfile
import unittest

import fitz
from openpyxl import load_workbook

from estimate_pipeline import _allocate_group_markup
from estimate_review import Target, build_draft, approve, build_approved_estimate, estimate_for_output
from markup_rules import line_amount, measured_add_cap
from pricing_options import build_options, choice_for, selected
from pricing_options_ui import set_choice, reconcile_choice, KEY
import test_estimate_review as review_fixture
from test_markup_rules import frame


class PricingOptionsTest(unittest.TestCase):
    def example(self):
        f,_=frame([('構造材',427,'kg',345),('小材料',12,'個',3000),
                   ('取付工事',1,'式',150000),('諸経費',1,'式',65000)])
        return f.to_dict('records')

    def test_cost_based_caps_are_smooth_and_not_based_on_raw_unit_price(self):
        self.assertEqual(measured_add_cap(147315,.5),147315)
        for rate in (499,500,999,1000,2999,3000):
            f,_=frame([('材料',10,'個',rate),('工事',10,'m',10000)])
            out=_allocate_group_markup(f,int(f['原価金額'].sum())+20000)
            self.assertLessEqual(out.iloc[0]['上乗せ額']/out.iloc[0]['原価金額'],.6)

    def test_slope_like_mix_no_small_material_profit_sink(self):
        rows=self.example(); before=copy.deepcopy(rows)
        o=build_options(rows,[Target('全体','全体','上乗せ額',200000)])
        plan=selected(o)
        self.assertEqual(rows,before)
        self.assertEqual(plan['totals']['最終利益'],200000)
        block=plan['rows'][1];steel=plan['rows'][0]
        self.assertLess(block['見積単価'],6001)
        self.assertGreater(steel['上乗せ額'],block['上乗せ額'])
        for r in plan['rows']:
            self.assertEqual(line_amount(r['数量'],r['見積単価']),r['見積金額'])

    def test_propose_adopt_reject_zero_and_repeat_never_double_applies(self):
        rows=self.example(); targets=[Target('全体','全体','上乗せ額',200000)]
        o=build_options(rows,targets)
        self.assertIsNotNone(o['proposal'])
        self.assertEqual(selected(o)['totals']['顧客値引き'],0)
        yes=choice_for(o,True); no=choice_for(o,False)
        for choice in (yes,yes,no,yes,no,None):
            p=selected(o,choice)
            self.assertEqual(p['totals']['最終利益'],200000)
            self.assertEqual(p['totals']['税抜売価']-p['totals']['仕入原価'],200000)
            self.assertEqual(p['totals']['顧客値引き']!=0, choice==yes)
        z=build_options(rows,[Target('全体','全体','上乗せ額',0)])
        self.assertIsNone(z['proposal'])
        self.assertEqual(selected(z)['totals']['顧客値引き'],0)

    def test_old_choice_is_bound_to_prices_quantity_fixed_flags_and_tax(self):
        rows=self.example(); target=[Target('全体','全体','上乗せ額',200000)]
        o=build_options(rows,target); old=choice_for(o,True)
        for change in ('profit','quantity','fixed','tax','request'):
            changed=copy.deepcopy(rows);t=target;tax=.1;request=0
            if change=='profit':t=[Target('全体','全体','上乗せ額',190000)]
            if change=='quantity':changed[1].update(数量=11,原価金額=33000)
            if change=='fixed':changed[1]['単価固定']=True
            if change=='tax':tax=.08
            if change=='request':request=-1000
            new=build_options(changed,t,tax,request)
            with self.assertRaises(ValueError):selected(new,old)
            state={KEY:copy.deepcopy(old),'_work_draft_inputs':{KEY:copy.deepcopy(old)}}
            self.assertTrue(reconcile_choice(state,new))
            self.assertNotIn(KEY,state)
            self.assertNotIn(KEY,state['_work_draft_inputs'])
            self.assertFalse(reconcile_choice(state,o),'old settings cannot resurrect adoption')

    def test_fixed_prices_huge_and_fractional_quantities(self):
        for qty,rate,profit in [(100000,2,20000),(1000.5,200,20010),(.125,80000,1000),(6.5,18000,10001)]:
            with self.subTest(qty=qty):
                f,_=frame([('実数量工事',qty,'m',rate),('別作業',1,'箇所',20000)])
                f['単価固定']=[False,True]
                out=_allocate_group_markup(f,int(f['原価金額'].sum())+profit)
                self.assertEqual(out.iloc[1]['見積単価'],20000)
                self.assertEqual(out.iloc[1]['上乗せ額'],0)
                self.assertEqual(out['上乗せ額'].sum(),profit)
                for _,r in out.iterrows():self.assertEqual(line_amount(r['数量'],r['見積単価']),r['見積金額'])

    def test_invalid_edit_then_undo_cannot_revive_an_old_adoption(self):
        from pricing_options_ui import reconcile_inputs
        rows=self.example();targets=[Target('全体','全体','上乗せ額',200000)]
        o=build_options(rows,targets);choice=choice_for(o,True)
        state={KEY:choice,'_work_draft_inputs':{KEY:choice}}
        bad=copy.deepcopy(rows);bad[0]['数量']=None
        self.assertTrue(reconcile_inputs(state,bad,targets,.1,0))
        self.assertNotIn(KEY,state['_work_draft_inputs'])
        self.assertFalse(reconcile_inputs(state,rows,targets,.1,0))
        self.assertNotIn(KEY,state)

    def test_infeasible_price_grid_can_only_apply_attainable_proposal_after_selection(self):
        f,_=frame([('大量材料',100000,'個',2)])
        o=build_options(f.to_dict('records'),[Target('全体','全体','上乗せ額',19999)])
        self.assertIsNone(o['no_discount'])
        with self.assertRaises(ValueError):selected(o)
        p=selected(o,choice_for(o,True))
        self.assertEqual(p['totals']['顧客値引き'],-1)
        self.assertEqual(p['totals']['最終利益'],19999)

    def test_multiple_scopes_preserve_requested_final_profit_and_source_credits(self):
        f,_=frame([('工事A',10,'m',10000),('工事B',20,'m',10000),('仕入値引き',1,'式',-5000)])
        f.loc[1,'見積元']='B社'
        rows=f.to_dict('records')
        o=build_options(rows,[Target('業者別','テスト工務店','上乗せ額',12345),Target('業者別','B社','上乗せ額',23456)],requested_discount=-1000)
        p=selected(o,choice_for(o,True))
        self.assertEqual(p['totals']['最終利益'],35801)
        self.assertEqual(p['rows'][2]['見積金額'],-5000)
        self.assertEqual(p['totals']['顧客値引き'],-1000)

    def test_reject_and_adopt_persist_in_real_private_draft_and_zip(self):
        from estimate_review import OriginalVault
        from test_work_drafts import working
        from work_drafts import save_draft,restore_draft,hydrate_inputs
        from vault_archive import archive_bytes,restore_archive
        o=build_options(self.example(),[Target('全体','全体','上乗せ額',200000)])
        with tempfile.TemporaryDirectory() as folder:
            store=OriginalVault(folder,'owner');state=working(store)
            set_choice(state,choice_for(o,True))
            key=save_draft(state,store);fresh={}
            restore_draft(fresh,store,key)
            self.assertEqual(fresh[KEY],state[KEY])
            self.assertEqual(selected(o,fresh[KEY])['totals']['最終利益'],200000)
            set_choice(fresh,choice_for(o,False)); key2=save_draft(fresh,store)
            later={};restore_draft(later,store,key2)
            self.assertEqual(selected(o,later[KEY])['totals']['顧客値引き'],0)
            later.pop(KEY);hydrate_inputs(later)
            self.assertEqual(later[KEY]['mode'],'none')
            backup=archive_bytes(store)
            new=OriginalVault(folder+'/restore','owner');restore_archive(new,backup)
            self.assertEqual(new.get_entry(f'drafts/{key2}.json'),store.get_entry(f'drafts/{key2}.json'))


class DiscountOutputTest(unittest.TestCase):
    def test_preview_payload_pdf_and_excel_use_the_same_selected_totals(self):
        fixture=review_fixture.EstimateReviewTest();fixture.setUp()
        rows=fixture.rows; source=fixture.source; targets=[Target('全体','全体','上乗せ額',10000)]
        before=copy.deepcopy(rows)
        d=build_draft(rows,rows,[source],targets,-1000,fixture.meta)
        options=d['pricing_options']
        for compact in (True,False):
            for adopt in (False,True,False):
                selected_draft=build_draft(rows,rows,[source],targets,-1000,fixture.meta,
                    pricing_choice=choice_for(options,adopt))
                self.assertFalse(selected_draft['unresolved'])
                estimate=estimate_for_output(selected_draft)
                estimate.source_count=1 if compact else 2
                from template_fill_test_v2 import fill_estimate_v2
                result=fill_estimate_v2(estimate,save_to_disk=False)
                self.assertTrue(result.ok,result.errors)
                self.assertEqual(estimate.net_total,110000)
                self.assertEqual(estimate.tax,11000)
                self.assertEqual(estimate.grand_total,121000)
                wb=load_workbook(BytesIO(result.data))
                labels=[c.value.replace(' ','').replace('　','') for ws in wb for row in ws for c in row
                        if isinstance(c.value,str) and c.data_type!='f' and not ws.row_dimensions[c.row].hidden]
                self.assertEqual('値引き' in labels,adopt)
                if not compact:
                    self.assertEqual(wb['工事品目集計']['G24'].value,'=G22+G23' if adopt else '=G22')
                with fitz.open(stream=result.pdf_data,filetype='pdf') as pdf:
                    text=''.join(p.get_text() for p in pdf)
                    self.assertEqual('値引き' in text,adopt)
                    self.assertIn('121,000',text)
                    self.assertIn('110,000',text)
                    self.assertIn('11,000',text)
                self.assertEqual(rows,before)


if __name__=='__main__':unittest.main()
