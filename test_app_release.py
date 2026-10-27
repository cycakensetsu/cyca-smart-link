from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import app_release
import importlib


class ReleaseRefreshTest(unittest.TestCase):
    def test_watcher_eviction_reimports_but_real_import_failure_is_not_hidden(self):
        import sys
        old=SimpleNamespace()
        def evict(module):
            sys.modules.pop('synthetic_live_module')
            raise ImportError('module removed by watcher',name='synthetic_live_module')
        with patch.object(app_release,'_MODULES',('synthetic_live_module',)),\
             patch.object(app_release,'_loaded_fingerprint',None),\
             patch.object(app_release,'_source_fingerprint',return_value='new-source'),\
             patch.dict('sys.modules',{'synthetic_live_module':old}),\
             patch('app_release.importlib.reload',side_effect=evict),\
             patch('app_release.importlib.import_module') as reimport:
            app_release.ensure_current_modules()
            reimport.assert_called_once_with('synthetic_live_module')
        with patch.object(app_release,'_MODULES',('synthetic_live_module',)),\
             patch.object(app_release,'_loaded_fingerprint',None),\
             patch.object(app_release,'_source_fingerprint',return_value='other-source'),\
             patch.dict('sys.modules',{'synthetic_live_module':old}),\
             patch('app_release.importlib.reload',side_effect=ImportError('missing real dependency')),\
             patch('app_release.importlib.import_module') as reimport:
            with self.assertRaisesRegex(ImportError,'missing real dependency'):
                app_release.ensure_current_modules()
            reimport.assert_not_called()

    def test_refreshing_manifest_keeps_lock_and_already_loaded_version(self):
        lock=app_release._lock
        with patch.object(app_release,'_loaded_fingerprint','loaded-source-version'):
            with lock:
                importlib.reload(app_release)
            self.assertIs(app_release._lock,lock)
            self.assertEqual(app_release._loaded_fingerprint,'loaded-source-version')

    def test_source_fingerprint_changes_when_an_imported_file_changes(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(app_release,'_ROOT',Path(temp)), patch.object(app_release,'_MODULES',('synthetic',)):
            source=Path(temp)/'synthetic.py'
            source.write_text('version = 1')
            first=app_release._source_fingerprint()
            source.write_text('version = 2')
            self.assertNotEqual(first,app_release._source_fingerprint())

    def test_frontend_asset_change_also_refreshes_component(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(app_release,'_ROOT',Path(temp)), patch.object(app_release,'_MODULES',()):
            folder=Path(temp)/'work_effects_frontend';folder.mkdir()
            asset=folder/'effects.js';asset.write_text('first')
            first=app_release._source_fingerprint()
            asset.write_text('second')
            self.assertNotEqual(first,app_release._source_fingerprint())

    def test_refresh_once_per_source_change(self):
        module=SimpleNamespace(version=1)
        with patch.object(app_release,'_MODULES',('synthetic_release_module',)), patch.object(app_release,'_loaded_fingerprint',None), patch.dict('sys.modules',{'synthetic_release_module':module}), patch.object(app_release,'_source_fingerprint',side_effect=['a','a','b']), patch('app_release.importlib.reload') as reload:
            app_release.ensure_current_modules()
            app_release.ensure_current_modules()
            self.assertEqual(reload.call_count,1)
            app_release.ensure_current_modules()
            self.assertEqual(reload.call_count,2)
