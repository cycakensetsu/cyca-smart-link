"""Driver/SQL contract tests. No live Postgres/Supabase connection is made."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from postgres_workflow_store import PostgresDeliveryStore, _Connection, _parameters
from invoice_registry import InvoiceRegistry
from invoice_documents import invoice_fingerprint
from test_invoice_documents import make


class FakeDriver:
    def __init__(self, path, lock, queries):
        self.db=sqlite3.connect(path,check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        self.lock=lock;self.queries=queries;self.closed=False
    @contextmanager
    def transaction(self):
        with self.lock:
            with self.db:yield
    def execute(self,sql,parameters=()):
        self.queries.append(sql)
        if sql.startswith('SET LOCAL') or sql.startswith('SET TRANSACTION') or 'pg_advisory_xact_lock' in sql:
            return self.db.execute('SELECT 1')
        return self.db.execute(sql.replace('cyca_workflow.','').replace('%s','?'),parameters)
    def close(self):self.closed=True;self.db.close()


class FakeTransport:
    def __init__(self,fail=False):self.fail=fail;self.calls=0
    def send(self,payload,pdf,key):
        self.calls+=1
        if self.fail:raise TimeoutError()
        return {'id':'message','threadId':'thread'}
    def find(self,key,sender,**kwargs):return [{'id':'message','threadId':'thread'}]


class PostgresContractTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'db'
        from document_delivery import DeliveryStore
        sqlite=DeliveryStore(self.path);InvoiceRegistry(sqlite)
        with sqlite.connect() as db:
            db.execute('CREATE TABLE schema_version(version INTEGER)');db.execute('INSERT INTO schema_version VALUES(1)')
        self.lock=threading.Lock();self.queries=[];self.connections=[]
        def factory(dsn):
            conn=FakeDriver(self.path,self.lock,self.queries);self.connections.append(conn);return conn
        self.store=PostgresDeliveryStore('test-only',connect_factory=factory)
        self.args=dict(sender='sender@example.com',recipient='client@example.com',subject='資料',body='確認ください',filename='工事.pdf',pdf=b'%PDF-test',document_id='doc')
    def test_constructors_never_execute_ddl(self):
        InvoiceRegistry(self.store)
        self.assertFalse(any(q.lstrip().upper().startswith('CREATE') for q in self.queries))
        self.assertTrue(all(c.closed for c in self.connections))
        self.assertTrue(any('pg_advisory_xact_lock' in q for q in self.queries))
    def test_new_uuid_and_cross_employee_unknown_cannot_resend(self):
        key=self.store.prepare('company','one',**self.args)
        self.store.approve('company',key,self.store.get('company',key)['fingerprint'],'employee-a')
        transport=FakeTransport(True)
        with self.assertRaises(RuntimeError):self.store.send('company',key,transport)
        self.assertEqual(key,self.store.prepare('company','two',**self.args))
        with self.assertRaises(ValueError):self.store.prepare('company','three',**dict(self.args,body='changed'))
        with self.assertRaises(ValueError):self.store.cancel_unsent('company',key)
        with self.assertRaises(ValueError):self.store.send('company',key,transport)
        self.assertEqual(transport.calls,1)
    def test_concurrent_prepare_is_one_business_action(self):
        with ThreadPoolExecutor(2) as pool:
            keys=list(pool.map(lambda key:self.store.prepare('company',key,**self.args),['a','b']))
        self.assertEqual(keys[0],keys[1])
    def test_company_invoice_uniqueness(self):
        registry=InvoiceRegistry(self.store);invoice=make();fingerprint=invoice_fingerprint(invoice)
        pdf=registry.finalize('company',invoice,fingerprint,'employee-a')
        self.assertEqual(pdf,registry.finalize('company',invoice,fingerprint,'employee-b'))
        invoice['due_date']='2027-01-01'
        with self.assertRaises(ValueError):registry.finalize('company',invoice,invoice_fingerprint(invoice),'employee-b')
    def test_query_parameter_translation(self):
        self.assertEqual(_parameters("SELECT '?' WHERE x=? AND y='can''t?'"),"SELECT '?' WHERE x=%s AND y='can''t?'")
    def test_no_arbitrary_runtime_ddl(self):
        with self.store.connect() as db:
            with self.assertRaises(ValueError):db.execute('DROP TABLE deliveries')
    def test_remote_dsn_requires_verified_tls(self):
        try: import psycopg
        except ImportError: self.skipTest('Optional psycopg not installed')
        with self.assertRaises(ValueError) as failure:
            PostgresDeliveryStore('postgresql://user:never-print-secret@localhost/db?sslmode=require')
        self.assertNotIn('never-print-secret',str(failure.exception))

    def test_migration_private_and_unique(self):
        sql=Path(__file__).with_name('workflow_schema.sql').read_text()
        for expected in ('REVOKE ALL ON SCHEMA cyca_workflow FROM PUBLIC','ENABLE ROW LEVEL SECURITY',
                         'delivery_active_document','delivery_active_pdf',"WHERE state <> 'cancelled'",'PRIMARY KEY(owner, number)'):
            self.assertIn(expected,sql)
        self.assertNotIn('CREATE POLICY',sql)

if __name__=='__main__':unittest.main()
