import streamlit as st
from google import genai
import json
import hashlib
import uuid
import pandas as pd
import os
import math
import sys
import time
import tempfile
from io import BytesIO
import openpyxl  # ExcelWriter engine 用
from app_release import ensure_current_modules
ensure_current_modules()
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
import template_fill_test_v2 as tfv2
from horizontal_estimate_flow import build_horizontal_estimate
from estimate_review import REVIEW_MODE
from review_ui import preserve_uploads, capture_extraction, render_review, render_archive
from legacy_exports import queue_draft_export, render_draft_exports
from work_effects import cue, render as render_work_effects
from product_design import STYLE as PRODUCT_STYLE, loading_panel, ready_panel

HORIZONTAL_FORMAT = "彩架建設 横型見積書（Numbers対応・A4横）"

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
            "単価（円）": pd.to_numeric(source["見積単価"], errors="coerce"),
            "金額（円）": pd.to_numeric(source["見積金額"], errors="coerce"),
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
    from app_auth import load_users as configured_users
    try:
        settings = st.secrets.to_dict()
    except (FileNotFoundError, KeyError):
        settings = {}
    return configured_users(USERS_JSON, settings)


def check_login(username: str, password: str) -> bool:
    from app_auth import check_login as authenticate
    return authenticate(username, password, load_users())

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

st.markdown(PRODUCT_STYLE, unsafe_allow_html=True)

# ==========================================
# 🔐 ログイン画面
# ==========================================
from app_auth import configuration_signature, session_valid
import time as _auth_time
_login_users = load_users()
if not session_valid(st.session_state, _login_users):
    if st.session_state.get("logged_in"):
        st.session_state.clear()
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
    st.caption("更新 2026.10.22 / 見積の読み取り・確認を改善")
    st.subheader("🔐 ログイン")
    if not load_users():
        st.info("管理者によるログイン設定が必要です。設定完了後にご利用いただけます。")
    login_user = st.text_input("ユーザー名", placeholder="ユーザー名を入力")
    login_pass = st.text_input("パスワード", type="password", placeholder="パスワードを入力")
    if st.button("ログイン"):
        if check_login(login_user, login_pass):
            st.session_state.logged_in = True
            st.session_state['_auth_signature'] = configuration_signature(_login_users)
            st.session_state['_auth_started'] = _auth_time.time()
            st.session_state.username = login_user
            st.rerun()
        else:
            st.error("ユーザー名またはパスワードが正しくありません。")
    st.stop()

# ログアウト用・ログイン中ユーザー表示
if "username" not in st.session_state:
    st.session_state.username = ""
with st.sidebar:
    st.caption("更新 2026.10.22 / 見積の読み取り・確認を改善")
    from markup_rules import RULE_DESCRIPTION, RULE_VERSION
    with st.expander(f"配分ルール（{RULE_VERSION}）"):
        st.write(RULE_DESCRIPTION)
        st.caption("数量・単価・金額に欠損や不一致がある見積書は出力を止めます。")
    if st.session_state.username:
        st.caption(f"ログイン中: **{st.session_state.username}**")
    if st.button("🚪 ログアウト"):
        st.session_state.logged_in = False
        st.session_state.username = ""
        for business_key in list(st.session_state):
            if business_key not in ("logged_in", "username"):
                del st.session_state[business_key]
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
st.info("原本を確認 → 品目・数量を確認 → お客様に出す金額を決める → 仕上がりを見て出力")
st.caption("本サービスはベータ版です。計算結果は目安であり、正式な見積には必ずご自身で内容をご確認ください。重要な意思決定の唯一の根拠としてご利用にならないでください。")

