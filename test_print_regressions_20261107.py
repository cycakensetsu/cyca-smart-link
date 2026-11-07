"""Real failures reduced to synthetic fixtures, so CI needs no private PDFs."""
import copy
from io import BytesIO
import unittest

import fitz
from openpyxl import load_workbook

from print_text_height import TABLE_WIDTHS
from template_fill_test_v2 import Estimate,Category,LineItem,fill_estimate_v2,TEMPLATE_PATH


class PrintRegressionTest(unittest.TestCase):
    def test_decimal_and_large_quantities_print_in_full_at_nine_points(self):
        quantities=(1210.7,2037.2,.125,100000)
        prices=(100,100,8000,2)
        amounts=(121070,203720,1000,200000)
        items=[LineItem(f'合成明細{i}','原本の数量を保持',q,'㎡',p,a) for i,(q,p,a) in enumerate(zip(quantities,prices,amounts))]
        for pour in (False,True):
            with self.subTest(pour_layout=pour):
                estimate=Estimate('', '検証用・提出不可','数量印刷の回帰検証','2026/11/07','30日',
                                  [Category('防水工事',items)],pour_layout=pour,source_count=1)
                result=fill_estimate_v2(estimate,save_to_disk=False)
                self.assertTrue(result.ok,result.errors)
                with fitz.open(stream=result.pdf_data,filetype='pdf') as pdf:
                    text='\n'.join(p.get_text() for p in pdf)
                    for expected in ('1,210.7','2,037.2','0.125','100,000'):
                        self.assertIn(expected,text)
                    for page in list(pdf)[2 if not pour else 1:]:
                        for block in page.get_text('dict')['blocks']:
                            for line in block.get('lines',[]):
                                for span in line['spans']:
                                    if span['text'] in ('1,210.7','2,037.2','0.125','100,000'):
                                        self.assertAlmostEqual(span['size'],9)
                                        self.assertGreaterEqual(span['bbox'][0],22+sum(TABLE_WIDTHS[:3])-.01)
                                        self.assertLessEqual(span['bbox'][2],22+sum(TABLE_WIDTHS[:4])+.01)
                wb=load_workbook(BytesIO(result.data))
                printed=[(ws.cell(row,4).value,ws.cell(row,6).value) for ws in wb if ws.title.startswith('明細')
                         for row in range(3,23) if isinstance(ws.cell(row,1).value,int)]
                self.assertEqual(printed,list(zip(quantities,prices)))
                self.assertEqual(result.summary['小計'],525790)

    def test_legacy_long_text_reflows_without_mutating_source_or_losing_items(self):
        spec='原本仕様・下地処理と指定材料を省略しない。'*5
        items=[LineItem(f'合成長文{i}',spec,2,'㎡',100,200) for i in range(10)]
        estimate=Estimate('', '検証用・提出不可','旧形式の長文回帰検証','2026/11/07','30日',
                          [Category('防水工事',items)])
        original=copy.deepcopy(estimate);template_before=TEMPLATE_PATH.read_bytes()
        result=fill_estimate_v2(estimate,save_to_disk=False)
        self.assertTrue(result.ok,result.errors)
        self.assertEqual(estimate,original)
        self.assertEqual(TEMPLATE_PATH.read_bytes(),template_before)
        wb=load_workbook(BytesIO(result.data))
        names=[ws.cell(row,2).value for ws in wb if ws.title.startswith('明細') for row in range(3,23)
               if isinstance(ws.cell(row,1).value,int)]
        self.assertEqual(names,[i.name for i in items])
        self.assertGreater(len(wb.sheetnames),3)
        with fitz.open(stream=result.pdf_data,filetype='pdf') as pdf:
            self.assertEqual(len(pdf),len(wb.sheetnames))
            text=''.join(p.get_text() for p in pdf)
            self.assertEqual(''.join(text.split()).count(spec),10)
            for page in pdf:
                for block in page.get_text('dict')['blocks']:
                    for line in block.get('lines',[]):
                        for span in line['spans']:
                            self.assertGreaterEqual(span['bbox'][1],0)
                            self.assertLessEqual(span['bbox'][3],page.rect.height)


if __name__=='__main__':unittest.main()
