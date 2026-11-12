"""PostgreSQL/Supabase durable backend compatible with the existing stores.

No credentials, roles, tables, migrations or grants are created at runtime.
Apply reviewed workflow_schema.sql separately, then configure a dedicated,
least-privilege server DB connection. An HTTP Supabase Storage service key is
NOT a PostgreSQL DSN. Never send the DSN to a browser or log it.

Low-volume design: all short transactions take the same advisory xact lock.
This serializes prepare/finalize across processes and company members, while
transport.send is outside any transaction. Partial unique indexes additionally
protect business dedup. Do not replace this with per-operation UUID locks.
Existing owner columns hold the server-validated company scope; actor is the
employee. Database access is server-only, private schema; browser JWTs have no
policies. App membership authorization remains mandatory before every call.
"""
from contextlib import contextmanager
import re
from document_delivery import DeliveryStore

SCHEMA = 'cyca_workflow'
LOCK_ID = 49304842331474521


def _parameters(sql):
    """Translate qmark parameters outside quoted SQL literals to psycopg form."""
    result=[]; quoted=False; i=0
    while i < len(sql):
        c=sql[i]
        if c=="'":
            result.append(c)
            if quoted and i+1<len(sql) and sql[i+1]=="'":
                result.append("'"); i+=2; continue
            quoted=not quoted
        elif c=='?' and not quoted: result.append('%s')
        else: result.append(c)
        i+=1
    return ''.join(result)


class _Connection:
    """Small, explicit compatibility layer, not a general SQLite translator."""
    def __init__(self, connection): self.connection=connection

    def execute(self, sql, parameters=()):
        normalized=' '.join(sql.split())
        if normalized.upper()=='BEGIN IMMEDIATE':
            # connect() already owns a transaction and the cross-process lock.
            return self.connection.execute('SELECT 1')
        create=re.match(r'CREATE TABLE IF NOT EXISTS (deliveries|invoices)\b',normalized,re.I)
        if create:
            # Legacy constructors request DDL. Only verify the migrated table.
            return self.connection.execute(f'SELECT 1 FROM {SCHEMA}.{create.group(1).lower()} LIMIT 0')
        if re.match(r'\s*(CREATE|ALTER|DROP|GRANT|REVOKE|TRUNCATE|PRAGMA|ATTACH)\b',sql,re.I):
            raise ValueError('スキーマ変更は専用の確認済み移行手順で行ってください。')
        return self.connection.execute(_parameters(sql),parameters)


class PostgresDeliveryStore(DeliveryStore):
    """Same public methods and connect() interface; no inherited SQLite DDL.

    connect_factory is only for tests. Production uses psycopg3 dict rows.
    TLS server identity verification is mandatory for a configured remote DSN.
    """
    def __init__(self, dsn, *, connect_factory=None):
        if not isinstance(dsn,str) or not dsn.strip():
            raise ValueError('非公開のPostgreSQL接続設定が必要です。')
        self._dsn=dsn
        self._factory=connect_factory
        if connect_factory is None:
            from psycopg.conninfo import conninfo_to_dict
            try: options=conninfo_to_dict(dsn)
            except Exception:
                raise ValueError('PostgreSQL接続設定を確認してください。') from None
            if options.get('sslmode')!='verify-full':
                raise ValueError('PostgreSQL接続はTLSサーバー検証を有効にしてください。')
            if options.get('options'):
                raise ValueError('接続文字列に追加セッションオプションは設定できません。')
        # Verify migration without any schema writes.
        with self.connect() as db:
            row=db.execute(f'SELECT version FROM {SCHEMA}.schema_version WHERE version=1').fetchone()
            if not row or row['version']!=1:
                raise ValueError('請求・送信台帳のスキーマ移行が未完了です。')
            db.execute(f'SELECT 1 FROM {SCHEMA}.deliveries LIMIT 0')
            db.execute(f'SELECT 1 FROM {SCHEMA}.invoices LIMIT 0')

    @contextmanager
    def connect(self):
        if self._factory:
            connection=self._factory(self._dsn)
        else:
            import psycopg
            from psycopg.rows import dict_row
            connection=psycopg.connect(self._dsn,row_factory=dict_row,connect_timeout=10,
                                      autocommit=False)
        try:
            with connection.transaction():
                # A waiting transaction must see the preceding lock-holder's commit.
                connection.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
                connection.execute('SET LOCAL statement_timeout = 15000')
                connection.execute('SET LOCAL lock_timeout = 10000')
                connection.execute(f'SET LOCAL search_path = {SCHEMA}, pg_catalog')
                connection.execute('SELECT pg_advisory_xact_lock(%s)',(LOCK_ID,))
                yield _Connection(connection)
        finally:
            connection.close()
