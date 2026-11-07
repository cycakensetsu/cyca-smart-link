"""Exercise first edits through real Streamlit widgets; never bypass review."""
import copy
import tempfile
import unittest

from streamlit.testing.v1 import AppTest

from test_unpriced_reference import unpriced_review_app


class ReviewEditorStabilityTest(unittest.TestCase):
    def test_missing_optional_text_does_not_change_editor_identity_on_first_rerun(self):
        for reference_first in (False, True):
            with self.subTest(reference_first=reference_first), tempfile.TemporaryDirectory() as folder:
                at = AppTest.from_string(unpriced_review_app(folder, reference_first)).run()
                self.assertEqual(list(at.exception), [])
                self.assertNotIn('備考', at.session_state['_review_original_rows'][0])
                editors = {d.key: (d.proto.id, d.value.dtypes.astype(str).to_dict()) for d in at.dataframe}
                self.assertEqual(len(editors), 2)
                at.run()
                self.assertEqual(list(at.exception), [])
                self.assertEqual(editors, {d.key: (d.proto.id, d.value.dtypes.astype(str).to_dict()) for d in at.dataframe})

    def test_first_edit_is_retained_and_reopens_source_comparison_without_reusing_old_reason(self):
        with tempfile.TemporaryDirectory() as folder:
            at = AppTest.from_string(unpriced_review_app(folder, reference_first=True)).run()
            source = next(s for s in at.session_state['_review_sources'] if s['name']=='quote.pdf')
            sid = source['id']
            raw = copy.deepcopy(at.session_state['_review_original_rows'])
            key = 'review_source_items_'+sid+'_0'
            # Commit the first actual table edit, without a warm-up rerun.
            at.session_state[key] = {'edited_rows': {0: {'備考':'原本の行注記', '訂正理由':'合成原本の該当行を照合'}},
                                     'added_rows': [], 'deleted_rows': []}
            at.run()
            self.assertEqual(list(at.exception), [])
            row = at.session_state['_review_assistant_rows'][0]
            self.assertEqual(row['備考'], '原本の行注記')
            self.assertEqual(row['訂正理由'], '合成原本の該当行を照合')
            reason = next(t for t in at.text_input if t.label=='原本で仕様・注記を確認した内容と理由')
            old_reason_key = reason.key
            self.assertTrue(at.checkbox(key='review_source_ok_'+sid).disabled)
            reason.input('合成原本1ページの仕様Aとこの行の注記を照合済み').run()
            self.assertFalse(at.checkbox(key='review_source_ok_'+sid).disabled)
            self.assertEqual(len(at.warning), 0)
            at.checkbox(key='review_source_ok_'+sid).check().run()
            self.assertTrue(at.checkbox(key='review_source_ok_'+sid).value)
            # A later specification edit requires a new, bound comparison.
            # The old editor delta includes the earlier committed note/reason.
            at.session_state[key] = {'edited_rows': {0: {'仕様':'原本どおりの仕様B', '備考':'原本の行注記',
                                                        '訂正理由':'合成原本の仕様Bを再照合'}},
                                     'added_rows': [], 'deleted_rows': []}
            at.run()
            self.assertEqual(list(at.exception), [])
            reason = next(t for t in at.text_input if t.label=='原本で仕様・注記を確認した内容と理由')
            self.assertNotEqual(reason.key, old_reason_key)
            self.assertEqual(reason.value, '')
            self.assertTrue(at.checkbox(key='review_source_ok_'+sid).disabled)
            self.assertFalse(at.checkbox(key='review_source_ok_'+sid).value)
            self.assertEqual(len(at.warning), 1)
            self.assertEqual(at.session_state['_review_assistant_rows'][0]['仕様'], '原本どおりの仕様B')
            self.assertEqual(at.session_state['_review_original_rows'], raw)
            at.run()
            self.assertEqual(at.session_state['_review_assistant_rows'][0]['仕様'], '原本どおりの仕様B')
            self.assertEqual(at.session_state['_review_assistant_rows'][0]['備考'], '原本の行注記')


if __name__ == '__main__':
    unittest.main()
