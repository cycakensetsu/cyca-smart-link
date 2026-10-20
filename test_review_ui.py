"""Exercise the human approval path without calling an AI or using business data."""
from pathlib import Path
import tempfile
import unittest
from streamlit.testing.v1 import AppTest


class ReviewUITest(unittest.TestCase):
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
            self.assertTrue(at.button(key='review_approve').disabled)
            sid=at.session_state['_review_sources'][0]['id']
            at.selectbox(key='review_basis_'+sid).select('税抜')
            at.selectbox(key='review_tax_'+sid).select('10%')
            at.number_input(key='review_net_'+sid).set_value(100000)
            at.checkbox(key='review_source_ok_'+sid).check()
            at.text_input(key='review_customer').set_value('デモ 御中')
            at.text_input(key='review_project').set_value('検証用工事')
            at.run()
            self.assertEqual(list(at.exception),[])
            self.assertTrue(at.button(key='review_approve').disabled)
            agree=next(c for c in at.checkbox if c.key.startswith('review_agree_'))
            agree.check().run()
            self.assertFalse(at.button(key='review_approve').disabled)
            at.button(key='review_approve').click().run()
            self.assertEqual(list(at.exception),[])
            self.assertIn('_review_approval',at.session_state)
            self.assertTrue(any('承認内容と現在' in s.value for s in at.success))
            target=next(n for n in at.number_input if n.key.startswith('review_target_'))
            target.set_value(10000).run()
            self.assertEqual(list(at.exception),[])
            self.assertFalse(any('承認内容と現在' in s.value for s in at.success))
            self.assertTrue(at.button(key='review_approve').disabled)


if __name__=='__main__':unittest.main()
