import copy
import unittest
from extraction_batch import reconcile_document_results
from source_sections import reconcile_financial_pages


def row(name, amount, page, **extra):
    return dict(品名=name,数量=1,単位='式',単価=amount,金額=amount,
                __source_name='sample.pdf',__page_number=page,
                抽出元テキスト範囲=f'{name} 1 式 '+('▲' if amount<0 else '')+str(abs(amount)),**extra)


class FinancialPageRepairTest(unittest.TestCase):
    def test_missing_rate_for_printed_single_location_uses_same_line_amount_only(self):
        from source_sections import reconcile_sections
        original=row('コンセント本体 設置費込み',2500,1)
        original.update(単位='ヶ所',単価=None,抽出元テキスト範囲='コンセント本体 設置費込み 1ヶ所 2,500')
        fixed=reconcile_sections([original],[],'sample.pdf')
        self.assertEqual(fixed[0]['単価'],2500)
        self.assertIsNone(original['単価'])
        for change in ({'数量':2},{'単位':''},{'抽出元テキスト範囲':'コンセント本体 2,500'},{'__source_name':'other.pdf'}):
            self.assertIsNone(reconcile_sections([{**original,**change}],[],'sample.pdf')[0]['単価'])

    def fixture(self):
        rows=[row('製品代計',1100,1),row('出精値引',-100,1),
              row('本体',800,2),row('取付',300,2),row('出精値引',-100,2),
              row('御見積総金額',1000,3)]
        rows[-1].update(数量=None,単位='',単価=None,抽出元テキスト範囲='御見積総金額 1,000')
        summaries=[{'__source_name':'sample.pdf','__page_number':1,'__page_role':'cover_summary_page',
                    '改小計':1000,'消費税':100,'工事費計':1100,
                    '工事項目':[{'工事項目':'製品代計','金額':1100},{'工事項目':'出精値引','金額':-100}]}]
        return rows,summaries

    def test_repeated_cover_subtotal_discount_and_standalone_total_do_not_become_three_quotes(self):
        rows,summaries=self.fixture();original=copy.deepcopy((rows,summaries))
        out,ss=reconcile_document_results([{'name':'sample.pdf','pages':3}],rows,summaries)
        self.assertEqual(out,rows[2:5])
        self.assertEqual(sum(r['金額'] for r in out),1000)
        self.assertEqual((rows,summaries),original)
        again,_=reconcile_document_results([{'name':'sample.pdf','pages':3}],out,ss)
        self.assertEqual(again,out)

    def test_missing_proof_wrong_total_and_partial_matching_body_keep_ambiguous_rows(self):
        for kind in ('positive_discount','missing_discount_proof','wrong_net','extra_detail','wrong_source'):
            rows,ss=self.fixture()
            if kind=='positive_discount':rows[1]['抽出元テキスト範囲']='出精値引 1式100'
            if kind=='missing_discount_proof':rows[4]['抽出元テキスト範囲']=''
            if kind=='wrong_net':ss[0]['改小計']=999
            if kind=='extra_detail':rows.append(row('別の明細',1,2))
            if kind=='wrong_source':rows[4]['__source_name']='another.pdf'
            out,_=reconcile_document_results([{'name':'sample.pdf','pages':3}],rows,ss)
            self.assertTrue(any(r['品名']=='製品代計' for r in out),kind)

    def test_total_with_real_quantity_or_unrelated_quote_is_never_discarded(self):
        rows,ss=self.fixture();rows[-1].update(数量=1,単位='式',単価=1000)
        out,_=reconcile_financial_pages(rows,ss,'sample.pdf')
        self.assertIn(rows[-1],out)
        rows,ss=self.fixture();ss.append({'__source_name':'sample.pdf','__page_number':2,
            '__page_role':'cover_summary_page','改小計':999,'消費税':99,'工事費計':1098})
        out,_=reconcile_financial_pages(rows,ss,'sample.pdf')
        self.assertIn(rows[-1],out)

    def garage_fixture(self):
        rows=[row('車庫外部塗装工事費',900,4),row('養生 諸経費',54,4),row('値引き',-4,4),
              row('壁面',150,5),row('屋根',750,5)]
        ss=[{'__source_name':'sample.pdf','__page_number':4,'__page_role':'detail_page',
             'ページ種別':'detail_page','改小計':950,'消費税':95,'工事費計':1045,'工事項目':[]}]
        return rows,ss

    def test_separately_printed_cover_is_recognized_even_when_ai_calls_it_detail(self):
        rows,ss=self.garage_fixture()
        out,changed=reconcile_document_results([{'name':'sample.pdf','pages':5}],rows,ss)
        self.assertEqual(sum(r['金額'] for r in out),950)
        self.assertEqual(len(out),4)
        self.assertEqual(changed[0]['__page_role'],'cover_summary_page')
        self.assertEqual(changed[0]['ページ種別'],'cover_summary_page')
        self.assertEqual(ss[0]['__page_role'],'detail_page')

    def test_incomplete_page_or_next_independent_cover_cannot_prove_aggregate(self):
        for kind in ('no_evidence','wrong_tax','unknown_amount','extra_row','next_cover'):
            rows,ss=self.garage_fixture()
            if kind=='no_evidence':rows[0]['抽出元テキスト範囲']=''
            if kind=='wrong_tax':ss[0]['消費税']=94
            if kind=='unknown_amount':rows[-1]['金額']=None
            if kind=='extra_row':rows.append(row('運搬',1,5))
            if kind=='next_cover':ss.append({'__source_name':'sample.pdf','__page_number':5,'__page_role':'cover_summary_page'})
            out,changed=reconcile_financial_pages(rows,ss,'sample.pdf')
            self.assertEqual(changed[0]['__page_role'],'detail_page',kind)
            self.assertEqual(out,rows,kind)


if __name__=='__main__':unittest.main()
