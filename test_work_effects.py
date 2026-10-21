from types import SimpleNamespace
import unittest
from work_effects import cue, confirmed_sources
from product_design import ready_panel, loading_panel


class WorkEffectsTest(unittest.TestCase):
    def test_events_are_bounded_and_contain_no_quote_data(self):
        st = SimpleNamespace(session_state={})
        for _ in range(20):
            cue(st, 'preview')
        events = st.session_state['_effects_events']
        self.assertEqual(len(events), 12)
        self.assertEqual(len({e['id'] for e in events}), 12)
        self.assertTrue(all(set(e) == {'id', 'stage', 'at'} for e in events))
        with self.assertRaises(ValueError):
            cue(st, 'unknown')

    def test_confirm_sound_is_a_transition_not_a_rerun_or_unchecked_source(self):
        st = SimpleNamespace(session_state={})
        a = [{'id': 'a', 'confirmed': False}, {'id': 'b', 'confirmed': False}]
        confirmed_sources(st, a)
        a[0]['confirmed'] = True
        confirmed_sources(st, a)
        self.assertNotIn('_effects_events', st.session_state)
        a[1]['confirmed'] = True
        confirmed_sources(st, a)
        confirmed_sources(st, a)
        self.assertEqual(len(st.session_state['_effects_events']), 1)
        a[0]['confirmed'] = False
        confirmed_sources(st, a)
        self.assertEqual(len(st.session_state['_effects_events']), 1)

    def test_token_can_deduplicate_success_events(self):
        st = SimpleNamespace(session_state={})
        cue(st, 'output', 'one')
        cue(st, 'output', 'one')
        self.assertEqual(len(st.session_state['_effects_events']), 1)
        cue(st, 'output', 'two')
        self.assertEqual(len(st.session_state['_effects_events']), 2)

    def test_document_names_are_escaped_and_loading_uses_real_count(self):
        self.assertNotIn('<script>', ready_panel(['<script>bad</script>.pdf']))
        self.assertIn('3件の原本', loading_panel(3))
        self.assertIn('2ページ目', loading_panel(3, 2))
