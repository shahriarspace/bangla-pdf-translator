#!/usr/bin/env python3
"""
translate_book.py — CLI script for GitHub Actions

Standalone pipeline that:
1. Copies a PDF from the uploads/ directory (committed via Contents API)
2. Extracts text (PyMuPDF + Tesseract OCR or AI Vision)
3. Translates Bangla → English (Argos, Google, or AI literary)
4. Optionally refines with AI (GitHub Models / OpenAI)
5. Generates AsciiDoc, HTML, PDF outputs
6. Updates library/catalog.json
7. Saves outputs to library/{slug}/

Usage:
  python backend/translate_book.py \
    --slug my-book \
    --pdf-path "uploads/my-book/my-book.pdf" \
    --filename "my-book.pdf" \
    --mode offline \
    --refinement none \
    --model ""

Environment variables:
  GITHUB_TOKEN    — for AI refinement via GitHub Models
  OPENAI_API_KEY  — for OpenAI refinement (optional)
"""

import argparse
import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add backend to path so we can import src modules
BACKEND_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BACKEND_DIR))

# Patch config to work outside Docker
os.environ.setdefault("TESSERACT_PATH", "/usr/bin/tesseract")

import config
from src.extractor import extract_text
from src.translator import translate_pages
from src.generator import (
    generate_asciidoc,
    save_asciidoc,
    asciidoc_to_html,
    asciidoc_to_pdf,
    BookMetadata,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("translate_book")

# Project root (one level up from backend/)
PROJECT_ROOT = BACKEND_DIR.parent
LIBRARY_DIR = PROJECT_ROOT / "library"
CATALOG_PATH = LIBRARY_DIR / "catalog.json"


def load_catalog() -> dict:
    """Load the library catalog."""
    if CATALOG_PATH.exists():
        return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return {"books": []}


def save_catalog(catalog: dict) -> None:
    """Save the library catalog."""
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    logger.info(f"Catalog saved: {CATALOG_PATH}")


def update_catalog_entry(
    slug: str,
    filename: str,
    status: str,
    page_count: int = 0,
    translation_mode: str = "",
    refinement_provider: str = "",
    refinement_model: str = "",
    files: dict | None = None,
    title: str = "",
    author: str = "",
) -> None:
    """Add or update a book entry in the catalog."""
    catalog = load_catalog()
    now = datetime.now(timezone.utc).isoformat()

    # Find existing entry or create new one
    entry = next((b for b in catalog["books"] if b["slug"] == slug), None)

    # Determine display title: explicit > PDF metadata > filename fallback
    display_title = title or (
        filename.replace(".pdf", "").replace("_", " ").replace("-", " ").title()
    )

    if entry is None:
        entry = {
            "slug": slug,
            "title": display_title,
            "status": status,
            "date_added": now,
        }
        catalog["books"].insert(0, entry)  # newest first
    else:
        entry["status"] = status
        # Update title if we have a better one
        if title:
            entry["title"] = title

    if author:
        entry["author"] = author
    if page_count:
        entry["page_count"] = page_count
    if translation_mode:
        entry["translation_mode"] = translation_mode
    if refinement_provider and refinement_provider != "none":
        entry["refinement_provider"] = refinement_provider
        entry["refinement_model"] = refinement_model
    if status == "translated":
        entry["date_translated"] = now
    if files:
        entry["files"] = files

    save_catalog(catalog)


def progress_callback(current: int, total: int, msg: str) -> None:
    """Print progress to stdout for GitHub Actions logs."""
    pct = int(current / total * 100) if total else 0
    print(f"  [{pct:3d}%] {msg}")


def extract_pdf_metadata(pdf_path: Path) -> dict:
    """Extract title and author from PDF metadata fields."""
    metadata = {"title": "", "author": ""}
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(pdf_path))
        pdf_meta = doc.metadata or {}
        doc.close()

        title = (pdf_meta.get("title") or "").strip()
        author = (pdf_meta.get("author") or "").strip()

        # Only use if they look real (not empty, not a filepath, not 'untitled')
        if title and len(title) > 1 and not title.lower().startswith("untitled"):
            metadata["title"] = title
        if author and len(author) > 1:
            metadata["author"] = author

        logger.info(f"PDF metadata — title: {title!r}, author: {author!r}")
    except Exception as e:
        logger.warning(f"Could not read PDF metadata: {e}")

    return metadata


