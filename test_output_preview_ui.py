import tempfile
import unittest
import test_conversation_ui as conversation_tests


class PreviewUITest(unittest.TestCase):
    def test_changed_page_break_requires_new_preview_and_new_consent(self):
        with tempfile.TemporaryDirectory() as temp:
            at=conversation_tests.ConversationUITest().checked_app(temp)
            self.assertTrue(at.button(key='review_approve').disabled)
            at.button(key='review_preview').click().run()
            self.assertEqual(list(at.exception),[])
            self.assertEqual(len(at.session_state['_review_preview']['pages']),2)
            self.assertNotIn('_review_approval',at.session_state)
            next(c for c in at.checkbox if c.key.startswith('review_agree_')).check().run()
            self.assertFalse(at.button(key='review_approve').disabled)
            row_id=at.session_state['_review_original_rows'][0]['行ID']
            at.multiselect(key='review_page_breaks').set_value([row_id]).run()
            self.assertTrue(at.button(key='review_approve').disabled)
            at.button(key='review_preview').click().run()
            self.assertEqual(list(at.exception),[])
            self.assertEqual(len(at.session_state['_review_preview']['pages']),3)
            self.assertTrue(at.button(key='review_approve').disabled)

    def test_impossible_page_does_not_create_preview_or_official_file(self):
        with tempfile.TemporaryDirectory() as temp:
            at=conversation_tests.ConversationUITest().checked_app(temp)
            row_id=at.session_state['_review_original_rows'][0]['行ID']
            at.session_state['review_page_breaks']=[row_id]
            at.session_state['_review_requested_pages']={row_id:4}
            at.run()
            at.button(key='review_preview').click().run()
            self.assertEqual(list(at.exception),[])
            self.assertNotIn('_review_preview',at.session_state)
            self.assertNotIn('_review_approval',at.session_state)
            self.assertTrue(at.button(key='review_approve').disabled)
            self.assertTrue(any('指定' in e.value for e in at.error))


if __name__=='__main__':unittest.main()
