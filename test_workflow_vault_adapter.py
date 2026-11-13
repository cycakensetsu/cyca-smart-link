import tempfile
import unittest
from pathlib import Path
from document_delivery import DeliveryStore
from invoice_registry import InvoiceRegistry
from invoice_documents import invoice_fingerprint
from test_invoice_documents import make
from workflow_vault_adapter import make_resolvers

class VaultAdapterTests(unittest.TestCase):
    def test_final_invoice_available_only_in_company(self):
        with tempfile.TemporaryDirectory() as temp:
            store=DeliveryStore(Path(temp)/'db'); invoice=make(); fp=invoice_fingerprint(invoice)
            pdf=InvoiceRegistry(store).finalize('company',invoice,fp,'employee')
            class Empty:
                def entries(self):return []
            _,document=make_resolvers(lambda owner:Empty(),store)
            self.assertEqual(document('company',fp)['pdf'],pdf)
            with self.assertRaises(KeyError):document('other',fp)

if __name__=='__main__':unittest.main()
