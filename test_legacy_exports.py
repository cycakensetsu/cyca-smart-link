import unittest
from streamlit.testing.v1 import AppTest


class LegacyExportsTest(unittest.TestCase):
    def test_review_checkbox_does_not_discard_download_on_rerun(self):
        app='''
import streamlit as st
from legacy_exports import queue_draft_export, render_draft_exports
value=st.number_input('input',value=1,key='value')
signature=(value,)
if st.button('generate',key='generate'):
    queue_draft_export(st,signature,label='CSV',data=b'synthetic',file_name='test.csv',mime='text/csv',key='csv')
render_draft_exports(st,signature)
'''
        at=AppTest.from_string(app).run()
        at.button(key='generate').click().run()
        self.assertEqual(len(at.checkbox),1)
        at.checkbox[0].check().run()
        self.assertTrue(at.checkbox[0].value)
        self.assertEqual(at.session_state['_legacy_exports']['csv']['data'],b'synthetic')
        self.assertEqual(len(at.get('download_button')),1)
        at.number_input(key='value').set_value(2).run()
        self.assertEqual(len(at.get('download_button')),0)
        self.assertEqual(list(at.exception),[])
