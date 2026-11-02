"""Validate a portable archive locally before sending any bytes to shared storage.

Call only after authorization for the specific source archive and destination.
Credentials are read from server configuration; never accept them in CLI flags.
Migration is additive and retryable; there is no delete/replace operation.
"""
from pathlib import Path
from tempfile import TemporaryDirectory

from estimate_review import OriginalVault
from vault_archive import restore_archive


def migrate_archive(data, target, *, apply=False):
    if apply and not getattr(target, 'shared', False):
        raise ValueError('移行先の共通保存設定が未完了です。ローカル保存へ切り替えて移行は行いません。')
    # Validate even records that would not otherwise be opened by this session.
    with TemporaryDirectory() as tmp:
        # OriginalVault hashes a login name, so use the already established
        # target namespace after constructing the private verification folder.
        checked = OriginalVault(tmp, 'archive-verification-only')
        checked.root = Path(tmp)/target.root.name
        checked.root.mkdir(mode=0o700)
        count = restore_archive(checked, data)
        plan = {'records': count, 'bytes': sum(size for _,size,_ in checked.entries()),
                'owner': target.root.name, 'applied': False}
        if not apply:
            return plan
        restored = restore_archive(target, data)
        # restore_archive also performs uncached provider readback, so the GUI
        # and this administrator path have the same completion condition.
        return {**plan, 'applied': True, 'verified_records': restored}


if __name__ == '__main__':
    import argparse
    from shared_storage import configuration, configured_vault
    parser = argparse.ArgumentParser(description='検証済みZIPの追加移行。既定は送信しない事前確認です。')
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--user', required=True)
    parser.add_argument('--cache', type=Path, default=Path(__file__).parent/'.private_data')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.apply and configuration() is None:
        parser.error('共通保存先のサーバー設定が必要です。接続情報を引数やチャットへ書かないでください。')
    target = (configured_vault(args.cache, args.user) if args.apply
              else OriginalVault(args.cache, args.user))
    print(migrate_archive(args.archive.read_bytes(), target, apply=args.apply))
