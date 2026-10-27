"""Keep the native PDF engine out of Streamlit's concurrent script threads.

Only trusted, internally generated Python arguments use this local pipe.
No document objects, credentials, network requests or filenames are exposed.
"""
from functools import wraps
import os
from pathlib import Path
import pickle
import subprocess
import sys
import threading

_PDF_SLOT=globals().get('_PDF_SLOT') or threading.BoundedSemaphore(1)
_FLAG='CYCA_INTERNAL_PDF_WORKER'
_MARKER=b'\nCYCA_PDF_RESULT:'
_ALLOWED={'cyca_reference':{'read_reference_quote'},'source_table':{'read_native_tables','attach_native_dimensions'},
          'source_amounts':{'read_amounts'},'gemini_resilience':{'prepare_upload_for_gemini_pages','_render_pdf_pages_as_images'},
          'pdf_worker':{'page_count','render_original','render_pages'}}


def isolated_pdf(function):
    @wraps(function)
    def run(*args,**kwargs):
        if os.environ.get(_FLAG)=='1':
            return function(*args,**kwargs)
        environment=dict(os.environ);environment[_FLAG]='1'
        environment['PYTHONPATH']=str(Path(__file__).parent)+os.pathsep+environment.get('PYTHONPATH','')
        # The PDF worker has no use for API keys or passwords.
        for key in list(environment):
            if any(word in key.upper() for word in ('API_KEY','PASSWORD','ACCESS_TOKEN')):environment.pop(key)
        try:
            with _PDF_SLOT:
                result=subprocess.run([sys.executable,str(Path(__file__).resolve()),function.__module__,function.__name__],
                    input=pickle.dumps((args,kwargs)),stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                    env=environment,timeout=120,check=False)
        except subprocess.TimeoutExpired:
            raise ValueError('原本の読み取りに時間がかかりました。ページを分けて読み込み直してください。') from None
        if result.returncode or _MARKER not in result.stdout:
            raise ValueError('この原本の読み取りが止まりました。他の原本は保持しています。画像として読み込み直してください。')
        try:
            ok,value=pickle.loads(result.stdout.rsplit(_MARKER,1)[1])
        except (ValueError,EOFError,pickle.UnpicklingError):
            raise ValueError('原本の読み取り結果を確認できませんでした。') from None
        if not ok:
            raise ValueError(value)
        return value
    return run


@isolated_pdf
def page_count(data):
    import fitz
    with fitz.open(stream=data,filetype='pdf') as document:return len(document)


@isolated_pdf
def render_original(data,suffix,page):
    import fitz
    with fitz.open(stream=data,filetype=suffix) as document:
        original=document[page-1]
        scale=min(1600/max(original.rect.width,1),2200/max(original.rect.height,1),3)
        return original.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False).tobytes('png')


@isolated_pdf
def render_pages(data,stamp='配置確認用・未確定'):
    import fitz
    images=[]
    with fitz.open(stream=data,filetype='pdf') as document:
        for page in document:
            if stamp:page.insert_text((page.rect.width/2-65,18),stamp,fontname='japan',fontsize=9)
            images.append(page.get_pixmap(matrix=fitz.Matrix(1.3,1.3),alpha=False).tobytes('png'))
    return images


def _main():
    import contextlib,importlib
    module,name=sys.argv[1:3]
    if name not in _ALLOWED.get(module,set()):raise ValueError('Unsupported PDF operation')
    args,kwargs=pickle.loads(sys.stdin.buffer.read())
    try:
        with contextlib.redirect_stdout(sys.stderr):
            value=getattr(importlib.import_module(module),name)(*args,**kwargs)
        response=(True,value)
    except Exception as exc:
        response=(False,str(exc))
    sys.stdout.buffer.write(_MARKER+pickle.dumps(response));sys.stdout.buffer.flush()


if __name__=='__main__':_main()
