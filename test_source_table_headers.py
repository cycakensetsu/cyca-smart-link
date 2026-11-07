"""Native PDF headers must match common net-price column labels."""
import unittest

from source_table import native_header_columns, native_quantity_unit, native_cell_text
from unittest.mock import Mock


class NativeHeaderTest(unittest.TestCase):
    def test_cell_layout_restores_decimal_position_without_changing_characters(self):
        page=Mock()
        page.get_text.return_value='鋼板-3.5t 曲げ有'
        self.assertEqual(native_cell_text(page,(0,0,10,10),'鋼板 35t 曲げ有\n- .'), '鋼板-3.5t 曲げ有')
        page.get_text.return_value='鋼板-5t'
        self.assertEqual(native_cell_text(page,(0,0,10,10),'鋼板-3.5t'), '鋼板-3.5t')

    def test_net_amount_header_variants(self):
        for label in ('金額', '金額(税抜)', '金額（税抜）', '金額 ( 税抜き )',
                      '税抜金額', '金額（税別）', '金額(円)', '見積金額'):
            with self.subTest(label=label):
                self.assertEqual(native_header_columns(['工事名', '数量', '単位', '単価', label]),
                                 (1, 2, 3, 4))

    def test_tax_inclusive_or_ambiguous_header_is_not_treated_as_net_cost(self):
        self.assertIsNone(native_header_columns(['工事名', '数量', '単位', '単価', '金額(税込)']))
        self.assertIsNone(native_header_columns(['工事名', '数量', '単価', '金額', '金額(税抜)']))

    def test_quantity_and_unit_can_be_in_one_or_two_cells(self):
        self.assertEqual(native_quantity_unit('12㎡'), (12, '㎡'))
        self.assertEqual(native_quantity_unit('12.0', 'm2'), (12, '㎡'))
        self.assertIsNone(native_quantity_unit('12.0', None))


if __name__ == '__main__':
    unittest.main()
