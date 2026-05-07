import streamlit as st
from google import genai
import json
import pandas as pd
import os
import math
import sys
import time
from io import BytesIO
import openpyxl  # ExcelWriter engine 用

class _CatInputNeeded(Exception):
    """工事種別ごと入力UIへ遷移するための内部シグナル"""
    pass

def _gold_sparkle_html():
    import random
    particles = ""
    for i in range(35):
        left = random.randint(0, 100)
        delay = round(random.uniform(0, 2.5), 2)
        duration = round(random.uniform(2.5, 5.0), 2)
        size = random.choice([4, 6, 8, 10, 12])
        shape = random.choice(["✦", "✧", "★", "·", "✶"])
        opacity = round(random.uniform(0.6, 1.0), 2)
        particles += f'<span style="position:absolute;left:{left}%;top:-10px;font-size:{size}px;color:gold;opacity:{opacity};animation:sparkle-fall {duration}s {delay}s ease-in forwards;">{shape}</span>'
    return f"""
    <div style="position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:999999;overflow:hidden;" id="gold-sparkle">
        {particles}
    </div>
    <style>
    @keyframes sparkle-fall {{
        0% {{ transform: translateY(0) rotate(0deg) scale(1); opacity: 1; }}
        25% {{ transform: translateY(25vh) rotate(90deg) scale(1.2); opacity: 0.9; }}
        50% {{ transform: translateY(50vh) rotate(180deg) scale(0.8); opacity: 0.7; }}
        75% {{ transform: translateY(75vh) rotate(270deg) scale(1.1); opacity: 0.4; }}
        100% {{ transform: translateY(105vh) rotate(360deg) scale(0.5); opacity: 0; }}
    }}
    </style>
    <script>setTimeout(function(){{var e=document.getElementById('gold-sparkle');if(e)e.remove();}},6000);</script>
    """

