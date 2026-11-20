"""Exercise real employee entry points with synthetic accounts; no provider calls."""
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from streamlit.testing.v1 import AppTest


HERE = Path(__file__).parent


class BeginnerIntakeTests(unittest.TestCase):
    def test_both_employee_entries_default_to_guided_and_keep_file_widget(self):
        for entry in ('extract_data.py','extract_data_cloud.py'):
            # AppTest cannot mount JavaScript custom components.
            with (self.subTest(entry=entry),
                  patch('streamlit._bidi_component', return_value=SimpleNamespace(event=None))):
                at = AppTest.from_file(str(HERE / entry))
                at.secrets['GOOGLE_API_KEY'] = 'synthetic-never-used'
                at.secrets['users'] = [{'username':'verification','password':'synthetic-login-only'}]
                at.run(timeout=30)
                self.assertFalse(at.get('file_uploader'))
                at.text_input[0].set_value('verification')
                at.text_input[1].set_value('synthetic-login-only')
                at.button[0].click().run(timeout=30)
                self.assertFalse(list(at.exception))
                self.assertTrue(at.session_state['_review_beginner_guided'])
                widget = at.get('file_uploader')[0]
                self.assertEqual(widget.proto.label,'見積書のPDF・写真を選ぶ')
                self.assertTrue(widget.proto.multiple_files)
                self.assertTrue(any('まず、業者から届いた見積書を選んでください' in m.value for m in at.markdown))
                self.assertFalse(any(e.label == '詳しい利益設定・従来の出力形式' for e in at.sidebar.expander))
                self.assertIn('新しい見積を作る', [b.label for b in at.button])
                self.assertNotIn('前回の続きから再開', [b.label for b in at.button])
                self.assertTrue(at.session_state['_live_estimate_session'])
                # AppTest on newer Streamlit retains the one-run login widget even
                # after the app has deliberately erased its password. Reopen the
                # app with only the real validated session fields, no password.
                active = AppTest.from_file(str(HERE / entry))
                active.secrets['GOOGLE_API_KEY'] = 'synthetic-never-used'
                active.secrets['users'] = [{'username':'verification','password':'synthetic-login-only'}]
                for key in ('logged_in', 'username', '_auth_signature', '_auth_started', '_upload_epoch'):
                    active.session_state[key] = at.session_state[key]
                at = active.run(timeout=30)
                at.checkbox(key='expert_review_settings').check().run(timeout=30)
                self.assertFalse(at.session_state['_review_beginner_guided'])
                self.assertEqual(at.get('file_uploader')[0].proto.id,widget.proto.id)
                self.assertTrue(at.session_state['logged_in'])
                at.checkbox(key='expert_review_settings').uncheck().run(timeout=30)
                self.assertTrue(at.session_state['_review_beginner_guided'])
                self.assertEqual(at.get('file_uploader')[0].proto.id,widget.proto.id)

    def test_current_action_after_read_and_same_upload_identity(self):
        at = AppTest.from_string('''
import streamlit as st
from beginner_intake_ui import render_intake, render_read_button, render_resume_action
read = st.checkbox("架空読み取り済み", key="synthetic_read")
if read:
    st.session_state["_review_original_rows"] = [{"合計": 100}]
    st.session_state["_review_workflow_step"] = 3
render_intake(st,key="fixed-upload",guided=True,max_upload_size=20)
render_read_button(st,"見積書を読み取る",guided=True,active=read)
render_resume_action(st)
''').run()
        before=at.get('file_uploader')[0].proto.id
        self.assertEqual(at.button(key='beginner_estimate_read').proto.type,'primary')
        at.checkbox(key='synthetic_read').check().run()
        self.assertFalse(list(at.exception))
        self.assertEqual(at.get('file_uploader')[0].proto.id,before)
        self.assertTrue(any('href="#cyca-step-3"' in h.proto.body and '上乗せする金額を決める' in h.proto.body for h in at.get('html')))
        self.assertTrue(any(e.label == '読み込み中の見積書を見る・選び直す' for e in at.expander))
        self.assertTrue(any(e.label == '同じ見積書をもう一度読み取る' for e in at.expander))
        self.assertEqual(at.session_state['_review_original_rows'],[{"合計": 100}])


if __name__ == '__main__':
    unittest.main()
