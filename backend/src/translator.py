"""
Translation Module

Multi-backend translation pipeline:
1. Argos Translate (offline, default) — no internet, no rate limits
2. Google Translate (online) — better quality, has rate limits
3. Hybrid — Argos offline with Google as fallback/enhancement
4. AI — Direct literary translation via AI model (OpenAI / GitHub Models)
         Ported from PDF-to-Book project — same prompts for consistent results
5. Optional LLM refinement via OpenAI (any mode except AI)

Reports progress via callback for the web UI.
"""

import collections
import logging
import re
import time
from dataclasses import dataclass
from typing import Callable

import config

logger = logging.getLogger(__name__)

CHUNK_SIZE = 4500


# ── Google Translate rate limiter ──────────────────────────────────────────


class _RateLimiter:
    """Sliding-window rate limiter for Google Translate requests.

    Tracks timestamps of recent requests and sleeps when the window is full.
    Thread-safe enough for single-process asyncio usage.
    """

    def __init__(self, max_per_minute: int):
        self.max_per_minute = max_per_minute
        self._timestamps: collections.deque[float] = collections.deque()

    def wait(self):
        """Block until a request slot is available."""
        now = time.time()
        window = 60.0

        # Purge timestamps older than the window
        while self._timestamps and (now - self._timestamps[0]) > window:
            self._timestamps.popleft()

        if len(self._timestamps) >= self.max_per_minute:
            # Sleep until the oldest request falls out of the window
            wait_time = window - (now - self._timestamps[0]) + 0.1
            logger.info(
                f"Rate limit: {self.max_per_minute} req/min reached, "
                f"waiting {wait_time:.1f}s"
            )
            time.sleep(wait_time)

            # Purge again after sleeping
            now = time.time()
            while self._timestamps and (now - self._timestamps[0]) > window:
                self._timestamps.popleft()

        self._timestamps.append(time.time())


_google_rate_limiter = _RateLimiter(config.GOOGLE_REQUESTS_PER_MINUTE)

# ── Argos Translate singleton ──────────────────────────────────────────────

_argos_ready = False


def _ensure_argos_model():
    """Load the Argos bn→en model. Only runs once per process."""
    global _argos_ready
    if _argos_ready:
        return

    try:
        import argostranslate.settings
        import argostranslate.package
        import argostranslate.translate

        # Use MINISBD for sentence splitting — Stanza has a bug with
        # Bengali language resources in v1.10.x (KeyError: 'packages').
        argostranslate.settings.chunk_type = argostranslate.settings.ChunkType.MINISBD

        # Check if bn→en is already installed
        installed = argostranslate.package.get_installed_packages()
        bn_en = [p for p in installed if p.from_code == "bn" and p.to_code == "en"]

        if not bn_en:
            logger.info("Argos bn→en model not installed, downloading...")
            argostranslate.package.update_package_index()
            available = argostranslate.package.get_available_packages()
            pkg = next(
                (p for p in available if p.from_code == "bn" and p.to_code == "en"),
                None,
            )
            if pkg is None:
                raise RuntimeError("Argos Translate bn→en package not found in index")
            argostranslate.package.install_from_path(pkg.download())
            logger.info("Argos bn→en model installed successfully")
        else:
            logger.info("Argos bn→en model already installed")

        _argos_ready = True

    except ImportError:
        raise RuntimeError(
            "argostranslate is not installed. Run: pip install argostranslate"
        )


# ── Data structures ───────────────────────────────────────────────────────


@dataclass
class TranslationResult:
    """Result of translating a single page."""

    page_number: int
    original_text: str
    translated_text: str
    refined_text: str | None = None

    @property
    def final_text(self) -> str:
        return self.refined_text if self.refined_text else self.translated_text


# ── Text chunking ─────────────────────────────────────────────────────────


