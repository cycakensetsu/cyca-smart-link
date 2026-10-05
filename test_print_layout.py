import unittest
from io import BytesIO
import fitz
from openpyxl import load_workbook
from template_fill_test_v2 import Estimate, Category, LineItem, fill_estimate_v2

class PrintLayoutTest(unittest.TestCase):
    def estimate(self, count=14):
        return Estimate(vendor='検証工務店', customer='塚本 様', project_name='今回の工事', issue_date='2026/10/05', valid_days='30日', categories=[Category('内装工事', [LineItem(f'工事{i}', '', 2, '㎡', 5000, 10000, '') for i in range(count)])])

    def test_print_pages_and_honorific_match_pdf(self):
        result=fill_estimate_v2(self.estimate(), save_to_disk=False)
        self.assertTrue(result.ok,result.errors)
        wb=load_workbook(BytesIO(result.data))
        self.assertEqual(wb.sheetnames,['見積書','工事品目集計','明細1'])
        self.assertEqual(wb['見積書']['D5'].value,'様')
        self.assertEqual(wb['見積書']['C13'].value,"='工事品目集計'!G26")
        for sheet in wb:
            self.assertEqual(sheet.sheet_state,'visible')
            self.assertEqual(sheet.page_setup.fitToWidth,1)
            self.assertEqual(sheet.page_setup.fitToHeight,1)
            self.assertEqual(sheet.page_setup.orientation,'landscape')
            self.assertTrue(sheet.print_area)
        pdf=fitz.open(stream=result.pdf_data,filetype='pdf')
        self.assertEqual(len(pdf),3)
        for page in pdf:
            self.assertAlmostEqual(page.rect.width,841.89,places=1)
            self.assertAlmostEqual(page.rect.height,595.28,places=1)
        self.assertIn('塚本',pdf[0].get_text())
        self.assertNotIn('工 事 内 容 明 細',pdf[1].get_text())
        self.assertIn('工事13',pdf[2].get_text())

    def test_multi_page_subtotal_keeps_cross_sheet_references(self):
        result=fill_estimate_v2(self.estimate(81),save_to_disk=False)
        self.assertTrue(result.ok,result.errors)
        wb=load_workbook(BytesIO(result.data))
        self.assertEqual(wb.sheetnames,['見積書','工事品目集計']+[f'明細{i}' for i in range(1,6)])
        self.assertIn("'明細5'!",wb['工事品目集計']['G3'].value)
        formulas=[c.value for sheet in wb for row in sheet for c in row if c.data_type=='f']
        self.assertFalse(any('_layout_source' in f or '明細データ' in f for f in formulas))
        pdf=fitz.open(stream=result.pdf_data,filetype='pdf')
        self.assertEqual(len(pdf),7)
        self.assertIn('工事80',pdf[-1].get_text())
        self.assertNotIn('工事80',pdf[-2].get_text())

if __name__=='__main__': unittest.main()
