import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from document_delivery import DeliveryStore
from document_workflow_service import DocumentWorkflowService
from workflow_runtime import actor_gmail_config, configured_service
from test_document_delivery import Fake

class MailboxTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=DeliveryStore(Path(self.temp.name)/'db')
        self.a=Fake();self.b=Fake()
        self.sa=DocumentWorkflowService(self.store,'a',self.a,company_id='company',sender='a@example.com')
        self.sb=DocumentWorkflowService(self.store,'b',self.b,company_id='company',sender='b@example.com')
        self.args=dict(sender='a@example.com',recipient='client@example.com',subject='資料',body='ご確認ください',filename='工事.pdf',pdf=b'%PDF-test',document_id='doc')
    def test_cross_mailbox_send_reconcile_prepare_rejected(self):
        record=self.sa.prepare_email('one',**self.args)
        self.sb.approve_email(record['id'],record['fingerprint'])
        with self.assertRaises(ValueError):self.sb.send_email(record['id'])
        with self.assertRaises(ValueError):self.sb.reconcile_email(record['id'])
        with self.assertRaises(ValueError):self.sb.prepare_email('two',**self.args)
        self.assertEqual((self.a.calls,self.b.calls),(0,0))
        self.sa.send_email(record['id'])
        self.assertEqual(self.a.calls,1)
    def test_company_dedup_cannot_reuse_other_sender(self):
        record=self.sa.prepare_email('one',**self.args)
        self.sa.approve_email(record['id'],record['fingerprint'])
        self.sa.send_email(record['id'])
        with self.assertRaises(ValueError):self.sb.prepare_email('two',**dict(self.args,sender='b@example.com'))
        self.assertEqual(len(self.store.history('company')),1)
    def test_config_no_global_fallback(self):
        config=dict(company_id='company',company_members=['a','b'],sender='shared@example.com',google_oauth_credentials_file='/do-not-read',gmail_enabled=True)
        self.assertEqual(actor_gmail_config(config,'a'),dict(sender='',google_oauth_credentials_file=None,gmail_enabled=False))
        config['gmail_accounts']={'a':{'sender':'a@example.com','google_oauth_credentials_file':'/a','gmail_enabled':True},'b':{'sender':'b@example.com','gmail_enabled':False}}
        self.assertEqual(actor_gmail_config(config,'a')['google_oauth_credentials_file'],'/a')
        self.assertIsNone(actor_gmail_config(config,'b')['google_oauth_credentials_file'])
        self.assertFalse(actor_gmail_config(config,'b')['gmail_enabled'])
        with self.assertRaises(ValueError):actor_gmail_config(config,'other')
        config['member_permissions']={x:['read','prepare','approve','send'] for x in ('a','b')}
        with patch('workflow_runtime.configured_store',return_value=self.store),patch('workflow_runtime.configured_transport',side_effect=[self.a,self.b]) as transport:
            a=configured_service(config,'a');b=configured_service(config,'b')
        self.assertEqual((a.sender,b.sender),('a@example.com','b@example.com'))
        self.assertIs(a.transport,self.a);self.assertIs(b.transport,self.b)
        self.assertEqual(transport.call_args_list[1].args[0]['sender'],'b@example.com')
    def test_transport_reconcile_rejects_wrong_oauth_profile(self):
        from unittest.mock import MagicMock
        from document_delivery import GmailTransport
        api=MagicMock()
        api.users.return_value.getProfile.return_value.execute.return_value={'emailAddress':'a@example.com'}
        with self.assertRaises(ValueError): GmailTransport(api).find('a'*32,'b@example.com')
        api.users.return_value.messages.assert_not_called()
        api.users.return_value.messages.return_value.list.return_value.execute.return_value={'messages':[{'id':'m'}]}
        self.assertEqual(GmailTransport(api).find('a'*32,'a@example.com'),[{'id':'m'}])

    def test_unconfigured_mailbox_does_not_block_read_or_invoice(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest('Optional FastAPI dependency unavailable')
        from workflow_api import create_app
        from test_invoice_documents import fixture
        app=create_app(store=self.store,token_owners={hashlib.sha256(b'a').hexdigest():'a',hashlib.sha256(b'b').hexdigest():'b'},
            resolve_estimate=lambda *args:fixture(),issuer={'name':'架空会社'},actor_companies={'a':'company','b':'company'},
            actor_mailboxes={'a':{'sender':'a@example.com','transport':self.a}})
        client=TestClient(app);headers={'Authorization':'Bearer b'}
        self.assertEqual(client.get('/deliveries',headers=headers).status_code,200)
        request=dict(estimate_approval_id='x',invoice_number='n',issue_date='2026-11-12',due_date='2026-12-31')
        self.assertEqual(client.post('/invoices/preview',headers=headers,json=request).status_code,200)
        self.assertEqual(client.post('/deliveries/prepare',headers=headers,json={}).status_code,503)

    def test_streamlit_secrets_attrdict_accounts(self):
        from streamlit.runtime.secrets import AttrDict
        config = AttrDict({'company_id':'company','company_members':['a','b'],
            'gmail_accounts': {'a': {'sender':'a@example.com','gmail_enabled':False},
                               'b': {'sender':'b@example.com','gmail_enabled':False}}})
        self.assertEqual(actor_gmail_config(config,'a')['sender'],'a@example.com')
        self.assertEqual(actor_gmail_config(config,'b')['sender'],'b@example.com')

    def test_legacy_single_user(self):
        self.assertEqual(actor_gmail_config({'sender':'single@example.com'},'owner')['sender'],'single@example.com')
    def test_api_uses_authenticated_mapping_not_request_sender(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest('Optional FastAPI dependency unavailable')
        from workflow_api import create_app
        tokens={hashlib.sha256(x.encode()).hexdigest():x for x in ('a','b')}
        app=create_app(store=self.store,token_owners=tokens,resolve_estimate=lambda *args:None,issuer={},
                       resolve_document=lambda *args:dict(filename='工事.pdf',pdf=b'%PDF-test'),
                       actor_companies={'a':'company','b':'company'},
                       actor_mailboxes={'a':{'sender':'a@example.com','transport':self.a},'b':{'sender':'b@example.com','transport':self.b}})
        c=TestClient(app);ha={'Authorization':'Bearer a'};hb={'Authorization':'Bearer b'}
        r=c.post('/deliveries/prepare',headers=ha,json=dict(request_key='x',document_id='d',recipient='client@example.com',subject='資料',body='ご確認ください',sender='b@example.com')).json()
        self.assertEqual(r['payload']['sender'],'a@example.com')
        url='/deliveries/'+r['id']
        self.assertEqual(c.post(url+'/approve',headers=hb,json=dict(confirmed=True,fingerprint=r['fingerprint'])).status_code,200)
        self.assertEqual(c.post(url+'/send',headers=hb).status_code,409)
        self.assertEqual(c.post(url+'/reconcile',headers=hb).status_code,409)
        self.assertEqual(c.post(url+'/send',headers=ha).status_code,200)
        self.assertEqual(self.b.calls,0)

if __name__=='__main__':unittest.main()
