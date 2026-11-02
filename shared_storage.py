"""Private server-side shared storage; local disk is a verified read cache only.

Configuration is opt-in. No buckets, credentials, policies or public URLs are
created here. Existing owner namespaces and immutable evidence are retained.
"""
import base64
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re

from estimate_review import OriginalVault
from vault_archive import _PATH, validate_entry, MAX_BYTES

_OWNER = re.compile(r'[a-f0-9]{64}')
_URL = re.compile(r'https://[a-z0-9]{10,40}\.supabase\.co')


def configuration(settings=None, environ=None):
    env = os.environ if environ is None else environ
    section = dict(settings or {})
    names = {'url': 'CYCA_SHARED_STORAGE_URL', 'service_key': 'CYCA_SHARED_STORAGE_KEY',
             'bucket': 'CYCA_SHARED_STORAGE_BUCKET'}
    for key, name in names.items():
        if env.get(name):
            section[key] = env[name]
    if not section:
        return None
    if section.get('provider', 'supabase') != 'supabase':
        raise ValueError('共通保存先の種類を確認してください。')
    url = str(section.get('url', '')).rstrip('/')
    key = str(section.get('service_key', ''))
    bucket = str(section.get('bucket', 'cyca-estimates'))
    if not _URL.fullmatch(url) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{2,62}', bucket):
        raise ValueError('共通保存先の設定が未完了です。管理者の接続確認が必要です。')
    valid_key = key.startswith('sb_secret_') and len(key) >= 30
    if not valid_key:
        try:
            payload = key.split('.')[1]
            valid_key = json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4))).get('role') == 'service_role'
        except (IndexError, ValueError, TypeError, UnicodeError, AttributeError):
            valid_key = False
    if not valid_key:
        raise ValueError('共通保存先にはサーバー専用の接続情報が必要です。公開用キーは使えません。')
    return url, key, bucket


def object_path(owner, name):
    if not _OWNER.fullmatch(owner) or not _PATH.fullmatch(name):
        raise ValueError('共通保存先の指定が不正です。')
    # One owner directory permits bounded, sorted pagination without recursively
    # listing every original/export directory. Canonical vault paths contain no
    # underscores, so this provider-safe delimiter is unambiguous and reversible.
    return owner+'/'+name.replace('/', '__')


def validate_owned(owner, name, data):
    validate_entry(name, data)
    if name.startswith('drafts/'):
        from work_drafts import validate_draft
        validate_draft(json.loads(data), owner)


class SupabaseObjects:
    def __init__(self, url, key, bucket, client=None):
        if client is None:
            from storage3 import SyncStorageClient
            import httpx
            client = SyncStorageClient(url+'/storage/v1/',
                {'apikey': key, 'Authorization': 'Bearer '+key},
                http_client=httpx.Client(timeout=httpx.Timeout(30, connect=10),
                                         follow_redirects=False))
        self.client = client
        self.bucket = bucket
        self.files = client.from_(bucket)
        self.identity = hashlib.sha256((url+'/'+bucket).encode()).hexdigest()

    def private(self):
        try:
            info = self.client.get_bucket(self.bucket)
            if getattr(info, 'public', None) is not False:
                raise ValueError
        except Exception:
            raise OSError('非公開の共通保存先を確認できません。設定と接続を確認してください。') from None

    def get(self, owner, name):
        path = object_path(owner, name)
        self.private()
        try:
            content = self.files.download(path)
        except Exception as exc:
            # A missing object is distinct from denied access, quota or outage.
            if str(getattr(exc, 'code', '')) in ('NoSuchKey', 'ObjectNotFound', 'not_found'):
                return None
            if str(getattr(exc, 'status', '')) == '404':
                return None
            raise OSError('共通保存先から読み込めません。接続を確認してから再度開いてください。') from None
        if len(content) > MAX_BYTES:
            raise ValueError('共通保存先のファイルが上限を超えています。')
        validate_owned(owner, name, content)
        return content

    def put(self, owner, name, data):
        path = object_path(owner, name)
        validate_owned(owner, name, data)
        if len(data) > MAX_BYTES:
            raise ValueError('保存するファイルが上限を超えています。')
        self.private()
        try:
            self.files.upload(path, data, {'content-type': 'application/octet-stream',
                                          'upsert': 'false', 'cache-control': '0'})
        except Exception as exc:
            if (str(getattr(exc, 'code', '')) not in ('Duplicate', 'ResourceAlreadyExists', 'AlreadyExists')
                    and str(getattr(exc, 'status', '')) != '409'):
                raise OSError('共通保存が完了していません。この画面を閉じず、接続を確認してください。') from None
            if self.get(owner, name) != data:
                raise ValueError('共通保存先の記録と一致しないため、上書きを止めました。') from None
        # A successful upload acknowledges durable object creation. A response
        # lost after a write is safely retried with the same immutable key.

    def entries(self, owner):
        if not _OWNER.fullmatch(owner):
            raise ValueError('ログイン利用者を確認できません。')
        self.private()
        found = {}
        offset = 0
        while True:
            try:
                page = self.files.list(owner, {'limit': 100, 'offset': offset,
                    'sortBy': {'column': 'name', 'order': 'asc'}})
                if not isinstance(page, list) or len(page) > 100:
                    raise ValueError
                for item in page:
                    flat = item['name']
                    size = item['metadata']['size']
                    # The dashboard creates this zero-byte folder marker. It is
                    # not a vault record; malformed/nonempty lookalikes still fail.
                    if flat == '.emptyFolderPlaceholder' and type(size) is int and size == 0:
                        continue
                    name = flat.replace('__', '/')
                    if (not _PATH.fullmatch(name) or flat != name.replace('/', '__')
                            or type(size) is not int or not 0 <= size <= MAX_BYTES):
                        raise ValueError
                    found[name] = size
            except Exception:
                raise OSError('保存見積の一覧を取得できません。空の一覧とは扱わず、接続を確認してください。') from None
            if len(page) < 100:
                break
            offset += 100
            if offset >= 100_000:
                raise OSError('保存一覧が大きいため取得を止めました。管理者の確認が必要です。')
        return sorted(found.items())


