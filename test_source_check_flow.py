import tempfile
import unittest
from streamlit.testing.v1 import AppTest


class MultipleSourceCheckTest(unittest.TestCase):
    def test_three_sources_must_each_match_before_pricing_and_no_save_clutter(self):
        with tempfile.TemporaryDirectory() as temp:
            app=f'''
import os
os.environ['CYCA_DATA_DIR']={temp!r}
import streamlit as st
from review_ui import vault,render_review,render_archive
from estimate_pipeline import build_intermediate_dataframe
from estimate_review import review_rows
if '_review_original_rows' not in st.session_state:
    st.session_state['username']='synthetic'
    sources=[];records=[];summaries=[];readings={{}}
    for i in range(3):
        name=f'source-{{i}}.pdf'
        source=vault(st).preserve(name,f'synthetic {{i}}'.encode());source['pages']=2;sources.append(source)
        records.append(dict(品名=f'原本{{i}}の品目',仕様='原本の仕様',数量=1,単位='式',単価=100000,金額=100000,見積元=f'会社{{i}}',工事種別='防水工事',__source_name=name,__page_number=2))
        summaries.append({{'元ファイル':name,'工事名称':f'工事{{i}}'}})
        readings[source['id']]={{'net_total':100000,'tax':10000,'gross_total':110000,'conflicts':[],'page':1}}
    detail,_=build_intermediate_dataframe(records)
    st.session_state['_review_sources']=sources
    st.session_state['_review_original_rows']=review_rows(detail,sources)
    for row in st.session_state['_review_original_rows']:row['集計のみ']=False
    st.session_state['_review_metadata']={{'summary_sources':summaries}}
    st.session_state['_review_amount_readings']=readings
render_review(st)
render_archive(st)
'''
            at=AppTest.from_string(app).run()
            self.assertEqual(list(at.exception),[])
            ids=[s['id'] for s in at.session_state['_review_sources']]
            self.assertEqual(len(at.get('download_button')),0)
            self.assertNotIn('review_consult',[b.key for b in at.button])
            for sid in ids[:-1]:
                at.checkbox(key='review_source_ok_'+sid).check().run()
                self.assertEqual(list(at.exception),[])
                self.assertNotIn('review_consult',[b.key for b in at.button])
            at.checkbox(key='review_source_ok_'+ids[-1]).check().run()
            self.assertIn('review_consult',[b.key for b in at.button])
            self.assertEqual(len(at.get('download_button')),0)
            at.number_input(key='review_net_'+ids[0]).set_value(99999).run()
            self.assertFalse(at.checkbox(key='review_source_ok_'+ids[0]).value)
            self.assertTrue(at.checkbox(key='review_source_ok_'+ids[0]).disabled)
            self.assertNotIn('review_consult',[b.key for b in at.button])

    def test_special_amount_stays_unconfirmed_until_a_decision_is_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            app=f'''
import streamlit as st
from estimate_review import OriginalVault
from source_review_ui import render_source
store=OriginalVault({temp!r},'synthetic')
source=store.preserve('synthetic.pdf',b'fixture')
source['pages']=1
sid=source['id']
st.session_state['_review_amount_readings']={{sid:{{'net_total':100000,'tax':10000,'gross_total':110000,'conflicts':[],'amount_notes':[{{'kind':'negotiated_total','value':108000,'page':1}}]}}}}
rows=[dict(行ID='test',原本ID=sid,品名='架空工事',仕様='',数量=1,単位='式',原価単価=100000,原価金額=100000,ページ=1,見積元='架空社',工事種別='内装工事')]
render_source(st,source,store,1,rows)
st.session_state['source_result']=source
'''
            at=AppTest.from_string(app).run()
            sid=at.session_state['source_result']['id']
            self.assertEqual(list(at.exception),[])
            self.assertTrue(at.checkbox(key='review_source_ok_'+sid).disabled)
            at.text_input(key='review_amount_notes_'+sid).set_value('架空試験：今回は明細の金額を採用').run()
            self.assertFalse(at.checkbox(key='review_source_ok_'+sid).disabled)
            at.checkbox(key='review_source_ok_'+sid).check().run()
            self.assertTrue(at.session_state['source_result']['confirmed'])
