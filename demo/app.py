"""pullback live demo — FastAPI app with SSE streaming.

Run:
    set -a && source .env.local && set +a
    python demo/app.py
"""

from __future__ import annotations

import re
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pullback.api.deps import build_orchestrator  # noqa: E402
from pullback.settings import load_settings as _load_settings  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent))
from demo.stream import _run_stream  # noqa: E402

_settings = _load_settings()
_MAX_RESULTS = _settings.librarian.max_results

_STATIC_DIR = Path(__file__).parent / "static"
_DEMO_LOGS_DIR = Path(__file__).parent / "logs"

# Set up dedicated demo logging to capture query and config for each run
_DEMO_LOGS_DIR.mkdir(parents=True, exist_ok=True)
_demo_logger = logger.bind(component="demo")

app = FastAPI(title="The Pullback - Theorem Search")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/")
async def index():
    from fastapi.responses import FileResponse
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/stream")
async def stream(
    query: str = "Banach fixed point theorem",
    strictness: float = 0.0,
) -> StreamingResponse:
    # Set up per-run logging with timestamp
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_log_file = _DEMO_LOGS_DIR / f"run_{run_timestamp}.log"

    # Add a handler for this specific run (ERROR, WARNING, INFO, DEBUG and above)
    logger.add(
        str(run_log_file),
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {message}",
        backtrace=False,
        diagnose=False,
        enqueue=False,
    )

    # Log the query and configuration at the start of this run
    _demo_logger.info(f"query={query}")
    _demo_logger.info(f"strictness={strictness}")
    _demo_logger.info(f"max_results={_MAX_RESULTS}")

    return StreamingResponse(
        _run_stream(query, _MAX_RESULTS, strictness, build_orchestrator),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# PDF snippet rendering
# ---------------------------------------------------------------------------

_PDF_CACHE = Path("/tmp/mathgent_pdf_cache")
_PDF_CACHE.mkdir(exist_ok=True)


def _latex_to_search_text(latex: str, max_words: int = 6) -> str:
    """Extract plain English words from a LaTeX snippet for PDF text search."""
    text = re.sub(r'\\(?:begin|end)\{[^}]+\}(?:\[[^\]]*\])?', ' ', latex)
    text = re.sub(r'\\[a-zA-Z]+\{([^{}]*)\}', r' \1 ', text)  # keep braced content
    text = re.sub(r'\\[a-zA-Z@]+\*?', ' ', text)               # strip commands
    text = re.sub(r'\$+|\\\[|\\\]|\\\(|\\\)', ' ', text)       # strip math delimiters
    text = re.sub(r'[{}()\[\]_^~&%]', ' ', text)
    words = [w for w in text.split() if len(w) > 3 and w.isalpha()]
    return ' '.join(words[:max_words])


async def _fetch_pdf(arxiv_id: str) -> Path:
    safe = re.sub(r'[^A-Za-z0-9._-]', '_', arxiv_id)
    pdf_path = _PDF_CACHE / f"{safe}.pdf"
    if not pdf_path.exists():
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            r = await client.get(f"https://arxiv.org/pdf/{arxiv_id}")
            r.raise_for_status()
            pdf_path.write_bytes(r.content)
    return pdf_path


try:
    import fitz as _fitz  # PyMuPDF — loaded once at startup for fast response
    _FITZ_OK = True
except ImportError:
    _fitz = None  # type: ignore[assignment]
    _FITZ_OK = False
    print("WARNING: pymupdf not installed — PDF snippet rendering disabled. Run: uv pip install pymupdf")


@app.get("/pdf-snippet/{arxiv_id:path}")
async def pdf_snippet(arxiv_id: str, q: str = "") -> Response:
    """Return a PNG crop of the PDF region matching q (plain-text search key)."""
    if not _FITZ_OK:
        return Response(status_code=503, content=b"pymupdf not installed")
    if not q:
        return Response(status_code=400)

    try:
        pdf_path = await _fetch_pdf(arxiv_id)
    except Exception as exc:
        print(f"pdf_snippet: fetch failed for {arxiv_id}: {exc}")
        return Response(status_code=503)

    try:
        doc = _fitz.open(str(pdf_path))
    except Exception as exc:
        print(f"pdf_snippet: open failed for {arxiv_id}: {exc}")
        return Response(status_code=422)

    # Try all sliding windows of 3 then 2 consecutive words.
    # Prefix-only search fails when the first words don't appear together in the PDF
    # (e.g. "Banach theorem" vs "Banach's theorem"), but a later window like
    # "complete metric space" will reliably match.
    words = q.split()
    candidates: list[str] = []
    for n in (3, 2):
        for i in range(len(words) - n + 1):
            candidates.append(' '.join(words[i:i + n]))

    for phrase in candidates:
        for page in doc:
            rects = page.search_for(phrase, quads=False)
            if rects:
                r = rects[0]
                page_w = page.rect.width
                clip = _fitz.Rect(r.x0 - 40, r.y0 - 80, r.x0 + page_w, r.y1 + 220)
                clip = clip & page.rect
                pix = page.get_pixmap(clip=clip, dpi=150, colorspace=_fitz.csGRAY)
                return Response(content=pix.tobytes("png"), media_type="image/png")

    return Response(status_code=404)


if __name__ == "__main__":
    port = 7860
    url = f"http://localhost:{port}"
    print(f"\n  mathgent demo → {url}\n")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
