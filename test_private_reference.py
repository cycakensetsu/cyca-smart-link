"""Keep private reference PDFs outside Git; opt in with CYCA_REFERENCE_DIR."""
from io import BytesIO
import os
import json
from pathlib import Path
import unicodedata
import unittest

import fitz
from openpyxl import load_workbook

from cyca_reference import read_reference_quote, reference_estimate
from reference_layout import build_reference_outputs, SUMMARY_SHEET


@unittest.skipUnless(os.environ.get('CYCA_REFERENCE_CASES'), 'Private source PDFs are intentionally not stored in Git.')
class PrivateReferenceRegressionTest(unittest.TestCase):
    def check_reference(self, case_id):
        cases=json.loads(Path(os.environ['CYCA_REFERENCE_CASES']).read_text())
        case=cases[case_id]
        path=Path(case['path'])
        count,expected_pages=case['rows'],case['pages']
        quote=read_reference_quote(path)
        self.assertEqual(len(quote.detail),count)
        estimate=reference_estimate(quote,'回帰検証用',{'屋上防水工事':'防水工事','塔屋防水工事':'防水工事'})
        xlsx,pdf,names=build_reference_outputs(estimate,draft=True)
        self.assertEqual(len(names),expected_pages)
        wb=load_workbook(BytesIO(xlsx))
        actual=[]
        for sheet in list(wb)[2:]:
            for row in range(3,29):
                if isinstance(sheet.cell(row,1).value,int):
                    actual.append([sheet.cell(row,col).value if sheet.cell(row,col).value is not None else '' for col in (1,2,3,4,5,6,8)])
        expected=[[r[k] for k in ('No','品名','仕様','数量','単位','単価','備考')] for r in quote.detail]
        self.assertEqual(actual,expected)
        self.assertEqual(wb['見積書']['D16'].value,f"='{SUMMARY_SHEET}'!H31")
        with fitz.open(stream=pdf,filetype='pdf') as doc:
            self.assertEqual(len(doc),expected_pages)
            for page in doc:
                self.assertAlmostEqual(page.rect.width,841.89,places=1)
                self.assertAlmostEqual(page.rect.height,595.28,places=1)

    def test_private_case_1(self):
        self.check_reference('case_1')

    def test_private_case_2(self):
        self.check_reference('case_2')

    def test_private_case_3(self):
        self.check_reference('case_3')
