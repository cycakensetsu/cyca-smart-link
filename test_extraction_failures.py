import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from extraction_batch import extract_batch, extraction_failure_reason, extraction_notice


class ExtractionFailureTest(unittest.TestCase):
    def job(self,name):
        return SimpleNamespace(source_name=name,page_number=1,total_pages=1,whole_document=True)

    def test_invalid_credentials_are_sanitized_not_cached_and_do_not_erase_other_files(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'synthetic.pdf';path.write_bytes(b'synthetic-original')
            jobs=[self.job('failed.pdf'),self.job('good.pdf')];calls=[]
            marker='private-credential-and-document-marker'
            def read(client,job,*args,**kwargs):
                calls.append(job.source_name)
                if job.source_name=='failed.pdf':
                    raise ValueError('API_KEY_INVALID: API key not valid. '+marker)
                return [],[{'品名':'確認用','数量':1,'単位':'式','単価':100,'金額':100,
                             '__source_name':job.source_name,'__page_number':1}],[],[]
            cache={};options=dict(client_factory=lambda:None,primary_model='fake',fallback_models=[],extractor=read,cache=cache)
            events=list(extract_batch(jobs,{id(j):path for j in jobs},2,**options))
            result=events[-1]['results']
            self.assertEqual(result[0],([],[],[],[{'source':'failed.pdf','page':None,'reason':'credential_invalid'}]))
            self.assertEqual(result[1][1][0]['__source_name'],'good.pdf')
            self.assertNotIn(marker,repr(events));self.assertEqual(len(cache),1)
            list(extract_batch(jobs,{id(j):path for j in jobs},2,**options))
            self.assertEqual(calls.count('failed.pdf'),2)
            self.assertEqual(calls.count('good.pdf'),1)

    def test_wrapped_quota_temporary_and_unknown_failures_keep_only_allowlisted_reasons(self):
        class ProviderError(Exception):
            def __init__(self,code):
                super().__init__('provider-private-detail')
                self.error_code=code
        exceptions=[ProviderError(429),ProviderError(503),ValueError('provider-private-detail')]
        self.assertEqual([extraction_failure_reason(e) for e in exceptions],['quota','temporary','read_failed'])
        for message in ('API_KEY_INVALID','API_KEY_EXPIRED','API key has expired. Renew it.'):
            self.assertEqual(extraction_failure_reason(ValueError(message)),'credential_invalid')
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'synthetic.pdf';path.write_bytes(b'synthetic-original')
            jobs=[self.job(str(i)+'.pdf') for i in range(3)]
            def read(client,job,*a,**k):raise exceptions[int(job.source_name[0])]
            cache={}
            events=list(extract_batch(jobs,{id(j):path for j in jobs},3,
                client_factory=lambda:None,primary_model='fake',fallback_models=[],cache=cache,extractor=read))
            self.assertEqual([r[3][0]['reason'] for r in events[-1]['results']],['quota','temporary','read_failed'])
            self.assertEqual(cache,{})
            self.assertNotIn('provider-private-detail',repr(events))

    def test_all_failed_batch_gets_one_connection_notice_instead_of_file_replacement_advice(self):
        failures=[{'source':'private-original-name.pdf','reason':'credential_invalid'} for _ in range(20)]
        message=extraction_notice(failures)
        self.assertIn('接続設定',message)
        self.assertIn('原本は保持',message)
        self.assertNotIn('選び直',message)
        self.assertNotIn('private-original-name',message)
        self.assertEqual(message,extraction_notice(failures[:1]))
        self.assertEqual(message,extraction_notice(failures,empty=False))
        self.assertIn('利用上限',extraction_notice([{'reason':'quota'}]))
        self.assertIn('少し待って',extraction_notice([{'reason':'temporary'}]))
        self.assertNotIn('接続設定',extraction_notice([{'reason':'no_details'}]))


if __name__=='__main__':
    unittest.main()
