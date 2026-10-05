"""New uploads must discard the previous document's overrides and output."""
from pathlib import Path
import unittest
from streamlit.testing.v1 import AppTest


class UploadStateTest(unittest.TestCase):
    def test_both_entrypoints_clear_previous_titles_and_keep_current_edits(self):
        for name in ('extract_data.py', 'extract_data_cloud.py'):
            with self.subTest(entrypoint=name):
                at = AppTest.from_file(str(Path(__file__).parent / name))
                at.secrets['GOOGLE_API_KEY'] = 'verification-only-placeholder'
                at.session_state['logged_in'] = True
                at.session_state['username'] = 'verification'
                at.run(timeout=30)
                at.text_input(key='output_customer').set_value('前の宛名 様')
                at.text_input(key='output_project').set_value('前の工事')
                at.run(timeout=30)
                self.assertEqual(at.text_input(key='output_customer').value, '前の宛名 様')
                at.session_state['_upload_signature'] = (('previous.pdf', 1, 'old'),)
                at.session_state['_horizontal_result'] = 'old-file'
                at.run(timeout=30)
                self.assertEqual(list(at.exception), [])
                self.assertEqual(at.text_input(key='output_customer').value, '')
                self.assertEqual(at.text_input(key='output_project').value, '')
                self.assertNotIn('_horizontal_result', at.session_state)
                at.text_input(key='output_customer').set_value('今回の宛名 様').run(timeout=30)
                self.assertEqual(at.text_input(key='output_customer').value, '今回の宛名 様')


if __name__ == '__main__':
    unittest.main()
