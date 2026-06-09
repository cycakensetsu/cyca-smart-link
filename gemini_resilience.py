import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from io import BytesIO
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from google.genai import types


LOGGER = logging.getLogger("saika_smart_link.gemini")
AI_RETRY_MESSAGE = "AI解析サーバーが混み合っています。30秒後に再試行してください。"
TRANSIENT_ERROR_CODES = {429, 500, 503, 504}
DEFAULT_PRIMARY_MODEL = "gemini-2.5-flash"
DEFAULT_FALLBACK_MODELS = "gemini-2.0-flash,gemini-1.5-flash,gemini-1.5-pro"


@dataclass
class GeminiPage:
    source_name: str
    page_number: int
    total_pages: int
    parts: List[types.Part]


@dataclass
class GeminiCallResult:
    text: str
    model_name: str
    retry_count: int


class GeminiTemporaryUnavailable(Exception):
    def __init__(self, message: str, *, page_number: Optional[int] = None, error_code: Optional[int] = None):
        super().__init__(message)
        self.page_number = page_number
        self.error_code = error_code


def configure_gemini_logging() -> None:
    if not LOGGER.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)


def _setting(name: str, default: str, secrets=None) -> str:
    value = (os.environ.get(name) or "").strip()
    if value:
        return value
    if secrets is not None:
        try:
            value = (getattr(secrets, name, None) or "").strip()
            if value:
                return value
        except Exception:
            pass
    return default


def get_configured_gemini_models(secrets=None) -> Tuple[str, List[str]]:
    primary = _setting("GEMINI_PRIMARY_MODEL", DEFAULT_PRIMARY_MODEL, secrets)
    fallback_raw = _setting("GEMINI_FALLBACK_MODELS", DEFAULT_FALLBACK_MODELS, secrets)
    fallback_models = [m.strip() for m in fallback_raw.split(",") if m.strip()]
    fallback_models = [m for m in fallback_models if m != primary]
    return primary, fallback_models


def _get_response_text(response) -> str:
    parts_text = []
    for candidate in (getattr(response, "candidates", None) or []):
        content = getattr(candidate, "content", None)
        if not content:
            continue
        for part in (getattr(content, "parts", None) or []):
            text = getattr(part, "text", None)
            if text:
                parts_text.append(text)
    if parts_text:
        return "".join(parts_text).strip()
    try:
        return (getattr(response, "text", None) or "").strip()
    except (UnicodeDecodeError, UnicodeEncodeError):
        return ""


