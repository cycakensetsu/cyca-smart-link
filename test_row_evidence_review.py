"""Synthetic independent quotations catch descriptive errors without prices."""
import copy
import hashlib
import unittest

from row_evidence_review import compare_row_evidence, parse_row_evidence
from source_annotations import annotation_cache_ready


SID = hashlib.sha256(b'synthetic original for row-evidence tests').hexdigest()


def evidence(name='架空装置A', spec='TYPE-A', note='本体だけ', page=1, section='設備A'):
    return {'ページ': page, '見積区分': section, '品名': name, '仕様': spec, '備考': note,
            '原文': '\n'.join((name, spec, note))}


def row(name='架空装置A', spec='TYPE-A', note='本体だけ', page=1, row_id='r1'):
    return {'原本ID': SID, '行ID': row_id, 'ページ': page, '品名': name,
            '仕様': spec, '備考': note, '数量': 2, '原価単価': 101, '原価金額': 202,
            '顧客備考': '', '採用': True}


def source(items, pages=1):
    return {'name': 'synthetic.pdf', 'id': SID, 'pages': pages,
            'amount_reading': {'annotation_reviews': [{
                'source_name': 'synthetic.pdf', 'sha256': SID, 'schema_version': 2,
                'status': 'completed', 'checked_pages': list(range(1, pages + 1)),
                'row_evidence': items}]}}


