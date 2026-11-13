import tempfile
from pathlib import Path
import unittest
from streamlit.testing.v1 import AppTest

class InvoiceUITests(unittest.TestCase):
    def test_preview_final_and_unconfigured_gmail(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp).chmod(0o700)
            script='''
import streamlit as st
import json
from test_invoice_documents import fixture
from invoice_workflow_ui import render_invoice_workflow
class Store:
    def entries(self):
        value=json.dumps(fixture()).encode()
        return [('approvals/test.json',len(value),lambda:value)]
st.session_state['username']='owner'
render_invoice_workflow(st,Store(),fixture()['id'])
'''
            # Stable approval time is required across reruns for a stable source ID.
            script=script.replace("from test_invoice_documents import fixture", "from test_invoice_documents import fixture as make_fixture\n@st.cache_data\ndef fixture(): return make_fixture()")
            at=AppTest.from_string(script)
            at.secrets['invoice_workflow']={'enabled':True,'database_path':str(Path(temp)/'db'),'persistent_storage_verified':True,'issuer':{'name':'架空会社'}}
            at.run()
            self.assertEqual(list(at.exception),[])
            at.text_input[0].set_value('TEST-1');at.button[0].click();at.run()
            self.assertEqual(list(at.exception),[])
            at.checkbox[0].check();at.run()
            [b for b in at.button if b.label=='請求書を確定してPDF保存'][0].click();at.run()
            self.assertEqual(list(at.exception),[])
            self.assertTrue(any(t.label=='宛先メールアドレス' for t in at.text_input))
            self.assertTrue(Path(temp,'db').is_file())

if __name__=='__main__':unittest.main()
