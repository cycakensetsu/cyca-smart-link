import pickle
import subprocess
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch
import fitz
from pdf_worker import page_count,render_original,render_pages,_MARKER
from gemini_resilience import _render_pdf_pages_as_images
from source_amounts import read_amounts


class PdfWorkerTest(unittest.TestCase):
    def test_fresh_import_after_watcher_eviction_keeps_one_pdf_slot(self):
        import importlib.util
        import pdf_worker
        spec=importlib.util.spec_from_file_location('_fresh_cyca_pdf_worker_test',pdf_worker.__file__)
        fresh=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fresh)
        self.assertIs(fresh._PDF_SLOT,pdf_worker._PDF_SLOT)

    def data(self):
        document=fitz.open();page=document.new_page();page.insert_text((30,60),'税抜合計 100,000円',fontname='japan',fontsize=12)
        result=document.tobytes();document.close();return result

    def test_concurrent_ui_requests_use_independent_pdf_processes(self):
        data=self.data()
        def check(_):
            return page_count(data),read_amounts('synthetic.pdf',data)['net_total'],render_original(data,'pdf',1)[:8]
        with ThreadPoolExecutor(max_workers=4) as executor:results=list(executor.map(check,range(8)))
        self.assertEqual(results,[(1,100000,b'\x89PNG\r\n\x1a\n')]*8)
        self.assertEqual(len(render_pages(data)),1)

    def test_worker_crash_does_not_expose_its_output_or_crash_the_app(self):
        with patch('pdf_worker.subprocess.run',return_value=SimpleNamespace(returncode=-11,stdout=b'',stderr=b'private-data-not-for-ui')):
            with self.assertRaisesRegex(ValueError,'他の原本は保持') as result:page_count(b'synthetic')
        self.assertNotIn('private-data',str(result.exception))

    def test_worker_does_not_receive_api_keys_or_passwords(self):
        result=SimpleNamespace(returncode=0,stdout=_MARKER+pickle.dumps((True,1)),stderr=b'')
        with patch.dict('os.environ',GOOGLE_API_KEY='synthetic-secret',STREAMLIT_APP_PASSWORD='synthetic-password'),patch('pdf_worker.subprocess.run',return_value=result) as run:
            self.assertEqual(page_count(b'pdf'),1)
        environment=run.call_args.kwargs['env']
        self.assertNotIn('GOOGLE_API_KEY',environment)
        self.assertNotIn('STREAMLIT_APP_PASSWORD',environment)

    def test_image_parts_cross_process_boundary_without_document_objects(self):
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'synthetic.pdf';path.write_bytes(self.data())
            pages=_render_pdf_pages_as_images(str(path),'synthetic.pdf')
        self.assertEqual(len(pages),1)
        self.assertEqual(pages[0].parts[0].inline_data.mime_type,'image/jpeg')
        self.assertTrue(pages[0].parts[0].inline_data.data)
