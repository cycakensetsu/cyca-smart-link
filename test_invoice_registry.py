import tempfile
import unittest
from pathlib import Path
from document_delivery import DeliveryStore
from invoice_registry import InvoiceRegistry
from invoice_documents import invoice_fingerprint
from test_invoice_documents import make

class RegistryTests(unittest.TestCase):
    def test_duplicate_number_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            registry=InvoiceRegistry(DeliveryStore(Path(temp)/'db'))
            invoice=make(); fp=invoice_fingerprint(invoice)
            pdf=registry.finalize('owner',invoice,fp,'owner')
            self.assertTrue(pdf.startswith(b'%PDF-'))
            self.assertEqual(registry.finalize('owner',invoice,fp,'owner'),pdf)
            invoice['due_date']='2027-01-01'
            with self.assertRaises(ValueError): registry.finalize('owner',invoice,invoice_fingerprint(invoice),'owner')
    def test_reject_changed_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            registry=InvoiceRegistry(DeliveryStore(Path(temp)/'db'))
            with self.assertRaises(ValueError): registry.finalize('owner',make(),'bad','owner')

if __name__=='__main__': unittest.main()
