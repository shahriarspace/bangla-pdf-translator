"""
PDF Text Extraction Module

Handles both:
- Digital PDFs (selectable text) using PyMuPDF
- Scanned PDFs (image-based) using Tesseract OCR or AI Vision OCR

OCR backends:
  - tesseract: Local Tesseract OCR with Bangla language pack (offline, fast)
  - ai: Vision-capable AI model via OpenAI-compatible API (online, high quality)
        Ported from the PDF-to-Book project — uses the same prompts for
        consistent Bengali text extraction.

Reports progress via callback for the web UI.
"""

import base64
import io
import logging
import re
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable

import fitz  # PyMuPDF

import config

logger = logging.getLogger(__name__)


@dataclass
class PageContent:
    """Extracted content from a single PDF page."""

    page_number: int
    text: str
    is_ocr: bool = False


@dataclass
class BookContent:
    """Full extracted content of a book."""

    title: str
    pages: list[PageContent] = field(default_factory=list)
    total_pages: int = 0


def _is_page_scanned(page: fitz.Page, min_text_length: int = 30) -> bool:
    """Determine if a page is scanned (image-based) vs digital."""
    text = page.get_text("text").strip()
    has_images = len(page.get_images()) > 0
    return len(text) < min_text_length and has_images


# ── Tesseract OCR (offline) ───────────────────────────────────────────────


def _ocr_page_tesseract(page: fitz.Page, page_num: int) -> PageContent:
    """Extract text from a scanned page using Tesseract OCR."""
    import pytesseract
    from PIL import Image

    if config.TESSERACT_PATH:
        pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_PATH

    pix = page.get_pixmap(dpi=300)
    img_data = pix.tobytes("png")
    image = Image.open(io.BytesIO(img_data))

    text = pytesseract.image_to_string(image, lang="ben+eng")
    return PageContent(page_number=page_num, text=text.strip(), is_ocr=True)


# ── AI Vision OCR (online, high quality) ──────────────────────────────────

# Same prompt used by the PDF-to-Book project for consistent results
_AI_OCR_PROMPT = (
    "You are an expert OCR engine for Bengali (Bangla) script. "
    "Extract ALL the Bengali text from this image exactly as written. "
    "Preserve paragraph breaks with blank lines. "
    "Do NOT translate — output only the original Bengali text. "
    "Do NOT add any commentary, headers, or explanation. "
    "If the page has no readable text (blank page, only images/decorations), "
    "respond with exactly: NO_TEXT_CONTENT"
)

# Pattern to strip common AI boilerplate from output
_BOILERPLATE_PATTERNS = [
    r"^let me know",
    r"^i hope this",
    r"^feel free to",
    r"^if you need",
    r"^please let me",
    r"^is there anything",
    r"^do you want",
    r"^would you like",
    r"^---\s*$",
]


def _clean_ai_output(text: str) -> str:
    """Remove markdown fences and trailing boilerplate from AI output."""
    lines = text.rstrip().split("\n")

    # Strip leading/trailing markdown code fences
    if lines and re.match(r"^```\w*$", lines[0].strip()):
        lines.pop(0)
    if lines and lines[-1].strip() == "```":
        lines.pop()

    # Remove trailing boilerplate
    while lines:
        last = lines[-1].strip().lower()
        if not last:
            lines.pop()
            continue
        if any(re.match(pat, last) for pat in _BOILERPLATE_PATTERNS):
            lines.pop()
            continue
        break

    return "\n".join(lines).rstrip()


def _get_ai_client():
    """Create an OpenAI-compatible client based on AI_PROVIDER config."""
    from openai import OpenAI

    provider = config.AI_PROVIDER.lower()
    if provider == "github":
        if not config.GITHUB_TOKEN:
            raise ValueError(
                "GITHUB_TOKEN is required for AI provider 'github'. "
                "Set it in .env or environment variables."
            )
        return OpenAI(
            base_url=config.GITHUB_MODELS_URL,
            api_key=config.GITHUB_TOKEN,
        )
    elif provider == "openai":
        if not config.OPENAI_API_KEY:
            raise ValueError(
                "OPENAI_API_KEY is required for AI provider 'openai'. "
                "Set it in .env or environment variables."
            )
        return OpenAI(api_key=config.OPENAI_API_KEY)
    else:
        raise ValueError(f"Unknown AI_PROVIDER: {provider}. Use 'openai' or 'github'.")