# ==========================================
# 画面レイアウト（2カラムで見やすく）
# ==========================================
with st.expander("従来の利益設定・出力形式を使う", expanded=False):
    col1, col2 = st.columns(2)

    with col1:
        st.markdown('<div class="sub-header">💰 利益シミュレーション</div>', unsafe_allow_html=True)
        profit_mode = st.radio(
            "利益の上乗せ方法を選択してください",
            (REVIEW_MODE, "上乗せしない（原価そのまま）", "固定金額（円）を全体に割り振る", "パーセンテージ（%）で全体に乗せる", "見積元（会社）ごとに金額を指定する")
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
                HORIZONTAL_FORMAT,
                "彩架建設 企業用見積（2シート：一式表＋明細）",
                "彩架建設 簡易工事見積（1シート：明細のみ）",
                "汎用フォーマット（Excel）",
            ),
            disabled=profit_mode == REVIEW_MODE,
        )
        if profit_mode == REVIEW_MODE:
            st.caption("承認付き統合では、アプリ既存の横型流し込みテンプレートを使います。表紙・工事品目集計・明細を分け、明細は20行ごとに改ページします。")
        if format_choice == HORIZONTAL_FORMAT:
            st.caption("新しい見積書をアップロードして解析すると、表紙・一式まとめ・明細を印刷ページごとに分けたA4横型のNumbers対応ファイルを作成します。テストデータは使用しません。")

        elif format_choice == "彩架建設 企業用見積（2シート：一式表＋明細）":
            st.caption("Numbers の「工事内容」と「工事内容明細」にそのままコピペできる形式です。列順: No. / 工事品目 / 仕様 / 数量 / 単位 / 単価 / 金額 / 備考")
        elif format_choice == "彩架建設 簡易工事見積（1シート：明細のみ）":
            st.caption("1枚ペラの簡易見積用。Numbers にそのままコピペできます。列順: 商品名・工事名 / 数量 / 単 / 単価（円）/ 金額（円）")
        else:
            st.caption("工事品目・仕様・数量・単位・単価・金額・備考の汎用 Excel を出力します。")

st.markdown("---")
effects_slot = st.container()
st.markdown('<div class="sub-header">業者の見積書を入れる</div>', unsafe_allow_html=True)
st.caption("PDF・写真を選べます。複数の業者分もまとめて選べます（1ファイル20MBまで）。")
st.caption("見積を読み込んだ後は、マイクで今回の要望を伝えて相談できます。")

if '_upload_epoch' not in st.session_state:
    st.session_state['_upload_epoch'] = uuid.uuid4().hex
uploaded_files = st.file_uploader(
    "見積書のPDF・写真を選ぶ",
    type=["pdf", "jpg", "jpeg", "png"],
    accept_multiple_files=True,
    key='estimate_upload_' + st.session_state['_upload_epoch'],
)

# 新しいアップロードへ切り替わったら、前回の会社別入力と完成ファイルを破棄する。
upload_signature = tuple(
    (f.name, f.size, hashlib.sha256(f.getbuffer()).hexdigest()) for f in (uploaded_files or [])
)
if st.session_state.get("_upload_signature") != upload_signature:
    for key in list(st.session_state):
        if key.startswith(("_extracted_", "cat_profit_", "_review_", "review_", "_legacy_")) or key in (
            "_detail_df", "_vendor_summaries", "_summary_data", "_categories_summary",
            "_horizontal_result", "_horizontal_signature",
        ):
            del st.session_state[key]
    # 明示的に空文字を設定する。キー削除だけではブラウザーの旧widget値が復活する。
    st.session_state["output_customer"] = ""
    st.session_state["output_project"] = ""
    st.session_state["_upload_signature"] = upload_signature
    st.session_state.pop("_effects_source_checks", None)
    if uploaded_files:
        cue(st, "upload")


if format_choice == HORIZONTAL_FORMAT and profit_mode != REVIEW_MODE:
    st.text_input("宛名（出力用・敬称まで入力）", key="output_customer", placeholder="例：塚本 様")
    st.text_input("工事名称（出力用）", key="output_project", placeholder="空欄なら元見積書の工事名を使用")
    st.caption("空欄は読み取り結果を使います。変更後は上乗せを適用して作り直してください。印刷にはA4横PDFをご利用ください。")


