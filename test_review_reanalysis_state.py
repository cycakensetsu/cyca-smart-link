"""Re-reading identical originals must reset visible and server confirmations."""
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from streamlit.testing.v1 import AppTest


def app_source(temp):
    return f'''
import os
from types import SimpleNamespace
from unittest.mock import patch
import streamlit as st
os.environ['CYCA_DATA_DIR']={temp!r}
from review_ui import preserve_uploads,capture_extraction,vault
from review_input_ui import render_inputs
from estimate_pipeline import build_intermediate_dataframe
uploads=[SimpleNamespace(name='quote'+str(i)+'.pdf',getbuffer=lambda i=i:('synthetic-original-'+str(i)).encode()) for i in range(5)]
def read_again():
    with patch('pdf_worker.page_count',return_value=1):preserve_uploads(st,uploads)
    raw=[dict(品名='原本工事'+str(i),仕様='原本仕様',数量=1,単位='式',単価=100000,金額=100000,
         見積元='架空業者'+str(i),工事種別='防水工事',__source_name='quote'+str(i)+'.pdf',__page_number=1) for i in range(4)]
    detail,_=build_intermediate_dataframe(raw)
    capture_extraction(st,detail,{{}},raw)
    readings={{}}
    for i,source in enumerate(st.session_state['_review_sources']):
        readings[source['id']]={{'net_total':100000 if i<4 else None,'tax':10000 if i<4 else None,
            'gross_total':110000 if i<4 else None,'conflicts':[],'page':1}}
    st.session_state['_review_amount_readings']=readings
st.session_state['username']='synthetic-reanalysis-actor'
if '_review_original_rows' not in st.session_state:read_again()
if st.button('同じ5件を読み直す',key='qa_read_again'):read_again()
with patch('source_review_ui.preview'):
    _,sources=render_inputs(st,st.session_state['_review_original_rows'],st.session_state['_review_sources'],vault(st))
st.session_state['probe_confirmed']=[bool(s.get('confirmed')) for s in sources]
'''


class ReanalysisStateTest(unittest.TestCase):
    def test_new_extraction_revokes_source_and_output_approvals_without_changing_originals(self):
        from review_ui import capture_extraction
        from estimate_review import OriginalVault
        from estimate_pipeline import build_intermediate_dataframe
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ',CYCA_DATA_DIR=temp):
            store=OriginalVault(temp,'reset-test')
            source=store.preserve('synthetic.pdf',b'original-unmodified')
            source.update(pages=1,confirmed=True,reference_only=True,reference_confirmed=True)
            sid=source['id']
            state={'username':'reset-test','_review_sources':[source],
                   '_review_approval':{'old':True},'_review_result':'old output',
                   '_review_chat':[{'role':'user','content':'old request'}],
                   '_review_source_signature_'+sid:'old signature',
                   '_complete_document_cache':{'safe':'unchanged cache'}}
            keys=['review_source_ok_'+sid,'review_reference_only_'+sid,
                  'review_agree_previous','review_resolve_totals_'+sid]
            state.update({key:True for key in keys})
            frame,_=build_intermediate_dataframe([dict(品名='原本工事',数量=1,単位='式',単価=100,金額=100,
                見積元='架空会社',__source_name='synthetic.pdf',__page_number=1)])
            capture_extraction(SimpleNamespace(session_state=state),frame,{},[])
            self.assertTrue(all(state[key] is False for key in keys))
            self.assertFalse(source['confirmed'])
            self.assertFalse(source['reference_only'])
            self.assertFalse(source['reference_confirmed'])
            for key in ('_review_approval','_review_result','_review_chat','_review_source_signature_'+sid):
                self.assertNotIn(key,state)
            self.assertEqual(state['_complete_document_cache'],{'safe':'unchanged cache'})
            self.assertEqual(store.read(sid),b'original-unmodified')

    def test_same_originals_reset_wire_checkmarks_then_accept_new_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            at=AppTest.from_string(app_source(temp)).run()
            self.assertEqual(list(at.exception),[])
            sources=at.session_state['_review_sources']
            keys=['review_source_ok_'+s['id'] for s in sources[:4]]+['review_reference_only_'+sources[4]['id']]
            originals=at.session_state['_review_original_rows']
            for index,key in enumerate(keys,1):
                at.checkbox(key=key).check().run()
                self.assertEqual(list(at.exception),[])
                self.assertEqual(sum(at.session_state['probe_confirmed']),index)
            at.button(key='qa_read_again').click().run()
            self.assertEqual(list(at.exception),[])
            self.assertEqual(at.session_state['probe_confirmed'],[False]*5)
            self.assertEqual(at.session_state['_review_original_rows'],originals)
            for key in keys:
                checkbox=at.checkbox(key=key)
                self.assertFalse(checkbox.value)
                # The browser needs an explicit update when its old widget ID
                # survives. A missing server key alone leaves it checked there.
                self.assertTrue(checkbox.proto.set_value,key)
                self.assertFalse(checkbox.proto.value,key)
            self.assertTrue(any('あと5件' in message.value for message in at.info))
            at.run()
            self.assertEqual(at.session_state['probe_confirmed'],[False]*5)
            for index,key in enumerate(keys,1):
                at.checkbox(key=key).check().run()
                self.assertEqual(list(at.exception),[])
                self.assertEqual(sum(at.session_state['probe_confirmed']),index)
            self.assertFalse(any('あと' in message.value and '件を確認' in message.value for message in at.info))
            self.assertNotIn('_review_approval',at.session_state)


if __name__=='__main__':unittest.main()
