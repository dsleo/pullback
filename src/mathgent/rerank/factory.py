"""Factory for selecting reranker strategy from configuration."""

from __future__ import annotations

from ..observability import get_logger
from .backends import BGEReranker, ModernColBERTReranker, TokenOverlapReranker
from .base import Reranker

log = get_logger("rerank")


def create_reranker(
    strategy: str = "auto",
    *,
    colbert_endpoint: str = "http://127.0.0.1:8001/rerank",
    bge_model: str = "BAAI/bge-reranker-v2-m3",
) -> Reranker:
    selected = strategy.lower().strip()

    if selected == "token":
        log.info("create strategy=token")
        return TokenOverlapReranker()
    if selected == "colbert":
        log.info("create strategy=colbert endpoint={}", colbert_endpoint)
        return ModernColBERTReranker(endpoint=colbert_endpoint)
    if selected == "bge":
        log.info("create strategy=bge model={}", bge_model)
        return BGEReranker(model_name=bge_model)

    if selected != "auto":
        log.warning("create unknown_strategy={} fallback=auto", selected)

    try:
        log.info("create strategy=auto resolved=bge model={}", bge_model)
        return BGEReranker(model_name=bge_model)
    except RuntimeError:
        log.info("create strategy=auto resolved=token_fallback")
        return TokenOverlapReranker()