def _extract_error_code(exc: Exception) -> Optional[int]:
    for attr in ("code", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    text = str(exc)
    match = re.search(r"\b(429|500|503|504)\b", text)
    if match:
        return int(match.group(1))
    if "RESOURCE_EXHAUSTED" in text:
        return 429
    if "UNAVAILABLE" in text:
        return 503
    return None


def _is_retryable(exc: Exception) -> bool:
    code = _extract_error_code(exc)
    if code in TRANSIENT_ERROR_CODES:
        return True
    lowered = str(exc).lower()
    return any(keyword in lowered for keyword in ("high demand", "temporarily unavailable", "rate limit"))


def is_gemini_temporary_error(exc: Exception) -> bool:
    """True when a Gemini/API exception should be shown as a temporary server busy state."""
    return _is_retryable(exc)


def call_gemini_with_retry(
    client,
    contents,
    *,
    primary_model: Optional[str] = None,
    fallback_models: Optional[Sequence[str]] = None,
    max_attempts_per_model: int = 4,
    backoff_seconds: Sequence[float] = (3, 8, 15, 30),
    page_number: Optional[int] = None,
    source_name: Optional[str] = None,
    on_retry: Optional[Callable[[str, int, float, Optional[int], Optional[int]], None]] = None,
    on_model_start: Optional[Callable[[str, Optional[int]], None]] = None,
) -> GeminiCallResult:
    primary = primary_model or DEFAULT_PRIMARY_MODEL
    models = [primary] + [m for m in (fallback_models or []) if m and m != primary]
    last_error: Optional[Exception] = None
    last_code: Optional[int] = None

    for model_name in models:
        if on_model_start:
            on_model_start(model_name, page_number)
        LOGGER.info("gemini_call_start model=%s page=%s source=%s", model_name, page_number, source_name)
        for attempt in range(1, max_attempts_per_model + 1):
            try:
                response = client.models.generate_content(model=model_name, contents=contents)
                text = _get_response_text(response)
                LOGGER.info(
                    "gemini_call_success model=%s retry_count=%s page=%s source=%s",
                    model_name,
                    attempt - 1,
                    page_number,
                    source_name,
                )
                return GeminiCallResult(text=text, model_name=model_name, retry_count=attempt - 1)
            except Exception as exc:
                last_error = exc
                last_code = _extract_error_code(exc)
                LOGGER.warning(
                    "gemini_call_error model=%s attempt=%s/%s error_code=%s page=%s source=%s error=%s",
                    model_name,
                    attempt,
                    max_attempts_per_model,
                    last_code,
                    page_number,
                    source_name,
                    exc,
                )
                if not _is_retryable(exc):
                    raise
                if attempt >= max_attempts_per_model:
                    LOGGER.warning(
                        "gemini_model_exhausted model=%s error_code=%s page=%s source=%s",
                        model_name,
                        last_code,
                        page_number,
                        source_name,
                    )
                    break
                base_delay = backoff_seconds[min(attempt - 1, len(backoff_seconds) - 1)]
                delay = base_delay + random.uniform(0.2, 1.5)
                if on_retry:
                    on_retry(model_name, attempt, delay, last_code, page_number)
                LOGGER.info(
                    "gemini_retry_wait model=%s attempt=%s delay=%.2f error_code=%s page=%s source=%s",
                    model_name,
                    attempt,
                    delay,
                    last_code,
                    page_number,
                    source_name,
                )
                time.sleep(delay)

    LOGGER.error(
        "gemini_call_failed final_error_code=%s failed_page=%s source=%s final_error=%s",
        last_code,
        page_number,
        source_name,
        last_error,
    )
    raise GeminiTemporaryUnavailable(AI_RETRY_MESSAGE, page_number=page_number, error_code=last_code)


def _jpeg_part_from_image(image, *, max_side: int = 1600, quality: int = 78) -> types.Part:
    from PIL import ImageOps

    image = ImageOps.exif_transpose(image).convert("RGB")
    width, height = image.size
    scale = min(1.0, max_side / max(width, height))
    if scale < 1.0:
        image = image.resize((int(width * scale), int(height * scale)))

    output = BytesIO()
    image.save(output, format="JPEG", quality=quality, optimize=True)
    return types.Part.from_bytes(data=output.getvalue(), mime_type="image/jpeg")


def _render_pdf_pages_as_images(path: str, source_name: str) -> List[GeminiPage]:
    import fitz
    from PIL import Image

    pages: List[GeminiPage] = []
    with fitz.open(path) as doc:
        total_pages = len(doc)
        for idx, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image = Image.open(BytesIO(pix.tobytes("png")))
            pages.append(
                GeminiPage(
                    source_name=source_name,
                    page_number=idx,
                    total_pages=total_pages,
                    parts=[_jpeg_part_from_image(image)],
                )
            )
    return pages


def prepare_upload_for_gemini_pages(path: str, source_name: str) -> List[GeminiPage]:
    ext = os.path.splitext(source_name or path)[1].lower()
    if ext == ".pdf":
        try:
            return _render_pdf_pages_as_images(path, source_name)
        except Exception as exc:
            LOGGER.error("pdf_preprocess_failed source=%s error=%s", source_name, exc)
            raise

    from PIL import Image

    with Image.open(path) as image:
        return [
            GeminiPage(
                source_name=source_name,
                page_number=1,
                total_pages=1,
                parts=[_jpeg_part_from_image(image)],
            )
        ]


def build_page_prompt(page: GeminiPage, n_files: int) -> str:
    simple_quote_rule = (
        "このファイルは1ページだけです。具体的な内訳がない簡易見積もりの場合のみ、"
        "「〇〇工事一式」の行を明細として抽出してください。"
        if page.total_pages == 1
        else "このファイルは複数ページです。表紙や総括ページの「〇〇工事一式」「〇〇工事費」は、具体的な明細ではないため抽出しないでください。"
    )
    return f"""
    あなたは建設見積書のOCR兼明細抽出AIです。
    添付画像は、アップロードされた{n_files}件のファイルのうち「{page.source_name}」の
    {page.page_number}/{page.total_pages}ページ目です。

    画像はスキャンPDFから変換された可能性があり、横向き・縦向き・90度回転・傾きがある可能性があります。
    文字の向きを自動判断して、表の全行を漏れなく読み取ってください。

    【ページ role 分類】
    まずこのページを次のいずれかに分類し、page_role に入れてください。
    - cover_summary_page: 「御見積書」「工事金額」「工事費内訳」「小計」「消費税」「工事費計」などがある表紙・まとめページ
    - summary_page: 工事費内訳、小計、端数調整、改小計、消費税、工事費計などの集計ページ
    - detail_page: 各工法、適用、数量、単位、単価、金額、備考などの明細ページ
    - unknown: 判定不能

    【このページだけを解析するルール】
    - cover_summary_page / summary_page では、summary_data を必ず抽出してください。
    - detail_page では、このページに存在する具体的な明細行を detail_data に入れてください。
    - 別ページにある行を推測で追加しないでください。
    - 小計・消費税・税込合計・総合計・内訳合計などの計算行は明細として抽出しないでください。
    - このページに税抜合計・消費税・税込合計が見える場合は、summary_data に入れてください。
    - {simple_quote_rule}

    【超厳格な仕分けルール】
    1. 「小計」「合計」「消費税」「税込」「見積金額」「内訳合計」「改小計」「総合計」などの計算結果や税金の行は絶対に抽出しないでください。
    2. 「諸経費」「法定福利費」「運搬費」「処分費」「荷揚げ費」「養生費」「安全対策費」などの経費項目は絶対に見落とさずに抽出してください。
    3. 「値引き」「出精値引き」「端数調整」「調整値引き」などの値引き項目も抽出し、単価と金額は必ずマイナス表記にしてください。
    4. 表の行は1行たりとも飛ばさないでください。部位ごと・階ごと・面ごとの小見出しの後にある明細行も個別に抽出してください。
    5. 見積元は、宛先ではなく見積書を作成した会社名です。このページで読めない場合は null で構いません。
    6. 工事種別は、防水工事、塗装工事、仮設足場工事などを明細ごとに判別してください。
    7. ```json などのマークダウン記号は含めず、純粋なJSONオブジェクトのみを返してください。

    【summary_data の抽出ルール】
    - 「御見積書」「工事金額」「工事費内訳」「小計」「端数調整」「端末調整」「改小計」「消費税」「工事費計」があるページを無視しないでください。
    - 宛名、工事名称、工事場所、支払条件、有効期限、見積担当が読める場合は必ず入れてください。
    - 工事費内訳のまとめは「工事項目」配列にしてください。
    - 例: 防水工事 11381700、諸経費 65000、厚生福利費 55000、小計 11501700、端数調整 -1700、改小計 11500000、消費税 1150000、工事費計 12650000。

    【明細の列分けルール】
    - 品名が複数行に分かれている場合は、1つの「品名」に結合してください。
    - 「665架㎡」「1,534架㎡」「10基」「1式」のように数量と単位が連結している場合は、数量と単位を分けてください。
    - 値引き・調整額などマイナス金額の明細も、通常の明細行として必ず残してください。

    【出力形式見本（キーを変えないこと）】
    {{
      "page_role": "cover_summary_page",
      "summary_data": {{
        "宛名": "",
        "工事名称": "屋上防水改修工事",
        "工事場所": "",
        "支払条件": "",
        "有効期限": "",
        "見積担当": "",
        "工事項目": [
          {{"工事項目": "防水工事", "金額": 11381700}},
          {{"工事項目": "諸経費", "金額": 65000}},
          {{"工事項目": "厚生福利費", "金額": 55000}}
        ],
        "小計": 11501700,
        "端数調整": -1700,
        "改小計": 11500000,
        "消費税": 1150000,
        "工事費計": 12650000
      }},
      "detail_data": [
        {{"No": 1, "見積元": "瀧上工業", "工事種別": "防水工事", "品名": "平場 ウレタン塗膜防水", "仕様": "X-1工法", "数量": 76.1, "単位": "㎡", "単価": 6400, "金額": 487040}}
      ]
    }}
    """


def parse_gemini_json_payload(raw_text: str):
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").replace("json\n", "", 1).strip()
    return json.loads(text)


def parse_gemini_json_array(raw_text: str) -> List[dict]:
    data = parse_gemini_json_payload(raw_text)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def log_gemini_analysis_complete(successful_models: Iterable[str], page_count: int) -> None:
    models = [model for model in successful_models if model]
    LOGGER.info(
        "gemini_analysis_complete final_success_model=%s page_count=%s models_used=%s",
        models[-1] if models else None,
        page_count,
        ",".join(models),
    )
