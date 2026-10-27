import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from estimate_review import OriginalVault, digest
from test_source_spec_history import snapshot
from vault_archive import archive_bytes


class RestoreUITest(unittest.TestCase):
    def test_optional_read_backup_before_export_contains_sources_without_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            at=AppTest.from_string(f'''
import os
os.environ['CYCA_DATA_DIR']={temp!r}
import streamlit as st
from review_ui import vault, render_archive
st.session_state['username']='read-backup-actor'
store=vault(st)
source=store.preserve('original.pdf',b'original bytes')
st.session_state['_review_extraction_id']=store.snapshot('extractions',{{'type':'read_checkpoint_v1','result':[]}})
render_archive(st)
''').run()
            self.assertEqual(len(at.get('download_button')),0)
            at.button(key='intake_prepare_archive').click().run()
            self.assertEqual(list(at.exception),[])
            from io import BytesIO
            from zipfile import ZipFile
            with ZipFile(BytesIO(at.session_state['_vault_archive'][0])) as archive:
                names=archive.namelist()
                self.assertTrue(any(n.startswith('originals/') for n in names))
                self.assertTrue(any(n.startswith('extractions/') for n in names))
                self.assertFalse(any(n.startswith(('approvals/','exports/')) for n in names))

    def app(self, temp):
        return AppTest.from_string(f'''
import copy, os
os.environ['CYCA_DATA_DIR']={temp!r}
import streamlit as st
from review_ui import vault, render_archive
from source_spec_history import ensure_spec_history
from test_source_spec_history import snapshot
st.session_state['username']='restore-test-actor'
store=vault(st)
if '_review_extraction_id' not in st.session_state:
    current=snapshot('CURRENT')
    st.session_state['_review_extraction_id']=store.snapshot('extractions', current)
    st.session_state['_review_original_rows']=copy.deepcopy(current['rows'])
    st.session_state['review_customer']='今のお客様'
    st.session_state['review_source_ok_sentinel']=True
    st.session_state['_review_approval']={{'current':'preserve'}}
history=ensure_spec_history(st.session_state,store)
st.write('比較件数：'+str(len(history['findings'])))
render_archive(st)
''').run()

    def backup(self, temp, actor='restore-test-actor'):
        store=OriginalVault(Path(temp)/'backup-source',actor)
        store.preserve('original.pdf',b'synthetic original')
        store.snapshot('extractions',snapshot('PREVIOUS'))
        store.snapshot('approvals',{'old':'must-not-replace-current'})
        return archive_bytes(store)

    def test_restore_before_export_refreshes_same_extraction_history_without_applying_old_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            at=self.app(str(Path(temp)/'destination'))
            before=copy.deepcopy(at.session_state['_review_original_rows'])
            extraction=at.session_state['_review_extraction_id']
            self.assertEqual(at.session_state['_review_spec_history']['findings'],[])
            self.assertEqual(len(at.get('download_button')),0)
            self.assertTrue(at.button(key='vault_restore').disabled)
            upload=SimpleNamespace(getbuffer=lambda:self.backup(temp))
            with patch('streamlit.file_uploader',return_value=upload):
                at.run();at.button(key='vault_restore').click().run()
            self.assertEqual(list(at.exception),[])
            self.assertEqual(at.session_state['_review_extraction_id'],extraction)
            self.assertEqual(len(at.session_state['_review_spec_history']['findings']),1)
            self.assertEqual(at.session_state['_review_original_rows'],before)
            self.assertEqual(at.session_state['review_customer'],'今のお客様')
            self.assertTrue(at.session_state['review_source_ok_sentinel'])
            self.assertEqual(at.session_state['_review_approval'],{'current':'preserve'})
            self.assertTrue(any('保存データを戻しました' in m.value for m in at.success))
            self.assertEqual(len(at.get('download_button')),0)

    def test_wrong_owner_or_invalid_backup_does_not_change_files_history_or_current_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            for data in (b'invalid zip',self.backup(temp,'another-actor')):
                at=self.app(str(Path(temp)/('destination-'+digest(data.hex()))))
                root=OriginalVault(str(Path(temp)/('destination-'+digest(data.hex()))),'restore-test-actor').root
                before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
                history=copy.deepcopy(at.session_state['_review_spec_history'])
                with patch('streamlit.file_uploader',return_value=SimpleNamespace(getbuffer=lambda:data)):
                    at.run();at.button(key='vault_restore').click().run()
                self.assertEqual(list(at.exception),[])
                self.assertTrue(list(at.error))
                self.assertEqual(at.session_state['_review_spec_history'],history)
                self.assertEqual(at.session_state['_review_approval'],{'current':'preserve'})
                self.assertEqual({str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()},before)


if __name__=='__main__':unittest.main()
