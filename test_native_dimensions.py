import copy
from pathlib import Path
import tempfile
import unittest
import fitz
from source_table import attach_native_dimensions


class NativeDimensionTest(unittest.TestCase):
    def document(self, folder, *, labelled=True):
        path=Path(folder)/'window.pdf'
        with fitz.open() as doc:
            page=doc.new_page(width=600,height=800)
            if labelled:page.insert_text((250,90),'寸法(mm)',fontname='japan',fontsize=8)
            for x,t in ((252,'W'),(275,'H')):page.insert_text((x,104),t,fontsize=8)
            for x,t in ((250,'601'),(272,'1302'),(332,'2'),(390,'100'),(450,'200')):
                page.insert_text((x,140),t,fontsize=8)
            page.insert_text((50,200),'障子部',fontname='japan',fontsize=8)
            for x,t in ((250,'503'),(272,'1190')):page.insert_text((x,200),t,fontsize=8)
            doc.save(path)
        return path

    def rows(self):
        return [dict(品名='検証窓',仕様='ブラック・既存の採寸メモを保持',数量=2,単価=100,金額=200,
                     __source_name='window.pdf',__page_number=1)]

    def test_native_product_and_sash_dimensions_keep_existing_spec_and_money(self):
        with tempfile.TemporaryDirectory() as temp:
            rows=self.rows();original=copy.deepcopy(rows)
            result=attach_native_dimensions(str(self.document(temp)),rows)
            self.assertEqual(rows,original)
            self.assertIn('W601×H1302mm',result[0]['仕様'])
            self.assertIn('障子部 W503×H1190mm',result[0]['仕様'])
            self.assertTrue(result[0]['仕様'].startswith(original[0]['仕様']))
            self.assertEqual(result[0]['金額'],200)
            self.assertEqual(attach_native_dimensions(str(Path(temp)/'window.pdf'),result),result)

    def test_ambiguous_or_changed_price_does_not_attach_dimensions(self):
        with tempfile.TemporaryDirectory() as temp:
            path=str(self.document(temp));rows=self.rows()
            for changed in ([{**rows[0],'金額':201}],rows+copy.deepcopy(rows),
                            rows+[{**rows[0],'__source_name':'other.pdf'}]):
                self.assertEqual(attach_native_dimensions(path,changed),changed)

    def test_unlabelled_columns_are_not_interpreted_as_dimensions(self):
        with tempfile.TemporaryDirectory() as temp:
            rows=self.rows()
            self.assertEqual(attach_native_dimensions(str(self.document(temp,labelled=False)),rows),rows)

    def test_blank_unpriced_sash_continuation_is_retained_inside_native_priced_row(self):
        with tempfile.TemporaryDirectory() as temp:
            source=self.document(temp)
            path=Path(temp)/'component.pdf'
            with fitz.open(source) as doc:
                doc[0].insert_text((95,200),'検証ガラス3A透明',fontname='japan',fontsize=8)
                doc.save(path)
            child=dict(品名='障子部 検証ガラス3A透明',仕様='原本の枠寸法メモ',No='',数量='',単位='',単価='',金額='',
                       抽出元テキスト範囲='障子部 検証ガラス3A透明',__source_name='window.pdf',__page_number=1)
            rows=self.rows()+[child];original=copy.deepcopy(rows)
            out=attach_native_dimensions(str(path),rows)
            self.assertEqual(rows,original)
            self.assertEqual(len(out),1)
            self.assertEqual(out[0]['金額'],200)
            self.assertIn('障子部 検証ガラス3A透明',out[0]['仕様'])
            self.assertIn('原本の枠寸法メモ',out[0]['仕様'])
            self.assertEqual(attach_native_dimensions(str(path),out),out)
            for changes in ({'No':2},{'金額':0},{'品名':'障子部 原本にない別部材'},{'__page_number':2}):
                kept=attach_native_dimensions(str(path),self.rows()+[{**child,**changes}])
                self.assertEqual(len(kept),2)
