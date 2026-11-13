import copy
import unittest
from datetime import datetime,timezone
from unittest.mock import MagicMock
from document_delivery import GmailTransport,delivery_fingerprint

class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.api=MagicMock();self.transport=GmailTransport(self.api)
        self.api.users.return_value.getProfile.return_value.execute.return_value={'emailAddress':'a@example.com'}
        self.messages=self.api.users.return_value.messages.return_value
        self.payload={'sender':'a@example.com','recipient':'b@example.com','subject':'テスト資料','body':'確認ください','filename':'工事.pdf','pdf_sha256':'f'*64}
        self.key='a'*32;self.approved='2026-01-01T00:00:00+00:00'
        self.messages.list.return_value.execute.return_value={'messages':[{'id':'m'}]}
        self.message={'id':'m','threadId':'thread','labelIds':['SENT'],'internalDate':str(int(datetime.now(timezone.utc).timestamp())*1000),
                      'payload':{'headers':[{'name':name,'value':value} for name,value in [
                          ('From','a@example.com'),('To','b@example.com'),('Subject','テスト資料'),
                          ('Message-ID','<REWRITTEN@mail.gmail.com>'),('X-CYCA-Operation-ID',self.key),
                          ('X-CYCA-Payload-SHA256',delivery_fingerprint(self.payload))]]}}
        self.messages.get.return_value.execute.return_value=self.message
    def find(self):return self.transport.find(self.key,'a@example.com',payload=self.payload,approved_at=self.approved)
    def change(self,name,value):
        self.message['payload']['headers']=[h for h in self.message['payload']['headers'] if h['name']!=name]
        if value is not None:self.message['payload']['headers'].append({'name':name,'value':value})
    def test_rewritten_id_exact_markers_pass(self):
        self.assertEqual(self.find(),[{'id':'m','threadId':'thread'}])
        args=self.messages.list.call_args.kwargs
        self.assertRegex(args['q'],r'^after:\d+ before:\d+$')
        self.assertEqual(args['labelIds'],['SENT'])
        self.assertEqual(self.messages.get.call_args.kwargs['format'],'metadata')
    def test_serialized_mime_folded_proof_roundtrip(self):
        import base64
        from email import policy
        from email.parser import BytesParser
        self.transport.send(self.payload,b'%PDF-test',self.key)
        wire=base64.urlsafe_b64decode(self.messages.send.call_args.kwargs['body']['raw'])
        self.assertIn(b'X-CYCA-Payload-SHA256:\n ',wire)
        mime=BytesParser(policy=policy.default).parsebytes(wire)
        # Gmail metadata can expose RFC-unfolded header values with leading space.
        headers=[{'name':k,'value':str(v)} for k,v in mime.items()]
        headers=[h for h in headers if h['name']!='Message-ID']
        headers.append({'name':'Message-ID','value':'<rewritten@mail.gmail.com>'})
        self.message['payload']['headers']=headers
        self.assertEqual(len(self.find()),1)
        self.change('X-CYCA-Payload-SHA256',' '+delivery_fingerprint(self.payload)[:32]+' '+delivery_fingerprint(self.payload)[32:]+' ')
        self.assertEqual(self.find(),[])

    def test_wrong_payload_headers_or_label_do_not_match(self):
        original=copy.deepcopy(self.message)
        for name,value in [('From','other@example.com'),('To','other@example.com'),('To','b@example.com, c@example.com'),
                           ('Subject','別資料'),('Cc','c@example.com'),('X-CYCA-Operation-ID','b'*32),
                           ('X-CYCA-Payload-SHA256','0'*64),('X-CYCA-Payload-SHA256',None)]:
            with self.subTest(name=name,value=value):
                self.message=copy.deepcopy(original);self.change(name,value)
                self.messages.get.return_value.execute.return_value=self.message
                self.assertEqual(self.find(),[])
        self.message=copy.deepcopy(original);self.message['labelIds']=['INBOX']
        self.messages.get.return_value.execute.return_value=self.message
        self.assertEqual(self.find(),[])
    def test_legacy_requires_original_id_and_addresses(self):
        self.change('X-CYCA-Operation-ID',None);self.change('X-CYCA-Payload-SHA256',None)
        self.assertEqual(self.find(),[])
        self.change('Message-ID',f'<cyca-{self.key}@example.com>')
        self.assertEqual(len(self.find()),1)
        self.change('From','other@example.com');self.assertEqual(self.find(),[])
    def test_missing_or_invalid_timestamp_fails_before_search(self):
        for value in (None,'invalid','2026-01-01','2099-01-01T00:00:00+00:00'):
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.transport.find(self.key,'a@example.com',payload=self.payload,approved_at=value)
        self.messages.list.assert_not_called()
    def test_multiple_matches_remain_ambiguous(self):
        self.messages.list.return_value.execute.return_value={'messages':[{'id':'m'},{'id':'n'}]}
        second=copy.deepcopy(self.message);second['id']='n'
        self.messages.get.return_value.execute.side_effect=[self.message,second]
        self.assertEqual(len(self.find()),2)
    def test_second_page_match(self):
        self.messages.list.return_value.execute.side_effect=[{'messages':[],'nextPageToken':'p2'},{'messages':[{'id':'m'}]}]
        self.assertEqual(len(self.find()),1)
        self.assertEqual(self.messages.list.call_args.kwargs['pageToken'],'p2')
    def test_page_cap_or_metadata_failure_never_returns_partial_match(self):
        self.messages.list.return_value.execute.side_effect=[{'messages':[{'id':'m'}],'nextPageToken':str(i)} for i in range(5)]
        with self.assertRaises(ValueError):self.find()
        self.messages.list.return_value.execute.side_effect=None
        self.messages.list.return_value.execute.return_value={'messages':[{'id':'m'},{'id':'n'}]}
        self.messages.get.return_value.execute.side_effect=[self.message,RuntimeError('offline')]
        with self.assertRaises(RuntimeError):self.find()
    def test_corrupt_second_metadata_or_wrong_id_invalidates_all(self):
        self.messages.list.return_value.execute.return_value={'messages':[{'id':'m'},{'id':'n'}]}
        cases=[{},dict(self.message,id='wrong'),dict(self.message,id='n',internalDate='bad'),
               dict(self.message,id='n',payload={}),dict(self.message,id='n',payload={'headers':[{}]})]
        for corrupt in cases:
            with self.subTest(fields=list(corrupt)):
                self.messages.get.return_value.execute.side_effect=[self.message,corrupt]
                with self.assertRaises(ValueError):self.find()

    def test_zero_and_duplicate_header(self):
        self.message['payload']['headers'].append({'name':'X-CYCA-Operation-ID','value':self.key})
        self.assertEqual(self.find(),[])
        self.messages.list.return_value.execute.return_value={}
        self.assertEqual(self.find(),[])

if __name__=='__main__':unittest.main()
