import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from test_conversation_assistant import change, proposal


def app_source(temp):
    return f'''
import os
os.environ['CYCA_DATA_DIR']={temp!r}
import streamlit as st
from review_ui import vault, render_review
from estimate_pipeline import build_intermediate_dataframe
from estimate_review import review_rows
if '_review_original_rows' not in st.session_state:
    st.session_state['username']='voice-test-actor'
    source=vault(st).preserve('sample.pdf',b'synthetic-voice-test')
    source['pages']=1
    st.session_state['_review_sources']=[source]
    detail,_=build_intermediate_dataframe([dict(品名='架空防水',仕様='仕様A',数量=10,単位='㎡',単価=10000,金額=100000,見積元='架空会社',工事種別='防水工事',__source_name='sample.pdf',__page_number=1)])
    st.session_state['_review_original_rows']=review_rows(detail,[source])
    for r in st.session_state['_review_original_rows']:r['集計のみ']=False
    st.session_state['_review_metadata']={{}}
render_review(st, api_key='test-key-not-sent')
'''


class ConversationUITest(unittest.TestCase):
    def checked_app(self, temp):
        at = AppTest.from_string(app_source(temp)).run()
        sid = at.session_state['_review_sources'][0]['id']
        at.selectbox(key='review_basis_' + sid).select('税抜')
        at.selectbox(key='review_tax_' + sid).select('10%')
        at.number_input(key='review_net_' + sid).set_value(100000)
        at.checkbox(key='review_source_ok_' + sid).check()
        at.text_input(key='review_customer').set_value('架空会社 御中')
        at.text_input(key='review_project').set_value('架空工事')
        at.run()
        self.assertEqual(list(at.exception), [])
        return at

    def ask(self, at):
        words = '全体に10万円上乗せ'
        raw = proposal([change(kind='target', scope='全体', key='全体', mode='上乗せ額', value='100000', evidence=words)])
        at.text_area(key='review_instruction').set_value(words).run()
        with patch('conversation_assistant._generate', return_value=raw):
            at.button(key='review_consult').click().run()
        self.assertEqual(list(at.exception), [])

    def test_must_check_original_then_consult_then_explicit_apply_and_approve(self):
        with tempfile.TemporaryDirectory() as temp:
            initial = AppTest.from_string(app_source(temp)).run()
            initial.text_area(key='review_instruction').set_value('全体に10万円上乗せ').run()
            self.assertTrue(initial.button(key='review_consult').disabled)
            at = self.checked_app(temp)
            self.ask(at)
            target = next(n for n in at.number_input if n.key.startswith('review_target_'))
            self.assertEqual(target.value, 0.0)
            self.assertNotIn('_review_approval', at.session_state)
            at.button(key='review_apply_conversation').click().run()
            self.assertEqual(list(at.exception), [])
            target = next(n for n in at.number_input if n.key.startswith('review_target_'))
            self.assertEqual(target.value, 100000.0)
            self.assertEqual(at.session_state['_review_original_rows'][0]['原価金額'], 100000)
            self.assertTrue(at.button(key='review_approve').disabled)
            self.assertEqual(len(list(Path(temp).rglob('extractions/*.json'))), 1)
            at.button(key='review_preview').click().run()
            next(c for c in at.checkbox if c.key.startswith('review_agree_')).check().run()
            at.button(key='review_approve').click().run()
            self.assertEqual(list(at.exception), [])
            self.assertEqual(at.session_state['_review_approval']['payload']['合計']['税抜売価'], 200000)
            self.assertIn('相談反映記録', at.session_state['_review_approval']['payload']['表題'])
            self.ask(at)
            at.button(key='review_apply_conversation').click().run()
            self.assertEqual(list(at.exception), [])
            self.assertNotIn('_review_approval', at.session_state)
            self.assertNotIn('_review_result', at.session_state)
            self.assertTrue(at.button(key='review_approve').disabled)

    def test_manual_change_disables_stale_plan_and_reset_keeps_current_estimate(self):
        with tempfile.TemporaryDirectory() as temp:
            at = self.checked_app(temp)
            self.ask(at)
            at.text_input(key='review_project').set_value('変更した工事').run()
            self.assertTrue(at.button(key='review_apply_conversation').disabled)
            at.button(key='review_new_conversation').click().run()
            self.assertEqual(list(at.exception), [])
            self.assertEqual(at.text_area(key='review_instruction').value, '')
            self.assertEqual(at.text_input(key='review_project').value, '変更した工事')
            self.assertNotIn('_review_proposal', at.session_state)

    def test_extraction_replacement_forgets_previous_client_conversation(self):
        from types import SimpleNamespace
        from review_ui import capture_extraction
        from estimate_pipeline import build_intermediate_dataframe
        from estimate_review import OriginalVault
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ', CYCA_DATA_DIR=temp):
            state = {'username': 'actor', '_review_chat': [{'role': 'user', 'content': 'previous client'}],
                     '_review_proposal': {'private': True}, '_review_sources': [OriginalVault(temp, 'actor').preserve('new.pdf', b'new')]}
            detail, _ = build_intermediate_dataframe([dict(品名='新しい明細',数量=1,単位='式',単価=100,金額=100,見積元='会社',__source_name='new.pdf',__page_number=1)])
            capture_extraction(SimpleNamespace(session_state=state), detail, {}, [])
            self.assertNotIn('_review_chat', state)
            self.assertNotIn('_review_proposal', state)


if __name__ == '__main__': unittest.main()
