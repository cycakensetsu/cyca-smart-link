import copy
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from estimate_review import OriginalVault,review_rows,build_draft,preview_allowed,approve
from estimate_pipeline import build_intermediate_dataframe
from review_ui import preserve_uploads


class IntakeRecoveryTest(unittest.TestCase):
    def test_exact_duplicate_warns_once_and_same_name_different_bytes_stays_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            store=OriginalVault(temp,'test');st=SimpleNamespace(session_state={})
            uploads=[SimpleNamespace(name=n,getbuffer=lambda data=data:data) for n,data in
                [('施工　見積.pdf',b'a'),('施工　見積.pdf',b'a'),('施工　見積.pdf',b'b')]]
            with patch('review_ui.vault',return_value=store),patch('pdf_worker.page_count',return_value=1):
                pairs=preserve_uploads(st,uploads)
            sources=st.session_state['_review_sources']
            self.assertEqual(len(pairs),2);self.assertEqual(len(sources),2)
            self.assertEqual(sources[0]['duplicate_count'],2)
            self.assertNotEqual(sources[0]['id'],sources[1]['id'])
            self.assertNotEqual(sources[0]['name'],sources[1]['name'])
            self.assertEqual(sources[1]['original_name'],'施工　見積.pdf')
            self.assertEqual(store.read(sources[0]['id']),b'a');self.assertEqual(store.read(sources[1]['id']),b'b')
            records=[dict(品名='施工',数量=1,単位='式',単価=100,金額=100,__source_name=s['name']) for s in sources]
            frame,_=build_intermediate_dataframe(records)
            self.assertEqual([r['原本ID'] for r in review_rows(frame,sources)],[s['id'] for s in sources])

    def test_bad_pdf_keeps_other_originals_and_its_own_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            store=OriginalVault(temp,'test');st=SimpleNamespace(session_state={})
            uploads=[SimpleNamespace(name=n,getbuffer=lambda data=data:data) for n,data in [('bad.pdf',b'bad'),('good.pdf',b'good')]]
            def count(data):
                if data==b'bad':raise ValueError('broken')
                return 2
            with patch('review_ui.vault',return_value=store),patch('pdf_worker.page_count',side_effect=count):
                preserve_uploads(st,uploads)
            sources=st.session_state['_review_sources']
            self.assertTrue(sources[0]['prepare_error']);self.assertEqual(sources[1]['pages'],2)
            self.assertEqual(store.read(sources[0]['id']),b'bad')

    def test_reference_requires_an_explicit_decision_and_cannot_hide_priced_rows(self):
        source={'id':'quote','name':'a.pdf','pages':1,'confirmed':True,'net_total':100,'tax_basis':'税抜','tax_rate':.1}
        ref={'id':'drawing','name':'drawing.pdf','reference_only':True,'reference_confirmed':True,'confirmed':True,'read_errors':[{'page':1}]}
        frame,_=build_intermediate_dataframe([dict(品名='施工',数量=1,単位='式',単価=100,金額=100,見積元='架空社',工事種別='塗装工事',__source_name='a.pdf',__page_number=1)])
        rows=review_rows(frame,[source])
        draft=build_draft(rows,copy.deepcopy(rows),[source,ref])
        self.assertEqual(draft['unresolved'],[])
        self.assertEqual(draft['payload']['合計']['仕入原価'],100)
        refused=build_draft(rows,copy.deepcopy(rows),[{**source,'reference_only':True,'reference_confirmed':True},ref])
        self.assertTrue(any(i['種類']=='参考資料の確認' for i in refused['unresolved']))
        unconfirmed=build_draft(rows,copy.deepcopy(rows),[source,{**ref,'reference_confirmed':False}])
        self.assertTrue(unconfirmed['unresolved'])

    def test_duplicate_warning_does_not_block_layout_preview_but_requires_final_choice(self):
        source={'id':'quote','name':'a.pdf','pages':1,'confirmed':True,'net_total':200,'tax_basis':'税抜','tax_rate':.1}
        frame,_=build_intermediate_dataframe([dict(品名='運搬費',数量=1,単位='式',単価=100,金額=100,見積元='架空社',工事種別='諸経費',__source_name='a.pdf',__page_number=1) for _ in range(2)])
        rows=review_rows(frame,[source]);draft=build_draft(rows,copy.deepcopy(rows),[source])
        self.assertTrue(draft['unresolved']);self.assertTrue(preview_allowed(draft))
        with self.assertRaises(ValueError):approve(draft,'test')
        broken=copy.deepcopy(draft);broken['unresolved'].append({'種類':'原本小計','レベル':'停止'})
        self.assertFalse(preview_allowed(broken))
