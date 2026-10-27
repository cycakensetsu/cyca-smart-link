import copy
import unittest
from streamlit.testing.v1 import AppTest
from item_correction_ui import apply_correction
from test_source_spec_history import snapshot


class ItemCorrectionTest(unittest.TestCase):
    def fields(self,row):
        return {k:row.get(k) for k in ('品名','仕様','数量','単位','原価単価','原価金額','部位','ページ','見積元','訂正理由')}

    def test_only_current_item_changes_and_approvals_are_invalidated_without_touching_originals(self):
        raw=snapshot();rows=raw['rows'];other=copy.deepcopy(rows[0]);other['行ID']='other'
        state={'_review_original_rows':copy.deepcopy(rows+[other]),
               '_review_assistant_rows':copy.deepcopy(rows+[other]),'_review_approval':{'old':True},
               '_review_result':'old output','review_source_ok_'+rows[0]['原本ID']:True}
        before=copy.deepcopy(state['_review_original_rows'])
        fields=self.fields(rows[0]);fields.update(仕様='原本を確認した仕様',訂正理由='原本4ページで確認')
        apply_correction(state,rows,rows[0]['行ID'],fields)
        self.assertEqual(state['_review_original_rows'],before)
        self.assertEqual(state['_review_assistant_rows'][1],other)
        self.assertEqual(state['_review_assistant_rows'][0]['仕様'],fields['仕様'])
        self.assertNotIn('_review_approval',state)
        self.assertNotIn('_review_result',state)
        self.assertFalse(state['review_source_ok_'+rows[0]['原本ID']])

    def test_reason_missing_foreign_id_and_protected_fields_rejected_without_mutating_state(self):
        rows=snapshot()['rows'];fields=self.fields(rows[0]);fields['訂正理由']='原本確認'
        for changes,key in (({**fields,'訂正理由':''},rows[0]['行ID']),
                            (fields,'foreign'),({**fields,'原本ID':'changed'},rows[0]['行ID'])):
            state={'_review_original_rows':copy.deepcopy(rows)};before=copy.deepcopy(state)
            with self.assertRaises(ValueError):apply_correction(state,rows,key,changes)
            self.assertEqual(state,before)

    def test_form_keeps_unknown_numbers_unknown_and_requires_reason(self):
        raw=snapshot();raw['rows'][0]['原価単価']=''
        app=f'''
import streamlit as st
from item_correction_ui import render_item_correction
if '_review_original_rows' not in st.session_state:
    st.session_state['_review_original_rows']={raw['rows']!r}
rows=st.session_state.get('_review_assistant_rows',st.session_state['_review_original_rows'])
render_item_correction(st,rows,{raw['sources'][0]['id']!r})
'''
        at=AppTest.from_string(app).run()
        self.assertEqual(list(at.exception),[])
        self.assertIsNone(next(n for n in at.number_input if n.label=='仕入単価').value)
        at.button[0].click().run()
        self.assertTrue(any('理由' in e.value for e in at.error))
        next(t for t in at.text_area if t.label=='仕様・寸法').input('原本どおりの仕様')
        next(t for t in at.text_input if t.label=='直した理由（必須）').input('原本4ページを確認')
        at.button[0].click().run()
        self.assertEqual(list(at.exception),[])
        self.assertEqual(at.session_state['_review_assistant_rows'][0]['仕様'],'原本どおりの仕様')
        self.assertIsNone(at.session_state['_review_assistant_rows'][0]['原価単価'])


if __name__=='__main__':unittest.main()
