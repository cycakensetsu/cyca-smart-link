"""Opt-in real DB tests. Only a temporary local Unix-socket cyca_workflow_test.
Operator provisions/destroys cluster. No setup, DDL, remote DB or real email.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
from email import policy
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import threading
import unittest
import uuid
try:
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    from psycopg.rows import dict_row
except ImportError:
    psycopg=None


def local_options(dsn):
    if psycopg is None: raise unittest.SkipTest('psycopg unavailable')
    if not dsn: raise unittest.SkipTest('CYCA_TEST_POSTGRES_DSN not specified')
    try: values=conninfo_to_dict(dsn)
    except Exception: raise ValueError('Invalid isolated test DSN') from None
    host=values.get('host','')
    if (values.get('dbname')!='cyca_workflow_test' or not values.get('user')
        or not host.startswith(('/tmp/cyca-','/private/tmp/cyca-')) or ',' in host
        or any(values.get(k) for k in ('hostaddr','service','options'))):
        raise ValueError('Only isolated cyca_workflow_test Unix socket allowed')
    path=Path(host).resolve()
    roots=(Path('/tmp'),Path('/private/tmp'))
    dedicated=(path.parent in roots and path.name.startswith('cyca-'))
    socket_child=(path.name=='socket' and path.parent.parent in roots and path.parent.name.startswith('cyca-'))
    if not (dedicated or socket_child):
        raise ValueError('Socket must be in dedicated temporary test directory')
    if os.environ.get('PGSERVICE') or os.environ.get('PGHOSTADDR'):
        raise ValueError('Inherited remote connection selectors are forbidden')
    values.update(host=str(path),sslmode='disable',connect_timeout='5',application_name='cyca-isolated-tests')
    return values


def connect_local(options,user=None):
    values=dict(options)
    if user: values['user']=user
    return psycopg.connect(**values,row_factory=dict_row)


@unittest.skipIf(psycopg is None,'psycopg unavailable')
class GuardTests(unittest.TestCase):
    def test_remote_wrong_database_and_selector_rejected(self):
        for dsn in ('host=db.example.com dbname=cyca_workflow_test user=runtime',
                    'host=/tmp/cyca-test dbname=postgres user=runtime',
                    'host=/tmp/cyca-test dbname=cyca_workflow_test user=runtime hostaddr=127.0.0.1',
                    'host=/tmp/cyca-test dbname=cyca_workflow_test user=runtime service=production',
                    'host=/tmp/cyca-test/nested dbname=cyca_workflow_test user=runtime'):
            with self.subTest(dsn=dsn),self.assertRaises(ValueError):local_options(dsn)


class RealPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.options=local_options(os.environ.get('CYCA_TEST_POSTGRES_DSN'))
        with connect_local(cls.options) as db:
            identity=db.execute('SELECT current_database() AS db,inet_server_addr() AS address').fetchone()
            if identity['db']!='cyca_workflow_test' or identity['address'] is not None:
                raise RuntimeError('Not isolated Unix-socket database')
            role=db.execute('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user').fetchone()
            if any(role.values()):raise RuntimeError('Dedicated non-administrative runtime role required')
        from postgres_workflow_store import PostgresDeliveryStore
        cls.store=PostgresDeliveryStore('isolated-local-test',connect_factory=lambda ignored:connect_local(cls.options))

    def setUp(self):
        self.company='synthetic-'+uuid.uuid4().hex
        self.args=dict(sender='sender@example.com',recipient='client@example.com',subject='架空テスト',body='架空の試験です。',
                       filename='架空工事見積書20261112.pdf',pdf=b'%PDF-1.4 synthetic-only',document_id=uuid.uuid4().hex)

    def test_concurrent_prepare_one_business_action(self):
        with ThreadPoolExecutor(8) as pool:
            keys=list(pool.map(lambda n:self.store.prepare(self.company,str(n),**self.args),range(8)))
        self.assertEqual(len(set(keys)),1)
        self.assertEqual(len(self.store.history(self.company)),1)

    def test_unknown_new_uuid_no_resend_or_cancel(self):
        key=self.store.prepare(self.company,'one',**self.args)
        self.store.approve(self.company,key,self.store.get(self.company,key)['fingerprint'],'employee-a')
        class Failing:
            calls=0
            def send(self,*args):self.calls+=1;raise TimeoutError('simulated lost response')
        transport=Failing()
        with self.assertRaises(RuntimeError):self.store.send(self.company,key,transport)
        self.assertEqual(self.store.prepare(self.company,'new-uuid',**self.args),key)
        with self.assertRaises(ValueError):self.store.prepare(self.company,'changed',**dict(self.args,body='changed'))
        with self.assertRaises(ValueError):self.store.cancel_unsent(self.company,key)
        with self.assertRaises(ValueError):self.store.send(self.company,key,transport)
        self.assertEqual(transport.calls,1)
        self.assertEqual(self.store.get(self.company,key)['state'],'unknown')

    def test_company_application_scope_isolation(self):
        key=self.store.prepare(self.company,'one',**self.args)
        with self.assertRaises(KeyError):self.store.get(self.company+'-other',key)
        self.assertEqual(self.store.history(self.company+'-other'),[])
        other=self.store.prepare(self.company+'-other','one',**self.args)
        self.assertNotEqual(key,other)

    def test_invoice_number_concurrent_and_immutable(self):
        from invoice_registry import InvoiceRegistry
        from invoice_documents import invoice_fingerprint
        from test_invoice_documents import make
        invoice=make(invoice_number=uuid.uuid4().hex);registry=InvoiceRegistry(self.store)
        def finalize(actor):return registry.finalize(self.company,invoice,invoice_fingerprint(invoice),actor)
        with ThreadPoolExecutor(4) as pool:outputs=list(pool.map(finalize,['a','b','c','d']))
        self.assertTrue(all(v==outputs[0] for v in outputs))
        changed=dict(invoice,due_date='2027-01-31')
        with self.assertRaises(ValueError):registry.finalize(self.company,changed,invoice_fingerprint(changed),'b')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) AS n FROM invoices WHERE owner=?',(self.company,)).fetchone()['n'],1)

    def test_db_unique_constraints_without_application_guard(self):
        key=self.store.prepare(self.company,'original',**self.args);original=self.store.get(self.company,key)
        for change in ({'pdf_sha256':hashlib.sha256(b'different').hexdigest()},{'document_id':'different-document'}):
            payload=dict(original['payload'],**change)
            with self.subTest(change=change),self.assertRaises(psycopg.errors.UniqueViolation):
                with self.store.connect() as db:
                    db.execute('INSERT INTO deliveries (id,owner,request_key,payload,pdf,fingerprint,state,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                               (uuid.uuid4().hex,self.company,uuid.uuid4().hex,json.dumps(payload),self.args['pdf'],uuid.uuid4().hex,'review',original['updated_at']))

    def test_runtime_and_readonly_privileges(self):
        with connect_local(self.options) as db:
            grants=db.execute("SELECT has_schema_privilege(current_user,'cyca_workflow','CREATE') AS create_schema,has_table_privilege(current_user,'cyca_workflow.deliveries','DELETE') AS delete_data").fetchone()
            self.assertFalse(grants['create_schema']);self.assertFalse(grants['delete_data'])
        readonly=os.environ.get('CYCA_TEST_POSTGRES_READONLY_USER','cyca_workflow_readonly')
        with connect_local(self.options,user=readonly) as db:db.execute('SELECT id FROM cyca_workflow.deliveries LIMIT 1')
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with connect_local(self.options,user=readonly) as db:
                db.execute("UPDATE cyca_workflow.deliveries SET state='review' WHERE owner=%s",(self.company,))

    def test_migration_contract(self):
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT version FROM schema_version').fetchone()['version'],1)
            indexes=db.execute("SELECT indexname FROM pg_indexes WHERE schemaname='cyca_workflow'").fetchall()
            self.assertTrue({'delivery_active_document','delivery_active_pdf'}<={v['indexname'] for v in indexes})
            tables=db.execute("SELECT relname,relrowsecurity FROM pg_class JOIN pg_namespace n ON n.oid=relnamespace WHERE n.nspname='cyca_workflow' AND relname IN ('deliveries','invoices','schema_version')").fetchall()
            self.assertEqual(len(tables),3);self.assertTrue(all(r['relrowsecurity'] for r in tables))

    def test_mime_fake_gmail_concurrent_send_once(self):
        from document_delivery import GmailTransport
        captured=[];lock=threading.Lock()
        class Call:
            def __init__(self,result):self.result=result
            def execute(self,**kwargs):return self.result
        class Gmail:
            def users(self):return self
            def getProfile(self,**kwargs):return Call({'emailAddress':'sender@example.com'})
            def messages(self):return self
            def send(self,**kwargs):
                with lock:captured.append(kwargs['body'])
                return Call({'id':'synthetic-gmail-id','threadId':'synthetic-thread'})
        transport=GmailTransport(Gmail());key=self.store.prepare(self.company,'one',**self.args)
        self.store.approve(self.company,key,self.store.get(self.company,key)['fingerprint'],'employee')
        def send(_):
            try:return self.store.send(self.company,key,transport)
            except ValueError:return None
        with ThreadPoolExecutor(4) as pool:list(pool.map(send,range(4)))
        self.assertEqual(len(captured),1)
        message=BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(captured[0]['raw']))
        self.assertEqual(message['To'],self.args['recipient'])
        parts=list(message.iter_attachments());self.assertEqual(len(parts),1)
        self.assertEqual(parts[0].get_filename(),self.args['filename'])
        self.assertEqual(parts[0].get_payload(decode=True),self.args['pdf'])
        self.assertEqual(self.store.get(self.company,key)['state'],'sent')

if __name__=='__main__':unittest.main()
