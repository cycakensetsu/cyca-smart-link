"""One clear next action for employees; no change to file intake or permissions."""
from contextlib import nullcontext
from inspect import signature
from pathlib import Path

from workflow_steps_ui import current_step, render_next_action, render_step_heading

STYLE = """<style>
.cyca-start {color:#173c52;font-size:1.18rem;line-height:1.65;margin:.25rem 0 .55rem}
.st-key-beginner_intake [data-testid="stFileUploaderDropzone"] {min-height:78px}
.st-key-beginner_intake [data-testid="stFileUploaderDropzone"] button {
background:#164f68;color:white;border-color:#164f68;padding-inline:18px}
.st-key-beginner_intake [data-testid="stFileUploaderDropzone"] button:not(:disabled):hover {
background:#24677e;color:white}
.st-key-beginner_intake [data-testid="stFileUploaderDropzone"] button,
.st-key-beginner_read_button button {min-height:48px;font-size:1.05rem;font-weight:700}
.st-key-beginner_intake button:focus-visible,.st-key-beginner_read_button button:focus-visible {
outline:3px solid #167291;outline-offset:3px}
</style>"""


def render_intake(st, *, key, max_upload_size=None, guided=True):
    active = bool(guided and st.session_state.get('_review_original_rows'))
    if guided:
        st.html(STYLE)
    panel = st.expander('読み込み中の見積書を見る・選び直す', expanded=False) if active else nullcontext()
    options = {} if max_upload_size is None else {'max_upload_size': max_upload_size}
    with panel:
        with st.container(key='beginner_intake'):
            render_step_heading(st, 1)
            if not active:
                st.markdown('**まず、業者から届いた見積書を選んでください。**')
                st.caption('PDF・写真に対応しています。複数の業者分も一度に選べます。')
            else:
                st.caption('別の見積書を選ぶと、この画面は新しい見積に切り替わります。')
            value = st.file_uploader('見積書のPDF・写真を選ぶ', type=['pdf', 'jpg', 'jpeg', 'png'],
                                     accept_multiple_files=True, key=key, **options)
    # Use a fixed bundled script, never filenames or browser input. Native
    # upload remains usable on older Streamlit versions that lack this option.
    if 'unsafe_allow_javascript' in signature(st.html).parameters:
        script = Path(__file__).with_name('estimate_uploader_ja.js').read_text(encoding='utf-8')
        st.html('<script>' + script + '</script>', unsafe_allow_javascript=True)
    return value


def render_resume_action(st):
    """Use the current step after the review widgets have run."""
    if not st.session_state.get('_review_original_rows'):
        return
    step = max(2, current_step(st.session_state))
    label = {
        2: '読み取った内容を確認する',
        3: '上乗せする金額を決める',
        4: '見積書の仕上がりを見る',
        5: 'PDFを作成・保存する',
    }[step]
    render_next_action(st, step, label)


def render_read_button(st, label, *, guided, active):
    if not guided:
        return st.button(label)
    with st.container(key='beginner_read_button'):
        if active:
            with st.expander('同じ見積書をもう一度読み取る'):
                return st.button(label, key='beginner_estimate_read', width='stretch')
        return st.button(label, key='beginner_estimate_read', type='primary', width='stretch')
