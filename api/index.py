"""Vercel API entrypoint for the Pullback demo."""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from loguru import logger

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from api.embedded_assets import APP_JS, INDEX_HTML, STYLE_CSS
from demo.stream import _run_stream
from pullback.api.deps import build_orchestrator
from pullback.settings import load_settings

_settings = load_settings()
_MAX_RESULTS = _settings.librarian.max_results
_PUBLIC_DIR = REPO_ROOT / "public"
_ASSET_VERSION = (os.getenv("VERCEL_GIT_COMMIT_SHA") or os.getenv("VERCEL_URL") or "dev")[:12]
_NO_STORE = {"Cache-Control": "no-store, max-age=0"}

app = FastAPI(title="The Pullback - Theorem Search")


def _with_asset_version(html: str) -> str:
    return (
        html
        .replace("/style.css", f"/style.css?v={_ASSET_VERSION}")
        .replace("/app.js", f"/app.js?v={_ASSET_VERSION}")
    )


@app.get("/")
async def root() -> HTMLResponse:
    index_path = _PUBLIC_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(_with_asset_version(index_path.read_text(encoding="utf-8")), headers=_NO_STORE)
    return HTMLResponse(_with_asset_version(INDEX_HTML), headers=_NO_STORE)


@app.get("/app.js")
async def app_js() -> Response:
    app_js_path = _PUBLIC_DIR / "app.js"
    if app_js_path.exists():
        return Response(app_js_path.read_text(encoding="utf-8"), media_type="text/javascript", headers=_NO_STORE)
    return Response(APP_JS, media_type="text/javascript", headers=_NO_STORE)


@app.get("/style.css")
async def style_css() -> Response:
    style_path = _PUBLIC_DIR / "style.css"
    if style_path.exists():
        return Response(style_path.read_text(encoding="utf-8"), media_type="text/css", headers=_NO_STORE)
    return Response(STYLE_CSS, media_type="text/css", headers=_NO_STORE)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({"ok": True, "service": "pullback-api"})


async def _stream_impl(
    query: str = "Banach fixed point theorem",
    strictness: float = 0.0,
) -> StreamingResponse:
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger.bind(component="demo").info(f"query={query} strictness={strictness} run={run_timestamp}")

    return StreamingResponse(
        _run_stream(query, _MAX_RESULTS, strictness, build_orchestrator),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/stream")
async def stream(
    query: str = "Banach fixed point theorem",
    strictness: float = 0.0,
) -> StreamingResponse:
    return await _stream_impl(query=query, strictness=strictness)


@app.get("/api/stream")
async def api_stream(
    query: str = "Banach fixed point theorem",
    strictness: float = 0.0,
) -> StreamingResponse:
    return await _stream_impl(query=query, strictness=strictness)
