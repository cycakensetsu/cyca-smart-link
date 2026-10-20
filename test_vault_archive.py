from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile, ZIP_DEFLATED

from estimate_review import OriginalVault
from vault_archive import archive_bytes, restore_archive


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source=OriginalVault(Path(self.temp.name)/'source','test-actor')
        self.record=self.source.preserve('synthetic.pdf',b'synthetic-source')
        self.source.snapshot('extractions',{'rows': [{'品名':'検証用'}]})
        self.destination=OriginalVault(Path(self.temp.name)/'destination','test-actor')

    def edit_archive(self, edit):
        output=BytesIO()
        with ZipFile(BytesIO(archive_bytes(self.source))) as old, ZipFile(output,'w',ZIP_DEFLATED) as new:
            files={name:old.read(name) for name in old.namelist()}
            edit(files)
            for name,data in files.items():new.writestr(name,data)
        return output.getvalue()

    def test_restore_after_new_instance_and_idempotent_restore(self):
        data=archive_bytes(self.source)
        self.assertEqual(restore_archive(self.destination,data),3)
        self.assertEqual(restore_archive(self.destination,data),3)
        restarted=OriginalVault(Path(self.temp.name)/'destination','test-actor')
        self.assertEqual(restarted.read(self.record['id']),b'synthetic-source')
        with ZipFile(BytesIO(data)) as before, ZipFile(BytesIO(archive_bytes(restarted))) as after:
            self.assertEqual({n:before.read(n) for n in before.namelist()},
                             {n:after.read(n) for n in after.namelist()})

    def test_changed_original_is_rejected_before_any_restore(self):
        def edit(files):
            key=next(k for k in files if k.startswith('originals/'))
            files[key]=b'changed'
        with self.assertRaises(ValueError):restore_archive(self.destination,self.edit_archive(edit))
        self.assertEqual(list(self.destination.root.iterdir()),[])

    def test_traversal_and_missing_manifest_rejected(self):
        def edit(files):files['../escape']=b'bad'
        with self.assertRaises(ValueError):restore_archive(self.destination,self.edit_archive(edit))
        def missing(files):del files['manifest.json']
        with self.assertRaises(ValueError):restore_archive(self.destination,self.edit_archive(missing))
        self.assertFalse((Path(self.temp.name)/'escape').exists())

    def test_wrong_user_cannot_restore(self):
        other=OriginalVault(Path(self.temp.name)/'other','another-actor')
        with self.assertRaises(ValueError):restore_archive(other,archive_bytes(self.source))

    def test_existing_conflict_is_not_overwritten(self):
        path=self.destination.root/'originals'/self.record['id']/'original'
        path.parent.mkdir(parents=True);path.write_bytes(b'conflicting')
        with self.assertRaises(ValueError):restore_archive(self.destination,archive_bytes(self.source))
        self.assertEqual(path.read_bytes(),b'conflicting')

    def test_invalid_data_and_empty_store(self):
        self.assertIsNone(archive_bytes(self.destination))
        with self.assertRaises(ValueError):restore_archive(self.destination,b'invalid')


if __name__=='__main__':unittest.main()
