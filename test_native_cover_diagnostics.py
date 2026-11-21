"""Conditional regressions, not a claim to contain the production AI payload."""
import copy
import unittest
from unittest.mock import patch

from estimate_pipeline import split_extraction_payload
from extraction_batch import reconcile_document_results
from source_sections import reconcile_native_cover_sections
from source_table import read_native_cover_groups
from test_cover_double_counting import fixture, original_pdf, NAME


class NativeCoverDiagnostics(unittest.TestCase):
    def test_explicit_flat_summary_role_is_used_only_with_full_physical_proof(self):
        rows, summaries = fixture()
        payload = {k: v for k, v in summaries[0].items() if not k.startswith('__')}
        payload.update(page_role='cover_summary_page', detail_data=rows)
        summaries, rows = split_extraction_payload(payload, source_name=NAME, page_number=1)
        self.assertNotIn('__page_role', summaries[0])
        self.assertEqual(summaries[0]['page_role'], 'cover_summary_page')
        # The parser knows only the page passed to it. Restore the test's body
        # page separately; do not claim it came from an observed AI envelope.
        for row in rows[5:]:
            row['__page_number'] = 2
        output, _ = reconcile_native_cover_sections(rows, summaries, NAME, original_pdf())
        self.assertEqual(len(output), 15)

    def test_nan_in_unrelated_field_does_not_break_worker_row_binding(self):
        rows, summaries = fixture()
        for row in rows:
            row['optional_test_field'] = float('nan')
        other = dict(rows[0], __source_name='another.pdf')
        source = dict(name=NAME, pages=2)
        output, _ = reconcile_document_results([source], [other, *rows], summaries,
                                               original_reader=lambda _: original_pdf())
        selected = [r for r in output if r['__source_name'] == NAME]
        self.assertEqual(len(selected), 15)
        self.assertEqual(sum(r['金額'] for r in selected), 545000)
        self.assertEqual(output[0]['__source_name'], 'another.pdf')
        self.assertEqual([r['金額'] for r in selected[:3]], [30000, 20000, -5000])
        self.assertEqual(selected[3]['部位'], '補修作業 A')
        self.assertEqual(selected[9]['部位'], '補修作業 B')
        self.assertEqual(source['aggregate_check_diagnostics'][0]['status'], 'matched')

    def test_missing_role_or_incoherent_totals_keep_all_rows_with_reasons(self):
        rows, summaries = fixture()
        for field, value, reason in (
            ('__page_role', None, 'cover_classification_missing'),
            ('改小計', None, 'cover_page_or_totals_unverified'),
            ('消費税', 1, 'cover_page_or_totals_unverified'),
        ):
            with self.subTest(field=field):
                ss = copy.deepcopy(summaries)
                ss[0][field] = value
                result = read_native_cover_groups(original_pdf(), NAME, rows, ss, explain=True)
                self.assertEqual(result['proofs'], [])
                self.assertEqual(result['diagnostics'][0]['reason'], reason)
                self.assertEqual(set(result['diagnostics'][0]), {'status', 'reason', 'page'})
                output, _ = reconcile_native_cover_sections(rows, ss, NAME, original_pdf())
                self.assertEqual(len(output), 17)

    def test_incomplete_or_same_money_different_work_is_not_approved(self):
        rows, summaries = fixture()
        for data in (original_pdf(unrelated=True), original_pdf(unlisted=True),
                     original_pdf(tail_unit='組'), original_pdf(tail_fee=True)):
            with self.subTest(size=len(data)):
                result = read_native_cover_groups(data, NAME, rows, summaries, explain=True)
                self.assertEqual(result['proofs'], [])
                self.assertEqual(result['diagnostics'][0]['reason'], 'detail_evidence_incomplete')
                output, _ = reconcile_native_cover_sections(rows, summaries, NAME, data)
                self.assertEqual(len(output), 17)

    def test_worker_error_is_retained_as_safe_reason_without_exception_text(self):
        rows, summaries = fixture()
        source = dict(name=NAME, pages=2)
        with patch('source_table.read_native_cover_groups', side_effect=ValueError('PRIVATE TEST TEXT')):
            output, _ = reconcile_document_results([source], rows, summaries,
                                                   original_reader=lambda _: b'invalid')
        self.assertEqual(len(output), 17)
        self.assertEqual(source['aggregate_check_diagnostics'], [dict(
            status='unverified', reason='original_verification_unavailable', page=None)])

    def test_invalid_critical_value_and_duplicate_row_keep_every_record(self):
        rows, summaries = fixture()
        for bad in ('critical', 'duplicate'):
            changed = copy.deepcopy(rows)
            if bad == 'critical':
                changed[5]['数量'] = float('nan')
            else:
                changed.append(dict(changed[5]))
            output, _ = reconcile_native_cover_sections(changed, summaries, NAME, original_pdf())
            self.assertEqual(len(output), len(changed))

    def test_unknown_cover_quantities_are_not_invented_when_aggregates_are_proved(self):
        rows, summaries = fixture()
        for row in rows[:4]:
            row.update(数量=None, 単位=None, 単価=None)
        output, _ = reconcile_native_cover_sections(rows, summaries, NAME, original_pdf())
        self.assertEqual(len(output), 15)
        for row in output[:2]:
            self.assertIsNone(row['数量'])
            self.assertIsNone(row['単位'])
            self.assertIsNone(row['単価'])
        self.assertEqual(output[2]['金額'], -5000)

    def test_beginner_card_shows_japanese_reason_and_still_requires_confirmation(self):
        from streamlit.testing.v1 import AppTest
        app="""
import streamlit as st
from source_review_ui import _source_exception_card
rows=[dict(行ID='fictional',品名='架空の工事',原価金額=100,採用=True)]
_source_exception_card(st, rows, dict(id='fictional',net_total=90,
    aggregate_check_diagnostics=[dict(status='unverified',reason='cover_classification_missing',page=None)]))
"""
        at=AppTest.from_string(app).run()
        self.assertFalse(at.exception)
        self.assertTrue(any('自動で確認できなかった点：表紙の区分' in t.value for t in at.caption))
        self.assertTrue(at.button(key='review_exception_apply_fictional').disabled)
        self.assertFalse(any('cover_classification_missing' in t.value for t in at.caption))

    def test_flat_role_does_not_activate_amount_only_legacy_removal(self):
        rows, summaries = fixture()
        payload = {k: v for k, v in summaries[0].items() if not k.startswith('__')}
        payload['page_role'] = 'cover_summary_page'
        summaries, _ = split_extraction_payload(payload, source_name=NAME, page_number=1)
        for index, row in enumerate(rows[5:]):
            row['工事種別'] = '補修作業 A' if index < 6 else '補修作業 B'
        output, _ = reconcile_document_results([dict(name=NAME, pages=2)], rows, summaries,
            original_reader=lambda _: original_pdf(unrelated=True))
        self.assertEqual(len(output), 17)
        self.assertTrue(any(r['品名'] == '補修作業 A' and r['__page_number'] == 1 for r in output))
