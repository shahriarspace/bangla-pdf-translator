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
import re
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
    BilingualMetadata,
    generate_bilingual_json,
    save_bilingual_json,
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
    title_bangla: str = "",
    author_bangla: str = "",
    category: str = "",
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
    if title_bangla:
        entry["title_bangla"] = title_bangla
    if author_bangla:
        entry["author_bangla"] = author_bangla
    if category:
        entry["category"] = category
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


def _has_bangla_unicode(text: str) -> bool:
    """Check if text contains any Bangla Unicode characters (U+0980–U+09FF)."""
    if not text:
        return False
    return any("\u0980" <= c <= "\u09ff" for c in text)


def _is_plausible_english(text: str) -> bool:
    """Check if text looks like plausible English/Latin text (not mojibake).

    Mojibake from legacy Bangla fonts has unusual patterns: high density of
    uppercase letters, special chars (^, |, `, ©, ¼), and non-word sequences.
    Real English book titles are mostly lowercase words with normal punctuation.
    """
    if not text:
        return False
    # Count characters that are normal in English titles
    normal = sum(1 for c in text if c.isalpha() or c in " '-:.,!?&0123456789")
    # If more than 80% are normal English chars, it's probably real English
    return len(text) > 0 and (normal / len(text)) > 0.80


def extract_pdf_metadata(pdf_path: Path) -> dict:
    """
    Extract title and author from a PDF using a three-level fallback chain:
      1. PDF embedded metadata fields (fast, works if publisher set them)
      2. PyMuPDF text extraction with font-size heuristics on first pages
         (works for text-based PDFs — largest font = title, second = author)
      3. Tesseract OCR on the cover page (works for scanned PDFs)

    At each level, results are validated for Bangla Unicode content.
    Legacy fonts (SutonnyMJ, BanglaWord) produce mojibake (Latin chars
    instead of Bengali), which is detected and skipped.
    """
    metadata = {"title": "", "author": ""}

    # --- Level 1: PDF embedded metadata ---
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(pdf_path))
        pdf_meta = doc.metadata or {}

        title = (pdf_meta.get("title") or "").strip()
        author = (pdf_meta.get("author") or "").strip()

        # Only use if they look real (not empty, not a filepath, not 'untitled')
        if title and len(title) > 1 and not title.lower().startswith("untitled"):
            # Check for mojibake — metadata from legacy-font PDFs can also be garbled
            if _has_bangla_unicode(title) or _is_plausible_english(title):
                metadata["title"] = title
            else:
                logger.info(f"Level 1 title looks like mojibake, skipping: {title!r}")
        if author and len(author) > 1:
            if _has_bangla_unicode(author) or _is_plausible_english(author):
                metadata["author"] = author
            else:
                logger.info(f"Level 1 author looks like mojibake, skipping: {author!r}")

        logger.info(f"PDF metadata (level 1) — title: {title!r}, author: {author!r}")

        # --- Level 2: Font-size heuristics on first pages ---
        if not metadata["title"]:
            logger.info(
                "Level 1 metadata empty, trying font-size heuristics (level 2)..."
            )
            l2 = _extract_metadata_from_text(doc)
            if l2["title"]:
                # Validate: reject if it looks like mojibake from legacy fonts
                if _has_bangla_unicode(l2["title"]) or _is_plausible_english(
                    l2["title"]
                ):
                    metadata["title"] = l2["title"]
                    logger.info(f"Level 2 found title: {l2['title']!r}")
                else:
                    logger.info(
                        f"Level 2 title looks like mojibake (legacy font), "
                        f"skipping: {l2['title']!r}"
                    )
            if l2["author"] and not metadata["author"]:
                if _has_bangla_unicode(l2["author"]) or _is_plausible_english(
                    l2["author"]
                ):
                    metadata["author"] = l2["author"]
                    logger.info(f"Level 2 found author: {l2['author']!r}")
                else:
                    logger.info(
                        f"Level 2 author looks like mojibake, skipping: {l2['author']!r}"
                    )

        # --- Level 3: Tesseract OCR on cover page ---
        if not metadata["title"]:
            logger.info("Level 2 empty, trying Tesseract OCR on cover (level 3)...")
            l3 = _extract_metadata_via_ocr(doc)
            if l3["title"]:
                metadata["title"] = l3["title"]
                logger.info(f"Level 3 found title: {l3['title']!r}")
            if l3["author"] and not metadata["author"]:
                metadata["author"] = l3["author"]
                logger.info(f"Level 3 found author: {l3['author']!r}")

        doc.close()
    except Exception as e:
        logger.warning(f"Could not read PDF metadata: {e}")

    return metadata


