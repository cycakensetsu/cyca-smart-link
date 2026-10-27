import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import fitz
from streamlit.testing.v1 import AppTest

from estimate_review import OriginalVault, digest, review_rows
from estimate_pipeline import build_intermediate_dataframe
from saved_read_resume import available_reads, resume_read, apply_saved_read


def stored_read(store):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((30, 40), 'SYNTHETIC 2 x 100 = 200')
        content = doc.tobytes()
    source = store.preserve('sample.pdf', content)
    source.update(pages=1, confirmed=False, read_errors=[])
    records = [dict(品名='架空の検証品',仕様='元の仕様',数量=2,単位='台',単価=100,金額=200,
                    見積元='架空会社',__source_name='sample.pdf',__page_number=1)]
    frame, _ = build_intermediate_dataframe(records)
    raw = dict(rows=review_rows(frame,[source]), sources=[source], records=records,
               summary={'summary_sources': []})
    return store.snapshot('extractions',raw), raw


class SavedReadResumeTest(unittest.TestCase):
    def test_roundtrip_keeps_immutable_evidence_and_never_reads_another_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            store = OriginalVault(temp,'owner')
            key, raw = stored_read(store)
            before = {n:r() for n,_,r in store.entries()}
            self.assertEqual([r['id'] for r in available_reads(store)], [key])
            self.assertEqual(resume_read(store,key)['records'],raw['records'])
            self.assertEqual({n:r() for n,_,r in store.entries()},before)
            other = OriginalVault(temp,'other')
            self.assertEqual(available_reads(other),[])
            with self.assertRaises(ValueError):resume_read(other,key)

    def test_changed_original_name_page_provider_error_edited_rows_and_digest_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            store = OriginalVault(temp,'owner')
            key, raw = stored_read(store)
            for field,value in [('id','a'*64),('name','other.pdf'),('pages',2)]:
                current=copy.deepcopy(raw['sources']);current[0][field]=value
                self.assertEqual(available_reads(store,current),[])
                with self.assertRaises(ValueError):resume_read(store,key,current)
            for change in ('provider','edited','record_page','record_source','missing_source','wrong_page_count'):
                changed=copy.deepcopy(raw)
                if change=='provider':changed['sources'][0]['read_errors']=[{'reason':'quota'}]
                if change=='edited':changed['rows'][0]['訂正理由']='訂正済み'
                if change=='record_page':changed['records'][0]['__page_number']=2
                if change=='record_source':changed['records'][0]['__source_name']='other.pdf'
                if change=='missing_source':changed['records']=[]
                if change=='wrong_page_count':changed['sources'][0]['pages']=2
                badkey=store.snapshot('extractions',changed)
                with self.assertRaises(ValueError):resume_read(store,badkey)
            store._put('extractions/'+'b'*64+'.json',b'{}')
            self.assertNotIn('b'*64,[r['id'] for r in available_reads(store)])

    def test_resume_rebuilds_unconfirmed_rows_clears_old_price_customer_and_approval(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ',CYCA_DATA_DIR=temp):
            store=OriginalVault(temp,'owner');key,raw=stored_read(store)
            store.snapshot('approvals',{'old':'keep evidence but never apply'})
            state={'username':'owner','_review_sources':copy.deepcopy(raw['sources']),
                   '_review_approval':{'old':True},'_review_result':'old output',
                   'review_customer':'old customer','review_target_old':500,
                   'review_source_ok_sentinel':True,'review_agree_old':True}
            apply_saved_read(SimpleNamespace(session_state=state),key)
            self.assertNotIn('_review_approval',state)
            self.assertNotIn('_review_result',state)
            self.assertNotIn('review_customer',state)
            self.assertNotIn('review_target_old',state)
            self.assertFalse(state['review_source_ok_sentinel'])
            self.assertFalse(state['review_agree_old'])
            self.assertFalse(state['_review_sources'][0]['confirmed'])
            self.assertEqual(state['_review_original_rows'][0]['原価金額'],200)
            self.assertEqual(state['_review_resumed_from'],key)
            self.assertEqual(resume_read(store,key)['records'],raw['records'])

    def test_page_mismatch_fails_before_changing_current_review(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ',CYCA_DATA_DIR=temp):
            store=OriginalVault(temp,'owner');key,raw=stored_read(store)
            raw['sources'][0]['pages']=2
            badkey=store.snapshot('extractions',raw)
            state={'username':'owner','_review_approval':{'keep':True}}
            before=copy.deepcopy(state)
            with self.assertRaises(ValueError):apply_saved_read(SimpleNamespace(session_state=state),badkey)
            self.assertEqual(state,before)

    def test_ui_recovery_does_not_require_new_upload_or_api_and_resets_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            key,raw=stored_read(OriginalVault(temp,'owner'))
            app=f'''
import os
os.environ['CYCA_DATA_DIR']={temp!r}
import streamlit as st
from review_ui import render_archive
st.session_state['username']='owner'
st.checkbox('current confirmation',key='review_source_ok_current')
st.text_input('current customer',key='review_customer')
render_archive(st)
'''
            at=AppTest.from_string(app).run()
            at.checkbox(key='review_source_ok_current').check().run()
            at.text_input(key='review_customer').input('以前の宛名').run()
            at.button(key='saved_read_resume').click().run()
            self.assertEqual(list(at.exception),[])
            self.assertEqual(at.session_state['_review_resumed_from'],key)
            self.assertFalse(at.session_state['_review_sources'][0]['confirmed'])
            self.assertFalse(at.session_state['review_source_ok_current'])
            self.assertEqual(at.session_state['review_customer'],'')
            self.assertTrue(any('保存した読み取り結果を戻しました' in s.value for s in at.success))


if __name__=='__main__':unittest.main()
