"""Refresh coordinated application modules after a Cloud source update.

Streamlit can rerun the entrypoint before invalidating imported modules. Compare
the source set on each rerun, and refresh it in dependency order once per change.
No session, original, receipt, configuration or storage path is modified here.
"""
import hashlib
import importlib
from pathlib import Path
import sys
import threading

_ROOT = Path(__file__).resolve().parent
_MODULES = ('estimate_pipeline', 'template_fill_test_v2', 'horizontal_estimate_flow',
            'print_layout', 'reference_layout', 'estimate_review', 'vault_archive',
            'conversation_assistant', 'conversation_ui', 'pour_layout',
            'output_preview_ui', 'review_ui', 'legacy_exports')
_lock = threading.RLock()
_loaded_fingerprint = None


def _source_fingerprint():
    result = hashlib.sha256()
    for name in _MODULES:
        result.update(name.encode())
        result.update((_ROOT / (name + '.py')).read_bytes())
    return result.hexdigest()


def ensure_current_modules():
    global _loaded_fingerprint
    with _lock:
        fingerprint = _source_fingerprint()
        if fingerprint == _loaded_fingerprint:
            return
        importlib.invalidate_caches()
        for name in _MODULES:
            module = sys.modules.get(name)
            if module is None:
                importlib.import_module(name)
            else:
                importlib.reload(module)
        _loaded_fingerprint = fingerprint
