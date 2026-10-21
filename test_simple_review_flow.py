"""Novice flow: normal totals are automatic, mismatches remain a hard stop."""
from pathlib import Path
import tempfile
import unittest
from streamlit.testing.v1 import AppTest
import fitz


class SimpleReviewFlowTest(unittest.TestCase):
    def app(self, temp, *, amount=100000, read_errors=False):
        p=Path(temp)/'synthetic.pdf';doc=fitz.open();page=doc.new_page()
        for y,text in [(50,'税抜合計 100,000円'),(80,'消費税 10,000円'),(110,'税込合計 110,000円')]:
            page.insert_text((30,y),text,fontname='japan',fontsize=12)
        p.write_bytes(doc.tobytes());doc.close()
        return f'''
import streamlit as st
from estimate_review import OriginalVault
from source_review_ui import render_source
store=OriginalVault({str(Path(temp)/'vault')!r},'synthetic')
source=store.preserve('synthetic.pdf',open({str(p)!r},'rb').read())
source['pages']=1
source['read_errors']={([{'page':1}] if read_errors else [])!r}
rows=[{{'行ID':'row-1','原本ID':source['id'],'ページ':1,'採用':True,'訂正理由':'','見積元':'架空社','工事種別':'防水工事','品名':'架空工事','数量':1,'単位':'式','原価単価':{amount},'原価金額':{amount}}}]
render_source(st,source,store,1,rows)
st.session_state['source_result']=source
'''

    def test_normal_original_is_ready_without_tax_or_amount_input(self):
        with tempfile.TemporaryDirectory() as temp:
            app=self.app(temp);at=AppTest.from_string(app).run()
            self.assertEqual(list(at.exception),[])
            source=at.session_state['source_result'];sid=source['id']
            self.assertEqual(source['net_total'],100000)
            self.assertEqual(source['tax_rate'],.10)
            self.assertFalse(source['confirmed'])
            self.assertEqual(len(at.warning),0)
            self.assertFalse(at.checkbox(key='review_source_ok_'+sid).disabled)
            at.checkbox(key='review_source_ok_'+sid).check().run()
            self.assertTrue(at.session_state['source_result']['confirmed'])
            at.number_input(key='review_net_'+sid).set_value(99999).run()
            self.assertFalse(at.session_state['source_result']['confirmed'])
            self.assertTrue(at.checkbox(key='review_source_ok_'+sid).disabled)
            self.assertEqual(len(at.warning),1)
            self.assertEqual(len(at.error),0)

    def test_mismatched_detail_has_one_actionable_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            at=AppTest.from_string(self.app(temp,amount=90000)).run()
            source=at.session_state['source_result']
            self.assertEqual(len(at.warning),1)
            self.assertIn('10,000円違います',at.warning[0].value)
            self.assertEqual(len(at.error),0)
            self.assertTrue(at.checkbox(key='review_source_ok_'+source['id']).disabled)

    def test_unread_page_cannot_be_confirmed(self):
        with tempfile.TemporaryDirectory() as temp:
            at=AppTest.from_string(self.app(temp,read_errors=True)).run()
            self.assertTrue(at.checkbox(key='review_source_ok_'+at.session_state['source_result']['id']).disabled)

    def test_findings_are_silent_before_check_and_deduplicated_after(self):
        for ready,count in [(False,0),(True,1)]:
            at=AppTest.from_string(f'''
import streamlit as st
from review_findings_ui import render_findings
item={{'id':'one','種類':'原本小計','内容':'品目の金額を確認','行':[],'レベル':'停止'}}
render_findings(st,{{'payload':{{'指摘':[item,dict(item,id='two')],'計算明細':[]}}}},{ready!r})
''').run()
            self.assertEqual(list(at.exception),[])
            self.assertEqual(len(at.warning),count)
            self.assertEqual(len(at.error),0)

    def test_repeated_findings_have_one_confirmation_for_the_visible_group(self):
        at=AppTest.from_string("""
import streamlit as st
from review_findings_ui import render_findings
items=[{'id':str(n),'種類':'共通費重複疑い','内容':'複数原本に足場があります','行':[],'レベル':'確認'} for n in range(4)]
st.session_state['answers']=render_findings(st,{'payload':{'指摘':items,'計算明細':[]}},True)
""").run()
        self.assertEqual(len(at.text_input),1)
        at.text_input[0].set_value('検証用・施工範囲を確認').run()
        self.assertEqual(len(at.session_state['answers']),4)
        self.assertTrue(all(value=='検証用・施工範囲を確認' for value in at.session_state['answers'].values()))

    def test_file_without_items_does_not_ask_for_a_total_to_force_it_through(self):
        with tempfile.TemporaryDirectory() as temp:
            app=self.app(temp).replace('render_source(st,source,store,1,rows)','render_source(st,source,store,1,[])')
            at=AppTest.from_string(app).run()
            self.assertEqual(list(at.exception),[])
            self.assertEqual(len(at.warning),1)
            self.assertIn('品目を読み取れません',at.warning[0].value)
            self.assertEqual(len(at.number_input),0)
            self.assertFalse(at.session_state['source_result']['confirmed'])

    def test_original_save_is_absent_until_completed_output(self):
        with tempfile.TemporaryDirectory() as temp:
            at=AppTest.from_string(f'''
import os
os.environ['CYCA_DATA_DIR']={temp!r}
import streamlit as st
from review_ui import vault,render_archive
st.session_state['username']='synthetic'
vault(st).preserve('synthetic.pdf',b'synthetic test')
render_archive(st)
''').run()
            self.assertEqual(list(at.exception),[])
            self.assertEqual(len(at.sidebar),0)
            labels=[d.label for d in at.get('download_button')]
            self.assertEqual(labels,[])
            at.session_state['_review_export_ready']=True
            at.run()
            self.assertEqual([d.label for d in at.get('download_button')],['作業データをまとめて保存'])
