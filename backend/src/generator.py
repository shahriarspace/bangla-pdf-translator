"""
AsciiDoc Generator Module

Converts translated pages into AsciiDoc format.
AsciiDoc can then be exported to HTML or PDF using asciidoctor.
"""

import logging
import subprocess
import shutil
from pathlib import Path
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class BookMetadata:
    """Metadata for the generated book."""

    title: str
    author: str = "Translated from Bangla"
    subtitle: str = ""


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
