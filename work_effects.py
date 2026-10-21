"""Small, local-only celebrations. Never include quote data in browser events."""
from pathlib import Path
import time
import uuid

STAGES = {
    'upload': ('📄', '原本を受け取りました'),
    'read': ('🔎', '読み取り完了。原本と確認しましょう'),
    'check': ('✅', '原本の確認ができました'),
    'preview': ('✨', '完成イメージができました'),
    'output': ('🎉', '見積書が完成しました！'),
    'finish': ('🎁', '出力ファイルを用意しました。おつかれさまです'),
}
_component = None


def cue(st, stage, token=None):
    if stage not in STAGES:
        raise ValueError('Unknown work stage')
    seen = st.session_state.setdefault('_effects_seen', {})
    if token is not None and seen.get(stage) == token:
        return
    seen[stage] = token
    events = st.session_state.setdefault('_effects_events', [])
    events.append({'id': uuid.uuid4().hex, 'stage': stage, 'at': time.time()})
    st.session_state['_effects_events'] = events[-12:]


def confirmed_sources(st, sources):
    signature = tuple((s['id'], bool(s.get('confirmed'))) for s in sources)
    previous = st.session_state.get('_effects_source_checks')
    st.session_state['_effects_source_checks'] = signature
    if previous is not None and signature != previous and sources and all(s.get('confirmed') for s in sources):
        cue(st, 'check')


def download_cue():
    import streamlit as st
    cue(st, 'finish')


def render(st):
    global _component
    if _component is None:
        assets = Path(__file__).parent / 'work_effects_frontend'
        if hasattr(st.components, 'v2'):
            text = (assets / 'index.html').read_text()
            css = text.split('<style>', 1)[1].split('</style>', 1)[0]
            html = text.split('</style>', 1)[1].split('<script', 1)[0]
            engine = (assets / 'effects.js').read_text()
            js = engine + '''\nexport default function(component) {
                const root=component.parentElement;
                if(!root.cycaEffects || root.cycaEffects.card!==root.querySelector('#card')) {
                    if(root.cycaEffects)root.cycaEffects.dispose();
                    root.cycaEffects=createEffects(root);
                }
                root.cycaEffects.receive(component.data.events);
            }'''
            _component = st.components.v2.component('cyca_work_effects', html=html, css=css, js=js)
        else:
            from streamlit.components.v1 import declare_component
            _component = declare_component('cyca_work_effects', path=str(assets))
    with st.sidebar.expander('音と演出の設定'):
        events = st.session_state.get('_effects_events', [])
        if hasattr(st.components, 'v2'):
            _component(data={'events': events}, key='cyca_work_effects')
        else:
            _component(events=events, height=264, key='cyca_work_effects', default=None)
