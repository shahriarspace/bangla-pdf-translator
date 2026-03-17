"""
Generator Module

Converts translated pages into various output formats:
- AsciiDoc (-> HTML, PDF via asciidoctor or Python fallback)
- Bilingual JSON for bangla-library (Astro content collection format)
"""

import json
import logging
import re
import subprocess
import shutil
from pathlib import Path
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Text utilities (shared across Docker app + CLI)
# ---------------------------------------------------------------------------


def has_bangla_unicode(text: str) -> bool:
    """Check if text contains any Bangla Unicode characters (U+0980-U+09FF)."""
    if not text:
        return False
    return any("\u0980" <= c <= "\u09ff" for c in text)


def slugify_author(name: str) -> str:
    """Convert an author name to a URL-friendly slug.

    E.g. "Humayun Ahmed" -> "humayun-ahmed"
    """
    if not name:
        return ""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


@dataclass
class BookMetadata:
    """Metadata for the generated book."""

    title: str
    author: str = "Translated from Bangla"
    subtitle: str = ""


@dataclass
class BilingualMetadata:
    """Metadata for bangla-library bilingual JSON export.

    Maps to the Astro content collection schema in bangla-library's
    src/content/config.ts.
    """

    title_bn: str = ""
    title_en: str = ""
    author_bn: str = ""
    author_en: str = ""
    author_slug: str = ""
    year: str = ""
    category: str = "Novel"
    description_en: str = ""
    description_bn: str = ""
    copyright_notice: str = ""
    status: str = "published"
    published_date: str = ""  # ISO date string, e.g. "2026-03-17"


def generate_asciidoc(
    translated_pages: list[dict],
    metadata: BookMetadata | None = None,
) -> str:
    """
    Generate AsciiDoc content from translated pages.

    Args:
        translated_pages: List of dicts with 'page_number' and 'text'.
        metadata: Optional book metadata.

    Returns:
        AsciiDoc string content.
    """
    if metadata is None:
        metadata = BookMetadata(title="Translated Book")

    lines = []

    # Document header
    lines.append(f"= {metadata.title}")
    lines.append(f"{metadata.author}")
    lines.append(":doctype: book")
    lines.append(":toc: left")
    lines.append(":toclevels: 2")
    lines.append(":sectnums:")
    lines.append(":icons: font")
    lines.append(":source-highlighter: highlight.js")
    if metadata.subtitle:
        lines.append(f":description: {metadata.subtitle}")
    lines.append("")

    # Group pages into logical chapters
    # Heuristic: every ~10 pages or when there's a large text break
    chapter_size = 10
    current_chapter = 0

    for page_data in translated_pages:
        text = page_data["text"]
        page_num = page_data["page_number"]

        if not text.strip():
            continue

        # Start new chapter every N pages
        if (page_num - 1) % chapter_size == 0:
            current_chapter += 1
            lines.append("")
            lines.append(f"== Chapter {current_chapter}")
            lines.append("")

        # Add page marker as a comment + anchor
        lines.append(f"// Original page {page_num}")
        lines.append(f"[[page-{page_num}]]")
        lines.append("")

        # Process paragraphs
        paragraphs = text.split("\n")
        for para in paragraphs:
            para = para.strip()
            if not para:
                lines.append("")
                continue
            lines.append(para)
            lines.append("")

    return "\n".join(lines)


