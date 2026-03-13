# ============================================================
# Bangla PDF to English Book - Web Application
# Tesseract OCR + Argos Translate + Asciidoctor + FastAPI
# ============================================================

FROM python:3.12-slim

LABEL description="Bangla PDF to English Book - Web UI"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# ---- System dependencies ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Tesseract OCR with Bangla
    tesseract-ocr \
    tesseract-ocr-ben \
    tesseract-ocr-eng \
    # Asciidoctor for AsciiDoc -> HTML/PDF
    ruby \
    # Image libs for Pillow/PyMuPDF
    libglib2.0-0 \
    && gem install asciidoctor --no-document \
    && rm -rf /var/lib/apt/lists/*

# Verify installations
RUN tesseract --version && tesseract --list-langs && asciidoctor --version

# ---- App setup ----
WORKDIR /app

# ---- Install CPU-only PyTorch FIRST (saves ~2.5GB vs GPU version) ----
# Argos Translate depends on stanza -> torch. Without this, pip pulls
# the full CUDA torch (~3GB). CPU-only is ~200MB.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- Pre-download Argos Translate bn->en model ----
# This bakes the ~100MB model into the image so it works fully offline.
RUN python -c "\
import argostranslate.package; \
argostranslate.package.update_package_index(); \
pkgs = argostranslate.package.get_available_packages(); \
bn_en = next(p for p in pkgs if p.from_code == 'bn' and p.to_code == 'en'); \
argostranslate.package.install_from_path(bn_en.download()); \
print('Argos bn->en model installed successfully'); \
"

COPY . .
RUN mkdir -p uploads jobs static

# ---- Environment defaults ----
ENV OCR_ENGINE=tesseract
ENV TESSERACT_PATH=/usr/bin/tesseract
ENV TRANSLATION_MODE=offline
ENV FREE_TRANSLATOR=google
ENV ENABLE_LLM_REFINEMENT=false
ENV OPENAI_API_KEY=""
ENV OPENAI_MODEL=gpt-4o-mini
ENV HOST=0.0.0.0
ENV PORT=8000

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
