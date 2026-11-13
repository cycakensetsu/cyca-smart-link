import hashlib
import tempfile
import unittest
from pathlib import Path
from document_delivery import DeliveryStore
from test_invoice_documents import fixture
from test_document_delivery import Fake
from workflow_api import create_app
try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient=None

@unittest.skipIf(TestClient is None,'Optional FastAPI test dependency not installed')
class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.transport=Fake(); self.store=DeliveryStore(Path(self.temp.name)/'db')
        self.source=fixture()
        def resolve(owner,key):
            if owner!='owner' or key!=self.source['id']: raise KeyError()
            return self.source
        self.client=TestClient(create_app(store=self.store,token_owners={hashlib.sha256(b'test-token-only').hexdigest():'owner'},
            resolve_estimate=resolve,issuer={'name':'架空会社'},transport=self.transport,sender='sender@example.com',
            resolve_document=lambda owner,key:dict(filename='工事見積書20261112.pdf',pdf=b'%PDF-test')))
        self.headers={'Authorization':'Bearer test-token-only'}
    def test_requires_auth(self):
        self.assertEqual(self.client.get('/deliveries/x').status_code,401)
    def test_invoice_preview_and_approved_final(self):
        request=dict(estimate_approval_id=self.source['id'],invoice_number='1',issue_date='2026-11-12',due_date='2026-12-31')
        result=self.client.post('/invoices/preview',json=request,headers=self.headers)
        self.assertEqual(result.status_code,200)
        self.assertEqual(self.client.post('/invoices/finalize',json=request,headers=self.headers).status_code,409)
        request.update(confirmed=True,fingerprint=result.json()['fingerprint'])
        self.assertTrue(self.client.post('/invoices/finalize',json=request,headers=self.headers).content.startswith(b'%PDF-'))
    def test_prepare_approve_send(self):
        record=self.client.post('/deliveries/prepare',headers=self.headers,json=dict(request_key='one',document_id='doc',recipient='client@example.com',subject='資料',body='添付をご確認ください。')).json()
        path='/deliveries/'+record['id']
        self.assertEqual(self.client.post(path+'/send',headers=self.headers).status_code,409)
        self.assertEqual(self.client.post(path+'/approve',headers=self.headers,json={'confirmed':True,'fingerprint':record['fingerprint']}).status_code,200)
        self.assertEqual(self.client.post(path+'/send',headers=self.headers).json()['state'],'sent')
        self.client.post(path+'/send',headers=self.headers)
        self.assertEqual(self.transport.calls,1)

if __name__=='__main__': unittest.main()
