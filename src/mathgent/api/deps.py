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
from ..sandbox import E2BSandboxRunner, LocalSandboxRunner
from ..discovery import (
    ChainedDiscoveryClient,
    OpenAISearchDiscoveryClient,
    OpenAlexDiscoveryClient,
    PaperMetadataFetcher,
    PaperDiscoveryClient,
    fetch_arxiv_metadata,
)
from ..tools import DiscoveryTools, ExtractionTools

log = get_logger("api.deps")
DiscoveryProvider: TypeAlias = OpenAlexDiscoveryClient | OpenAISearchDiscoveryClient


@dataclass(frozen=True)
class _RuntimeDeps:
    discovery_client: PaperDiscoveryClient
    discovery_tools: DiscoveryTools
    extraction_tools: ExtractionTools
    forager: ForagerAgent
    metadata_fetcher: PaperMetadataFetcher | None


def _build_discovery_layer(settings: AppSettings) -> tuple[PaperDiscoveryClient, DiscoveryTools, list[str]]:
    providers: list[tuple[str, DiscoveryProvider]] = []

    for name in settings.discovery.provider_order:
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

        if name == "openai_search":
            if settings.discovery.openai_search.api_key:
                providers.append(
                    (
                        "openai_search",
                        OpenAISearchDiscoveryClient(
                            api_key=settings.discovery.openai_search.api_key,
                            model_name=settings.discovery.openai_search.model_name,
                            timeout_seconds=settings.discovery.timeout_seconds,
                            max_output_tokens=settings.discovery.openai_search.max_output_tokens,
                        ),
                    )
                )
            else:
                log.warning("discovery.provider_skipped provider=openai_search reason=missing_OPENAI_API_KEY")
            continue

        log.warning("discovery.provider_unknown provider={}", name)

    provider_timeout_seconds = settings.discovery.timeout_seconds
    chain = ChainedDiscoveryClient(
        providers=providers,
        provider_timeout_seconds=provider_timeout_seconds,
    )
    active = [name for name, _ in providers]
    log.info(
        "discovery.config provider_order={} active_providers={} timeout_s={:.1f}",
        settings.discovery.provider_order,
        active,
        provider_timeout_seconds,
    )
    return chain, DiscoveryTools(chain=chain), active


def _build_extraction_tools(settings: AppSettings) -> ExtractionTools:
    local_dummy_dir = settings.sandbox.local_tex_dir
    if local_dummy_dir:
        paper_map = {p.stem: p for p in Path(local_dummy_dir).glob("*.tex") if p.is_file()}
        log.info("orchestrator.mode local_tex_dir={}", local_dummy_dir)
        return ExtractionTools(LocalSandboxRunner(paper_map=paper_map))

    log.info("orchestrator.mode e2b")
    return ExtractionTools(E2BSandboxRunner.create())


def _build_forager(settings: AppSettings, extraction_tools: ExtractionTools) -> ForagerAgent:
    return ForagerAgent(
        reranker=create_reranker(
            settings.rerank.strategy,
            colbert_endpoint=settings.rerank.colbert_endpoint,
            bge_model=settings.rerank.bge_model,
        ),
        tools=extraction_tools,
        top_k_headers=settings.librarian.top_k_headers,
    )


def _build_runtime_deps(settings: AppSettings) -> _RuntimeDeps:
    discovery_client, discovery_tools, active_providers = _build_discovery_layer(settings)
    if not active_providers:
        raise RuntimeError("No discovery providers configured. Set OPENALEX_API_KEY and/or OPENAI_API_KEY.")

    extraction_tools = _build_extraction_tools(settings)
    forager = _build_forager(settings, extraction_tools)

    metadata_fetcher: PaperMetadataFetcher | None
    if os.getenv("MATHGENT_DISABLE_METADATA_FETCH", "").strip().lower() in {"1", "true", "yes", "on"}:
        metadata_fetcher = None
    else:
        metadata_fetcher = partial(
            fetch_arxiv_metadata,
            timeout_seconds=settings.librarian.timeout_seconds,
        )
    return _RuntimeDeps(
        discovery_client=discovery_client,
        discovery_tools=discovery_tools,
        extraction_tools=extraction_tools,
        forager=forager,
        metadata_fetcher=metadata_fetcher,
    )


def build_orchestrator(settings: AppSettings | None = None) -> LibrarianOrchestrator:
    resolved_settings = settings or load_settings()
    log.info(
        "orchestrator.config agentic_query_loop={} agentic_discovery={} timeout_s={:.2f} max_query_attempts={} max_replan_rounds={} delegate_concurrency={} top_k_headers={}",
        resolved_settings.librarian.agentic_query_loop,
        resolved_settings.librarian.agentic_discovery,
        resolved_settings.librarian.timeout_seconds,
        resolved_settings.librarian.max_query_attempts,
        resolved_settings.librarian.max_replan_rounds,
        resolved_settings.librarian.delegate_concurrency,
        resolved_settings.librarian.top_k_headers,
    )

    deps = _build_runtime_deps(resolved_settings)

    return LibrarianOrchestrator(
        discovery_client=deps.discovery_client,
        discovery_tools=deps.discovery_tools,
        metadata_fetcher=deps.metadata_fetcher,
        forager=deps.forager,
        tools=deps.extraction_tools,
        model_name=resolved_settings.librarian.model_name,
        delegate_concurrency=resolved_settings.librarian.delegate_concurrency,
        top_k_headers=resolved_settings.librarian.top_k_headers,
        agentic_query_loop=resolved_settings.librarian.agentic_query_loop,
        agentic_discovery=resolved_settings.librarian.agentic_discovery,
        max_query_attempts=resolved_settings.librarian.max_query_attempts,
        max_replan_rounds=resolved_settings.librarian.max_replan_rounds,
        timeout_seconds=resolved_settings.librarian.timeout_seconds,
        query_planner_model_name=resolved_settings.librarian.query_planner_model_name,
    )


def get_orchestrator(request: Request) -> LibrarianOrchestrator:
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        orchestrator = build_orchestrator(getattr(request.app.state, "settings", None))
        request.app.state.orchestrator = orchestrator
    return orchestrator