@lru_cache(maxsize=4)
def backend(url, key, bucket):
    return SupabaseObjects(url, key, bucket)


class SharedVault(OriginalVault):
    def __init__(self, cache_root, user, objects):
        # Never trust the old local-only vault as a shared-store cache. A local
        # original must not falsely make a remote draft appear self-contained.
        super().__init__(Path(cache_root)/'shared-cache'/objects.identity, user)
        self.objects = objects
        self.shared = True

    def _put(self, relative, data):
        if relative == '.vault_meta/instance.json':
            return super()._put(relative, data)
        self.objects.put(self.root.name, relative, bytes(data))
        return super()._put(relative, data)

    def get_entry(self, name):
        # Content-addressed cached files are immutable and verified on every
        # read. Cache directories are isolated by shared-store identity.
        cached = super().get_entry(name)
        if cached is not None:
            validate_owned(self.root.name, name, cached)
            return cached
        content = self.objects.get(self.root.name, name)
        if content is not None:
            super()._put(name, content)
        return content

    def read(self, source_id):
        if not _OWNER.fullmatch(str(source_id)):
            raise ValueError('原本IDが不正です。')
        content = self.get_entry(f'originals/{source_id}/original')
        if content is None:
            raise ValueError('共通保存先に原本が見つかりません。')
        return content

    def entries(self):
        for name, size in self.objects.entries(self.root.name):
            def read(n=name):
                content = self.get_entry(n)
                if content is None:
                    raise OSError('一覧にある記録を読み込めません。共通保存先を確認してください。')
                return content
            yield name, size, read

    def draft_entries(self):
        return (entry for entry in self.entries() if entry[0].startswith('drafts/'))

    def verify_restored_entries(self, entries):
        # Bypass the warm app cache: GUI restores and browser recovery must
        # prove the provider can return the same bytes before reporting success.
        for name, expected in entries:
            if self.objects.get(self.root.name, name) != expected:
                raise OSError('共通保存先からの読み戻しが一致しません。元データは保全しています。')


def configured_vault(cache_root, user, settings=None, environ=None):
    enabled = (settings or {}).get('enabled_users')
    if enabled is not None:
        if (not isinstance(enabled, list) or not enabled or any(
                not isinstance(name, str) or not name.strip() or len(name) > 128
                for name in enabled)):
            raise ValueError('共通保存を有効にするログインIDの設定を確認してください。')
        if user not in enabled:
            # Roll out only the explicitly migrated owner; preserve every
            # other login's existing local namespace and avoid remote calls.
            return OriginalVault(cache_root, user)
    config = configuration(settings, environ)
    if config is None:
        return OriginalVault(cache_root, user)
    return SharedVault(cache_root, user, backend(*config))
