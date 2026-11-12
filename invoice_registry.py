"""Persistent unique invoice numbers and frozen final PDFs."""
import json
from document_delivery import now
from invoice_documents import invoice_fingerprint, render_invoice_pdf

class InvoiceRegistry:
    def __init__(self, delivery_store):
        self.store = delivery_store
        with self.store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS invoices
                (owner TEXT NOT NULL, number TEXT NOT NULL, fingerprint TEXT NOT NULL,
                 payload TEXT NOT NULL, pdf BLOB NOT NULL, actor TEXT NOT NULL,
                 approved_at TEXT NOT NULL, PRIMARY KEY(owner, number))''')

    def finalize(self, owner, invoice, fingerprint, actor):
        if not owner or not actor or fingerprint != invoice_fingerprint(invoice):
            raise ValueError('最新の請求書の承認が必要です。')
        pdf = render_invoice_pdf(invoice, preview=False, approved_fingerprint=fingerprint)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT fingerprint,pdf FROM invoices WHERE owner=? AND number=?',
                             (owner, invoice['invoice_number'])).fetchone()
            if row:
                if row['fingerprint'] != fingerprint:
                    raise ValueError('この請求書番号は使用済みです。既存の請求書は上書きしません。')
                return row['pdf']
            db.execute('INSERT INTO invoices VALUES(?,?,?,?,?,?,?)',
                       (owner,invoice['invoice_number'],fingerprint,json.dumps(invoice,ensure_ascii=False),pdf,actor,now()))
        return pdf