def horizontal_signature(company_profits=()):
    return (upload_signature, profit_mode, float(profit_val), tuple(company_profits),
            st.session_state.get("output_customer", ""), st.session_state.get("output_project", ""))


def save_horizontal_result(detail, base_cost, quoted_cost, metadata, signature):
    try:
        with st.spinner("新しい見積書を横型テンプレートへ流し込み中..."):
            metadata = dict(metadata or {})
            if st.session_state.get("output_customer", "").strip():
                metadata["宛名"] = st.session_state["output_customer"].strip()
            if st.session_state.get("output_project", "").strip():
                metadata["工事名称"] = st.session_state["output_project"].strip()
            result = build_horizontal_estimate(detail, base_cost, quoted_cost, metadata)
        st.session_state["_horizontal_result"] = result
        st.session_state["_horizontal_signature"] = signature
    except Exception as exc:
        st.session_state.pop("_horizontal_result", None)
        st.error(f"横型見積書の生成に失敗しました: {exc}")

def legacy_export_signature():
    profits = tuple(sorted((str(k), float(v)) for k, v in st.session_state.items() if str(k).startswith("cat_profit_")))
    return (horizontal_signature(profits), format_choice)


def draft_download_button(*args, **kwargs):
    return queue_draft_export(st, legacy_export_signature(), *args, **kwargs)


# ==========================================
# 横型：Numbers / Excel互換テンプレート流し込み
# ==========================================
def render_template_fill_test_v2():
    st.markdown("---")
    with st.expander(f"📐 {tfv2.TEMPLATE_FILL_TEST_V2_NAME}", expanded=False):
        st.caption(
            "現在の彩架建設デザインを基調にしたA4横型の見積書です。"
            "表紙・明細・集計を横向きで揃え、NumbersとExcelのどちらでも崩れにくい形式で出力します。"
        )
        default = tfv2.default_test_estimate()
        m1, m2, m3 = st.columns(3)
        with m1:
            v_vendor = st.text_input("見積元", value=default.vendor, key="tfv2_vendor")
            v_customer = st.text_input("宛名", value=default.customer, key="tfv2_customer")
        with m2:
            v_project = st.text_input("工事名", value=default.project_name, key="tfv2_project")
            v_issue = st.text_input("発行日（見積日）", value=default.issue_date, key="tfv2_issue")
        with m3:
            v_valid = st.text_input("有効期限", value=default.valid_days, key="tfv2_valid")
            v_discount = st.number_input(
                "値引き（マイナスで入力）", value=int(default.discount), step=1, key="tfv2_discount"
            )

        st.caption("明細（工事分類ごとにまとまります。数量×単価＝金額 が一致している必要があります）")
        rows_df = pd.DataFrame(tfv2.estimate_to_rows(default), columns=tfv2.DETAIL_COLUMNS)
        edited = st.data_editor(
            rows_df,
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            key="tfv2_editor",
        )

        estimate = tfv2.rows_to_estimate(
            edited.to_dict("records"),
            vendor=v_vendor,
            customer=v_customer,
            project_name=v_project,
            issue_date=v_issue,
            valid_days=v_valid,
            discount=int(v_discount),
        )

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("小計", f"{estimate.subtotal:,}")
        c2.metric("値引き", f"{estimate.discount:,}")
        c3.metric("税抜合計", f"{estimate.net_total:,}")
        c4.metric("消費税", f"{estimate.tax:,}")
        c5.metric("税込合計", f"{estimate.grand_total:,}")

        if st.button("📐 横型見積書を作成する", key="tfv2_run"):
            try:
                result = tfv2.fill_estimate_v2(estimate)
            except Exception as e:  # noqa: BLE001
                st.error(f"出力に失敗しました: {e}")
                return
            if not result.ok:
                st.error("出力検証に失敗したため、ダウンロードは表示しません。以下を修正してください。")
                for msg in result.errors:
                    st.write(f"- {msg}")
                return
            st.success(f"検証OK！ シート構成: {result.sheet_names}")
            draft_download_button(
                label="✅ 横型見積書xlsxをダウンロード",
                data=result.data,
                file_name=result.file_name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="tfv2_download",
            )


