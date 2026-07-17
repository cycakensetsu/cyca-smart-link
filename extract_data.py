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
from gemini_resilience import (
    AI_RETRY_MESSAGE,
    GeminiTemporaryUnavailable,
    build_page_prompt,
    call_gemini_with_retry,
    configure_gemini_logging,
    get_configured_gemini_models,
    is_gemini_temporary_error,
    log_gemini_analysis_complete,
    parse_gemini_json_payload,
    prepare_upload_for_gemini_pages,
)
from estimate_pipeline import (
    OUTPUT_COLUMNS,
    DETAIL_SHEET_NAME,
    QUOTE_SHEET_NAME,
    assign_unknown_vendors_to_pdf_vendor,
    apply_company_profit_to_details,
    apply_profit,
    build_vendor_copy_sheets,
    build_intermediate_dataframe,
    build_cost_basis_dataframe,
    build_quote_summary_dataframe,
    build_vendor_work_summary_dataframe,
    numbers_detail_dataframe,
    output_dataframe,
    normalize_summary_data,
    summary_data_from_cost_dataframe,
    split_extraction_payload,
    validate_intermediate,
    vendor_detail_dataframe,
)
from template_fill_test import TEMPLATE_FILL_TEST_NAME, build_template_fill_test_workbook

try:
    from estimate_pipeline import WORK_SUMMARY_SHEET_NAME, build_work_summary_dataframe
except ImportError:
    WORK_SUMMARY_SHEET_NAME = "工事別まとめ"

    def build_work_summary_dataframe(summary_data, detail_df, tax_rate=0.10):
        return build_quote_summary_dataframe(summary_data, detail_df, tax_rate)

class _CatInputNeeded(Exception):
    """工事種別ごと入力UIへ遷移するための内部シグナル"""
    pass


SIMPLE_DETAIL_COLUMNS = ["商品名・工事名", "数量", "単位", "単価（円）", "金額（円）", "備考"]


def simple_detail_dataframe(detail_df):
    """簡易工事見積用。Numbersへ崩れず貼るための固定6列表。"""
    if detail_df is None or detail_df.empty:
        return pd.DataFrame(columns=SIMPLE_DETAIL_COLUMNS), []
    source = output_dataframe(detail_df)
    simple = pd.DataFrame(
        {
            "商品名・工事名": source["品名"],
            "数量": pd.to_numeric(source["数量"], errors="coerce"),
            "単位": source["単位"],
            "単価（円）": pd.to_numeric(source["原価単価"], errors="coerce"),
            "金額（円）": pd.to_numeric(source["原価金額"], errors="coerce"),
            "備考": source["備考"],
        },
        index=source.index,
    )
    return simple[SIMPLE_DETAIL_COLUMNS], []

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

