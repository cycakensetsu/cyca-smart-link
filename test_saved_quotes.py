from pathlib import Path
import tempfile
import unittest
from streamlit.testing.v1 import AppTest
from estimate_review import OriginalVault, approve, digest
from saved_quotes import completed_quotes


def seed(root):
    store=OriginalVault(root,'owner')
    payload={'表題':{'工事名称':'架空の完成済み工事'},'確認明細':[],'合計':{'税込':220}}
    record=approve({'unresolved':[],'fingerprint':digest(payload),'payload':payload},'owner')
    store.snapshot('approvals',record)
    store.export(record,'pdf',b'synthetic saved PDF')
    store.export(record,'xlsx',b'synthetic saved XLSX')
    return store,record


class CompletedQuoteUITest(unittest.TestCase):
    def test_opens_existing_outputs_without_generation_and_does_not_show_another_owner_cache(self):
        with tempfile.TemporaryDirectory() as root:
            store,record=seed(root)
            source=f'''
import streamlit as st
from estimate_review import OriginalVault
from saved_quotes import render_saved_quotes
st.session_state.setdefault('fixture_owner','owner')
if st.button('別の利用者へ切替'):
    st.session_state['fixture_owner']='other'
render_saved_quotes(st,OriginalVault({root!r},st.session_state['fixture_owner']))
'''
            at=AppTest.from_string(source).run()
            self.assertFalse(at.exception)
            self.assertEqual(at.selectbox[0].options[0].split(' ｜ ')[1],'架空の完成済み工事')
            next(b for b in at.button if b.label=='この完成見積を開く').click().run()
            self.assertFalse(at.exception)
            ready=at.session_state['_saved_quote_files']
            self.assertEqual(ready,(str(store.root),record['id'],{'pdf':b'synthetic saved PDF','xlsx':b'synthetic saved XLSX'}))
            self.assertIn('保存した完成ファイルを開けます。',[v.value for v in at.success])
            next(b for b in at.button if b.label=='別の利用者へ切替').click().run()
            self.assertFalse(at.exception)
            self.assertEqual(list(at.success),[])
            self.assertEqual(list(at.selectbox),[])

    def test_changed_receipt_is_not_accepted_as_approved_completed_output(self):
        with tempfile.TemporaryDirectory() as root:
            store,record=seed(root)
            record['payload']['表題']['工事名称']='改変された表題'
            store.snapshot('approvals',record)
            self.assertEqual(len(completed_quotes(store)),1)
            self.assertEqual(completed_quotes(store)[0]['title'],'架空の完成済み工事')

    def test_unavailable_storage_is_displayed_as_failure_not_empty_success(self):
        at=AppTest.from_string('''
import streamlit as st
from saved_quotes import render_saved_quotes
class Offline:
    def entries(self):raise OSError('synthetic offline')
render_saved_quotes(st,Offline())
''').run()
        self.assertFalse(at.exception)
        self.assertIn('接続を確認',[v.value for v in at.warning][0])
        self.assertEqual(list(at.success),[])


if __name__=='__main__':unittest.main()
