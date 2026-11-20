"""A physical PDF, real subtotal reading, real review flow, no customer/third-party data."""
from tempfile import TemporaryDirectory
from unittest import TestCase

from streamlit.testing.v1 import AppTest


class PhysicalSubtotalWorkflow(TestCase):
    def test_original_printed_subtotal_survives_cover_tied_ocr_omission(self):
        with TemporaryDirectory() as temp:
            app = f"""
import os
os.environ['CYCA_DATA_DIR'] = {temp!r}
import streamlit as st
from estimate_pipeline import build_intermediate_dataframe
from estimate_review import review_rows
from review_ui import vault, render_review
from test_source_subtotal_checks import original_table, item, subtotal
rows = [('資材搬入',12000),('工事甲',27000),('工事乙',8000),('工事丙',29000),('工事丁',47000)]
data = original_table([*[item(n,a) for n,a in rows],subtotal('小計',111000),item('諸経費',27000)])
name = '架空の見積.pdf'
st.session_state['username'] = 'test-reviewer'
source = vault(st).preserve(name, data)
source['pages'] = 2
sid = source['id']
records = [dict(品名=n,仕様='架空',数量=1,単位='式',単価=v,金額=v,見積元='架空業者',
                工事種別='配管',__source_name=name,__page_number=2)
            for n,v in [*rows[1:],('諸経費',27000)]]
if '_review_original_rows' not in st.session_state:
    st.session_state['username'] = 'test-reviewer'
    st.session_state['_review_sources'] = [source]
    detail,_ = build_intermediate_dataframe(records)
    st.session_state['_review_original_rows'] = review_rows(detail,[source])
    st.session_state['_review_metadata'] = {{'工事名称':'帳票確認用'}}
render_review(st)
"""
            at = AppTest.from_string(app).run(timeout=15)
            self.assertFalse(at.exception)
            self.assertTrue(any('12,000円' in x.value for x in at.warning),
                            [x.value for x in at.warning])
            sid = at.session_state['_review_sources'][0]['id']
            self.assertEqual(at.session_state['_review_amount_readings'][sid]['net_total'], 138000)
            self.assertEqual(sum(r['原価金額'] for r in at.session_state['_review_original_rows']), 138000)
            self.assertTrue(at.checkbox(key='review_source_ok_'+sid).disabled)
            self.assertFalse(any('仮見積を作る' in x.label for x in at.checkbox))
