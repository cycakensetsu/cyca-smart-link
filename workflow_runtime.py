"""Explicit private configuration. No credentials, accounts, or OAuth flow created here."""
from pathlib import Path
from collections.abc import Mapping
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


def actor_gmail_config(config, actor):
    """Resolve only from trusted server configuration, never request content.
    Multi-user configurations never fall back to legacy global credentials.
    """
    company_scope(config, actor)
    accounts = config.get('gmail_accounts')
    multi = len(config.get('company_members', [])) > 1 or accounts is not None
    if multi:
        if accounts is None or (isinstance(accounts, Mapping) and actor not in accounts):
            return dict(sender='', google_oauth_credentials_file=None, gmail_enabled=False)
        if not isinstance(accounts, Mapping) or not isinstance(accounts.get(actor), Mapping):
            raise ValueError('この利用者のGmail設定が不正です。')
        account = accounts[actor]
    else:
        account = config
    from document_delivery import address
    sender = account.get('sender')
    if sender:
        address(sender)
    elif account.get('gmail_enabled') or account.get('google_oauth_credentials_file') or account.get('authorized_user') is not None:
        raise ValueError('Gmail送信元が未設定です。')
    result = dict(sender=sender or '',
                  google_oauth_credentials_file=account.get('google_oauth_credentials_file'),
                  gmail_enabled=bool(account.get('gmail_enabled', False)))
    if 'authorized_user' in account:
        result['authorized_user'] = account['authorized_user']
    return result


GMAIL_REQUIRED_SCOPES = frozenset({
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.readonly',
})
_AUTH_ERROR = 'Google認可情報を確認できません。非公開の設定と再接続状態を確認してください。'


def validate_authorized_user(value):
    """Validate private server config without reflecting any supplied value.
    Never accept service accounts, arbitrary token endpoints, or missing scopes.
    """
    if not isinstance(value, Mapping):
        raise ValueError(_AUTH_ERROR)
    info = dict(value)
    if info.get('type', 'authorized_user') != 'authorized_user':
        raise ValueError(_AUTH_ERROR)
    for field in ('client_id', 'client_secret', 'refresh_token'):
        item = info.get(field)
        if not isinstance(item, str) or not item.strip() or any(c in item for c in '\r\n'):
            raise ValueError(_AUTH_ERROR)
    if info.get('token_uri', 'https://oauth2.googleapis.com/token') != 'https://oauth2.googleapis.com/token':
        raise ValueError(_AUTH_ERROR)
    if info.get('universe_domain', 'googleapis.com') != 'googleapis.com':
        raise ValueError(_AUTH_ERROR)
    scopes = info.get('scopes')
    if isinstance(scopes, str):
        scopes = scopes.split()
    if (not isinstance(scopes, (list, tuple)) or not scopes
            or any(not isinstance(scope, str) for scope in scopes)
            or not GMAIL_REQUIRED_SCOPES.issubset(scopes)):
        raise ValueError(_AUTH_ERROR)
    info['scopes'] = list(scopes)
    return info


def configured_transport(account):
    credentials_path = account.get('google_oauth_credentials_file')
    info_present = account.get('authorized_user') is not None
    if credentials_path and info_present:
        raise ValueError('Google認可情報はファイルまたは非公開設定の一方だけを指定してください。')
    if not account.get('gmail_enabled', False):
        return None
    if not credentials_path and not info_present:
        return None
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        if info_present:
            info = validate_authorized_user(account['authorized_user'])
            credentials = Credentials.from_authorized_user_info(info)
        else:
            import json
            path = Path(credentials_path).expanduser()
            if not path.is_absolute() or not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o077:
                raise ValueError(_AUTH_ERROR)
            # Apply the same minimum checks to legacy private JSON files.
            info = validate_authorized_user(json.loads(path.read_text(encoding='utf-8')))
            credentials = Credentials.from_authorized_user_info(info)
        service = build('gmail','v1',credentials=credentials,cache_discovery=False)
        return GmailTransport(service)
    except Exception:
        # SDK/parser errors can include credential content. Never surface it.
        raise ValueError(_AUTH_ERROR) from None


def configured_service(config, owner):
    company = company_scope(config, owner)
    permissions = None
    if config.get('company_id'):
        permissions = config.get('member_permissions', {}).get(owner)
        if not isinstance(permissions, list) or 'read' not in permissions:
            raise ValueError('社員の操作権限が未設定です。')
    account = actor_gmail_config(config, owner)
    store = configured_store(config)
    return DocumentWorkflowService(store,owner,configured_transport(account),company_id=company,
                                   permissions=permissions,sender=account['sender'])


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
    store=configured_store(config)
    accounts={actor:actor_gmail_config(config,actor) for actor in members}
    mailboxes={actor:{"sender":account["sender"],"transport":configured_transport(account)} for actor,account in accounts.items()}
    InvoiceRegistry(store)
    estimates,documents=make_resolvers(vault_factory,store)
    return create_app(store=store,token_owners=token_owners,
                      actor_companies={actor:company for actor in members},
                      actor_permissions=dict(config.get('member_permissions',{})),resolve_estimate=estimates,
                      resolve_document=documents,issuer=dict(config.get('issuer',{})),
                      actor_mailboxes=mailboxes)
