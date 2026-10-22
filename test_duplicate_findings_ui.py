"""Human choices for suspected duplication never silently remove source rows."""
import copy
import unittest
from streamlit.testing.v1 import AppTest


ROWS = [
    {'行ID':'r1','品名':'作業足場','見積元':'架空防水社','元ファイル':'waterproof.pdf','ページ':2,
     '数量':100,'原価単価':1000,'原価金額':100000,'採用':True,'除外理由':''},
    {'行ID':'r2','品名':'作業足場','見積元':'架空塗装社','元ファイル':'painting.pdf','ページ':3,
     '数量':100,'原価単価':1000,'原価金額':100000,'採用':True,'除外理由':''},
    {'行ID':'r3','品名':'別の工事','見積元':'架空防水社','元ファイル':'waterproof.pdf','ページ':2,
     '数量':1,'原価単価':20000,'原価金額':20000,'採用':True,'除外理由':''},
]


def app():
    return f'''
import copy
import streamlit as st
from review_findings_ui import render_findings
if 'original_rows' not in st.session_state:
    st.session_state['original_rows']={ROWS!r}
    for key in ('_review_result','_review_approval','_review_preview','_vault_archive','_review_proposal'):
        st.session_state[key]={{'old':'must invalidate'}}
    st.session_state['_review_export_ready']=True
rows=copy.deepcopy(st.session_state.get('_review_assistant_rows',st.session_state['original_rows']))
issues=[]
if all(r['採用'] for r in rows[:2]):
    issues=[{{'id':'shared-scaffold','種類':'共通費重複疑い','内容':'作業足場の施工範囲を確認','行':['r1','r2'],'レベル':'確認'}}]
payload={{'指摘':issues,'確認明細':rows,'計算明細':[r for r in rows if r['採用']]}}
st.session_state['answers']=render_findings(st,{{'payload':payload}},True)
'''


