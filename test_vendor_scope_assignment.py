import copy
import unittest

from estimate_pipeline import assign_unknown_vendors_to_pdf_vendor


class VendorScopeAssignmentTest(unittest.TestCase):
    def summary(self,page,vendor,**more):
        return dict(__source_name='原本　A.pdf',__page_number=page,
                    __page_role='cover_summary_page',見積元=vendor,**more)

    def row(self,page,**more):
        return dict(__source_name='原本　A.pdf',__page_number=page,見積元='不明',
                    品名='原本品目',工事種別='',数量=1,単位='式',単価=100,金額=100,**more)

    def test_two_printed_issuers_are_assigned_only_within_their_quote_pages(self):
        summaries=[self.summary(1,'確認用施工A'),self.summary(4,'確認用施工B')]
        rows=[self.row(p) for p in (1,2,3,4,5)]
        original=copy.deepcopy(rows)
        result,_=assign_unknown_vendors_to_pdf_vendor(rows,summaries)
        self.assertEqual([r['見積元'] for r in result],['確認用施工A']*3+['確認用施工B']*2)
        self.assertEqual([r['__assigned_vendor_scope_page'] for r in result],[1]*3+[4]*2)
        self.assertEqual(rows,original)

    def test_verified_summary_can_start_a_scope_but_an_intermediate_heading_cannot(self):
        later=self.summary(4,'確認用施工B')
        later['__page_role']='summary_page'
        later['__quote_scope_verified']=True
        fake=self.summary(2,'途中見出しを会社と誤読')
        fake['__page_role']='detail_page'
        result,_=assign_unknown_vendors_to_pdf_vendor([self.row(3),self.row(5)],
                                                    [self.summary(1,'確認用施工A'),fake,later])
        self.assertEqual([r['見積元'] for r in result],['確認用施工A','確認用施工B'])
        later['__quote_scope_verified']=False
        result,_=assign_unknown_vendors_to_pdf_vendor([self.row(5)],
                                                    [self.summary(1,'確認用施工A'),later])
        self.assertEqual(result[0]['見積元'],'確認用施工A')
        self.assertEqual(result[0]['__assigned_vendor_scope_page'],1)

    def test_missing_or_conflicting_new_scope_does_not_inherit_another_issuer(self):
        for later in ([self.summary(4,'不明')],
                      [self.summary(4,'確認用施工B'),self.summary(4,'確認用施工C')]):
            result,_=assign_unknown_vendors_to_pdf_vendor([self.row(5)],
                                                        [self.summary(1,'確認用施工A')]+later)
            self.assertEqual(result[0]['見積元'],'不明')
        result,_=assign_unknown_vendors_to_pdf_vendor([self.row(None)],
                            [self.summary(1,'確認用施工A'),self.summary(4,'確認用施工B')])
        self.assertEqual(result[0]['見積元'],'不明')

    def test_exact_source_identity_and_existing_known_issuer_are_preserved(self):
        row=self.row(2);row['見積元']='原本に記載の既知施工者'
        other=self.row(2);other['__source_name']='原本 A.pdf'
        result,_=assign_unknown_vendors_to_pdf_vendor([row,other],[self.summary(1,'確認用施工A')])
        self.assertEqual(result[0]['見積元'],row['見積元'])
        self.assertEqual(result[1]['見積元'],'不明')

    def test_a_filename_is_never_manufactured_into_an_issuer(self):
        summary=self.summary(1,'');summary.pop('見積元')
        result,_=assign_unknown_vendors_to_pdf_vendor([self.row(1)],[summary])
        self.assertEqual(result[0]['見積元'],'不明')

    def test_only_unique_original_heading_present_in_every_item_can_fill_trade(self):
        summary=self.summary(1,'確認用施工A',工事項目=[{'工事項目':'原文作業区分'}])
        rows=[self.row(1),self.row(1)]
        rows[0].update(品名='原文作業区分 A',工事種別='未分類')
        rows[1].update(品名='原文作業区分 B',工事種別='既存の工種')
        result,_=assign_unknown_vendors_to_pdf_vendor(rows,[summary])
        self.assertEqual(result[0]['工事種別'],'原文作業区分')
        self.assertTrue(result[0]['__assigned_trade_from_summary'])
        self.assertEqual(result[1]['工事種別'],'既存の工種')
        for mutation in ('unrelated_item','two_headings','other_source'):
            ss=copy.deepcopy(summary);rr=copy.deepcopy(rows)
            if mutation=='unrelated_item':rr[1]['品名']='異なる原文作業'
            elif mutation=='two_headings':ss['工事項目'].append({'工事項目':'別区分'})
            else:ss['__source_name']='原本 A.pdf'
            out,_=assign_unknown_vendors_to_pdf_vendor(rr,[ss])
            self.assertEqual(out[0]['工事種別'],'未分類',mutation)

    def test_trade_heading_from_another_quote_scope_cannot_cross_page_boundary(self):
        first=self.summary(1,'確認用施工A',工事項目=[{'工事項目':'区分A'}])
        later=self.summary(4,'確認用施工B',工事項目=[{'工事項目':'区分B'}])
        row=self.row(5);row['品名']='区分A 原文品目'
        result,_=assign_unknown_vendors_to_pdf_vendor([row],[first,later])
        self.assertEqual(result[0]['工事種別'],'')


if __name__=='__main__':
    unittest.main()
