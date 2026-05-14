"""HTTP routes for lemma search API endpoints and error mapping."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..models import SearchRequest, SearchResponse
from ..observability import get_logger
from ..orchestration import LibrarianOrchestrator
from ..discovery import DiscoveryAccessError
from .deps import get_orchestrator

log = get_logger("api")
router = APIRouter()
PROVIDER_UNAVAILABLE = "provider unavailable"
SOURCE_UNAVAILABLE = "source unavailable"
INTERNAL_ERROR = "internal error"


def _runtime_error_message(exc: RuntimeError) -> str:
    lowered = str(exc).lower()
    if any(token in lowered for token in ("latex", "source", "resolve", "arxiv", "sandbox")):
        return SOURCE_UNAVAILABLE
    return INTERNAL_ERROR


@router.post("/search", response_model=SearchResponse)
async def search_lemmas(
    request: SearchRequest,
    orchestrator: LibrarianOrchestrator = Depends(get_orchestrator),
) -> SearchResponse:
    if orchestrator is None:
        raise HTTPException(
            status_code=503,
            detail="Service temporarily unavailable: orchestrator not initialized"
        )
    log.info(
        "search.request query={} max_results={} strictness={}",
        request.query,
        request.max_results,
        request.strictness,
    )
    try:
        result = await orchestrator.search(
            query=request.query,
            max_results=request.max_results,
            strictness=request.strictness,
        )
        log.info("search.response results={}", len(result.results))
        return result
    except DiscoveryAccessError as exc:
        log.opt(exception=exc).error(
            "search.error kind=provider_unavailable error_type={} error_repr={}",
            type(exc).__name__,
            repr(exc),
        )
        raise HTTPException(status_code=502, detail=PROVIDER_UNAVAILABLE) from exc
    except RuntimeError as exc:
        detail = _runtime_error_message(exc)
        log.opt(exception=exc).error(
            "search.error kind=runtime detail={} error_type={} error_repr={}",
            detail,
            type(exc).__name__,
            repr(exc),
        )
        raise HTTPException(status_code=500, detail=detail) from exc
    except Exception as exc:  # pragma: no cover - safety net
        log.opt(exception=exc).error(
            "search.error kind=unexpected error_type={} error_repr={}",
            type(exc).__name__,
            repr(exc),
        )
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR) from exc
