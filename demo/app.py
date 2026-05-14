"""pullback live demo — FastAPI app with SSE streaming.

Run:
    set -a && source .env.local && set +a
    python demo/app.py
"""

from __future__ import annotations

import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
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


if __name__ == "__main__":
    port = 7860
    url = f"http://localhost:{port}"
    print(f"\n  The Pullback → {url}\n")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
