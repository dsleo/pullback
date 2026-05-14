"""Vercel entrypoint for the mathgent demo (FastAPI + SSE).

Vercel will detect a FastAPI instance named `app` in `app.py`.

Static assets must live in `public/**` and are served by Vercel's CDN.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent / "src"))

from demo.stream import _run_stream
from mathgent.api.deps import build_orchestrator
from mathgent.settings import load_settings

_settings = load_settings()
_MAX_RESULTS = _settings.librarian.max_results

_PUBLIC_DIR = Path(__file__).parent / "public"

app = FastAPI(title="mathgent demo")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(_PUBLIC_DIR / "index.html")


@app.get("/app.js", include_in_schema=False)
async def app_js() -> FileResponse:
    return FileResponse(_PUBLIC_DIR / "app.js")


@app.get("/style.css", include_in_schema=False)
async def style_css() -> FileResponse:
    return FileResponse(_PUBLIC_DIR / "style.css")


@app.get("/stream")
async def stream(
    query: str = "Banach fixed point theorem",
    strictness: float = 0.0,
) -> StreamingResponse:
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger.bind(component="demo").info(f"query={query} strictness={strictness} run={run_timestamp}")

    return StreamingResponse(
        _run_stream(query, _MAX_RESULTS, strictness, build_orchestrator),
        media_type="text/event-stream",
        headers={
            # Avoid buffering so the browser receives events immediately.
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
