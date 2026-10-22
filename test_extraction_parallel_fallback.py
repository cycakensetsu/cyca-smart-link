import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from extraction_batch import extract_batch
from source_extraction import extract_job
from gemini_resilience import GeminiPage


class ParallelFallbackTest(unittest.TestCase):
    def response(self,page,*,whole=False):
        payload={'detail_data':[{'品名':('discarded-whole' if whole else 'physical-page-')+str(page),
                                 '数量':1,'単位':'式','単価':100,'金額':100,'__page_number':page}]}
        if page==1:
            payload.update(page_role='cover_summary_page',summary_data={
                '改小計':100 if whole else 300,'消費税':10 if whole else 30,
                '工事費計':110 if whole else 330,'__page_number':1})
        return SimpleNamespace(text=json.dumps(payload),model_name='fake')

    def test_four_file_fallbacks_share_four_network_slots_and_keep_physical_order(self):
        lock=threading.Lock();active=0;peak=0;closed=[]
        whole_barrier=threading.Barrier(4,timeout=5)
        page_barrier=threading.Barrier(4,timeout=5)
        def fake_call(client,contents,**kwargs):
            nonlocal active,peak
            whole=kwargs['page_number'] is None
            with lock:active+=1;peak=max(peak,active)
            try:
                if kwargs.get('on_model_start'):kwargs['on_model_start']('fake',kwargs['page_number'])
                (whole_barrier if whole else page_barrier).wait()
                return self.response(1 if whole else kwargs['page_number'],whole=whole)
            finally:
                with lock:active-=1
        def images(path,name):
            return [GeminiPage(name,p,3,parts=['image']) for p in (1,2,3)]
        def factory():return SimpleNamespace(close=lambda:closed.append(True))
        with tempfile.TemporaryDirectory() as temp:
            jobs=[GeminiPage(f'source-{i}.pdf',1,3,parts=['whole'],whole_document=True) for i in range(4)]
            paths={}
            for i,job in enumerate(jobs):
                p=Path(temp)/str(i);p.write_bytes(str(i).encode());paths[id(job)]=p
            with patch('source_extraction.call_gemini_with_retry',side_effect=fake_call),patch('source_extraction._render_pdf_pages_as_images',side_effect=images),patch('source_extraction.reconcile_native_pdf',side_effect=lambda p,r,s:(r,s)):
                events=list(extract_batch(jobs,paths,4,client_factory=factory,primary_model='fake',fallback_models=[]))
        self.assertEqual(peak,4)
        self.assertEqual(len(closed),16)
        for job,result in zip(jobs,events[-1]['results']):
            self.assertEqual([r['__page_number'] for r in result[1]],[1,2,3])
            self.assertTrue(all(r['__source_name']==job.source_name for r in result[1]))
            self.assertFalse(any(r['品名'].startswith('discarded') for r in result[1]))
            self.assertEqual(sum(r['金額'] for r in result[1]),300)
            self.assertEqual(result[3],[])
        self.assertEqual(sum(e['kind']=='reading' for e in events),16)

    def test_fallback_page_failure_keeps_other_pages_and_marks_the_failed_page(self):
        job=GeminiPage('source.pdf',1,3,parts=['whole'],whole_document=True)
        def fake_call(client,contents,**kwargs):
            page=kwargs['page_number']
            if page==2:
                error=RuntimeError('private-provider-text');error.code=503;raise error
            return self.response(1 if page is None else page,whole=page is None)
        images=[GeminiPage(job.source_name,p,3,parts=['image']) for p in (1,2,3)]
        with patch('source_extraction.call_gemini_with_retry',side_effect=fake_call),patch('source_extraction._render_pdf_pages_as_images',return_value=images),patch('source_extraction.reconcile_native_pdf',side_effect=lambda p,r,s:(r,s)):
            result=extract_job(None,job,'source.pdf',1,primary_model='fake',fallback_models=[],client_factory=lambda:None)
        self.assertEqual([r['__page_number'] for r in result[1]],[1,3])
        self.assertEqual(result[3],[{'source':'source.pdf','page':2,'reason':'temporary'}])
        self.assertNotIn('private-provider-text',repr(result))

    def test_waiting_event_is_emitted_before_a_slow_read_completes(self):
        release=threading.Event()
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'source.pdf';p.write_bytes(b'synthetic')
            job=GeminiPage('source.pdf',1,1,parts=['synthetic'])
            def read(*args,**kwargs):
                if not release.wait(timeout=5):raise RuntimeError('test release not received')
                return [],[{'品名':'原本行','数量':1,'単位':'式','単価':100,'金額':100,
                            '__source_name':job.source_name,'__page_number':1}],[],[]
            iterator=extract_batch([job],{id(job):p},1,client_factory=lambda:None,
                                  primary_model='fake',fallback_models=[],extractor=read)
            self.assertEqual(next(iterator)['kind'],'started')
            try:
                event=next(iterator)
                self.assertEqual(event['kind'],'waiting')
                self.assertGreaterEqual(event['seconds'],1)
                self.assertEqual(event['completed'],0)
            finally:release.set()
            self.assertEqual(list(iterator)[-1]['kind'],'result')


if __name__=='__main__':unittest.main()
