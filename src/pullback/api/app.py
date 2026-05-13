"""FastAPI app factory and lifespan wiring for Pullback."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from ..settings import load_settings
from ..observability import setup_observability
from .deps import build_orchestrator
from .middleware import request_context_middleware
from .routes import router


@asynccontextmanager
async def _lifespan(app: FastAPI):
    try:
        setup_observability()
        settings = load_settings()
        app.state.settings = settings
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
        # If settings can't load, app is non-functional but should at least start
        import sys
        print(f"Warning: app initialization failed: {e}", file=sys.stderr)
        yield


def create_app() -> FastAPI:
    app = FastAPI(title="Pullback Lemma Search", lifespan=_lifespan)
    app.middleware("http")(request_context_middleware)

    # Health check endpoint (doesn't require orchestrator)
    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "Pullback Lemma Search"}

    app.include_router(router)
    return app


app = create_app()
