"""Transient note failures stay reviewable and retry instead of becoming hits."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from complete_document_cache import get_document, put_document
from extraction_batch import extract_batch
from gemini_resilience import GeminiPage


def result(status):
    return ([dict(__source_name='a.pdf',__page_number=1,改小計=100,
                  annotation_review={'status':status})],
            [dict(__source_name='a.pdf',__page_number=1,品名='原本行',数量=1,単位='式',単価=100,金額=100)],
            ['synthetic-model'],[])


class AnnotationCacheRetryTest(unittest.TestCase):
    def test_page_read_retries_failed_annotations_then_caches_complete_result_without_price_change(self):
        cache={};calls=[]
        def extractor(*args,**kwargs):
            calls.append(1)
            return result('needs_review' if len(calls)==1 else 'completed')
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'source.pdf';path.write_bytes(b'synthetic-original')
            job=GeminiPage('a.pdf',1,1,parts=[])
            values=[]
            for _ in range(3):
                events=list(extract_batch([job],{id(job):path},1,client_factory=lambda:None,
                    primary_model='synthetic-model',fallback_models=[],cache=cache,extractor=extractor))
                values.append(events[-1]['results'][0])
            self.assertEqual(len(calls),2)
            self.assertEqual([v[0][0]['annotation_review']['status'] for v in values],
                             ['needs_review','completed','completed'])
            self.assertTrue(all(v[1]==result('completed')[1] and v[3]==[] for v in values))
            # A stale page cache from before the rule must not be accepted either.
            key=next(iter(cache));cache[key]=result('needs_review')
            list(extract_batch([job],{id(job):path},1,client_factory=lambda:None,
                primary_model='synthetic-model',fallback_models=[],cache=cache,extractor=extractor))
            self.assertEqual(len(calls),3)

    def test_document_cache_rejects_unfinished_notes_on_insert_and_existing_hit(self):
        source={'id':hashlib.sha256(b'synthetic-original').hexdigest(),'name':'a.pdf','pages':1}
        settings=dict(primary_model='synthetic-model',fallback_models=[])
        for review in ({'status':'needs_review'},{'status':'unknown'},True):
            candidate=result('completed');candidate[0][0]['annotation_review']=review
            before=copy.deepcopy(candidate)
            self.assertFalse(put_document({},source,candidate,**settings))
            self.assertEqual(candidate,before)
        cache={}
        self.assertTrue(put_document(cache,source,result('completed'),**settings))
        self.assertEqual(get_document(cache,source,**settings)[1],result('completed')[1])
        key=next(iter(cache));cache[key]=result('needs_review')
        self.assertIsNone(get_document(cache,source,**settings))
        self.assertNotIn(key,cache)
        self.assertTrue(put_document(cache,source,result('completed'),**settings))
        self.assertEqual(get_document(cache,source,**settings)[1],result('completed')[1])


if __name__=='__main__':unittest.main()