class RowEvidenceReviewTest(unittest.TestCase):
    def parse(self, items, **changes):
        data = {'元ファイル': 'synthetic.pdf', 'schema_version': 2, '行照合': items}
        data.update(changes)
        return parse_row_evidence(data, 'synthetic.pdf', 2, [1, 2])

    def test_matching_rows_complete_without_mutating_original_prices_notes_or_customer_fields(self):
        rows, src = [row()], source([evidence()])
        before = copy.deepcopy((rows, src))
        self.assertEqual(compare_row_evidence(rows, src), {
            'schema_version': 2, 'status': 'completed', 'findings': []})
        self.assertEqual((rows, src), before)

    def test_shifted_three_notes_and_two_product_character_errors_all_detected(self):
        names = ['架空装置A', '架空設置B', '架空配管C', '架空壁D', '架空壁E']
        notes = ['本体だけ', '台座と固定を含む', '保温と弁を含む', '', '']
        specs = ['', '', '', 'テストプレミアム材料', '下地・テストプレミアム材料']
        items = [evidence(n, s, note) for n, s, note in zip(names, specs, notes)]
        rows = [row(n, s, note, row_id='r'+str(i)) for i, (n, s, note) in enumerate(zip(names, specs, notes))]
        rows[0]['備考'] += '・'+notes[1]
        rows[1]['備考'], rows[2]['備考'] = notes[2], ''
        for target in rows[3:]:
            target['仕様'] = target['仕様'].replace('プレミアム', 'ププレミアム')
        before = copy.deepcopy(rows)
        result = compare_row_evidence(rows, source(items))
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(len(result['findings']), 5)
        self.assertEqual([f['fields'] for f in result['findings']], [['備考']]*3+[['仕様']]*2)
        self.assertTrue(all(f['reason'] == 'field_difference' for f in result['findings']))
        self.assertEqual(rows, before)
        for corrected, original in zip(rows, items):
            corrected.update({k: original[k] for k in ('仕様', '備考')})
        self.assertEqual(compare_row_evidence(rows, source(items))['status'], 'completed')
        self.assertEqual([[r[k] for k in ('数量', '原価単価', '原価金額')] for r in rows],
                         [[r[k] for k in ('数量', '原価単価', '原価金額')] for r in before])

    def test_same_name_different_pages_only_compare_within_each_page(self):
        rows = [row(page=1), row(spec='TYPE-B', page=2, row_id='r2')]
        src = source([evidence(page=2, spec='TYPE-B'), evidence(page=1)], pages=2)
        self.assertEqual(compare_row_evidence(rows, src)['status'], 'completed')
        rows[0]['仕様'] = 'TYPE-B'
        self.assertEqual(compare_row_evidence(rows, src)['findings'][0]['row_id'], 'r1')

    def test_same_page_repeated_name_never_disambiguated_by_section_or_prices(self):
        rows = [row(), row(row_id='r2', note='別工区')]
        rows[1]['原価金額'] = 303
        src = source([evidence(section='工区A'), evidence(section='工区B', note='別工区')])
        result = compare_row_evidence(rows, src)
        self.assertEqual([f['reason'] for f in result['findings']], ['ambiguous_row']*2)

    def test_duplicate_independent_quotes_remain_ambiguous(self):
        result = compare_row_evidence([row()], source([evidence(), evidence()]))
        self.assertEqual(result['findings'][0]['reason'], 'ambiguous_row')

    def test_changed_name_is_missing_and_unmatched_never_fuzzy_correction(self):
        result = compare_row_evidence([row(name='架空装置AA')], source([evidence()]))
        self.assertEqual({f['reason'] for f in result['findings']}, {'missing_row', 'unmatched_evidence'})

    def test_evidence_from_wrong_original_or_filename_does_not_verify(self):
        for key, value in [('sha256', 'a'*64), ('source_name', 'other.pdf')]:
            src = source([evidence()])
            src['amount_reading']['annotation_reviews'][0][key] = value
            self.assertEqual(compare_row_evidence([row()], src)['status'], 'missing')

    def test_missing_empty_legacy_and_incomplete_evidence_never_verify(self):
        variants = [source([]), source([evidence()]), source([evidence()]), source([evidence()])]
        variants[1]['amount_reading']['annotation_reviews'][0].pop('schema_version')
        variants[2]['amount_reading']['annotation_reviews'][0]['checked_pages'] = []
        variants[3]['amount_reading']['annotation_reviews'][0]['status'] = 'needs_review'
        for src in variants:
            with self.subTest(src=src):
                self.assertNotEqual(compare_row_evidence([row()], src)['status'], 'completed')
        for invalid in [None, {}, True, 'invalid']:
            src = source([]); src['amount_reading']['annotation_reviews'] = invalid
            self.assertEqual(compare_row_evidence([row()], src)['status'], 'missing')

    def test_fields_must_be_present_in_their_original_quotation(self):
        for key in ('品名', '仕様', '備考'):
            item = evidence()
            item[key] += '無根拠補完'
            accepted, reasons = self.parse([item])
            self.assertEqual(accepted, [])
            self.assertIn('unquoted_row_field', reasons)
        item = evidence(spec='ププレミアム')
        item['原文'] = '架空装置A\nプレミアム\n本体だけ'
        self.assertEqual(self.parse([item])[0], [])

    def test_whitespace_wraps_are_ignored_but_characters_and_punctuation_are_not(self):
        item = evidence(spec='架空材料・A')
        item['原文'] = '架空装置A 架空\n材料・A 本体だけ'
        self.assertEqual(self.parse([item])[1], [])
        self.assertEqual(compare_row_evidence([row(spec='架空\n材料・A')], source([item]))['status'], 'completed')
        for text in ('架空材料A', '架空材料・・A'):
            self.assertEqual(compare_row_evidence([row(spec=text)], source([item]))['status'], 'needs_review')

    def test_schema_rejects_financial_fields_types_unchecked_pages_and_retains_valid_rows(self):
        invalid = []
        for key, value in [('ページ', True), ('ページ', 3), ('仕様', None), ('品名', ''), ('原文', '')]:
            item = evidence(); item[key] = value; invalid.append(item)
        item = evidence(); item['金額'] = 777; invalid.append(item)
        for item in invalid:
            with self.subTest(item=item):
                accepted, reasons = self.parse([evidence(), item])
                self.assertEqual(accepted, [evidence()])
                self.assertTrue(reasons)
        for version in (None, True, 1, '2'):
            self.assertEqual(self.parse([evidence()], schema_version=version)[0], [])

    def test_acquisition_cache_does_not_confuse_agreement_with_success_or_legacy_status(self):
        review = source([evidence()])['amount_reading']['annotation_reviews'][0]
        self.assertTrue(annotation_cache_ready([{'annotation_review': review}]))
        self.assertEqual(compare_row_evidence([row(spec='Wrong')], source([evidence()]))['status'], 'needs_review')
        self.assertTrue(annotation_cache_ready([{'annotation_review': review}]))
        self.assertFalse(annotation_cache_ready([{'annotation_review': {'status': 'completed'}}]))
        self.assertFalse(annotation_cache_ready([{'annotation_review': {**review, 'status': 'needs_review'}}]))


if __name__ == '__main__':
    unittest.main()
