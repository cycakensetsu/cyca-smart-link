import copy
import unittest
from unittest.mock import patch

from row_evidence_gate import requirement, verified
import test_estimate_review as estimate_tests


class RowEvidenceGateTest(unittest.TestCase):
    def setUp(self):
        case = estimate_tests.EstimateReviewTest()
        case.setUp()
        self.case = case
        self.source = case.source
        self.source['amount_reading'] = {'annotation_reviews': [{'status': 'completed'}]}
        self.rows = case.rows
        self.result = {'schema_version': 2, 'status': 'needs_review', 'findings': [
            {'row_id': self.rows[0]['行ID'], 'page': 2, 'name': '架空の機器',
             'fields': ['備考'], 'reason': '別の行に付いた注記', 'actual': {'備考': '別の注記'},
             'expected': {'備考': '据付費含む'}, 'evidence_digest': 'evidence-a'}]}
        self.mock = patch('row_evidence_review.compare_row_evidence', return_value=self.result)
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def test_matching_money_and_general_confirmation_cannot_bypass_missing_row_review(self):
        draft = self.case.draft()
        self.assertTrue(any(i['種類'] == '仕様注記の原本照合' for i in draft['unresolved']))
        self.assertFalse(verified(self.rows, self.source))

    def test_specific_comparison_reason_is_saved_and_any_later_change_invalidates_it(self):
        needed = requirement(self.rows, self.source)
        self.source['row_evidence_confirmation'] = {'signature': needed['signature'],
            'reason': '架空原本2ページの枠線と材料名を確認。今回の表記を採用。'}
        self.assertTrue(verified(self.rows, self.source))
        self.assertFalse(any(i['種類'] == '仕様注記の原本照合' for i in self.case.draft()['unresolved']))
        for field, value in [('仕様', 'changed'), ('備考', 'changed'), ('品名', 'changed'),
                             ('ページ', 1), ('原価金額', 100001), ('訂正理由', 'changed')]:
            with self.subTest(field=field):
                rows = copy.deepcopy(self.rows)
                rows[0][field] = value
                self.assertFalse(verified(rows, self.source))
        self.result['findings'][0]['evidence_digest'] = 'new-evidence'
        self.assertFalse(verified(self.rows, self.source))

    def test_old_annotation_status_completed_is_not_row_verification(self):
        self.assertIsNotNone(requirement(self.rows, self.source))
        self.assertFalse(verified(self.rows, self.source))

    def test_empty_or_legacy_automated_reading_never_implies_native_verification(self):
        for reading in ({}, {'annotation_reviews': []}, {'net_total': 100000}):
            with self.subTest(reading=reading):
                self.source['amount_reading'] = reading
                self.assertIsNotNone(requirement(self.rows, self.source))
                self.assertFalse(verified(self.rows, self.source))

    def test_comparison_reason_survives_only_the_allowlisted_text_checkpoint(self):
        from work_drafts import allowed_key, validate_draft
        key = 'review_row_evidence_' + requirement(self.rows, self.source)['signature']
        self.assertTrue(allowed_key(key))
        self.assertFalse(allowed_key(key + '_credential'))
        value = {'schema': 1, 'kind': 'working_draft', 'owner': 'a'*64,
                 'work_id': 'b'*32, 'extraction_id': 'c'*64,
                 'saved_at': '2026-11-03T00:00:00+00:00',
                 'state': {'_review_assistant_rows': self.rows, key: '原本確認理由'}}
        validate_draft(value)
        value['state'][key] = True
        with self.assertRaises(ValueError):
            validate_draft(value)


if __name__ == '__main__':
    unittest.main()
