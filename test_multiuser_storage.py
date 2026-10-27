"""Concurrent sessions must never observe half-written originals or receipts."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import tempfile
import threading
import unittest
from unittest.mock import patch
from zipfile import ZipFile
from io import BytesIO

from estimate_review import OriginalVault
from vault_archive import archive_bytes, restore_archive


class MultiuserStorageTest(unittest.TestCase):
    def test_same_original_and_backup_while_another_session_is_writing(self):
        entered, release = threading.Event(), threading.Event()
        real_fdopen = os.fdopen

        class SlowFile:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): self.stream.__enter__(); return self
            def __exit__(self, *args): return self.stream.__exit__(*args)
            def __getattr__(self, key): return getattr(self.stream, key)
            def write(self, data):
                split = len(data) // 2
                self.stream.write(data[:split]); self.stream.flush()
                entered.set()
                if not release.wait(5): raise TimeoutError('test writer not released')
                return split + self.stream.write(data[split:])

        def fdopen(*args, **kwargs):
            stream = real_fdopen(*args, **kwargs)
            return SlowFile(stream) if threading.current_thread().name == 'slow-writer' else stream

        with tempfile.TemporaryDirectory() as temp:
            store = OriginalVault(Path(temp) / 'source', 'shared-user')
            destination = OriginalVault(Path(temp) / 'restore', 'shared-user')
            errors = []
            data = b'exact-original-bytes' * 10000
            def first():
                try: store.preserve('same.pdf', data)
                except BaseException as error: errors.append(error)
            with patch('os.fdopen', fdopen):
                worker = threading.Thread(target=first, name='slow-writer')
                worker.start()
                try:
                    self.assertTrue(entered.wait(3))
                    record = store.preserve('same.pdf', data)
                    backup = archive_bytes(store)
                    with ZipFile(BytesIO(backup)) as archive:
                        self.assertFalse(any('.staging' in name for name in archive.namelist()))
                    restore_archive(destination, backup)
                    self.assertEqual(destination.read(record['id']), data)
                finally:
                    release.set(); worker.join(6)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(list((store.root / '.staging').iterdir()), [])

    def test_ten_same_account_writers_are_idempotent_and_never_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            barrier = threading.Barrier(10)
            stores = [OriginalVault(temp, 'shared-user') for _ in range(10)]
            data = b'the-same-original' * 100000
            def write(store):
                barrier.wait(timeout=5)
                return store.preserve('same.pdf', data)
            with ThreadPoolExecutor(max_workers=10) as executor:
                records = list(executor.map(write, stores))
            self.assertEqual(len({r['id'] for r in records}), 1)
            self.assertEqual(stores[0].read(records[0]['id']), data)
            self.assertEqual(len(list(stores[0].entries())), 2)
            with self.assertRaisesRegex(ValueError, '不一致'):
                stores[0]._put('originals/' + records[0]['id'] + '/original', b'changed')
            self.assertEqual(stores[0].read(records[0]['id']), data)

    def test_failed_write_leaves_no_published_or_temporary_file(self):
        with tempfile.TemporaryDirectory() as temp:
            store = OriginalVault(temp, 'synthetic-user')
            with patch('estimate_review.os.fsync', side_effect=OSError('disk full')):
                with self.assertRaises(OSError): store.preserve('same.pdf', b'original')
            self.assertEqual(list(store.entries()), [])
            self.assertEqual(list((store.root / '.staging').iterdir()), [])


if __name__ == '__main__': unittest.main()