def save_asciidoc(content: str, output_path: str | Path) -> Path:
    """Save AsciiDoc content to a file."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    logger.info(
        f"AsciiDoc saved: {output_path} ({output_path.stat().st_size / 1024:.1f} KB)"
    )
    return output_path


def asciidoc_to_html(adoc_path: str | Path) -> Path | None:
    """
    Convert AsciiDoc to HTML using asciidoctor.
    Falls back to a simple Python-based conversion if asciidoctor is not available.
    """
    adoc_path = Path(adoc_path)
    html_path = adoc_path.with_suffix(".html")

    # Try asciidoctor first
    if shutil.which("asciidoctor"):
        try:
            subprocess.run(
                [
                    "asciidoctor",
                    "-b",
                    "html5",
                    "-a",
                    "stylesheet!",
                    "-a",
                    "linkcss!",
                    "-a",
                    "toc=left",
                    "-a",
                    "icons=font",
                    "-o",
                    str(html_path),
                    str(adoc_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info(f"HTML generated via asciidoctor: {html_path}")
            return html_path
        except subprocess.CalledProcessError as e:
            logger.warning(f"asciidoctor failed: {e.stderr}")

    # Fallback: simple Python-based conversion
    logger.info("asciidoctor not found, using Python fallback for HTML")
    content = adoc_path.read_text(encoding="utf-8")
    html = _fallback_adoc_to_html(content)
    html_path.write_text(html, encoding="utf-8")
    return html_path


def asciidoc_to_pdf(adoc_path: str | Path) -> Path | None:
    """
    Convert AsciiDoc to PDF using asciidoctor-pdf.
    Falls back to ReportLab-based generation if asciidoctor-pdf is not available.
    """
    adoc_path = Path(adoc_path)
    pdf_path = adoc_path.with_suffix(".pdf")

    # Try asciidoctor-pdf first
    if shutil.which("asciidoctor-pdf"):
        try:
            subprocess.run(
                [
                    "asciidoctor-pdf",
                    "-a",
                    "toc",
                    "-o",
                    str(pdf_path),
                    str(adoc_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info(f"PDF generated via asciidoctor-pdf: {pdf_path}")
            return pdf_path
        except subprocess.CalledProcessError as e:
            logger.warning(f"asciidoctor-pdf failed: {e.stderr}")

    # Fallback: use ReportLab
    logger.info("asciidoctor-pdf not found, using ReportLab fallback for PDF")
    content = adoc_path.read_text(encoding="utf-8")
    _fallback_adoc_to_pdf(content, pdf_path)
    return pdf_path


def _fallback_adoc_to_html(adoc_content: str) -> str:
    """Simple AsciiDoc to HTML conversion without external tools."""
    lines = adoc_content.split("\n")
    html_parts = []
    title = "Translated Book"

    html_parts.append("<!DOCTYPE html>")
    html_parts.append('<html lang="en">')
    html_parts.append("<head>")
    html_parts.append('<meta charset="UTF-8">')
    html_parts.append(
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
    )

    # Extract title from first line
    for line in lines:
        if line.startswith("= ") and not line.startswith("== "):
            title = line[2:].strip()
            break

    html_parts.append(f"<title>{title}</title>")
    html_parts.append("<style>")
    html_parts.append("""
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Georgia', 'Times New Roman', serif;
            line-height: 1.8;
            color: #2d2d2d;
            max-width: 800px;
            margin: 0 auto;
            padding: 40px 20px;
            background: #fafafa;
        }
        h1 {
            font-size: 2.2em;
            color: #1a1a2e;
            border-bottom: 3px solid #e0e0e0;
            padding-bottom: 15px;
            margin-bottom: 30px;
        }
        h2 {
            font-size: 1.6em;
            color: #16213e;
            margin-top: 50px;
            margin-bottom: 20px;
            border-bottom: 1px solid #eee;
            padding-bottom: 8px;
        }
        p {
            margin-bottom: 16px;
            text-align: justify;
            text-indent: 1.5em;
        }
        p:first-of-type { text-indent: 0; }
        .page-ref {
            font-size: 0.75em;
            color: #aaa;
            text-align: center;
            margin: 30px 0 10px;
            font-style: italic;
        }
        @media print {
            body { max-width: 100%; padding: 20px; }
            h2 { page-break-before: always; }
        }
    """)
    html_parts.append("</style>")
    html_parts.append("</head>")
    html_parts.append("<body>")

    in_header = True
    for line in lines:
        stripped = line.strip()

        # Skip AsciiDoc metadata
        if stripped.startswith(":") and ":" in stripped[1:]:
            continue
        if not stripped:
            continue
        if in_header and (stripped.startswith("= ") or not stripped.startswith("=")):
            if stripped.startswith("= ") and not stripped.startswith("== "):
                html_parts.append(f"<h1>{stripped[2:]}</h1>")
                in_header = False
                continue
            if in_header and not stripped.startswith("="):
                # Author line or other header content
                if not stripped.startswith("//") and not stripped.startswith("[["):
                    html_parts.append(
                        f'<p style="text-align:center;color:#777;text-indent:0">{stripped}</p>'
                    )
                continue

        in_header = False

        # Comments (page markers)
        if stripped.startswith("//"):
            page_info = stripped[2:].strip()
            if "page" in page_info.lower():
                html_parts.append(f'<div class="page-ref">{page_info}</div>')
            continue

        # Anchors - skip
        if stripped.startswith("[[") and stripped.endswith("]]"):
            continue

        # Chapter headings
        if stripped.startswith("== "):
            html_parts.append(f"<h2>{stripped[3:]}</h2>")
            continue

        # Regular paragraph
        html_parts.append(f"<p>{stripped}</p>")

    html_parts.append("</body>")
    html_parts.append("</html>")

    return "\n".join(html_parts)


def _fallback_adoc_to_pdf(adoc_content: str, pdf_path: Path):
    """Generate PDF from AsciiDoc content using ReportLab."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
    from reportlab.lib import colors

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "BookTitle",
        parent=styles["Title"],
        fontSize=28,
        spaceAfter=30,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#1a1a2e"),
    )
    chapter_style = ParagraphStyle(
        "Chapter",
        parent=styles["Heading1"],
        fontSize=20,
        spaceBefore=30,
        spaceAfter=15,
        textColor=colors.HexColor("#1a1a2e"),
    )
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=11,
        leading=16,
        spaceAfter=8,
        alignment=TA_JUSTIFY,
        firstLineIndent=20,
    )

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        topMargin=0.8 * inch,
        bottomMargin=0.8 * inch,
        leftMargin=1 * inch,
        rightMargin=1 * inch,
    )

    elements = []
    lines = adoc_content.split("\n")

    for line in lines:
        stripped = line.strip()
        if (
            not stripped
            or stripped.startswith(":")
            or stripped.startswith("//")
            or stripped.startswith("[[")
        ):
            continue

        if stripped.startswith("= ") and not stripped.startswith("== "):
            elements.append(Spacer(1, 2 * inch))
            elements.append(Paragraph(stripped[2:], title_style))
            elements.append(Spacer(1, 1 * inch))
            elements.append(PageBreak())
            continue

        if stripped.startswith("== "):
            elements.append(Paragraph(stripped[3:], chapter_style))
            continue

        # Escape for ReportLab
        safe = stripped.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        try:
            elements.append(Paragraph(safe, body_style))
        except Exception:
            elements.append(Paragraph(safe[:500], body_style))

    if elements:
        doc.build(elements)
    logger.info(f"PDF generated via ReportLab: {pdf_path}")


