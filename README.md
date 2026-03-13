# Bangla PDF to English Book

A library website that hosts Bangla books translated to English. Upload a Bangla PDF through the web UI, and GitHub Actions automatically processes it: OCR extraction, machine translation, and document generation. Results are committed to the repo and deployed to GitHub Pages.

## How It Works

```
  Browser (GitHub Pages)           GitHub Actions Runner
  ─────────────────────           ──────────────────────
  Upload PDF via web UI  ──────>  Download PDF from release asset
  Trigger workflow_dispatch        Install Tesseract + Argos Translate
  See "submitted" confirmation     Extract text (PyMuPDF + Tesseract OCR)
  Check Actions progress           Translate (Argos offline / Google online)
                                   Optionally refine with AI (GitHub Models)
  Browse library  <────────────   Generate AsciiDoc + HTML + PDF
  Read books online                Commit to library/{slug}/
  Download translations            Rebuild & deploy Astro site
```

## Architecture

```
bangla-pdf-to-eng-book/
├── .github/workflows/
│   ├── translate.yml        # Translation pipeline (workflow_dispatch)
│   └── deploy.yml           # Build Astro + deploy to GitHub Pages
├── src/                     # Astro frontend source
│   ├── pages/
│   │   ├── index.astro      # Home page (library overview)
│   │   ├── upload/
│   │   │   └── index.astro  # Upload page
│   │   └── books/
│   │       ├── index.astro  # Browse all books
│   │       └── [slug]/
│   │           ├── index.astro  # Book detail page
│   │           └── read.astro   # Read online
│   ├── layouts/
│   │   └── BaseLayout.astro # Shared layout
│   ├── components/
│   │   └── UploadForm.svelte  # Interactive upload island
│   └── types.ts             # TypeScript type definitions
├── public/                  # Static assets
│   ├── styles/global.css
│   └── favicon.svg
├── library/                 # Book storage (committed to repo)
│   ├── catalog.json         # Book metadata catalog
│   └── {slug}/              # Per-book directory
│       ├── original.pdf
│       ├── *_english.adoc
│       ├── *_english.html
│       └── *_english.pdf
├── backend/                 # Python translation pipeline
│   ├── translate_book.py    # CLI entry point for GitHub Actions
│   ├── config.py            # Configuration
│   ├── requirements.txt     # Python dependencies
│   ├── app.py               # Legacy FastAPI server (Docker mode)
│   ├── Dockerfile           # Legacy Docker image
│   ├── docker-compose.yml   # Legacy Docker Compose
│   └── src/
│       ├── extractor.py     # PDF text extraction
│       ├── translator.py    # Translation backends
│       └── generator.py     # AsciiDoc/HTML/PDF generation
├── astro.config.mjs         # Astro configuration
├── package.json             # Node.js dependencies
└── tsconfig.json            # TypeScript config
```

## Quick Start

### 1. Fork & Configure

1. Fork this repository (or create from template)
2. Go to **Settings > Pages** and set source to **GitHub Actions**
3. Update `astro.config.mjs`:
   - Set `site` to `https://YOUR_USERNAME.github.io`
   - Set `base` to `/bangla-pdf-to-eng-book` (or your repo name)

### 2. Set Up Secrets

Go to **Settings > Secrets and variables > Actions** and add:

| Secret | Required | Description |
|--------|----------|-------------|
| `GITHUB_TOKEN` | Auto | Provided automatically by GitHub Actions |
| `OPENAI_API_KEY` | Optional | For OpenAI AI refinement |

> **Note:** The default `GITHUB_TOKEN` has sufficient permissions for the translate workflow. For the upload UI, users need a Personal Access Token (PAT) with `repo` scope.

### 3. Create a PAT for the Upload UI

The web UI needs a GitHub PAT to upload PDFs and trigger workflows:

1. Go to https://github.com/settings/tokens
2. Create a **Fine-grained token** with:
   - **Repository access:** Select your fork
   - **Permissions:** Contents (Read/Write), Actions (Read/Write)
3. Copy the token — you'll paste it into the upload form on the website

### 4. Deploy

Push to `main` to trigger the first deployment:

```bash
git push origin main
```

The site will be available at `https://YOUR_USERNAME.github.io/bangla-pdf-to-eng-book/`

## Usage

1. Visit your GitHub Pages site
2. Go to **Upload** page
3. Enter your GitHub username and PAT
4. Drop a Bangla PDF
5. Choose translation options (Offline/Online, AI refinement)
6. Click **Upload & Translate**
7. Check the Actions tab for progress
8. Once complete, the book appears in the library

## Translation Pipeline

| Step | Tool | Description |
|------|------|-------------|
| Extract | PyMuPDF + Tesseract | Digital pages: direct text extraction. Scanned pages: 300 DPI OCR with `ben+eng` |
| Translate | Argos (offline) or Google (online) | Chunks text at Bangla sentence boundaries, translates each chunk |
| Refine | GitHub Models or OpenAI (optional) | Sends original + translation to LLM for literary polish |
| Generate | AsciiDoc + asciidoctor | Creates `.adoc` source, converts to HTML and PDF |

## Translation Modes

- **Offline (Argos):** No internet needed. ~100MB model downloaded during workflow. Good enough quality for most books.
- **Online (Google):** Better quality. Rate-limited with sliding window + exponential backoff retry.
- **AI Refinement:** Optional post-translation step. Uses GitHub Models API (free with GitHub PAT) or OpenAI for literary-quality output.

## Local Development

```bash
# Install Node.js dependencies
npm install

# Run Astro dev server
npm run dev

# Build for production
npm run build
```

## Legacy Docker Mode

The original Docker-based setup is preserved in `backend/`. To run the standalone FastAPI server:

```bash
cd backend
docker compose up --build
# Open http://localhost:8501
```
