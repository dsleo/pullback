"""FastAPI app factory and lifespan wiring for Mathgent."""

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
    setup_observability()
    settings = load_settings()
    app.state.settings = settings
    orchestrator = build_orchestrator(settings)
    app.state.orchestrator = orchestrator
    try:
        yield
    finally:
        if hasattr(orchestrator, "close"):
            orchestrator.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Mathgent Lemma Search", lifespan=_lifespan)
    app.middleware("http")(request_context_middleware)
    app.include_router(router)
    return app


app = create_app()
