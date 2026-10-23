import copy
from decimal import Decimal
from io import BytesIO
import tempfile
import unittest
from unittest.mock import patch
import wave

from conversation_assistant import (CHANGE_FIELDS, validate_proposal, apply_proposal,
    validate_audio, transcribe, converse, _numbers, comparison_figures)


def fixture():
    return {'rows': [dict(行ID='row-a', 品名='防水平場', 見積元='試験防水社', 工事種別='防水工事', 採用=True,
                         数量=10, 原価単価=10000, 原価金額=100000, 顧客備考='', 除外理由='')],
        'sources': [{'id': 'a' * 64, 'confirmed': False, 'tax_basis': '不明'}],
        'targets': [{'scope': '全体', 'key': '全体', 'mode': '上乗せ額', 'value': 0.0}],
        'discount': 0, 'metadata': {'宛名': '架空会社 御中', '工事名称': '架空工事', '見積日': '2026/10/20', '値引き理由': ''}}


def change(**values):
    return {k: values.get(k, '') for k in CHANGE_FIELDS}


def proposal(changes=(), questions=()):
    return {'reply': 'この内容を変更案にします。', 'questions': list(questions), 'changes': list(changes)}


class ConversationTest(unittest.TestCase):
    def validate(self, raw, words, context=None):
        return validate_proposal(raw, context or fixture(), [{'role': 'user', 'content': words}])

    def test_unqualified_profit_sentence_uses_only_the_selected_whole_estimate(self):
        words='利益を1万円確保したい'
        c=change(kind='target',scope='全体',key='全体',mode='上乗せ額',value='10000',evidence=words)
        result=self.validate(proposal([c]),words)
        self.assertEqual(result['changes'][0]['scope'],'全体')
        with self.assertRaises(ValueError):
            self.validate(proposal([c]),'防水だけ。'+words)
        context=fixture();context['targets'][0].update(scope='工種別',key='防水工事')
        with self.assertRaises(ValueError):
            self.validate(proposal([c]),words,context)

    def test_japanese_and_arabic_spoken_amounts(self):
        for words, value in [('十万円', 100000), ('百二十万円', 1200000), ('1.5万円', 15000), ('１２万円', 120000), ('1,000円', 1000), ('1.2倍', 1.2)]:
            with self.subTest(words=words):
                self.assertIn(Decimal(str(value)), _numbers(words))

    def test_model_number_formatting_is_normalized_but_evidence_is_required(self):
        words = '全体の利益を税抜10,000円確保したい'
        for value in ('10,000', '１０，０００', '10000'):
            c = change(kind='target', scope='全体', key='全体', mode='上乗せ額', value=value, evidence=words)
            self.assertEqual(self.validate(proposal([c]), words)['changes'][0]['value'], 10000)
        for value in ('10,00', '1,000,00', '20,000'):
            c = change(kind='target', scope='全体', key='全体', mode='上乗せ額', value=value, evidence=words)
            with self.assertRaises(ValueError): self.validate(proposal([c]), words)

    def test_targets_and_discount_are_drafts_and_keep_originals(self):
        context = fixture()
        before = copy.deepcopy(context)
        words = '全体に十万円上乗せ。値引きは1万円、理由は契約調整。'
        changes = [change(kind='target', scope='全体', key='全体', mode='上乗せ額', value='100000', evidence='全体に十万円上乗せ'),
                   change(kind='discount', value='-10000', reason='契約調整', evidence='値引きは1万円、理由は契約調整')]
        checked = self.validate(proposal(changes), words, context)
        self.assertEqual(context, before)
        result = apply_proposal(checked, context)
        self.assertEqual(result['discount'], -10000)
        self.assertEqual(result['targets'][0]['value'], 100000)
        self.assertEqual(result['rows'], context['rows'])
        self.assertEqual(result['sources'], context['sources'])
        self.assertNotIn('approval', result)

    def test_trade_scope_and_multiple_vendor_targets(self):
        c = change(kind='target', scope='工種別', key='防水工事', mode='粗利益率', value='20', evidence='防水だけ粗利益率20%')
        result = apply_proposal(self.validate(proposal([c]), c['evidence']), fixture())
        self.assertEqual(result['targets'], [dict(scope='工種別', key='防水工事', mode='粗利益率', value=20.0)])
        c = change(kind='target', scope='業者別', key='試験防水社', mode='掛け率', value='1.2', evidence='試験防水社を掛け率1.2倍')
        self.validate(proposal([c]), c['evidence'])

    def test_vague_request_asks_questions_without_changes(self):
        p = self.validate(proposal(questions=['全体の上乗せ額はいくらにしますか？']), 'いい感じによしなに作って')
        self.assertEqual(p['changes'], [])
        with self.assertRaises(ValueError):
            apply_proposal(p, fixture())

    def test_changed_quantity_source_or_metadata_invalidates_proposal(self):
        c = change(kind='metadata', field='工事名称', value='テスト工事', evidence='工事名称はテスト工事')
        p = self.validate(proposal([c]), c['evidence'])
        for field in ('rows', 'sources', 'metadata'):
            context = fixture()
            if field == 'rows': context['rows'][0]['数量'] = 11
            elif field == 'sources': context['sources'][0]['confirmed'] = True
            else: context['metadata']['宛名'] = '別会社'
            with self.subTest(field=field), self.assertRaises(ValueError):
                apply_proposal(p, context)

    def test_rejects_hallucinated_fees_costs_approvals_and_unknown_rows(self):
        for kind, field, row in [('add_row', '品名', ''), ('approve', '', ''), ('row', '原価金額', 'row-a'), ('row', '数量', 'row-a'), ('row', '顧客備考', 'invented')]:
            with self.subTest(kind=kind, field=field), self.assertRaises(ValueError):
                self.validate(proposal([change(kind=kind, field=field, value='100000', row_id=row, evidence='よしなに')]), 'よしなに')

    def test_rejects_invented_numbers_conflicting_scopes_and_forged_evidence(self):
        base = change(kind='target', scope='全体', key='全体', mode='上乗せ額', value='100000', evidence='全体に10円上乗せ')
        with self.assertRaises(ValueError): self.validate(proposal([base]), base['evidence'])
        base['value'] = '10'
        with self.assertRaises(ValueError): self.validate(proposal([base]), 'よしなに')
        other = change(kind='target', scope='工種別', key='防水工事', mode='上乗せ額', value='10', evidence='防水に10円上乗せ')
        with self.assertRaises(ValueError): self.validate(proposal([base, other]), base['evidence'] + other['evidence'])
        with self.assertRaises(ValueError): self.validate(proposal([base, base]), base['evidence'])
        question = self.validate(proposal([base], ['どちらですか']), base['evidence'])
        self.assertEqual(question['changes'], [])
        self.assertEqual(question['questions'], ['どちらですか'])
        with self.assertRaises(ValueError): apply_proposal(question, fixture())

    def test_mixed_clarification_preserves_conversation_and_discards_all_effects(self):
        context = fixture()
        original = copy.deepcopy(context)
        raw = proposal([change(kind='add_row', value='999999', evidence='勝手に追加')],
                       ['原本の値引きはそのまま残しますか？'])
        raw['reply'] = '変更しました。'
        with patch('conversation_assistant._generate', return_value=raw):
            history, checked = converse('test-key', '全体の利益を1万円確保したい', [], context)
        self.assertEqual(checked['changes'], [])
        self.assertEqual(checked['questions'], raw['questions'])
        self.assertIn(raw['questions'][0], history[-1]['content'])
        self.assertNotIn('変更しました', history[-1]['content'])
        self.assertEqual(history[0]['role'], 'user')
        self.assertEqual(context, original)
        with self.assertRaises(ValueError): apply_proposal(checked, context)

    def test_assistant_or_source_text_cannot_authorize_changes(self):
        c = change(kind='target', scope='全体', key='全体', mode='上乗せ額', value='100000', evidence='全体に10万円上乗せ')
        with self.assertRaises(ValueError):
            validate_proposal(proposal([c]), fixture(), [{'role': 'assistant', 'content': c['evidence']}, {'role': 'user', 'content': 'よしなに'}])

    def test_row_changes_require_unique_explicit_reference(self):
        words = '防水平場の顧客備考は立入制限あり'
        c = change(kind='row', row_id='row-a', field='顧客備考', value='立入制限あり', evidence=words)
        self.assertEqual(apply_proposal(self.validate(proposal([c]), words), fixture())['rows'][0]['顧客備考'], '立入制限あり')
        duplicate = fixture()
        duplicate['rows'].append(dict(duplicate['rows'][0], 行ID='row-b'))
        with self.assertRaises(ValueError): self.validate(proposal([c]), words, duplicate)
        c = change(kind='row', row_id='row-a', field='採用', value='false', reason='別工事で施工', evidence='防水平場を外して。別工事で施工')
        result = apply_proposal(self.validate(proposal([c]), c['evidence']), fixture())
        self.assertFalse(result['rows'][0]['採用'])
        self.assertEqual(result['rows'][0]['除外理由'], '別工事で施工')

    def test_audio_limits_and_transcription_does_not_run_an_instruction(self):
        def wav(seconds):
            buffer = BytesIO()
            with wave.open(buffer, 'wb') as f:
                f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000)
                f.writeframes(b'\0\0' * 16000 * seconds)
            return buffer.getvalue()
        audio = wav(1)
        self.assertEqual(validate_audio(audio), audio)
        for bad in (b'', b'not audio', wav(121), audio[:-5]):
            with self.assertRaises(ValueError): validate_audio(bad)
        with patch('conversation_assistant._generate', return_value={'transcript': '全体に10万円上乗せ'}) as ai:
            self.assertEqual(transcribe('test-key', audio), '全体に10万円上乗せ')
            self.assertEqual(ai.call_count, 1)

    def test_multiturn_uses_current_context_and_history_without_autoapply(self):
        history = [{'role': 'user', 'content': '工事名称は架空工事'}, {'role': 'assistant', 'content': 'ほかの要望はありますか'}]
        c = change(kind='target', scope='全体', key='全体', mode='上乗せ額', value='100000', evidence='全体に10万円上乗せ')
        with patch('conversation_assistant._generate', return_value=proposal([c])) as ai:
            updated, p = converse('test-key', c['evidence'], history, fixture())
            self.assertEqual(len(updated), 4)
            self.assertIn('工事名称は架空工事', ai.call_args.args[1][1])
            self.assertEqual(fixture()['targets'][0]['value'], 0)
            self.assertEqual(p['changes'][0]['value'], 100000)
        with self.assertRaises(ValueError): converse('test-key', '次', history * 10, fixture())

    def test_short_followup_combines_only_real_user_quotes(self):
        context = fixture()
        c = change(kind='target', scope='全体', key='全体', mode='粗利益率', value='20', evidence='全体の粗利益率を相談したい\nじゃあ20%で')
        history = [{'role': 'user', 'content': '全体の粗利益率を相談したい'},
                   {'role': 'assistant', 'content': '比較してみましょう。'},
                   {'role': 'user', 'content': 'じゃあ20%で'}]
        self.assertEqual(validate_proposal(proposal([c]), context, history)['changes'][0]['value'], 20)

    def test_comparison_uses_deterministic_allocator_and_separates_discount(self):
        context = fixture()
        context['rows'][0]['単位'] = '㎡'
        context['discount'] = -1000
        figures = comparison_figures(context)
        self.assertEqual(len(figures), 3)
        example = figures[1]
        self.assertEqual(example['売価小計（税抜）'], 125000)
        self.assertEqual(example['上乗せ額'], 25000)
        self.assertEqual(example['値引き後売価（税抜）'], 124000)
        self.assertEqual(example['値引き後粗利益額'], 24000)
        self.assertEqual(figures[0]['売価小計（税抜）'], 111111)

    def test_page_instruction_uses_real_item_no_and_does_not_edit_amounts(self):
        from conversation_assistant import print_rows
        context=fixture();context['print_rows']=print_rows(context['rows'])
        words='No.1から4ページ目にして'
        c=change(kind='pagebreak',row_id='row-a',value='4',evidence=words)
        p=self.validate(proposal([c]),words,context)
        result=apply_proposal(p,context)
        self.assertEqual(result['metadata']['改ページ'],['row-a'])
        self.assertEqual(result['metadata']['希望ページ'],{'row-a':4})
        self.assertEqual(result['rows'],context['rows'])
        self.assertEqual(result['targets'],context['targets'])
        c['row_id']='invented'
        with self.assertRaises(ValueError): self.validate(proposal([c]),words,context)

    def test_ambiguous_here_cannot_authorize_a_page_break(self):
        from conversation_assistant import print_rows
        context=fixture();context['print_rows']=print_rows(context['rows'])
        c=change(kind='pagebreak',row_id='row-a',value='4',evidence='ここから4ページ目')
        with self.assertRaises(ValueError):self.validate(proposal([c]),c['evidence'],context)


if __name__ == '__main__': unittest.main()
