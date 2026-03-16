"""Configuration management for the Bangla PDF to English Book pipeline."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Paths
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
JOBS_DIR = BASE_DIR / "jobs"

# Ensure directories exist
UPLOAD_DIR.mkdir(exist_ok=True)
JOBS_DIR.mkdir(exist_ok=True)

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# GitHub Models (uses your GitHub PAT for inference)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_MODEL = os.getenv("GITHUB_MODEL", "gpt-4o-mini")
GITHUB_MODELS_URL = "https://models.inference.ai.azure.com"

# Translation
# TRANSLATION_MODE: "offline" (Argos), "online" (Google), "hybrid", "ai" (full AI)
TRANSLATION_MODE = os.getenv("TRANSLATION_MODE", "offline")
FREE_TRANSLATOR = os.getenv("FREE_TRANSLATOR", "google")  # legacy, kept for compat
ENABLE_LLM_REFINEMENT = os.getenv("ENABLE_LLM_REFINEMENT", "false").lower() == "true"

# OCR
# OCR_ENGINE: "tesseract" (default, offline) or "ai" (vision model via API)
OCR_ENGINE = os.getenv("OCR_ENGINE", "tesseract")
_default_tesseract = (
    "/usr/bin/tesseract"
    if os.name != "nt"
    else r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)
TESSERACT_PATH = os.getenv("TESSERACT_PATH", _default_tesseract)

# AI Pipeline settings (for OCR_ENGINE=ai and/or TRANSLATION_MODE=ai)
# Ported from PDF-to-Book project: uses OpenAI-compatible API with vision
# for high-quality AI OCR and literary AI translation.
# AI_PROVIDER: "openai" or "github" — which API endpoint to use
AI_PROVIDER = os.getenv("AI_PROVIDER", "github")
# AI_OCR_MODEL: Vision-capable model for OCR (must support image inputs)
AI_OCR_MODEL = os.getenv("AI_OCR_MODEL", "gpt-4o")
# AI_TRANSLATE_MODEL: Model for literary translation
AI_TRANSLATE_MODEL = os.getenv("AI_TRANSLATE_MODEL", "gpt-4o")
# AI_MAX_RETRIES: Retries per page on failure
AI_MAX_RETRIES = int(os.getenv("AI_MAX_RETRIES", "3"))
# AI_RETRY_DELAY: Base delay between retries in seconds
AI_RETRY_DELAY = float(os.getenv("AI_RETRY_DELAY", "5.0"))
# AI_PAGE_DELAY: Delay between pages in seconds (rate limiting)
AI_PAGE_DELAY = float(os.getenv("AI_PAGE_DELAY", "2.0"))

# Server
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

# Cleanup
JOB_MAX_AGE_HOURS = float(os.getenv("JOB_MAX_AGE_HOURS", "24"))
CLEANUP_INTERVAL_MINUTES = float(os.getenv("CLEANUP_INTERVAL_MINUTES", "30"))

# Google Translate rate limiting
GOOGLE_REQUESTS_PER_MINUTE = int(os.getenv("GOOGLE_REQUESTS_PER_MINUTE", "20"))
GOOGLE_MAX_RETRIES = int(os.getenv("GOOGLE_MAX_RETRIES", "5"))
GOOGLE_RETRY_BASE_DELAY = float(os.getenv("GOOGLE_RETRY_BASE_DELAY", "2.0"))
