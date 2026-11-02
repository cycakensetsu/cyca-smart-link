from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from estimate_review import OriginalVault
from storage_health import storage_status
from vault_archive import archive_bytes, restore_archive


class StorageHealthTest(unittest.TestCase):
    def test_continuity_is_stable_per_owner_and_changes_with_a_new_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = OriginalVault(tmp, 'owner')
            first = storage_status(store, {})
            again = storage_status(OriginalVault(tmp, 'owner'), {})
            other = storage_status(OriginalVault(tmp, 'other'), {})
            replaced = storage_status(OriginalVault(Path(tmp)/'new-host', 'owner'), {})
            self.assertEqual(first, again)
            self.assertNotEqual(first['instance'], other['instance'])
            self.assertNotEqual(first['instance'], replaced['instance'])
            self.assertFalse(first['path_configured'])
            self.assertTrue(storage_status(store, {'CYCA_DATA_DIR': tmp})['path_configured'])
            self.assertEqual(list(store.entries()), [])

    def test_ten_initial_observers_agree_on_one_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = OriginalVault(tmp, 'owner')
            with ThreadPoolExecutor(max_workers=10) as pool:
                results = list(pool.map(lambda _: storage_status(store, {}), range(10)))
            self.assertEqual(len({v['instance'] for v in results}), 1)

    def test_metadata_is_not_exported_and_cannot_relabel_a_restored_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = OriginalVault(Path(tmp)/'source', 'owner')
            source.preserve('synthetic.pdf', b'synthetic original')
            source.snapshot('extractions', {'synthetic': 'only'})
            source.snapshot('approvals', {'synthetic': 'only'})
            status = storage_status(source, {})
            self.assertEqual([status[k] for k in ('originals','reads','approvals')], [1,1,1])
            data = archive_bytes(source)
            with ZipFile(BytesIO(data)) as z:
                self.assertFalse(any('.vault_meta' in n for n in z.namelist()))
                manifest = json.loads(z.read('manifest.json'))
            target = OriginalVault(Path(tmp)/'target', 'owner')
            target_status = storage_status(target, {})
            self.assertEqual(restore_archive(target, data), len(manifest['files']))
            after = storage_status(target, {})
            self.assertEqual(after['instance'], target_status['instance'])
            self.assertNotEqual(after['instance'], status['instance'])
            self.assertEqual([after[k] for k in ('originals','reads','approvals')], [1,1,1])

    def test_invalid_or_symlinked_marker_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = OriginalVault(tmp, 'owner')
            path = store.root/'.vault_meta/instance.json'
            path.parent.mkdir()
            path.write_text('{"schema": 0}')
            with self.assertRaises(ValueError): storage_status(store, {})
            self.assertEqual(path.read_text(), '{"schema": 0}')
            path.unlink()
            outside = Path(tmp)/'outside.json'
            outside.write_text('keep')
            path.symlink_to(outside)
            with self.assertRaises(ValueError): storage_status(store, {})
            self.assertEqual(outside.read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
