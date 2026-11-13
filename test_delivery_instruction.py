import unittest
from delivery_instruction import extract_recipient

class InstructionTests(unittest.TestCase):
    def test_explicit_single_address(self):
        for value in ('この見積書を user@example.com にメールで送って', '「user@example.com」に送って',
                      '宛先：\n<user@example.com>\nこの請求書をお願いします', '"user@example.com"',
                      'USER+invoice@sub.example.com'):
            with self.subTest(value=value):self.assertIn('@',extract_recipient(value))
        self.assertEqual(extract_recipient('宛先：USER+invoice@sub.example.com'),'USER+invoice@sub.example.com')
    def test_ambiguous_malformed_and_injection(self):
        for value in ('柿山様に送って', '', None,'a@example.com b@example.com',
                      '引用 a@example.com\n宛先 a@example.com','a..b@example.com','a@example..com',
                      '.a@example.com','a.@example.com','a@-example.com','a@example.com-',
                      'a@localhost','a＠example.com','a@\nexample.com','a@example.com\nBcc: 他の人',
                      '"a b"@example.com','https://a@example.com','a@example.com?bcc=other',
                      'a@example.com\x00', 'a@example.com_ignored', '!a@example.com',
                      'tést@example.com', 'foo@example.comé', 'user@example.com\u200b.evil',
                      'e\u0301user@example.com', 'user@example.com\u0301', 'user@example.com\ufeff.evil',
                      '宛先user@example.com', 'user@example.comに送って',
                      'user@example.com\u00a0.evil'):
            with self.subTest(value=value),self.assertRaises(ValueError):extract_recipient(value)

if __name__=='__main__':unittest.main()