# ── Bilingual JSON for bangla-library ─────────────────────────────────────


def _split_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs on double-newlines.

    Filters out empty paragraphs and strips whitespace.
    Single newlines within a paragraph are preserved (the content
    may contain intentional line breaks within a passage).
    """
    # Normalise various line ending styles
    text = text.replace("\r\n", "\n")
    # Split on two or more consecutive newlines
    parts = re.split(r"\n{2,}", text)
    return [p.strip() for p in parts if p.strip()]


def _align_paragraphs(
    bn_paragraphs: list[str],
    en_paragraphs: list[str],
) -> list[tuple[str, str]]:
    """Pair up Bangla and English paragraphs.

    If paragraph counts match, pairs are 1:1.
    If they don't match (AI sometimes merges/splits paragraphs),
    falls back to joining all paragraphs into a single pair.
    """
    if len(bn_paragraphs) == len(en_paragraphs):
        return list(zip(bn_paragraphs, en_paragraphs))

    # Small mismatch (within 20% or <=2 difference): try best-effort 1:1
    # by padding the shorter list with empty strings
    diff = abs(len(bn_paragraphs) - len(en_paragraphs))
    max_len = max(len(bn_paragraphs), len(en_paragraphs))
    if diff <= 2 or (max_len > 0 and diff / max_len <= 0.2):
        # Pad shorter list
        bn_padded = bn_paragraphs + [""] * (max_len - len(bn_paragraphs))
        en_padded = en_paragraphs + [""] * (max_len - len(en_paragraphs))
        return list(zip(bn_padded, en_padded))

    # Large mismatch: join all into one paragraph pair
    logger.warning(
        f"Paragraph count mismatch (bn={len(bn_paragraphs)}, "
        f"en={len(en_paragraphs)}), merging into single pair"
    )
    return [("\n\n".join(bn_paragraphs), "\n\n".join(en_paragraphs))]


def generate_bilingual_json(
    bilingual_pages: list[dict],
    metadata: BilingualMetadata | None = None,
) -> dict:
    """Generate a bangla-library-compatible JSON structure.

    Args:
        bilingual_pages: List of dicts with keys:
            - page_number (int)
            - original_text (str) — Bangla source text
            - translated_text (str) — English translation
        metadata: Optional bilingual metadata for the book.

    Returns:
        A dict matching bangla-library's book JSON schema:
        {
            title_bn, title_en, author_bn, author_en, author_slug,
            year, category, paragraphs: [{id, bn, en}, ...]
        }
    """
    if metadata is None:
        metadata = BilingualMetadata()

    from datetime import date

    paragraphs: list[dict] = []
    para_id = 1

    for page in bilingual_pages:
        bn_text = page.get("original_text", "").strip()
        en_text = page.get("translated_text", "").strip()

        if not bn_text and not en_text:
            continue

        # Split each page into paragraph-level pairs
        bn_paras = _split_paragraphs(bn_text) if bn_text else []
        en_paras = _split_paragraphs(en_text) if en_text else []

        if not bn_paras and not en_paras:
            continue

        # Handle edge cases: one side empty
        if not bn_paras:
            bn_paras = [""] * len(en_paras)
        if not en_paras:
            en_paras = [""] * len(bn_paras)

        pairs = _align_paragraphs(bn_paras, en_paras)
        for bn, en in pairs:
            if bn.strip() or en.strip():
                paragraphs.append({"id": para_id, "bn": bn, "en": en})
                para_id += 1

    # Build the book JSON
    book_json: dict = {
        "title_bn": metadata.title_bn,
        "title_en": metadata.title_en,
        "author_bn": metadata.author_bn,
        "author_en": metadata.author_en,
    }

    if metadata.author_slug:
        book_json["author_slug"] = metadata.author_slug

    book_json["year"] = metadata.year or str(date.today().year)
    book_json["published_date"] = metadata.published_date or date.today().isoformat()
    book_json["status"] = metadata.status
    book_json["category"] = metadata.category

    if metadata.description_en:
        book_json["description_en"] = metadata.description_en
    if metadata.description_bn:
        book_json["description_bn"] = metadata.description_bn
    if metadata.copyright_notice:
        book_json["copyright_notice"] = metadata.copyright_notice

    book_json["paragraphs"] = paragraphs

    return book_json


def save_bilingual_json(
    book_json: dict,
    output_path: str | Path,
) -> Path:
    """Save the bilingual JSON to a file.

    Args:
        book_json: The book dict from generate_bilingual_json().
        output_path: Path to write the JSON file.

    Returns:
        The Path object of the saved file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(book_json, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    para_count = len(book_json.get("paragraphs", []))
    size_kb = output_path.stat().st_size / 1024
    logger.info(
        f"Bilingual JSON saved: {output_path} "
        f"({para_count} paragraphs, {size_kb:.1f} KB)"
    )
    return output_path


def build_bilingual_output(
    translation_results: list,
    output_dir: str | Path,
    slug: str,
    *,
    title_en: str = "",
    author_en: str = "",
    title_bn: str = "",
    author_bn: str = "",
    year: str = "",
    category: str = "Novel",
    pdf_meta: dict | None = None,
) -> tuple[Path, dict]:
    """High-level helper: build bilingual JSON from translation results.

    Handles Bangla Unicode validation, author slugification, metadata
    assembly, paragraph alignment, and file saving.  Used by both
    the Docker FastAPI app and the CLI translate_book.py.

    Args:
        translation_results: List of objects with ``page_number``,
            ``original_text``, and ``final_text`` attributes.
        output_dir: Directory to write ``{slug}.json`` into.
        slug: URL-safe book identifier.
        title_en: English title (falls back to slug if empty).
        author_en: English author name.
        title_bn: Bangla title override.  If empty, tries ``pdf_meta['title']``
            and validates it contains Bangla Unicode.
        author_bn: Bangla author override.  Same fallback logic.
        year: Publication year string.
        category: Book category for bangla-library.
        pdf_meta: Optional dict from ``extract_pdf_metadata()``; used as
            fallback for Bangla title/author.

    Returns:
        A tuple of (json_path, book_json_dict).
    """
    pdf_meta = pdf_meta or {}

    # Resolve Bangla title/author: CLI/form value -> PDF metadata -> empty
    bn_title = title_bn or pdf_meta.get("title", "")
    bn_author = author_bn or pdf_meta.get("author", "")

    # Only keep values that actually contain Bangla characters
    if bn_title and not has_bangla_unicode(bn_title):
        bn_title = ""
    if bn_author and not has_bangla_unicode(bn_author):
        bn_author = ""

    bilingual_pages = [
        {
            "page_number": r.page_number,
            "original_text": r.original_text,
            "translated_text": r.final_text,
        }
        for r in translation_results
    ]

    meta = BilingualMetadata(
        title_bn=bn_title,
        title_en=title_en or slug.replace("-", " ").title(),
        author_bn=bn_author,
        author_en=author_en or "Unknown Author",
        author_slug=slugify_author(author_en) if author_en else "",
        year=year,
        category=category,
        copyright_notice=(
            "This work may be under copyright. "
            "This translation is provided for educational purposes only."
        ),
    )

    book_json = generate_bilingual_json(bilingual_pages, meta)
    json_path = save_bilingual_json(book_json, Path(output_dir) / f"{slug}.json")

    return json_path, book_json
