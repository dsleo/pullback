"""mathgent live demo — FastAPI app with SSE streaming.

Run:
    set -a && source .env.local && set +a
    python demo/app.py
"""

from __future__ import annotations

import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mathgent.api.deps import build_orchestrator  # noqa: E402
from mathgent.settings import load_settings as _load_settings  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent))
from demo.stream import _run_stream  # noqa: E402

_settings = _load_settings()
_MAX_RESULTS = _settings.librarian.max_results

_STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="mathgent demo")
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
    return StreamingResponse(
        _run_stream(query, _MAX_RESULTS, strictness, build_orchestrator),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    port = 7860
    url = f"http://localhost:{port}"
    print(f"\n  mathgent demo → {url}\n")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
