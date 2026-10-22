import copy
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from extraction_batch import extract_batch


class BatchTest(unittest.TestCase):
    def test_parallel_network_reads_keep_original_order_and_cache_per_session(self):
        with tempfile.TemporaryDirectory() as temp:
            jobs=[SimpleNamespace(source_name=f'原本　{i}.pdf',page_number=1,total_pages=1,whole_document=True) for i in range(4)]
            paths={}
            for i,job in enumerate(jobs):
                path=Path(temp)/str(i);path.write_bytes(bytes([i]));paths[id(job)]=path
            gate=threading.Barrier(2,timeout=3);lock=threading.Lock();active=0;peak=0;calls=[];closed=[]
            def factory():return SimpleNamespace(close=lambda:closed.append(True))
            def read(client,job,path,count,**kwargs):
                nonlocal active,peak
                with lock:active+=1;peak=max(peak,active);calls.append(job.source_name)
                gate.wait()
                kwargs['on_model_start']('fake',1)
                with lock:active-=1
                return [],[{'__source_name':job.source_name,'__page_number':1,'name':job.source_name,'品名':'検証品目','数量':1,'単位':'式','単価':100,'金額':100}],['fake'],[]
            cache={}
            kwargs=dict(client_factory=factory,primary_model='fake',fallback_models=[],cache=cache,extractor=read)
            events=list(extract_batch(jobs,paths,4,**kwargs))
            self.assertEqual(peak,2)
            self.assertEqual([r[1][0]['name'] for r in events[-1]['results']],[j.source_name for j in jobs])
            self.assertEqual(len(calls),4);self.assertEqual(len(closed),4)
            second=list(extract_batch(jobs,paths,4,**kwargs))
            self.assertEqual(len(calls),4)
            self.assertEqual(sum(e.get('cached',False) is True for e in second if e['kind']=='done'),4)
            second[-1]['results'][0][1][0]['name']='mutated'
            third=list(extract_batch(jobs,paths,4,**kwargs))
            self.assertEqual(third[-1]['results'][0][1][0]['name'],jobs[0].source_name)
            self.assertEqual(len(cache),4)

    def test_one_failed_file_does_not_erase_others_or_get_cached(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'pdf';p.write_bytes(b'original')
            jobs=[SimpleNamespace(source_name=n,page_number=1,total_pages=1,whole_document=True) for n in ('bad.pdf','good.pdf')]
            def read(client,job,*args,**kwargs):
                if job.source_name=='bad.pdf':raise ValueError('secret-provider-detail')
                return [],[{'__source_name':job.source_name,'__page_number':1,'name':job.source_name,'品名':'検証品目','数量':1,'単位':'式','単価':100,'金額':100}],[],[]
            cache={};kwargs=dict(client_factory=lambda:None,primary_model='fake',fallback_models=[],extractor=read,cache=cache)
            result=list(extract_batch(jobs,{id(j):p for j in jobs},2,**kwargs))[-1]['results']
            self.assertEqual(result[1][1][0]['name'],'good.pdf');self.assertEqual(len(cache),1)
            self.assertEqual(result[0][3],[{'source':'bad.pdf','page':None,'reason':'read_failed'}])
            self.assertNotIn('secret-provider-detail',repr(result))
            other_session={}
            list(extract_batch(jobs,{id(j):p for j in jobs},2,**{**kwargs,'cache':other_session}))
            self.assertEqual(len(other_session),1)

    def test_changed_bytes_or_settings_invalidate_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'pdf';p.write_bytes(b'v1');job=SimpleNamespace(source_name='a.pdf',page_number=1,total_pages=1,whole_document=True)
            calls=[]
            def read(*args,**kwargs):calls.append(1);return [],[{'__source_name':'a.pdf','__page_number':1,'valid':True,'品名':'検証品目','数量':1,'単位':'式','単価':100,'金額':100}],[],[]
            kwargs=dict(client_factory=lambda:None,primary_model='fake',fallback_models=[],extractor=read,cache={})
            list(extract_batch([job],{id(job):p},1,**kwargs));list(extract_batch([job],{id(job):p},1,**kwargs))
            self.assertEqual(len(calls),1)
            p.write_bytes(b'v2');list(extract_batch([job],{id(job):p},1,**kwargs));self.assertEqual(len(calls),2)
            list(extract_batch([job],{id(job):p},1,**{**kwargs,'primary_model':'new'}));self.assertEqual(len(calls),3)

    def test_incomplete_reading_is_shown_but_never_reused_as_a_success(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'pdf';p.write_bytes(b'original')
            job=SimpleNamespace(source_name='a.pdf',page_number=1,total_pages=1,whole_document=True)
            calls=[]
            def read(*args,**kwargs):calls.append(1);return [],[{'品名':'値引き','金額':-100}],[],[]
            cache={};kwargs=dict(client_factory=lambda:None,primary_model='fake',fallback_models=[],extractor=read,cache=cache)
            first=list(extract_batch([job],{id(job):p},1,**kwargs))[-1]
            self.assertEqual(first['results'][0][1][0]['金額'],-100)
            list(extract_batch([job],{id(job):p},1,**kwargs))
            self.assertEqual(len(calls),2);self.assertEqual(cache,{})
