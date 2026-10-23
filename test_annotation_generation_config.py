"""Structured annotations reach the real SDK adapter without changing main OCR."""
import copy
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from google import genai
from google.genai import types

from gemini_resilience import call_gemini_with_retry
from gemini_resilience import GeminiPage
from source_annotations import annotation_generation_config, verify_scanned_annotations


class AnnotationGenerationConfigTest(unittest.TestCase):
    def test_sdk_adapter_preserves_json_schema_required_types_and_extra_key_bans(self):
        data = {'元ファイル': 'a.pdf', '確認ページ': [1], '原本備考': []}
        response = types.HttpResponse(headers={}, body=json.dumps({
            'candidates': [{'content': {'role': 'model', 'parts': [{'text': json.dumps(data)}]},
                            'finishReason': 'STOP'}]}))
        # Match extract_data.py's official genai.Client constructor. All HTTP
        # requests are mocked; no API key, document, or real service is used.
        with genai.Client(api_key='synthetic-offline-key', http_options={'timeout': 90000}) as client:
            with patch.object(client._api_client, 'request', return_value=response) as request:
                result = call_gemini_with_retry(
                    client, ['synthetic annotation text'], primary_model='configured-primary',
                    fallback_models=[], generation_config=annotation_generation_config())
        self.assertEqual(json.loads(result.text), data)
        body = request.call_args.args[2]
        config = body['generationConfig']
        self.assertEqual(config['responseMimeType'], 'application/json')
        schema = config['responseJsonSchema']
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(set(schema['required']), {'元ファイル', '確認ページ', '原本備考'})
        self.assertEqual(set(schema['properties']), set(schema['required']))
        self.assertEqual(schema['properties']['元ファイル']['type'], 'string')
        self.assertEqual(schema['properties']['確認ページ']['items'], {'type': 'integer'})
        item = schema['properties']['原本備考']['items']
        self.assertFalse(item['additionalProperties'])
        self.assertEqual(set(item['required']), {'ページ', '原文'})
        self.assertEqual(item['properties'], {'ページ': {'type': 'integer'}, '原文': {'type': 'string'}})

    def test_main_read_still_supports_the_original_two_argument_adapter(self):
        calls = []
        def original_adapter(*, model, contents):
            calls.append((model, contents))
            return SimpleNamespace(text='main-price-response')
        client = SimpleNamespace(models=SimpleNamespace(generate_content=original_adapter))
        result = call_gemini_with_retry(client, ['original'], primary_model='main', fallback_models=[])
        self.assertEqual(calls, [('main', ['original'])])
        self.assertEqual(result.text, 'main-price-response')

    def test_annotation_schema_is_per_call_and_does_not_mutate_another_request(self):
        first, second = annotation_generation_config(), annotation_generation_config()
        self.assertIsNot(first, second)
        before = copy.deepcopy(second.response_json_schema)
        first.response_json_schema['properties']['元ファイル']['type'] = 'integer'
        first.response_json_schema['required'].clear()
        self.assertEqual(second.response_json_schema, before)

    def test_annotation_failure_keeps_two_attempt_bound_and_does_not_leak_config_to_main_read(self):
        calls = []
        def adapter(*, model, contents, **options):
            calls.append((model, options))
            if model != 'main':
                raise RuntimeError('503 temporary synthetic failure')
            return SimpleNamespace(text='main-price-response')
        client = SimpleNamespace(models=SimpleNamespace(generate_content=adapter))
        original = [{'__source_name': 'a.pdf', '__page_number': 1, '改小計': 100,
                     '原本備考': [{'ページ': 1, '原文': '元の注記'}]}]
        before = copy.deepcopy(original)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'original.pdf'
            path.write_bytes(b'%PDF-1.7 synthetic-only')
            with patch('gemini_resilience.time.sleep') as sleep:
                summaries, _ = verify_scanned_annotations(
                    client, GeminiPage('a.pdf', 1, 1, parts=[]), path, original,
                    primary_model='annotation-primary', fallback_models=['annotation-backup', 'unused-third'],
                    network_gate=threading.BoundedSemaphore(1))
            sleep.assert_not_called()
        self.assertEqual([name for name, _ in calls], ['annotation-primary', 'annotation-backup'])
        self.assertTrue(all(options['config'].response_mime_type == 'application/json'
                            for _, options in calls))
        self.assertEqual(original, before)
        self.assertEqual(summaries[:1], before)
        self.assertEqual(summaries[-1]['annotation_review']['status'], 'needs_review')
        call_gemini_with_retry(client, ['original'], primary_model='main', fallback_models=[])
        self.assertEqual(calls[-1], ('main', {}))

    def test_annotation_call_requests_structured_output_but_source_page_validation_stays_strict(self):
        # Structured output helps shape, but cannot make another original or
        # an out-of-range physical page valid. Preserve the server-side guard.
        for data, reason in (
                ({'元ファイル': 'other.pdf', '確認ページ': [1], '原本備考': []}, 'invalid_source'),
                ({'元ファイル': 'a.pdf', '確認ページ': [1],
                  '原本備考': [{'ページ': 2, '原文': '別ページの注記'}]}, 'invalid_page')):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / 'original.pdf'; path.write_bytes(b'%PDF-1.7 synthetic-only')
                with patch('source_annotations.call_gemini_with_retry', return_value=SimpleNamespace(
                        text=json.dumps(data), model_name='configured')) as call:
                    summaries, _ = verify_scanned_annotations(
                        object(), GeminiPage('a.pdf', 1, 1, parts=[]), path, [],
                        primary_model='configured', fallback_models=[], network_gate=threading.BoundedSemaphore(1))
                config = call.call_args.kwargs['generation_config']
                self.assertIsInstance(config, types.GenerateContentConfig)
                self.assertEqual(config.response_mime_type, 'application/json')
                self.assertEqual(summaries[-1]['原本備考'], [])
                self.assertIn(reason, summaries[-1]['annotation_review']['reasons'])


if __name__ == '__main__':
    unittest.main()
