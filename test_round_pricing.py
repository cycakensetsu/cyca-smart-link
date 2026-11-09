import copy
import unittest
from estimate_review import Target
from pricing_options import build_options, choice_for, selected
from pricing_options_ui import reconcile_inputs
from round_pricing import custom_candidate, validate_settings
from test_markup_rules import frame

class RoundPricingTest(unittest.TestCase):
    def setUp(self):
        f,_ = frame([('主工事',10,'m',10000),('補修',100,'㎡',50),('加工',10,'個',400),('法定福利費',1,'式',25621),('運搬',10,'kg',130)])
        self.rows = f.to_dict('records')
        for i,r in enumerate(self.rows): r['行ID']=str(i)
        self.targets = [Target('全体','全体','上乗せ額',350000)]
        self.settings = {'step':50,'limit':100000,'rates':{'0':45000,'1':150,'2':500,'3':30000,'4':130},'welfare_reason':'原本と算定対象を確認した提案'}

    def options(self, settings=None, **kwargs):
        return build_options(self.rows,self.targets,rounding=settings or self.settings,**kwargs)

    def test_exact_net_and_source_facts_and_explicit_selection(self):
        before=copy.deepcopy(self.rows)
        options=self.options();proposal=options['proposal']
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal['totals']['最終利益'],350000)
        self.assertEqual(proposal['rows'][1]['見積単価'],150)
        self.assertEqual(proposal['rows'][2]['見積単価'],500)
        self.assertEqual(proposal['rows'][3]['見積金額'],30000)
        self.assertEqual(proposal['rows'][4]['見積単価'],130)
        self.assertEqual(self.rows,before)
        self.assertEqual(selected(options,choice_for(options,True)),proposal)
        if options['no_discount']:
            self.assertEqual(selected(options)['totals']['顧客値引き'],0)
        for old,new in zip(before,proposal['rows']):
            for key in ('数量','原価単価','原価金額'): self.assertEqual(old[key],new[key])

    def test_200_and_welfare_25000_are_explicit_not_auto(self):
        config=copy.deepcopy(self.settings);config['rates'].update({'1':200,'3':25000})
        proposal=self.options(config)['proposal']
        self.assertEqual(proposal['rows'][1]['見積単価'],200)
        self.assertEqual(proposal['rows'][3]['見積金額'],25000)
        del config['rates']['3'];config['welfare_reason']=''
        proposal=self.options(config)['proposal']
        self.assertEqual(proposal['rows'][3]['見積金額'],25621)

    def test_welfare_reason_required(self):
        config=copy.deepcopy(self.settings);config['welfare_reason']=''
        with self.assertRaises(ValueError):self.options(config)

    def test_fixed_negative_missing_nan_and_below_cost_blocked(self):
        for value in (-1,float('nan'),float('inf'),149.5,True):
            config=copy.deepcopy(self.settings);config['rates']['1']=value
            with self.assertRaises(ValueError):self.options(config)
        self.rows[1]['単価固定']=True
        with self.assertRaises(ValueError):self.options()
        self.rows[1]['単価固定']=False;self.rows[1]['原価金額']=-5
        with self.assertRaises(ValueError):self.options()

    def test_context_changes_invalidate_choice_and_undo_does_not_restore(self):
        options=self.options();choice=choice_for(options,True)
        for key,value in [('step',100),('limit',90000),('welfare_reason','再確認')]:
            config=copy.deepcopy(self.settings);config[key]=value
            with self.assertRaises(ValueError):selected(self.options(config),choice)
            state={'review_pricing_choice':choice,'_work_draft_inputs':{'review_pricing_choice':choice}}
            self.assertTrue(reconcile_inputs(state,self.rows,self.targets,.1,0,config))
            self.assertNotIn('review_pricing_choice',state)
            self.assertNotIn('review_pricing_choice',state['_work_draft_inputs'])

    def test_scope_discount_limit_insufficient_and_zero(self):
        for targets in ([Target('業者別',self.rows[0]['見積元'],'上乗せ額',350000)],):
            options=build_options(self.rows,targets,rounding=self.settings)
            self.assertIsNone(options['proposal']);self.assertIn('全体',options['proposal_error'])
        self.assertIsNone(self.options(requested_discount=-1000)['proposal'])
        config=copy.deepcopy(self.settings);config['limit']=0
        self.assertIsNone(self.options(config)['proposal'])
        config['rates']['0']=10000
        self.assertIsNone(self.options(config)['proposal'])
        zero=build_options(self.rows,[Target('全体','全体','上乗せ額',0)],rounding=self.settings)
        self.assertIsNone(zero['proposal']);self.assertEqual(zero['no_discount']['totals']['顧客値引き'],0)
        from round_pricing import price_flags
        self.assertEqual(price_flags(self.rows,zero['no_discount']['rows']),[])

    def test_saved_rounding_options_reject_invalid_values(self):
        from work_drafts import validate_draft
        value={'schema':1,'kind':'working_draft','owner':'a'*64,'work_id':'b'*32,
               'extraction_id':'c'*64,'saved_at':'2026-11-09T00:00:00+00:00',
               'state':{'_review_assistant_rows':[{}]}}
        for key,bad in [('review_round_step',42),('review_round_low_floor',999),('review_round_limit',-1),('review_round_limit',1000001)]:
            changed=copy.deepcopy(value);changed['state'][key]=bad
            with self.assertRaises(ValueError):validate_draft(changed)
        value['state'].update(review_round_step=50,review_round_low_floor=150,review_round_limit=100000)
        self.assertEqual(validate_draft(value),value)

    def test_malformed_source_rates_are_not_editable(self):
        from round_pricing import adjustable
        for value in ('bad', None, float('nan'), float('inf'), -1):
            row = dict(self.rows[0], 原価単価=value)
            self.assertFalse(adjustable(row))

    def test_zero_adjustment_is_selectable(self):
        settings=copy.deepcopy(self.settings);settings['rates'].update({'0':43500,'1':200,'2':400,'3':25621})
        options=self.options(settings)
        self.assertIsNotNone(options['proposal'])
        self.assertEqual(options['proposal']['totals']['顧客値引き'],0)
        self.assertEqual(selected(options,choice_for(options,True))['totals']['最終利益'],350000)