# 環境によっては標準出力が ascii になり API 応答の日本語でエラーになるため UTF-8 に統一
if getattr(sys.stdout, "reconfigure", None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
# ロケールを UTF-8 に（genai 等の内部で参照される場合に備える）
for env_key in ("LANG", "LC_ALL", "LC_CTYPE"):
    if env_key not in os.environ or not os.environ[env_key].lower().endswith("utf-8"):
        try:
            os.environ[env_key] = "ja_JP.UTF-8"
        except Exception:
            pass

# ==========================================
# 🔐 ログイン設定（複数社対応：ログインした会社ごとにフォーマットを保存）
# ==========================================
APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(APP_DIR, "config")
USERS_JSON = os.path.join(CONFIG_DIR, "users.json")

def _ensure_dirs():
    os.makedirs(CONFIG_DIR, exist_ok=True)


def _get_response_text(response):
    """API応答テキストを .text プロパティに頼らず取得（ascii エンコードエラー回避）"""
    parts_text = []
    for c in (getattr(response, "candidates", None) or []):
        content = getattr(c, "content", None)
        if not content:
            continue
        for p in (getattr(content, "parts", None) or []):
            t = getattr(p, "text", None)
            if t:
                parts_text.append(t)
    if parts_text:
        return "".join(parts_text).strip()
    try:
        return (getattr(response, "text", None) or "").strip()
    except (UnicodeDecodeError, UnicodeEncodeError):
        return ""

def load_users():
    """複数ユーザーを config/users.json から読み込み。無ければ環境変数で1ユーザー。"""
    if os.path.isfile(USERS_JSON):
        try:
            with open(USERS_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    uname = os.environ.get("STREAMLIT_APP_USER", "cycakensetsu")
    pwd = os.environ.get("STREAMLIT_APP_PASSWORD", "cycapass")
    return [{"username": uname, "password": pwd}]

def check_login(username: str, password: str) -> bool:
    users = load_users()
    for u in users:
        if u.get("username") == username and u.get("password") == password:
            return True
    return False

# ==========================================
# 🎨 CYCA ブランドデザイン（ロゴに合わせたモダン・高揚感）
# ==========================================
st.set_page_config(page_title="CYCA smart Link（ベータ版）", page_icon="🏗️", layout="wide")

# ロゴパス（実行ディレクトリ基準）
LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "cyca_logo.png")

st.markdown("""
<style>
    /* CYCA ブランドカラー: ダークブルー・メタリックグレー */
    :root {
        --cyca-deep: #0f2847;
        --cyca-blue: #1e3c72;
        --cyca-mid: #2a5298;
        --cyca-light: #4a7bc8;
        --cyca-silver: #6b8cae;
        --cyca-glow: rgba(74, 123, 200, 0.4);
    }
    /* ヘッダーエリア：白背景・コンパクト。下に流れるグラデーションライン */
    .header-block {
        margin-bottom: 24px;
        padding-bottom: 0;
    }
    .main-header {
        font-size: 1.5rem;
        font-weight: 800;
        color: #1e3c72;
        background: transparent;
        padding: 12px 0 0 0;
        text-align: center;
        margin: 0;
        letter-spacing: 0.12em;
        text-transform: uppercase;
    }
    /* 誰が見てもベータ版とわかる表示 */
    .beta-badge {
        display: inline-block;
        font-size: 0.65rem;
        font-weight: 700;
        color: #fff;
        background: linear-gradient(135deg, #e65100 0%, #ff9800 100%);
        padding: 4px 10px;
        margin-left: 12px;
        border-radius: 20px;
        vertical-align: middle;
        letter-spacing: 0.08em;
        box-shadow: 0 2px 8px rgba(230, 81, 0, 0.4);
    }
    /* 彩架 smart Link の下の流れるグラデーションのバー */
    .header-gradient-line {
        height: 4px;
        margin-top: 10px;
        margin-bottom: 0;
        border-radius: 2px;
        background: linear-gradient(90deg,
            transparent 0%,
            #6b8cae 15%,
            #2a5298 40%,
            #1e3c72 50%,
            #2a5298 60%,
            #6b8cae 85%,
            transparent 100%);
        background-size: 200% 100%;
        animation: gradientFlow 4s ease-in-out infinite;
    }
    @keyframes gradientFlow {
        0%, 100% { background-position: 0% 50%; }
        50% { background-position: 100% 50%; }
    }
    .header-with-logo {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 20px;
        flex-wrap: wrap;
    }
    .header-with-logo img { border-radius: 8px; }
    /* サブ見出し：シャープでプロ仕様 */
    .sub-header {
        font-size: 1.25rem;
        color: #1e3c72;
        font-weight: 700;
        border-bottom: 3px solid #2a5298;
        padding-bottom: 8px;
        margin-bottom: 14px;
        margin-top: 22px;
        letter-spacing: 0.05em;
    }
    /* 金額・強調：ブランドブルーで統一 */
    .highlight-red {
        color: #1e3c72;
        font-size: 1.7rem;
        font-weight: 800;
        background: linear-gradient(135deg, #e8eef7 0%, #d4e0f0 100%);
        padding: 12px 22px;
        border-radius: 10px;
        border-left: 5px solid #2a5298;
        display: inline-block;
        margin-top: 10px;
        box-shadow: 0 2px 12px rgba(30, 60, 114, 0.15);
    }
    /* メインボタン：CYCAブルー・ホバーで浮き上がり */
    .stButton>button {
        background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
        color: white;
        font-weight: bold;
        font-size: 1.1rem;
        padding: 12px 28px;
        border-radius: 10px;
        border: none;
        box-shadow: 0 4px 16px rgba(30, 60, 114, 0.35);
        transition: transform 0.25s ease, box-shadow 0.25s ease;
        width: 100%;
    }
    .stButton>button:hover {
        transform: translateY(-4px);
        box-shadow: 0 8px 24px rgba(30, 60, 114, 0.45);
    }
    /* ダウンロードボタン：成功感のあるアクセント */
    .stDownloadButton>button {
        background: linear-gradient(135deg, #1a5f4a 0%, #27ae60 100%);
        box-shadow: 0 4px 16px rgba(39, 174, 96, 0.35);
    }
    .stDownloadButton>button:hover {
        transform: translateY(-4px);
        box-shadow: 0 8px 24px rgba(39, 174, 96, 0.5);
    }
    /* ログイン画面：区切り線でスッキリ（空の□を出さない） */
    /* アップロード成功：大袈裟にテンション上がる演出 */
    .upload-success-box {
        animation: uploadPulse 1.2s ease-out;
        background: linear-gradient(135deg, #e8f5e9 0%, #c8e6c9 100%);
        border: 2px solid #2e7d32;
        border-radius: 16px;
        padding: 24px 28px;
        margin: 16px 0;
        text-align: center;
        box-shadow: 0 8px 24px rgba(46, 125, 50, 0.25);
    }
    .upload-success-box .big-check { font-size: 3rem; margin-bottom: 8px; }
    .upload-success-box .msg { font-size: 1.35rem; font-weight: 700; color: #1b5e20; }
    @keyframes uploadPulse {
        0% { transform: scale(0.92); opacity: 0.6; }
        50% { transform: scale(1.02); opacity: 1; }
        100% { transform: scale(1); opacity: 1; }
    }
    /* ダウンロード完了：オシャレな完了アクション */
    .download-done-box {
        animation: downloadShine 1.5s ease-out;
        background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
        border: 2px solid #1565c0;
        border-radius: 16px;
        padding: 20px 24px;
        margin: 16px 0;
        text-align: center;
        box-shadow: 0 8px 24px rgba(21, 101, 192, 0.22);
    }
    .download-done-box .msg { font-size: 1.2rem; font-weight: 700; color: #0d47a1; }
    @keyframes downloadShine {
        0% { opacity: 0.7; box-shadow: 0 0 0 0 rgba(21, 101, 192, 0.4); }
        60% { box-shadow: 0 0 0 12px rgba(21, 101, 192, 0); }
        100% { opacity: 1; box-shadow: 0 8px 24px rgba(21, 101, 192, 0.22); }
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 🔐 ログイン画面
# ==========================================
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    if os.path.isfile(LOGO_PATH):
        col_logo, col_title = st.columns([1, 2])
        with col_logo:
            st.image(LOGO_PATH, use_container_width=True)
        with col_title:
            st.markdown('<div class="header-block"><div class="main-header">CYCA smart Link <span class="beta-badge">ベータ版</span></div><div class="header-gradient-line"></div></div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="header-block"><div class="main-header">CYCA smart Link <span class="beta-badge">ベータ版</span></div><div class="header-gradient-line"></div></div>', unsafe_allow_html=True)
    st.markdown("---")
    st.subheader("🔐 ログイン")
    login_user = st.text_input("ユーザー名", placeholder="ユーザー名を入力")
    login_pass = st.text_input("パスワード", type="password", placeholder="パスワードを入力")
    if st.button("ログイン"):
        if check_login(login_user, login_pass):
            st.session_state.logged_in = True
            st.session_state.username = login_user
            st.rerun()
        else:
            st.error("ユーザー名またはパスワードが正しくありません。")
    st.stop()

# ログアウト用・ログイン中ユーザー表示
if "username" not in st.session_state:
    st.session_state.username = ""
with st.sidebar:
    if st.session_state.username:
        st.caption(f"ログイン中: **{st.session_state.username}**")
    if st.button("🚪 ログアウト"):
        st.session_state.logged_in = False
        st.session_state.username = ""
        st.rerun()

# ==========================================
# APIキー（環境変数 GOOGLE_API_KEY または .streamlit/secrets.toml）
# ==========================================
def _get_api_key():
    key = (os.environ.get("GOOGLE_API_KEY") or "").strip()
    if key:
        return key
    try:
        return (getattr(st.secrets, "GOOGLE_API_KEY", None) or "").strip() or None
    except Exception:
        return None

MY_API_KEY = _get_api_key()

# ヘッダー表示（ロゴ + CYCA smart Link）
if os.path.isfile(LOGO_PATH):
    hcol1, hcol2 = st.columns([1, 3])
    with hcol1:
        st.image(LOGO_PATH, use_container_width=True)
    with hcol2:
        st.markdown('<div class="header-block"><div class="main-header">CYCA smart Link <span class="beta-badge">ベータ版</span></div><div class="header-gradient-line"></div></div>', unsafe_allow_html=True)
else:
    st.markdown('<div class="header-block"><div class="main-header">CYCA smart Link <span class="beta-badge">ベータ版</span></div><div class="header-gradient-line"></div></div>', unsafe_allow_html=True)
st.info("💡 職人さんからの見積もり（PDF・写真）をポンと入れるだけ。AIが自動で仕分けし、完璧な利益計算を行います！")
st.caption("本サービスはベータ版です。計算結果は目安であり、正式な見積には必ずご自身で内容をご確認ください。重要な意思決定の唯一の根拠としてご利用にならないでください。")

# ==========================================
# 画面レイアウト（2カラムで見やすく）
# ==========================================
col1, col2 = st.columns(2)

with col1:
    st.markdown('<div class="sub-header">💰 利益シミュレーション</div>', unsafe_allow_html=True)
    profit_mode = st.radio(
        "利益の上乗せ方法を選択してください",
        ("上乗せしない（原価そのまま）", "固定金額（円）を全体に割り振る", "パーセンテージ（%）で全体に乗せる", "見積元（会社）ごとに金額を指定する")
    )

    profit_val = 0.0
    if profit_mode == "パーセンテージ（%）で全体に乗せる":
        profit_val = st.number_input("上乗せする割合（%）を入力", min_value=0.0, value=10.0, step=1.0)
        st.markdown(f"<div class='highlight-red'>🚀 {profit_val} ％ 上乗せ</div>", unsafe_allow_html=True)
    elif profit_mode == "固定金額（円）を全体に割り振る":
        profit_val = st.number_input("上乗せする金額を入力（数字のみ）", min_value=0, value=3000000, step=100000)
        st.markdown(f"<div class='highlight-red'>💰 上乗せ額: {int(profit_val):,} 円</div>", unsafe_allow_html=True)
    elif profit_mode == "見積元（会社）ごとに金額を指定する":
        st.caption("PDFを解析した後、見積元（会社）ごとの原価が表示されます。各社に上乗せ額を入力してください。")

with col2:
    st.markdown('<div class="sub-header">📋 出力フォーマット設定</div>', unsafe_allow_html=True)
    current_username = st.session_state.get("username", "")

    format_choice = st.radio(
        "出力フォーマットを選んでください",
        (
            "彩架建設 企業用見積（2シート：一式表＋明細）",
            "彩架建設 簡易工事見積（1シート：明細のみ）",
            "汎用フォーマット（Excel）",
        )
    )
    if format_choice == "彩架建設 企業用見積（2シート：一式表＋明細）":
        st.caption("Numbers の「工事内容」と「工事内容明細」にそのままコピペできる形式です。列順: No. / 工事品目 / 仕様 / 数量 / 単位 / 単価 / 金額 / 備考")
    elif format_choice == "彩架建設 簡易工事見積（1シート：明細のみ）":
        st.caption("1枚ペラの簡易見積用。Numbers にそのままコピペできます。列順: 商品名・工事名 / 数量 / 単 / 単価（円）/ 金額（円）")
    else:
        st.caption("工事品目・仕様・数量・単位・単価・金額・備考の汎用 Excel を出力します。")

st.markdown("---")
st.markdown('<div class="sub-header">📄 ファイルアップロード</div>', unsafe_allow_html=True)
st.caption("目安：1ファイル20MBまで・複数は5ファイル程度まで。それ以上はタイムアウトやエラーになる場合があります。")

uploaded_files = st.file_uploader(
    "PDF・写真（JPG, PNG）を選択、またはドラッグ＆ドロップ（複数社分まとめてOK！）", 
    type=["pdf", "jpg", "jpeg", "png"], 
    accept_multiple_files=True
)

if uploaded_files:
    file_names = ", ".join([f.name for f in uploaded_files]).replace("<", "&lt;").replace(">", "&gt;")
    st.markdown(f"""
    <div class="upload-success-box">
        <div class="big-check">✅</div>
        <div class="msg">{len(uploaded_files)}件のファイルを受付しました！準備完了です。</div>
        <div style="margin-top:8px; color:#2e7d32; font-size:0.95rem;">{file_names}</div>
    </div>
    """, unsafe_allow_html=True)
    if st.button("✨ AIでデータを解析 ＆ 利益計算を実行する ✨"):
        if not MY_API_KEY:
            st.error("APIキーが設定されていません。環境変数 GOOGLE_API_KEY または .streamlit/secrets.toml に GOOGLE_API_KEY を設定してください。")
        else:
                try:
                    client = genai.Client(api_key=MY_API_KEY)
                    all_extracted_data = []
                    n_files = len(uploaded_files)

                    progress_bar = st.progress(0, text="🚀 解析を開始します...")
                    status_area = st.empty()

                    _anim_steps = [
                        ("📄 ファイルをアップロード中...", "AIサーバーにデータを転送しています"),
                        ("🔍 全ページをスキャン中...", "見積書の文字を高速認識しています"),
                        ("🧮 金額を照合・計算中...", "工事種別と明細を自動で仕分けしています"),
                        ("📊 データを最終整理中...", "もうすぐ完了です！"),
                    ]

                    status_area.markdown(f"""
                    <div style="
                        background: linear-gradient(135deg, #0f2847 0%, #1e3c72 50%, #2a5298 100%);
                        border-radius: 12px;
                        padding: 20px 24px; margin: 12px 0;
                        animation: shimmer 2s ease-in-out infinite;
                        box-shadow: 0 4px 20px rgba(15, 40, 71, 0.3);
                        position: relative;
                        overflow: hidden;
                    ">
                        <div style="font-size: 1.2rem; color: #ffffff; font-weight: 700; margin-bottom: 8px;">
                            📄 {n_files}件のファイルをアップロード中...
                        </div>
                        <div style="color: #a8c8f0; font-size: 0.95rem;">
                            🤖 AIサーバーにデータを転送しています
                        </div>
                        <div style="
                            margin-top: 12px; height: 4px; border-radius: 2px;
                            background: rgba(255,255,255,0.15);
                            overflow: hidden;
                        ">
                            <div style="
                                height: 100%; width: 40%;
                                background: linear-gradient(90deg, #f0c040, #ffd700, #f0c040);
                                border-radius: 2px;
                                animation: loading-slide 1.8s ease-in-out infinite;
                            "></div>
                        </div>
                    </div>
                    <style>
                    @keyframes shimmer {{
                        0%, 100% {{ opacity: 0.92; }}
                        50% {{ opacity: 1; }}
                    }}
                    @keyframes loading-slide {{
                        0% {{ transform: translateX(-100%); }}
                        100% {{ transform: translateX(350%); }}
                    }}
                    </style>
                    """, unsafe_allow_html=True)

                    ai_files = []
                    temp_paths = []
                    for i, uploaded_file in enumerate(uploaded_files):
                        progress_bar.progress(int((i / n_files) * 30), text=f"📤 ファイルをアップロード中... ({i+1}/{n_files})")
                        file_extension = os.path.splitext(uploaded_file.name)[1].lower() or ".pdf"
                        if not file_extension.startswith("."):
                            file_extension = "." + file_extension
                        temp_file_path = os.path.join(APP_DIR, f"temp_upload_{i}{file_extension}")
                        temp_paths.append(temp_file_path)

                        with open(temp_file_path, "wb") as f:
                            f.write(uploaded_file.getbuffer())

                        ai_file = client.files.upload(file=temp_file_path)
                        ai_files.append(ai_file)

                    progress_bar.progress(30, text="🔍 AI が全ファイルを一括解析中...")

                    status_area.markdown(f"""
                    <div style="
                        background: linear-gradient(135deg, #0f2847 0%, #1e3c72 50%, #2a5298 100%);
                        border-radius: 12px;
                        padding: 20px 24px; margin: 12px 0;
                        animation: shimmer 2s ease-in-out infinite;
                        box-shadow: 0 4px 20px rgba(15, 40, 71, 0.3);
                        position: relative; overflow: hidden;
                    ">
                        <div style="font-size: 1.2rem; color: #ffffff; font-weight: 700; margin-bottom: 8px;">
                            🔍 AI が {n_files}件を一括で読み取り中...
                        </div>
                        <div style="color: #a8c8f0; font-size: 0.95rem;">
                            🧮 各見積書の全ページを解析し、工事種別を自動判別しています
                        </div>
                        <div style="
                            margin-top: 12px; height: 4px; border-radius: 2px;
                            background: rgba(255,255,255,0.15); overflow: hidden;
                        ">
                            <div style="
                                height: 100%; width: 40%;
                                background: linear-gradient(90deg, #f0c040, #ffd700, #f0c040);
                                border-radius: 2px;
                                animation: loading-slide 1.8s ease-in-out infinite;
                            "></div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

                    prompt = f"""
                    添付した{n_files}件のファイル（PDFまたは画像）の【すべてのページ・すべての行】を漏れなく正確に読み取り、
                    全ファイル分のデータをまとめて1つのJSON配列で出力してください。

                    【超厳格な仕分けルール】
                    1. 「小計」「合計」「消費税」「税込」「見積金額」「内訳合計」「改小計」「総合計」などの【計算結果や税金の行】は絶対に抽出しないでください。
                    2. 「諸経費」「法定福利費」「運搬費」「処分費」「荷揚げ費」「養生費」「安全対策費」などの【経費項目】は絶対に見落とさずに抽出してください。
                    3. 「値引き」「出精値引き」「端数調整」「調整値引き」などの【値引き項目】も絶対に見落とさずに抽出し、単価と金額は必ずマイナス表記にしてください。
                    4. 【重要: 二重計上の防止と1枚ペラ対応】
                       - もしファイル内に「具体的な内訳明細（2ページ目以降）」が存在する場合、表紙にある「〇〇工事一式」や「〇〇工事費」といった総括的な行は【絶対に無視】して、明細のみを抽出してください。
                       - ただし、ファイルが1枚のみで内訳が一切ない「簡易見積もり」の場合に限り、その「〇〇工事一式」を抽出してください。
                    5. 【全行読み取りの徹底】
                       - 各ページの表の行は1行たりとも飛ばさないでください。
                       - 部位ごと・階ごと・面ごとの小見出し（例：「1階、2階正面」「タイル面」「ポンデ鋼板部」「7階バルコニー」「3階ルーフバルコニー」等）の後にある明細行はすべて個別に抽出してください。
                       - 内訳明細が複数ページにわたる場合、全ページの全行を漏れなく読み取ってください。
                    6. 【見積元の会社名】各ファイルの見積書を出した会社名（差出人）を正確に読み取り、「見積元」に入れてください。
                       宛先（彩架建設 御中など）ではなく、見積書を作成した会社名です。
                    7. 【見積書の税抜合計】各ファイルの見積書に記載されている税抜の最終合計金額（値引き後・税抜）を「見積税抜合計」に入れてください。
                       複数ページある場合は最終ページや明細ページの合計・改小計・税抜合計を優先して読み取ってください。
                       表紙に「〇〇工事一式 ×××円」という概算だけが記載されている場合でも、詳細ページに具体的な明細がある場合はその明細の合計金額を「見積税抜合計」として使ってください。
                    8. 各ファイルの見積書全体が何の工事か（例：防水工事、塗装工事、仮設足場工事など）を自動判別し、
                       同じファイルの項目には同じ「工事種別」を入れてください。
                       【重要】1つのファイルに複数の工事カテゴリ（例：防水工事とシーリング工事）が含まれる場合は、
                       それぞれの明細行に正しい工事種別を入れてください。ただし「見積元」は同じファイルなら同じ会社名です。
                    9. ```json などのマークダウン記号は含めず、純粋なJSON文字列のみを返してください。

                    【出力形式見本（キーを変えないこと）】
                    [
                        {{"見積元": "瀧上工業", "見積税抜合計": 2600000, "工事種別": "防水工事", "項目名": "平場 ウレタン塗膜防水", "仕様": "X-1工法", "数量": 76.1, "単位": "㎡", "単価": 6400, "金額": 487040}},
                        {{"見積元": "瀧上工業", "見積税抜合計": 2600000, "工事種別": "シーリング工事", "項目名": "サッシ廻りシーリング打替え", "仕様": "変成シリコン系", "数量": 84.0, "単位": "m", "単価": 800, "金額": 67200}},
                        {{"見積元": "アキヨシ塗装", "見積税抜合計": 2400000, "工事種別": "外部塗装工事", "項目名": "壁面塗装", "仕様": "エスケープレミアムシリコン", "数量": 634.0, "単位": "㎡", "単価": 1700, "金額": 1077800}}
                    ]
                    """

                    _models_to_try = [
                        "gemini-2.5-flash",
                        "gemini-2.0-flash",
                        "gemini-1.5-flash",
                        "gemini-1.5-pro",
                    ]
                    raw_text = None
                    contents_for_api = ai_files + [prompt]
                    used_model = None

                    for model_idx, model_name in enumerate(_models_to_try):
                        try:
                            progress_bar.progress(30 + (model_idx * 15), text=f"🔍 {model_name} で解析中...")
                            status_area.markdown(f"""
                            <div style="
                                background: linear-gradient(135deg, #0f2847 0%, #1e3c72 50%, #2a5298 100%);
                                border-radius: 12px;
                                padding: 20px 24px; margin: 12px 0;
                                animation: shimmer 2s ease-in-out infinite;
                                box-shadow: 0 4px 20px rgba(15, 40, 71, 0.3);
                                position: relative; overflow: hidden;
                            ">
                                <div style="font-size: 1.2rem; color: #ffffff; font-weight: 700; margin-bottom: 8px;">
                                    🧮 {model_name} で全ファイルを解析中...
                                </div>
                                <div style="color: #a8c8f0; font-size: 0.95rem;">
                                    工事種別と明細を自動で仕分けしています
                                </div>
                                <div style="
                                    margin-top: 12px; height: 4px; border-radius: 2px;
                                    background: rgba(255,255,255,0.15); overflow: hidden;
                                ">
                                    <div style="
                                        height: 100%; width: 40%;
                                        background: linear-gradient(90deg, #f0c040, #ffd700, #f0c040);
                                        border-radius: 2px;
                                        animation: loading-slide 1.8s ease-in-out infinite;
                                    "></div>
                                </div>
                            </div>
                            """, unsafe_allow_html=True)

                            response = client.models.generate_content(
                                model=model_name,
                                contents=contents_for_api
                            )
                            raw_text = _get_response_text(response)
                            if raw_text:
                                used_model = model_name
                                break
                        except Exception as api_err:
                            err_str = str(api_err)
                            is_quota = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "quota" in err_str.lower()
                            if is_quota and model_idx < len(_models_to_try) - 1:
                                next_model = _models_to_try[model_idx + 1]
                                status_area.markdown(f"""
                                <div style="
                                    background: linear-gradient(135deg, #fff3e0 0%, #ffe0b2 100%);
                                    border-left: 4px solid #ef6c00; border-radius: 8px;
                                    padding: 12px 16px; margin: 8px 0;
                                ">
                                    <span style="font-size: 1.05rem;">⚡ {model_name} の無料枠を使い切りました → {next_model} に自動切り替え中...</span>
                                </div>
                                """, unsafe_allow_html=True)
                                time.sleep(2)
                                continue
                            else:
                                raise

                    if used_model:
                        st.toast(f"✅ {used_model} で解析完了！", icon="🤖")

                    for tp in temp_paths:
                        if os.path.isfile(tp):
                            try:
                                os.remove(tp)
                            except Exception:
                                pass

                    if not raw_text:
                        st.error("❌ AIからの応答を取得できませんでした。しばらく時間を置いて再度お試しください。")
                    else:
                        progress_bar.progress(80, text="📊 データを整理しています...")
                        if raw_text.startswith("```"):
                            raw_text = raw_text.strip("`").replace("json\n", "")

                        try:
                            all_extracted_data = json.loads(raw_text)
                            if not isinstance(all_extracted_data, list):
                                all_extracted_data = [all_extracted_data] if isinstance(all_extracted_data, dict) else []
                        except json.JSONDecodeError:
                            st.error("❌ AIの応答をJSON形式で解析できませんでした。ファイル内容をご確認ください。")
                            all_extracted_data = []

                    progress_bar.progress(100, text="🎉 解析完了！")
                    status_area.empty()
                    time.sleep(0.3)

                    if not all_extracted_data:
                        st.warning("読み取れたデータがありませんでした。ファイル形式（PDF・JPG・PNG）と内容をご確認ください。")
                    else:
                        df = pd.DataFrame(all_extracted_data)
                        
                        # エラー回避用の保険
                        if "工事種別" not in df.columns: df["工事種別"] = "一般工事"
                        if "見積元" not in df.columns: df["見積元"] = "不明"
                        if "見積税抜合計" not in df.columns: df["見積税抜合計"] = 0
                        df["見積税抜合計"] = pd.to_numeric(df["見積税抜合計"], errors='coerce').fillna(0).astype(int)
                        if "項目名" not in df.columns:
                            if "工事品目" in df.columns: df["項目名"] = df["工事品目"]
                            elif "名称・内容" in df.columns: df["項目名"] = df["名称・内容"]
                            elif "名称" in df.columns: df["項目名"] = df["名称"]
                            else: df["項目名"] = df.iloc[:, 0]

                        if "仕様" not in df.columns: df["仕様"] = ""
                        if "単位" not in df.columns: df["単位"] = ""
                        
                        s_qty = pd.to_numeric(df["数量"], errors='coerce')
                        s_pri = pd.to_numeric(df["単価"], errors='coerce')
                        s_amt = pd.to_numeric(df["金額"], errors='coerce')
                        df["要確認"] = s_qty.isna() | s_pri.isna() | s_amt.isna()
                        df["数量"] = s_qty.fillna(0)
                        df["単価"] = s_pri.fillna(0)
                        df["金額"] = s_amt.fillna(0)

                        # ▼▼▼ プログラム側での最終ゴミ排除フィルター ▼▼▼
                        invalid_keywords = ["小計", "合計", "消費税", "税込", "税抜", "見積額", "見積金額", "総合計", "内訳合計", "改小計"]
                        pattern = '|'.join(invalid_keywords)
                        df = df[~df['項目名'].astype(str).str.contains(pattern, na=False, regex=True)].reset_index(drop=True)

                        def get_sort_priority(item_name):
                            name = str(item_name)
                            if any(kw in name for kw in ["値引", "調整"]): return 3
                            elif any(kw in name for kw in ["諸経費", "法定福利費", "運搬", "処分", "荷揚げ", "現場管理", "養生"]): return 2
                            else: return 1

                        df['並び替え優先度'] = df['項目名'].apply(get_sort_priority)

                        # ==========================================
                        # 利益上乗せ計算
                        # ==========================================
                        adjustment_amount = 0
                        target_mask = df['並び替え優先度'] == 1

                        if profit_mode == "見積元（会社）ごとに金額を指定する":
                            st.session_state["_extracted_df"] = df.copy()
                            st.session_state["_extracted_format_choice"] = format_choice
                            categories_summary = []
                            for company, grp in df.groupby('見積元', sort=False):
                                company_name = str(company) if pd.notna(company) and str(company).strip() != "" else "不明な会社"
                                item_total = int(grp['金額'].sum())
                                declared_total = int(grp['見積税抜合計'].iloc[0]) if len(grp) > 0 else 0
                                # 明細合計が表紙記載合計以上の場合は明細合計を優先する
                                # （表紙が概算・丸め表示のケースで正確な明細値を採用）
                                if item_total > 0 and item_total >= declared_total:
                                    display_total = item_total
                                elif declared_total > 0:
                                    display_total = declared_total
                                else:
                                    display_total = item_total
                                cat_base = int(grp.loc[grp['並び替え優先度'] == 1, '金額'].sum())
                                work_types = grp['工事種別'].dropna().unique().tolist()
                                work_label = "・".join([str(w) for w in work_types if str(w).strip()])
                                categories_summary.append({
                                    "name": company_name,
                                    "work_label": work_label,
                                    "total": display_total,
                                    "item_total": item_total,
                                    "declared_total": declared_total,
                                    "base": cat_base,
                                })
                            st.session_state["_categories_summary"] = categories_summary

                        elif profit_mode != "上乗せしない（原価そのまま）":
                            total_base_amount = df.loc[target_mask, '金額'].sum()

                            if total_base_amount > 0:
                                if profit_mode == "パーセンテージ（%）で全体に乗せる":
                                    target_total_amount = total_base_amount * (1 + profit_val / 100)
                                elif profit_mode == "固定金額（円）を全体に割り振る":
                                    target_total_amount = total_base_amount + profit_val

                                for idx in df[target_mask].index:
                                    orig_amount = df.at[idx, '金額']
                                    qty = df.at[idx, '数量']
                                    if qty == 0: qty = 1

                                    exact_unit_price = (target_total_amount * (orig_amount / total_base_amount)) / qty
                                    rounded_unit_price = int(math.ceil(exact_unit_price / 10.0) * 10)

                                    df.at[idx, '単価'] = rounded_unit_price
                                    df.at[idx, '金額'] = int(round(rounded_unit_price * qty))

                                actual_new_total = df.loc[target_mask, '金額'].sum()
                                adjustment_amount = int(target_total_amount - actual_new_total)

                        # 「工事種別ごと」モード：ここでは抽出結果のみ表示し、残りは下の入力UIに任せる
                        if profit_mode == "見積元（会社）ごとに金額を指定する":
                            st.session_state["_extracted_df"] = df.copy()
                            cats = st.session_state.get("_categories_summary", [])
                            grand_total = sum(c["total"] for c in cats)
                            st.markdown('<div class="sub-header">📊 見積元ごとの原価が検出されました</div>', unsafe_allow_html=True)
                            st.markdown(f"**原価合計（税抜・値引後）: {grand_total:,} 円**")
                            for c in cats:
                                diff_note = ""
                                declared = c.get("declared_total", 0)
                                if c["item_total"] != c["total"] and declared > 0:
                                    if c["item_total"] >= declared:
                                        diff_note = f"　<span style='color:#e65100; font-size:0.85rem;'>（表紙記載: {declared:,}円 ／ 明細合計: {c['item_total']:,}円 → 明細合計を採用）</span>"
                                    else:
                                        diff_note = f"　<span style='color:#888; font-size:0.85rem;'>（明細合計: {c['item_total']:,}円 → 表紙記載の税抜合計: {declared:,}円 を採用）</span>"
                                st.markdown(f"- **{c['name']}**（{c['work_label']}）: **{c['total']:,} 円**{diff_note}", unsafe_allow_html=True)
                            st.success("✅ 読み取り完了！下にスクロールして、各社の上乗せ額を入力してください。")
                            raise _CatInputNeeded()

                        # ==========================================
                        # シート1（2枚目用：総括表）の作成
                        # ==========================================
                        _blank = {"項目名": "", "仕様": "", "数量": "", "単位": "", "単価": "", "金額": "", "要確認": False}
                        sheet1_rows = []
                        df_main = df[df['並び替え優先度'] == 1]
                        if not df_main.empty:
                            for category, group in df_main.groupby('工事種別', sort=False):
                                category_name = str(category) if pd.notna(category) and str(category).strip() != "" else "その他工事"
                                cat_sum = int(group['金額'].sum())
                                sheet1_rows.append({
                                    "項目名": f"{category_name}一式", "仕様": "", "数量": 1, "単位": "式", "単価": cat_sum, "金額": cat_sum, "要確認": False
                                })
                                sheet1_rows.append(_blank.copy())

                        for _, row in df[df['並び替え優先度'] == 2].iterrows():
                            sheet1_rows.append(row.to_dict())
                            sheet1_rows.append(_blank.copy())

                        if adjustment_amount != 0:
                            sheet1_rows.append({
                                "項目名": "【⚠️要確認】端数調整（任意変更可）", "仕様": "", "数量": 1, "単位": "式", "単価": adjustment_amount, "金額": adjustment_amount, "要確認": False
                            })
                            sheet1_rows.append(_blank.copy())

                        for _, row in df[df['並び替え優先度'] == 3].iterrows():
                            sheet1_rows.append(row.to_dict())
                            sheet1_rows.append(_blank.copy())

                        if sheet1_rows and sheet1_rows[-1] == _blank:
                            sheet1_rows.pop()

                        df_sheet1 = pd.DataFrame(sheet1_rows)

                        # ==========================================
                        # シート2（明細用：美しいブロック）の作成
                        # ==========================================
                        sheet2_rows = []
                        if not df_main.empty:
                            for category, group in df_main.groupby('工事種別', sort=False):
                                category_name = str(category) if pd.notna(category) and str(category).strip() != "" else "その他工事"
                                sheet2_rows.append({
                                    "項目名": f"【 {category_name} 】", "仕様": "", "数量": "", "単位": "", "単価": "", "金額": "", "要確認": False
                                })
                                for _, row in group.iterrows():
                                    sheet2_rows.append(row.to_dict())

                        df_sheet2 = pd.DataFrame(sheet2_rows)

                        # ==========================================
                        # 彩架建設フォーマットに成形（コピペ用）
                        # ==========================================
                        # --- 企業用見積: No./工事品目/仕様/数量/単位/単価/金額/備考 ---
                        cyca_corp_columns = ["No.", "工事品目", "仕様", "数量", "単位", "単価", "金額", "備考"]

                        def format_cyca_corp(df_in):
                            if df_in.empty: return pd.DataFrame(columns=cyca_corp_columns)
                            rows = []
                            seq = 0
                            for _, row in df_in.iterrows():
                                item = row.get("項目名", "")
                                amt = row.get("金額", "")
                                has_amount = amt != "" and pd.notna(amt) and amt != 0
                                is_category_header = str(item).startswith("【") and not has_amount
                                if is_category_header or str(item) == "":
                                    rows.append({"No.": "", "工事品目": item, "仕様": "", "数量": "", "単位": "", "単価": "", "金額": "", "備考": ""})
                                else:
                                    seq += 1
                                    rows.append({
                                        "No.": seq,
                                        "工事品目": item,
                                        "仕様": row.get("仕様", ""),
                                        "数量": row.get("数量", ""),
                                        "単位": row.get("単位", ""),
                                        "単価": row.get("単価", ""),
                                        "金額": row.get("金額", ""),
                                        "備考": "要確認" if row.get("要確認") else ""
                                    })
                            return pd.DataFrame(rows)

                        # --- 簡易工事見積: 商品名・工事名/数量/単/単価（円）/金額（円） ---
                        cyca_simple_columns = ["商品名・工事名", "数量", "単", "単価（円）", "金額（円）"]

                        def format_cyca_simple(df_in):
                            if df_in.empty: return pd.DataFrame(columns=cyca_simple_columns)
                            rows = []
                            for _, row in df_in.iterrows():
                                item = row.get("項目名", "")
                                if str(item).startswith("【") or str(item) == "":
                                    continue
                                spec = row.get("仕様", "")
                                name = str(item)
                                if spec and str(spec) not in ("", "nan"):
                                    name = f"{item}　{spec}"
                                rows.append({
                                    "商品名・工事名": name,
                                    "数量": row.get("数量", ""),
                                    "単": row.get("単位", ""),
                                    "単価（円）": row.get("単価", ""),
                                    "金額（円）": row.get("金額", ""),
                                })
                            return pd.DataFrame(rows)

                        # --- 汎用: 工事品目/仕様/数量/単位/単価/金額/備考 ---
                        def format_generic(df_in):
                            if df_in.empty: return pd.DataFrame()
                            df_out = df_in.copy()
                            df_out["工事品目"] = df_out["項目名"]
                            if "要確認" in df_out.columns:
                                df_out["備考"] = df_out["要確認"].map(lambda x: "要確認" if x else "")
                            else:
                                df_out["備考"] = ""
                            return df_out[["工事品目", "仕様", "数量", "単位", "単価", "金額", "備考"]]

                        is_cyca = format_choice.startswith("彩架建設")
                        is_simple = "簡易" in format_choice

                        if is_simple:
                            df_sheet1_final = format_cyca_simple(df_sheet1)
                            df_sheet2_final = format_cyca_simple(df_sheet2)
                        elif is_cyca:
                            df_sheet1_final = format_cyca_corp(df_sheet1)
                            df_sheet2_final = format_cyca_corp(df_sheet2)
                        else:
                            df_sheet1_final = format_generic(df_sheet1)
                            df_sheet2_final = format_generic(df_sheet2)

                        for col in ["数量", "単価", "金額", "単価（円）", "金額（円）"]:
                            if col in df_sheet1_final.columns:
                                df_sheet1_final[col] = pd.to_numeric(df_sheet1_final[col], errors="coerce")
                            if col in df_sheet2_final.columns:
                                df_sheet2_final[col] = pd.to_numeric(df_sheet2_final[col], errors="coerce")
                        # No.列を文字列に統一（ArrowTypeError防止）
                        if "No." in df_sheet1_final.columns:
                            df_sheet1_final["No."] = df_sheet1_final["No."].astype(str).replace("", "")
                        if "No." in df_sheet2_final.columns:
                            df_sheet2_final["No."] = df_sheet2_final["No."].astype(str).replace("", "")

                        st.toast("計算完了！データ準備OK", icon="✅")
                        st.markdown('<div class="sub-header">計算完了！Numbers にコピペできます</div>', unsafe_allow_html=True)
                        st.markdown(_gold_sparkle_html(), unsafe_allow_html=True)

                        if is_simple:
                            df_simple_merged = pd.concat([df_sheet1_final, df_sheet2_final], ignore_index=True)
                            df_simple_merged = df_simple_merged[df_simple_merged["商品名・工事名"].astype(str).str.strip() != ""]
                            st.write("▼ 簡易見積データ（Numbers の表にそのままコピペ）")
                            st.dataframe(df_simple_merged)
                        else:
                            st.write("▼ 2枚目用：一式表（Numbers「工事内容」にコピペ）")
                            st.dataframe(df_sheet1_final)
                            st.write("▼ 3枚目用：明細（Numbers「工事内容明細」にコピペ）")
                            st.dataframe(df_sheet2_final)

                        check_cols = df_sheet2_final.columns.tolist()
                        if "備考" in check_cols and df_sheet2_final["備考"].astype(str).str.contains("要確認").any():
                            st.info("一部の行は見積書から数値が正しく読み取れなかったため「要確認」と表示しています。実際の見積書をご確認ください。")

                        output = BytesIO()
                        with pd.ExcelWriter(output, engine='openpyxl') as writer:
                            if is_simple:
                                df_simple_merged.to_excel(writer, index=False, header=False, sheet_name='明細データ')
                            else:
                                df_sheet1_final.to_excel(writer, index=False, header=False, sheet_name='一式表（2枚目用）')
                                df_sheet2_final.to_excel(writer, index=False, header=False, sheet_name='明細データ（3枚目用）')
                        excel_data = output.getvalue()

                        st.markdown("""
                        <div class="download-done-box" style="position: relative; overflow: hidden;">
                            <div class="msg">📥 ダウンロード → Numbers を開いて → データ部分をコピペ！</div>
                            <div class="sparkle-bar"></div>
                        </div>
                        <style>
                        .sparkle-bar {
                            position: absolute; bottom: 0; left: 0;
                            width: 100%; height: 3px;
                            background: linear-gradient(90deg, transparent, #1565c0, #42a5f5, #1565c0, transparent);
                            background-size: 200% 100%;
                            animation: sparkleSlide 2s ease-in-out infinite;
                        }
                        @keyframes sparkleSlide {
                            0% { background-position: -200% 0; }
                            100% { background-position: 200% 0; }
                        }
                        </style>
                        """, unsafe_allow_html=True)
                        if st.download_button(
                            label="📥 Excel をダウンロードする",
                            data=excel_data,
                            file_name="CYCA_smartLink_見積完成版.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        ):
                            st.toast("ダウンロード完了！Numbers にコピペして仕上げましょう 🚀", icon="🎉")
                            st.markdown("""
                            <div style="
                                background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 50%, #e3f2fd 100%);
                                border: 2px solid #1565c0; border-radius: 12px;
                                padding: 16px 24px; margin: 12px 0; text-align: center;
                                animation: celebratePulse 1.5s ease-out;
                                box-shadow: 0 4px 20px rgba(21, 101, 192, 0.3);
                            ">
                                <span style="font-size: 1.3rem; font-weight: 700; color: #0d47a1;">
                                    ✨ ダウンロード完了！お疲れ様です ✨
                                </span>
                            </div>
                            <style>
                            @keyframes celebratePulse {
                                0% { transform: scale(0.95); opacity: 0; box-shadow: 0 0 0 0 rgba(21, 101, 192, 0.5); }
                                50% { transform: scale(1.02); box-shadow: 0 0 20px 8px rgba(21, 101, 192, 0.2); }
                                100% { transform: scale(1); opacity: 1; box-shadow: 0 4px 20px rgba(21, 101, 192, 0.3); }
                            }
                            </style>
                            """, unsafe_allow_html=True)

                except _CatInputNeeded:
                    pass
                except Exception as e:
                    try:
                        err_msg = str(e)
                    except Exception:
                        err_msg = "（エラー内容を表示できませんでした）"
                    if "ascii" in err_msg.lower() and "codec" in err_msg.lower():
                        st.error(
                            "❌ 文字コードのエラーが発生しました。\n\n"
                            "**対処法：** いったん Streamlit を止めて（Ctrl+C）、ターミナルで次のどちらかで起動し直してください。\n\n"
                            "• `./run_streamlit_utf8.sh`（Applications フォルダ内で実行）\n"
                            "• または `PYTHONUTF8=1 streamlit run extract_data.py`（同じく Applications に cd してから）"
                        )
                    else:
                        try:
                            st.error(f"❌ エラーが発生しました: {err_msg}")
                        except Exception:
                            st.error("❌ エラーが発生しました。")

# ==========================================
# 見積元（会社）ごとに金額を指定するモード（2段階目）
# ==========================================
if (profit_mode == "見積元（会社）ごとに金額を指定する"
    and "_extracted_df" in st.session_state
    and "_categories_summary" in st.session_state):

    cats = st.session_state["_categories_summary"]
    if cats:
        st.markdown("---")
        st.markdown('<div class="sub-header">💰 見積元ごとの上乗せ額を入力してください</div>', unsafe_allow_html=True)

        grand_total = sum(c["total"] for c in cats)
        st.markdown(f"**原価合計（税抜・値引後）: {grand_total:,} 円**")

        cat_profits = {}
        cols_per = st.columns(min(len(cats), 3))
        for i, c in enumerate(cats):
            with cols_per[i % min(len(cats), 3)]:
                st.markdown(f"**{c['name']}**<br><span style='color:#666; font-size:0.85rem;'>{c['work_label']}</span>", unsafe_allow_html=True)
                st.markdown(f"<span style='color:#1565c0; font-size:1.2rem; font-weight:700;'>{c['total']:,} 円</span>", unsafe_allow_html=True)
                cat_profits[c["name"]] = st.number_input(
                    f"上乗せ額（円）",
                    min_value=0, value=0, step=50000,
                    key=f"cat_profit_{c['name']}"
                )

        total_profit = sum(cat_profits.values())
        if total_profit > 0:
            st.markdown(f"<div class='highlight-red'>💰 上乗せ合計: {total_profit:,} 円 → 出力合計（税抜）: {grand_total + total_profit:,} 円</div>", unsafe_allow_html=True)

        if st.button("✨ 上乗せを適用して出力する ✨", key="apply_cat_profit"):
            df = st.session_state["_extracted_df"].copy()
            format_choice = st.session_state.get("_extracted_format_choice", "彩架建設 企業用見積（2シート：一式表＋明細）")
            target_mask = df['並び替え優先度'] == 1

            adjustment_amount = 0
            total_adj = 0
            for company_name, profit_amount in cat_profits.items():
                if profit_amount <= 0:
                    continue
                company_mask = target_mask & (df['見積元'].astype(str).apply(
                    lambda x, cn=company_name: (str(x) if pd.notna(x) and str(x).strip() != "" else "不明な会社") == cn
                ))
                company_base = df.loc[company_mask, '金額'].sum()
                if company_base <= 0:
                    continue
                company_target = company_base + profit_amount

                for idx in df[company_mask].index:
                    orig_amount = df.at[idx, '金額']
                    qty = df.at[idx, '数量']
                    if qty == 0: qty = 1
                    exact_unit_price = (company_target * (orig_amount / company_base)) / qty
                    rounded_unit_price = int(math.ceil(exact_unit_price / 10.0) * 10)
                    df.at[idx, '単価'] = rounded_unit_price
                    df.at[idx, '金額'] = int(round(rounded_unit_price * qty))

                company_actual = df.loc[company_mask, '金額'].sum()
                total_adj += int(company_target - company_actual)

            adjustment_amount = total_adj

            # --- 以下、通常フローと同じ出力処理 ---
            _blank = {"項目名": "", "仕様": "", "数量": "", "単位": "", "単価": "", "金額": "", "要確認": False}
            sheet1_rows = []
            df_main = df[target_mask]
            if not df_main.empty:
                for category, group in df_main.groupby('工事種別', sort=False):
                    category_name = str(category) if pd.notna(category) and str(category).strip() != "" else "その他工事"
                    cat_sum = int(group['金額'].sum())
                    sheet1_rows.append({
                        "項目名": f"{category_name}一式", "仕様": "", "数量": 1, "単位": "式", "単価": cat_sum, "金額": cat_sum, "要確認": False
                    })
                    sheet1_rows.append(_blank.copy())

            for _, row in df[df['並び替え優先度'] == 2].iterrows():
                sheet1_rows.append(row.to_dict())
                sheet1_rows.append(_blank.copy())

            if adjustment_amount != 0:
                sheet1_rows.append({
                    "項目名": "【⚠️要確認】端数調整（任意変更可）", "仕様": "", "数量": 1, "単位": "式", "単価": adjustment_amount, "金額": adjustment_amount, "要確認": False
                })
                sheet1_rows.append(_blank.copy())

            for _, row in df[df['並び替え優先度'] == 3].iterrows():
                sheet1_rows.append(row.to_dict())
                sheet1_rows.append(_blank.copy())

            if sheet1_rows and sheet1_rows[-1] == _blank:
                sheet1_rows.pop()

            df_sheet1 = pd.DataFrame(sheet1_rows)

            sheet2_rows = []
            if not df_main.empty:
                for category, group in df_main.groupby('工事種別', sort=False):
                    category_name = str(category) if pd.notna(category) and str(category).strip() != "" else "その他工事"
                    sheet2_rows.append({
                        "項目名": f"【 {category_name} 】", "仕様": "", "数量": "", "単位": "", "単価": "", "金額": "", "要確認": False
                    })
                    for _, row in group.iterrows():
                        sheet2_rows.append(row.to_dict())

            df_sheet2 = pd.DataFrame(sheet2_rows)

            cyca_corp_columns = ["No.", "工事品目", "仕様", "数量", "単位", "単価", "金額", "備考"]
            cyca_simple_columns = ["商品名・工事名", "数量", "単", "単価（円）", "金額（円）"]

            def _fmt_corp(df_in):
                if df_in.empty: return pd.DataFrame(columns=cyca_corp_columns)
                rows = []; seq = 0
                for _, row in df_in.iterrows():
                    item = row.get("項目名", "")
                    amt = row.get("金額", "")
                    has_amount = amt != "" and pd.notna(amt) and amt != 0
                    is_cat = str(item).startswith("【") and not has_amount
                    if is_cat or str(item) == "":
                        rows.append({"No.": "", "工事品目": item, "仕様": "", "数量": "", "単位": "", "単価": "", "金額": "", "備考": ""})
                    else:
                        seq += 1
                        rows.append({"No.": seq, "工事品目": item, "仕様": row.get("仕様", ""), "数量": row.get("数量", ""), "単位": row.get("単位", ""), "単価": row.get("単価", ""), "金額": row.get("金額", ""), "備考": "要確認" if row.get("要確認") else ""})
                return pd.DataFrame(rows)

            def _fmt_simple(df_in):
                if df_in.empty: return pd.DataFrame(columns=cyca_simple_columns)
                rows = []
                for _, row in df_in.iterrows():
                    item = row.get("項目名", "")
                    if str(item).startswith("【") or str(item) == "": continue
                    spec = row.get("仕様", "")
                    name = str(item)
                    if spec and str(spec) not in ("", "nan"): name = f"{item}　{spec}"
                    rows.append({"商品名・工事名": name, "数量": row.get("数量", ""), "単": row.get("単位", ""), "単価（円）": row.get("単価", ""), "金額（円）": row.get("金額", "")})
                return pd.DataFrame(rows)

            def _fmt_generic(df_in):
                if df_in.empty: return pd.DataFrame()
                df_out = df_in.copy()
                df_out["工事品目"] = df_out["項目名"]
                if "要確認" in df_out.columns: df_out["備考"] = df_out["要確認"].map(lambda x: "要確認" if x else "")
                else: df_out["備考"] = ""
                return df_out[["工事品目", "仕様", "数量", "単位", "単価", "金額", "備考"]]

            is_cyca = format_choice.startswith("彩架建設")
            is_simple = "簡易" in format_choice
            if is_simple:
                df_sheet1_final = _fmt_simple(df_sheet1); df_sheet2_final = _fmt_simple(df_sheet2)
            elif is_cyca:
                df_sheet1_final = _fmt_corp(df_sheet1); df_sheet2_final = _fmt_corp(df_sheet2)
            else:
                df_sheet1_final = _fmt_generic(df_sheet1); df_sheet2_final = _fmt_generic(df_sheet2)

            for col in ["数量", "単価", "金額", "単価（円）", "金額（円）"]:
                if col in df_sheet1_final.columns: df_sheet1_final[col] = pd.to_numeric(df_sheet1_final[col], errors="coerce")
                if col in df_sheet2_final.columns: df_sheet2_final[col] = pd.to_numeric(df_sheet2_final[col], errors="coerce")
            if "No." in df_sheet1_final.columns: df_sheet1_final["No."] = df_sheet1_final["No."].astype(str).replace("", "")
            if "No." in df_sheet2_final.columns: df_sheet2_final["No."] = df_sheet2_final["No."].astype(str).replace("", "")

            st.toast("計算完了！", icon="✅")
            st.markdown(_gold_sparkle_html(), unsafe_allow_html=True)
            st.markdown('<div class="sub-header">計算完了！Numbers にコピペできます</div>', unsafe_allow_html=True)

            if is_simple:
                df_simple_merged = pd.concat([df_sheet1_final, df_sheet2_final], ignore_index=True)
                df_simple_merged = df_simple_merged[df_simple_merged["商品名・工事名"].astype(str).str.strip() != ""]
                st.write("▼ 簡易見積データ（Numbers の表にそのままコピペ）")
                st.dataframe(df_simple_merged)
            else:
                st.write("▼ 2枚目用：一式表（Numbers「工事内容」にコピペ）")
                st.dataframe(df_sheet1_final)
                st.write("▼ 3枚目用：明細（Numbers「工事内容明細」にコピペ）")
                st.dataframe(df_sheet2_final)

            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                if is_simple:
                    df_simple_merged.to_excel(writer, index=False, header=False, sheet_name='明細データ')
                else:
                    df_sheet1_final.to_excel(writer, index=False, header=False, sheet_name='一式表（2枚目用）')
                    df_sheet2_final.to_excel(writer, index=False, header=False, sheet_name='明細データ（3枚目用）')
            excel_data = output.getvalue()

            st.markdown("""
            <div class="download-done-box" style="position: relative; overflow: hidden;">
                <div class="msg">📥 ダウンロード → Numbers を開いて → データ部分をコピペ！</div>
                <div class="sparkle-bar"></div>
            </div>
            """, unsafe_allow_html=True)
            if st.download_button(
                label="📥 Excel をダウンロードする",
                data=excel_data,
                file_name="CYCA_smartLink_見積完成版.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="download_cat_profit"
            ):
                st.toast("ダウンロード完了！Numbers にコピペして仕上げましょう 🚀", icon="🎉")