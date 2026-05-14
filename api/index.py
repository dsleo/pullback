"""Vercel API entrypoint for the Pullback demo."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from loguru import logger

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from demo.stream import _run_stream
from pullback.api.deps import build_orchestrator
from pullback.settings import load_settings

_settings = load_settings()
_MAX_RESULTS = _settings.librarian.max_results

app = FastAPI(title="The Pullback - Theorem Search")


@app.get("/")
async def root() -> JSONResponse:
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
