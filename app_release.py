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
_MODULES = ('pdf_worker', 'product_design', 'work_effects', 'estimate_pipeline', 'print_text_height', 'template_fill_test_v2', 'horizontal_estimate_flow',
            'print_layout', 'reference_layout', 'estimate_review', 'vault_archive',
            'cyca_reference', 'source_table', 'gemini_request_gate', 'gemini_resilience', 'source_sections', 'document_scopes', 'source_annotations', 'source_extraction', 'extraction_batch', 'complete_document_cache', 'source_variants', 'source_amounts', 'source_rounding', 'source_spec_history', 'item_correction_ui', 'source_review_ui', 'workflow_steps_ui', 'review_input_ui', 'review_findings_ui',
            'conversation_assistant', 'conversation_ui', 'pour_layout',
            'output_preview_ui', 'review_ui', 'saved_read_resume', 'legacy_exports')
_lock = globals().get('_lock') or threading.RLock()
_loaded_fingerprint = globals().get('_loaded_fingerprint')


def _source_fingerprint():
    result = hashlib.sha256()
    for name in _MODULES:
        result.update(name.encode())
        result.update((_ROOT / (name + '.py')).read_bytes())
    for asset in sorted((_ROOT / 'work_effects_frontend').glob('*')):
        if asset.is_file():
            result.update(asset.name.encode())
            result.update(asset.read_bytes())
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
