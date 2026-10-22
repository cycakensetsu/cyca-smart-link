"""The visible stage follows verified work, not stale generated files."""
import re
import tempfile
import unittest
from streamlit.testing.v1 import AppTest
from test_conversation_ui import app_source
from workflow_steps_ui import current_step, progress_markup


class WorkflowStepsUITest(unittest.TestCase):
    def test_status_identifies_one_current_stage_and_new_upload_starts_at_one(self):
        self.assertEqual(current_step({'_review_workflow_step':5}),1)
        self.assertEqual(current_step({'_review_original_rows':[{}]}),2)
        self.assertEqual(current_step({'_review_original_rows':[{}],'_review_workflow_step':999}),2)
        html=progress_markup(3)
        self.assertEqual(html.count('aria-current="step"'),1)
        self.assertIn('いま：ステップ3 / 5',html)
        self.assertEqual(re.findall(r'<li class="cyca-step-([a-z]+)"',html),
                         ['done','done','current','upcoming','upcoming'])

    def test_stage_two_to_five_then_edit_returns_to_the_required_step(self):
        with tempfile.TemporaryDirectory() as temp:
            at=AppTest.from_string(app_source(temp)).run()
            self.assertEqual(current_step(at.session_state),2)
            source_id=at.session_state['_review_sources'][0]['id']
            self.assertTrue(any(f'href="#cyca-source-{source_id}"' in h.value for h in at.html))
            self.assertTrue(any('名前を押すと、その資料に戻れます' in i.value for i in at.info))
            at.number_input(key='review_net_'+source_id).set_value(100000).run()
            at.checkbox(key='review_source_ok_'+source_id).check().run()
            at.text_input(key='review_customer').set_value('架空のお客様 御中')
            at.text_input(key='review_project').set_value('工程確認・提出不可')
            at.run()
            self.assertEqual(list(at.exception),[])
            self.assertEqual(current_step(at.session_state),3)
            headings=[m.value for m in at.subheader if m.value.startswith('ステップ')]
            self.assertEqual([int(re.match(r'ステップ(\d)',h).group(1)) for h in headings],[2,3,4,5])
            self.assertTrue(any('href="#cyca-step-3"' in h.value and '次へ：金額を決める' in h.value for h in at.html))
            self.assertEqual([h.proto.anchor for h in at.subheader if h.value.startswith('ステップ')],
                             ['cyca-step-2','cyca-step-3','cyca-step-4','cyca-step-5'])
            self.assertEqual(at.button(key='review_preview').proto.type,'primary')
            at.button(key='review_preview').click().run()
            self.assertEqual(current_step(at.session_state),4)
            self.assertTrue(any('この内容で見積書を作成' in c.value for c in at.caption))
            self.assertNotIn('_review_approval',at.session_state)
            next(c for c in at.checkbox if c.key.startswith('review_agree_')).check().run()
            self.assertEqual(current_step(at.session_state),5)
            self.assertNotIn('_review_approval',at.session_state)
            at.button(key='review_approve').click().run()
            self.assertEqual(list(at.exception),[])
            self.assertTrue(at.session_state['_review_export_ready'])
            self.assertEqual(current_step(at.session_state),5)
            target=next(n for n in at.number_input if n.key.startswith('review_target_'))
            target.set_value(10000).run()
            self.assertEqual(current_step(at.session_state),3)
            self.assertFalse(at.session_state['_review_export_ready'])
            at.number_input(key='review_net_'+source_id).set_value(99999).run()
            self.assertEqual(current_step(at.session_state),2)
            self.assertTrue(at.checkbox(key='review_source_ok_'+source_id).disabled)