def _chunk_text(text: str, max_length: int = CHUNK_SIZE) -> list[str]:
    """Split text into chunks respecting sentence boundaries."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        split_pos = remaining[:max_length].rfind("\u0964")  # Bangla danda
        if split_pos == -1:
            split_pos = remaining[:max_length].rfind(".")
        if split_pos == -1:
            split_pos = remaining[:max_length].rfind("\n")
        if split_pos == -1:
            split_pos = remaining[:max_length].rfind(" ")
        if split_pos == -1:
            split_pos = max_length

        chunks.append(remaining[: split_pos + 1])
        remaining = remaining[split_pos + 1 :].strip()

    return chunks


# ── AI output cleaning (shared with extractor) ───────────────────────────

_BOILERPLATE_PATTERNS = [
    r"^let me know",
    r"^i hope this",
    r"^feel free to",
    r"^if you need",
    r"^please let me",
    r"^is there anything",
    r"^do you want",
    r"^would you like",
    r"^---\s*$",
]


def _clean_ai_output(text: str) -> str:
    """Remove markdown fences and trailing boilerplate from AI output."""
    lines = text.rstrip().split("\n")

    # Strip leading/trailing markdown code fences
    if lines and re.match(r"^```\w*$", lines[0].strip()):
        lines.pop(0)
    if lines and lines[-1].strip() == "```":
        lines.pop()

    # Remove trailing boilerplate
    while lines:
        last = lines[-1].strip().lower()
        if not last:
            lines.pop()
            continue
        if any(re.match(pat, last) for pat in _BOILERPLATE_PATTERNS):
            lines.pop()
            continue
        break

    return "\n".join(lines).rstrip()


# ── Translation backends ──────────────────────────────────────────────────


def _translate_argos(text: str) -> str:
    """Translate Bangla text to English using Argos Translate (offline)."""
    if not text.strip():
        return ""

    _ensure_argos_model()
    import argostranslate.translate

    chunks = _chunk_text(text)
    translated_chunks = []

    for chunk in chunks:
        try:
            result = argostranslate.translate.translate(chunk, "bn", "en")
            translated_chunks.append(result or "")
        except Exception as e:
            logger.warning(f"Argos translation failed for chunk: {e}")
            translated_chunks.append(f"[Translation failed: {chunk[:50]}...]")

    return " ".join(translated_chunks)


def _translate_google(text: str) -> str:
    """Translate Bangla text to English using free Google Translate.

    Chunks the text to stay under the ~5000 char/request limit, applies a
    sliding-window rate limiter (GOOGLE_REQUESTS_PER_MINUTE), and retries
    with exponential backoff on failures (HTTP 429, network errors, etc.).
    """
    if not text.strip():
        return ""

    from deep_translator import GoogleTranslator

    chunks = _chunk_text(text)
    translated_chunks = []
    translator = GoogleTranslator(source="bn", target="en")

    for chunk_idx, chunk in enumerate(chunks):
        result = None

        for attempt in range(config.GOOGLE_MAX_RETRIES):
            try:
                _google_rate_limiter.wait()
                result = translator.translate(chunk)
                break  # success
            except Exception as e:
                err_msg = str(e).lower()
                is_rate_limit = "429" in err_msg or "too many" in err_msg

                if attempt < config.GOOGLE_MAX_RETRIES - 1:
                    delay = config.GOOGLE_RETRY_BASE_DELAY * (2**attempt)
                    if is_rate_limit:
                        # Longer backoff for rate limits
                        delay = max(delay, 30.0)
                    logger.warning(
                        f"Google translate chunk {chunk_idx + 1}/{len(chunks)} "
                        f"attempt {attempt + 1} failed: {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"Google translate chunk {chunk_idx + 1}/{len(chunks)} "
                        f"failed after {config.GOOGLE_MAX_RETRIES} attempts: {e}"
                    )

        translated_chunks.append(
            result if result else f"[Translation failed: {chunk[:50]}...]"
        )

    return " ".join(translated_chunks)


def _translate_hybrid(text: str) -> str:
    """Translate with Argos first, then try Google for improvement.

    Falls back to Argos-only if Google is unavailable (no internet,
    rate limited, etc.).
    """
    argos_result = _translate_argos(text)

    try:
        google_result = _translate_google(text)
        # If Google produced a non-error, non-empty result, prefer it
        if google_result and "[Translation failed" not in google_result:
            return google_result
    except Exception as e:
        logger.info(f"Google unavailable in hybrid mode, using Argos: {e}")

    return argos_result


# ── AI Translation (high quality, from PDF-to-Book project) ───────────────


def _get_ai_client():
    """Create an OpenAI-compatible client based on AI_PROVIDER config."""
    from openai import OpenAI

    provider = config.AI_PROVIDER.lower()
    if provider == "github":
        if not config.GITHUB_TOKEN:
            raise ValueError(
                "GITHUB_TOKEN is required for AI provider 'github'. "
                "Set it in .env or environment variables."
            )
        return OpenAI(
            base_url=config.GITHUB_MODELS_URL,
            api_key=config.GITHUB_TOKEN,
        )
    elif provider == "openai":
        if not config.OPENAI_API_KEY:
            raise ValueError(
                "OPENAI_API_KEY is required for AI provider 'openai'. "
                "Set it in .env or environment variables."
            )
        return OpenAI(api_key=config.OPENAI_API_KEY)
    else:
        raise ValueError(f"Unknown AI_PROVIDER: {provider}. Use 'openai' or 'github'.")


def _translate_ai(text: str) -> str:
    """Translate Bangla text to English using an AI model.

    Uses the same literary translation prompt from the PDF-to-Book project.
    Sends the full page text (not chunked) to the AI for coherent translation.
    Retries with exponential backoff on failure.
    """
    if not text.strip():
        return ""

    client = _get_ai_client()
    model = config.AI_TRANSLATE_MODEL

    # Literary translation prompt — same approach as PDF-to-Book
    prompt = (
        "You are an expert literary translator from Bengali to English. "
        "Below is Bengali text extracted from a novel. "
        "Translate it into natural, literary English. "
        "Preserve the tone, style, and paragraph structure of the original. "
        "Use natural English prose — do not be overly literal.\n\n"
        "Output ONLY the English translation. "
        "Do NOT include the original Bengali text. "
        "Do NOT add any commentary, headers, notes, or explanation.\n\n"
        "--- BEGIN BENGALI TEXT ---\n"
        f"{text}\n"
        "--- END BENGALI TEXT ---"
    )

    last_error = None
    for attempt in range(1, config.AI_MAX_RETRIES + 1):
        try:
            t0 = time.time()
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an expert Bengali-to-English literary translator. "
                            "Produce polished, publication-ready English text that "
                            "preserves the original's tone and style."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=4000,
            )
            elapsed = time.time() - t0
            content = response.choices[0].message.content or ""
            result = _clean_ai_output(content)

            if result:
                logger.debug(
                    f"AI translation OK: {len(result)} chars in {elapsed:.1f}s"
                )
                return result
            else:
                logger.warning(
                    f"AI translation attempt {attempt}/{config.AI_MAX_RETRIES}: "
                    f"empty response ({elapsed:.1f}s)"
                )

        except Exception as e:
            last_error = e
            logger.warning(
                f"AI translation attempt {attempt}/{config.AI_MAX_RETRIES} failed: {e}"
            )

        if attempt < config.AI_MAX_RETRIES:
            delay = config.AI_RETRY_DELAY * attempt
            logger.info(f"  Retrying in {delay:.0f}s...")
            time.sleep(delay)

    logger.error(
        f"AI translation FAILED after {config.AI_MAX_RETRIES} attempts: {last_error}"
    )
    return f"[AI Translation failed: {last_error}]"


# ── LLM refinement ────────────────────────────────────────────────────────


def _refine_with_llm(
    original_bangla: str, free_translation: str, model: str = ""
) -> str:
    """Refine a machine translation using OpenAI for literary quality."""
    if not config.OPENAI_API_KEY or config.OPENAI_API_KEY == "your_openai_api_key_here":
        return free_translation

    try:
        from openai import OpenAI
    except ImportError:
        return free_translation

    client = OpenAI(api_key=config.OPENAI_API_KEY)
    use_model = model or config.OPENAI_MODEL

    prompt = f"""You are a professional Bangla-to-English book translator.

