"""A live estimate opens no durable vault; generated PDF exists only in session."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from estimate_session import estimate_originals, clear_work


SAMPLE = """
from types import SimpleNamespace
import streamlit as st
from estimate_session import render_entry
from estimate_pipeline import build_intermediate_dataframe
from review_ui import preserve_uploads, capture_extraction, render_review
from test_source_subtotal_checks import original_table, item
st.session_state.setdefault('username', 'employee-fixture')
render_entry(st)
if st.button('架空見積の読み取り結果を確認', key='mock_intake'):
    rows = [('架空作業', 160000)]
    data = original_table([*[item(n,a) for n,a in rows]])
    name = '架空.pdf'
    preserve_uploads(st, [SimpleNamespace(name=name, getbuffer=lambda: data)])
    records = [dict(品名=n,仕様='架空',数量=1,単位='式',単価=v,金額=v,見積元='架空業者',
                    工事種別='配管',__source_name=name,__page_number=2) for n,v in rows]
    detail, _ = build_intermediate_dataframe(records)
    capture_extraction(st, detail, {'工事名称':'架空工事'}, records)
render_review(st)
"""


class EstimateSessionTests(unittest.TestCase):
    def test_sessions_and_owners_do_not_share_originals_and_reset_leaves_auth(self):
        alice, bob = {'username': 'alice', 'logged_in': True}, {'username': 'bob'}
        source = estimate_originals(alice).preserve('fake.pdf', b'synthetic')
        self.assertEqual(estimate_originals(alice).read(source['id']), b'synthetic')
        for other in (bob, {'username':'alice'}):
            with self.assertRaises(ValueError):
                estimate_originals(other).read(source['id'])
        alice['username'] = 'other'
        with self.assertRaises(ValueError):
            estimate_originals(alice).read(source['id'])
        alice['_review_result'] = b'old PDF'
        clear_work(alice)
        self.assertTrue(alice['logged_in'])
        self.assertEqual(alice['username'], 'other')
        self.assertNotIn('_review_result', alice)
        self.assertNotIn('_live_estimate_store', alice)

    def test_real_review_generates_pdf_without_opening_durable_storage(self):
        with (tempfile.TemporaryDirectory() as tmp,
              patch.dict('os.environ', {'CYCA_DATA_DIR': tmp}),
              patch('shared_storage.configured_vault', side_effect=AssertionError('durable vault is not allowed'))):
            at = AppTest.from_string(SAMPLE, default_timeout=15).run()
            self.assertNotIn('_review_original_rows', at.session_state)
            at.button(key='mock_intake').click().run()
            sid = at.session_state['_review_sources'][0]['id']
            self.assertEqual(at.session_state['_review_workflow_step'], 2)
            self.assertTrue(at.session_state['_review_extraction_id'])
            self.assertFalse(at.exception)
            next(t for t in at.text_input if t.key.startswith('review_row_evidence_')).set_value('架空原本の品目と仕様を確認').run()
            at.number_input(key='review_net_'+sid).set_value(160000).run()
            self.assertFalse(at.checkbox(key='review_source_ok_'+sid).disabled, [w.value for w in at.warning])
            at.checkbox(key='review_source_ok_'+sid).check().run()
            at.text_input(key='review_customer').set_value('架空のお客様 御中').run()
            at.button(key='review_preview').click().run()
            next(c for c in at.checkbox if c.key.startswith('review_agree_')).check().run()
            at.button(key='review_approve').click().run()
            self.assertFalse(at.exception)
            self.assertTrue(at.session_state['_review_result'].pdf_data.startswith(b'%PDF'))
            downloads = [d.proto.label for d in at.get('download_button')]
            self.assertEqual(downloads, ['PDFを保存'])
            self.assertEqual(list(Path(tmp).iterdir()), [])
            at.button(key='session_estimate_new').click().run()
            at.button(key='session_estimate_cancel_new').click().run()
            self.assertIn('_review_result', at.session_state)
            at.button(key='session_estimate_new').click().run()
            at.button(key='session_estimate_confirm_new').click().run()
            self.assertNotIn('_review_result', at.session_state)
            self.assertEqual(list(Path(tmp).iterdir()), [])
            reloaded = AppTest.from_string(SAMPLE, default_timeout=15).run()
            self.assertNotIn('_review_result', reloaded.session_state)
            self.assertNotIn('_review_original_rows', reloaded.session_state)


    def test_historical_invoice_query_only_after_explicit_separate_route(self):
        app = """
import streamlit as st
from estimate_session import render_invoice_route, render_entry
st.session_state.setdefault('username', 'employee-fixture')
render_invoice_route(st)
render_entry(st)
"""
        with (patch('review_ui.durable_vault') as store,
              patch('saved_quotes.render_historical_invoices') as history):
            at = AppTest.from_string(app, default_timeout=15).run()
            self.assertFalse(at.exception)
            store.assert_not_called()
            self.assertIn('新しい見積を作る', [b.label for b in at.button])
            at.checkbox(key='show_historical_invoices').check().run()
            store.assert_called_once()
            history.assert_called_once()
            self.assertNotIn('新しい見積を作る', [b.label for b in at.button])
            at.checkbox(key='show_historical_invoices').uncheck().run()
            store.assert_called_once()
            self.assertIn('新しい見積を作る', [b.label for b in at.button])
