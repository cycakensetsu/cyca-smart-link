"""Explicit private configuration only; no credentials in the repository."""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

_attempts = {}
_lock = threading.Lock()
AUTH_POLICY = '2026-10-20-explicit-private-credentials'


def configuration_signature(users):
    value = json.dumps([AUTH_POLICY, users], sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(value.encode()).hexdigest()


def session_valid(state, users, now=None):
    now = time.time() if now is None else now
    return (bool(users) and bool(state.get('logged_in'))
            and state.get('_auth_signature') == configuration_signature(users)
            and 0 <= now - state.get('_auth_started', 0) < 12 * 60 * 60)


def password_hash(password, iterations=600000):
    salt = secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), iterations).hex()
    return f'pbkdf2_sha256${iterations}${salt}${value}'


def verify_password(password, encoded):
    try:
        algorithm, rounds, salt, expected = encoded.split('$')
        rounds = int(rounds)
        if algorithm != 'pbkdf2_sha256' or not 100000 <= rounds <= 2000000:
            return False
        if len(salt) != 32 or len(expected) != 64:
            return False
        value = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), rounds).hex()
        return hmac.compare_digest(value, expected)
    except (ValueError, TypeError, AttributeError):
        return False


def load_users(config_file, private_settings=None, environ=None):
    env = os.environ if environ is None else environ
    settings = private_settings or {}
    # A configured list overrides the legacy private file/environment pair.
    users = settings.get('users')
    if users is None and Path(config_file).is_file():
        try:
            users = json.loads(Path(config_file).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return []
    if users is None:
        username = env.get('STREAMLIT_APP_USER') or settings.get('STREAMLIT_APP_USER')
        encoded = env.get('STREAMLIT_APP_PASSWORD_HASH') or settings.get('STREAMLIT_APP_PASSWORD_HASH')
        password = env.get('STREAMLIT_APP_PASSWORD') or settings.get('STREAMLIT_APP_PASSWORD')
        users = [{'username': username, 'password_hash': encoded, 'password': password}]
    if not isinstance(users, (list, tuple)):
        return []
    return [dict(u) for u in users if isinstance(u, dict) and str(u.get('username') or '').strip()
            and (u.get('password_hash') or u.get('password'))]


def check_login(username, password, users, now=None):
    """Constant-time comparison and shared per-account attempt throttling."""
    if not username or not password or len(username) > 256 or len(password) > 1024:
        return False
    now = time.monotonic() if now is None else now
    key = hashlib.sha256(username.encode()).hexdigest()
    with _lock:
        # Bound memory and retain only the recent rate-limit window.
        for old_key in list(_attempts):
            if now - _attempts[old_key][0] >= 60:
                del _attempts[old_key]
        if len(_attempts) >= 10000 and key not in _attempts:
            return False
        start, count = _attempts.get(key, (now, 0))
        if count >= 5:
            return False
        _attempts[key] = (start, count + 1)
    valid = False
    for user in users:
        same_user = hmac.compare_digest(str(user['username']).encode(), username.encode())
        if not same_user:
            continue
        if user.get('password_hash'):
            valid = verify_password(password, user['password_hash'])
        elif user.get('password'):
            # Migration support for explicitly configured private credentials.
            valid = hmac.compare_digest(str(user['password']).encode(), password.encode())
        break
    if valid:
        with _lock:
            _attempts.pop(key, None)
    return valid
