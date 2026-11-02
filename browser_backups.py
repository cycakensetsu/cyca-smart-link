"""Recover committed work from this browser after an ephemeral host is replaced.

Only authenticated, owner-validated work/evidence is transferred. Authentication,
approvals and uncommitted inputs are never part of a browser recovery package.
"""
import base64
from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
import re
import uuid
from zipfile import ZIP_DEFLATED, ZipFile, BadZipFile

from estimate_review import canonical, digest
from vault_archive import restore_archive, validate_entry
from work_drafts import validate_draft

MAX_PACKAGE_BYTES = 20 * 1024 * 1024
_component = None


def work_package(store, draft_id):
    if not isinstance(draft_id, str) or not re.fullmatch('[a-f0-9]{64}', draft_id):
        raise ValueError('途中作業の識別を確認できません。')
    name = f'drafts/{draft_id}.json'
    draft_data = store.get_entry(name)
    if not draft_data:
        raise ValueError('途中作業が見つかりません。')
    validate_entry(name, draft_data)
    draft = validate_draft(json.loads(draft_data), store.root.name)
    extraction_name = f"extractions/{draft['extraction_id']}.json"
    extraction_data = store.get_entry(extraction_name)
    if not extraction_data:
        raise ValueError('途中作業の原本情報が見つかりません。')
    validate_entry(extraction_name, extraction_data)
    extraction = json.loads(extraction_data)
    files = {name: draft_data, extraction_name: extraction_data}
    total = len(draft_data) + len(extraction_data)
    for source in extraction['sources']:
        content = store.read(source['id'])
        total += len(content)
        if total > MAX_PACKAGE_BYTES:
            raise ValueError('この作業はブラウザへの自動保全の上限20MBを超えています。PCの作業データも保存してください。')
        files[f"originals/{source['id']}/original"] = content
    manifest = {'schema': 1, 'owner': store.root.name,
                'files': {n: digest_bytes(v) for n,v in files.items()}}
    stream = BytesIO()
    with ZipFile(stream, 'w', ZIP_DEFLATED) as z:
        z.writestr('manifest.json', canonical(manifest))
        for n,v in files.items():
            z.writestr(n,v)
    data = stream.getvalue()
    if len(data) > MAX_PACKAGE_BYTES:
        raise ValueError('この作業はブラウザへの自動保全の上限20MBを超えています。PCの作業データも保存してください。')
    return {'owner': store.root.name, 'work': draft['work_id'], 'revision': draft_id,
            'revision_us': int(datetime.fromisoformat(draft['saved_at']).timestamp()*1_000_000),
            'title': str(draft['state'].get('review_project') or '工事名の入力前')[:200],
            'saved_at': draft['saved_at'], 'sha256': digest_bytes(data),
            'archive': base64.b64encode(data).decode('ascii')}


def digest_bytes(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def restore_browser_package(store, packet):
    if (not isinstance(packet, dict) or packet.get('owner') != store.root.name
            or not isinstance(packet.get('archive'), str)
            or len(packet['archive']) > (MAX_PACKAGE_BYTES*4//3)+8):
        raise ValueError('このログイン利用者の作業データを確認できません。')
    try:
        data = base64.b64decode(packet['archive'], validate=True)
    except (ValueError, TypeError):
        raise ValueError('作業データの形式を確認できません。') from None
    if len(data) > MAX_PACKAGE_BYTES or digest_bytes(data) != packet.get('sha256'):
        raise ValueError('作業データが変更されているため復旧を止めました。')
    # Validate the recovery-only scope before the general immutable ZIP restore.
    try:
        with ZipFile(BytesIO(data)) as z:
            names = z.namelist()
            if (len(names) > 102 or sum(i.file_size for i in z.infolist()) > MAX_PACKAGE_BYTES
                    or any(n != 'manifest.json' and not n.startswith(
                        ('originals/', 'extractions/', 'drafts/')) for n in names)
                    or sum(n.startswith('drafts/') for n in names) != 1
                    or sum(n.startswith('extractions/') for n in names) != 1):
                raise ValueError('途中作業以外のデータが含まれています。')
    except BadZipFile:
        raise ValueError('作業データの形式を確認できません。') from None
    return restore_archive(store, data)


def render(st):
    global _component
    if not st.session_state.get('logged_in'):
        return
    if not hasattr(st.components, 'v2'):
        st.caption('ブラウザへの自動保全はこの環境では使えません。PCの作業データを保存してください。')
        return
    from review_ui import vault
    store = vault(st)
    state = st.session_state
    token = state.setdefault('_browser_backup_session', uuid.uuid4().hex)
    key = state.get('_work_draft_record')
    packet = None
    if key and state.get('_browser_backup_ack') != key:
        try:
            if state.get('_browser_backup_packet', {}).get('revision') != key:
                state['_browser_backup_packet'] = work_package(store, key)
            packet = state['_browser_backup_packet']
        except (OSError, ValueError, KeyError, TypeError):
            st.warning('ブラウザへの自動保全を完了できていません。作業データをPCにも保存してください。')
    if _component is None:
        assets = Path(__file__).parent/'browser_backups_frontend'
        _component = st.components.v2.component('cyca_browser_backups',
            html=(assets/'index.html').read_text(), css=(assets/'style.css').read_text(),
            js=(assets/'backup.js').read_text())
    result = _component(data={'owner':store.root.name, 'session':token,
                             'active':bool(state.get('_review_extraction_id')),
                             'head':key, 'packet':packet},
                        key='cyca_browser_backup', default={'event':None},
                        on_event_change=lambda: None)
    event = result.event
    if not isinstance(event, dict) or event.get('session') != token:
        return
    if event.get('kind') == 'saved' and event.get('revision') == key:
        if state.get('_browser_backup_ack') != key:
            state['_browser_backup_ack'] = key
            state.pop('_browser_backup_packet',None)
            st.rerun()
    elif event.get('kind') == 'restore' and not state.get('_review_extraction_id'):
        event_id = digest(event)
        if event_id == state.get('_browser_restore_processed'):
            return
        state['_browser_restore_processed'] = event_id
        try:
            count = restore_browser_package(store, event.get('packet'))
        except (OSError, ValueError, KeyError, TypeError):
            st.error('ブラウザの作業データを戻せませんでした。現在のデータは保全し、PCのZIPからの復旧をお試しください。')
        else:
            state['_vault_restore_notice'] = f'このブラウザに保存した作業データ{count}件を戻しました。上の「途中の作業から再開」で開けます。'
            st.rerun()