def _extract_metadata_from_text(doc) -> dict:
    """
    Level 2: Extract title/author by analysing font sizes on the first 3 pages.
    The largest text span is likely the title; the second-largest (or text near
    common Bangla author keywords) is likely the author.
    """
    import re

    result = {"title": "", "author": ""}
    try:
        # Collect text spans with their font sizes from first 3 pages
        spans_by_size: list[tuple[float, str]] = []
        pages_to_check = min(3, len(doc))

        for page_idx in range(pages_to_check):
            page = doc[page_idx]
            blocks = page.get_text("dict", flags=0)["blocks"]
            for block in blocks:
                if block.get("type") != 0:  # text blocks only
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "").strip()
                        size = span.get("size", 0)
                        if text and len(text) > 1:
                            spans_by_size.append((size, text))

        if not spans_by_size:
            return result

        # Sort by font size descending
        spans_by_size.sort(key=lambda x: x[0], reverse=True)

        # Largest font text = likely title
        # Merge adjacent spans with the same (largest) font size
        max_size = spans_by_size[0][0]
        title_parts = [
            text for size, text in spans_by_size if abs(size - max_size) < 0.5
        ]
        candidate_title = " ".join(title_parts).strip()

        # Sanity check: title should be reasonable length and not just numbers/punctuation
        if candidate_title and 2 <= len(candidate_title) <= 200:
            result["title"] = candidate_title

        # Look for author: common Bangla patterns or second-largest font
        # Common Bangla author indicators: রচনা (written by), লেখক (author),
        # by/By, or text right after the title
        bangla_author_patterns = re.compile(
            r"(রচনা\s*[:\-–—]?\s*|লেখক\s*[:\-–—]?\s*|রচনায়\s*[:\-–—]?\s*)(.*)",
            re.UNICODE,
        )

        for _size, text in spans_by_size:
            match = bangla_author_patterns.search(text)
            if match:
                author_name = match.group(2).strip()
                if author_name and len(author_name) > 1:
                    result["author"] = author_name
                    break

        # If no pattern match, try second-largest font size (common for author name)
        if not result["author"] and len(spans_by_size) > 1:
            sizes_seen = sorted(set(s for s, _ in spans_by_size), reverse=True)
            if len(sizes_seen) >= 2:
                second_size = sizes_seen[1]
                author_parts = [
                    text
                    for size, text in spans_by_size
                    if abs(size - second_size) < 0.5
                ]
                candidate_author = " ".join(author_parts).strip()
                # Author names are typically short
                if candidate_author and 2 <= len(candidate_author) <= 100:
                    result["author"] = candidate_author

    except Exception as e:
        logger.debug(f"Font-size heuristics failed: {e}")

    return result


def _extract_metadata_via_ocr(doc) -> dict:
    """
    Level 3: OCR the cover page with Tesseract and apply heuristics.
    Only runs if Tesseract is available on the system.
    """
    result = {"title": "", "author": ""}
    try:
        import subprocess

        # Check if tesseract is available
        tess_check = subprocess.run(
            ["tesseract", "--version"],
            capture_output=True,
            timeout=5,
        )
        if tess_check.returncode != 0:
            logger.debug("Tesseract not available, skipping OCR metadata extraction")
            return result

        from PIL import Image
        import io

        # Render cover page to image
        page = doc[0]
        # Use 2x zoom for better OCR accuracy
        mat = page.get_pixmap(matrix=page.derotation_matrix, dpi=200)
        img_data = mat.tobytes("png")
        img = Image.open(io.BytesIO(img_data))

        # OCR with Tesseract (Bangla + English)
        import pytesseract

        # Get text with per-line confidence using tsv output
        ocr_data = pytesseract.image_to_data(
            img, lang="ben+eng", output_type=pytesseract.Output.DICT
        )

        # Group text by approximate "block" — larger text on cover = title
        # pytesseract data includes block_num, line_num, word_num, height, text
        lines_info: list[tuple[float, str]] = []
        current_line_texts: list[str] = []
        current_line_height = 0.0
        current_line_num = -1
        current_block_num = -1

        for i in range(len(ocr_data["text"])):
            text = (ocr_data["text"][i] or "").strip()
            block_num = ocr_data["block_num"][i]
            line_num = ocr_data["line_num"][i]
            height = ocr_data["height"][i]
            conf = int(ocr_data["conf"][i]) if ocr_data["conf"][i] != "-1" else 0

            if block_num != current_block_num or line_num != current_line_num:
                # Save previous line
                if current_line_texts:
                    line_text = " ".join(current_line_texts).strip()
                    if line_text:
                        lines_info.append((current_line_height, line_text))
                current_line_texts = []
                current_line_height = 0
                current_line_num = line_num
                current_block_num = block_num

            if text and conf > 30:  # filter low-confidence noise
                current_line_texts.append(text)
                current_line_height = max(current_line_height, height)

        # Don't forget the last line
        if current_line_texts:
            line_text = " ".join(current_line_texts).strip()
            if line_text:
                lines_info.append((current_line_height, line_text))

        if not lines_info:
            return result

        # Sort by height descending — tallest text = title
        lines_info.sort(key=lambda x: x[0], reverse=True)

        # Largest text = title
        candidate_title = lines_info[0][1]
        if candidate_title and 2 <= len(candidate_title) <= 200:
            result["title"] = candidate_title

        # Second largest = likely author
        if len(lines_info) > 1:
            candidate_author = lines_info[1][1]
            if candidate_author and 2 <= len(candidate_author) <= 100:
                result["author"] = candidate_author

    except FileNotFoundError:
        logger.debug("Tesseract binary not found, skipping OCR metadata extraction")
    except Exception as e:
        logger.debug(f"OCR metadata extraction failed: {e}")

    return result


