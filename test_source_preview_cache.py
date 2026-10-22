"""Ten-file review reruns reuse original images without unbounded memory."""
from collections import OrderedDict
from types import SimpleNamespace
import unittest
import tempfile
from unittest.mock import patch
from source_review_ui import _preview_data


class Store:
    def __init__(self): self.reads=[]
    def read(self, source_id):
        self.reads.append(source_id)
        return ('original-'+source_id).encode()


def source(number): return {'id':str(number),'name':f'source-{number}.pdf'}


class SourcePreviewCacheTest(unittest.TestCase):
    def test_ten_sources_second_form_rerun_does_not_render_or_reread_them(self):
        st=SimpleNamespace(session_state={});store=Store()
        with patch('pdf_worker.render_original',side_effect=lambda data,suffix,page: b'PNG '+data) as render:
            first=[_preview_data(st,source(n),store,1) for n in range(10)]
            second=[_preview_data(st,source(n),store,1) for n in range(10)]
        self.assertEqual(first,second)
        self.assertEqual(render.call_count,10)
        self.assertEqual(store.reads,[str(n) for n in range(10)])
        self.assertEqual(len(st.session_state['_review_source_previews']),10)

    def test_byte_budget_evicts_least_recently_used_not_the_recently_viewed_source(self):
        st=SimpleNamespace(session_state={});store=Store()
        with patch('source_review_ui._PREVIEW_CACHE_MAX_BYTES',10), patch('pdf_worker.render_original',return_value=b'PNG!') as render:
            for n in (1,2,1,3): _preview_data(st,source(n),store,1)
            cache=st.session_state['_review_source_previews']
            self.assertEqual([key[0] for key in cache],['1','3'])
            self.assertLessEqual(sum(map(len,cache.values())),10)
            _preview_data(st,source(1),store,1)
            self.assertEqual(render.call_count,3)
            _preview_data(st,source(2),store,1)
            self.assertEqual(render.call_count,4)

    def test_page_budget_is_bounded_and_an_existing_plain_dict_is_upgraded(self):
        st=SimpleNamespace(session_state={'_review_source_previews':{
            ('1',1,'large-v2'):b'one',('2',1,'large-v2'):b'two'}});store=Store()
        with patch('source_review_ui._PREVIEW_CACHE_MAX_PAGES',2),patch('pdf_worker.render_original',return_value=b'three') as render:
            self.assertEqual(_preview_data(st,source(1),store,1),b'one')
            _preview_data(st,source(3),store,1)
        cache=st.session_state['_review_source_previews']
        self.assertIsInstance(cache,OrderedDict)
        self.assertEqual([key[0] for key in cache],['1','3'])
        self.assertEqual(render.call_count,1)

    def test_oversized_image_is_shown_without_displacing_other_originals(self):
        st=SimpleNamespace(session_state={});store=Store()
        def render(data,suffix,page): return b'x'*11 if data.endswith(b'2') else b'png'
        with patch('source_review_ui._PREVIEW_CACHE_MAX_BYTES',10),patch('pdf_worker.render_original',side_effect=render) as mocked:
            self.assertEqual(_preview_data(st,source(1),store,1),b'png')
            for _ in range(2): self.assertEqual(_preview_data(st,source(2),store,1),b'x'*11)
            self.assertEqual(_preview_data(st,source(1),store,1),b'png')
        self.assertEqual(mocked.call_count,3)
        cache=st.session_state['_review_source_previews']
        self.assertEqual([key[0] for key in cache],['1'])

    def test_failed_original_does_not_clear_successful_previews(self):
        st=SimpleNamespace(session_state={});store=Store()
        def render(data,suffix,page):
            if data.endswith(b'2'):raise ValueError('unreadable original')
            return b'png'
        with patch('pdf_worker.render_original',side_effect=render) as mocked:
            _preview_data(st,source(1),store,1)
            self.assertIsNone(_preview_data(st,source(2),store,1))
            self.assertEqual(_preview_data(st,source(1),store,1),b'png')
        self.assertEqual(mocked.call_count,2)
        self.assertEqual(len(st.session_state['_review_source_previews']),1)

    def test_ten_individual_checks_survive_reruns_without_preview_rerender(self):
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as temp:
            app=f'''
import copy
from io import BytesIO
from unittest.mock import patch
from PIL import Image
import streamlit as st
from estimate_review import OriginalVault
from review_input_ui import render_inputs
store=OriginalVault({temp!r},'ten-source-cache-test')
if '_review_sources' not in st.session_state:
    sources=[]; rows=[]; readings={{}}
    for n in range(10):
        source=store.preserve(f'case-{{n}}.pdf',f'synthetic-original-{{n}}'.encode())
        source['pages']=1;sources.append(source)
        rows.append(dict(行ID=f'r{{n}}',原本ID=source['id'],元ファイル=source['name'],ページ=1,
                         品名='確認用工事',仕様='原本仕様',部位='',数量=1,単位='式',
                         原価単価=1000,原価金額=1000,見積元=f'架空社{{n}}',工事種別='防水工事',
                         採用=True,集計のみ=False,訂正理由='',除外理由='',顧客備考=''))
        readings[source['id']]={{'net_total':1000,'tax':100,'gross_total':1100,'conflicts':[],'page':1}}
    st.session_state['_review_sources']=sources
    st.session_state['original_rows']=rows
    st.session_state['_review_amount_readings']=readings
buffer=BytesIO();Image.new('RGB',(16,16),'white').save(buffer,format='PNG')
with patch('pdf_worker.render_original',return_value=buffer.getvalue()) as renderer:
    rows,sources=render_inputs(st,st.session_state['original_rows'],copy.deepcopy(st.session_state['_review_sources']),store)
    st.session_state['render_count']=renderer.call_count
st.session_state['checked_sources']=sources
'''
            at=AppTest.from_string(app,default_timeout=20).run()
            self.assertEqual(list(at.exception),[])
            self.assertEqual(at.session_state['render_count'],10)
            source_ids=[source['id'] for source in at.session_state['checked_sources']]
            for number,sid in enumerate(source_ids,1):
                at.checkbox(key='review_source_ok_'+sid).check().run()
                self.assertEqual(list(at.exception),[])
                self.assertEqual(at.session_state['render_count'],0)
                self.assertEqual(sum(bool(s.get('confirmed')) for s in at.session_state['checked_sources']),number)
            # Deliberately changing one original amount resets only that source's
            # confirmation, preserving the other nine independent human checks.
            at.number_input(key='review_net_'+source_ids[0]).set_value(999).run()
            self.assertEqual(list(at.exception),[])
            self.assertEqual(sum(bool(s.get('confirmed')) for s in at.session_state['checked_sources']),9)
            self.assertTrue(at.checkbox(key='review_source_ok_'+source_ids[0]).disabled)
            self.assertEqual(at.session_state['render_count'],0)
