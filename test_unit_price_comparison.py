"""Unit-rate advice must not treat source totals or discounts as product rates."""
import copy
import unittest

from estimate_pipeline import build_intermediate_dataframe
from estimate_review import build_draft, review_rows


class UnitPriceComparisonTest(unittest.TestCase):
    def build(self, name, amounts, *, unit='式', summary=False, customer_discount=0):
        sources, records = [], []
        for number, amount in enumerate(amounts):
            filename = f'synthetic-{number}.pdf'
            sources.append(dict(id=f'source-{number}', name=filename, size=1,
                                tax_basis='税抜', tax_rate=.1, confirmed=True,
                                net_total=100000 + amount))
            for item_name, quantity, item_unit, price, value in (
                    (f'架空基準工事{number}', 10, '㎡', 10000, 100000),
                    (name, 1, unit, amount, amount)):
                records.append(dict(品名=item_name, 仕様='仕様A', 数量=quantity,
                                    単位=item_unit, 単価=price, 金額=value,
                                    見積元=f'架空業者{number}', 工事種別='塗装工事',
                                    __source_name=filename, __page_number=1))
        frame, _ = build_intermediate_dataframe(records)
        rows = review_rows(frame, sources)
        for row in rows:
            if row['品名'] == name:
                row['集計のみ'] = summary
        before = copy.deepcopy(rows)
        source_before = copy.deepcopy(sources)
        draft = build_draft(rows, copy.deepcopy(rows), sources,
                            discount=customer_discount,
                            metadata={'値引き理由': '架空検証の契約調整'})
        self.assertEqual(rows, before)
        self.assertEqual(sources, source_before)
        self.assertEqual(draft['payload']['原本明細'], before)
        self.assertEqual(draft['payload']['確認明細'], before)
        priced = {row['行ID']: row for row in draft['payload']['計算明細']}
        for original in before:
            result = priced[original['行ID']]
            for field in ('数量', '単位', '原価単価', '原価金額'):
                self.assertEqual(result[field], original[field])
            self.assertEqual(result['見積金額'], original['原価金額'])
            self.assertEqual(result['見積単価'], original['原価単価'])
        cost = sum(source['net_total'] for source in sources)
        self.assertEqual(draft['payload']['合計']['仕入原価'], cost)
        self.assertEqual(draft['payload']['合計']['売価小計'], cost)
        self.assertEqual(draft['payload']['合計']['顧客値引き'], customer_discount)
        self.assertEqual(draft['payload']['合計']['税抜売価'], cost + customer_discount)
        self.assertFalse(any(item['レベル'] == '停止' for item in draft['payload']['指摘']))
        return draft

    def kinds(self, draft):
        return [item['種類'] for item in draft['payload']['指摘']]

    def test_summary_expenses_keep_common_expense_check_without_rate_warning(self):
        draft = self.build('養生諸経費', [10000, 20000], summary=True,
                           customer_discount=-3000)
        self.assertNotIn('売価単価差', self.kinds(draft))
        self.assertIn('共通費重複疑い', self.kinds(draft))

    def test_negative_source_adjustments_remain_in_totals_without_rate_warning(self):
        draft = self.build('調整値引き', [-1000, -2000], customer_discount=-3000)
        self.assertNotIn('売価単価差', self.kinds(draft))
        discounts = [row['見積金額'] for row in draft['payload']['計算明細']
                     if row['品名'] == '調整値引き']
        self.assertEqual(discounts, [-1000, -2000])
        self.assertEqual(draft['payload']['合計']['税抜売価'], 194000)

    def test_positive_lump_sum_and_measured_work_still_need_rate_confirmation(self):
        for unit in ('式', '㎡'):
            with self.subTest(unit=unit):
                draft = self.build('検証用機器取付', [10000, 20000], unit=unit)
                self.assertIn('売価単価差', self.kinds(draft))

    def test_identical_summary_expenses_still_need_duplicate_confirmation(self):
        draft = self.build('養生諸経費', [10000, 10000], summary=True)
        self.assertIn('明細重複疑い', self.kinds(draft))
        self.assertIn('共通費重複疑い', self.kinds(draft))
        self.assertNotIn('売価単価差', self.kinds(draft))


if __name__ == '__main__':
    unittest.main()