def _ocr_page_ai(page: fitz.Page, page_num: int) -> PageContent:
    """Extract text from a page using an AI vision model.

    Renders the page to a high-DPI PNG, base64-encodes it, and sends it
    to a vision-capable model (GPT-4o, etc.) with the OCR prompt from
    the PDF-to-Book project.
    """
    client = _get_ai_client()
    model = config.AI_OCR_MODEL

    # Render page to PNG at 300 DPI
    pix = page.get_pixmap(dpi=300)
    img_bytes = pix.tobytes("png")
    b64_image = base64.b64encode(img_bytes).decode("utf-8")

    last_error = None
    for attempt in range(1, config.AI_MAX_RETRIES + 1):
        try:
            t0 = time.time()
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": _AI_OCR_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{b64_image}",
                                    "detail": "high",
                                },
                            },
                        ],
                    }
                ],
                max_tokens=4000,
                temperature=0.1,
            )
            elapsed = time.time() - t0
            content = response.choices[0].message.content or ""
            text = _clean_ai_output(content)

            if text == "NO_TEXT_CONTENT":
                logger.info(
                    f"  AI OCR page {page_num}: no text content ({elapsed:.1f}s)"
                )
                return PageContent(page_number=page_num, text="", is_ocr=True)

            logger.info(f"  AI OCR page {page_num}: {len(text)} chars ({elapsed:.1f}s)")
            return PageContent(page_number=page_num, text=text, is_ocr=True)

        except Exception as e:
            last_error = e
            logger.warning(
                f"  AI OCR page {page_num} attempt {attempt}/{config.AI_MAX_RETRIES} "
                f"failed: {e}"
            )
            if attempt < config.AI_MAX_RETRIES:
                delay = config.AI_RETRY_DELAY * attempt
                logger.info(f"  Retrying in {delay:.0f}s...")
                time.sleep(delay)

    logger.error(
        f"  AI OCR page {page_num} FAILED after {config.AI_MAX_RETRIES} attempts: "
        f"{last_error}"
    )
    return PageContent(
        page_number=page_num,
        text=f"[OCR FAILED: {last_error}]",
        is_ocr=True,
    )


# ── Main extraction entry point ───────────────────────────────────────────


def extract_text(
    pdf_path: str | Path,
    on_progress: Callable[[int, int, str], None] | None = None,
    ocr_engine: str | None = None,
) -> BookContent:
    """
    Extract text from a PDF file. Auto-detects scanned vs digital per page.

    Args:
        pdf_path: Path to the input Bangla PDF.
        on_progress: Optional callback(current_page, total_pages, status_msg).
        ocr_engine: Override OCR engine ("tesseract" or "ai").
                    Defaults to config.OCR_ENGINE.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    engine = (ocr_engine or config.OCR_ENGINE).lower()
    ocr_fn = _ocr_page_ai if engine == "ai" else _ocr_page_tesseract

    if engine == "ai":
        logger.info(
            f"Using AI OCR (provider: {config.AI_PROVIDER}, "
            f"model: {config.AI_OCR_MODEL})"
        )
    else:
        logger.info("Using Tesseract OCR (ben+eng)")

    doc = fitz.open(str(pdf_path))
    total_pages = len(doc)

    book = BookContent(title=pdf_path.stem, total_pages=total_pages)

    for page_num in range(total_pages):
        page = doc[page_num]

        if _is_page_scanned(page):
            status = f"OCR page {page_num + 1}/{total_pages} [{engine}]"
            if on_progress:
                on_progress(page_num + 1, total_pages, status)
            page_content = ocr_fn(page, page_num + 1)

            # Rate limit for AI OCR to avoid API throttling
            if engine == "ai" and page_num < total_pages - 1:
                time.sleep(config.AI_PAGE_DELAY)
        else:
            text = page.get_text("text").strip()
            page_content = PageContent(
                page_number=page_num + 1, text=text, is_ocr=False
            )
            status = f"Extracted page {page_num + 1}/{total_pages} ({len(text)} chars)"
            if on_progress:
                on_progress(page_num + 1, total_pages, status)

        book.pages.append(page_content)

    doc.close()
    return book
