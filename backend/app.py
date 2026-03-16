"""
Bangla PDF to English Book - Web Application

FastAPI backend serving:
- File upload endpoint
- Background processing with SSE progress
- ZIP download (original PDF + AsciiDoc + HTML + PDF)
- Automatic cleanup of old jobs
- Static frontend
"""

import asyncio
import json
import logging
import shutil
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

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
logger = logging.getLogger(__name__)

app = FastAPI(title="Bangla PDF to English Book")

# Use absolute paths based on where this file lives
_APP_DIR = Path(__file__).parent.resolve()
_STATIC_DIR = _APP_DIR / "static"

# Serve static files (frontend)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# In-memory job tracking
jobs: dict[str, dict] = {}

# Cleanup task handle
_cleanup_task: asyncio.Task | None = None


def _cleanup_old_jobs():
    """Remove job directories older than JOB_MAX_AGE_HOURS."""
    max_age_secs = config.JOB_MAX_AGE_HOURS * 3600
    now = time.time()
    removed = 0

    if not config.JOBS_DIR.exists():
        return

    for job_dir in config.JOBS_DIR.iterdir():
        if not job_dir.is_dir():
            continue
        try:
            # Use directory modification time as proxy for job age
            dir_age = now - job_dir.stat().st_mtime
            if dir_age > max_age_secs:
                shutil.rmtree(job_dir, ignore_errors=True)
                # Also remove from in-memory tracking
                job_id = job_dir.name
                jobs.pop(job_id, None)
                removed += 1
        except OSError:
            pass

    if removed:
        logger.info(
            f"Cleanup: removed {removed} old job(s) (max age: {config.JOB_MAX_AGE_HOURS}h)"
        )


def _cleanup_old_uploads():
    """Remove uploaded PDF files older than JOB_MAX_AGE_HOURS."""
    max_age_secs = config.JOB_MAX_AGE_HOURS * 3600
    now = time.time()
    removed = 0

    if not config.UPLOAD_DIR.exists():
        return

    for upload_file in config.UPLOAD_DIR.iterdir():
        if upload_file.is_dir():
            # Shouldn't happen, but clean up stale subdirectories too
            try:
                dir_age = now - upload_file.stat().st_mtime
                if dir_age > max_age_secs:
                    shutil.rmtree(upload_file, ignore_errors=True)
                    removed += 1
            except OSError:
                pass
            continue
        try:
            file_age = now - upload_file.stat().st_mtime
            if file_age > max_age_secs:
                upload_file.unlink(missing_ok=True)
                removed += 1
        except OSError:
            pass

    if removed:
        logger.info(
            f"Cleanup: removed {removed} old upload(s) (max age: {config.JOB_MAX_AGE_HOURS}h)"
        )


async def _periodic_cleanup():
    """Background loop that runs cleanup every CLEANUP_INTERVAL_MINUTES."""
    while True:
        await asyncio.sleep(config.CLEANUP_INTERVAL_MINUTES * 60)
        try:
            _cleanup_old_jobs()
            _cleanup_old_uploads()
        except Exception as e:
            logger.warning(f"Cleanup error: {e}")


@app.on_event("startup")
async def startup_event():
    """Run initial cleanup and start periodic cleanup task."""
    global _cleanup_task
    _cleanup_old_jobs()
    _cleanup_old_uploads()
    _cleanup_task = asyncio.create_task(_periodic_cleanup())
    logger.info(
        f"Cleanup enabled: max age {config.JOB_MAX_AGE_HOURS}h, "
        f"interval {config.CLEANUP_INTERVAL_MINUTES}min"
    )


