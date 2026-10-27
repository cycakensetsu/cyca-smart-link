"""Pace this server's Gemini traffic across sessions, readers and consultation.

Conservative defaults fit the active free project inspected on 2026-10-27:
Flash Lite 15 RPM, Flash 5 RPM. This is not a quota increase, daily allowance,
token accounting, or coordination with other applications/server processes.
No credentials, prompts or response bodies are retained by the scheduler.
"""
from collections import deque
import math
import logging
import threading
import time

LOGGER = logging.getLogger('saika_smart_link.gemini')


class RequestPacer:
    def __init__(self, *, clock=time.monotonic, window_seconds=60.5):
        if not math.isfinite(window_seconds) or window_seconds <= 0:
            raise ValueError('positive pacing window required')
        self.clock = clock
        self.window_seconds = window_seconds
        self.condition = threading.Condition()
        self.lanes = {}

    def _lane(self, model):
        return self.lanes.setdefault(model.removeprefix('models/'),
            {'queue': deque(), 'starts': deque(), 'cooldown': 0.0})

    def wait(self, model, on_wait=None):
        # Only actual starts consume slots. Waiting/cancelled tickets do not.
        ticket = object()
        limit = 12 if model.removeprefix('models/') == 'gemini-3.5-flash-lite' else 4
        spacing = self.window_seconds / limit
        with self.condition:
            lane = self._lane(model)
            lane['queue'].append(ticket)
            last_notice = -math.inf
            try:
                while True:
                    now = self.clock()
                    while lane['starts'] and now - lane['starts'][0] >= self.window_seconds:
                        lane['starts'].popleft()
                    ready = lane['cooldown']
                    if lane['starts']:
                        ready = max(ready, lane['starts'][-1] + spacing)
                    if len(lane['starts']) >= limit:
                        ready = max(ready, lane['starts'][0] + self.window_seconds)
                    if lane['queue'][0] is ticket and now >= ready:
                        lane['starts'].append(now)
                        lane['queue'].popleft()
                        self.condition.notify_all()
                        return
                    if on_wait is not None and now - last_notice >= 5:
                        # The callback only reports progress. It never releases
                        # the slot or changes the rate policy.
                        on_wait(max(0.0, ready - now), len(lane['queue']))
                        last_notice = now
                    self.condition.wait(timeout=min(1.0, max(0.05, ready - now)))
            finally:
                if ticket in lane['queue']:
                    lane['queue'].remove(ticket)
                    self.condition.notify_all()

    def defer(self, model, seconds=65):
        with self.condition:
            lane = self._lane(model)
            lane['cooldown'] = max(lane['cooldown'], self.clock() + max(65, seconds))
            self.condition.notify_all()


# Keep the gate during coordinated module reloads and overlapping old workers.
REQUEST_PACER = globals().get('REQUEST_PACER') or RequestPacer()


def retry_after_seconds(error):
    """Read numeric provider retry hints only; never log the provider payload."""
    seconds = 65.0
    details = getattr(error, 'details', None)
    if isinstance(details, dict):
        details = details.get('error', details)
        details = details.get('details', []) if isinstance(details, dict) else []
    if isinstance(details, list):
        for entry in details:
            if not isinstance(entry, dict) or not str(entry.get('@type', '')).endswith('google.rpc.RetryInfo'):
                continue
            delay = entry.get('retryDelay', '')
            try:
                value = float(delay[:-1]) if isinstance(delay, str) and delay.endswith('s') else 0
                if math.isfinite(value) and value > 0:
                    seconds = max(seconds, value)
            except (ValueError, TypeError):
                pass
    response = getattr(error, 'response', None)
    try:
        value = float(response.headers.get('Retry-After', 0))
        if math.isfinite(value) and value > 0:
            seconds = max(seconds, value)
    except (AttributeError, TypeError, ValueError):
        pass
    return seconds


class _PacedModels:
    def __init__(self, owner):
        self.owner = owner

    def generate_content(self, *, model, **kwargs):
        owner = self.owner
        owner.pacer.wait(model, owner.on_wait)
        LOGGER.info('gemini_request_admitted model=%s', model)
        try:
            return owner.client.models.generate_content(model=model, **kwargs)
        except Exception as error:
            if getattr(error, 'code', getattr(error, 'status_code', None)) == 429:
                seconds = retry_after_seconds(error)
                owner.pacer.defer(model, seconds)
                LOGGER.warning('gemini_request_cooldown model=%s seconds=%.2f', model, seconds)
            raise


class PacedGeminiClient:
    """Wrap an existing SDK client without copying/exposing its credentials."""
    def __init__(self, client, *, pacer=None, on_wait=None):
        self.client = client
        self.pacer = REQUEST_PACER if pacer is None else pacer
        self.on_wait = on_wait
        self.models = _PacedModels(self)

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
