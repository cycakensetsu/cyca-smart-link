from concurrent.futures import ThreadPoolExecutor
import importlib
from pathlib import Path
from types import SimpleNamespace
import threading
import tempfile
import time
import unittest

import gemini_request_gate as gate


class RequestPacingTest(unittest.TestCase):
    def test_ten_clients_share_one_request_budget(self):
        # Ten independent clients stand for ten sessions. Observe the actual
        # provider entry, not just reservations in the scheduler.
        pacer = gate.RequestPacer(window_seconds=.24)
        starts, lock = [], threading.Lock()
        def generate_content(**kwargs):
            with lock:
                starts.append(time.monotonic())
            return kwargs['contents']
        def user(index):
            client = gate.PacedGeminiClient(SimpleNamespace(
                models=SimpleNamespace(generate_content=generate_content)), pacer=pacer)
            return [client.models.generate_content(model='gemini-3.5-flash-lite',
                contents=(index, n)) for n in range(3)]
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(user, range(10)))
        self.assertEqual(results, [[(i, n) for n in range(3)] for i in range(10)])
        self.assertEqual(len(starts), 30)
        self.assertGreaterEqual(min(b-a for a,b in zip(starts, starts[1:])), .019)
        self.assertTrue(all(sum(t <= other < t+.24 for other in starts) <= 12 for t in starts))

    def test_fallback_has_its_own_smaller_budget_and_cancel_does_not_block_next_user(self):
        pacer = gate.RequestPacer(window_seconds=.12)
        pacer.wait('gemini-3.6-flash')
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            pacer.wait('gemini-3.6-flash', lambda *args: (_ for _ in ()).throw(RuntimeError('cancelled')))
        before = time.monotonic()
        pacer.wait('gemini-3.5-flash-lite')
        self.assertLess(time.monotonic()-before, .025)
        pacer.wait('gemini-3.6-flash')
        self.assertGreaterEqual(time.monotonic()-before, .025)

    def test_provider_429_defers_other_sessions_and_honors_retry_hint(self):
        waits, defers = [], []
        pacer = SimpleNamespace(wait=lambda *args: waits.append(args[0]),
                               defer=lambda *args: defers.append(args))
        class ProviderError(Exception):
            code = 429
            details = {'error': {'details': [{'@type':'type.googleapis.com/google.rpc.RetryInfo',
                                            'retryDelay':'90.5s'}]}}
        def fail(**kwargs):
            raise ProviderError('not printed')
        client = gate.PacedGeminiClient(SimpleNamespace(models=SimpleNamespace(generate_content=fail)), pacer=pacer)
        with self.assertRaises(ProviderError):
            client.models.generate_content(model='gemini-3.5-flash-lite', contents='private')
        self.assertEqual(waits, ['gemini-3.5-flash-lite'])
        self.assertEqual(defers, [('gemini-3.5-flash-lite', 90.5)])

    def test_numeric_retry_headers_and_invalid_hints(self):
        self.assertEqual(gate.retry_after_seconds(SimpleNamespace(
            response=SimpleNamespace(headers={'Retry-After':'120'}))), 120)
        self.assertEqual(gate.retry_after_seconds(SimpleNamespace(details={'error':{'details':[
            {'@type':'google.rpc.RetryInfo','retryDelay':'NaNs'},
            {'@type':'google.rpc.RetryInfo','retryDelay':'-1s'}]}})), 65)

    def test_existing_cooldown_survives_source_reload(self):
        original = gate.REQUEST_PACER
        original.defer('reload-test-model', 70)
        importlib.reload(gate)
        self.assertIs(gate.REQUEST_PACER, original)
        self.assertGreater(original.lanes['reload-test-model']['cooldown'], original.clock()+60)

    def test_batch_progress_reports_waiting_and_keeps_each_completed_result(self):
        from extraction_batch import extract_batch
        pacer = gate.RequestPacer(window_seconds=.24)
        def client_factory():
            return gate.PacedGeminiClient(SimpleNamespace(
                close=lambda:None, models=SimpleNamespace(generate_content=lambda **kw:kw['contents'])), pacer=pacer)
        def extract(client, job, *args, **kwargs):
            name = client.models.generate_content(model='gemini-3.5-flash-lite', contents=job.source_name)
            return [],[],[name],[]
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'sample.pdf';path.write_bytes(b'%PDF-test')
            jobs=[SimpleNamespace(source_name=f'{i}.pdf',page_number=1,total_pages=1,whole_document=True) for i in range(6)]
            events=list(extract_batch(jobs,{id(j):str(path) for j in jobs},6,
                client_factory=client_factory,primary_model='gemini-3.5-flash-lite',fallback_models=[],extractor=extract))
        self.assertTrue(any(e['kind']=='rate_wait' for e in events))
        self.assertEqual(len([e for e in events if e['kind']=='done']),6)
        self.assertEqual([r[2] for r in events[-1]['results']],[[f'{i}.pdf'] for i in range(6)])


if __name__ == '__main__':
    unittest.main()
