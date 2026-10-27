import copy
import unittest
from source_sections import reconcile_sections
from source_rounding import printed_rounding
from estimate_pipeline import build_intermediate_dataframe
from estimate_review import review_rows,build_draft,Target,target_total
from markup_rules import line_amount


class SourceSectionsTest(unittest.TestCase):
    def test_cover_ledger_without_summary_items_needs_printed_heading_and_full_details(self):
        cover=dict(品名='外壁塗装工事費',数量=1,単位='式',単価=1000,金額=1000,
                   抽出元テキスト範囲='外壁塗装工事費 1式 1,000',__source_name='a.pdf',__page_number=1)
        rows=[cover,dict(品名='養生',数量=1,単位='式',単価=100,金額=100,__source_name='a.pdf',__page_number=1),
              dict(品名='壁面',数量=2,単位='㎡',単価=300,金額=600,__source_name='a.pdf',__page_number=2),
              dict(品名='屋根',数量=2,単位='㎡',単価=200,金額=400,__source_name='a.pdf',__page_number=2)]
        summary={'__source_name':'a.pdf','__page_number':1,'__page_role':'cover_summary_page','工事項目':[]}
        original=copy.deepcopy(rows)
        result=reconcile_sections(rows,[summary],'a.pdf')
        self.assertEqual([r['品名'] for r in result],['養生','壁面','屋根'])
        self.assertEqual(rows,original)
        for mutation in ('no_evidence','wrong_total','second_cover'):
            changed=copy.deepcopy(rows);summaries=[summary]
            if mutation=='no_evidence':changed[0]['抽出元テキスト範囲']=''
            if mutation=='wrong_total':changed[-1]['金額']=399
            if mutation=='second_cover':summaries.append({**summary,'__page_number':2})
            self.assertTrue(any(r['品名']=='外壁塗装工事費' for r in reconcile_sections(changed,summaries,'a.pdf')))

    def test_blank_discount_quantity_uses_only_its_own_explicit_negative_printed_line(self):
        row=dict(品名='調整値引き',数量=None,単位='',単価=None,金額=-500,
                 抽出元テキスト範囲='調整値引き -500',__source_name='a.pdf',__page_number=4)
        original=copy.deepcopy(row)
        out=reconcile_sections([row],[],'a.pdf')[0]
        self.assertEqual((out['数量'],out['単位'],out['単価'],out['金額']),(1,'式',-500,-500))
        self.assertEqual(row,original)
        for change in ({'品名':'返品資材'},{'金額':500},{'抽出元テキスト範囲':'調整値引き -499'},
                       {'__source_name':'other.pdf'},{'__page_number':None}):
            untouched=reconcile_sections([{**row,**change}],[],'a.pdf')[0]
            self.assertIsNone(untouched['数量'])

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

    def test_cover_fee_with_its_description_is_summary_only_once(self):
        row=dict(品名='養生 諸経費',仕様='安全対策費・法定福利費含む',数量=1,単位='式',単価=6000,金額=6000,
                 工事種別='諸経費',__source_name='a.pdf',__page_number=1)
        summary={'__source_name':'a.pdf','__page_number':1,'__page_role':'cover_summary_page',
                 '工事項目':[{'工事項目':'養生 諸経費（安全対策費・法定福利費含む）','金額':6000}]}
        result=reconcile_sections([row],[summary],'a.pdf')
        self.assertEqual(len(result),1)
        self.assertTrue(result[0]['__summary_only'])
        self.assertEqual(result[0]['金額'],6000)

    def test_printed_inclusive_total_repairs_a_net_gross_label_swap(self):
        from source_sections import reconcile_printed_totals
        s={'小計':106000,'端数調整':-6000,'消費税':10000,'工事費計':100000,
           '抽出元テキスト範囲':'小計106,000 / 調整値引き -6,000 / 合計100,000 / 税込：￥110,000'}
        fixed=reconcile_printed_totals(s)
        self.assertEqual((fixed['改小計'],fixed['工事費計']),(100000,110000))
        self.assertEqual(s['工事費計'],100000)
        for bad in ({**s,'消費税':9000},{**s,'端数調整':-5000},{**s,'抽出元テキスト範囲':'合計100,000'}):
            self.assertEqual(reconcile_printed_totals(bad),bad)

    def test_printed_discounted_net_without_inclusive_text_does_not_invent_gross(self):
        from source_sections import reconcile_printed_totals
        source={'小計':110000,'端数調整':-10000,'改小計':None,'消費税':10000,'工事費計':100000,
                '抽出元テキスト範囲':'小 計 110,000 調整値引き -10,000 合 計 100,000'}
        original=copy.deepcopy(source)
        fixed=reconcile_printed_totals(source)
        self.assertEqual(fixed['改小計'],100000)
        self.assertEqual(fixed['工事費計'],100000)
        self.assertEqual(source,original)
        for bad in ({**source,'端数調整':-9999},
                    {**source,'抽出元テキスト範囲':'小計110000 値引き10000 合計100000'},
                    {**source,'抽出元テキスト範囲':'小計110000 値引き-10000 合計100001'}):
            self.assertEqual(reconcile_printed_totals(bad),bad)

    def test_detail_summary_can_preserve_printed_net_and_normalize_discount_ledger(self):
        from source_sections import reconcile_printed_totals
        summary={'__source_name':'a.pdf','__page_number':1,'__page_role':'detail_page',
                 '小計':30000,'端数調整':-1000,'消費税':2900,'工事費計':31900,
                 '抽出元テキスト範囲':'小 計30,000 値 引 △1,000 合 計29,000 消 費 税2,900 合 計31,900'}
        fixed=reconcile_printed_totals(summary)
        self.assertEqual(fixed['改小計'],29000)
        rows=[dict(品名='本体',数量=1,単位='台',単価=30000,金額=30000,__source_name='a.pdf',__page_number=1),
              dict(品名='値引き',数量=None,単位='',単価=None,金額=-1000,
                   抽出元テキスト範囲='値 引 △1,000',__source_name='a.pdf',__page_number=1)]
        out=reconcile_sections(rows,[summary],'a.pdf')
        self.assertEqual(len(out),2)
        self.assertEqual((out[1]['数量'],out[1]['単位'],out[1]['単価']),(1,'式',-1000))
        self.assertEqual(sum(r['金額'] for r in out),29000)
        self.assertTrue(out[1]['__summary_only'])

    def multi_cover_fixture(self):
        def row(name,amount,page,**extra):
            return dict(品名=name,数量=1,単位='式',単価=amount,金額=amount,
                        見積元='確認用施工会社',__source_name='a.pdf',__page_number=page,**extra)
        rows=[row('工事一式',110000,1),row('値引き',-10000,1),
              row('本体',80000,2),row('施工',20000,2),row('荷揚げ',10000,3),
              row('工事一式',20000,4),row('別本体',15000,5),row('別施工',5000,5)]
        rows[1].update(数量=None,単位='',単価=None,抽出元テキスト範囲='調整値引き -10,000')
        summaries=[]
        for page,before,discount,after,tax in ((1,110000,-10000,100000,10000),(4,20000,-1000,19000,1900)):
            summaries.append({'__source_name':'a.pdf','__page_number':page,'__page_role':'cover_summary_page',
                '工事項目':[{'工事項目':'工事一式','金額':before}], '小計':before,'端数調整':discount,
                '改小計':None,'消費税':tax,'工事費計':after,'見積元':'確認用施工会社',
                '抽出元テキスト範囲':f'小計{before} 調整値引き {discount} 合計{after} 消費税{tax} 税込{after+tax}'})
        return rows,summaries

    def test_multiple_covers_bound_details_and_recover_only_printed_missing_discount(self):
        rows,summaries=self.multi_cover_fixture();original=copy.deepcopy(rows)
        out=reconcile_sections(rows,summaries,'a.pdf')
        self.assertEqual(rows,original)
        self.assertFalse(any(r['品名']=='工事一式' for r in out))
        self.assertEqual(len(out),7)
        self.assertEqual(sum(r['金額'] for r in out),119000)
        discounts=[r for r in out if r['金額']<0]
        self.assertEqual(sorted((r['__page_number'],r['金額']) for r in discounts),[(1,-10000),(4,-1000)])
        self.assertTrue(all(r['数量']==1 and r['単位']=='式' and r['単価']==r['金額'] for r in discounts))
        again=reconcile_sections(out,summaries,'a.pdf')
        self.assertEqual(again,out,'reconciliation must not duplicate an already recovered ledger entry')

    def test_next_cover_details_cannot_prove_previous_cover_aggregate(self):
        rows,summaries=self.multi_cover_fixture()
        rows[0].update(金額=130000,単価=130000)
        summaries[0]['工事項目'][0]['金額']=130000
        out=reconcile_sections(rows,summaries,'a.pdf')
        self.assertTrue(any(r['品名']=='工事一式' and r['__page_number']==1 for r in out))

    def test_unique_full_detail_pages_can_prove_broadly_classified_cover_headings(self):
        summary={'__source_name':'a.pdf','__page_number':1,'__page_role':'cover_summary_page',
                 '工事項目':[{'工事項目':'外壁改修工事費','金額':30000},
                              {'工事項目':'屋上改修工事','金額':40000}]}
        rows=[dict(品名='外壁改修工事',数量=1,単位='式',単価=30000,金額=30000,__source_name='a.pdf',__page_number=1),
              dict(品名='屋上改修工事',数量=1,単位='式',単価=40000,金額=40000,__source_name='a.pdf',__page_number=1)]
        for page,values in ((2,(10000,20000)),(3,(15000,25000))):
            for value in values:
                rows.append(dict(品名='原本にある施工',数量=1,単位='式',単価=value,金額=value,
                                 工事種別='改修工事',__source_name='a.pdf',__page_number=page))
        out=reconcile_sections(rows,[summary],'a.pdf')
        self.assertEqual(out,rows[2:])
        duplicate_page=[dict(r,__page_number=4) for r in rows[2:4]]
        ambiguous=reconcile_sections(rows+duplicate_page,[summary],'a.pdf')
        self.assertTrue(any(r['品名']=='外壁改修工事' for r in ambiguous))
        partial=[dict(rows[2],金額=30000,単価=30000)]+rows[3:]
        ambiguous=reconcile_sections(rows[:2]+partial,[summary],'a.pdf')
        self.assertTrue(any(r['品名']=='外壁改修工事' for r in ambiguous),
                        'a matching subset on a page is insufficient when the full page total differs')

    def test_missing_discount_is_never_filled_from_an_unverified_residual(self):
        for mutation in ('no_text','positive_text','wrong_before','wrong_discount','no_page','wrong_source'):
            rows,summaries=self.multi_cover_fixture();second=summaries[1]
            if mutation=='no_text':second['抽出元テキスト範囲']=''
            elif mutation=='positive_text':second['抽出元テキスト範囲']=second['抽出元テキスト範囲'].replace('-1000','1000')
            elif mutation=='wrong_before':second['小計']=20001
            elif mutation=='wrong_discount':second['端数調整']=-999
            elif mutation=='no_page':second['__page_number']=None
            elif mutation=='wrong_source':second['__source_name']='different.pdf'
            out=reconcile_sections(rows,summaries,'a.pdf')
            self.assertFalse(any(r['__page_number']==4 and r['金額']<0 for r in out),mutation)

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


class PrintedTotalLabelRegression(unittest.TestCase):
    def test_two_printed_totals_separated_by_discount_are_retained_without_inventing_a_fee(self):
        from source_sections import reconcile_printed_totals
        source={'小計':1100,'端数調整':-100,'工事費計':1000,'消費税':100,
                '抽出元テキスト範囲':'合計 1,100 / 調整値引き -100 / 合計 1,000 / 消費税 100'}
        result=reconcile_printed_totals(source)
        self.assertEqual(result['改小計'],1000)
        self.assertEqual(result['工事費計'],1000)
        self.assertNotIn('改小計',source)
