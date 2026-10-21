from pathlib import Path
import tempfile
import unittest
import fitz
from streamlit.testing.v1 import AppTest


class SourceReviewUITest(unittest.TestCase):
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
