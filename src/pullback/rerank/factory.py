"""Factory for selecting reranker strategy from configuration."""

from __future__ import annotations

from ..observability import get_logger
from .backends import (
    BGEReranker,
    BiEncoderReranker,
    FilteredReranker,
    HybridReranker,
    LLMReranker,
    ModernColBERTReranker,
    OpenRouterReranker,
    TokenOverlapReranker,
    OpenAIEmbeddingReranker,
)
from .base import Reranker

log = get_logger("rerank")


def create_reranker(
    strategy: str = "auto",
    *,
    colbert_endpoint: str | None = None,
    bge_model: str | None = None,
    biencoder_model: str | None = None,
    openrouter_model: str | None = None,
    api_key: str | None = None,
    top_k_filter: int | None = None,
    min_overlap: float = 0.01,
) -> Reranker:
    # Apply defaults for None values
    colbert_endpoint = colbert_endpoint or "http://127.0.0.1:8001/rerank"
    bge_model = bge_model or "BAAI/bge-reranker-v2-m3"
    biencoder_model = biencoder_model or "all-MiniLM-L6-v2"
    openrouter_model = openrouter_model or "cohere/rerank-v3.5"

    selected = strategy.lower().strip()

    if selected == "token":
        log.info("create strategy=token")
        return TokenOverlapReranker()
    if selected == "hybrid_token_openai":
        log.info("create strategy=hybrid_token_openai (resolving forager to token)")
        # In this mode, the forager just does token filtering.
        # The orchestrator handles the global semantic pass.
        return TokenOverlapReranker()
    if selected == "openai":
        log.info("create strategy=openai")
        return OpenAIEmbeddingReranker(api_key=api_key)
    if selected == "colbert":
        log.info("create strategy=colbert endpoint={}", colbert_endpoint)
        return ModernColBERTReranker(endpoint=colbert_endpoint)
    if selected == "bge":
        log.info("create strategy=bge model={}", bge_model)
        return BGEReranker(model_name=bge_model)
    if selected == "biencoder":
        log.info("create strategy=biencoder model={}", biencoder_model)
        return BiEncoderReranker(model_name=biencoder_model)
    if selected == "hybrid":
        log.info("create strategy=hybrid model={}", bge_model)
        return HybridReranker(
            fast=TokenOverlapReranker(),
            slow=BGEReranker(model_name=bge_model),
            min_overlap=min_overlap,
        )
    if selected in {"llm", "llm_openrouter"}:
        log.info("create strategy=llm model={}", openrouter_model)
        inner: Reranker = LLMReranker(model_name=openrouter_model, api_key=api_key)
        if top_k_filter:
            log.info("create strategy=llm_filtered top_k={}", top_k_filter)
            return FilteredReranker(fast=TokenOverlapReranker(), slow=inner, top_k=top_k_filter)
        return inner
    if selected in {"openrouter", "cohere", "hybrid_openrouter"}:
        log.info("create strategy={} model={}", selected, openrouter_model)
        inner: Reranker = OpenRouterReranker(model_name=openrouter_model, api_key=api_key)

        # If hybrid_openrouter, we add a token overlap filter before the slow reranker
        if selected == "hybrid_openrouter":
            inner = HybridReranker(
                fast=TokenOverlapReranker(), slow=inner, min_overlap=min_overlap
            )

        if top_k_filter:
            log.info("create strategy=filtered_openrouter top_k={}", top_k_filter)
            return FilteredReranker(fast=TokenOverlapReranker(), slow=inner, top_k=top_k_filter)
        return inner

    if selected != "auto":
        log.warning("create unknown_strategy={} fallback=auto", selected)

    try:
        log.info("create strategy=auto resolved=hybrid model={}", bge_model)
        return HybridReranker(
            fast=TokenOverlapReranker(),
            slow=BGEReranker(model_name=bge_model),
            min_overlap=min_overlap,
        )
    except Exception:
        log.info("create strategy=auto resolved=token_fallback")
        return TokenOverlapReranker()

