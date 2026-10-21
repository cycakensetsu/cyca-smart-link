import copy
import unittest
from source_sections import reconcile_sections
from source_rounding import printed_rounding
from estimate_pipeline import build_intermediate_dataframe
from estimate_review import review_rows,build_draft,Target,target_total
from markup_rules import line_amount


class SourceSectionsTest(unittest.TestCase):
    def test_one_cover_trade_includes_detail_page_expenses_without_dropping_them(self):
        summary={'__source_name':'a.pdf','__page_number':1,'__page_role':'cover_summary_page',
                 '工事項目':[{'工事項目':'工事一式','金額':110000}]}
        rows=[dict(品名='工事一式',数量=1,単位='式',金額=110000,工事種別='空調工事',__source_name='a.pdf',__page_number=1),
              dict(品名='機器本体',数量=1,単位='台',単価=80000,金額=80000,工事種別='機器費',__source_name='a.pdf',__page_number=2),
              dict(品名='取付工事',数量=1,単位='式',単価=20000,金額=20000,工事種別='空調工事',__source_name='a.pdf',__page_number=2),
              dict(品名='荷揚げ費',数量=1,単位='式',単価=10000,金額=10000,工事種別='諸経費',__source_name='a.pdf',__page_number=2)]
        result=reconcile_sections(rows,[summary],'a.pdf')
        self.assertEqual(result,rows[1:])
        summary['工事項目'].append({'工事項目':'別工事','金額':1})
        self.assertEqual(len(reconcile_sections(rows,[summary],'a.pdf')),4)

    def test_only_verified_trade_aggregate_is_removed_and_fees_and_discount_remain(self):
        rows=[dict(品名='取付工事',数量=1,単位='式',単価=None,金額=100000,工事種別='取付工事',__source_name='a.pdf',__page_number=1),
              dict(品名='諸経費',数量=1,単位='式',単価=None,金額=6000,工事種別='諸経費',__source_name='a.pdf',__page_number=1),
              dict(品名='値引き',数量=None,単位=None,単価=None,金額=-1234,工事種別='値引き',抽出元テキスト範囲='値引き -1,234',__source_name='a.pdf',__page_number=1),
              dict(品名='設置作業',数量=2,単位='台',単価=40000,金額=80000,工事種別='取付工事',__source_name='a.pdf',__page_number=2),
              dict(品名='撤去作業',数量=2,単位='台',単価=10000,金額=20000,工事種別='取付工事',__source_name='a.pdf',__page_number=2)]
        summary={'__source_name':'a.pdf','__page_number':1,'__page_role':'cover_summary_page','工事項目':[{'工事項目':'取付工事','金額':100000},{'工事項目':'諸経費','金額':6000}],'小計':106000,'端数調整':-1234,'改小計':104766}
        original=copy.deepcopy(rows)
        out=reconcile_sections(rows,[summary],'a.pdf')
        self.assertEqual(rows,original)
        self.assertEqual(len(out),4)
        self.assertEqual(sum(r['金額'] for r in out),104766)
        self.assertEqual(out[0]['単価'],6000)
        self.assertTrue(out[0]['__summary_only'])
        self.assertEqual((out[1]['数量'],out[1]['単価']),(1,-1234))
        self.assertEqual(out[2:],rows[3:])
        summary['工事項目'][0]['金額']=99999
        self.assertEqual(len(reconcile_sections(rows,[summary],'a.pdf')),5,'a coincidental group name is not enough')
        summary['__source_name']='other.pdf'
        unrelated=reconcile_sections(rows,[summary],'a.pdf')
        self.assertEqual(len(unrelated),len(rows))
        self.assertEqual([r['金額'] for r in unrelated],[r['金額'] for r in rows])
        self.assertFalse(any(r.get('__summary_only') for r in unrelated))
        self.assertEqual(rows,original)

    def fixture(self):
        source={'id':'source-a','name':'a.pdf','pages':1,'net_total':110095,'tax_basis':'税抜','tax_rate':.1,'confirmed':True}
        records=[dict(品名='測定数量の工事',仕様='原本仕様',数量=10.1,単位='㎡',単価=1000,金額=10095,見積元='検証社',工事種別='取付工事',抽出元テキスト範囲='測定数量の工事 10.1㎡ 単価1,000 金額10,095',__source_name='a.pdf',__page_number=1),
                 dict(品名='機器本体',仕様='型番TEST-A',数量=2,単位='台',単価=50000,金額=100000,見積元='検証社',工事種別='機器費',抽出元テキスト範囲='機器本体 2台 50,000 100,000',__source_name='a.pdf',__page_number=1)]
        frame,_=build_intermediate_dataframe(records);rows=review_rows(frame,[source])
        for r in rows:r['集計のみ']=False
        source['rounding_rows']=[rows[0]['行ID']]
        metadata={'宛名':'架空確認先 御中','工事名称':'架空の機器交換','顧客税率':.1}
        return rows,source,metadata

    def test_source_rounding_requires_printed_evidence_and_does_not_rewrite_purchase_facts(self):
        rows,source,metadata=self.fixture()
        self.assertEqual(printed_rounding(rows[0]),-5)
        for mode,value in [('上乗せ額',0),('上乗せ額',100000),('掛け率',1.2),('粗利益率',20)]:
            target=Target('全体','全体',mode,value)
            draft=build_draft(rows,copy.deepcopy(rows),[source],[target],metadata=metadata)
            self.assertEqual(draft['unresolved'],[])
            self.assertEqual(draft['payload']['合計']['仕入原価'],110095)
            self.assertEqual(draft['payload']['合計']['税抜売価'],target_total(110095,target))
            for before,priced in zip(rows,draft['payload']['計算明細']):
                for field in ('品名','仕様','数量','単位','原価単価','原価金額'):
                    self.assertEqual(before[field],priced[field])
                self.assertEqual(line_amount(priced['数量'],priced['見積単価']),priced['見積金額'])
        self.assertEqual(rows[0]['原価金額'],10095)

    def test_claimed_confirmation_alone_cannot_bypass_a_calculation_error(self):
        rows,source,metadata=self.fixture()
        source['rounding_rows']=[]
        draft=build_draft(rows,rows,[source],metadata=metadata)
        self.assertIn('明細検算',[i['種類'] for i in draft['unresolved']])
        source['rounding_rows']=[rows[0]['行ID']]
        rows[0]['抽出元テキスト範囲']='no numeric evidence'
        self.assertIsNone(printed_rounding(rows[0]))
        draft=build_draft(rows,rows,[source],metadata=metadata)
        self.assertIn('明細検算',[i['種類'] for i in draft['unresolved']])

    def test_large_differences_and_modified_original_values_are_not_rounding(self):
        rows,source,metadata=self.fixture()
        edited=copy.deepcopy(rows);edited[0]['原価単価']=1001;edited[0]['訂正理由']='検証用の変更'
        draft=build_draft(rows,edited,[source],metadata=metadata)
        self.assertIn('明細検算',[i['種類'] for i in draft['unresolved']])
        row=copy.deepcopy(rows[0]);row['原価金額']=9000
        self.assertIsNone(printed_rounding(row))
