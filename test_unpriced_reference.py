"""Unpriced drawings are explicit references; priced originals cannot be hidden."""
import copy
import tempfile
import unittest
from streamlit.testing.v1 import AppTest
from estimate_review import (build_draft, approve, verify_approval, reference_material_allowed)


def example():
    priced={'id':'quote','name':'quote.pdf','pages':1,'confirmed':True,'tax_basis':'税抜','tax_rate':.1,
            'net_total':1000,'tax':100,'gross_total':1100}
    reference={'id':'drawing','name':'drawing.pdf','pages':1,'reference_only':True,
               'reference_confirmed':True,'confirmed':True,'read_errors':[{'page':1,'reason':'no prices'}],
               'amount_reading':{'net_total':None,'tax':None,'gross_total':None,'amount_notes':[]}}
    base={'行ID':'paid','原本ID':'quote','元ファイル':'quote.pdf','ページ':1,'品名':'実施する工事','仕様':'仕様A',
          '数量':1,'単位':'式','原価単価':1000,'原価金額':1000,'見積元':'架空施工社','工事種別':'塗装工事',
          '部位':'','採用':True,'集計のみ':False,'訂正理由':'','除外理由':'','顧客備考':''}
    rows=[base]+[dict(base,行ID=f'ref{n}',原本ID='drawing',元ファイル='drawing.pdf',品名=f'非価格デザイン{n}',
                     数量=2,単位='枚',原価単価='',原価金額='',見積元='不明',工事種別='未分類') for n in range(6)]
    edited=copy.deepcopy(rows)
    for row in edited[1:]:row.update(採用=False,除外理由='金額のない参考資料として、見積金額から除外')
    return rows,edited,[priced,reference]


def unpriced_review_app(temp, reference_first=False):
    return f'''
import copy
from unittest.mock import patch
import streamlit as st
from estimate_review import OriginalVault
from review_ui import render_review
store=OriginalVault({temp!r},'test-reference-ui')
if '_review_original_rows' not in st.session_state:
    from test_unpriced_reference import example
    rows,_,sources=example()
    if {reference_first!r}: sources.reverse()
    saved=[];readings={{}}
    for i,source in enumerate(sources):
        item=store.preserve(source['name'],f'original-test-{{i}}'.encode())
        old=source['id'];source.update(item)
        for row in rows:
            if row['原本ID']==old:row['原本ID']=source['id']
        source.pop('confirmed',None);source.pop('reference_only',None);source.pop('reference_confirmed',None)
        readings[source['id']]={{'net_total':1000,'tax':100,'gross_total':1100,'conflicts':[],'page':1}} if old=='quote' else {{'net_total':None,'tax':None,'gross_total':None,'amount_notes':[],'conflicts':[],'page':1}}
        saved.append(source)
    st.session_state['_review_original_rows']=rows
    st.session_state['_review_sources']=saved
    st.session_state['_review_amount_readings']=readings
    st.session_state['username']='test-reference-ui'
with patch('review_ui.vault',return_value=store),patch('source_review_ui._preview_data',return_value=None):
    render_review(st)
'''


