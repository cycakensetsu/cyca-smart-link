from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import app_release


class ReleaseRefreshTest(unittest.TestCase):
    def test_source_fingerprint_changes_when_an_imported_file_changes(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(app_release,'_ROOT',Path(temp)), patch.object(app_release,'_MODULES',('synthetic',)):
            source=Path(temp)/'synthetic.py'
            source.write_text('version = 1')
            first=app_release._source_fingerprint()
            source.write_text('version = 2')
            self.assertNotEqual(first,app_release._source_fingerprint())

    def test_refresh_once_per_source_change(self):
        module=SimpleNamespace(version=1)
        with patch.object(app_release,'_MODULES',('synthetic_release_module',)), patch.object(app_release,'_loaded_fingerprint',None), patch.dict('sys.modules',{'synthetic_release_module':module}), patch.object(app_release,'_source_fingerprint',side_effect=['a','a','b']), patch('app_release.importlib.reload') as reload:
            app_release.ensure_current_modules()
            app_release.ensure_current_modules()
            self.assertEqual(reload.call_count,1)
            app_release.ensure_current_modules()
            self.assertEqual(reload.call_count,2)
