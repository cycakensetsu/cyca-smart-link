import unittest
from tempfile import TemporaryDirectory
from streamlit.testing.v1 import AppTest

class CoverReviewUI(unittest.TestCase):
    def test_mismatch_rows_are_actionable_without_horizontal_table_and_choices_are_explicit(self):
        with TemporaryDirectory() as temp:
            app=f'''
import streamlit as st
from test_cover_double_counting import fixture,original_pdf,NAME
from estimate_review import OriginalVault,review_rows
from estimate_pipeline import build_intermediate_dataframe,normalize_summary_data
from source_review_ui import render_source
store=OriginalVault({temp!r},'fictional')
data=st.session_state.setdefault('fixture_pdf',original_pdf())
source=store.preserve(NAME,data);source['pages']=2
records,summaries=fixture()
frame,_=build_intermediate_dataframe(records)
st.session_state.setdefault('_review_original_rows',review_rows(frame,[source]))
st.session_state['_review_metadata']=normalize_summary_data(summaries)
rows=st.session_state.get('_review_assistant_rows',st.session_state['_review_original_rows'])
updated=render_source(st,source,store,1,rows)
st.session_state['_review_assistant_rows']=updated
st.session_state['source_result']=source
'''
            at=AppTest.from_string(app).run(timeout=20)
            self.assertFalse(at.exception)
            source=at.session_state['source_result'];sid=source['id']
            self.assertTrue(any('差 500,000円' in x.value for x in at.markdown))
            apply=at.button(key='review_exception_apply_'+sid)
            self.assertTrue(apply.disabled)
            self.assertTrue(any('理由を入れると反映できます' in x.value for x in at.caption))
            original=at.session_state['_review_original_rows']
            for name in ('補修作業 A','補修作業 B'):
                row=next(r for r in original if r['品名']==name)
                at.selectbox(key='review_exception_row_'+sid).select(row['行ID']).run()
                at.radio(key='review_exception_choice_'+sid+'_'+row['行ID']).set_value('表紙の集計なので明細には含めない').run()
                at.text_input(key='review_exception_reason_'+sid+'_'+row['行ID']).set_value('架空原本の表紙と2ページを見比べ、作業区分の集計と確認').run()
                at.button(key='review_exception_apply_'+sid).click().run()
                self.assertFalse(at.exception)
            rows=at.session_state['_review_assistant_rows']
            self.assertEqual(sum(r['原価金額'] for r in rows if r['採用']),545000)
            self.assertEqual(sum(r['採用'] for r in rows),15)
            self.assertEqual(len(rows),17,'excluded source rows remain as audit records')
            for row in rows:
                if not row['採用']:self.assertTrue(row['除外理由'])
            reason=next(t for t in at.text_input if t.label=='原本で仕様・注記を確認した内容と理由')
            reason.set_value('架空原本の全品目・仕様・注記と表紙集計の役割を確認').run()
            self.assertFalse(at.checkbox(key='review_source_ok_'+sid).disabled)
            at.checkbox(key='review_source_ok_'+sid).check().run()
            self.assertTrue(at.session_state['source_result']['confirmed'])
    def test_inclusion_choice_does_not_apply_without_source_and_reason(self):
        from item_correction_ui import apply_inclusion
        row={'行ID':'a','原本ID':'s','採用':True}
        state={'_review_original_rows':[row]}
        with self.assertRaises(ValueError):apply_inclusion(state,[row],'a',False,'')
        with self.assertRaises(ValueError):apply_inclusion(state,[row],'other',False,'原本照合')
        self.assertTrue(state['_review_original_rows'][0]['採用'])

    def test_empty_and_all_excluded_rows_do_not_crash_exception_card(self):
        for rows in ([], [dict(行ID='fictional',品名='架空の集計',原価金額=500,採用=False,除外理由='原本で集計と照合')]):
            with self.subTest(rows=rows):
                app=f"""
import streamlit as st
from source_review_ui import _source_exception_card
_source_exception_card(st, {rows!r}, dict(id='fictional',net_total=500))
"""
                at=AppTest.from_string(app).run()
                self.assertFalse(at.exception)
                self.assertEqual(len(at.selectbox), 1 if rows else 0)
                if rows:
                    self.assertTrue(any('品目の合計 0円' in x.value for x in at.markdown))
