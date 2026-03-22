"""Dependency wiring for orchestrator, providers, sandbox, and forager components."""

from __future__ import annotations

from pathlib import Path
from typing import TypeAlias

from fastapi import Request

from ..agents import ForagerAgent, HeuristicHeaderSelector, LLMHeaderSelector
from ..settings import AppSettings, load_settings
from ..observability import get_logger
from ..orchestration import LibrarianOrchestrator
from ..rerank import create_reranker
from ..sandbox import E2BSandboxRunner, LocalSandboxRunner
from ..discovery import (
    ArxivMetadataClient,
    ChainedDiscoveryClient,
    ExaDiscoveryClient,
    OpenAlexDiscoveryClient,
    RetryConfig,
)
from ..discovery.arxiv_title_resolver import ArxivTitleResolver
from ..tools import DiscoveryTools, ExtractionTools

log = get_logger("api.deps")
DiscoveryProvider: TypeAlias = OpenAlexDiscoveryClient | ExaDiscoveryClient


def _build_discovery_clients(
    settings: AppSettings,
) -> tuple[list[tuple[str, DiscoveryProvider]], DiscoveryTools]:
    providers: list[tuple[str, DiscoveryProvider]] = []
    openalex_client: OpenAlexDiscoveryClient | None = None
    exa_client: ExaDiscoveryClient | None = None
    retry_cfg = RetryConfig(
        max_retries=settings.discovery.retry.max_retries,
        base_backoff_seconds=settings.discovery.retry.base_backoff_seconds,
    )

    for name in settings.discovery.provider_order:
        if name == "openalex":
            resolver = ArxivTitleResolver(
                query_max_results=settings.discovery.openalex.arxiv_query_max_results,
                delay_seconds=settings.discovery.openalex.arxiv_resolver_delay_seconds,
                title_match_threshold=settings.discovery.openalex.title_match_threshold,
            )
            openalex_client = OpenAlexDiscoveryClient(
                api_key=settings.discovery.openalex.api_key,
                timeout_seconds=settings.discovery.openalex.timeout_seconds,
                retry=retry_cfg,
                title_resolution_enabled=settings.discovery.openalex.title_resolution_enabled,
                max_title_resolutions=settings.discovery.openalex.max_title_resolutions,
                mailto=settings.discovery.openalex.mailto,
                title_resolver=resolver,
            )
            providers.append(("openalex", openalex_client))
            continue

        if name == "exa":
            if settings.discovery.exa.api_key:
                exa_client = ExaDiscoveryClient(
                    api_key=settings.discovery.exa.api_key,
                    timeout_seconds=settings.discovery.exa.timeout_seconds,
                    retry=retry_cfg,
                )
                providers.append(("exa", exa_client))
            else:
                log.warning("discovery.provider_skipped provider=exa reason=missing_EXA_API_KEY")
            continue

        log.warning("discovery.provider_unknown provider={}", name)

    chain = ChainedDiscoveryClient(
        providers=providers,
        provider_timeout_seconds=settings.discovery.provider_timeout_seconds,
    )
    log.info(
        "discovery.config provider_order={} active_providers={} provider_timeout_s={:.1f} openalex_timeout_s={:.1f} exa_timeout_s={:.1f}",
        settings.discovery.provider_order,
        [name for name, _ in providers],
        settings.discovery.provider_timeout_seconds,
        settings.discovery.openalex.timeout_seconds,
        settings.discovery.exa.timeout_seconds,
    )
    return providers, DiscoveryTools(chain=chain, openalex=openalex_client, exa=exa_client)


def build_orchestrator(settings: AppSettings | None = None) -> LibrarianOrchestrator:
    resolved_settings = settings or load_settings()
    log.info(
        "orchestrator.config agentic_query_loop={} agentic_discovery={} query_planner_timeout_s={:.2f} max_query_attempts={} max_replan_rounds={} delegate_concurrency={}",
        resolved_settings.librarian.agentic_query_loop,
        resolved_settings.librarian.agentic_discovery,
        resolved_settings.librarian.query_planner_timeout_seconds,
        resolved_settings.librarian.max_query_attempts,
        resolved_settings.librarian.max_replan_rounds,
        resolved_settings.librarian.delegate_concurrency,
    )

    providers, discovery_tools = _build_discovery_clients(resolved_settings)
    if not providers:
        raise RuntimeError("No discovery providers configured. Set OPENALEX_API_KEY and/or EXA_API_KEY.")

    discovery_client = discovery_tools.chain

    local_dummy_dir = resolved_settings.sandbox.local_tex_dir
    if local_dummy_dir:
        paper_map = {p.stem: p for p in Path(local_dummy_dir).glob("*.tex") if p.is_file()}
        log.info("orchestrator.mode local_tex_dir={}", local_dummy_dir)
        tools = ExtractionTools(LocalSandboxRunner(paper_map=paper_map))
    else:
        log.info("orchestrator.mode e2b")
        tools = ExtractionTools(E2BSandboxRunner.create())

    header_selector = (
        LLMHeaderSelector(
            model_name=resolved_settings.forager.model_name,
            request_limit=resolved_settings.forager.selector_request_limit,
            total_tokens_limit=resolved_settings.forager.selector_total_tokens_limit,
        )
        if resolved_settings.forager.use_llm_header_pick
        else HeuristicHeaderSelector()
    )
    forager = ForagerAgent(
        reranker=create_reranker(
            resolved_settings.rerank.strategy,
            colbert_endpoint=resolved_settings.rerank.colbert_endpoint,
            bge_model=resolved_settings.rerank.bge_model,
        ),
        tools=tools,
        header_selector=header_selector,
    )

    return LibrarianOrchestrator(
        discovery_client=discovery_client,
        discovery_tools=discovery_tools,
        metadata_client=ArxivMetadataClient(),
        forager=forager,
        tools=tools,
        model_name=resolved_settings.librarian.model_name,
        candidate_multiplier=resolved_settings.librarian.candidate_multiplier,
        candidate_cap=resolved_settings.librarian.candidate_cap,
        delegate_concurrency=resolved_settings.librarian.delegate_concurrency,
        early_stop_on_matches=resolved_settings.librarian.early_stop_on_matches,
        agentic_query_loop=resolved_settings.librarian.agentic_query_loop,
        agentic_discovery=resolved_settings.librarian.agentic_discovery,
        max_query_attempts=resolved_settings.librarian.max_query_attempts,
        max_replan_rounds=resolved_settings.librarian.max_replan_rounds,
        query_planner_timeout_seconds=resolved_settings.librarian.query_planner_timeout_seconds,
        query_planner_model_name=resolved_settings.librarian.query_planner_model_name,
        planner_request_limit=resolved_settings.librarian.planner_request_limit,
        planner_total_tokens_limit=resolved_settings.librarian.planner_total_tokens_limit,
        discovery_request_limit=resolved_settings.librarian.discovery_request_limit,
        discovery_total_tokens_limit=resolved_settings.librarian.discovery_total_tokens_limit,
    )


def get_orchestrator(request: Request) -> LibrarianOrchestrator:
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        orchestrator = build_orchestrator(getattr(request.app.state, "settings", None))
        request.app.state.orchestrator = orchestrator
    return orchestrator
