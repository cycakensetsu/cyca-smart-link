"""Owner-private storage continuity diagnostics, without customer data."""
from datetime import datetime, timezone
import json
import os
import re
import uuid

_MARKER = '.vault_meta/instance.json'


def storage_status(store, environ=None):
    path = store.root / _MARKER
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError('保存状態の確認先が不正です。')
    if not path.exists():
        value = {'schema': 1, 'instance': uuid.uuid4().hex,
                 'created_at': datetime.now(timezone.utc).isoformat()}
        try:
            store._put(_MARKER, json.dumps(value, sort_keys=True).encode())
        except ValueError:
            # Another process may have published its own immutable marker.
            # Read the winner, never replace or repair an invalid marker.
            if not path.is_file():
                raise
    if path.stat().st_size > 1024:
        raise ValueError('保存状態の確認記録が不正です。')
    value = json.loads(path.read_bytes())
    if (not isinstance(value, dict) or value.get('schema') != 1
            or not isinstance(value.get('instance'), str)
            or not re.fullmatch('[a-f0-9]{32}', value['instance'])):
        raise ValueError('保存状態の確認記録が不正です。')
    datetime.fromisoformat(value['created_at'])
    counts = {'originals': 0, 'reads': 0, 'approvals': 0}
    for name, _, _ in store.entries():
        kind = name.split('/')[0]
        key = {'originals': 'originals', 'extractions': 'reads', 'approvals': 'approvals'}.get(kind)
        if key:
            counts[key] += 1
    env = os.environ if environ is None else environ
    shared = bool(getattr(store, 'shared', False))
    return {**value, **counts, 'path_configured': bool(env.get('CYCA_DATA_DIR')),
            'shared': shared,
            'shared_identity': store.objects.identity if shared else None}


def render_storage_status(st, store):
    try:
        status = storage_status(store)
    except (OSError, ValueError, KeyError, TypeError):
        st.error('保存した見積の保管状態を確認できません。PCの作業データはそのまま保全してください。')
        return
    if status['shared']:
        st.success('共通保存先に接続しています。同じログインIDで、別の端末から保存した見積を開けます。')
    elif not status['path_configured']:
        st.warning('この公開版の保存履歴は、アプリの再起動・再設置などで消える場合があります。作業の最後に「作業データをまとめて保存」でPCにも残してください。')
    if not status['reads'] and not status['approvals']:
        st.info('この保管場所には、保存した見積がまだありません。以前保存した作業データ（ZIP）を下から戻すと、見積を開けます。')
    with st.expander('保存状態の確認（管理用）'):
        st.caption(f"保管場所の識別：{status['instance']} ／ 作成：{status['created_at']}")
        st.caption(f"原本 {status['originals']}件・読み取り {status['reads']}件・完成見積 {status['approvals']}件")
        if status['shared']:
            st.caption('非公開の共通保存先：'+status['shared_identity'][:12]+' ／ 上の保管場所の識別はアプリ側の一時作業場所です。')
        else:
            st.caption('保存先の指定あり（永続性は別途確認が必要）' if status['path_configured'] else 'アプリ内の一時保管。PCの作業データから復元できます。')
