from pathlib import Path
import copy
import tempfile
import unittest
import unicodedata
from html.parser import HTMLParser
import fitz
from streamlit.testing.v1 import AppTest


class SourceReviewUITest(unittest.TestCase):
    def test_original_exclusions_are_visible_without_becoming_customer_remarks(self):
        with tempfile.TemporaryDirectory() as temp:
            script = self.reference_app(temp, with_rows=True)
            script = script.replace("'conflicts':[],'page':1", "'conflicts':[],'page':1,'source_notes':['1ページ：既存設備の撤去は含まない','1ページ：原本の連絡事項']")
            at = AppTest.from_string(script).run()
            self.assertEqual(list(at.exception), [])
            self.assertTrue(any('工事範囲・除外条件' in info.value for info in at.info))
            self.assertTrue(any('既存設備の撤去は含まない' in item.value for item in at.markdown))
            rows = at.session_state['checked_rows']
            self.assertFalse(any(r.get('顧客備考') for r in rows))
            self.assertEqual(rows[0]['原価金額'], 100000)

    def test_vendor_ignores_only_current_filename_fallback_without_changing_data(self):
        from source_review_ui import source_vendor
        source = {'id': 'synthetic', 'name': '検証ガーデン　御見積書.pdf'}
        fallback = unicodedata.normalize('NFD', '検証ガーデン 御見積書.pdf')
        rows = [{'原本ID': 'synthetic', '見積元': '(有)架空塗装'},
                {'原本ID': 'another', '見積元': '別原本の会社'}]
        metadata = {'summary_sources': [
            {'元ファイル': source['name'], '見積元': '(有)架空塗装'},
            {'元ファイル': source['name'], '見積元': fallback},
            {'元ファイル': 'another.pdf', '見積元': '別原本の会社'}]}
        before = copy.deepcopy((source, metadata, rows))
        self.assertEqual(source_vendor(source, metadata, rows), '(有)架空塗装')
        self.assertEqual((source, metadata, rows), before)

    def test_filename_without_issuer_is_not_displayed_as_a_company(self):
        from source_review_ui import source_vendor
        source = {'id': 'synthetic', 'name': '架空資料 Ａ.pdf'}
        rows = [{'原本ID': 'synthetic', '見積元': '架空資料A.pdf'}]
        metadata = {'summary_sources': [{'元ファイル': source['name'], '見積元': source['name']}]}
        self.assertEqual(source_vendor(source, metadata, rows), '業者名の記載なし')

    def test_distinct_issuer_candidates_and_other_filenames_still_need_confirmation(self):
        from source_review_ui import source_vendor
        source = {'id': 'synthetic', 'name': 'original.pdf'}
        rows = [{'原本ID': 'synthetic', '見積元': '(有)架空塗装'}]
        for other in ('有限会社架空塗装', '(株)架空塗装', '(有)別会社', 'other.pdf'):
            with self.subTest(other=other):
                metadata = {'summary_sources': [{'元ファイル': source['name'], '見積元': other}]}
                self.assertEqual(source_vendor(source, metadata, rows), '業者名を確認')

    def reference_app(self, temp, *, with_rows=False):
        return f'''
import streamlit as st
from estimate_review import OriginalVault
from source_review_ui import render_source
store=OriginalVault({str(Path(temp)/'vault')!r},'reference-original-test')
source=store.preserve('design.pdf',b'synthetic reference attachment')
source.update(pages=1, read_errors=[{{'page':1,'reason':'no priced items'}}],
              duplicate_count=3, duplicate_names=['design.pdf','design-copy.pdf','same-design.pdf'],
              reference_only=True, reference_confirmed=True)
if {with_rows!r}: source['read_errors']=[]
sid=source['id']
st.session_state['_review_amount_readings']={{sid:{{'net_total':{100000 if with_rows else None!r},'tax':{10000 if with_rows else None!r},'gross_total':{110000 if with_rows else None!r},'conflicts':[],'page':1}}}}
rows={"[dict(行ID='priced',原本ID=sid,品名='原本記載の工事',数量=1,単位='式',原価単価=100000,原価金額=100000,ページ=1)]" if with_rows else '[]'}
st.session_state['checked_rows']=render_source(st,source,store,1,rows)
st.session_state['checked_source']=source
'''

    def test_empty_original_needs_explicit_reference_choice_and_can_be_unchecked(self):
        with tempfile.TemporaryDirectory() as temp:
            at=AppTest.from_string(self.reference_app(temp)).run()
            self.assertEqual(list(at.exception),[])
            source=at.session_state['checked_source'];sid=source['id']
            self.assertFalse(source['reference_only'])
            self.assertFalse(source['reference_confirmed'])
            self.assertFalse(source['confirmed'])
            self.assertEqual(len(at.number_input),0)
            at.checkbox(key='review_reference_only_'+sid).check().run()
            source=at.session_state['checked_source']
            self.assertTrue(source['reference_only'])
            self.assertTrue(source['reference_confirmed'])
            self.assertTrue(source['confirmed'])
            self.assertEqual(source['read_errors'],[{'page':1,'reason':'no priced items'}])
            self.assertEqual(at.session_state['checked_rows'],[])
            self.assertFalse(any('品目を読み取れません' in w.value for w in at.warning))
            from estimate_review import OriginalVault
            self.assertEqual(OriginalVault(Path(temp)/'vault','reference-original-test').read(sid),b'synthetic reference attachment')
            at.checkbox(key='review_reference_only_'+sid).uncheck().run()
            self.assertFalse(at.session_state['checked_source']['confirmed'])
            self.assertFalse(at.session_state['checked_source']['reference_only'])
            self.assertFalse(at.session_state['checked_source']['reference_confirmed'])

    def test_priced_original_cannot_be_hidden_as_reference_and_duplicate_notice_is_plain(self):
        with tempfile.TemporaryDirectory() as temp:
            at=AppTest.from_string(self.reference_app(temp,with_rows=True)).run()
            self.assertEqual(list(at.exception),[])
            source=at.session_state['checked_source']
            self.assertFalse(source['reference_only'])
            self.assertFalse(source['reference_confirmed'])
            self.assertEqual(len(at.session_state['checked_rows']),1)
            self.assertNotIn('review_reference_only_'+source['id'],[c.key for c in at.checkbox])
            self.assertTrue(any('同じ原本が3回選ばれています。内容は1件として表示しています。' == i.value for i in at.info))
            # A duplicated priced original still needs its ordinary human check.
            self.assertFalse(source['confirmed'])
            self.assertFalse(at.checkbox(key='review_source_ok_'+source['id']).disabled)
            at.checkbox(key='review_source_ok_'+source['id']).check().run()
            self.assertTrue(at.session_state['checked_source']['confirmed'])

    def test_unknown_unit_price_is_blank_in_editor_without_becoming_zero_or_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            app=self.reference_app(temp,with_rows=True).replace('原価単価=100000','原価単価=None')
            at=AppTest.from_string(app).run()
            self.assertEqual(list(at.exception),[])
            self.assertTrue(at.dataframe[0].value['原価単価'].isna().all())
            self.assertEqual(at.session_state['checked_rows'][0]['原価単価'],'')
            self.assertEqual(at.session_state['checked_rows'][0]['原価金額'],100000)
            source=at.session_state['checked_source']
            self.assertNotIn('review_reference_only_'+source['id'],[c.key for c in at.checkbox])
            self.assertTrue(at.checkbox(key='review_source_ok_'+source['id']).disabled)

    def test_page_preview_changes_without_mutating_original_or_confirming_it(self):
        with tempfile.TemporaryDirectory() as temp:
            document=fitz.open()
            for number in range(2):
                page=document.new_page();page.insert_text((30,40),f'SYNTHETIC PAGE {number+1}')
            original=document.tobytes();document.close()
            path=Path(temp)/'source.pdf';path.write_bytes(original)
            app=f'''
import streamlit as st
from estimate_review import OriginalVault
from source_review_ui import render_source
store=OriginalVault({str(Path(temp)/'vault')!r},'synthetic-test')
source=store.preserve('source.pdf',open({str(path)!r},'rb').read())
source['pages']=2
render_source(st,source,store,1)
st.session_state['checked_source']=source
'''
            at=AppTest.from_string(app).run()
            self.assertFalse(at.session_state['checked_source']['confirmed'])
            self.assertEqual(at.session_state['checked_source']['tax_basis'],'税抜')
            self.assertEqual(len(at.session_state['_review_source_previews']),1)
            sid=at.session_state['checked_source']['id']
            self.assertTrue(at.button(key='review_source_page_'+sid+'_previous').disabled)
            at.button(key='review_source_page_'+sid+'_next').click().run()
            self.assertEqual(at.session_state['review_source_page_'+sid],2)
            self.assertTrue(at.button(key='review_source_page_'+sid+'_next').disabled)
            self.assertEqual(list(at.exception),[])
            self.assertEqual(len(at.session_state['_review_source_previews']),2)
            from estimate_review import OriginalVault
            self.assertEqual(OriginalVault(Path(temp)/'vault','synthetic-test').read(sid),original)

    def test_explicit_large_original_has_independent_navigation_and_does_not_approve(self):
        with tempfile.TemporaryDirectory() as temp:
            document=fitz.open()
            for number in range(3):
                document.new_page().insert_text((30,40),f'SYNTHETIC PAGE {number+1}')
            original=document.tobytes();document.close()
            path=Path(temp)/'source.pdf';path.write_bytes(original)
            at=AppTest.from_string(f'''
import streamlit as st
from estimate_review import OriginalVault
from source_review_ui import render_source
store=OriginalVault({str(Path(temp)/'vault')!r},'large-original-test')
source=store.preserve('source.pdf',__import__('pathlib').Path({str(path)!r}).read_bytes())
source['pages']=3
render_source(st,source,store,1)
st.session_state['checked_source']=source
''').run()
            sid=at.session_state['checked_source']['id']
            at.button(key='review_source_page_'+sid+'_next').click().run()
            at.button(key='review_enlarge_'+sid).click().run()
            self.assertEqual(list(at.exception),[])
            self.assertEqual(at.session_state['review_source_large_page_'+sid],2)
            at.button(key='review_source_large_page_'+sid+'_next').click().run()
            self.assertEqual(list(at.exception),[])
            self.assertEqual(at.session_state['review_source_large_page_'+sid],3)
            self.assertEqual(at.session_state['review_source_page_'+sid],2)
            # AppTest reruns the full app when a fragment button is clicked, so
            # reopen the dialog to inspect its final-page controls.
            at.button(key='review_enlarge_'+sid).click().run()
            self.assertTrue(at.button(key='review_source_large_page_'+sid+'_next').disabled)
            self.assertFalse(at.session_state['checked_source']['confirmed'])
            self.assertTrue(any('3ページ目 / 全3ページ' in m.value for m in at.markdown))
            from estimate_review import OriginalVault
            self.assertEqual(OriginalVault(Path(temp)/'vault','large-original-test').read(sid),original)

    def test_long_items_and_discount_have_complete_numeric_cells_and_safe_text(self):
        from source_review_ui import _items_html
        class Cells(HTMLParser):
            def __init__(self):
                super().__init__();self.cells=[];self.current=None
            def handle_starttag(self,tag,attrs):
                if tag=='td': self.current=[]
            def handle_data(self,data):
                if self.current is not None:self.current.append(data)
            def handle_endtag(self,tag):
                if tag=='td':self.cells.append(''.join(self.current));self.current=None
        name='高性能空調機の本体・既設撤去および取付工事' * 3
        specification='品番TEST-A-123456789 / 室内外機セット <原本の記載> '+('施工条件の詳細。' * 8)
        rows=[{'品名':name,'部位':'屋上・東側','工事種別':'空調工事','仕様':specification,
               '数量':2.5,'単位':'台','原価単価':12345678,'原価金額':30864195},
              {'品名':'原本記載の値引き','仕様':'価格調整','数量':1,'単位':'式','原価単価':-1234,'原価金額':-1234}]
        parser=Cells();parser.feed(_items_html(rows))
        self.assertEqual(len(parser.cells),8)
        self.assertIn(name,parser.cells[0])
        self.assertIn(specification,parser.cells[0])
        self.assertIn('屋上・東側',parser.cells[0])
        self.assertEqual(parser.cells[1:4],['2.5 台','12,345,678円','30,864,195円'])
        self.assertEqual(parser.cells[5:8],['1 式','-1,234円','-1,234円'])
        # Larger imports retain every row in their original order, without a hidden
        # editor, summary-only preview, or arbitrary short-list limit.
        parser=Cells();parser.feed(_items_html(rows*35))
        self.assertEqual(len(parser.cells),280)
        self.assertIn('70. 原本記載の値引き',parser.cells[-4])
