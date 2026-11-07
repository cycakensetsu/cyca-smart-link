"""Optional private integration test against the user's original quote.

Set CYCA_REAL_QUOTE_PDF and CYCA_REAL_QUOTE_EXPECTED_JSON to private local
files. The JSON needs cost, markup, item, quantity and unit_cost. No customer
document or quote facts are checked in or sent to an AI service.
"""
import copy
import hashlib
import json
import os
import tempfile
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from estimate_pipeline import build_intermediate_dataframe
from estimate_review import (OriginalVault, Target, approve, build_approved_estimate,
                             build_draft, preview_allowed, review_rows)
from source_table import read_native_tables, reconcile_native_pdf
from gemini_resilience import GeminiPage
from source_extraction import extract_job


class RealQuoteRegressionTest(unittest.TestCase):
    def setUp(self):
        path = os.environ.get('CYCA_REAL_QUOTE_PDF')
        expected_path = os.environ.get('CYCA_REAL_QUOTE_EXPECTED_JSON')
        if not path or not os.path.isfile(path) or not expected_path or not os.path.isfile(expected_path):
            self.skipTest('private real-quote PDF and expected JSON are not both set')
        with open(expected_path, encoding='utf-8') as stream:
            self.expected = json.load(stream)
        self.cost = int(self.expected['cost'])
        self.markup = int(self.expected['markup'])
        self.item = str(self.expected['item'])
        self.path = path
        with open(path, 'rb') as stream:
            self.original = stream.read()
        self.source_name = os.path.basename(path)
        self.vault_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.vault_dir.cleanup)
        self.vault = OriginalVault(self.vault_dir.name, 'real-quote-test')
        self.source = self.vault.preserve(self.source_name, self.original)
        self.assertEqual(self.source['id'], hashlib.sha256(self.original).hexdigest())
        native, subtotals, _ = read_native_tables(path)
        self.assertEqual(len(native), self.expected['row_count'])
        self.assertEqual(int(sum(row['金額'] for row in native)), self.cost)
        self.assertIn(self.cost, subtotals)
        # Simulate the OCR response. The native table must replace its rows
        # only after the printed subtotal and row facts reconcile.
        ocr = [dict(row, 見積元='原本発行者', __source_name=self.source_name)
               for row in native]
        checked, _ = reconcile_native_pdf(path, ocr, [])
        self.assertEqual(len(checked), self.expected['row_count'])
        self.assertTrue(all(row.get('抽出元テキスト範囲') for row in checked))
        detail, _ = build_intermediate_dataframe(checked)
        self.source.update(pages=1, tax_basis='税抜', tax_rate=.10,
                           confirmed=True, net_total=self.cost)
        self.rows = review_rows(detail, [self.source])
        self.meta = {'宛名': '検証用 御中', '工事名称': '原本照合検証', '見積日': '2026/11/07'}

    def draft(self, markup, choice=None, edited=None):
        return build_draft(self.rows, edited or copy.deepcopy(self.rows), [self.source],
                           [Target('全体', '全体', '上乗せ額', markup)], 0, self.meta,
                           pricing_choice=choice)

    def test_original_quote_has_balanced_natural_prices_and_exports(self):
        draft = self.draft(self.markup)
        self.assertEqual(draft['unresolved'], [])
        self.assertFalse(draft['profit_plan']['requires_confirmation'])
        self.assertEqual(draft['payload']['合計']['仕入原価'], self.cost)
        self.assertEqual(draft['payload']['合計']['税抜売価'], self.cost + self.markup)
        self.assertEqual(draft['payload']['合計']['税込売価'],
                         self.cost + self.markup + int((self.cost + self.markup) * .10))
        self.assertEqual(draft['payload']['値引き'], 0)
        block = next(row for row in draft['payload']['計算明細']
                     if self.item in row['品名'])
        self.assertEqual(block['数量'], self.expected['quantity'])
        self.assertEqual(block['原価単価'], self.expected['unit_cost'])
        self.assertLessEqual(block['見積単価'], 2 * self.expected['unit_cost'])
        self.assertLess(block['上乗せ額'] / self.markup, .10)
        result = build_approved_estimate(draft, approve(draft, '原本検証'))
        self.assertTrue(result.ok, result.errors)
        self.assertTrue(result.data.startswith(b'PK'))
        self.assertTrue(result.pdf_data.startswith(b'%PDF'))

    def test_odd_target_requires_choice_and_invalidates_stale_choice(self):
        pending = self.draft(self.markup + 1)
        self.assertFalse(preview_allowed(pending))
        with self.assertRaises(ValueError):
            approve(pending, '原本検証')
        options = {option['id']: option for option in pending['profit_plan']['options']}
        self.assertEqual(set(options), {'no_discount', 'rounding_discount'})
        self.assertEqual(options['no_discount']['discount'], 0)
        self.assertGreater(options['rounding_discount']['markup'], self.markup + 1)
        self.assertLess(options['rounding_discount']['discount'], 0)
        self.assertTrue(all(option['final_profit'] == self.markup + 1 for option in options.values()))
        choice = {'signature': pending['profit_plan']['signature'], 'id': 'rounding_discount'}
        approved = self.draft(self.markup + 1, choice=choice)
        self.assertEqual(approved['unresolved'], [])
        self.assertEqual(approved['payload']['値引き'], options['rounding_discount']['discount'])
        self.assertEqual(approved['payload']['合計']['粗利益'], self.markup + 1)
        stale = self.draft(self.markup + 2, choice=choice)
        self.assertIn('利益配分案', [issue['種類'] for issue in stale['unresolved']])

    def test_original_upload_extract_job_reconciles_noisy_ai_rows_without_network(self):
        native, _, _ = read_native_tables(self.path)
        noisy = [dict(row, 見積元='原本発行者', 工事種別='原本照合') for row in native]
        item_index = next(index for index, row in enumerate(noisy) if self.item in row['品名'])
        noisy[item_index]['品名'] = self.item + ' 読み間違い'
        note_index = next(index for index, row in enumerate(noisy)
                          if self.expected['note_item'] in row['品名'])
        noisy[note_index]['品名'] = self.expected['note_item']
        noisy[note_index]['備考'] = self.expected['note_text']
        job = GeminiPage(source_name=self.source_name, page_number=1,
                         total_pages=1, parts=[], whole_document=True)
        reply = SimpleNamespace(text=json.dumps({'detail_data': noisy}, ensure_ascii=False),
                                model_name='offline-mock')
        with patch('source_extraction.call_gemini_with_retry', return_value=reply), \
             patch('source_annotations.verify_scanned_annotations',
                   side_effect=lambda *args, **kwargs: (args[3], None)), \
             patch('source_extraction._render_pdf_pages_as_images') as fallback:
            _, records, _, errors = extract_job(None, job, self.path, 1,
                primary_model='offline-mock', fallback_models=[], network_gate=nullcontext())
        fallback.assert_not_called()
        self.assertEqual(errors, [])
        self.assertEqual(len(records), self.expected['row_count'])
        self.assertEqual(sum(row['金額'] for row in records), self.cost)
        self.assertIn(self.item, [row['品名'] for row in records])
        self.assertFalse(any('読み間違い' in row['品名'] for row in records))
        noted = next(row for row in records if row['品名'].startswith(self.expected['note_item']))
        self.assertEqual(noted['備考'], self.expected['note_text'])
