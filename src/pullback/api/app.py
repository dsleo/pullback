"""FastAPI app factory and lifespan wiring for Pullback."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse

from ..settings import load_settings
from ..observability import setup_observability
from .deps import build_orchestrator
from .middleware import request_context_middleware
from .routes import router


async def _error_stream(message: str):
    """Generate an SSE error event."""
    yield f"event: error\ndata: {message}\n\n"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    try:
        try:
            setup_observability()
        except Exception as e:
            import sys
            print(f"Warning: observability setup failed: {e}", file=sys.stderr)
        try:
            settings = load_settings()
            app.state.settings = settings
        except Exception as e:
            # On serverless, settings loading may fail
            import sys
            print(f"Warning: settings loading failed: {e}", file=sys.stderr)
            app.state.settings = None
            app.state.orchestrator = None
            yield
            return
        try:
            orchestrator = build_orchestrator(settings)
            app.state.orchestrator = orchestrator
        except Exception as e:
            # On serverless, orchestrator initialization may fail
            # Log but don't crash — requests will fail gracefully
            import sys
            print(f"Warning: orchestrator initialization failed: {e}", file=sys.stderr)
            app.state.orchestrator = None
        try:
            yield
        finally:
            if app.state.orchestrator and hasattr(app.state.orchestrator, "close"):
                app.state.orchestrator.close()
    except Exception as e:
        # Catch any remaining exceptions to ensure app always yields
        import sys
        print(f"Warning: unexpected error in lifespan: {e}", file=sys.stderr)
        yield


def create_app() -> FastAPI:
    app = FastAPI(title="Pullback Lemma Search", lifespan=_lifespan)
    app.middleware("http")(request_context_middleware)

    # Find and mount static files (demo UI)
    static_dir = Path(__file__).parent.parent.parent.parent / "demo" / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        # Serve index.html at root
        @app.get("/")
        async def root():
            index_path = static_dir / "index.html"
            if index_path.exists():
                return FileResponse(str(index_path))
            return {
                "service": "Pullback Lemma Search",
                "version": "0.1.0",
                "docs": "/docs",
                "search": "/search"
            }
    else:
        # Fallback if demo directory not found
        @app.get("/")
        async def root():
            return {
                "service": "Pullback Lemma Search",
                "version": "0.1.0",
                "docs": "/docs",
                "search": "/search"
            }

    # Health check endpoint (doesn't require orchestrator)
    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "Pullback Lemma Search"}

    # Stream endpoint for demo UI (SSE streaming)
    @app.get("/stream")
    async def stream(
        query: str = Query("Banach fixed point theorem"),
        strictness: float = Query(0.0),
    ):
        try:
            import sys
            from datetime import datetime
            from loguru import logger

            # Ensure demo module can be imported
            repo_root = Path(__file__).parent.parent.parent.parent
            demo_path = repo_root / "demo"
            if str(repo_root) not in sys.path:
                sys.path.insert(0, str(repo_root))

            from demo.stream import _run_stream  # type: ignore[import]

            settings = app.state.settings
            if not settings:
                return StreamingResponse(
                    _error_stream("Service not configured"),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )

            max_results = settings.librarian.max_results
            demo_logger = logger.bind(component="stream")

            # Per-run logging (use /tmp on serverless, demo/logs locally)
            run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            try:
                demo_logs_dir = demo_path / "logs"
                demo_logs_dir.mkdir(parents=True, exist_ok=True)
                run_log_file = demo_logs_dir / f"run_{run_timestamp}.log"
            except (OSError, PermissionError):
                # Fall back to /tmp on serverless
                demo_logs_dir = Path("/tmp/pullback_logs")
                demo_logs_dir.mkdir(parents=True, exist_ok=True)
                run_log_file = demo_logs_dir / f"run_{run_timestamp}.log"

            try:
                logger.add(
                    str(run_log_file),
                    level="DEBUG",
                    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {message}",
                    backtrace=False,
                    diagnose=False,
                    enqueue=False,
                )
            except Exception:
                # Logging failures should not block streaming
                pass

            demo_logger.info(f"query={query}")
            demo_logger.info(f"strictness={strictness}")
            demo_logger.info(f"max_results={max_results}")

            return StreamingResponse(
                _run_stream(query, max_results, strictness, build_orchestrator),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        except ImportError as e:
            import sys
            print(f"Warning: demo streaming import failed: {e}", file=sys.stderr)
            return StreamingResponse(
                _error_stream("Demo streaming not available"),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

    app.include_router(router)
    return app


app = create_app()
