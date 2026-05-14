"""Dependency wiring for orchestrator, providers, sandbox, and forager components."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
import os
from pathlib import Path
from typing import TypeAlias

from fastapi import Request

from ..agents import ForagerAgent
from ..settings import AppSettings, load_settings
from ..observability import get_logger
from ..orchestration import LibrarianOrchestrator
from ..rerank import create_reranker
from ..sandbox import E2BSandboxRunner, LocalSandboxRunner, HybridSandboxRunner
from ..discovery import (
    ArxivAPIDiscoveryClient,
    ChainedDiscoveryClient,
    OpenRouterSearchDiscoveryClient,
    OpenAlexDiscoveryClient,
    ZbMathOpenDiscoveryClient,
    SemanticScholarDiscoveryClient,
    PaperMetadataFetcher,
    PaperDiscoveryClient,
    fetch_arxiv_metadata,
)

from ..tools import ExtractionTools

log = get_logger("api.deps")

DiscoveryProvider: TypeAlias = (
    OpenAlexDiscoveryClient | ZbMathOpenDiscoveryClient | OpenRouterSearchDiscoveryClient
 | ArxivAPIDiscoveryClient | SemanticScholarDiscoveryClient
)

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass(frozen=True)
class _RuntimeDeps:
    discovery_client: PaperDiscoveryClient
    extraction_tools: ExtractionTools
    forager: ForagerAgent
    metadata_fetcher: PaperMetadataFetcher | None


def _build_discovery_layer(settings: AppSettings) -> tuple[PaperDiscoveryClient, list[str]]:
    providers: list[tuple[str, DiscoveryProvider]] = []
    raw_only_provider_names: list[str] = []

    for name in settings.discovery.providers:
        if name == "openalex":
            providers.append(
                (
                    "openalex",
                    OpenAlexDiscoveryClient(
                        api_key=settings.discovery.openalex.api_key,
                        timeout_seconds=settings.discovery.timeout_seconds,
                        mailto=settings.discovery.openalex.mailto,
                    ),
                )
            )
            continue

        if name == "openrouter_search":
            if settings.discovery.openrouter_search.api_key:
                providers.append(
                    (
                        "openrouter_search",
                        OpenRouterSearchDiscoveryClient(
                            api_key=settings.discovery.openrouter_search.api_key,
                            model_name=settings.discovery.openrouter_search.model_name,
                            timeout_seconds=settings.discovery.timeout_seconds,
                            max_output_tokens=settings.discovery.openrouter_search.max_output_tokens,
                            base_url=_OPENROUTER_BASE_URL,
                        ),
                    )
                )
            else:
                log.warning("discovery.provider_skipped provider=openrouter_search reason=missing_OPENROUTER_API_KEY")
            continue

        if name == "openai_search":
            if settings.discovery.openai_search.api_key:
                _openai_model = settings.discovery.openai_search.model_name.removeprefix("openai:")
                providers.append(
                    (
                        "openai_search",
                        OpenRouterSearchDiscoveryClient(
                            api_key=settings.discovery.openai_search.api_key,
                            model_name=_openai_model,
                            timeout_seconds=settings.discovery.timeout_seconds,
                            max_output_tokens=settings.discovery.openai_search.max_output_tokens,
                            base_url=None,
                        ),
                    )
                )
            else:
                log.warning("discovery.provider_skipped provider=openai_search reason=missing_OPENAI_API_KEY")
            continue

        if name in {"arxiv_api", "arxiv"}:
            providers.append(
                (
                    "arxiv_api",
                    ArxivAPIDiscoveryClient(timeout_seconds=settings.discovery.timeout_seconds),
                )
            )
            continue

        if name in {"zbmath_open", "zbmath"}:
            providers.append(
                (
                    "zbmath_open",
                    ZbMathOpenDiscoveryClient(timeout_seconds=settings.discovery.timeout_seconds),
                )
            )
            continue

        if name == "semantic_scholar":
            providers.append(
                (
                    "semantic_scholar",
                    SemanticScholarDiscoveryClient(
                        api_key=settings.discovery.semantic_scholar.api_key or None,
                        timeout_seconds=settings.discovery.provider_timeout_seconds,
                    ),
                )
            )
            raw_only_provider_names.append("semantic_scholar")
            if not settings.discovery.semantic_scholar.api_key:
                log.warning("discovery.semantic_scholar running unauthenticated (rate-limited)")
            continue

        log.warning("discovery.provider_unknown provider={}", name)

    provider_timeout_seconds = settings.discovery.provider_timeout_seconds
    chain = ChainedDiscoveryClient(
        providers=providers,
        provider_timeout_seconds=provider_timeout_seconds,
        raw_only_providers=frozenset(raw_only_provider_names),
    )
    active = [name for name, _ in providers]
    log.info(
        "discovery.config discovery_providers={} active_providers={} timeout_s={:.1f}",
        settings.discovery.providers,
        active,
        provider_timeout_seconds,
    )
    return chain, active


def _build_extraction_tools(settings: AppSettings) -> ExtractionTools:
    local_dummy_dir = settings.sandbox.local_tex_dir
    if local_dummy_dir:
        paper_map = {p.stem: p for p in Path(local_dummy_dir).glob("*.tex") if p.is_file()}
        log.info("orchestrator.mode local_tex_dir={}", local_dummy_dir)
        return ExtractionTools(LocalSandboxRunner(paper_map=paper_map))

    log.info("orchestrator.mode hybrid (local cache + E2B fallback)")
    return ExtractionTools(HybridSandboxRunner())


def _build_forager(settings: AppSettings, extraction_tools: ExtractionTools) -> ForagerAgent:
    return ForagerAgent(
        reranker=create_reranker(
            settings.rerank.strategy,
            colbert_endpoint=settings.rerank.colbert_endpoint,
            bge_model=settings.rerank.bge_model,
            openrouter_model=settings.rerank.openrouter_model,
            api_key=settings.rerank.api_key,
        ),
        tools=extraction_tools,
        top_k_headers=settings.librarian.top_k_headers,
    )


def _build_runtime_deps(settings: AppSettings) -> _RuntimeDeps:
    discovery_client, active_providers = _build_discovery_layer(settings)
    if not active_providers:
        raise RuntimeError(
            "No discovery providers configured. Set PULLBACK_DISCOVERY_ORDER to include 'arxiv_api', "
            "or configure OPENALEX_API_KEY and/or OPENROUTER_API_KEY."
        )

    extraction_tools = _build_extraction_tools(settings)
    forager = _build_forager(settings, extraction_tools)

    metadata_fetcher: PaperMetadataFetcher | None
    if os.getenv("PULLBACK_DISABLE_METADATA_FETCH", "").strip().lower() in {"1", "true", "yes", "on"}:
        metadata_fetcher = None
    else:
        metadata_fetcher = partial(
            fetch_arxiv_metadata,
            timeout_seconds=settings.librarian.timeout_seconds,
        )
    return _RuntimeDeps(
        discovery_client=discovery_client,
        extraction_tools=extraction_tools,
        forager=forager,
        metadata_fetcher=metadata_fetcher,
    )


def build_orchestrator(settings: AppSettings | None = None) -> LibrarianOrchestrator:
    resolved_settings = settings or load_settings()
    log.info(
        "orchestrator.config agentic={} timeout_s={:.2f} max_query_attempts={} max_replan_rounds={} delegate_concurrency={} top_k_headers={}",
        resolved_settings.librarian.agentic,
        resolved_settings.librarian.timeout_seconds,
        resolved_settings.librarian.max_query_attempts,
        resolved_settings.librarian.max_replan_rounds,
        resolved_settings.librarian.delegate_concurrency,
        resolved_settings.librarian.top_k_headers,
    )

    deps = _build_runtime_deps(resolved_settings)

    return LibrarianOrchestrator(
        discovery_client=deps.discovery_client,
        metadata_fetcher=deps.metadata_fetcher,
        forager=deps.forager,
        tools=deps.extraction_tools,
        model_name=resolved_settings.librarian.model_name,
        delegate_concurrency=resolved_settings.librarian.delegate_concurrency,
        top_k_headers=resolved_settings.librarian.top_k_headers,
        agentic=resolved_settings.librarian.agentic,
        max_query_attempts=resolved_settings.librarian.max_query_attempts,
        max_replan_rounds=resolved_settings.librarian.max_replan_rounds,
        timeout_seconds=resolved_settings.librarian.timeout_seconds,
        query_planner_model_name=resolved_settings.librarian.query_planner_model_name,
        ranking_strategy=resolved_settings.rerank.strategy,
    )


def get_orchestrator(request: Request) -> LibrarianOrchestrator | None:
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        try:
            orchestrator = build_orchestrator(getattr(request.app.state, "settings", None))
            request.app.state.orchestrator = orchestrator
        except Exception as e:
            log.error("orchestrator.build_failed_on_request error={}", e)
            return None
    return orchestrator
