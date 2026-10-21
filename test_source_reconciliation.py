"""Source-level omissions are recovered only from explicit printed evidence."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from gemini_resilience import GeminiPage
from source_extraction import include_printed_discount, extract_job
from source_review_ui import source_title, source_vendor, _items_html


def records():
    return [dict(品名='機器本体',仕様='型番TEST-A',数量=1,単位='台',単価=80000,金額=80000,見積元='検証社',工事種別='機器費',__source_name='a.pdf',__page_number=2),
            dict(品名='機器取付工事',数量=1,単位='式',単価=15000,金額=15000,見積元='検証社',工事種別='取付工事',__source_name='a.pdf',__page_number=2),
            dict(品名='専用回路電気工事',数量=1,単位='式',単価=5000,金額=5000,見積元='検証社',工事種別='電気工事',__source_name='a.pdf',__page_number=2)]


def summary():
    return dict(小計=100000,端数調整=-1234,改小計=98766,消費税=9877,工事費計=108643,見積元='検証社',工事名称='機器交換検証',
                抽出元テキスト範囲='小計100,000 値引き-1,234 改小計98,766',__source_name='a.pdf',__page_number=1,__page_role='cover_summary_page')


class SourceReconciliationTest(unittest.TestCase):
    def test_reread_does_not_assign_unlocated_old_money_to_a_new_page(self):
        from source_extraction import _reread_summaries
        old=summary();old.pop('__page_number')
        new={'__source_name':'a.pdf','__page_number':1,'__page_role':'cover_summary_page'}
        merged=_reread_summaries([old],[new])[0]
        self.assertEqual(merged['工事名称'],'機器交換検証')
        self.assertNotIn('改小計',merged)
        self.assertNotIn('抽出元テキスト範囲',merged)

    def test_printed_discount_preserves_product_installation_electrical_and_provenance(self):
        before=records(); actual=include_printed_discount(before,[summary()],'a.pdf',2)
        self.assertEqual(actual[:3],before)
        self.assertEqual(sum(r['金額'] for r in actual),98766)
        self.assertEqual(actual[-1]['__page_number'],1)
        self.assertEqual(actual[-1]['金額'],-1234)
        self.assertTrue(actual[-1]['__summary_only'])
        self.assertEqual(include_printed_discount(actual,[summary()],'a.pdf',2),actual)
        self.assertEqual(len(before),3)

    def test_difference_alone_missing_expense_and_conflicting_originals_never_make_a_fee(self):
        for field,value in [('端数調整',None),('抽出元テキスト範囲','差額1234'),('__source_name','other.pdf'),('__page_number',3),('小計',100001),('改小計',90000)]:
            with self.subTest(field=field):
                s=summary();s[field]=value
                self.assertEqual(include_printed_discount(records(),[s],'a.pdf',2),records())
        s=summary();s['__page_number']=2
        self.assertEqual(include_printed_discount(records(),[summary(),s],'a.pdf',2),records())
        self.assertEqual(include_printed_discount(records()[:-1],[summary()],'a.pdf',2),records()[:-1])

    def test_missing_discount_triggers_image_reread_and_is_reconciled_before_confirmation(self):
        first=summary();first.pop('端数調整');first.pop('抽出元テキスト範囲')
        page1=summary()
        responses=[{'page_role':'cover_summary_page','summary_data':first,'detail_data':records()},
                   {'page_role':'cover_summary_page','summary_data':page1,'detail_data':[]},
                   {'page_role':'detail_page','detail_data':records()}]
        job=GeminiPage(source_name='a.pdf',page_number=1,total_pages=2,parts=[],whole_document=True)
        images=[GeminiPage(source_name='a.pdf',page_number=i,total_pages=2,parts=[]) for i in (1,2)]
        with patch('source_extraction.call_gemini_with_retry',side_effect=[SimpleNamespace(text=json.dumps(r),model_name='fake') for r in responses]) as call,patch('source_extraction._render_pdf_pages_as_images',return_value=images),patch('source_extraction.reconcile_native_pdf',side_effect=lambda p,r,s:(r,s)):
            ss,rr,mm,ee=extract_job(None,job,'not-opened.pdf',1,primary_model='fake',fallback_models=[])
        self.assertEqual(call.call_count,3)
        self.assertEqual(sum(r['金額'] for r in rr),98766)
        self.assertEqual(len(rr),4)
        self.assertEqual(ee,[])
        self.assertEqual(ss[0]['工事名称'],'機器交換検証')

    def test_project_name_is_scoped_and_table_escapes_source_content(self):
        metadata={'工事名称':'別原本の名称','summary_sources':[{'元ファイル':'a.pdf','工事名称':'機器交換'}]}
        self.assertEqual(source_title({'name':'a.pdf'},metadata,3),'機器交換')
        self.assertEqual(source_title({'name':'b.pdf'},metadata,3),'')
        self.assertEqual(source_title({'name':'b.pdf'},metadata,1),'別原本の名称')
        self.assertEqual(source_vendor({'id':'a','name':'a.pdf'},metadata,[{'原本ID':'a','見積元':'検証会社'}]),'検証会社')
        self.assertEqual(source_vendor({'id':'b','name':'b.pdf'},metadata,[{'原本ID':'a','見積元':'検証会社'}]),'業者名の記載なし')
        item={'品名':'<script>','仕様':'<img>','数量':1,'単位':'台','原価単価':12345678,'原価金額':12345678}
        html=_items_html([item])
        self.assertNotIn('<script>',html)
        self.assertIn('12,345,678円',html)
        self.assertIn('&lt;img&gt;',html)