Given the original Bangla text and a machine translation, produce natural, fluent English 
preserving meaning, tone, and paragraph structure. Fix awkward phrasing and mistranslations.

--- ORIGINAL BANGLA ---
{original_bangla[:3000]}

--- MACHINE TRANSLATION ---
{free_translation[:3000]}

--- REFINED TRANSLATION ---"""

    try:
        response = client.chat.completions.create(
            model=use_model,
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert Bangla-to-English literary translator. "
                    "Produce polished, publication-ready English text.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=4000,
        )
        content = response.choices[0].message.content
        return content.strip() if content else free_translation
    except Exception as e:
        logger.error(f"LLM refinement failed: {e}")
        return free_translation


def _refine_with_github(
    original_bangla: str, free_translation: str, model: str = ""
) -> str:
    """Refine a machine translation using GitHub Models API.

    Uses the same OpenAI-compatible SDK but pointed at GitHub's inference
    endpoint, authenticated with a GitHub PAT (personal access token).
    """
    if not config.GITHUB_TOKEN:
        logger.warning("GitHub token not set, skipping GitHub Models refinement")
        return free_translation

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai package not installed, skipping GitHub refinement")
        return free_translation

    client = OpenAI(
        base_url=config.GITHUB_MODELS_URL,
        api_key=config.GITHUB_TOKEN,
    )
    use_model = model or config.GITHUB_MODEL

    prompt = f"""You are a professional Bangla-to-English book translator.

