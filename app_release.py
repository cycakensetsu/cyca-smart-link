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
_MODULES = ('login_ui', 'pdf_worker', 'product_design', 'work_effects', 'markup_rules', 'round_pricing', 'estimate_pipeline', 'row_evidence_review', 'row_evidence_gate', 'print_text_height', 'template_fill_test_v2', 'horizontal_estimate_flow',
            'print_layout', 'reference_layout', 'estimate_review', 'pricing_options', 'pricing_options_ui', 'vault_archive', 'shared_storage', 'saved_quotes', 'storage_health',
            'cyca_reference', 'source_table', 'gemini_request_gate', 'gemini_resilience', 'source_sections', 'document_scopes', 'source_annotations', 'source_extraction', 'extraction_batch', 'complete_document_cache', 'source_variants', 'source_amounts', 'source_rounding', 'source_spec_history', 'item_correction_ui', 'source_review_ui', 'workflow_steps_ui', 'review_input_ui', 'review_findings_ui',
            'conversation_assistant', 'conversation_ui', 'pour_layout',
            'output_preview_ui', 'review_ui', 'saved_read_resume', 'work_drafts', 'browser_backups', 'legacy_exports')
_lock = vars(threading).setdefault('_cyca_release_lock',globals().get('_lock') or threading.RLock())
_loaded_fingerprint = globals().get('_loaded_fingerprint')


def _source_fingerprint():
    result = hashlib.sha256()
    for name in _MODULES:
        result.update(name.encode())
        result.update((_ROOT / (name + '.py')).read_bytes())
    for asset in sorted([*(_ROOT / 'work_effects_frontend').glob('*'),
                         *(_ROOT / 'browser_backups_frontend').glob('*')]):
        if asset.is_file():
            result.update(asset.name.encode())
            result.update(asset.read_bytes())
    return result.hexdigest()


def _refresh_module(name):
    # Cloud's source watcher mutates sys.modules outside our release lock. An
    # eviction can race with importlib's final pop even on an initial import.
    # Retry only that precise importlib failure, never a module's own KeyError.
    for attempt in range(3):
        module = sys.modules.get(name)
        try:
            if module is None:
                return importlib.import_module(name)
            return importlib.reload(module)
        except ImportError:
            if module is None or sys.modules.get(name) is module or attempt == 2:
                raise
        except KeyError as exc:
            last = exc.__traceback__
            while last.tb_next is not None:
                last = last.tb_next
            code = last.tb_frame.f_code
            if (exc.args != (name,) or code.co_filename != '<frozen importlib._bootstrap>'
                    or code.co_name not in ('_load_unlocked', '_exec') or attempt == 2):
                raise


def ensure_current_modules():
    global _loaded_fingerprint
    with _lock:
        fingerprint = _source_fingerprint()
        if fingerprint == _loaded_fingerprint:
            return
        importlib.invalidate_caches()
        for name in _MODULES:
            _refresh_module(name)
        _loaded_fingerprint = fingerprint
