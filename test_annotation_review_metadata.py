import copy
import hashlib
import tempfile
import unittest

from estimate_pipeline import normalize_summary_data
from source_amounts import _annotation_reviews
from streamlit.testing.v1 import AppTest
import test_source_review_ui as source_ui_tests


class AnnotationReviewMetadataTest(unittest.TestCase):
    def test_note_verification_without_money_survives_and_stays_original_scoped(self):
        data = b'original scan bytes'
        review = dict(source_name='scan.pdf', sha256=hashlib.sha256(data).hexdigest(),
                      status='needs_review', pages=[1, 2], checked_pages=[1], reasons=['incomplete_pages'])
        source = dict(__source_name='scan.pdf', __page_number=1, annotation_review=review)
        before = copy.deepcopy(source)
        metadata = normalize_summary_data([source])
        self.assertEqual(_annotation_reviews('scan.pdf', data, metadata), [review])
        self.assertEqual(_annotation_reviews('another.pdf', data, metadata), [])
        self.assertEqual(_annotation_reviews('scan.pdf', b'different original', metadata), [])
        self.assertEqual(source, before)
        self.assertIsNone(metadata['summary_sources'][0]['小計'])

    def test_annotation_failure_preserves_priced_workflow_and_human_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            script = source_ui_tests.SourceReviewUITest().reference_app(temp, with_rows=True)
            script = script.replace("'conflicts':[],'page':1", "'conflicts':[],'page':1,'annotation_reviews':[{'status':'needs_review'}]")
            app = AppTest.from_string(script).run()
            self.assertEqual(list(app.exception), [])
            self.assertTrue(any('欄外の注記は自動確認が終わっていません' in item.value for item in app.info))
            source = app.session_state['checked_source']
            checkbox = app.checkbox(key='review_source_ok_' + source['id'])
            self.assertFalse(checkbox.disabled)
            checkbox.check().run()
            self.assertTrue(app.session_state['checked_source']['confirmed'])
            self.assertEqual(app.session_state['checked_rows'][0]['原価金額'], 100000)

    def test_plain_language_exclusion_is_visible_before_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            script = source_ui_tests.SourceReviewUITest().reference_app(temp, with_rows=True)
            script = script.replace("'conflicts':[],'page':1", "'conflicts':[],'page':1,'source_notes':['2ページ：スロープ床は入っておりません']")
            app = AppTest.from_string(script).run()
            self.assertEqual(list(app.exception), [])
            self.assertTrue(any('工事範囲・除外条件' in item.value for item in app.info))
            self.assertFalse(app.session_state['checked_source']['confirmed'])


if __name__ == '__main__':
    unittest.main()
