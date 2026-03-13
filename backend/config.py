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
# TRANSLATION_MODE: "offline" (Argos), "online" (Google), "hybrid" (Argos + Google)
TRANSLATION_MODE = os.getenv("TRANSLATION_MODE", "offline")
FREE_TRANSLATOR = os.getenv("FREE_TRANSLATOR", "google")  # legacy, kept for compat
ENABLE_LLM_REFINEMENT = os.getenv("ENABLE_LLM_REFINEMENT", "false").lower() == "true"

# OCR
OCR_ENGINE = os.getenv("OCR_ENGINE", "tesseract")
_default_tesseract = (
    "/usr/bin/tesseract"
    if os.name != "nt"
    else r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)
TESSERACT_PATH = os.getenv("TESSERACT_PATH", _default_tesseract)

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
