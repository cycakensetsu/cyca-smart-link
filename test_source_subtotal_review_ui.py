"""Isolated app tests for choosing an internal provisional estimate."""
import tempfile
import unittest

from streamlit.testing.v1 import AppTest


class SubtotalReviewUI(unittest.TestCase):
    def app(self, temp, status='variance', amount=12000, *, manual_scope=None):
        return f"""
from unittest.mock import patch
import os
os.environ['CYCA_DATA_DIR'] = {temp!r}
import streamlit as st
from review_ui import vault, render_review
from estimate_pipeline import build_intermediate_dataframe
from estimate_review import review_rows

name = 'sample-quote.pdf'
st.session_state['username'] = 'internal-reviewer'
source = vault(st).preserve(name, b'example-only PDF')
source['pages'] = 2
sid = source['id']
records = [
   dict(品名=label, 仕様='架空資料', 数量=1, 単位='式', 単価=yen, 金額=yen,
        見積元='架空会社', 工事種別='配管工事', __source_name=name,__page_number=2)
   for label, yen in [('資材搬入', {amount!r}), ('配管修理',27000), ('片付け',8000),
                            ('仮設工事',29000), ('作業費',47000), ('諸経費',27000)]
   if yen
]
if '_review_original_rows' not in st.session_state:
    st.session_state['username'] = 'internal-reviewer'
    st.session_state['_review_sources'] = [source]
    detail, _ = build_intermediate_dataframe(records)
    st.session_state['_review_original_rows'] = review_rows(detail, [source])
    st.session_state['_review_metadata'] = {{'工事名称': '帳票確認用'}}
st.session_state['_review_amount_readings'] = {{sid: {{
    'net_total':138000,'tax':13800,'gross_total':151800,'conflicts':[],'page':1,
    'subtotal_checks':[{{'status':{status!r},'page':2,'printed_subtotal':111000,
        'computed_subtotal':123000 if {status!r}=='variance' else None,
        'difference':12000 if {status!r}=='variance' else None,'scope':'page'}}]
}}}}
if {manual_scope is not None!r}:
    # Pure OCR case where the only range is an entire detail page.
    st.session_state['_review_amount_readings'][sid]['subtotal_checks'] = [{{
        'status':'unverified','page':2,'scope':{manual_scope!r},'section':'','method':'ocr',
        'printed_subtotal':138000,'computed_subtotal':138000,'difference':0,
        'printed_evidence':'ページ小計：138,000円',
        'row_evidence':[dict(name=row['品名'],amount=row['金額'],page=2,
            evidence=f"{{row['品名']}} 1 式 {{row['金額']}}") for row in records],
    }}]
with patch('source_review_ui.preview'):
    render_review(st)
"""

    def test_retry_or_explicit_high_choice_before_margin_and_formal_output_stays_off(self):
        with tempfile.TemporaryDirectory() as temp:
            at = AppTest.from_string(self.app(temp)).run()
            self.assertEqual(list(at.exception), [])
            self.assertTrue(any('2ページ目：明細の合計 123,000円 ／ 見積書の小計 111,000円 ／ 差 12,000円' in w.value for w in at.warning))
            self.assertNotIn('上乗せする利益（円）', [n.label for n in at.number_input])
            reason = next(t for t in at.text_input if t.label == '原本で仕様・注記を確認した内容と理由')
            reason.input('架空原本の仕様・注記・頁・明細を見比べました。').run()
            key = next(c.key for c in at.checkbox if c.label == '150,000円で仮見積を作る')
            self.assertNotIn('上乗せする利益（円）', [n.label for n in at.number_input])
            at.checkbox(key=key).check().run()
            self.assertEqual(list(at.exception), [])
            self.assertIn('上乗せする利益（円）', [n.label for n in at.number_input])
            self.assertIn('仮見積を見る（社内確認用）', [b.label for b in at.button])
            self.assertNotIn('review_approve', [b.key for b in at.button])
            self.assertEqual(len(at.get('download_button')), 0)
            self.assertTrue(any('仮見積（社内確認用）' in w.value for w in at.warning))
            at.text_input(key='review_customer').input('架空のお客様 御中').run()
            self.assertFalse(at.button(key='review_preview').disabled)
            at.button(key='review_preview').click().run()
            self.assertEqual(list(at.exception), [])
            self.assertFalse(at.error)
            self.assertGreater(len(at.session_state['_review_preview']['images']), 0)
            self.assertNotIn('review_approve', [b.key for b in at.button])
            self.assertEqual(len(at.get('download_button')), 0)
            self.assertFalse(any('このページ分けでPDFを作成' in m.value for m in at.markdown))
            at.checkbox(key=key).uncheck().run()
            self.assertNotIn('上乗せする利益（円）', [n.label for n in at.number_input])

    def test_possible_missing_row_is_not_a_verified_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            at = AppTest.from_string(self.app(temp, status='unverified', amount=0)).run()
            self.assertEqual(list(at.exception), [])
            self.assertTrue(any('抜けや重複がないか' in w.value for w in at.warning))
            self.assertFalse(any('仮見積' in c.label for c in at.checkbox))
            self.assertNotIn('上乗せする利益（円）', [n.label for n in at.number_input])
            sid = at.session_state['_review_sources'][0]['id']
            self.assertTrue(at.checkbox(key='review_source_ok_'+sid).disabled)

    def test_equal_ocr_subtotal_requires_current_explicit_visual_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            at = AppTest.from_string(self.app(temp, status='unverified', amount=0, manual_scope='page')).run()
            self.assertEqual(list(at.exception), [])
            sid = at.session_state['_review_sources'][0]['id']
            key = 'review_source_ok_' + sid
            self.assertTrue(at.checkbox(key=key).disabled)
            reason = next(t for t in at.text_input if t.label == '原本で仕様・注記を確認した内容と理由')
            reason.input('架空原本の小計と全品目、仕様、注記、ページを照合済み。').run()
            decision = next(c for c in at.checkbox if c.label == '原本の小計と全品目を見比べました')
            decision.check().run()
            self.assertFalse(at.checkbox(key=key).disabled)
            at.checkbox(key=key).check().run()
            self.assertIn('review_approve', [b.key for b in at.button])
            stale = [dict(row) for row in at.session_state['_review_assistant_rows']]
            stale[0]['原価金額'] = 99000
            at.session_state['_review_assistant_rows'] = stale
            at.run()
            self.assertTrue(at.checkbox(key=key).disabled)
            self.assertNotIn('review_approve', [b.key for b in at.button])

    def test_equal_digits_but_ambiguous_scope_cannot_be_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            at = AppTest.from_string(self.app(temp, status='unverified', amount=0, manual_scope='unknown')).run()
            self.assertEqual(list(at.exception), [])
            reason = next(t for t in at.text_input if t.label == '原本で仕様・注記を確認した内容と理由')
            reason.input('架空原本を一通り見比べました。').run()
            self.assertFalse(any(c.label == '原本の小計と全品目を見比べました' for c in at.checkbox))
            sid = at.session_state['_review_sources'][0]['id']
            self.assertTrue(at.checkbox(key='review_source_ok_'+sid).disabled)

    def test_beginner_uses_current_preview_and_one_consent_before_save_is_offered(self):
        with tempfile.TemporaryDirectory() as temp:
            script = self.app(temp, status='matched', amount=0)
            script = script.replace("with patch('source_review_ui.preview'):", "st.session_state['_review_beginner_guided'] = True\nwith patch('source_review_ui.preview'):")
            at = AppTest.from_string(script).run()
            sid = at.session_state['_review_sources'][0]['id']
            next(t for t in at.text_input if t.label == '原本で仕様・注記を確認した内容と理由').input('架空原本の品目と注記を原本で確認。').run()
            at.checkbox(key='review_source_ok_'+sid).check().run()
            self.assertTrue(any('いくら上乗せしますか？' in m.value for m in at.markdown))
            self.assertNotIn('はい、PDFを作る', [b.label for b in at.button])
            self.assertFalse(at.get('download_button'))
            amount = next(n for n in at.number_input if n.label == '上乗せする金額（円）')
            amount.set_value(10000).run()
            at.text_input(key='review_customer').input('架空のお客様 御中').run()
            at.button(key='review_preview').click().run()
            self.assertEqual(list(at.exception), [])
            self.assertFalse(at.get('download_button'))
            self.assertFalse(any('これでOKです' in c.label for c in at.checkbox))
            next(b for b in at.button if b.label == 'はい、PDFを作る').click().run()
            self.assertEqual(list(at.exception), [])
            self.assertFalse(at.error)
            from pymupdf import open as open_pdf
            with open_pdf(stream=at.session_state['_review_result'].pdf_data, filetype='pdf') as pdf:
                self.assertGreater(pdf.page_count, 0)
                self.assertTrue(any('162,800' in page.get_text() for page in pdf))
            labels = [node.proto.label for node in at.get('download_button')]
            self.assertEqual(labels, ['PDFを保存', 'Numbers用を保存', '社内承認記録を保存'])
            self.assertTrue(any(e.label == 'ほかの形式・社内記録（必要なときだけ）' for e in at.expander))
            next(n for n in at.number_input if n.label == '上乗せする金額（円）').set_value(20000).run()
            self.assertNotIn('はい、PDFを作る', [b.label for b in at.button])
            self.assertFalse(at.get('download_button'))


if __name__ == '__main__':
    unittest.main()
