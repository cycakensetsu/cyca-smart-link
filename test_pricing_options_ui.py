import tempfile
import unittest
from streamlit.testing.v1 import AppTest
from estimate_review import digest


class PricingOptionsUITest(unittest.TestCase):
    def test_choose_reject_recalculate_preview_and_export(self):
        with tempfile.TemporaryDirectory() as folder:
            app=f'''
import os
os.environ['CYCA_DATA_DIR']={folder!r}
import streamlit as st
from estimate_review import review_rows
from test_markup_rules import frame
from review_ui import _render_review_plan
if 'original' not in st.session_state:
    f,_=frame([('構造材',427,'kg',345),('小材料',12,'個',3000),('取付工事',1,'式',150000),('諸経費',1,'式',65000)])
    f['元ファイル']='synthetic.pdf';f['ページ']=1;f['工事種別']='防水工事'
    source={{'id':'a'*64,'name':'synthetic.pdf','pages':1,'confirmed':True,'tax_basis':'税抜','tax_rate':.1,'net_total':398315}}
    st.session_state['original']=review_rows(f,[source]);st.session_state['sources']=[source]
    st.session_state['username']='synthetic-user'
    st.session_state['_review_metadata']={{'宛名':'検証用・提出不可','工事名称':'合成データ検証'}}
_render_review_plan(st,'',st.session_state['original'],st.session_state['original'],st.session_state['sources'])
'''
            at=AppTest.from_string(app,default_timeout=20).run()
            target='review_target_'+digest(['全体','上乗せ額','全体'])
            at.number_input(key=target).set_value(200000).run()
            self.assertEqual(list(at.exception),[])
            self.assertIn('review_adopt_discount',[b.key for b in at.button])
            at.button(key='review_adopt_discount').click().run()
            self.assertEqual(at.session_state['review_pricing_choice']['mode'],'discount_adjustment')
            self.assertEqual(list(at.exception),[])
            self.assertIn('200,000',next(m.value for m in at.metric if '最終利益' in m.label))
            at.button(key='review_preview').click().run()
            next(c for c in at.checkbox if c.key.startswith('review_agree_')).check().run()
            at.button(key='review_approve').click().run()
            self.assertEqual(list(at.exception),[])
            payload=at.session_state['_review_approval']['payload']
            self.assertEqual(payload['合計']['粗利益'],200000)
            self.assertLess(payload['値引き'],0)
            self.assertTrue(at.session_state['_review_result'].ok)
            at.button(key='review_reject_discount').click().run()
            self.assertEqual(at.session_state['review_pricing_choice']['mode'],'none')
            self.assertFalse(any('見積書ができました' in s.value for s in at.success))
            at.button(key='review_adopt_discount').click().run()
            at.number_input(key=target).set_value(190000).run()
            self.assertNotIn('review_pricing_choice',at.session_state)
            at.number_input(key=target).set_value(200000).run()
            self.assertNotIn('review_pricing_choice',at.session_state)
            self.assertEqual(list(at.exception),[])
            at.number_input(key=target).set_value(0).run()
            self.assertNotIn('review_adopt_discount',[b.key for b in at.button])
            self.assertEqual(list(at.exception),[])


if __name__=='__main__':unittest.main()
