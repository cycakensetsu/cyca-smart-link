"""Historical OCR disagreements are evidence to inspect, never corrections."""
import copy
import hashlib
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from estimate_pipeline import build_intermediate_dataframe
from estimate_review import OriginalVault, digest, review_rows
from source_spec_history import compare_spec_history, ensure_spec_history, review_vault_spec_history


def snapshot(spec='TEST-A', *, original=b'synthetic original', duplicate=False):
    source = {'id': hashlib.sha256(original).hexdigest(), 'name': 'original.pdf',
              'pages': 4, 'confirmed': False}
    record = {'品名': '原本の機器', '仕様': spec, '数量': 2, '単位': '台', '単価': 100,
              '金額': 200, '見積元': '架空施工社', '工事種別': '設備工事',
              '__source_name': 'original.pdf', '__page_number': 4}
    records = [record, copy.deepcopy(record)] if duplicate else [record]
    frame, _ = build_intermediate_dataframe(records)
    return {'rows': review_rows(frame, [source]), 'summary': {}, 'sources': [source], 'records': records}


def pair(raw):
    return digest(raw), raw


class SourceSpecHistoryTest(unittest.TestCase):
    def test_unique_same_original_difference_keeps_candidates_proof_and_every_input_unchanged(self):
        current, old, other = snapshot('TEST-AA'), snapshot('TEST-A'), snapshot('TEST-B')
        before = copy.deepcopy((current, old, other))
        result = compare_spec_history(current, [pair(old), pair(old), pair(other), pair(current)])
        self.assertEqual((current, old, other), before)
        self.assertFalse(result['automatic_changes'])
        self.assertFalse(result['first_read_covered'])
        self.assertTrue(result['nonempty_specs_only'])
        self.assertEqual(len(result['findings']), 1)
        finding = result['findings'][0]
        self.assertEqual(finding['行ID'], current['rows'][0]['行ID'])
        self.assertEqual(finding['ページ'], 4)
        self.assertEqual(finding['今回の仕様'], 'TEST-AA')
        self.assertEqual([x['仕様'] for x in finding['過去の表記']], ['TEST-A', 'TEST-B'])
        self.assertEqual(finding['過去の表記'][0]['根拠'],
                         [{'snapshot_id': digest(old), '行ID': old['rows'][0]['行ID']}])

    def test_changed_original_page_name_vendor_or_numeric_fact_has_no_guessed_correspondence(self):
        current = snapshot('TEST-AA')
        for field, value in (('ページ', 3), ('品名', '別の機器'), ('見積元', '別会社'),
                             ('単位', '式'), ('数量', 3), ('原価単価', 101), ('原価金額', 201)):
            with self.subTest(field=field):
                old = snapshot('TEST-A'); old['rows'][0][field] = value
                self.assertEqual(compare_spec_history(current, [pair(old)])['findings'], [])
        foreign = snapshot('TEST-A', original=b'different original with same filename')
        self.assertEqual(compare_spec_history(current, [pair(foreign)])['findings'], [])

    def test_ambiguous_rows_on_either_side_are_not_matched(self):
        for current, old in ((snapshot('TEST-AA', duplicate=True), snapshot()),
                             (snapshot('TEST-AA'), snapshot(duplicate=True))):
            self.assertEqual(compare_spec_history(current, [pair(old)])['findings'], [])

    def test_bool_unknown_nonfinite_and_empty_spec_never_become_numeric_zero_or_proof(self):
        for field in ('数量', '原価単価', '原価金額', 'ページ'):
            for value in (True, None, '', float('nan'), float('inf')):
                with self.subTest(field=field, value_type=type(value).__name__):
                    current = snapshot('TEST-AA'); current['rows'][0][field] = value
                    self.assertEqual(compare_spec_history(current, [pair(snapshot())])['findings'], [])
        for current, old in ((snapshot(''), snapshot()), (snapshot('TEST-AA'), snapshot(''))):
            self.assertEqual(compare_spec_history(current, [pair(old)])['findings'], [])

    def test_raw_schema_digest_and_unedited_flags_are_required_but_legacy_missing_confirmed_is_valid(self):
        current = snapshot('TEST-AA')
        legacy = snapshot(); legacy['sources'][0].pop('confirmed')
        self.assertEqual(len(compare_spec_history(current, [pair(legacy)])['findings']), 1)
        for field, value in (('訂正理由', '手で訂正済み'), ('除外理由', '除外済み'),
                             ('顧客備考', '顧客向けに変更'), ('採用', False)):
            edited = snapshot(); edited['rows'][0][field] = value
            self.assertEqual(compare_spec_history(current, [pair(edited)])['findings'], [])
        for confirmed in (True, None, 0):
            edited = snapshot(); edited['sources'][0]['confirmed'] = confirmed
            self.assertEqual(compare_spec_history(current, [pair(edited)])['findings'], [])
        tampered = snapshot(); old_id = digest(tampered); tampered['rows'][0]['仕様'] = 'changed after hashing'
        for evidence in ((old_id, tampered), pair({'kind': 'approval', 'rows': snapshot()['rows']}),
                         pair({**snapshot(), 'records': None})):
            result = compare_spec_history(current, [evidence])
            self.assertEqual(result['findings'], [])
            self.assertEqual(result['skipped_snapshots'], 1)

    def test_malformed_original_or_row_ids_and_duplicate_row_ids_skip_instead_of_crashing(self):
        for field in ('原本ID', '行ID'):
            for value in ([], {}, None, '', True):
                with self.subTest(field=field, value_type=type(value).__name__):
                    malformed = snapshot(); malformed['rows'][0][field] = value
                    self.assertEqual(compare_spec_history(snapshot('TEST-AA'), [pair(malformed)])['findings'], [])
                    self.assertEqual(compare_spec_history(malformed, [pair(snapshot())])['findings'], [])
        malformed = snapshot(duplicate=True)
        malformed['rows'][1]['行ID'] = malformed['rows'][0]['行ID']
        self.assertEqual(compare_spec_history(snapshot('TEST-AA'), [pair(malformed)])['findings'], [])

    def test_lazy_initialization_runs_once_per_extraction_and_isolated_user_vault(self):
        current, old = snapshot('TEST-AA'), snapshot()
        with tempfile.TemporaryDirectory() as temp:
            first = OriginalVault(temp, 'first-user')
            second = OriginalVault(temp, 'second-user')
            first.snapshot('extractions', old)
            current_id = first.snapshot('extractions', current)
            state = {'_review_extraction_id': current_id}
            with patch.object(first, 'entries', wraps=first.entries) as entries:
                found = ensure_spec_history(state, first)
                self.assertEqual(len(found['findings']), 1)
                self.assertEqual(ensure_spec_history(state, first), found)
                self.assertEqual(entries.call_count, 1)
            # A shared state cannot carry another user's historical candidates.
            self.assertEqual(ensure_spec_history(state, second)['findings'], [])
            self.assertTrue(state['_review_spec_history']['history_unavailable'])
            first.snapshot('extractions', snapshot('TEST-C'))
            # Merely rerendering does not rerun history: a new extraction ID does.
            state['_review_extraction_id'] = first.snapshot('extractions', snapshot('TEST-D'))
            self.assertTrue(ensure_spec_history(state, first)['findings'])

    def test_optional_history_failure_and_size_bound_do_not_break_current_extraction(self):
        current = snapshot('TEST-AA')
        for error in (ValueError('unsafe history path'), OSError('unreadable history')):
            store = SimpleNamespace(entries=lambda error=error: (_ for _ in ()).throw(error))
            result = review_vault_spec_history(store, current)
            self.assertEqual(result['findings'], [])
            self.assertTrue(result['history_unavailable'])
        store = SimpleNamespace(entries=lambda: iter([('extractions/' + 'a' * 64 + '.json', 100,
                                                     lambda: self.fail('oversized history was read'))]))
        with patch('source_spec_history._MAX_SNAPSHOT_BYTES', 1):
            result = review_vault_spec_history(store, current)
        self.assertTrue(result['history_limits_applied'])
        self.assertEqual(result['findings'], [])

    def test_capture_computes_history_once_without_modifying_price_rows(self):
        from review_ui import capture_extraction
        current, old = snapshot('TEST-AA'), snapshot()
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ', CYCA_DATA_DIR=temp):
            store = OriginalVault(temp, 'capture-user'); store.snapshot('extractions', old)
            state = {'username': 'capture-user', '_review_sources': copy.deepcopy(current['sources'])}
            frame, _ = build_intermediate_dataframe(current['records'])
            capture_extraction(SimpleNamespace(session_state=state), frame, {}, copy.deepcopy(current['records']))
            self.assertEqual(len(state['_review_spec_history']['findings']), 1)
            self.assertEqual(state['_review_original_rows'][0]['仕様'], 'TEST-AA')
            self.assertEqual(state['_review_original_rows'][0]['原価金額'], 200)
            with patch.object(store, 'entries', side_effect=AssertionError('unexpected rerun scan')):
                self.assertTrue(ensure_spec_history(state, store)['findings'])

    def test_comparison_ui_is_plain_text_local_and_never_changes_a_candidate(self):
        current, old = snapshot('TEST-AA'), snapshot()
        name = '![remote](https://example.invalid/image.png)'
        current['rows'][0]['品名'] = old['rows'][0]['品名'] = name
        current['rows'][0]['仕様'] = '[current](https://example.invalid/current)'
        old['rows'][0]['仕様'] = '<img src="https://example.invalid/prior">'
        comparison = compare_spec_history(current, [pair(old)])
        row, sid = current['rows'][0], current['sources'][0]['id']
        app = f'''
import streamlit as st
from source_review_ui import _render_spec_comparisons
st.session_state['_review_spec_history']={comparison!r}
rows={current['rows']!r}
_render_spec_comparisons(st,rows,{sid!r})
st.session_state['probe_rows']=rows
'''
        at = AppTest.from_string(app).run()
        self.assertEqual(list(at.exception), [])
        self.assertEqual(len(at.expander), 1)
        plain = [element.value for element in at.text]
        self.assertIn('品目：' + name, plain)
        self.assertIn('今回：' + row['仕様'], plain)
        self.assertIn('以前：' + old['rows'][0]['仕様'], plain)
        self.assertFalse(any('https://' in element.value for element in at.markdown))
        self.assertEqual(len(at.get('imgs')), 0)
        self.assertFalse(any('snapshot' in text or digest(old) in text for text in plain))
        at.button[0].click().run()
        self.assertEqual(list(at.exception), [])
        self.assertEqual(at.session_state['review_source_page_' + sid], 4)
        self.assertEqual(at.session_state['probe_rows'], current['rows'])


if __name__ == '__main__':
    unittest.main()
