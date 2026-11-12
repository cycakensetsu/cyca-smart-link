"""Explicit private configuration. No credentials, accounts, or OAuth flow created here."""
from pathlib import Path
from document_delivery import DeliveryStore, GmailTransport
from document_workflow_service import DocumentWorkflowService


def configured_store(config):
    if config.get('database_provider') == 'postgres':
        if not config.get('persistent_storage_verified', False):
            raise ValueError('永続データベースの検証が未完了です。')
        from postgres_workflow_store import PostgresDeliveryStore
        import os
        dsn = config.get('postgres_dsn') or os.environ.get('CYCA_WORKFLOW_POSTGRES_DSN')
        try:
            return PostgresDeliveryStore(dsn)
        except Exception:
            raise ValueError('永続DBへの接続または移行状態を確認できません。管理者が非公開設定を確認してください。') from None
    path = config.get('database_path')
    if not path or not config.get('persistent_storage_verified', False):
        raise ValueError('確認済みの非公開・永続保存先が未設定です。')
    path = Path(path).expanduser()
    if not path.is_absolute() or not path.parent.is_dir() or path.is_symlink():
        raise ValueError('永続保存先のパスを確認してください。')
    if path.parent.stat().st_mode & 0o077:
        raise ValueError('保存先は所有者のみアクセスできる専用フォルダーにしてください。')
    store = DeliveryStore(path)
    path.chmod(0o600)
    return store


def company_scope(config, actor):
    company = config.get('company_id')
    if company:
        if not isinstance(company,str) or not company.strip() or actor not in config.get('company_members', []):
            raise ValueError('会社の利用権限がありません。')
        return company
    return actor  # Explicit single-user compatibility; not a shared workspace.


def configured_service(config, owner):
    company = company_scope(config, owner)
    permissions = None
    if config.get('company_id'):
        permissions = config.get('member_permissions', {}).get(owner)
        if not isinstance(permissions, list) or 'read' not in permissions:
            raise ValueError('社員の操作権限が未設定です。')
    store = configured_store(config)
    transport = None
    credentials_path = config.get('google_oauth_credentials_file')
    if credentials_path and config.get('gmail_enabled', False):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        path = Path(credentials_path).expanduser()
        if not path.is_absolute() or not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o077:
            raise ValueError('Google認可情報の非公開保存設定を確認してください。')
        credentials = Credentials.from_authorized_user_file(str(path))
        service = build('gmail','v1',credentials=credentials,cache_discovery=False)
        transport = GmailTransport(service)
    return DocumentWorkflowService(store,owner,transport,company_id=company,permissions=permissions)


def configured_api(config, *, vault_factory):
    """Build, but do not publish, the API from explicitly configured company settings.
    vault_factory(company_id) must return the mapped, authorized existing quote vault.
    """
    from invoice_registry import InvoiceRegistry
    from workflow_vault_adapter import make_resolvers
    from workflow_api import create_app
    company=config.get('company_id')
    members=config.get('company_members',[])
    if not company or not members:
        raise ValueError('会社IDと利用者の設定が必要です。')
    token_owners=dict(config.get('api_token_owners',{}))
    if not token_owners or any(actor not in members for actor in token_owners.values()):
        raise ValueError('API利用者の認可設定を確認してください。')
    runtime=configured_service(config,members[0])
    InvoiceRegistry(runtime.store)
    estimates,documents=make_resolvers(vault_factory,runtime.store)
    return create_app(store=runtime.store,token_owners=token_owners,
                      actor_companies={actor:company for actor in members},
                      actor_permissions=dict(config.get('member_permissions',{})),resolve_estimate=estimates,
                      resolve_document=documents,issuer=dict(config.get('issuer',{})),
                      sender=config.get('sender'),transport=runtime.transport)
