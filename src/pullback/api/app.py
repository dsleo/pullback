"""FastAPI app factory and lifespan wiring for Pullback."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from ..settings import load_settings
from ..observability import setup_observability
from .deps import build_orchestrator
from .middleware import request_context_middleware
from .routes import router


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

    app.include_router(router)
    return app


app = create_app()
