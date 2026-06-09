import unittest
from unittest.mock import patch

from gemini_resilience import GeminiTemporaryUnavailable, call_gemini_with_retry


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


if __name__ == "__main__":
    unittest.main()
