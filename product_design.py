"""CYCA's restrained navy/cyan identity; no business data in decorations."""
from html import escape

STYLE = '''<style>
.cyca-received{margin:14px 0 18px;padding:18px 22px;background:#f3f7fa;border:1px solid #d8e4eb;border-radius:14px;color:#183248}.cyca-received-heading{display:flex;justify-content:space-between;align-items:center;font-size:17px;font-weight:700}.cyca-received-heading b{font-size:13px;background:#e0edf2;padding:3px 10px;border-radius:20px}.cyca-file-list{list-style:none;padding:0!important;margin:12px 0!important}.cyca-file-list li{display:flex;align-items:baseline;gap:12px;padding:10px 0;border-top:1px solid #dce6ed;font-size:14px;line-height:1.6;overflow-wrap:anywhere}.cyca-file-number{font-size:12px;color:#527589;flex:0 0 24px;font-variant-numeric:tabular-nums}.cyca-received p{font-size:13px;color:#526879;margin:8px 0 0}.cyca-file-list li span:last-child{white-space:pre-wrap}
[data-testid="stFileUploaderFile"]{width:100%!important;min-width:0!important}[data-testid="stFileUploaderFileName"]{white-space:normal!important;overflow-wrap:anywhere;max-width:none!important}[data-testid="stFileUploaderFileNames"]{max-height:none!important;overflow:visible!important}
.cyca-scan{position:relative;isolation:isolate;overflow:hidden;display:flex;align-items:center;gap:24px;padding:26px 30px;margin:16px 0;border:1px solid #24516b;border-radius:16px;background:radial-gradient(ellipse at 5% 10%,#0f4957aa,transparent 65%),#091727;color:#edf8ff;box-shadow:0 12px 32px #08223824}
.cyca-scan:after{content:"";position:absolute;inset:0;pointer-events:none;opacity:.2;background:linear-gradient(#4ccbd011 1px,transparent 1px),linear-gradient(90deg,#4ccbd011 1px,transparent 1px);background-size:24px 24px;z-index:-1}
.cyca-ring{position:relative;flex:0 0 62px;width:62px;height:62px;border:1px solid #35798966;border-radius:50%;display:grid;place-items:center;font-size:12px;font-weight:750;letter-spacing:.1em;color:#7ee9e6}
.cyca-ring:before,.cyca-ring:after{content:"";position:absolute;border-radius:50%;inset:-6px;border:2px solid transparent;border-top-color:#6de6df;border-right-color:#6de6df55;animation:cyca-orbit 2.5s linear infinite}.cyca-ring:after{inset:6px;border-top-color:#779edc;animation-direction:reverse;animation-duration:4s}
.cyca-ready .cyca-ring:before,.cyca-ready .cyca-ring:after{animation:none}.cyca-scan .eyebrow{font:600 10px/1.4 -apple-system,sans-serif;letter-spacing:.19em;color:#75cfce;margin-bottom:7px}.cyca-scan .headline{font-size:20px;line-height:1.5;font-weight:750;color:#f5fbff}.cyca-scan .detail{font-size:13px;line-height:1.6;color:#b6c9de;margin-top:5px;overflow-wrap:anywhere}.cyca-track{height:2px;margin-top:16px;overflow:hidden;background:#284456;border-radius:2px}.cyca-track i{display:block;width:38%;height:100%;background:linear-gradient(90deg,transparent,#70e5e2,#84a5ef);animation:cyca-track 2s ease-in-out infinite}
.cyca-scan:not(.cyca-ready):before{content:"";position:absolute;z-index:-1;inset:-80% -30%;pointer-events:none;background:linear-gradient(90deg,transparent 40%,#75ece91c 50%,transparent 60%);transform:rotate(-18deg);animation:cyca-scan-pass 2.7s ease-in-out infinite}@keyframes cyca-scan-pass{from{translate:-65% 0}to{translate:65% 0}}
@keyframes cyca-orbit{to{transform:rotate(360deg)}}@keyframes cyca-track{from{transform:translateX(-110%)}to{transform:translateX(370%)}}
.stButton>button{color:#f4faff;background:linear-gradient(120deg,#142c49,#254b71);border:1px solid #315d7a;box-shadow:0 3px 12px #122c4c22;border-radius:10px}.stButton>button p,.stDownloadButton>button p{color:inherit}.stButton>button:hover{color:#fff;transform:translateY(-1px);box-shadow:0 4px 18px #37b7bc35}.stButton>button:disabled{color:#748499;background:#e4eaf0;box-shadow:none;border-color:#d6dfe8}.stDownloadButton>button{color:white;background:linear-gradient(120deg,#174454,#226775);box-shadow:0 3px 12px #1d536b24}.stDownloadButton>button:hover{transform:translateY(-1px)}
.beta-badge{background:#2b5277;box-shadow:none}.header-gradient-line{background:linear-gradient(90deg,transparent,#34547b,#54c8cf,#34547b,transparent)}
.cyca-scan:before,.cyca-ring:before,.cyca-ring:after,.cyca-track i,.header-gradient-line,.upload-success-box{animation-play-state:var(--cyca-motion-play-state,running)}.cyca-scan:before{opacity:var(--cyca-sweep-opacity,1)}
@media(max-width:600px){.cyca-scan{padding:20px;gap:17px}.cyca-scan .headline{font-size:17px}.cyca-ring{flex-basis:46px;width:46px;height:46px}}
@media(prefers-reduced-motion:reduce){.cyca-scan:before,.cyca-ring:before,.cyca-ring:after,.cyca-track i,.header-gradient-line,.upload-success-box{animation:none!important}.stButton>button,.stDownloadButton>button{transition:none!important;transform:none!important}}
</style>'''


def loading_panel(count, page=None):
    detail = '文字・数量・単価を読み取り、工種ごとに整理しています。'
    if page is not None:
        detail = f'原本の{int(page)}ページ目を読み取っています。完了後に内容を確認できます。'
    return f'''<div class="cyca-scan" role="status"><div class="cyca-ring">AI</div><div style="flex:1"><div class="eyebrow">CYCA SMART LINK / ANALYZING</div><div class="headline">{int(count)}件の原本から、見積の構成を読み解く。</div><div class="detail">{detail}</div><div class="cyca-track"><i></i></div></div></div>'''


def ready_panel(names, action_label='見積書を読み取る'):
    rows = ''.join(f'<li><span class="cyca-file-number">{i:02}</span><span>{escape(name)}</span></li>'
                   for i,name in enumerate(names,1))
    return f'''<section class="cyca-received"><div class="cyca-received-heading"><span>受け取った見積書</span><b>{len(names)}件</b></div>
<ol class="cyca-file-list">{rows}</ol><p>内容を確認する準備ができました。下の「{escape(action_label)}」を押してください。</p></section>'''