@app.on_event("shutdown")
async def shutdown_event():
    """Cancel the periodic cleanup task."""
    global _cleanup_task
    if _cleanup_task:
        _cleanup_task.cancel()


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main page."""
    index_path = _STATIC_DIR / "index.html"
    return index_path.read_text(encoding="utf-8")


@app.post("/api/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    use_llm: bool = Form(False),
    refinement_provider: str = Form("none"),
    refinement_model: str = Form(""),
    translation_mode: str = Form(""),
    ocr_engine: str = Form(""),
    ai_provider: str = Form(""),
    ai_ocr_model: str = Form(""),
    ai_translate_model: str = Form(""),
):
    """Upload a Bangla PDF and start processing."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    # Validate translation_mode; fall back to config default
    valid_modes = ("offline", "online", "hybrid", "ai")
    if translation_mode not in valid_modes:
        translation_mode = config.TRANSLATION_MODE

    # Validate OCR engine; fall back to config default
    valid_ocr = ("tesseract", "ai")
    if ocr_engine not in valid_ocr:
        ocr_engine = config.OCR_ENGINE

    # Validate refinement_provider
    valid_providers = ("none", "openai", "github")
    if refinement_provider not in valid_providers:
        refinement_provider = "none"

    # Apply AI provider/model overrides to config for this request
    # These are process-global, which is fine for single-user Docker usage.
    if ai_provider in ("openai", "github"):
        config.AI_PROVIDER = ai_provider
    if ai_ocr_model:
        config.AI_OCR_MODEL = ai_ocr_model
    if ai_translate_model:
        config.AI_TRANSLATE_MODEL = ai_translate_model

    job_id = str(uuid.uuid4())[:8]
    job_dir = config.JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # Save uploaded file
    pdf_path = job_dir / file.filename
    with open(pdf_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Initialize job state
    jobs[job_id] = {
        "id": job_id,
        "status": "queued",
        "filename": file.filename,
        "pdf_path": str(pdf_path),
        "use_llm": use_llm,
        "refinement_provider": refinement_provider,
        "refinement_model": refinement_model,
        "translation_mode": translation_mode,
        "ocr_engine": ocr_engine,
        "progress": 0,
        "total_steps": 0,
        "current_step": "",
        "error": None,
        "adoc_path": None,
        "html_path": None,
        "pdf_out_path": None,
        "adoc_content": None,
    }

    # Start processing in background
    asyncio.create_task(_process_job(job_id))

    return {"job_id": job_id, "status": "queued"}


@app.get("/api/jobs/{job_id}/progress")
async def job_progress(job_id: str):
    """SSE endpoint for real-time progress updates."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        last_sent = None
        while True:
            job = jobs.get(job_id)
            if not job:
                break

            # Build the event data
            event_data = {
                "status": job["status"],
                "progress": job["progress"],
                "total_steps": job["total_steps"],
                "current_step": job["current_step"],
                "error": job["error"],
            }

            data_str = json.dumps(event_data)
            if data_str != last_sent:
                yield {"event": "progress", "data": data_str}
                last_sent = data_str

            if job["status"] in ("completed", "failed"):
                # Send final event with download info
                final = {**event_data}
                if job["status"] == "completed":
                    final["downloads"] = {
                        "zip": f"/api/jobs/{job_id}/download/zip",
                        "adoc": f"/api/jobs/{job_id}/download/adoc",
                        "html": f"/api/jobs/{job_id}/download/html",
                        "pdf": f"/api/jobs/{job_id}/download/pdf",
                    }
                yield {"event": "complete", "data": json.dumps(final)}
                break

            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())


@app.get("/api/jobs/{job_id}/preview")
async def job_preview(job_id: str):
    """Get the AsciiDoc content for preview."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    if job["status"] != "completed":
        raise HTTPException(status_code=400, detail="Job not completed yet")

    return {"adoc_content": job["adoc_content"]}


@app.get("/api/jobs/{job_id}/download/{fmt}")
async def download_file(job_id: str, fmt: str):
    """Download the translated file in the requested format (adoc, html, pdf, zip)."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    if job["status"] != "completed":
        raise HTTPException(status_code=400, detail="Job not completed yet")

    stem = Path(job["filename"]).stem

    if fmt == "zip":
        # Build zip bundle: original PDF + adoc + html + pdf
        zip_path = job.get("zip_path")
        if not zip_path or not Path(zip_path).exists():
            zip_path = _build_zip(job_id)
            job["zip_path"] = str(zip_path)
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=f"{stem}_translated.zip",
        )
    elif fmt == "adoc":
        path = job["adoc_path"]
        media_type = "text/asciidoc"
        filename = f"{stem}_english.adoc"
    elif fmt == "html":
        if not job.get("html_path"):
            html_path = asciidoc_to_html(job["adoc_path"])
            job["html_path"] = str(html_path)
        path = job["html_path"]
        media_type = "text/html"
        filename = f"{stem}_english.html"
    elif fmt == "pdf":
        if not job.get("pdf_out_path"):
            pdf_path = asciidoc_to_pdf(job["adoc_path"])
            job["pdf_out_path"] = str(pdf_path)
        path = job["pdf_out_path"]
        media_type = "application/pdf"
        filename = f"{stem}_english.pdf"
    else:
        raise HTTPException(status_code=400, detail=f"Unknown format: {fmt}")

    if not path or not Path(path).exists():
        raise HTTPException(
            status_code=500, detail=f"File not generated for format: {fmt}"
        )

    return FileResponse(path, media_type=media_type, filename=filename)


def _build_zip(job_id: str) -> Path:
    """Create a ZIP file containing the original PDF and all translated outputs."""
    job = jobs[job_id]
    stem = Path(job["filename"]).stem
    job_dir = config.JOBS_DIR / job_id
    zip_path = job_dir / f"{stem}_translated.zip"

    # Ensure HTML and PDF are generated
    if not job.get("html_path"):
        html_path = asciidoc_to_html(job["adoc_path"])
        job["html_path"] = str(html_path)
    if not job.get("pdf_out_path"):
        pdf_path = asciidoc_to_pdf(job["adoc_path"])
        job["pdf_out_path"] = str(pdf_path)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Original PDF
        original_pdf = Path(job["pdf_path"])
        if original_pdf.exists():
            zf.write(original_pdf, f"original/{original_pdf.name}")

        # AsciiDoc
        adoc = Path(job["adoc_path"])
        if adoc.exists():
            zf.write(adoc, f"translated/{stem}_english.adoc")

        # HTML
        html = Path(job["html_path"])
        if html.exists():
            zf.write(html, f"translated/{stem}_english.html")

        # PDF
        pdf_out = Path(job["pdf_out_path"])
        if pdf_out.exists():
            zf.write(pdf_out, f"translated/{stem}_english.pdf")

    logger.info(
        f"ZIP bundle created: {zip_path} ({zip_path.stat().st_size / 1024:.1f} KB)"
    )
    return zip_path


async def _process_job(job_id: str):
    """Background task: extract, translate, generate AsciiDoc."""
    job = jobs[job_id]

    try:
        job["status"] = "extracting"
        job["current_step"] = "Extracting text from PDF..."

        # Run extraction in a thread to not block the event loop
        def do_extract():
            progress_state = {"step": 0}

            def on_progress(current, total, msg):
                job["progress"] = current
                job["total_steps"] = total
                job["current_step"] = msg

            return extract_text(
                job["pdf_path"],
                on_progress=on_progress,
                ocr_engine=job.get("ocr_engine"),
            )

        loop = asyncio.get_event_loop()
        book = await loop.run_in_executor(None, do_extract)

        # Prepare pages for translation
        pages = [{"page_number": p.page_number, "text": p.text} for p in book.pages]

        # Translate
        job["status"] = "translating"
        job["progress"] = 0
        job["current_step"] = "Starting translation..."

        def do_translate():
            def on_progress(current, total, msg):
                job["progress"] = current
                job["total_steps"] = total
                job["current_step"] = msg

            return translate_pages(
                pages,
                use_llm=job["use_llm"],
                refinement_provider=job["refinement_provider"],
                refinement_model=job["refinement_model"],
                translation_mode=job["translation_mode"],
                on_progress=on_progress,
            )

        results = await loop.run_in_executor(None, do_translate)

        # Generate AsciiDoc
        job["status"] = "generating"
        job["current_step"] = "Generating AsciiDoc document..."

        translated_pages = [
            {"page_number": r.page_number, "text": r.final_text} for r in results
        ]

        metadata = BookMetadata(
            title=book.title.replace("_", " ").title(),
            subtitle=f"Translated from Bangla ({book.total_pages} pages)",
        )

        adoc_content = generate_asciidoc(translated_pages, metadata)

        job_dir = config.JOBS_DIR / job_id
        adoc_path = save_asciidoc(adoc_content, job_dir / f"{book.title}_english.adoc")

        # Pre-generate HTML and PDF
        job["current_step"] = "Generating HTML..."
        html_path = asciidoc_to_html(adoc_path)

        job["current_step"] = "Generating PDF..."
        pdf_out_path = asciidoc_to_pdf(adoc_path)

        # Build ZIP bundle with all outputs + original PDF
        job["current_step"] = "Creating ZIP bundle..."
        job["adoc_path"] = str(adoc_path)
        job["html_path"] = str(html_path) if html_path else None
        job["pdf_out_path"] = str(pdf_out_path) if pdf_out_path else None
        job["adoc_content"] = adoc_content

        zip_path = _build_zip(job_id)
        job["zip_path"] = str(zip_path)

        job["status"] = "completed"
        job["current_step"] = "Done!"
        job["progress"] = job["total_steps"]

        logger.info(f"Job {job_id} completed successfully")

    except Exception as e:
        logger.exception(f"Job {job_id} failed: {e}")
        job["status"] = "failed"
        job["error"] = str(e)
        job["current_step"] = f"Error: {e}"
