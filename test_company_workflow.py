import tempfile
from pathlib import Path
import unittest
from document_delivery import DeliveryStore
from document_workflow_service import DocumentWorkflowService
from invoice_registry import InvoiceRegistry
from invoice_documents import invoice_fingerprint
from test_invoice_documents import make
from test_document_delivery import Fake
from workflow_runtime import company_scope

class CompanyTests(unittest.TestCase):
    def test_shared_company_duplicate_and_actor(self):
        with tempfile.TemporaryDirectory() as temp:
            store=DeliveryStore(Path(temp)/'db'); fake=Fake(True)
            a=DocumentWorkflowService(store,'employee-a',fake,company_id='company')
            b=DocumentWorkflowService(store,'employee-b',fake,company_id='company')
            args=dict(sender='sender@example.com',recipient='client@example.com',subject='資料',body='確認ください',filename='工事.pdf',pdf=b'%PDF-test',document_id='doc')
            record=a.prepare_email('one',**args)
            a.approve_email(record['id'],record['fingerprint'])
            self.assertEqual(store.get('company',record['id'])['actor'],'employee-a')
            with self.assertRaises(RuntimeError):a.send_email(record['id'])
            second=b.prepare_email('two',**args)
            self.assertEqual(record['id'],second['id'])
            with self.assertRaises(ValueError):b.send_email(second['id'])
            other=DocumentWorkflowService(store,'other',fake,company_id='other-company')
            with self.assertRaises(KeyError):other.review(record['id'])
    def test_company_invoice_numbers(self):
        with tempfile.TemporaryDirectory() as temp:
            registry=InvoiceRegistry(DeliveryStore(Path(temp)/'db')); invoice=make()
            registry.finalize('company',invoice,invoice_fingerprint(invoice),'employee-a')
            invoice['due_date']='2027-01-31'
            with self.assertRaises(ValueError):registry.finalize('company',invoice,invoice_fingerprint(invoice),'employee-b')
    def test_read_only_actor_cannot_send_or_approve(self):
        with tempfile.TemporaryDirectory() as temp:
            service=DocumentWorkflowService(DeliveryStore(Path(temp)/'db'),'reader',Fake(),company_id='company',permissions=['read'])
            with self.assertRaises(ValueError): service.approve_email('id','fingerprint')
            with self.assertRaises(ValueError): service.send_email('id')
            with self.assertRaises(ValueError): service.cancel_email('id')

    def test_mutation_without_read_is_rejected_before_transport(self):
        with tempfile.TemporaryDirectory() as temp:
            fake=Fake()
            service=DocumentWorkflowService(DeliveryStore(Path(temp)/'db'),'employee',fake,company_id='company',permissions=['prepare','approve','send'])
            with self.assertRaises(ValueError): service.approve_email('id','fingerprint')
            with self.assertRaises(ValueError): service.send_email('id')
            self.assertEqual(fake.calls,0)

    def test_membership(self):
        config={'company_id':'company','company_members':['employee-a','employee-b']}
        self.assertEqual(company_scope(config,'employee-a'),'company')
        with self.assertRaises(ValueError):company_scope(config,'unknown')

if __name__=='__main__':unittest.main()