class UnpricedReferenceTest(unittest.TestCase):
    def test_manual_correction_survives_reference_first_in_the_file_order(self):
        with tempfile.TemporaryDirectory() as temp:
            at=AppTest.from_string(unpriced_review_app(temp,reference_first=True)).run()
            self.assertEqual(list(at.exception),[])
            reference_id,quote_id=[s['id'] for s in at.session_state['_review_sources']]
            editor='review_source_items_'+quote_id+'_0'
            at.session_state[editor]={'edited_rows':{0:{'品名':'原本で確認した名称','訂正理由':'原本と照合済み'}},
                                      'added_rows':[],'deleted_rows':[]}
            at.run()
            self.assertEqual(at.session_state['_review_assistant_rows'][0]['品名'],'原本で確認した名称')
            at.checkbox(key='review_reference_only_'+reference_id).check().run()
            self.assertEqual(list(at.exception),[])
            self.assertEqual(at.session_state['_review_assistant_rows'][0]['品名'],'原本で確認した名称')
            self.assertEqual(at.session_state['_review_assistant_rows'][0]['訂正理由'],'原本と照合済み')
            self.assertEqual(at.session_state['_review_original_rows'][0]['品名'],'実施する工事')
            at.checkbox(key='review_reference_only_'+reference_id).uncheck().run()
            self.assertEqual(at.session_state['_review_assistant_rows'][0]['品名'],'原本で確認した名称')

    def test_six_originally_unpriced_lines_are_preserved_but_not_charged(self):
        original,edited,sources=example();before=copy.deepcopy(original)
        draft=build_draft(original,edited,sources)
        self.assertEqual(draft['unresolved'],[])
        self.assertEqual(draft['payload']['合計']['仕入原価'],1000)
        self.assertEqual(len(draft['payload']['原本明細']),7)
        self.assertEqual(len(draft['payload']['確認明細']),7)
        self.assertEqual(len(draft['payload']['計算明細']),1)
        self.assertEqual(original,before)
        self.assertEqual(draft['payload']['原本'][1]['read_errors'],[{'page':1,'reason':'no prices'}])
        approved=approve(draft,'test-actor')
        sources[1].update(reference_only=False,reference_confirmed=False,confirmed=False)
        changed=build_draft(original,copy.deepcopy(edited),sources)
        self.assertTrue(changed['unresolved'])
        with self.assertRaises(ValueError):verify_approval(changed,approved)

    def test_price_even_zero_or_totals_cannot_be_hidden_by_clearing_edited_values(self):
        variants=[('原価単価',0),('原価金額',0),('原価単価',500),('原価金額',1000)]
        for field,value in variants:
            with self.subTest(field=field,value=value):
                original,edited,sources=example();original[1][field]=value
                self.assertFalse(reference_material_allowed(original,sources[1]))
                draft=build_draft(original,edited,sources)
                self.assertTrue(any(i['種類']=='参考資料の確認' for i in draft['unresolved']))
        for field,value in [('net_total',0),('tax',0),('gross_total',1100),('amount_notes',[{'kind':'negotiated_total','value':1000}])]:
            with self.subTest(field=field):
                original,edited,sources=example();sources[1]['amount_reading'][field]=value
                self.assertFalse(reference_material_allowed(original,sources[1]))
                self.assertTrue(any(i['種類']=='参考資料の確認' for i in build_draft(original,edited,sources)['unresolved']))
        original,edited,sources=example();edited[1]['原価単価']=100
        self.assertTrue(any(i['種類']=='参考資料の確認' for i in build_draft(original,edited,sources)['unresolved']))

    def test_no_reference_without_explicit_choice_or_exclusion_record(self):
        for field,value in [('reference_confirmed',False),('confirmed',False)]:
            original,edited,sources=example();sources[1][field]=value
            self.assertTrue(any(i['種類']=='参考資料の確認' for i in build_draft(original,edited,sources)['unresolved']))
        original,edited,sources=example();edited[1]['採用']=True
        with self.assertRaises(ValueError):build_draft(original,edited,sources)
        original,edited,sources=example();edited[1]['除外理由']=''
        self.assertTrue(any(i['種類']=='参考資料の確認' for i in build_draft(original,edited,sources)['unresolved']))

    def test_reference_choice_unblocks_pricing_and_undo_restores_rows_and_resets_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            app=unpriced_review_app(temp)
            at=AppTest.from_string(app).run()
            self.assertEqual(list(at.exception),[])
            original=copy.deepcopy(at.session_state['_review_original_rows'])
            quote_id,reference_id=[s['id'] for s in at.session_state['_review_sources']]
            self.assertTrue(any('見積書ではないと思われます' in item.value for item in at.info))
            self.assertFalse(at.checkbox(key='review_reference_only_'+reference_id).value)
            self.assertFalse(any('非価格デザイン' in item.value for item in at.markdown))
            self.assertEqual(len(at.dataframe),2)
            self.assertNotIn('採用',at.dataframe[0].value.columns)
            self.assertNotIn('集計のみ',at.dataframe[0].value.columns)
            self.assertTrue(any(e.label=='詳しい設定（必要なときだけ）' for e in at.expander))
            at.checkbox(key='review_source_ok_'+quote_id).check().run()
            self.assertNotIn('review_consult',[b.key for b in at.button])
            for key in ('_review_preview','_review_approval','_review_result'):
                at.session_state[key]={'old':'must invalidate'}
            at.checkbox(key='review_reference_only_'+reference_id).check().run()
            self.assertEqual(list(at.exception),[])
            self.assertIn('review_consult',[b.key for b in at.button])
            self.assertEqual(at.session_state['_review_original_rows'],original)
            self.assertTrue(all(not r['採用'] and r['除外理由'] for r in at.session_state['_review_assistant_rows'][1:]))
            self.assertTrue(all(r['原価単価']=='' and r['原価金額']=='' for r in at.session_state['_review_assistant_rows'][1:]))
            for key in ('_review_approval','_review_result'):
                self.assertNotIn(key,at.session_state)
            at.checkbox(key='review_reference_only_'+reference_id).uncheck().run()
            self.assertEqual(list(at.exception),[])
            self.assertTrue(all(r['採用'] for r in at.session_state['_review_assistant_rows'][1:]))
            self.assertNotIn('review_consult',[b.key for b in at.button])
            self.assertEqual(at.session_state['_review_workflow_step'],2)
            at.button(key='review_reparse_'+reference_id).click().run()
            self.assertTrue(at.session_state['_force_analyze'])
            self.assertEqual(at.session_state['_review_reparse_source'],reference_id)
