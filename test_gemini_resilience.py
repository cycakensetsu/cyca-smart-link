import os
import tempfile
import types as standard_types
import unittest
from unittest.mock import patch

from gemini_resilience import (
    GeminiTemporaryUnavailable,
    build_page_prompt,
    call_gemini_with_retry,
    get_configured_gemini_models,
    prepare_upload_for_gemini_pages,
)


class _FakeResponse:
    text = '[{"品名":"テスト","数量":1,"単位":"式","単価":100,"金額":100}]'


class _FakeModels:
    def __init__(self, fail_by_model):
        self.fail_by_model = dict(fail_by_model)
        self.calls = []

    def generate_content(self, *, model, contents):
        self.calls.append(model)
        remaining_failures = self.fail_by_model.get(model, 0)
        if remaining_failures > 0:
            self.fail_by_model[model] = remaining_failures - 1
            raise RuntimeError("503 UNAVAILABLE: This model is currently experiencing high demand.")
        return _FakeResponse()


class _FakeClient:
    def __init__(self, fail_by_model):
        self.models = _FakeModels(fail_by_model)


class GeminiRetryTest(unittest.TestCase):
    def test_503_falls_back_to_next_model(self):
        client = _FakeClient({"gemini-2.5-flash": 2})
        retries = []
        model_starts = []

        with patch("gemini_resilience.time.sleep", lambda _: None):
            result = call_gemini_with_retry(
                client,
                ["dummy"],
                primary_model="gemini-2.5-flash",
                fallback_models=["gemini-2.0-flash"],
                max_attempts_per_model=2,
                on_model_start=lambda model, page: model_starts.append((model, page)),
                on_retry=lambda model, retry, delay, code, page: retries.append((model, retry, code, page)),
            )

        self.assertEqual(result.model_name, "gemini-2.0-flash")
        self.assertEqual(client.models.calls, ["gemini-2.5-flash", "gemini-2.5-flash", "gemini-2.0-flash"])
        self.assertEqual(model_starts, [("gemini-2.5-flash", None), ("gemini-2.0-flash", None)])
        self.assertEqual(retries[0][0], "gemini-2.5-flash")
        self.assertEqual(retries[0][2], 503)

    def test_all_models_busy_raises_user_safe_exception(self):
        client = _FakeClient({"gemini-2.5-flash": 1, "gemini-2.0-flash": 1})

        with patch("gemini_resilience.time.sleep", lambda _: None):
            with self.assertRaises(GeminiTemporaryUnavailable):
                call_gemini_with_retry(
                    client,
                    ["dummy"],
                    primary_model="gemini-2.5-flash",
                    fallback_models=["gemini-2.0-flash"],
                    max_attempts_per_model=1,
                )

    def test_retired_model_404_skips_immediately_to_fallback(self):
        client = _FakeClient({})
        original_generate = client.models.generate_content

        def generate_content(*, model, contents):
            if model == "gemini-2.0-flash":
                client.models.calls.append(model)
                raise RuntimeError(
                    "404 NOT_FOUND: This model models/gemini-2.0-flash is no longer available."
                )
            return original_generate(model=model, contents=contents)

        client.models.generate_content = generate_content
        result = call_gemini_with_retry(
            client,
            ["dummy"],
            primary_model="gemini-2.0-flash",
            fallback_models=["gemini-3.6-flash"],
        )

        self.assertEqual(result.model_name, "gemini-3.6-flash")
        self.assertEqual(client.models.calls, ["gemini-2.0-flash", "gemini-3.6-flash"])

    def test_retired_config_is_replaced_and_deduplicated(self):
        with patch.dict(
            "os.environ",
            {
                "GEMINI_PRIMARY_MODEL": "models/gemini-2.0-flash",
                "GEMINI_FALLBACK_MODELS": "gemini-2.0-flash,gemini-1.5-flash,gemini-3.5-flash-lite",
            },
            clear=False,
        ):
            primary, fallbacks = get_configured_gemini_models()

        self.assertEqual(primary, "gemini-3.5-flash-lite")
        self.assertEqual(fallbacks, [])

    def test_pdf_is_sent_as_one_whole_document_request(self):
        class _FakePdfDocument:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def __len__(self):
                return 3

        fake_fitz = standard_types.SimpleNamespace(open=lambda _: _FakePdfDocument())

        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = os.path.join(temp_dir, "sample.pdf")
            with open(pdf_path, "wb") as pdf_file:
                pdf_file.write(b"%PDF-1.4 test")

            with patch.dict("sys.modules", {"fitz": fake_fitz}):
                jobs = prepare_upload_for_gemini_pages(pdf_path, "sample.pdf")

        self.assertEqual(len(jobs), 1)
        self.assertTrue(jobs[0].whole_document)
        self.assertEqual(jobs[0].total_pages, 3)
        self.assertEqual(len(jobs[0].parts), 1)

        prompt = build_page_prompt(jobs[0], 1)
        self.assertIn("全3ページ", prompt)
        self.assertIn("内訳合計", prompt)

    def test_default_retry_budget_is_two_attempts_per_model(self):
        client = _FakeClient({"gemini-3.5-flash-lite": 2, "gemini-3.6-flash": 0})

        with patch("gemini_resilience.time.sleep", lambda _: None):
            result = call_gemini_with_retry(
                client,
                ["dummy"],
                primary_model="gemini-3.5-flash-lite",
                fallback_models=["gemini-3.6-flash"],
            )

        self.assertEqual(result.model_name, "gemini-3.6-flash")
        self.assertEqual(
            client.models.calls,
            ["gemini-3.5-flash-lite", "gemini-3.5-flash-lite", "gemini-3.6-flash"],
        )


if __name__ == "__main__":
    unittest.main()
