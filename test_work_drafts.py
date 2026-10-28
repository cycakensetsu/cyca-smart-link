import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from estimate_review import OriginalVault, canonical, digest
from test_saved_read_resume import stored_read
from vault_archive import archive_bytes, restore_archive
from work_drafts import (available_drafts, hydrate_inputs, restore_draft,
                         save_draft, validate_draft, update_head)


def working(store):
    extraction, raw = stored_read(store)
    state = {'username': 'owner', '_review_extraction_id': extraction,
             '_review_original_rows': raw['rows'], '_review_sources': raw['sources'],
             '_review_metadata': raw['summary'], '_review_assistant_rows': copy.deepcopy(raw['rows']),
             'review_customer': '架空のお客様 御中', 'review_project': '架空の工事',
             'review_scope': '全体', 'review_target_mode': '上乗せ額',
             'review_target_' + digest(['全体', '上乗せ額', '全体']): 1000.0,
             'review_page_breaks': [raw['rows'][0]['行ID']],
             '_review_requested_pages': {raw['rows'][0]['行ID']: 3},
             '_review_chat': [{'role': 'user', 'content': '架空の相談'},
                              {'role': 'assistant', 'content': '確認用の返事'}],
             'logged_in': True, '_auth_signature': 'never save this',
             '_review_result': b'old PDF', '_review_approval': {'old': True},
             '_review_proposal': {'must': 'never replay'}, 'review_agree_old': True}
    state['_review_assistant_rows'][0].update(仕様='訂正した長い仕様', 工事種別='電気工事', 訂正理由='原本で確認')
    sid = raw['sources'][0]['id']
    for prefix, value in [('basis', '税抜'), ('tax', '10%'), ('net', 200),
                          ('tax_amount', 20), ('gross', 220), ('tax_exists', True)]:
        state[f'review_{prefix}_{sid}'] = value
    return state


class WorkingDraftTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = OriginalVault(self.tmp.name, 'owner')
        self.state = working(self.store)

    def test_reload_roundtrip_corrections_price_chat_but_no_auth_approval_or_AI_replay(self):
        before = {n: r() for n, _, r in self.store.entries()}
        key = save_draft(self.state, self.store)
        saved = json.loads(self.store.get_entry(f'drafts/{key}.json'))
        serialized = canonical(saved)
        for forbidden in ('_auth_signature', 'never save this', 'logged_in',
                          '_review_result', '_review_approval', '_review_proposal', 'review_agree_old'):
            self.assertNotIn(forbidden, serialized)
        fresh = {'username': 'owner', 'logged_in': True, '_auth_signature': 'current login'}
        restore_draft(fresh, OriginalVault(self.tmp.name, 'owner'), key)
        for name in ('review_customer', 'review_project', 'review_page_breaks',
                     '_review_requested_pages', '_review_chat', '_review_assistant_rows'):
            self.assertEqual(fresh[name], self.state[name])
        self.assertEqual(fresh['_auth_signature'], 'current login')
        self.assertFalse(fresh['_review_export_ready'])
        sid = fresh['_review_sources'][0]['id']
        self.assertFalse(fresh['review_source_ok_' + sid])
        self.assertEqual(fresh['review_net_' + sid], 200)
        self.assertTrue(fresh['_review_amount_defaults'][sid])
        self.assertNotEqual(fresh['_work_draft_id'], self.state['_work_draft_id'])
        self.assertTrue(all(self.store.get_entry(n) == data for n, data in before.items()))

    def test_unchanged_reruns_do_not_grow_vault_and_hidden_widgets_keep_committed_values(self):
        first = save_draft(self.state, self.store)
        self.assertEqual(save_draft(self.state, self.store), first)
        del self.state['review_customer']
        hydrate_inputs(self.state)
        self.assertEqual(self.state['review_customer'], '架空のお客様 御中')
        self.assertEqual(save_draft(self.state, self.store), first)
        self.assertEqual(len([n for n, _, _ in self.store.entries() if n.startswith('drafts/')]), 1)

    def test_ten_sessions_and_two_people_resuming_same_work_never_overwrite(self):
        original = save_draft(self.state, self.store)
        def run(index):
            state = {'username': 'owner'}
            restore_draft(state, self.store, original)
            state['review_project'] = f'独立した工事{index}'
            return save_draft(state, self.store)
        with ThreadPoolExecutor(max_workers=10) as pool:
            saved = list(pool.map(run, range(10)))
        self.assertEqual(len(set(saved)), 10)
        offered = available_drafts(self.store)
        self.assertEqual(len(offered), 11)
        self.assertEqual(len({d['work_id'] for d in offered}), 11)
        for index, key in enumerate(saved):
            state = {'username': 'owner'}
            restore_draft(state, self.store, key)
            self.assertEqual(state['review_project'], f'独立した工事{index}')
        self.assertIsNotNone(self.store.get_entry(f'drafts/{original}.json'))

    def test_wrong_owner_tampered_payload_missing_original_and_row_source_fail_before_mutation(self):
        key = save_draft(self.state, self.store)
        other = OriginalVault(self.tmp.name, 'other')
        self.assertEqual(available_drafts(other), [])
        state = {'username': 'other', 'review_customer': 'keep this'}
        with self.assertRaises(ValueError):
            restore_draft(state, other, key)
        self.assertEqual(state, {'username': 'other', 'review_customer': 'keep this'})
        original = json.loads(self.store.get_entry(f'drafts/{key}.json'))
        for change in ('owner', 'authentication', 'approval', 'original', 'row_source', 'chat_system'):
            value = copy.deepcopy(original)
            if change == 'owner': value['owner'] = other.root.name
            if change == 'authentication': value['state']['logged_in'] = True
            if change == 'approval': value['state']['_review_approval'] = {'id': 'fake'}
            if change == 'original': value['extraction_id'] = '0' * 64
            if change == 'row_source': value['state']['_review_assistant_rows'][0]['原本ID'] = '0' * 64
            if change == 'chat_system': value['state']['_review_chat'] = [{'role': 'system', 'content': 'bad'}]
            bad = digest(value)
            self.store._put(f'drafts/{bad}.json', canonical(value).encode())
            current = {'username': 'owner', 'review_customer': 'keep this'}
            with self.assertRaises((ValueError, KeyError)):
                restore_draft(current, self.store, bad)
            self.assertEqual(current, {'username': 'owner', 'review_customer': 'keep this'})
        source = self.store.root / 'originals' / self.state['_review_sources'][0]['id'] / 'original'
        source.write_bytes(b'changed source')
        current = {'username': 'owner', 'review_customer': 'keep this'}
        with self.assertRaises(ValueError): restore_draft(current, self.store, key)
        self.assertEqual(current, {'username': 'owner', 'review_customer': 'keep this'})

    def test_interrupted_atomic_save_retains_previous_checkpoint_and_does_not_claim_new_save(self):
        first = save_draft(self.state, self.store)
        self.state['review_customer'] = 'new input'
        with patch.object(self.store, '_put', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError): save_draft(self.state, self.store)
        self.assertEqual(self.state['_work_draft_record'], first)
        fresh = {'username': 'owner'}
        restore_draft(fresh, self.store, first)
        self.assertEqual(fresh['review_customer'], '架空のお客様 御中')

    def test_portable_backup_keeps_latest_work_revision_and_old_originals_and_receipts(self):
        old = save_draft(self.state, self.store, datetime(2026, 10, 28, tzinfo=timezone.utc))
        self.state['review_customer'] = '変更後のお客様'
        latest = save_draft(self.state, self.store, datetime(2026, 10, 28, tzinfo=timezone.utc) + timedelta(seconds=1))
        data = archive_bytes(self.store)
        with ZipFile(BytesIO(data)) as z:
            self.assertNotIn(f'drafts/{old}.json', z.namelist())
            self.assertIn(f'drafts/{latest}.json', z.namelist())
        self.assertIsNotNone(self.store.get_entry(f'drafts/{old}.json'))
        target = OriginalVault(Path(self.tmp.name) / 'restored', 'owner')
        restore_archive(target, data)
        recovered = {'username': 'owner'}
        restore_draft(recovered, target, latest)
        self.assertEqual(recovered['review_customer'], '変更後のお客様')
        with ZipFile(BytesIO(data)) as a, ZipFile(BytesIO(archive_bytes(target))) as b:
            self.assertEqual({n: a.read(n) for n in a.namelist()}, {n: b.read(n) for n in b.namelist()})

    def test_latest_index_skips_history_scan_and_restoring_old_backup_never_rolls_it_back(self):
        old = save_draft(self.state, self.store, datetime(2026, 10, 28, tzinfo=timezone.utc))
        backup = archive_bytes(self.store)
        self.state['review_customer'] = 'newer edit'
        new = save_draft(self.state, self.store, datetime(2026, 10, 28, tzinfo=timezone.utc) + timedelta(minutes=1))
        with patch.object(self.store, 'entries', side_effect=AssertionError('old history must not be scanned')):
            self.assertEqual([d['id'] for d in available_drafts(self.store)], [new])
        restore_archive(self.store, backup)
        self.assertEqual([d['id'] for d in available_drafts(self.store)], [new])
        self.assertIsNotNone(self.store.get_entry(f'drafts/{old}.json'))

    def test_interrupted_index_publish_preserves_old_head_without_claiming_new_saved_state(self):
        old = save_draft(self.state, self.store)
        self.state['review_customer'] = 'unsaved new edit'
        with patch('work_drafts.update_head', side_effect=OSError('index disk unavailable')):
            with self.assertRaises(OSError): save_draft(self.state, self.store)
        self.assertEqual(self.state['_work_draft_record'], old)
        self.assertEqual([d['id'] for d in available_drafts(self.store)], [old])

    def test_real_streamlit_fresh_session_restores_inputs_and_requires_new_preview_and_approval(self):
        from streamlit.testing.v1 import AppTest
        # The synthetic source costs only200yen; a1000yen markup correctly
        # fails the existing allocation cap. Use a valid output price here.
        self.state['review_target_' + digest(['全体', '上乗せ額', '全体'])] = 0.0
        save_draft(self.state, self.store)
        source = f'''
import os
os.environ['CYCA_DATA_DIR']={self.tmp.name!r}
import streamlit as st
from review_ui import render_review
from work_drafts import render_recovery, autosave
st.session_state.setdefault('username','owner')
render_recovery(st)
try:
    render_review(st)
finally:
    autosave(st)
'''
        at = AppTest.from_string(source).run()
        self.assertEqual(list(at.exception), [])
        at.button(key='work_resume').click().run()
        self.assertEqual(list(at.exception), [])
        self.assertEqual(at.session_state['review_customer'], '架空のお客様 御中')
        self.assertEqual(at.session_state['_review_assistant_rows'][0]['仕様'], '訂正した長い仕様')
        sid = at.session_state['_review_sources'][0]['id']
        at.checkbox(key='review_source_ok_' + sid).check().run()
        self.assertEqual(list(at.exception), [])
        self.assertEqual(at.text_input(key='review_customer').value, '架空のお客様 御中')
        target = next(n for n in at.number_input if n.key.startswith('review_target_'))
        self.assertEqual(target.value, 0.0)
        self.assertTrue(at.button(key='review_approve').disabled)
        self.assertNotIn('_review_approval', at.session_state)
        # Widget commit, then a genuinely new Streamlit session (like reload).
        at.text_input(key='review_customer').set_value('接続前に変えた宛名').run()
        fresh = AppTest.from_string(source).run()
        # There are now two independent works: choose the most recently saved.
        fresh.button(key='work_resume').click().run()
        self.assertEqual(list(fresh.exception), [])
        self.assertEqual(fresh.session_state['review_customer'], '接続前に変えた宛名')
        fresh.checkbox(key='review_source_ok_' + sid).check().run()
        self.assertEqual(list(fresh.exception), [])
        self.assertTrue(fresh.button(key='review_approve').disabled)
        self.assertFalse(fresh.button(key='review_preview').disabled,
                         [(type(e).__name__, getattr(e,'value',None)) for e in list(fresh.warning)+list(fresh.info)+list(fresh.markdown)])
        fresh.button(key='review_preview').click().run()
        self.assertEqual(list(fresh.exception), [])
        agree = next(c for c in fresh.checkbox if c.key.startswith('review_agree_'))
        agree.check().run()
        self.assertFalse(fresh.button(key='review_approve').disabled)


if __name__ == '__main__':
    unittest.main()
