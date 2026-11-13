import unittest
from streamlit.testing.v1 import AppTest

SCRIPT='''
import streamlit as st
import document_delivery_ui as ui
class Store:
    def history(self,*args): return []
class Workflow:
    sender='owner@example.com'
    scope='company'
    store=Store()
    def check_gmail_connection(self):
        st.session_state['connection_checks']=st.session_state.get('connection_checks',0)+1
        if st.session_state.get('connection_fail'):raise ValueError('SECRET-DO-NOT-EXPOSE')
        return self.sender
    def prepare_email(self,*args,**kwargs):raise AssertionError('must not prepare automatically')
    def send_email(self,*args,**kwargs):raise AssertionError('must not send automatically')
ui.configured_service=lambda *args: Workflow()
ui.render_delivery(st,{},owner='owner',document_id='doc',filename='工事.pdf',pdf=b'%PDF-test',customer='架空会社')
'''
class InstructionUITests(unittest.TestCase):
    def test_apply_before_widget_and_preserve_body_subject(self):
        app=AppTest.from_string(SCRIPT).run()
        [w for w in app.text_input if w.label=='件名'][0].set_value('変更した件名')
        [w for w in app.text_area if w.label=='本文'][0].set_value('変更した本文')
        [w for w in app.text_area if '文章で入力' in w.label][0].set_value('この書類を client@example.com に送って')
        [w for w in app.button if '宛先欄へ反映' in w.label][0].click();app.run()
        self.assertEqual(list(app.exception),[])
        self.assertEqual([w for w in app.text_input if w.label=='宛先メールアドレス'][0].value,'client@example.com')
        self.assertEqual([w for w in app.text_input if w.label=='件名'][0].value,'変更した件名')
        self.assertEqual([w for w in app.text_area if w.label=='本文'][0].value,'変更した本文')
        [w for w in app.text_area if '文章で入力' in w.label][0].set_value('担当者様に送って')
        [w for w in app.button if '宛先欄へ反映' in w.label][0].click();app.run()
        self.assertEqual([w for w in app.text_input if w.label=='宛先メールアドレス'][0].value,'client@example.com')
        self.assertTrue(app.warning)
    def test_connection_is_explicit_and_error_sanitized(self):
        app=AppTest.from_string(SCRIPT).run()
        self.assertNotIn('connection_checks',app.session_state)
        [w for w in app.button if 'Gmail接続を確認' in w.label][0].click();app.run()
        self.assertEqual(app.session_state['connection_checks'],1)
        self.assertIn('owner@example.com',app.success[0].value)
        app.session_state['connection_fail']=True
        [w for w in app.button if 'Gmail接続を確認' in w.label][0].click();app.run()
        self.assertNotIn('SECRET',app.error[0].value)
        self.assertEqual(list(app.exception),[])
    def test_transport_profile_only(self):
        from unittest.mock import MagicMock
        from document_delivery import GmailTransport
        api=MagicMock();api.users.return_value.getProfile.return_value.execute.return_value={'emailAddress':'owner@example.com'}
        self.assertEqual(GmailTransport(api).check_connection('owner@example.com'),'owner@example.com')
        api.users.return_value.messages.assert_not_called()
        api.users.return_value.getProfile.return_value.execute.return_value={'emailAddress':'other@example.com'}
        with self.assertRaises(ValueError): GmailTransport(api).check_connection('owner@example.com')

if __name__=='__main__':unittest.main()
