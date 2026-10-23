"""Printed scope and conditions remain source-bound facts, never invented fees."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import fitz

from estimate_pipeline import (build_intermediate_dataframe, normalize_summary_data,
                               split_extraction_payload)
from estimate_review import review_rows
from gemini_resilience import GeminiPage, build_page_prompt
from source_amounts import read_amounts
from source_extraction import _reread_summaries, extract_job


def detail(section='1階駐車場', amount=100, page=2):
    return dict(品名='柱塗装', 仕様='原本製品TEST-A', 部位=section, 備考='既設面のみ施工',
                数量=1, 単位='㎡', 単価=amount, 金額=amount, 見積元='架空施工社',
                工事種別='塗装工事', __source_name='a.pdf', __page_number=page)


class SourceContextRetentionTest(unittest.TestCase):
    def test_both_prompt_examples_include_row_context_and_page_notes_schema(self):
        for whole in (False, True):
            with self.subTest(whole=whole):
                page = GeminiPage(source_name='a.pdf', page_number=2, total_pages=3,
                                  parts=[], whole_document=whole)
                prompt = build_page_prompt(page, 2)
                example = prompt.split('【出力形式', 1)[1].split('】', 1)[1].strip()
                payload = json.loads(example)
                self.assertTrue({'部位', '備考'} <= payload['detail_data'][0].keys())
                self.assertTrue({'ページ', '原文'} <= payload['summary_data']['原本備考'][0].keys())
                for instruction in ('明細だけのページ', '除く', '使用材料', '補作', '原文'):
                    self.assertIn(instruction, prompt)

    def test_distinct_sections_and_row_remarks_survive_without_price_or_customer_note_changes(self):
        payload = {'page_role': 'detail_page', 'detail_data':
                   [detail(section=part) for part in ('1階駐車場', 'スロープ', '階段')]}
        before = copy.deepcopy(payload)
        _, records = split_extraction_payload(payload, source_name='a.pdf')
        frame, _ = build_intermediate_dataframe(records)
        rows = review_rows(frame, [{'id': 'synthetic', 'name': 'a.pdf', 'pages': 3}])
        self.assertEqual(payload, before)
        self.assertEqual([r['部位'] for r in rows], ['1階駐車場', 'スロープ', '階段'])
        self.assertEqual([r['備考'] for r in rows], ['既設面のみ施工'] * 3)
        self.assertEqual([r['顧客備考'] for r in rows], [''] * 3)
        self.assertEqual([r['原価金額'] for r in rows], [100] * 3)
        self.assertEqual([r['数量'] for r in rows], [1] * 3)
        self.assertEqual([r['原価単価'] for r in rows], [100] * 3)
        self.assertEqual([r['ページ'] for r in rows], [2] * 3)

    def test_scan_notes_only_summary_is_retained_with_exact_file_page_and_verbatim_text(self):
        original_note = '工程：下塗り→中塗り→上塗り\n使用材料：架空製品TEST-A（２回）'
        summary = {'page_role': 'detail_page', 'summary_data': {
            '原本備考': [{'ページ': 3, '原文': original_note},
                         {'ページ': 3, '原文': '混入不可', '元ファイル': 'other.pdf'}]}}
        sources, _ = split_extraction_payload(summary, source_name='a.pdf', page_number=3)
        before = copy.deepcopy(sources)
        metadata = normalize_summary_data(sources + [{
            '__source_name': 'other.pdf', '__page_number': 1,
            '原本備考': [{'ページ': 1, '原文': '別原本の条件'}], '改小計': 999999}])
        self.assertEqual(sources, before)
        own = metadata['summary_sources'][0]
        self.assertEqual(own['原本備考'], [{'元ファイル': 'a.pdf', 'ページ': 3, '原文': original_note}])
        with patch('source_amounts._lines', return_value=iter([])):
            result = read_amounts.__wrapped__('a.pdf', b'scan', metadata)
        self.assertEqual(result['source_notes'], ['3ページ：' + original_note])
        self.assertEqual(result['source_note_records'], own['原本備考'])
        self.assertIsNone(result['net_total'])
        self.assertIsNone(result['gross_total'])

    def test_real_native_pdf_exclusion_without_marker_keeps_its_page_only_and_money(self):
        document = fitz.open()
        page = document.new_page()
        page.insert_text((30, 40), '税抜小計 100000', fontname='japan')
        page.insert_text((30, 60), '消費税 10000', fontname='japan')
        page.insert_text((30, 80), '税込合計 110000', fontname='japan')
        document.new_page().insert_text((30, 40), '既設機器の撤去を除く', fontname='japan')
        document.new_page().insert_text((30, 40), '別ページの品名', fontname='japan')
        data = document.tobytes()
        document.close()
        result = read_amounts.__wrapped__('a.pdf', data)
        self.assertEqual([result[k] for k in ('net_total', 'tax', 'gross_total')], [100000, 10000, 110000])
        self.assertEqual(result['source_note_records'], [
            {'元ファイル': 'a.pdf', 'ページ': 2, '原文': '既設機器の撤去を除く'}])
        self.assertEqual(result['source_notes'], ['2ページ：既設機器の撤去を除く'])

    def test_reread_retains_old_and_new_notes_without_moving_old_money_or_other_sources(self):
        previous = [{'__source_name': 'a.pdf', '__page_number': 2,
                     '原本備考': [{'ページ': 2, '原文': '配線工事を除く'}]},
                    {'__source_name': 'a.pdf', '__page_number': 3, '改小計': 9999,
                     '原本備考': [{'ページ': 3, '原文': '材料：架空製品TEST-A'}]},
                    {'__source_name': 'other.pdf', '__page_number': 3,
                     '原本備考': [{'ページ': 3, '原文': '別会社の条件'}]}]
        reread = [{'__source_name': 'a.pdf', '__page_number': 2, '__page_role': 'detail_page',
                   '原本備考': [{'ページ': 2, '原文': '施工は平日のみ'}]}]
        before = copy.deepcopy((previous, reread))
        merged = _reread_summaries(previous, reread)
        self.assertEqual((previous, reread), before)
        self.assertEqual([n['原文'] for n in merged[0]['原本備考']], ['施工は平日のみ', '配線工事を除く'])
        page3 = next(s for s in merged if s['__source_name'] == 'a.pdf' and s['__page_number'] == 3)
        self.assertNotIn('改小計', page3)
        metadata = normalize_summary_data(merged)
        with patch('source_amounts._lines', return_value=iter([])):
            result = read_amounts.__wrapped__('a.pdf', b'scan', metadata)
        self.assertEqual(len(result['source_notes']), 3)
        self.assertFalse(any('別会社' in text for text in result['source_notes']))
        self.assertIsNone(result['net_total'])

    def test_unknown_note_page_is_preserved_without_assuming_first_page(self):
        previous = [{'__source_name': 'a.pdf', '原本備考': ['原本の条件（ページ不明）']}]
        merged = _reread_summaries(previous, [{'__source_name': 'a.pdf', '__page_number': 1}])
        metadata = normalize_summary_data(merged)
        with patch('source_amounts._lines', return_value=iter([])):
            result = read_amounts.__wrapped__('a.pdf', b'scan', metadata)
        self.assertEqual(result['source_note_records'][0]['ページ'], None)
        self.assertEqual(result['source_notes'], ['ページ未確認：原本の条件（ページ不明）'])

    def test_whole_pdf_then_scan_retry_retains_footer_and_row_context_without_extra_fees(self):
        cover = {'__page_number': 1, '__page_role': 'cover_summary_page',
                 '見積元': '架空施工社', '改小計': 100}
        footer = {'__page_number': 3, '__page_role': 'detail_page',
                  '原本備考': [{'ページ': 3, '原文': '工程：下塗り→上塗り。材料：架空TEST-A'}]}
        responses = [
            {'summary_data': [cover, footer], 'detail_data': [detail(amount=90)]},
            {'page_role': 'cover_summary_page', 'summary_data': cover, 'detail_data': []},
            {'page_role': 'detail_page', 'detail_data': [detail()]},
            {'page_role': 'detail_page', 'detail_data': []}]
        page = GeminiPage(source_name='a.pdf', page_number=1, total_pages=3,
                          parts=[], whole_document=True)
        images = [GeminiPage(source_name='a.pdf', page_number=i, total_pages=3, parts=[])
                  for i in (1, 2, 3)]
        with patch('source_extraction.call_gemini_with_retry', side_effect=[
                SimpleNamespace(text=json.dumps(r), model_name='synthetic') for r in responses]), \
                patch('source_extraction._render_pdf_pages_as_images', return_value=images), \
                patch('source_extraction.reconcile_native_pdf', side_effect=lambda p, r, s: (r, s)):
            summaries, rows, _, errors = extract_job(None, page, 'not-opened.pdf', 1,
                                                    primary_model='synthetic', fallback_models=[])
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['金額'], 100)
        self.assertEqual(rows[0]['部位'], '1階駐車場')
        self.assertEqual(rows[0]['備考'], '既設面のみ施工')
        metadata = normalize_summary_data(summaries)
        with patch('source_amounts._lines', return_value=iter([])):
            result = read_amounts.__wrapped__('a.pdf', b'scan', metadata)
        self.assertEqual(result['net_total'], 100)
        self.assertEqual(result['source_notes'], ['3ページ：工程：下塗り→上塗り。材料：架空TEST-A'])


if __name__ == '__main__':
    unittest.main()
