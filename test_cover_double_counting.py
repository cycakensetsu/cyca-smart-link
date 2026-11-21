"""Fictional cover/body reconciliation: never identify work by price alone."""
import copy
import unittest
from extraction_batch import reconcile_document_results

NAME='fictional.pdf'
ITEMS=[('仮設養生',1,'式',50000,50000),('既存材撤去',20,'m',3000,60000),
       ('下地調整',25,'m',2000,50000),('副資材',10,'個',2000,20000),
       ('防水材施工',25,'m',4000,100000),('点検記録作成',1,'式',40000,40000),
       ('搬入運搬',1,'式',25000,25000),('部品取付',8,'個',5000,40000),
       ('仕上げ調整',10,'m',4500,45000),('清掃作業',1,'式',20000,20000),
       ('廃材処分',1,'式',30000,30000),('完了点検',1,'式',20000,20000)]

def fixture():
    def row(name,q,u,p,a,page):
        return dict(品名=name,仕様='',数量=q,単位=u,単価=p,金額=a,見積元='架空業者',
            工事種別='補修工事',部位='',__source_name=NAME,__page_number=page,
            抽出元テキスト範囲=f'{name} {q}{u} {p:,} {a:,}')
    rows=[row(n,1,'式',a,a,1) for n,a in [('補修作業 A',320000),('補修作業 B',180000),
          ('諸経費',30000),('法定福利費',20000),('端数調整',-5000)]]
    rows += [row(*values,2) for values in ITEMS]
    summary=dict(__source_name=NAME,__page_number=1,__page_role='cover_summary_page',
        改小計=545000,消費税=54500,工事費計=599500,工事名称='架空検証',
        工事項目=[dict(工事項目=n,金額=a) for n,a in [('補修作業 A',320000),('補修作業 B',180000),('諸経費',30000),('法定福利費',20000),('端数調整',-5000)]],
        抽出元テキスト範囲='補修作業A320,000 補修作業B180,000 諸経費30,000 法定福利費20,000 端数調整-5,000 税抜合計545,000 消費税54,500 税込合計599,500')
    return rows,[summary]

def original_pdf(*, unrelated=False, unlisted=False, tail_unit=None, tail_fee=False):
    import pymupdf
    pdf=pymupdf.open(); cover=pdf.new_page()
    def text(page,y,value):page.insert_text((30,y),value,fontsize=10,fontname='japan')
    for i,line in enumerate(['御見積書','補修作業 A 320,000','補修作業 B 180,000','諸経費 30,000',
             '法定福利費 20,000','端数調整 -5,000','税抜合計 545,000','消費税（10%）54,500','税込合計 599,500']):
        text(cover,40+24*i,line)
    body=pdf.new_page();text(body,40,'工事項目 数量 単位 単価 金額')
    y=64
    for i,(n,q,u,p,a) in enumerate(ITEMS):
        if i in (0,6):text(body,y,'別の工事' if unrelated else ('補修作業 A' if i==0 else '補修作業 B'));y+=24
        text(body,y,f'{n} {q} {u} {p:,} {a:,}');y+=24
    if unlisted:text(body,y,'未読の本物工事 1 式 100 100');y+=24
    text(body,y,'作業代小計（税抜）500,000')
    if tail_unit is not None:
        text(body,y+24,f'追加の施工 1 {tail_unit} 100 100')
    if tail_fee:
        text(body,y+24,'追加費用 100')
    return pdf.tobytes()

class CoverDoubleCounting(unittest.TestCase):
    def reconcile(self,rows,summaries,data):
        return reconcile_document_results([dict(name=NAME,pages=2)],rows,summaries,original_reader=lambda _:data)
    def test_complete_native_sections_remove_two_aggregates_keep_fifteen_real_lines(self):
        rows,summaries=fixture(); original=copy.deepcopy((rows,summaries))
        output,summary=self.reconcile(rows,summaries,original_pdf())
        self.assertEqual(len(output),15)
        self.assertEqual(sum(r['金額'] for r in output),545000)
        self.assertEqual({r['品名']:r['金額'] for r in output[:3]},dict(諸経費=30000,法定福利費=20000,端数調整=-5000))
        self.assertIn('完了点検',[r['品名'] for r in output])
        self.assertEqual([r['部位'] for r in output[3:9]],['補修作業 A']*6)
        self.assertEqual([r['部位'] for r in output[9:]],['補修作業 B']*6)
        self.assertEqual((rows,summaries),original)
        check=summary[0]['__subtotal_checks'][-1]
        self.assertEqual((check['status'],check['printed_subtotal'],len(check['row_evidence'])),('matched',500000,12))
    def test_price_coincidence_or_incomplete_original_never_discards_work(self):
        rows,summaries=fixture()
        for data in (original_pdf(unrelated=True),original_pdf(unlisted=True),b'not pdf'):
            output,_=self.reconcile(rows,summaries,data)
            self.assertEqual(len(output),17)
    def test_wrong_row_amount_missing_page_or_duplicate_row_never_proves_aggregate(self):
        for change in ('amount','page','duplicate','missing','other_source'):
            rows,summaries=fixture()
            if change=='amount':rows[-1]['金額']=19999
            if change=='page':rows[-1]['__page_number']=1
            if change=='duplicate':rows.append(dict(rows[-1]))
            if change=='missing':rows.pop()
            if change=='other_source':rows[-1]['__source_name']='other.pdf'
            output,_=self.reconcile(rows,summaries,original_pdf())
            self.assertTrue(any(r['品名']=='補修作業 A' for r in output))
    def test_original_lump_sum_with_same_amount_remains(self):
        rows,summaries=fixture()
        # These real one-lump-sum detail lines share the price, not identity.
        output,_=self.reconcile(rows,summaries,original_pdf())
        self.assertEqual([(r['品名'],r['金額']) for r in output if r['金額']==20000],
                         [('法定福利費',20000),('副資材',20000),('清掃作業',20000),('完了点検',20000)])

    def test_unread_tail_rows_with_any_unit_keep_aggregates_for_review(self):
        rows,summaries=fixture()
        for unit in ('組','回','箱','%','m/日','袋','人','°'):
            with self.subTest(unit=unit):
                output,_=self.reconcile(rows,summaries,original_pdf(tail_unit=unit))
                self.assertEqual(len(output),17)

    def test_unread_amount_only_fee_after_subtotal_is_not_ignored(self):
        rows,summaries=fixture()
        output,_=self.reconcile(rows,summaries,original_pdf(tail_fee=True))
        self.assertEqual(len(output),17)
