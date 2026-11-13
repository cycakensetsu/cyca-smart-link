"""Synthetic credentials only. Google SDK construction is mocked: no network."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow_runtime import configured_transport, actor_gmail_config, validate_authorized_user, GMAIL_REQUIRED_SCOPES


def info():
    return dict(type='authorized_user',client_id='test-client',client_secret='SYNTHETIC-SECRET',
                refresh_token='SYNTHETIC-REFRESH',token_uri='https://oauth2.googleapis.com/token',
                scopes=sorted(GMAIL_REQUIRED_SCOPES))


class AuthorizedUserTests(unittest.TestCase):
    def mock_sdk(self):
        credentials=patch('google.oauth2.credentials.Credentials.from_authorized_user_info')
        build=patch('googleapiclient.discovery.build')
        self.addCleanup(credentials.stop);self.addCleanup(build.stop)
        return credentials.start(),build.start()

    def test_attrdict_goes_through_info_constructor(self):
        from streamlit.runtime.secrets import AttrDict
        make,build=self.mock_sdk()
        config=AttrDict(dict(company_id='company',company_members=['a','b'],gmail_accounts={
            'a':dict(sender='a@example.com',gmail_enabled=True,authorized_user=info())}))
        account=actor_gmail_config(config,'a')
        result=configured_transport(account)
        self.assertIs(result.service,build.return_value)
        self.assertEqual(make.call_args.args[0],info())
        self.assertFalse(actor_gmail_config(config,'b')['gmail_enabled'])
        self.assertNotIn('authorized_user',actor_gmail_config(config,'b'))

    def test_file_compatibility_and_permissions(self):
        make,build=self.mock_sdk()
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'synthetic.json'
            legacy=info();legacy.pop('type') # Credentials.to_json() omits this optional field.
            path.write_text(json.dumps(legacy));path.chmod(0o600)
            self.assertIsNotNone(configured_transport(dict(gmail_enabled=True,google_oauth_credentials_file=str(path))))
            self.assertEqual(make.call_args.args[0],legacy)
            path.chmod(0o644)
            with self.assertRaises(ValueError):configured_transport(dict(gmail_enabled=True,google_oauth_credentials_file=str(path)))
            self.assertEqual(build.call_count,1)

    def test_both_sources_rejected_even_disabled(self):
        with self.assertRaises(ValueError):configured_transport(dict(gmail_enabled=False,google_oauth_credentials_file='/not-read',authorized_user=info()))

    def test_missing_fields_wrong_endpoints_and_scopes(self):
        bad=[]
        for key in ('client_id','client_secret','refresh_token','scopes'):
            value=info();value.pop(key);bad.append(value)
        for key,value in [('type','service_account'),('token_uri','https://attacker.invalid/token'),
                          ('universe_domain','attacker.invalid'),('scopes',123),('refresh_token',''),
                          ('scopes',['https://www.googleapis.com/auth/gmail.send'])]:
            value_info=info();value_info[key]=value;bad.append(value_info)
        for value in bad:
            with self.subTest(value_key_count=len(value)):
                with self.assertRaises(ValueError) as result:validate_authorized_user(value)
                self.assertNotIn('SYNTHETIC',str(result.exception))
        value=info();value['scopes']=' '.join(value['scopes'])
        self.assertEqual(set(validate_authorized_user(value)['scopes']),GMAIL_REQUIRED_SCOPES)

    def test_sdk_error_is_sanitized(self):
        make,build=self.mock_sdk();make.side_effect=RuntimeError('SYNTHETIC-SECRET')
        with self.assertRaises(ValueError) as result:configured_transport(dict(gmail_enabled=True,authorized_user=info()))
        self.assertNotIn('SYNTHETIC',str(result.exception))
        self.assertTrue(result.exception.__suppress_context__)
        build.assert_not_called()

    def test_disabled_does_not_load_credentials(self):
        make,build=self.mock_sdk()
        self.assertIsNone(configured_transport(dict(gmail_enabled=False,authorized_user=info())))
        make.assert_not_called();build.assert_not_called()

if __name__=='__main__':unittest.main()