def _slugify_author(name: str) -> str:
    """Convert an author name to a URL-friendly slug.

    E.g. "Humayun Ahmed" → "humayun-ahmed"
    """
    if not name:
        return ""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


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
    parser.add_argument(
        "--export-bilingual-json",
        action="store_true",
        default=True,
        help="Generate a bangla-library-compatible bilingual JSON file (default: on)",
    )
    parser.add_argument(
        "--no-bilingual-json",
        action="store_true",
        default=False,
        help="Skip bilingual JSON generation",
    )
    parser.add_argument(
        "--book-title-bn",
        default="",
        help="Book title in Bangla (for bilingual JSON export)",
    )
    parser.add_argument(
        "--book-author-bn",
        default="",
        help="Book author in Bangla (for bilingual JSON export)",
    )
    parser.add_argument(
        "--book-year",
        default="",
        help="Book publication year (for bilingual JSON export)",
    )
    parser.add_argument(
        "--book-category",
        default="Novel",
        help="Book category (for bilingual JSON export, default: Novel)",
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

        # Also build bilingual page data (Bangla + English) for JSON export
        bilingual_pages = [
            {
                "page_number": r.page_number,
                "original_text": r.original_text,
                "translated_text": r.final_text,
            }
            for r in results
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

        # Step 4: Generate bilingual JSON for bangla-library (if enabled)
        bilingual_json_filename = ""
        bn_title = args.book_title_bn
        bn_author = args.book_author_bn
        if args.export_bilingual_json and not args.no_bilingual_json:
            print("\n=== Step 4: Generating bilingual JSON for bangla-library ===")

            # Use Bangla title/author from metadata extraction or CLI args
            # The extracted metadata may be in Bangla (if the PDF has proper Unicode)
            bn_title = args.book_title_bn or pdf_meta.get("title", "")
            bn_author = args.book_author_bn or pdf_meta.get("author", "")

            # If the extracted title/author look like Bangla, use them for _bn fields
            # and use the English versions for _en fields
            if bn_title and not _has_bangla_unicode(bn_title):
                # Metadata was in English/Latin, not Bangla — clear the bn field
                bn_title = ""
            if bn_author and not _has_bangla_unicode(bn_author):
                bn_author = ""

            bilingual_meta = BilingualMetadata(
                title_bn=bn_title,
                title_en=book_title,
                author_bn=bn_author,
                author_en=book_author or "Unknown Author",
                author_slug=_slugify_author(book_author) if book_author else "",
                year=args.book_year,
                category=args.book_category,
                copyright_notice=(
                    f"This work may be under copyright. "
                    f"This translation is provided for educational purposes only."
                ),
            )

            bilingual_json = generate_bilingual_json(bilingual_pages, bilingual_meta)
            bilingual_json_filename = f"{slug}.json"
            bilingual_json_path = save_bilingual_json(
                bilingual_json, book_dir / bilingual_json_filename
            )
            print(
                f"  Bilingual JSON: {bilingual_json_path} "
                f"({len(bilingual_json['paragraphs'])} paragraphs)"
            )
            files["bilingual_json"] = bilingual_json_filename

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
            title_bangla=bn_title
            if args.export_bilingual_json and not args.no_bilingual_json
            else args.book_title_bn,
            author_bangla=bn_author
            if args.export_bilingual_json and not args.no_bilingual_json
            else args.book_author_bn,
            category=args.book_category,
        )

        print(f"\n=== Done! Book '{slug}' translated successfully ===")
        print(f"  Output directory: {book_dir}")

    except Exception as e:
        logger.exception(f"Translation failed: {e}")
        update_catalog_entry(slug, args.filename, status="failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