Given the original Bangla text and a machine translation, produce natural, fluent English 
preserving meaning, tone, and paragraph structure. Fix awkward phrasing and mistranslations.

--- ORIGINAL BANGLA ---
{original_bangla[:3000]}

--- MACHINE TRANSLATION ---
{free_translation[:3000]}

--- REFINED TRANSLATION ---"""

    try:
        response = client.chat.completions.create(
            model=use_model,
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert Bangla-to-English literary translator. "
                    "Produce polished, publication-ready English text.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=4000,
        )
        content = response.choices[0].message.content
        return content.strip() if content else free_translation
    except Exception as e:
        logger.error(f"GitHub Models refinement failed: {e}")
        return free_translation


# ── Translation mode dispatcher ───────────────────────────────────────────

BACKENDS = {
    "offline": _translate_argos,
    "online": _translate_google,
    "hybrid": _translate_hybrid,
    "ai": _translate_ai,
}


def translate_pages(
    pages: list[dict],
    use_llm: bool = False,
    refinement_provider: str = "none",
    refinement_model: str = "",
    translation_mode: str | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> list[TranslationResult]:
    """
    Translate a list of pages.

    Args:
        pages: List of dicts with 'page_number' and 'text'.
        use_llm: Legacy flag — if True and refinement_provider is "none",
                 falls back to OpenAI refinement for backward compat.
        refinement_provider: "none", "openai", or "github".
        refinement_model: Model name override (e.g. "gpt-4o", "gpt-4o-mini").
                          Empty string = use config default.
        translation_mode: "offline" (Argos), "online" (Google), "hybrid", "ai".
                          Defaults to config.TRANSLATION_MODE.
        on_progress: Optional callback(current, total, status_msg).
    """
    # Backward compat: old use_llm=True maps to openai
    if use_llm and refinement_provider == "none":
        refinement_provider = "openai"

    mode = translation_mode or config.TRANSLATION_MODE
    translate_fn = BACKENDS.get(mode)
    if translate_fn is None:
        logger.warning(f"Unknown translation mode '{mode}', falling back to offline")
        translate_fn = _translate_argos

    # AI mode does its own high-quality translation — refinement is redundant
    refine_fn = None
    refine_label = None
    if mode != "ai":
        if refinement_provider == "openai":
            refine_fn = _refine_with_llm
            refine_label = "OpenAI"
        elif refinement_provider == "github":
            refine_fn = _refine_with_github
            refine_label = "GitHub Models"

    mode_labels = {
        "offline": "Argos (offline)",
        "online": "Google (online)",
        "hybrid": "Hybrid (Argos + Google)",
        "ai": f"AI ({config.AI_PROVIDER} / {config.AI_TRANSLATE_MODEL})",
    }
    mode_label = mode_labels.get(mode, mode)
    logger.info(f"Translation mode: {mode_label}")
    if refine_fn:
        logger.info(
            f"AI refinement: {refine_label} (model: {refinement_model or 'default'})"
        )

    # Pre-load Argos model if needed (so the first page doesn't stall)
    if mode in ("offline", "hybrid"):
        try:
            _ensure_argos_model()
        except Exception as e:
            logger.error(f"Failed to load Argos model: {e}")
            if mode == "offline":
                raise
            # In hybrid mode, fall back to online-only
            logger.warning("Falling back to Google-only translation")
            translate_fn = _translate_google

    results = []
    total = len(pages)

    for i, page in enumerate(pages):
        text = page["text"]
        page_num = page["page_number"]

        if not text.strip():
            results.append(
                TranslationResult(
                    page_number=page_num, original_text=text, translated_text=""
                )
            )
            continue

        if on_progress:
            on_progress(
                i + 1,
                total,
                f"Translating page {page_num} [{mode_label}]",
            )

        translated = translate_fn(text)

        refined = None
        if refine_fn:
            if on_progress:
                on_progress(
                    i + 1,
                    total,
                    f"Refining page {page_num} with {refine_label}",
                )
            refined = refine_fn(text, translated, model=refinement_model)

        results.append(
            TranslationResult(
                page_number=page_num,
                original_text=text,
                translated_text=translated,
                refined_text=refined,
            )
        )

        # Rate limit for AI mode
        if mode == "ai" and i < total - 1:
            time.sleep(config.AI_PAGE_DELAY)

    return results
