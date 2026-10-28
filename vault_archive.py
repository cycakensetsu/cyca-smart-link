"""Portable private archives. Verify everything before restoring any bytes."""
from io import BytesIO
import hashlib
import json
from pathlib import PurePosixPath
import re
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile

from estimate_review import canonical, digest

MAX_BYTES = 200 * 1024 * 1024
MAX_FILES = 2000
_HEX = r'[a-f0-9]{64}'
_PATH = re.compile(rf'(originals/{_HEX}/original|(?:uploads|extractions|approvals|drafts)/{_HEX}\.json|exports/{_HEX}/{_HEX}\.(?:xlsx|pdf))')


def validate_entry(name, data):
    if not _PATH.fullmatch(name):
        raise ValueError('バックアップ内の保存先が不正です。')
    parts = PurePosixPath(name).parts
    if parts[0] == 'originals':
        expected = parts[1]
    elif parts[0] == 'exports':
        expected = PurePosixPath(parts[-1]).stem
    else:
        value = json.loads(data)
        if digest(value) != PurePosixPath(parts[-1]).stem:
            raise ValueError('保管記録が変更されています。')
        if parts[0] == 'drafts':
            from work_drafts import validate_draft
            validate_draft(value)
        return
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('原本または帳票が変更されています。')


def archive_bytes(store):
    from work_drafts import available_drafts
    # Keep all immutable evidence/receipts. Draft revisions stay in the vault;
    # the portable backup needs only each independent work's latest checkpoint.
    latest_drafts = {f"drafts/{v['id']}.json" for v in available_drafts(store)}
    files = []
    total = 0
    for name, size, read in store.entries():
        if name.startswith('drafts/') and name not in latest_drafts:
            continue
        total += size
        if total > MAX_BYTES or len(files) >= MAX_FILES:
            raise ValueError('一括保存の上限200MB・2000件を超えました。管理者に保管先の設定を依頼してください。')
        data = read()
        validate_entry(name, data)
        files.append((name, data))
    if not files:
        return None
    manifest = {'schema': 1, 'owner': store.root.name,
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in files}}
    result = BytesIO()
    with ZipFile(result, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', canonical(manifest))
        for name, data in files:
            archive.writestr(name, data)
    return result.getvalue()


def restore_archive(store, data):
    if len(data) > MAX_BYTES:
        raise ValueError('バックアップが200MBを超えています。')
    try:
        with ZipFile(BytesIO(data)) as archive:
            infos = archive.infolist()
            names = [i.filename for i in infos]
            if len(names) != len(set(names)) or len(names) > MAX_FILES + 1:
                raise ValueError('バックアップの件数または重複が不正です。')
            if sum(i.file_size for i in infos) > MAX_BYTES + 1024 * 1024:
                raise ValueError('展開後のサイズが上限を超えています。')
            manifest = json.loads(archive.read('manifest.json'))
            if not isinstance(manifest, dict):
                raise ValueError('バックアップの一覧が不正です。')
            if manifest.get('schema') != 1 or manifest.get('owner') != store.root.name:
                raise ValueError('このログイン利用者用のバックアップではありません。')
            hashes = manifest['files']
            if not isinstance(hashes, dict) or set(names) != set(hashes) | {'manifest.json'}:
                raise ValueError('バックアップの一覧が一致しません。')
            checked = []
            for name, expected in hashes.items():
                content = archive.read(name)
                validate_entry(name, content)
                if name.startswith('drafts/'):
                    from work_drafts import validate_draft
                    validate_draft(json.loads(content), store.root.name)
                if hashlib.sha256(content).hexdigest() != expected:
                    raise ValueError('バックアップの内容が変更されています。')
                existing = store.get_entry(name)
                if existing is not None and existing != content:
                    raise ValueError('保存済みデータと異なるため復元を止めました。')
                checked.append((name, content))
    except (BadZipFile, KeyError, TypeError, UnicodeError, RuntimeError, json.JSONDecodeError):
        raise ValueError('サイカスマートリンクの有効なバックアップではありません。') from None
    for name, content in checked:
        store._put(name, content)
        if name.startswith('drafts/'):
            from work_drafts import update_head
            update_head(store, PurePosixPath(name).stem, json.loads(content))
    return len(checked)
