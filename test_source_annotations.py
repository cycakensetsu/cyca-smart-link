"""A second scan pass may retain quoted annotations, never financial facts."""
import copy
import hashlib
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gemini_resilience import GeminiPage
from source_annotations import verify_scanned_annotations
from source_extraction import extract_job


PDF = b'%PDF-1.7\nsynthetic scan source bytes; not parsed by these network tests'


def payload(name='a.pdf', pages=(1, 2), notes=None):
    return {'元ファイル': name, '確認ページ': list(pages), '原本備考': notes or []}


class SourceAnnotationsTest(unittest.TestCase):
    def run_check(self, response, *, summaries=None, total_pages=2):
        page = GeminiPage('a.pdf', 1, total_pages, parts=['existing page image'])
        originals = summaries if summaries is not None else [
            {'__source_name': 'a.pdf', '__page_number': 1, '改小計': 100,
             '原本備考': [{'ページ': 1, '原文': '主読取で得た条件'}]}]
        before = copy.deepcopy(originals)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'original.pdf'
            path.write_bytes(PDF)
            kwargs = {'side_effect': response} if isinstance(response, Exception) else {
                'return_value': SimpleNamespace(text=response if isinstance(response, str)
                                                else json.dumps(response), model_name='annotations')}
            client = object()
            with patch('source_annotations.call_gemini_with_retry', **kwargs) as call:
                actual, model = verify_scanned_annotations(
                    client, page, path, originals, primary_model='configured-primary',
                    fallback_models=['configured-backup', 'unused-third'],
                    network_gate=threading.BoundedSemaphore(1))
            self.assertEqual(path.read_bytes(), PDF)
        self.assertEqual(originals, before)
        self.assertEqual(actual[:len(before)], before)
        return actual, model, call, client

    def test_complete_pass_appends_page_quoted_footer_and_remarks_keeps_main_notes_and_original_bytes(self):
        text = '施工工程：工程A→工程B\n使用材料：架空製品TEST-A'
        actual, model, call, client = self.run_check(payload(notes=[
            {'ページ': 2, '原文': text}, {'ページ': 2, '原文': '架空部材A　H1,100　別紙参照'}]))
        summary = actual[-1]
        self.assertEqual(summary['原本備考'][0], {'元ファイル': 'a.pdf', 'ページ': 2, '原文': text})
        self.assertEqual(summary['annotation_review']['status'], 'completed')
        self.assertEqual(summary['annotation_review']['checked_pages'], [1, 2])
        self.assertEqual(summary['annotation_review']['sha256'], hashlib.sha256(PDF).hexdigest())
        self.assertGreaterEqual(summary['annotation_review']['seconds'], 0)
        self.assertEqual(model, 'annotations')
        self.assertIs(call.call_args.args[0], client)
        self.assertEqual(call.call_args.args[1][0].inline_data.data, PDF)
        self.assertEqual(call.call_args.kwargs['max_attempts_per_model'], 1)
        self.assertEqual(call.call_args.kwargs['fallback_models'], ['configured-backup'])

    def test_malformed_or_failed_pass_records_warning_without_erasing_main_notes(self):
        for bad in ('invalid-json', [], {'unexpected': True}, RuntimeError('private-provider-secret')):
            with self.subTest(bad_type=type(bad).__name__):
                actual, _, _, _ = self.run_check(bad)
                self.assertEqual(actual[-1]['annotation_review']['status'], 'needs_review')
                self.assertTrue(actual[-1]['annotation_review']['reasons'])
                self.assertEqual(actual[-1]['原本備考'], [])
                self.assertNotIn('private-provider-secret', repr(actual))

    def test_foreign_original_and_invalid_or_unchecked_pages_never_enter_source_notes(self):
        foreign = payload('other.pdf', notes=[{'ページ': 1, '原文': '他原本の条件'}])
        actual, _, _, _ = self.run_check(foreign)
        self.assertEqual(actual[-1]['原本備考'], [])
        for page in (0, 3, True, '2', 2.0):
            with self.subTest(page=page):
                actual, _, _, _ = self.run_check(payload(notes=[{'ページ': page, '原文': '誤ったページの条件'}]))
                self.assertEqual(actual[-1]['原本備考'], [])
                self.assertIn('invalid_page', actual[-1]['annotation_review']['reasons'])
        actual, _, _, _ = self.run_check(payload(pages=(1,), notes=[{'ページ': 2, '原文': '未確認ページ'}]))
        self.assertEqual(actual[-1]['原本備考'], [])
        self.assertIn('incomplete_pages', actual[-1]['annotation_review']['reasons'])

    def test_valid_quotations_survive_partial_warning_and_financial_fields_are_never_applied(self):
        data = payload(notes=[{'ページ': 2, '原文': '印字された条件'},
                              {'元ファイル': 'other.pdf', 'ページ': 2, '原文': '他社の条件'},
                              {'ページ': 1, '原文': '不正な価格提案', '金額': 999999}])
        data.update(detail_data=[{'品名': '架空の追加費用', '金額': 999999}], 改小計=999999)
        actual, _, _, _ = self.run_check(data)
        self.assertEqual(actual[-1]['原本備考'], [{'元ファイル': 'a.pdf', 'ページ': 2, '原文': '印字された条件'}])
        self.assertEqual(actual[-1]['annotation_review']['status'], 'needs_review')
        self.assertEqual(actual[0]['改小計'], 100)
        self.assertNotIn('detail_data', actual[-1])
        self.assertNotIn('改小計', actual[-1])

    def test_native_pdf_following_scan_pages_and_standalone_images_do_not_add_calls(self):
        original = [{'__source_name': 'a.pdf', '改小計': 100}]
        jobs = [GeminiPage('a.pdf', 1, 2, parts=[], whole_document=True),
                GeminiPage('a.pdf', 2, 2, parts=[]),
                GeminiPage('a.jpg', 1, 1, parts=[])]
        with patch('source_annotations.call_gemini_with_retry') as call:
            for job in jobs:
                actual, model = verify_scanned_annotations(
                    None, job, 'never-opened', original, primary_model='configured',
                    fallback_models=[], network_gate=threading.BoundedSemaphore(1))
                self.assertIs(actual, original)
                self.assertIsNone(model)
        call.assert_not_called()

    def test_integration_failure_preserves_price_rows_and_does_not_become_a_blocking_read_error(self):
        main = {'summary_data': {'改小計': 100}, 'detail_data': [
            {'品名': '原本の機器', '仕様': 'TEST-A', '数量': 2, '単位': '台', '単価': 50, '金額': 100,
             '見積元': '架空社', '工事種別': '設備工事', '備考': '元の行備考'}]}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'a.pdf'; path.write_bytes(PDF)
            page = GeminiPage('a.pdf', 1, 1, parts=['existing page image'])
            with patch('source_extraction.call_gemini_with_retry', return_value=SimpleNamespace(
                    text=json.dumps(main), model_name='main')), \
                    patch('source_annotations.call_gemini_with_retry', side_effect=RuntimeError('secret')):
                summaries, rows, models, errors = extract_job(
                    object(), page, path, 1, primary_model='configured', fallback_models=[],
                    network_gate=threading.BoundedSemaphore(1))
        self.assertEqual(errors, [])
        self.assertEqual(models, ['main'])
        self.assertEqual(len(rows), 1)
        for key, value in main['detail_data'][0].items():
            self.assertEqual(rows[0][key], value)
        self.assertEqual(summaries[0]['改小計'], 100)
        self.assertEqual(summaries[-1]['annotation_review']['status'], 'needs_review')

    def test_main_and_annotation_calls_share_gate_and_each_worker_reuses_its_own_client(self):
        lock = threading.Lock(); active = 0; peak = 0; client_active = set(); calls = []
        gate = threading.BoundedSemaphore(2)
        def fake(client, contents, **kwargs):
            nonlocal active, peak
            with lock:
                self.assertNotIn(id(client), client_active)
                client_active.add(id(client)); active += 1; peak = max(peak, active)
                calls.append((id(client), kwargs['page_number']))
            try:
                time.sleep(.01)
                name = kwargs['source_name']
                response = (payload(name, pages=(1,), notes=[{'ページ': 1, '原文': '原本の条件'}])
                            if kwargs['page_number'] is None else
                            {'detail_data': [{'品名': '原本行', '数量': 1, '単位': '式', '単価': 100,
                                              '金額': 100, '見積元': '架空社', '工事種別': '設備工事'}]})
                return SimpleNamespace(text=json.dumps(response), model_name='configured')
            finally:
                with lock:
                    client_active.remove(id(client)); active -= 1
        with tempfile.TemporaryDirectory() as temp:
            jobs = []
            for i in range(4):
                path = Path(temp) / f'{i}.pdf'; path.write_bytes(PDF)
                jobs.append((object(), GeminiPage(f'{i}.pdf', 1, 1, parts=['image']), path))
            with patch('source_extraction.call_gemini_with_retry', side_effect=fake), \
                    patch('source_annotations.call_gemini_with_retry', side_effect=fake):
                with ThreadPoolExecutor(max_workers=4) as pool:
                    results = list(pool.map(lambda args: extract_job(
                        *args, 4, primary_model='configured', fallback_models=[], network_gate=gate), jobs))
        self.assertEqual(peak, 2)
        self.assertEqual(len(calls), 8)
        for client, _, _ in jobs:
            self.assertEqual(sorted(page is None for cid, page in calls if cid == id(client)), [False, True])
        for i, (summaries, rows, _, errors) in enumerate(results):
            self.assertEqual(errors, [])
            self.assertEqual(rows[0]['金額'], 100)
            self.assertTrue(all(s['__source_name'] == f'{i}.pdf' for s in summaries))
            self.assertEqual(summaries[-1]['annotation_review']['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
