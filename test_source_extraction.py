import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from gemini_resilience import GeminiPage
from source_extraction import extract_job
from source_table import reconcile_cyca_reference
from template_fill_test_v2 import Estimate, Category, LineItem
from reference_layout import build_reference_outputs


class SourceExtractionTest(unittest.TestCase):
    def test_empty_pdf_is_retried_as_page_images_without_duplicating_rows(self):
        job=GeminiPage(source_name='scan.pdf',page_number=1,total_pages=2,parts=['pdf'],whole_document=True)
        images=[GeminiPage(source_name='scan.pdf',page_number=i,total_pages=2,parts=['image']) for i in (1,2)]
        replies=[{'detail_data':[]},{'detail_data':[]},{'detail_data':[{'品名':'架空工事','金額':100,'数量':1,'単価':100,'単位':'式','見積元':'架空社'}]}]
        with patch('source_extraction.call_gemini_with_retry',side_effect=[SimpleNamespace(text=json.dumps(r),model_name='fake') for r in replies]) as call, patch('source_extraction._render_pdf_pages_as_images',return_value=images),patch('source_extraction.reconcile_native_pdf',side_effect=lambda p,r,s:(r,s)):
            summaries,records,models,errors=extract_job(None,job,'scan.pdf',1,primary_model='fake',fallback_models=[])
        self.assertEqual(call.call_count,3)
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['__page_number'],2)
        self.assertEqual(records[0]['__source_name'],'scan.pdf')
        self.assertEqual(errors,[])

    def test_missing_or_malformed_page_is_retained_as_a_blocking_read_error(self):
        job=GeminiPage(source_name='scan.pdf',page_number=1,total_pages=1,parts=[],whole_document=True)
        image=GeminiPage(source_name='scan.pdf',page_number=1,total_pages=1,parts=[])
        with patch('source_extraction.call_gemini_with_retry',return_value=SimpleNamespace(text='',model_name='fake')),patch('source_extraction._render_pdf_pages_as_images',return_value=[image]):
            _,records,_,errors=extract_job(None,job,'scan.pdf',1,primary_model='fake',fallback_models=[])
        self.assertEqual(records,[])
        self.assertTrue(errors)
        self.assertTrue(all(e['source']=='scan.pdf' for e in errors))

    def test_valid_pdf_does_not_trigger_image_retry(self):
        job=GeminiPage(source_name='native.pdf',page_number=1,total_pages=1,parts=[],whole_document=True)
        with patch('source_extraction.call_gemini_with_retry',return_value=SimpleNamespace(text='{"detail_data":[{"品名":"架空","数量":1,"単位":"式","単価":100,"金額":100}]}',model_name='fake')),patch('source_extraction._render_pdf_pages_as_images') as fallback,patch('source_extraction.reconcile_native_pdf',side_effect=lambda p,r,s:(r,s)):
            extract_job(None,job,'native.pdf',1,primary_model='fake',fallback_models=[])
        fallback.assert_not_called()

    def test_validated_original_template_preserves_summary_expense_and_discount(self):
        estimate=Estimate('','架空確認先','架空工事','2026/10/21','30日',[Category('防水工事',[LineItem('架空防水'+str(i),'仕様',.25,'㎡',4000,1000) for i in range(3)]),Category('運搬費',[LineItem('運搬費','原本の費目',1,'式',10000,10000)],True)],-1000,.1,True)
        _,pdf,_=build_reference_outputs(estimate)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'synthetic.pdf';path.write_bytes(pdf)
            result=reconcile_cyca_reference(path,[{'見積元':'架空社','__source_name':'synthetic.pdf'}],[])
        self.assertIsNotNone(result)
        rows,_=result
        self.assertEqual(sum(r['金額'] for r in rows),estimate.net_total)
        self.assertEqual([r['品名'] for r in rows if r.get('__summary_only')],['運搬費','値引き'])
        self.assertTrue(all(r['__page_number']>=1 for r in rows))
        self.assertTrue(all(r.get('抽出元テキスト範囲') for r in rows))
        self.assertEqual(len(rows),5)
        from source_amounts import read_amounts
        totals=read_amounts('synthetic.pdf',pdf,{'summary_sources':[{'元ファイル':'synthetic.pdf','改小計':999999}]})
        self.assertEqual(totals['net_total'],estimate.net_total)

    def test_legacy_array_keeps_source_and_physical_page(self):
        from estimate_pipeline import split_extraction_payload
        _,rows=split_extraction_payload([{'品名':'架空工事'}],source_name='scan.pdf',page_number=2)
        self.assertEqual(rows[0]['__source_name'],'scan.pdf')
        self.assertEqual(rows[0]['__page_number'],2)

    def test_native_numbers_keep_the_unique_matching_work_category_and_part(self):
        from source_table import reconcile_native_pdf
        native=[dict(品名='室外機移設',仕様='',数量=3.,単位='台',単価=10000.,金額=30000.,__page_number=1,工事種別='')]
        rows=[dict(native[0],見積元='架空設備社',工事種別='空調設備工事',部位='屋上',__source_name='a.pdf')]
        with patch('source_table.reconcile_cyca_reference',return_value=None),patch('source_table.read_native_tables',return_value=(native,[30000],'')):
            result,_=reconcile_native_pdf('a.pdf',rows,[])
        self.assertEqual(result[0]['工事種別'],'空調設備工事')
        self.assertEqual(result[0]['部位'],'屋上')
        self.assertEqual(result[0]['金額'],30000)

    def test_unknown_table_does_not_get_balancing_fees(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'unsupported.pdf';p.write_bytes(b'not a pdf')
            self.assertIsNone(reconcile_cyca_reference(p,[{'見積元':'架空社'}],[]))