class DuplicateFindingsUITest(unittest.TestCase):
    def test_only_unanswered_groups_count_and_all_answers_survive_collapsing(self):
        at=AppTest.from_string('''
import streamlit as st
from review_findings_ui import render_findings
issues=[{'id':'shared-1','種類':'共通費重複疑い','内容':'施工範囲を確認','行':[],'レベル':'確認'},
        {'id':'shared-2','種類':'共通費重複疑い','内容':'施工範囲を確認','行':[],'レベル':'確認'},
        {'id':'rate','種類':'税率混在','内容':'原本の税率を確認','行':[],'レベル':'確認'}]
st.session_state['answers']=render_findings(st,{'payload':{'指摘':issues,'確認明細':[]}},True)
''').run()
        self.assertEqual(list(at.exception),[])
        self.assertIn('2件',at.warning[0].value)
        self.assertTrue(at.expander[0].proto.expanded)
        self.assertEqual(at.checkbox[0].label,'施工範囲が別なので、すべて使う')
        at.checkbox[0].check().run()
        self.assertIn('1件',at.warning[0].value)
        at.text_input[0].set_value('   ').run()
        self.assertIn('1件',at.warning[0].value)
        at.text_input[0].set_value('原本の税率を確認済み').run()
        self.assertEqual(len(at.warning),0)
        self.assertTrue(any(c.value=='確認事項はすべて確認済みです' for c in at.caption))
        self.assertEqual(at.expander[0].label,'確認した内容を見る')
        self.assertFalse(at.expander[0].proto.expanded)
        expected={'shared-1':'原本を確認し、施工範囲が別なので、すべて使う',
                  'shared-2':'原本を確認し、施工範囲が別なので、すべて使う','rate':'原本の税率を確認済み'}
        self.assertEqual(at.session_state['answers'],expected)
        # A later unrelated rerun still returns the answers from the collapsed
        # section; no hidden-widget cleanup silently drops approval evidence.
        at.run()
        self.assertTrue(at.checkbox[0].value)
        self.assertEqual(at.text_input[0].value,'原本の税率を確認済み')
        self.assertEqual(at.session_state['answers'],expected)
        self.assertEqual(len(at.warning),0)
        at.checkbox[0].uncheck().run()
        self.assertIn('1件',at.warning[0].value)
        self.assertEqual(at.expander[0].label,'確認する品目と内容')
        self.assertTrue(at.expander[0].proto.expanded)
        self.assertEqual(at.session_state['answers']['shared-1'],'')
        self.assertEqual(at.session_state['answers']['rate'],'原本の税率を確認済み')

    def test_stop_in_an_answered_group_stays_visible_and_expanded(self):
        at=AppTest.from_string('''
import streamlit as st
from estimate_review import digest
from review_findings_ui import render_findings
issues=[{'id':'explanation','種類':'明細検算','内容':'計算の確認','行':['r1'],'レベル':'確認'},
        {'id':'stop','種類':'明細検算','内容':'計算の確認','行':['r1'],'レベル':'停止'}]
key='review_resolution_group_'+digest(['明細検算','計算の確認',['explanation']])
st.session_state.setdefault(key,'説明を書いただけでは停止を解除しない')
st.session_state['answers']=render_findings(st,{'payload':{'指摘':issues,'確認明細':[]}},True)
''').run()
        self.assertEqual(list(at.exception),[])
        self.assertIn('1件',at.warning[0].value)
        self.assertTrue(at.expander[0].proto.expanded)
        self.assertEqual(at.expander[0].label,'確認する品目と内容')
        self.assertFalse(any(c.value=='確認事項はすべて確認済みです' for c in at.caption))
        self.assertEqual(at.session_state['answers'],{'explanation':'説明を書いただけでは停止を解除しない'})
        self.assertEqual(len(at.text_input),1)

    def test_no_default_choice_and_selection_alone_does_not_remove_a_row(self):
        at=AppTest.from_string(app()).run()
        self.assertEqual(list(at.exception),[])
        self.assertFalse(at.checkbox[0].value)
        self.assertEqual(at.session_state['answers'],{'shared-scaffold':''})
        self.assertTrue(at.button[0].disabled)
        self.assertEqual(set(at.multiselect[0].options),{
            'No.1 作業足場 ｜ 架空防水社 ｜ waterproof.pdf ｜ 2ページ',
            'No.2 作業足場 ｜ 架空塗装社 ｜ painting.pdf ｜ 3ページ'})
        at.multiselect[0].set_value(['r2']).run()
        self.assertFalse(at.button[0].disabled)
        self.assertNotIn('_review_assistant_rows',at.session_state)
        self.assertEqual(at.session_state['original_rows'],ROWS)
        self.assertEqual(at.session_state['answers'],{'shared-scaffold':''})

    def test_apply_exclusion_keeps_original_and_other_rows_but_invalidates_outputs(self):
        at=AppTest.from_string(app()).run()
        at.multiselect[0].set_value(['r2']).run()
        at.button[0].click().run()
        self.assertEqual(list(at.exception),[])
        updated=at.session_state['_review_assistant_rows']
        expected=copy.deepcopy(ROWS)
        expected[1].update(採用=False,除外理由='原本を見て重複のため除外')
        self.assertEqual(updated,expected)
        self.assertEqual(at.session_state['original_rows'],ROWS)
        self.assertEqual(len(updated),3)
        self.assertEqual(at.session_state['_review_editor_epoch'],1)
        for key in ('_review_result','_review_approval','_review_preview','_vault_archive','_review_proposal'):
            self.assertNotIn(key,at.session_state)
        self.assertFalse(at.session_state['_review_export_ready'])

    def test_other_warning_still_needs_written_reason_and_stop_cannot_be_checked_away(self):
        at=AppTest.from_string('''
import streamlit as st
from review_findings_ui import render_findings
issues=[{'id':'rate','種類':'税率混在','内容':'税率の異なる原本','行':[],'レベル':'確認'},
        {'id':'stopped','種類':'明細重複疑い','内容':'停止確認','行':['r1'],'レベル':'停止'}]
st.session_state['answers']=render_findings(st,{'payload':{'指摘':issues,'確認明細':[]}},True)
''').run()
        self.assertEqual(list(at.exception),[])
        self.assertEqual(len(at.checkbox),0)
        self.assertEqual(len(at.multiselect),0)
        at.text_input[0].set_value('原本ごとの税率を確認済み').run()
        self.assertEqual(at.session_state['answers'],{'rate':'原本ごとの税率を確認済み'})
