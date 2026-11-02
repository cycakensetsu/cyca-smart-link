import base64
import copy
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from browser_backups import work_package, restore_browser_package, digest_bytes
from estimate_review import OriginalVault
from test_work_drafts import working
from work_drafts import save_draft, available_drafts, restore_draft


class BrowserRecoveryTest(unittest.TestCase):
    def test_minimal_package_recovers_corrections_after_entire_host_storage_is_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = OriginalVault(Path(tmp)/'old-host','owner')
            state = working(first)
            key = save_draft(state, first)
            packet = work_package(first,key)
            content = base64.b64decode(packet['archive'])
            with ZipFile(BytesIO(content)) as z:
                self.assertTrue(any(n.startswith('originals/') for n in z.namelist()))
                self.assertEqual(sum(n.startswith('drafts/') for n in z.namelist()),1)
                self.assertFalse(any(n.startswith(('approvals/','exports/')) for n in z.namelist()))
                for n in z.namelist():
                    if n.endswith('.json'):
                        text = z.read(n).decode()
                        self.assertNotIn('_auth_signature',text)
                        self.assertNotIn('never save this',text)
            second = OriginalVault(Path(tmp)/'replacement-host','owner')
            self.assertFalse(available_drafts(second))
            self.assertGreater(restore_browser_package(second,packet),0)
            fresh = {'username':'owner','logged_in':True,'_auth_signature':'new-auth'}
            restore_draft(fresh,second,available_drafts(second)[0]['id'])
            for field in ('review_customer','review_project','_review_assistant_rows','_review_chat'):
                self.assertEqual(fresh[field],state[field])
            self.assertEqual(fresh['_auth_signature'],'new-auth')
            self.assertNotIn('_review_approval',fresh)
            self.assertFalse(fresh['_review_export_ready'])
            self.assertFalse(fresh['review_source_ok_'+state['_review_sources'][0]['id']])

    def test_old_browser_copy_does_not_overwrite_newer_shared_server_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=OriginalVault(tmp,'owner');state=working(store)
            key=save_draft(state,store);packet=work_package(store,key)
            state['review_project']='new server edit'
            newer=save_draft(state,store)
            restore_browser_package(store,packet)
            self.assertEqual(available_drafts(store)[0]['id'],newer)

    def test_foreign_corrupt_invalid_and_approval_packages_fail_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            first=OriginalVault(Path(tmp)/'one','owner');state=working(first)
            packet=work_package(first,save_draft(state,first))
            second=OriginalVault(Path(tmp)/'two','other')
            with self.assertRaises(ValueError):restore_browser_package(second,packet)
            self.assertEqual(list(second.entries()),[])
            second=OriginalVault(Path(tmp)/'two','owner')
            for changed in ({**packet,'sha256':'0'*64},{**packet,'archive':'!invalid!'},
                            {**packet,'archive':base64.b64encode(b'invalid ZIP').decode(),
                             'sha256':digest_bytes(b'invalid ZIP')}):
                with self.assertRaises(ValueError):restore_browser_package(second,changed)
                self.assertEqual(list(second.entries()),[])
            raw=BytesIO()
            with ZipFile(raw,'w') as z:
                z.writestr('approvals/'+'0'*64+'.json','{}')
            bad=raw.getvalue();changed={**packet,'archive':base64.b64encode(bad).decode(),
                                      'sha256':digest_bytes(bad)}
            with self.assertRaises(ValueError):restore_browser_package(second,changed)
            self.assertEqual(list(second.entries()),[])


if __name__=='__main__': unittest.main()
