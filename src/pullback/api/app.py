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
        try:
            setup_observability()
        except Exception as e:
            import sys
            print(f"Warning: observability setup failed: {e}", file=sys.stderr)
        settings = load_settings()
        app.state.settings = settings
        try:
            orchestrator = build_orchestrator(settings)
            app.state.orchestrator = orchestrator
        except Exception as e:
            import sys
            print(f"Warning: orchestrator initialization failed: {e}", file=sys.stderr)
            app.state.orchestrator = None
        try:
            yield
        finally:
            orchestrator = getattr(app.state, "orchestrator", None)
            if orchestrator and hasattr(orchestrator, "close"):
                orchestrator.close()
    except Exception as e:
        import sys
        print(f"Warning: app initialization failed: {e}", file=sys.stderr)
        yield


def create_app() -> FastAPI:
    app = FastAPI(title="Pullback Lemma Search", lifespan=_lifespan)
    app.middleware("http")(request_context_middleware)
    app.include_router(router)
    return app


app = create_app()
