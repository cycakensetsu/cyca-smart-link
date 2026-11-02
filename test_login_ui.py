import unittest
from streamlit.testing.v1 import AppTest
import app_auth


APP = '''
import streamlit as st
from app_auth import session_valid
from login_ui import clear_login_password, render_login
users = [{'username':'synthetic-user', 'password':'synthetic-test-password'}]
clear_login_password(st)
st.session_state.setdefault('logged_in', False)
if not session_valid(st.session_state, users):
    render_login(st, users)
else:
    st.success('ログイン済み：' + st.session_state.username)
'''


class LoginUiTest(unittest.TestCase):
    def setUp(self):
        app_auth._attempts.clear()

    def test_existing_credential_autocomplete_and_batched_submit(self):
        at = AppTest.from_string(APP).run()
        self.assertEqual(list(at.exception), [])
        self.assertEqual(at.text_input[0].proto.autocomplete, 'username')
        self.assertEqual(at.text_input[1].proto.autocomplete, 'current-password')
        self.assertEqual(at.text_input[1].proto.type, 1)
        at.text_input[0].input('synthetic-user')
        at.text_input[1].input('synthetic-test-password')
        self.assertFalse(app_auth._attempts)
        self.assertFalse(at.session_state['logged_in'])
        at.button[0].click().run()
        self.assertEqual(list(at.exception), [])
        self.assertTrue(at.session_state['logged_in'])
        self.assertEqual(at.session_state['username'], 'synthetic-user')
        with self.assertRaises(KeyError): at.session_state['_login_password']
        self.assertFalse(app_auth._attempts)

    def test_missing_autofill_does_not_consume_attempt_or_claim_bad_password(self):
        at = AppTest.from_string(APP).run()
        at.text_input[0].input('synthetic-user')
        at.button[0].click().run()
        self.assertEqual(list(at.exception), [])
        self.assertTrue(at.warning)
        self.assertFalse(at.error)
        self.assertFalse(app_auth._attempts)
        self.assertFalse(at.session_state['logged_in'])

    def test_wrong_password_fails_closed_and_editing_alone_does_not_retry(self):
        at = AppTest.from_string(APP).run()
        at.text_input[0].input('synthetic-user')
        at.text_input[1].input('incorrect-synthetic-password')
        at.button[0].click().run()
        self.assertTrue(at.error)
        self.assertFalse(at.session_state['logged_in'])
        before = dict(app_auth._attempts)
        at.text_input[1].input('synthetic-test-password')
        self.assertEqual(app_auth._attempts, before)
        self.assertFalse(at.session_state['logged_in'])
        at.button[0].click().run()
        self.assertTrue(at.session_state['logged_in'])
        self.assertFalse(app_auth._attempts)


if __name__ == '__main__':
    unittest.main()
