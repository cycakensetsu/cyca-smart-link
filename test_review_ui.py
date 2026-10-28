"""Exercise the human approval path without calling an AI or using business data."""
from pathlib import Path
import tempfile
import unittest
from streamlit.testing.v1 import AppTest


class ReviewUITest(unittest.TestCase):
    def test_supplier_addressee_does_not_silently_become_customer(self):
        from review_ui import customer_default
        for name in ('彩架建設 株式会社 御中','彩架建設㈱ 様','株式会社 彩架建設'):
            self.assertEqual(customer_default({'宛名':name}),'')
        self.assertEqual(customer_default({'宛名':'架空のお客様 御中'}),'架空のお客様 御中')
        self.assertEqual(customer_default({'宛名':'彩架建設管理株式会社 御中'}),'彩架建設管理株式会社 御中')

    def test_approval_requires_checks_and_changes_hide_old_downloads(self):
        with tempfile.TemporaryDirectory() as temp:
            source=f'''
import os
os.environ['CYCA_DATA_DIR']={temp!r}
import streamlit as st
from review_ui import vault, render_review
from estimate_pipeline import build_intermediate_dataframe
from estimate_review import review_rows
if '_review_original_rows' not in st.session_state:
    st.session_state['username']='test-actor'
    source=vault(st).preserve('sample.pdf',b'local-synthetic-fixture')
    st.session_state['_review_sources']=[source]
    detail,_=build_intermediate_dataframe([dict(品名='検証用防水',仕様='仕様A',数量=10,単位='㎡',単価=10000,金額=100000,見積元='検証会社',工事種別='防水工事',__source_name='sample.pdf',__page_number=2)])
    rows=review_rows(detail,[source])
    for r in rows:r['集計のみ']=False
    st.session_state['_review_original_rows']=rows
    st.session_state['_review_metadata']={{}}
render_review(st)
'''
            at=AppTest.from_string(source).run()
            self.assertEqual(list(at.exception),[])
            self.assertNotIn('review_approve',[b.key for b in at.button])
            sid=at.session_state['_review_sources'][0]['id']
            at.selectbox(key='review_basis_'+sid).select('税抜')
            at.selectbox(key='review_tax_'+sid).select('10%')
            at.number_input(key='review_net_'+sid).set_value(100000)
            at.run()
            at.checkbox(key='review_source_ok_'+sid).check().run()
            self.assertTrue(any('お客様名' in message.value for message in at.info))
            at.text_input(key='review_customer').set_value('デモ 御中')
            at.text_input(key='review_project').set_value('検証用工事')
            at.run()
            self.assertEqual(list(at.exception),[])
            self.assertTrue(at.button(key='review_approve').disabled)
            at.button(key='review_preview').click().run()
            self.assertEqual(list(at.exception),[])
            self.assertNotIn('_review_approval',at.session_state)
            agree=next(c for c in at.checkbox if c.key.startswith('review_agree_'))
            agree.check().run()
            self.assertFalse(at.button(key='review_approve').disabled)
            at.button(key='review_approve').click().run()
            self.assertEqual(list(at.exception),[])
            self.assertIn('_review_approval',at.session_state)
            labels=[b.label for b in at.get('download_button')]
            self.assertIn('PDFを保存',labels)
            self.assertIn('Numbers用を保存',labels)
            self.assertLess(labels.index('PDFを保存'),labels.index('Numbers用を保存'))
            self.assertNotIn('review_approve',[b.key for b in at.button])
            self.assertEqual(next(b.proto.type for b in at.get('download_button') if b.label=='PDFを保存'),'primary')
            self.assertTrue(any('印刷・お客様への提出' in c.value for c in at.caption))
            self.assertTrue(any('見積書ができました' in s.value for s in at.success))
            target=next(n for n in at.number_input if n.key.startswith('review_target_'))
            target.set_value(10000).run()
            self.assertEqual(list(at.exception),[])
            self.assertFalse(any('見積書ができました' in s.value for s in at.success))
            self.assertTrue(at.button(key='review_approve').disabled)


if __name__=='__main__':unittest.main()