# 上記は開発時の手入力確認用。固定のテストデータを本番画面へ表示しない。
# 本番の横型見積書は、解析・利益計算後の実データから下段で生成する。

if uploaded_files:
    analyze_label = "見積書を読み取る" if profit_mode == REVIEW_MODE else (
        "AIで見積を整理する" if profit_mode == REVIEW_MODE else
        "✨ 新しい見積書を解析してNumbers用に流し込む ✨"
        if format_choice == HORIZONTAL_FORMAT else "✨ AIでデータを解析 ＆ 利益計算を実行する ✨"
    )
    st.markdown(ready_panel([f.name for f in uploaded_files], analyze_label), unsafe_allow_html=True)
    analyze_clicked = st.button(analyze_label)
    horizontal_output_slot = st.empty()
    if st.session_state.pop("_force_analyze", False):
        analyze_clicked = True
    if analyze_clicked:
        for completed_key in ("_horizontal_result", "_legacy_exports", "_review_result", "_review_approval", "_review_original_rows"):
            st.session_state.pop(completed_key, None)
        if not MY_API_KEY:
            st.error("APIキーが設定されていません。環境変数 GOOGLE_API_KEY または .streamlit/secrets.toml に GOOGLE_API_KEY を設定してください。")
        else:
                try:
                    prepared_uploads = preserve_uploads(st, uploaded_files)
                    configure_gemini_logging()
                    all_extracted_data = []
                    summary_sources = []
                    analysis_errors = []
                    n_files = len(prepared_uploads)

                    progress_bar = st.progress(0, text="原本を読み取りに送る準備をしています…")
                    status_area = st.empty()

                    status_area.markdown(loading_panel(n_files), unsafe_allow_html=True)

                    page_jobs = []
                    temp_paths = []
                    source_paths = {}
                    for i, (source, uploaded_file) in enumerate(prepared_uploads):
                        if source.get('prepare_error'):
                            analysis_errors.append({'source':source['name'],'page':None,'reason':'prepare_failed'})
                            continue
                        progress_bar.progress(int((i / n_files) * 30), text=f"📄 PDF/画像をページ単位に準備中... ({i+1}/{n_files})")
                        file_extension = os.path.splitext(uploaded_file.name)[1].lower() or ".pdf"
                        if not file_extension.startswith("."):
                            file_extension = "." + file_extension
                        with tempfile.NamedTemporaryFile(prefix="cyca_upload_", suffix=file_extension, delete=False) as temp_upload:
                            temp_file_path = temp_upload.name
                        temp_paths.append(temp_file_path)

                        with open(temp_file_path, "wb") as f:
                            f.write(uploaded_file.getbuffer())

                        try:
                            jobs = prepare_upload_for_gemini_pages(temp_file_path, source['name'])
                        except Exception:
                            analysis_errors.append({'source':source['name'],'page':None,'reason':'prepare_failed'})
                            continue
                        for job in jobs:
                            source_paths[id(job)] = temp_file_path
                        page_jobs.extend(jobs)

                    progress_bar.progress(30, text=f"🔍 AI が{len(page_jobs)}件を解析中...")

                    status_area.markdown(loading_panel(n_files), unsafe_allow_html=True)

                    primary_model, fallback_models = get_configured_gemini_models(st.secrets)
                    successful_models = []
                    from extraction_batch import extract_batch
                    finished = 0
                    timing_rows = []
                    for event in extract_batch(page_jobs, source_paths, n_files,
                            client_factory=lambda: genai.Client(api_key=MY_API_KEY, http_options={"timeout":90000}),
                            primary_model=primary_model, fallback_models=fallback_models,
                            cache=st.session_state.setdefault('_extraction_cache', {})):
                        kind = event['kind']
                        if kind == 'done':
                            finished += 1
                            timing_rows.append({k:event[k] for k in ('index','cached','seconds')})
                            progress_bar.progress(30 + int(finished / max(len(page_jobs),1) * 55),
                                text=f"{finished} / {len(page_jobs)}ページの読み取りが終わりました")
                        elif kind == 'reading':
                            status_area.markdown(loading_panel(n_files), unsafe_allow_html=True)
                            status_label = event['source']
                            if event.get('page'): status_label += f"（{event['page']}ページ）"
                            st.session_state['_reading_status'] = status_label
                            progress_bar.progress(30 + int(finished / max(len(page_jobs),1) * 55),
                                text=f"{finished} / {len(page_jobs)}ページ完了 ｜ 読み取り中：{status_label}")
                        elif kind == 'retry':
                            status_area.info(f"「{event['source']}」を再確認しています。他の見積の読み取りも進めています。")
                        elif kind == 'result':
                            for summaries, records, models, errors in event['results']:
                                summary_sources.extend(summaries)
                                all_extracted_data.extend(records)
                                successful_models.extend(models)
                                analysis_errors.extend(errors)
                    st.session_state['_extraction_timings'] = timing_rows

                    from extraction_batch import reconcile_document_results
                    all_extracted_data,summary_sources=reconcile_document_results(
                        st.session_state['_review_sources'],all_extracted_data,summary_sources)
                    if successful_models:
                        log_gemini_analysis_complete(successful_models, len(page_jobs))


                    _cleanup_temp_paths(temp_paths)
                    progress_bar.progress(80, text="📊 データを整理しています...")

                    progress_bar.progress(90, text="原本と品目の対応・金額を確認しています…")
                    status_area.empty()

                    if not all_extracted_data:
                        from extraction_batch import extraction_notice
                        st.info(extraction_notice(analysis_errors))
                    else:
                        all_extracted_data, vendor_assign_debug_rows = assign_unknown_vendors_to_pdf_vendor(all_extracted_data, summary_sources)
                        summary_data = normalize_summary_data(summary_sources)
                        detail_df, pdf_totals = build_intermediate_dataframe(all_extracted_data)
                        cost_df, vendor_summaries = build_cost_basis_dataframe(summary_data, detail_df)
                        capture_extraction(st, detail_df, summary_data, all_extracted_data, analysis_errors)
                        progress_bar.progress(100, text="読み取り結果を下に表示しました。")
                        if analysis_errors:
                            from extraction_batch import extraction_notice
                            st.info(extraction_notice(analysis_errors, empty=False))
                        else:
                            st.toast("読み取り結果を下に表示しました。", icon=":material/check_circle:")
                        cue(st, "read")
                        if profit_mode == REVIEW_MODE:
                            raise _CatInputNeeded()
                        df = cost_df
                        issues = validate_intermediate(cost_df, {}) + validate_intermediate(detail_df, {})
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
                            df = apply_profit(cost_df, profit_mode, profit_val, detail_df=detail_df)
                            profits = df.groupby("見積元", sort=False)["上乗せ額"].sum().to_dict()
                            detail_df, df = apply_company_profit_to_details(detail_df, cost_df, profits)

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
                        if format_choice == HORIZONTAL_FORMAT and not has_blocking_issue and not detail_issues:
                            save_horizontal_result(
                                detail_df, cost_df, df, summary_data, horizontal_signature()
                            )
                        st.toast("計算完了！データ準備OK", icon="✅")
                        result_heading = "解析結果の確認" if format_choice == HORIZONTAL_FORMAT else "計算完了！Numbers / Excel / CSV 向けデータ"
                        st.markdown(f'<div class="sub-header">{result_heading}</div>', unsafe_allow_html=True)
                        st.markdown(_gold_sparkle_html(), unsafe_allow_html=True)
                        if format_choice != HORIZONTAL_FORMAT:
                            st.caption("画面確認用には見出しを表示しています。Excel / CSV / Numbers貼り付け用のダウンロードデータは、テンプレートにそのまま貼れるよう見出し行なしで出力します。数式は含まれません。")
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

                        if format_choice == HORIZONTAL_FORMAT:
                            st.info("数式付きの横型ファイルは、アップロード欄の下にある専用ボタンからダウンロードしてください。画面の表をコピーすると数式は移りません。")
                        else:
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
                        elif format_choice == HORIZONTAL_FORMAT:
                            pass  # 横型モードでは、数式のない貼り付け用ファイルを表示しない。
                        else:
                            dcol1, dcol2 = st.columns(2)
                            with dcol1:
                                excel_clicked = draft_download_button(
                                    label="📥 Excel（貼り付け用・数値のみ）",
                                    data=excel_data,
                                    file_name="CYCA_smartLink_見積完成版.xlsx",
                                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                                )
                            with dcol2:
                                csv_clicked = draft_download_button(
                                    label="📥 CSV（貼り付け用・数値のみ）",
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

        from markup_rules import RULE_DESCRIPTION
        st.caption(RULE_DESCRIPTION)
        apply_label = (
            "✨ 上乗せを適用してNumbers用に流し込む ✨"
            if format_choice == HORIZONTAL_FORMAT else "✨ 上乗せを適用して出力する ✨"
        )
        if st.button(apply_label, key="apply_cat_profit"):
            st.session_state.pop("_horizontal_result", None)
            df = st.session_state["_extracted_df"].copy()
            detail_df = st.session_state.get("_detail_df", pd.DataFrame())
            vendor_summaries = st.session_state.get("_vendor_summaries", [])
            summary_data = st.session_state.get("_summary_data", {})
            has_blocking_issue = bool(st.session_state.get("_extracted_blocking", False))
            if has_blocking_issue:
                st.error("読み取り明細の不一致を解消してから出力してください。")
                st.stop()
            try:
                detail_profit_df, df_profit = apply_company_profit_to_details(detail_df, df, cat_profits)
            except ValueError as exc:
                st.error(str(exc))
                st.stop()
            df_output = output_dataframe(df_profit)
            quote_summary_data = summary_data_from_cost_dataframe(summary_data, df_profit)
            df_quote_summary, quote_totals = build_quote_summary_dataframe(quote_summary_data, df_profit)
            df_work_summary, work_totals = build_vendor_work_summary_dataframe(vendor_summaries, df_profit)
            is_simple_format = format_choice == "彩架建設 簡易工事見積（1シート：明細のみ）"
            if is_simple_format:
                df_numbers_detail, detail_issues = simple_detail_dataframe(detail_profit_df)
                vendor_sheet_pairs = []
            else:
                df_numbers_detail, detail_issues = vendor_detail_dataframe(detail_profit_df)
                vendor_sheet_pairs = build_vendor_copy_sheets(vendor_summaries, detail_profit_df, df_profit)
            if detail_issues:
                has_blocking_issue = True
            if format_choice == HORIZONTAL_FORMAT and not has_blocking_issue:
                save_horizontal_result(
                    detail_df, df, df_profit, summary_data,
                    horizontal_signature(sorted(cat_profits.items())),
                )
            st.toast("計算完了！", icon="✅")
            st.markdown(_gold_sparkle_html(), unsafe_allow_html=True)
            result_heading = "解析結果の確認" if format_choice == HORIZONTAL_FORMAT else "計算完了！Numbers / Excel / CSV 向けデータ"
            st.markdown(f'<div class="sub-header">{result_heading}</div>', unsafe_allow_html=True)
            if format_choice != HORIZONTAL_FORMAT:
                st.caption("画面確認用には見出しを表示しています。Excel / CSV / Numbers貼り付け用のダウンロードデータは、テンプレートにそのまま貼れるよう見出し行なしで出力します。数式は含まれません。")
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

            if format_choice == HORIZONTAL_FORMAT:
                st.info("数式付きの横型ファイルは、アップロード欄の下にある専用ボタンからダウンロードしてください。画面の表をコピーすると数式は移りません。")
            else:
                st.markdown("""
            <div class="download-done-box" style="position: relative; overflow: hidden;">
                <div class="msg">📥 ダウンロード → Numbers を開いて → データ部分をコピペ！</div>
                <div class="sparkle-bar"></div>
            </div>
                """, unsafe_allow_html=True)
            if has_blocking_issue:
                st.error("検算エラーが残っているため、Excel / CSV 出力は停止しています。")
            elif format_choice == HORIZONTAL_FORMAT:
                pass  # 横型モードでは、数式のない貼り付け用ファイルを表示しない。
            else:
                dcol1, dcol2 = st.columns(2)
                with dcol1:
                    excel_clicked = draft_download_button(
                        label="📥 Excel（貼り付け用・数値のみ）",
                        data=excel_data,
                        file_name="CYCA_smartLink_見積完成版.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="download_cat_profit_xlsx"
                    )
                with dcol2:
                    csv_clicked = draft_download_button(
                        label="📥 CSV（貼り付け用・数値のみ）",
                        data=csv_data,
                        file_name="CYCA_smartLink_見積完成版.csv",
                        mime="text/csv",
                        key="download_cat_profit_csv"
                    )
                if excel_clicked or csv_clicked:
                    st.toast("ダウンロード完了！Numbers にコピペして仕上げましょう 🚀", icon="🎉")

if uploaded_files and format_choice == HORIZONTAL_FORMAT:
    current_profits = ()
    if profit_mode == "見積元（会社）ごとに金額を指定する":
        current_profits = sorted(
            (c["name"], st.session_state.get(f"cat_profit_{c['name']}", 0))
            for c in st.session_state.get("_categories_summary", [])
        )
    result = st.session_state.get("_horizontal_result")
    if result is not None and st.session_state.get("_horizontal_signature") == horizontal_signature(current_profits):
        with horizontal_output_slot.container():
            st.markdown("### 📐 数式付き・新規見積書")
            st.caption("このxlsxをNumbersで直接開き、数量や単価を編集してください。明細金額・一式小計・表題金額が連動します。表やCSVをコピーすると数式は移りません。")
            if result.warnings:
                with st.expander("流し込みの確認事項", expanded=False):
                    for warning in result.warnings:
                        st.write(f"- {warning}")
            if not result.ok:
                st.error("出力検証に失敗しました。ダウンロードは停止しています。")
                for error in result.errors:
                    st.write(f"- {error}")
            else:
                draft_download_button(
                    "📥 数式付き見積書をNumbersで開く（A4横）",
                    data=result.data,
                    file_name=result.file_name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="download_new_horizontal_estimate",
                )
                if result.pdf_data:
                    draft_download_button(
                        "📄 印刷用PDFをダウンロード（A4横・ページ固定）",
                        data=result.pdf_data,
                        file_name=result.file_name.removesuffix(".xlsx") + ".pdf",
                        mime="application/pdf",
                        key="download_new_horizontal_pdf",
                    )


if profit_mode == REVIEW_MODE:
    render_review(st, api_key=MY_API_KEY)

if profit_mode != REVIEW_MODE:
    render_draft_exports(st, legacy_export_signature())

# Stable component position preserves the user-activated audio context across reruns.
render_archive(st)
with effects_slot:
    render_work_effects(st)
