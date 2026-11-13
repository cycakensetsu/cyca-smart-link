import copy
import unittest
from estimate_review import approve, digest
from invoice_documents import build_invoice, invoice_filename, invoice_fingerprint, render_invoice_pdf


def fixture():
    payload = {'表題': {'宛名': '架空テスト株式会社 御中', '工事名称': '看板交換工事', '見積日': '2026年11月1日', '顧客税率': .10},
               '合計': {'税抜売価': 490000, '税額': 49000, '税込売価': 539000}}
    return approve({'payload': payload, 'fingerprint': digest(payload), 'unresolved': []}, 'test-owner')


def make(**kwargs):
    args = dict(invoice_number='TEST-20261112-001', issue_date='2026-11-12', due_date='2026-12-31',
                issuer={'name': '架空建設株式会社', 'details': ['架空市テスト町1番地'], 'payment_details': ['架空銀行 テスト支店（テスト情報）']})
    args.update(kwargs)
    return build_invoice(fixture(), **args)


class InvoiceTests(unittest.TestCase):
    def test_preserves_amounts_and_source(self):
        inv = make()
        self.assertEqual((inv['net'], inv['tax'], inv['gross']), (490000, 49000, 539000))
        self.assertRegex(inv['estimate_approval_id'], r'^[a-f0-9]{64}$')
        self.assertEqual(inv['estimate_date'], '2026-11-01')
        self.assertEqual(invoice_filename(inv), '看板交換工事請求書_20261112.pdf')

    def test_changed_source_rejected(self):
        source = fixture()
        source['payload']['合計']['税抜売価'] = 1
        with self.assertRaises(ValueError):
            build_invoice(source, invoice_number='A', issue_date='2026-11-12', due_date='2026-12-31', issuer={'name': '架空会社'})

    def test_bad_dates_rejected(self):
        for values in ({'due_date': '2026-11-11'}, {'issue_date': '2026-02-30'}, {'estimate_date': '2026-11-02'}):
            with self.subTest(values=values), self.assertRaises(ValueError): make(**values)

    def test_requires_explicit_issuer_and_number(self):
        for values in ({'issuer': {}}, {'invoice_number': ''}, {'issuer': {'name': 'A', 'details': ['X\nY']}}):
            with self.subTest(values=values), self.assertRaises(ValueError): make(**values)

    def test_final_requires_exact_review_fingerprint(self):
        inv = make()
        with self.assertRaises(ValueError): render_invoice_pdf(inv, preview=False)
        fingerprint = invoice_fingerprint(inv)
        inv['due_date'] = '2027-01-31'
        with self.assertRaises(ValueError): render_invoice_pdf(inv, preview=False, approved_fingerprint=fingerprint)

    def test_pdf_has_one_lump_sum_and_preview_mark(self):
        import pymupdf
        inv = make()
        with pymupdf.open(stream=render_invoice_pdf(inv), filetype='pdf') as doc:
            text = ''.join(page.get_text() for page in doc)
            self.assertEqual(len(doc), 1)
            self.assertIn('確認用・未確定', text)
            self.assertIn('1\n式', text)
            self.assertGreater(doc[0].rect.width, doc[0].rect.height)
            self.assertIn('2026年11月1日付 別見積書のとおり', text)
            self.assertIn('539,000', text)
        with pymupdf.open(stream=render_invoice_pdf(inv, preview=False, approved_fingerprint=invoice_fingerprint(inv)), filetype='pdf') as doc:
            self.assertNotIn('未確定', doc[0].get_text())

    def test_inconsistent_approved_totals_rejected(self):
        source = fixture()
        source['payload']['合計']['税込売価'] = 1
        payload = source['payload']
        source = approve({'payload': payload, 'fingerprint': digest(payload), 'unresolved': []}, 'test-owner')
        with self.assertRaises(ValueError):
            build_invoice(source, invoice_number='A', issue_date='2026-11-12', due_date='2026-12-31', issuer={'name': '架空会社'})

    def test_issuer_is_copied_and_markup_is_escaped(self):
        issuer = {'name': 'A & B', 'details': ['<会社情報>']}
        inv = make(issuer=issuer)
        issuer['name'] = 'CHANGED'
        self.assertEqual(inv['issuer']['name'], 'A & B')
        self.assertTrue(render_invoice_pdf(inv).startswith(b'%PDF-'))

    def test_tax_rate_must_be_explicit_and_is_preserved(self):
        for rate in (0, .08, .10, None, .15):
            source = fixture()
            payload = source['payload']
            if rate is None:
                del payload['表題']['顧客税率']
            else:
                payload['表題']['顧客税率'] = rate
                payload['合計']['税額'] = int(490000 * rate)
                payload['合計']['税込売価'] = 490000 + payload['合計']['税額']
            source = approve({'payload': payload, 'fingerprint': digest(payload), 'unresolved': []}, 'owner')
            args = dict(invoice_number='A', issue_date='2026-11-12', due_date='2026-12-31', issuer={'name': '架空会社'})
            with self.subTest(rate=rate):
                if rate is None or rate == .15:
                    with self.assertRaises(ValueError): build_invoice(source, **args)
                else:
                    result = build_invoice(source, **args)
                    self.assertEqual(result['tax_rate'], rate)
                    self.assertEqual(result['tax'], payload['合計']['税額'])

    def test_filename_cannot_escape(self):
        inv = make(); inv['project'] = '../看板/交換:*?'
        name = invoice_filename(inv)
        self.assertNotIn('/', name); self.assertNotIn('?', name); self.assertFalse(name.startswith('.'))


if __name__ == '__main__': unittest.main()
