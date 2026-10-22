import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from complete_document_cache import get_document,put_document


class CompleteDocumentCacheTest(unittest.TestCase):
    def fixture(self):
        source={'id':hashlib.sha256(b'original-bytes').hexdigest(),'name':'原本　見積.pdf','pages':2}
        records=[dict(品名='原本行'+str(i),数量=1,単位='式',単価=100,金額=100,
                      __source_name=source['name'],__page_number=i) for i in (1,2)]
        summary=dict(改小計=200,消費税=20,工事費計=220,__source_name=source['name'],
                     __page_number=1,__page_role='cover_summary_page')
        return source,([summary],records,['fake'],[])

    def test_complete_hit_is_available_without_preparation_or_network_and_returns_copies(self):
        source,result=self.fixture();cache={};settings=dict(primary_model='fake',fallback_models=[])
        with patch('extraction_batch.pipeline_version',return_value='revision-a'):
            self.assertTrue(put_document(cache,source,result,**settings))
            result[1][0]['金額']=999
            with patch('gemini_resilience.prepare_upload_for_gemini_pages') as prepare,patch('source_extraction.call_gemini_with_retry') as network:
                hit=get_document(cache,source,**settings)
                self.assertEqual(hit[1][0]['金額'],100)
                hit[1][0]['金額']=888
                self.assertEqual(get_document(cache,source,**settings)[1][0]['金額'],100)
                prepare.assert_not_called();network.assert_not_called()

    def test_bytes_exact_filename_model_and_revision_each_invalidate(self):
        source,result=self.fixture();cache={};settings=dict(primary_model='fake',fallback_models=['fallback'])
        with patch('extraction_batch.pipeline_version',return_value='revision-a'):
            self.assertTrue(put_document(cache,source,result,**settings))
            for changed in (dict(source,id=hashlib.sha256(b'changed').hexdigest()),
                            dict(source,name='原本 見積.pdf'),dict(source,pages=3)):
                self.assertIsNone(get_document(cache,changed,**settings))
            self.assertIsNone(get_document(cache,source,primary_model='changed',fallback_models=['fallback']))
            self.assertIsNone(get_document(cache,source,primary_model='fake',fallback_models=[]))
        with patch('extraction_batch.pipeline_version',return_value='revision-b'):
            self.assertIsNone(get_document(cache,source,**settings))

    def test_incomplete_or_mixed_readings_are_not_cached(self):
        source,result=self.fixture();settings=dict(primary_model='fake',fallback_models=[])
        for kind in ('failed','missing_page','wrong_source','mismatch','missing_price','reference_only'):
            candidate=copy.deepcopy(result)
            if kind=='failed':candidate[3].append({'reason':'temporary'})
            elif kind=='missing_page':candidate[1].pop()
            elif kind=='wrong_source':candidate[1][0]['__source_name']='another.pdf'
            elif kind=='mismatch':candidate[0][0]['改小計']=300
            elif kind=='missing_price':candidate[1][0]['単価']=None
            else:candidate[1].clear()
            cache={}
            self.assertFalse(put_document(cache,source,candidate,**settings),kind)
            self.assertFalse(cache,kind)

    def test_byte_cap_and_count_cap_are_bounded_and_oversized_item_is_not_kept(self):
        source,result=self.fixture();cache={};settings=dict(primary_model='fake',fallback_models=[])
        size=len(json.dumps(result,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode())
        with patch('extraction_batch.pipeline_version',return_value='revision-a'),patch('complete_document_cache.MAX_CACHE_BYTES',size*2+20),patch('complete_document_cache.MAX_DOCUMENTS',2):
            for i in range(4):
                changed=dict(source,id=hashlib.sha256(str(i).encode()).hexdigest())
                self.assertTrue(put_document(cache,changed,result,**settings))
                self.assertLessEqual(len(cache),2)
                self.assertLessEqual(sum(len(json.dumps(v,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()) for v in cache.values()),size*2+20)
            oversized=copy.deepcopy(result);oversized[0][0]['根拠']='x'*(size*3)
            before=copy.deepcopy(cache)
            self.assertFalse(put_document(cache,source,oversized,**settings))
            self.assertEqual(cache,before)


if __name__=='__main__':unittest.main()