def _cleanup_temp_paths(temp_paths):
    for temp_path in temp_paths or []:
        if os.path.isfile(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass

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
    analyze_clicked = st.button("✨ AIでデータを解析 ＆ 利益計算を実行する ✨")
    if st.session_state.pop("_force_analyze", False):
        analyze_clicked = True
    if analyze_clicked:
        if not MY_API_KEY:
            st.error("APIキーが設定されていません。環境変数 GOOGLE_API_KEY または .streamlit/secrets.toml に GOOGLE_API_KEY を設定してください。")
        else:
                try:
                    configure_gemini_logging()
                    client = genai.Client(api_key=MY_API_KEY)
                    all_extracted_data = []
                    summary_sources = []
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

                    page_jobs = []
                    temp_paths = []
                    for i, uploaded_file in enumerate(uploaded_files):
                        progress_bar.progress(int((i / n_files) * 30), text=f"📄 PDF/画像をページ単位に準備中... ({i+1}/{n_files})")
                        file_extension = os.path.splitext(uploaded_file.name)[1].lower() or ".pdf"
                        if not file_extension.startswith("."):
                            file_extension = "." + file_extension
                        temp_file_path = os.path.join(APP_DIR, f"temp_upload_{i}{file_extension}")
                        temp_paths.append(temp_file_path)

                        with open(temp_file_path, "wb") as f:
                            f.write(uploaded_file.getbuffer())

                        page_jobs.extend(prepare_upload_for_gemini_pages(temp_file_path, uploaded_file.name))

                    progress_bar.progress(30, text=f"🔍 AI が{len(page_jobs)}ページを順番に解析中...")

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
                            🔍 AI が {len(page_jobs)}ページを順番に読み取り中...
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

                    primary_model, fallback_models = get_configured_gemini_models(st.secrets)
                    successful_models = []

                    def _on_gemini_model_start(model_name, page_number):
                        status_area.info(
                            f"使用中モデル: {model_name} / リトライ回数: 0 / ページ: {page_number or '-'}"
                        )

                    def _on_gemini_retry(model_name, attempt, delay, error_code, page_number):
                        status_area.markdown(f"""
                        <div style="
                            background: linear-gradient(135deg, #fff3e0 0%, #ffe0b2 100%);
                            border-left: 4px solid #ef6c00; border-radius: 8px;
                            padding: 12px 16px; margin: 8px 0;
                        ">
                            <span style="font-size: 1.05rem;">AI解析サーバーが混み合っています。{int(delay)}秒後に自動再試行します。</span>
                            <div style="color:#6d4c41; font-size:0.9rem; margin-top:4px;">
                                model={model_name} / retry={attempt} / code={error_code or "unknown"} / page={page_number or "-"}
                            </div>
                        </div>
                        """, unsafe_allow_html=True)

                    total_pages = max(len(page_jobs), 1)
                    for page_idx, page in enumerate(page_jobs, start=1):
                        progress = 30 + int((page_idx - 1) / total_pages * 50)
                        progress_bar.progress(
                            progress,
                            text=f"🔍 {page.source_name} {page.page_number}/{page.total_pages}ページを解析中..."
                        )
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
                                🧮 {page.source_name} {page.page_number}/{page.total_pages}ページを解析中...
                            </div>
                            <div style="color: #a8c8f0; font-size: 0.95rem;">
                                使用モデル: {primary_model} / フォールバック: {", ".join(fallback_models) or "なし"}
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

                        result = call_gemini_with_retry(
                            client,
                            page.parts + [build_page_prompt(page, n_files)],
                            primary_model=primary_model,
                            fallback_models=fallback_models,
                            page_number=page.page_number,
                            source_name=page.source_name,
                            on_retry=_on_gemini_retry,
                            on_model_start=_on_gemini_model_start,
                        )
                        successful_models.append(result.model_name)
                        if not result.text:
                            continue
                        try:
                            payload = parse_gemini_json_payload(result.text)
                            page_summaries, page_records = split_extraction_payload(
                                payload,
                                source_name=page.source_name,
                                page_number=page.page_number,
                            )
                            summary_sources.extend(page_summaries)
                            all_extracted_data.extend(page_records)
                        except json.JSONDecodeError:
                            st.warning(f"{page.source_name} {page.page_number}ページ目のAI応答をJSONとして読み取れませんでした。このページをスキップして続行します。")

                    if successful_models:
                        log_gemini_analysis_complete(successful_models, len(page_jobs))
                        st.toast(f"✅ {successful_models[-1]} で解析完了！", icon="🤖")

                    _cleanup_temp_paths(temp_paths)
                    progress_bar.progress(80, text="📊 データを整理しています...")

                    progress_bar.progress(100, text="🎉 解析完了！")
                    status_area.empty()
                    time.sleep(0.3)

                    if not all_extracted_data:
                        st.warning("読み取れたデータがありませんでした。ファイル形式（PDF・JPG・PNG）と内容をご確認ください。")
                    else:
                        all_extracted_data, vendor_assign_debug_rows = assign_unknown_vendors_to_pdf_vendor(all_extracted_data, summary_sources)
                        summary_data = normalize_summary_data(summary_sources)
                        detail_df, pdf_totals = build_intermediate_dataframe(all_extracted_data)
                        cost_df, vendor_summaries = build_cost_basis_dataframe(summary_data, detail_df)
                        df = cost_df
                        issues = validate_intermediate(cost_df, {})
                        has_blocking_issue = any(i.get("レベル") == "停止" for i in issues)

                        st.markdown('<div class="sub-header">PDFから抽出した集計対象プレビュー</div>', unsafe_allow_html=True)
                        st.caption("原価合計・上乗せ計算に使うのは、業者ごとのまとめ税抜小計だけです。明細は確認用として別シートに出します。")
                        st.dataframe(output_dataframe(cost_df), use_container_width=True, hide_index=True)

                        subtotal = int(round(pd.to_numeric(cost_df["原価金額"], errors="coerce").fillna(0).sum()))
                        detail_subtotal = int(round(pd.to_numeric(detail_df["原価金額"], errors="coerce").fillna(0).sum()))
                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric("集計対象", f"{len(cost_df)} 社")
                        c2.metric("原価合計", f"{subtotal:,} 円")
                        c3.metric("明細行数", f"{len(detail_df)} 行")
                        c4.metric("明細合計（確認用）", f"{detail_subtotal:,} 円")

                        if vendor_assign_debug_rows:
                            with st.expander("見積元の補正ログ", expanded=False):
                                st.dataframe(pd.DataFrame(vendor_assign_debug_rows), use_container_width=True, hide_index=True)

                        if issues:
                            st.warning("確認が必要な明細があります。出力前に内容を確認してください。")
                            st.dataframe(pd.DataFrame(issues), use_container_width=True)
                        if has_blocking_issue:
                            st.error("PDFの小計と抽出明細の合計が一致していないため、このまま出力すると危険です。PDFまたは抽出結果を確認してください。")

                        if profit_mode == "見積元（会社）ごとに金額を指定する":
                            st.session_state["_extracted_df"] = cost_df.copy()
                            st.session_state["_detail_df"] = detail_df.copy()
                            st.session_state["_vendor_summaries"] = vendor_summaries
                            st.session_state["_extracted_totals"] = pdf_totals
                            st.session_state["_extracted_issues"] = issues
                            st.session_state["_extracted_blocking"] = has_blocking_issue
                            st.session_state["_extracted_format_choice"] = format_choice
                            st.session_state["_summary_data"] = summary_data
                            categories_summary = []
                            for company, grp in cost_df.groupby('見積元', sort=False):
                                company_name = str(company) if pd.notna(company) and str(company).strip() != "" else "不明な会社"
                                item_total = int(pd.to_numeric(grp['原価金額'], errors="coerce").fillna(0).sum())
                                categories_summary.append({
                                    "name": company_name,
                                    "work_label": "業者まとめ",
                                    "total": item_total,
                                    "item_total": item_total,
                                    "declared_total": item_total,
                                    "base": item_total,
                                })
                            st.session_state["_categories_summary"] = categories_summary

                        else:
                            df = apply_profit(cost_df, profit_mode, profit_val)

                        # 「工事種別ごと」モード：ここでは抽出結果のみ表示し、残りは下の入力UIに任せる
                        if profit_mode == "見積元（会社）ごとに金額を指定する":
                            st.session_state["_extracted_df"] = cost_df.copy()
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

                        df_output = output_dataframe(df)
                        quote_summary_data = summary_data_from_cost_dataframe(summary_data, df)
                        df_quote_summary, quote_totals = build_quote_summary_dataframe(quote_summary_data, df)
                        df_work_summary, work_totals = build_vendor_work_summary_dataframe(vendor_summaries, df)
                        is_simple_format = format_choice == "彩架建設 簡易工事見積（1シート：明細のみ）"
                        if is_simple_format:
                            df_numbers_detail, detail_issues = simple_detail_dataframe(detail_df)
                            vendor_sheet_pairs = []
                        else:
                            df_numbers_detail, detail_issues = vendor_detail_dataframe(detail_df)
                            vendor_sheet_pairs = build_vendor_copy_sheets(vendor_summaries, detail_df, df)
                        if detail_issues:
                            issues.extend(detail_issues)
                        st.toast("計算完了！データ準備OK", icon="✅")
                        st.markdown('<div class="sub-header">計算完了！Numbers / Excel / CSV 向けデータ</div>', unsafe_allow_html=True)
                        st.markdown(_gold_sparkle_html(), unsafe_allow_html=True)
                        st.caption("画面確認用には見出しを表示しています。Excel / CSV / Numbers貼り付け用のダウンロードデータは、テンプレートにそのまま貼れるよう見出し行なしで出力します。")
                        if is_simple_format:
                            st.write("▼ 簡易工事見積：明細のみ（Numbers貼り付け用）")
                            st.dataframe(df_numbers_detail, use_container_width=True, hide_index=True)
                        else:
                            st.write("▼ 1枚目用：見積書")
                            st.dataframe(df_quote_summary, use_container_width=True, hide_index=True)
                            st.metric("見積書 合計金額", f"{quote_totals.get('工事費計', 0):,} 円")
                            st.write("▼ 2枚目用：工事別まとめ（各社の一式・経費・税計算）")
                            st.dataframe(df_work_summary, use_container_width=True, hide_index=True)
                            st.metric("工事別まとめ 合計金額", f"{work_totals.get('工事費計', 0):,} 円")
                            st.write("▼ 3枚目用：明細（Numbers「工事内容明細」にコピペ）")
                            st.dataframe(df_numbers_detail, use_container_width=True, hide_index=True)

                        if df_output["備考"].astype(str).str.strip().ne("").any():
                            st.info("一部の行は見積書から数値が正しく読み取れなかった、または検算差異があるため備考に表示しています。")

                        output = BytesIO()
                        with pd.ExcelWriter(output, engine='openpyxl') as writer:
                            if is_simple_format:
                                df_numbers_detail.to_excel(writer, index=False, header=False, sheet_name="簡易明細")
                            elif vendor_sheet_pairs:
                                for sheet_name, sheet_df in vendor_sheet_pairs:
                                    sheet_df.to_excel(writer, index=False, header=False, sheet_name=sheet_name)
                            else:
                                df_quote_summary.to_excel(writer, index=False, header=False, sheet_name=QUOTE_SHEET_NAME)
                                df_work_summary.to_excel(writer, index=False, header=False, sheet_name=WORK_SUMMARY_SHEET_NAME)
                                df_numbers_detail.to_excel(writer, index=False, header=False, sheet_name=DETAIL_SHEET_NAME)
                        excel_data = output.getvalue()
                        csv_data = df_numbers_detail.to_csv(index=False, header=False).encode("utf-8-sig")

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
                        if has_blocking_issue:
                            st.error("検算エラーが残っているため、Excel / CSV 出力は停止しています。")
                        else:
                            dcol1, dcol2 = st.columns(2)
                            with dcol1:
                                excel_clicked = st.download_button(
                                    label="📥 Excel をダウンロードする",
                                    data=excel_data,
                                    file_name="CYCA_smartLink_見積完成版.xlsx",
                                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                                )
                            with dcol2:
                                csv_clicked = st.download_button(
                                    label="📥 CSV をダウンロードする",
                                    data=csv_data,
                                    file_name="CYCA_smartLink_見積完成版.csv",
                                    mime="text/csv"
                                )
                            if excel_clicked or csv_clicked:
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
                except GeminiTemporaryUnavailable:
                    _cleanup_temp_paths(locals().get("temp_paths", []))
                    try:
                        status_area.empty()
                    except Exception:
                        pass
                    st.error(AI_RETRY_MESSAGE)
                    if st.button("再解析する", key="retry_ai_analysis_after_busy"):
                        st.session_state["_force_analyze"] = True
                        st.rerun()
                except Exception as e:
                    _cleanup_temp_paths(locals().get("temp_paths", []))
                    try:
                        err_msg = str(e)
                    except Exception:
                        err_msg = "（エラー内容を表示できませんでした）"
                    if is_gemini_temporary_error(e):
                        try:
                            status_area.empty()
                        except Exception:
                            pass
                        st.error(AI_RETRY_MESSAGE)
                        if st.button("再解析する", key="retry_ai_analysis_after_raw_busy"):
                            st.session_state["_force_analyze"] = True
                            st.rerun()
                    elif "ascii" in err_msg.lower() and "codec" in err_msg.lower():
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
            detail_df = st.session_state.get("_detail_df", pd.DataFrame())
            vendor_summaries = st.session_state.get("_vendor_summaries", [])
            summary_data = st.session_state.get("_summary_data", {})
            has_blocking_issue = bool(st.session_state.get("_extracted_blocking", False))
            detail_profit_df, df_profit = apply_company_profit_to_details(detail_df, df, cat_profits)
            df_output = output_dataframe(df_profit)
            quote_summary_data = summary_data_from_cost_dataframe(summary_data, df_profit)
            df_quote_summary, quote_totals = build_quote_summary_dataframe(quote_summary_data, df_profit)
            df_work_summary, work_totals = build_vendor_work_summary_dataframe(vendor_summaries, df_profit)
            format_choice = st.session_state.get("_extracted_format_choice", "")
            is_simple_format = format_choice == "彩架建設 簡易工事見積（1シート：明細のみ）"
            if is_simple_format:
                df_numbers_detail, detail_issues = simple_detail_dataframe(detail_profit_df)
                vendor_sheet_pairs = []
            else:
                df_numbers_detail, detail_issues = vendor_detail_dataframe(detail_profit_df)
                vendor_sheet_pairs = build_vendor_copy_sheets(vendor_summaries, detail_profit_df, df_profit)
            if detail_issues:
                has_blocking_issue = True
            st.toast("計算完了！", icon="✅")
            st.markdown(_gold_sparkle_html(), unsafe_allow_html=True)
            st.markdown('<div class="sub-header">計算完了！Numbers / Excel / CSV 向けデータ</div>', unsafe_allow_html=True)
            st.caption("画面確認用には見出しを表示しています。Excel / CSV / Numbers貼り付け用のダウンロードデータは、テンプレートにそのまま貼れるよう見出し行なしで出力します。")
            if is_simple_format:
                st.write("▼ 簡易工事見積：明細のみ（Numbers貼り付け用）")
                st.dataframe(df_numbers_detail, use_container_width=True, hide_index=True)
            else:
                st.write("▼ 1枚目用：見積書")
                st.dataframe(df_quote_summary, use_container_width=True, hide_index=True)
                st.metric("見積書 合計金額", f"{quote_totals.get('工事費計', 0):,} 円")
                st.write("▼ 2枚目用：工事別まとめ（各社の一式・経費・税計算）")
                st.dataframe(df_work_summary, use_container_width=True, hide_index=True)
                st.metric("工事別まとめ 合計金額", f"{work_totals.get('工事費計', 0):,} 円")
                st.write("▼ 3枚目用：明細（Numbers「工事内容明細」にコピペ）")
                st.dataframe(df_numbers_detail, use_container_width=True, hide_index=True)

                st.markdown("---")
                st.markdown("### テスト版：テンプレート流し込み")
                st.caption("※Excel互換のテスト専用テンプレートを使います。既存テンプレートは上書きしません。")
                try:
                    test_bytes, test_file_name, test_issues = build_template_fill_test_workbook(
                        detail_df=detail_profit_df,
                        cost_df=df_profit,
                        metadata=summary_data,
                    )
                    if test_issues:
                        st.warning("テンプレート流し込みテストの検証で確認事項があります。")
                        st.dataframe(pd.DataFrame({"確認事項": test_issues}), use_container_width=True, hide_index=True)
                    st.download_button(
                        label=f"{TEMPLATE_FILL_TEST_NAME} xlsx をダウンロード",
                        data=test_bytes,
                        file_name=test_file_name,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="download_template_fill_test_xlsx",
                    )
                except Exception as e:
                    st.error(f"テンプレート直接流し込みテストの生成に失敗しました: {e}")

            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                if is_simple_format:
                    df_numbers_detail.to_excel(writer, index=False, header=False, sheet_name="簡易明細")
                elif vendor_sheet_pairs:
                    for sheet_name, sheet_df in vendor_sheet_pairs:
                        sheet_df.to_excel(writer, index=False, header=False, sheet_name=sheet_name)
                else:
                    df_quote_summary.to_excel(writer, index=False, header=False, sheet_name=QUOTE_SHEET_NAME)
                    df_work_summary.to_excel(writer, index=False, header=False, sheet_name=WORK_SUMMARY_SHEET_NAME)
                    df_numbers_detail.to_excel(writer, index=False, header=False, sheet_name=DETAIL_SHEET_NAME)
            excel_data = output.getvalue()
            csv_data = df_numbers_detail.to_csv(index=False, header=False).encode("utf-8-sig")

            st.markdown("""
            <div class="download-done-box" style="position: relative; overflow: hidden;">
                <div class="msg">📥 ダウンロード → Numbers を開いて → データ部分をコピペ！</div>
                <div class="sparkle-bar"></div>
            </div>
            """, unsafe_allow_html=True)
            if has_blocking_issue:
                st.error("検算エラーが残っているため、Excel / CSV 出力は停止しています。")
            else:
                dcol1, dcol2 = st.columns(2)
                with dcol1:
                    excel_clicked = st.download_button(
                        label="📥 Excel をダウンロードする",
                        data=excel_data,
                        file_name="CYCA_smartLink_見積完成版.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="download_cat_profit_xlsx"
                    )
                with dcol2:
                    csv_clicked = st.download_button(
                        label="📥 CSV をダウンロードする",
                        data=csv_data,
                        file_name="CYCA_smartLink_見積完成版.csv",
                        mime="text/csv",
                        key="download_cat_profit_csv"
                    )
                if excel_clicked or csv_clicked:
                    st.toast("ダウンロード完了！Numbers にコピペして仕上げましょう 🚀", icon="🎉")