def main():
    parser = argparse.ArgumentParser(description="Translate a Bangla PDF to English")
    parser.add_argument(
        "--slug", required=True, help="Book slug (used as directory name)"
    )
    parser.add_argument(
        "--pdf-path",
        required=True,
        help="Path to the PDF in the repo (e.g. uploads/my-book/file.pdf)",
    )
    parser.add_argument("--filename", required=True, help="Original PDF filename")
    parser.add_argument(
        "--mode", default="offline", choices=["offline", "online", "hybrid", "ai"]
    )
    parser.add_argument(
        "--ocr-engine",
        default="tesseract",
        choices=["tesseract", "ai"],
        help="OCR engine: tesseract (offline) or ai (vision model)",
    )
    parser.add_argument(
        "--refinement", default="none", choices=["none", "openai", "github"]
    )
    parser.add_argument("--model", default="", help="AI model name override")
    parser.add_argument(
        "--book-title",
        default="",
        help="Book title (optional, extracted from PDF if not provided)",
    )
    parser.add_argument(
        "--book-author",
        default="",
        help="Book author (optional, extracted from PDF if not provided)",
    )
    args = parser.parse_args()

    slug = args.slug
    book_dir = LIBRARY_DIR / slug
    book_dir.mkdir(parents=True, exist_ok=True)

    # Copy PDF from uploads/ to library/{slug}/
    pdf_path = book_dir / args.filename
    source_pdf = PROJECT_ROOT / args.pdf_path

    if not pdf_path.exists():
        if not source_pdf.exists():
            logger.error(f"PDF not found at {source_pdf}")
            sys.exit(1)
        shutil.copy2(str(source_pdf), str(pdf_path))
        logger.info(f"Copied PDF: {source_pdf} -> {pdf_path}")
    else:
        logger.info(f"PDF already exists: {pdf_path}")

    # Extract metadata from PDF (title, author)
    pdf_meta = extract_pdf_metadata(pdf_path)
    book_title = (
        args.book_title
        or pdf_meta["title"]
        or (
            args.filename.replace(".pdf", "")
            .replace("_", " ")
            .replace("-", " ")
            .title()
        )
    )
    book_author = args.book_author or pdf_meta["author"] or ""
    print(f"  Book title: {book_title}")
    if book_author:
        print(f"  Book author: {book_author}")

    # Mark as processing in catalog
    update_catalog_entry(
        slug, args.filename, status="processing", title=book_title, author=book_author
    )

    try:
        # Step 1: Extract text
        print("\n=== Step 1: Extracting text ===")
        book = extract_text(
            str(pdf_path),
            on_progress=progress_callback,
            ocr_engine=args.ocr_engine,
        )
        print(f"  Extracted {book.total_pages} pages")

        # Step 2: Translate
        print(f"\n=== Step 2: Translating ({args.mode}) ===")
        pages = [{"page_number": p.page_number, "text": p.text} for p in book.pages]

        results = translate_pages(
            pages,
            translation_mode=args.mode,
            refinement_provider=args.refinement,
            refinement_model=args.model,
            on_progress=progress_callback,
        )

        # Step 3: Generate outputs
        print("\n=== Step 3: Generating outputs ===")
        translated_pages = [
            {"page_number": r.page_number, "text": r.final_text} for r in results
        ]

        stem = Path(args.filename).stem
        metadata = BookMetadata(
            title=book_title,
            author=book_author or "Translated from Bangla",
            subtitle=f"Translated from Bangla ({book.total_pages} pages)",
        )

        adoc_content = generate_asciidoc(translated_pages, metadata)
        adoc_filename = f"{stem}_english.adoc"
        adoc_path = save_asciidoc(adoc_content, book_dir / adoc_filename)
        print(f"  AsciiDoc: {adoc_path}")

        html_filename = f"{stem}_english.html"
        html_path = asciidoc_to_html(adoc_path)
        if html_path and html_path.exists():
            print(f"  HTML: {html_path}")
        else:
            html_filename = ""
            logger.warning("HTML generation failed — skipping")

        pdf_filename = f"{stem}_english.pdf"
        pdf_out_path = asciidoc_to_pdf(adoc_path)
        if pdf_out_path and pdf_out_path.exists():
            print(f"  PDF: {pdf_out_path}")
        else:
            pdf_filename = ""
            logger.warning("PDF generation failed — skipping")

        # Update catalog with success — only include files that were actually generated
        files = {
            "original_pdf": args.filename,
            "translated_adoc": adoc_filename,
        }
        if html_filename:
            files["translated_html"] = html_filename
        if pdf_filename:
            files["translated_pdf"] = pdf_filename

        update_catalog_entry(
            slug=slug,
            filename=args.filename,
            status="translated",
            page_count=book.total_pages,
            translation_mode=args.mode,
            refinement_provider=args.refinement,
            refinement_model=args.model,
            files=files,
            title=book_title,
            author=book_author,
        )

        print(f"\n=== Done! Book '{slug}' translated successfully ===")
        print(f"  Output directory: {book_dir}")

    except Exception as e:
        logger.exception(f"Translation failed: {e}")
        update_catalog_entry(slug, args.filename, status="failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
