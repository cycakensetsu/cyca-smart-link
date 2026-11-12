import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from document_delivery import DeliveryStore

class Fake:
    def __init__(self, fail=False): self.calls=0; self.fail=fail
    def send(self,payload,pdf,key):
        self.calls+=1
        if self.fail: raise TimeoutError('secret must not be logged')
        return {'id':'gmail-1','threadId':'thread-1'}
    def find(self,key,sender): return [{'id':'gmail-1','threadId':'thread-1'}]

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=DeliveryStore(Path(self.tmp.name)/'outbox.db')
        self.args=dict(sender='sender@example.com',recipient='client@example.com',subject='見積書',body='添付をご確認ください。',filename='雨樋交換見積書20261112.pdf',pdf=b'%PDF-test',document_id='doc-1')
        self.key=self.store.prepare('owner','request-1',**self.args)
    def approve(self): self.store.approve('owner',self.key,self.store.get('owner',self.key)['fingerprint'],'owner')
    def test_dedup_prepare(self):
        self.assertEqual(self.key,self.store.prepare('owner','request-1',**self.args))
        with self.assertRaises(ValueError): self.store.prepare('owner','request-1',**dict(self.args,subject='changed'))
    def test_new_request_cannot_bypass_unknown(self):
        self.approve()
        with self.assertRaises(RuntimeError): self.store.send('owner',self.key,Fake(True))
        self.assertEqual(self.store.prepare('owner','new-uuid',**self.args),self.key)
        for change in ({'body':'違う本文'}, {'filename':'別名.pdf'}, {'document_id':'regenerated-id'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.store.prepare('owner','another-uuid',**dict(self.args,**change))
    def test_sent_blocks_fresh_request(self):
        self.approve();self.store.send('owner',self.key,Fake())
        self.assertEqual(self.store.prepare('owner','new-uuid',**self.args),self.key)
        with self.assertRaises(ValueError): self.store.prepare('owner','new-uuid-2',**dict(self.args,body='再送します'))
    def test_case_changed_recipient_still_blocks(self):
        with self.assertRaises(ValueError): self.store.prepare('owner','new-uuid',**dict(self.args,recipient='CLIENT@example.com'))

    def test_only_unsent_can_be_cancelled_for_revision(self):
        self.store.cancel_unsent('owner',self.key)
        revised=self.store.prepare('owner','revised',**dict(self.args,body='訂正本文'))
        self.assertNotEqual(revised,self.key)
        self.store.approve('owner',revised,self.store.get('owner',revised)['fingerprint'],'owner')
        with self.assertRaises(RuntimeError): self.store.send('owner',revised,Fake(True))
        with self.assertRaises(ValueError): self.store.cancel_unsent('owner',revised)

    def test_owner_isolation(self):
        with self.assertRaises(KeyError): self.store.get('other',self.key)
    def test_requires_approval(self):
        fake=Fake()
        with self.assertRaises(ValueError): self.store.send('owner',self.key,fake)
        self.assertEqual(fake.calls,0)
    def test_single_send(self):
        self.approve(); fake=Fake()
        self.store.send('owner',self.key,fake); self.store.send('owner',self.key,fake)
        self.assertEqual(fake.calls,1)
    def test_unknown_no_retry(self):
        self.approve(); fake=Fake(True)
        with self.assertRaises(RuntimeError): self.store.send('owner',self.key,fake)
        with self.assertRaises(ValueError): self.store.send('owner',self.key,fake)
        self.assertEqual(fake.calls,1)
        self.assertNotIn('secret',self.store.get('owner',self.key)['error'])
        self.assertEqual(self.store.reconcile('owner',self.key,fake)['state'],'sent')
    def test_concurrent(self):
        self.approve(); fake=Fake()
        def send():
            try: self.store.send('owner',self.key,fake)
            except ValueError: pass
        with ThreadPoolExecutor(2) as pool: list(pool.map(lambda _:send(),range(2)))
        self.assertEqual(fake.calls,1)
    def test_changed_approval(self):
        with self.assertRaises(ValueError): self.store.approve('owner',self.key,'changed','owner')
    def test_bad_address(self):
        with self.assertRaises(ValueError): self.store.prepare('owner','request-2',**dict(self.args,recipient='x@example.com\nBCC: victim@example.com'))
    def test_reopen(self):
        self.assertEqual(DeliveryStore(self.store.path).get('owner',self.key)['state'],'review')


class GmailMimeTests(unittest.TestCase):
    def test_actual_transport_builds_one_pdf_and_no_retry(self):
        import base64
        from email import policy
        from email.parser import BytesParser
        from document_delivery import GmailTransport
        class Result:
            def __init__(self,value):self.value=value
            def execute(self,**kwargs):
                self.kwargs=kwargs
                return self.value
        class Service:
            def users(self):return self
            def messages(self):return self
            def getProfile(self,**kwargs):return Result({'emailAddress':'sender@example.com'})
            def send(self,**kwargs):
                self.body=kwargs['body'];self.result=Result({'id':'mail','threadId':'thread'})
                return self.result
        service=Service()
        result=GmailTransport(service).send(dict(sender='sender@example.com',recipient='client@example.com',subject='工事見積書',body='添付をご確認ください。',filename='工事見積書20261112.pdf',thread_id='thread',reply_message_id='<original@example.com>'),b'%PDF-test','a'*32)
        mime=BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(service.body['raw']))
        self.assertEqual(result['id'],'mail')
        self.assertEqual(len(list(mime.iter_attachments())),1)
        self.assertEqual(list(mime.iter_attachments())[0].get_content_type(),'application/pdf')
        self.assertEqual(service.result.kwargs,{'num_retries':0})
        self.assertEqual(mime['In-Reply-To'],'<original@example.com>')

if __name__=='__main__': unittest.main()
