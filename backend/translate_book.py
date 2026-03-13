#!/usr/bin/env python3
"""
translate_book.py — CLI script for GitHub Actions

Standalone pipeline that:
1. Downloads a PDF from a GitHub Release asset
2. Extracts text (PyMuPDF + Tesseract OCR)
3. Translates Bangla → English (Argos or Google)
4. Optionally refines with AI (GitHub Models / OpenAI)
5. Generates AsciiDoc, HTML, PDF outputs
6. Updates library/catalog.json
7. Saves outputs to library/{slug}/

Usage:
  python backend/translate_book.py \
    --slug my-book \
    --pdf-url "https://github.com/.../my-book.pdf" \
    --filename "my-book.pdf" \
    --mode offline \
    --refinement none \
    --model ""

Environment variables:
  GITHUB_TOKEN    — for downloading release assets and AI refinement via GitHub Models
  OPENAI_API_KEY  — for OpenAI refinement (optional)
"""

import argparse
import json
import logging
import os
import sys
import urllib.request
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


def download_pdf(url: str, dest: Path, token: str = "") -> None:
    """Download a PDF from a URL (supports GitHub release assets)."""
    logger.info(f"Downloading PDF from {url}")
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/octet-stream")

    with urllib.request.urlopen(req) as response:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            while chunk := response.read(8192):
                f.write(chunk)

    size_mb = dest.stat().st_size / 1024 / 1024
    logger.info(f"Downloaded: {dest} ({size_mb:.1f} MB)")


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
) -> None:
    """Add or update a book entry in the catalog."""
    catalog = load_catalog()
    now = datetime.now(timezone.utc).isoformat()

    # Find existing entry or create new one
    entry = next((b for b in catalog["books"] if b["slug"] == slug), None)

    if entry is None:
        entry = {
            "slug": slug,
            "title": filename.replace(".pdf", "")
            .replace("_", " ")
            .replace("-", " ")
            .title(),
            "status": status,
            "date_added": now,
        }
        catalog["books"].insert(0, entry)  # newest first
    else:
        entry["status"] = status

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


def main():
    parser = argparse.ArgumentParser(description="Translate a Bangla PDF to English")
    parser.add_argument(
        "--slug", required=True, help="Book slug (used as directory name)"
    )
    parser.add_argument("--pdf-url", required=True, help="URL to download the PDF")
    parser.add_argument("--filename", required=True, help="Original PDF filename")
    parser.add_argument(
        "--mode", default="offline", choices=["offline", "online", "hybrid"]
    )
    parser.add_argument(
        "--refinement", default="none", choices=["none", "openai", "github"]
    )
    parser.add_argument("--model", default="", help="AI model name override")
    args = parser.parse_args()

    slug = args.slug
    book_dir = LIBRARY_DIR / slug
    book_dir.mkdir(parents=True, exist_ok=True)

    # Download PDF
    pdf_path = book_dir / args.filename
    github_token = os.environ.get("GITHUB_TOKEN", "")

    if not pdf_path.exists():
        download_pdf(args.pdf_url, pdf_path, token=github_token)
    else:
        logger.info(f"PDF already exists: {pdf_path}")

    # Mark as processing in catalog
    update_catalog_entry(slug, args.filename, status="processing")

    try:
        # Step 1: Extract text
        print("\n=== Step 1: Extracting text ===")
        book = extract_text(str(pdf_path), on_progress=progress_callback)
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
            title=stem.replace("_", " ").replace("-", " ").title(),
            subtitle=f"Translated from Bangla ({book.total_pages} pages)",
        )

        adoc_content = generate_asciidoc(translated_pages, metadata)
        adoc_filename = f"{stem}_english.adoc"
        adoc_path = save_asciidoc(adoc_content, book_dir / adoc_filename)
        print(f"  AsciiDoc: {adoc_path}")

        html_filename = f"{stem}_english.html"
        html_path = asciidoc_to_html(adoc_path)
        print(f"  HTML: {html_path}")

        pdf_filename = f"{stem}_english.pdf"
        pdf_out_path = asciidoc_to_pdf(adoc_path)
        print(f"  PDF: {pdf_out_path}")

        # Update catalog with success
        files = {
            "original_pdf": args.filename,
            "translated_adoc": adoc_filename,
            "translated_html": html_filename,
            "translated_pdf": pdf_filename,
        }

        update_catalog_entry(
            slug=slug,
            filename=args.filename,
            status="translated",
            page_count=book.total_pages,
            translation_mode=args.mode,
            refinement_provider=args.refinement,
            refinement_model=args.model,
            files=files,
        )

        print(f"\n=== Done! Book '{slug}' translated successfully ===")
        print(f"  Output directory: {book_dir}")

    except Exception as e:
        logger.exception(f"Translation failed: {e}")
        update_catalog_entry(slug, args.filename, status="failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
