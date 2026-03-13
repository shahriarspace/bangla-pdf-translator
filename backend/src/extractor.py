"""
PDF Text Extraction Module

Handles both:
- Digital PDFs (selectable text) using PyMuPDF
- Scanned PDFs (image-based) using Tesseract OCR

Automatically detects which method to use per page.
Reports progress via callback for the web UI.
"""

import logging
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


def _ocr_page_tesseract(page: fitz.Page, page_num: int) -> PageContent:
    """Extract text from a scanned page using Tesseract OCR."""
    import pytesseract
    from PIL import Image
    import io

    if config.TESSERACT_PATH:
        pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_PATH

    pix = page.get_pixmap(dpi=300)
    img_data = pix.tobytes("png")
    image = Image.open(io.BytesIO(img_data))

    text = pytesseract.image_to_string(image, lang="ben+eng")
    return PageContent(page_number=page_num, text=text.strip(), is_ocr=True)


def extract_text(
    pdf_path: str | Path,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> BookContent:
    """
    Extract text from a PDF file. Auto-detects scanned vs digital per page.

    Args:
        pdf_path: Path to the input Bangla PDF.
        on_progress: Optional callback(current_page, total_pages, status_msg).
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = fitz.open(str(pdf_path))
    total_pages = len(doc)

    book = BookContent(title=pdf_path.stem, total_pages=total_pages)

    for page_num in range(total_pages):
        page = doc[page_num]

        if _is_page_scanned(page):
            status = f"OCR page {page_num + 1}/{total_pages}"
            if on_progress:
                on_progress(page_num + 1, total_pages, status)
            page_content = _ocr_page_tesseract(page, page_num + 1)
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
