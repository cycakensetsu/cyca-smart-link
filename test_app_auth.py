import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app_auth


class AuthTest(unittest.TestCase):
    def setUp(self):
        app_auth._attempts.clear()

    def test_no_repository_default_login(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(app_auth.load_users(Path(temp)/'missing.json', {}, {}), [])

    def test_private_configuration_and_hash_override(self):
        encoded = app_auth.password_hash('synthetic-test-only')
        users = app_auth.load_users('/missing', {'users': [{'username': 'tester',
                  'password_hash': encoded, 'password': 'ignored-test-only'}]}, {})
        self.assertTrue(app_auth.check_login('tester', 'synthetic-test-only', users))
        self.assertFalse(app_auth.check_login('tester', 'ignored-test-only', users))
        self.assertFalse(app_auth.check_login('other', 'synthetic-test-only', users))

    def test_explicit_legacy_private_pair_is_supported(self):
        users = app_auth.load_users('/missing', {'STREAMLIT_APP_USER': 'tester',
                        'STREAMLIT_APP_PASSWORD': 'synthetic-test-only'}, {})
        self.assertTrue(app_auth.check_login('tester', 'synthetic-test-only', users))
        self.assertEqual(app_auth.load_users('/missing', {}, {'STREAMLIT_APP_USER': 'tester'}), [])

    def test_invalid_hash_and_corrupt_config_fail_closed(self):
        for encoded in ('', 'plain', 'pbkdf2_sha256$999999999$00$bad', 'other$600000$00$bad'):
            self.assertFalse(app_auth.verify_password('synthetic-test-only', encoded))
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'users.json';path.write_text('{')
            self.assertEqual(app_auth.load_users(path, {}, {}), [])

    def test_attempt_limit_is_shared_and_expires(self):
        users=[{'username': 'tester', 'password': 'synthetic-test-only'}]
        for i in range(5):
            self.assertFalse(app_auth.check_login('tester','wrong',users,now=100+i))
        self.assertFalse(app_auth.check_login('tester','synthetic-test-only',users,now=106))
        self.assertTrue(app_auth.check_login('tester','synthetic-test-only',users,now=161))

    def test_old_sessions_and_changed_credentials_are_revoked(self):
        users=[{'username':'tester','password':'synthetic-test-only'}]
        state={'logged_in':True,'username':'tester'}
        self.assertFalse(app_auth.session_valid(state,users,now=100))
        state.update(_auth_signature=app_auth.configuration_signature(users),_auth_started=90)
        self.assertTrue(app_auth.session_valid(state,users,now=100))
        self.assertFalse(app_auth.session_valid(state,[],now=100))
        self.assertFalse(app_auth.session_valid(state,[{'username':'tester','password':'changed-test-only'}],now=100))
        self.assertFalse(app_auth.session_valid(state,users,now=90+12*60*60))


if __name__ == '__main__':
    unittest.main()