if __name__=='__main__':unittest.main()

class RoundPricingUIAppTest(unittest.TestCase):
    def test_custom_choice_invalidation_and_no_discount(self):
        import tempfile
        from streamlit.testing.v1 import AppTest
        from estimate_review import digest
        with tempfile.TemporaryDirectory() as folder:
            app=f'''
import os
os.environ['CYCA_DATA_DIR']={folder!r}
import streamlit as st
from estimate_review import review_rows
from test_markup_rules import frame
from review_ui import _render_review_plan
if 'original' not in st.session_state:
    f,_=frame([('主工事',10,'m',10000),('補修',100,'㎡',50),('加工',10,'個',400),('法定福利費',1,'式',25621),('運搬',10,'kg',130)])
    f['元ファイル']='synthetic.pdf';f['ページ']=1;f['工事種別']='工事'
    source={{'id':'a'*64,'name':'synthetic.pdf','pages':1,'confirmed':True,'tax_basis':'税抜','tax_rate':.1,'net_total':135921}}
    st.session_state['original']=review_rows(f,[source]);st.session_state['sources']=[source]
    st.session_state['username']='synthetic-user'
    st.session_state['_review_metadata']={{'宛名':'検証用・提出不可','工事名称':'単価案検証'}}
_render_review_plan(st,'',st.session_state['original'],st.session_state['original'],st.session_state['sources'])
'''
            at=AppTest.from_string(app,default_timeout=20).run()
            target='review_target_'+digest(['全体','上乗せ額','全体'])
            at.number_input(key=target).set_value(350000).run()
            self.assertEqual(list(at.exception),[])
            self.assertTrue(any('端数' in x.value for x in at.warning))
            self.assertTrue(at.button(key='review_approve').disabled)
            at.checkbox(key='review_round_enabled').check().run()
            for checkbox in list(at.checkbox):
                if checkbox.key.startswith('review_round_override_'):checkbox.check()
            at.run()
            desired={'主工事':45000,'補修':150,'加工':500,'法定福利費':30000,'運搬':130}
            for widget in at.number_input:
                if widget.key.startswith('review_round_rate_'):
                    name=widget.label.split('：')[0];widget.set_value(desired[name])
            at.text_input(key='review_round_welfare_reason').set_value('算定根拠を確認した提案')
            at.run()
            self.assertEqual(list(at.exception),[])
            at.button(key='review_adopt_discount').click().run()
            self.assertEqual(at.session_state['review_pricing_choice']['mode'],'discount_adjustment')
            self.assertTrue(any('350,000' in m.value for m in at.metric if '最終利益' in m.label))
            at.button(key='review_preview').click().run()
            next(c for c in at.checkbox if c.key.startswith('review_agree_')).check().run()
            at.button(key='review_approve').click().run()
            self.assertEqual(list(at.exception),[])
            self.assertTrue(at.session_state['_review_result'].ok)
            payload=at.session_state['_review_approval']['payload']
            self.assertEqual(payload['合計']['粗利益'],350000)
            self.assertEqual(next(r['見積単価'] for r in payload['計算明細'] if r['品名']=='補修'),150)
            at.selectbox(key='review_round_step').select(100).run()
            self.assertNotIn('review_pricing_choice',at.session_state)
            self.assertFalse(any('見積書ができました' in s.value for s in at.success))
            at.selectbox(key='review_round_step').select(50).run()
            self.assertNotIn('review_pricing_choice',at.session_state)
            at.button(key='review_reject_discount').click().run()
            self.assertEqual(at.session_state['review_pricing_choice']['mode'],'none')
            self.assertEqual(list(at.exception),[])
