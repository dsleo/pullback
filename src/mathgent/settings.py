"""Config-backed settings for discovery, orchestration, and reranking."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from .config import get_config


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_optional_bool(name: str) -> bool | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if minimum is not None:
        return max(value, minimum)
    return value


def _env_float(name: str, default: float, *, minimum: float | None = None) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if minimum is not None:
        return max(value, minimum)
    return value


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None:
        return default
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or default


@dataclass(frozen=True)
class OpenAlexSettings:
    api_key: str | None
    mailto: str | None


@dataclass(frozen=True)
class OpenRouterSearchSettings:
    api_key: str | None
    model_name: str = "openai/gpt-4o-mini"
    max_output_tokens: int = 400


@dataclass(frozen=True)
class OpenAISearchSettings:
    api_key: str | None
    model_name: str = "gpt-4o-mini"
    max_output_tokens: int = 400


@dataclass(frozen=True)
class SemanticScholarSettings:
    api_key: str | None


@dataclass(frozen=True)
class DiscoverySettings:
    providers: list[str]
    timeout_seconds: float
    provider_timeout_seconds: float  # per-provider timeout, all run in parallel
    openalex: OpenAlexSettings
    openrouter_search: OpenRouterSearchSettings
    openai_search: OpenAISearchSettings
    semantic_scholar: SemanticScholarSettings


@dataclass(frozen=True)
class SandboxSettings:
    local_tex_dir: Path | None


@dataclass(frozen=True)
class LibrarianSettings:
    model_name: str
    delegate_concurrency: int
    top_k_headers: int
    agentic: bool
    max_query_attempts: int
    max_replan_rounds: int
    timeout_seconds: float
    query_planner_model_name: str | None


@dataclass(frozen=True)
class RerankSettings:
    strategy: str
    colbert_endpoint: str | None
    bge_model: str | None
    openrouter_model: str | None
    api_key: str | None


@dataclass(frozen=True)
class AppSettings:
    discovery: DiscoverySettings
    sandbox: SandboxSettings
    librarian: LibrarianSettings
    rerank: RerankSettings


def load_settings() -> AppSettings:
    cfg = get_config()

    # Retrieval settings
    timeout_seconds = cfg["execution"]["timeout_seconds"]
    providers = list(cfg["retrieval"]["discovery_providers"])

    provider_timeout_seconds = float(
        cfg["execution"].get("provider_timeout_seconds", timeout_seconds)
    )

    discovery = DiscoverySettings(
        providers=providers,
        timeout_seconds=timeout_seconds,
        provider_timeout_seconds=provider_timeout_seconds,
        openalex=OpenAlexSettings(
            api_key=cfg["providers"]["openalex"]["api_key"],
            mailto=cfg["providers"]["openalex"]["mailto"],
        ),
        openrouter_search=OpenRouterSearchSettings(
            api_key=cfg.get("providers", {}).get("openrouter", {}).get("api_key"),
            model_name=cfg["models"].get("llm_search", "openai/gpt-4o-mini"),
            max_output_tokens=cfg.get("providers", {}).get("openrouter", {}).get("max_output_tokens", 400),
        ),
        openai_search=OpenAISearchSettings(
            api_key=os.getenv("OPENAI_API_KEY"),
            model_name=cfg["models"].get("llm_search", "gpt-4o-mini"),
            max_output_tokens=cfg.get("providers", {}).get("openrouter", {}).get("max_output_tokens", 400),
        ),
        semantic_scholar=SemanticScholarSettings(
            api_key=os.getenv("SEMANTIC_SCHOLAR_API_KEY"),
        ),
    )

    # Sandbox settings
    local_tex_dir_raw = cfg["sandbox"]["local_tex_dir"]
    local_tex_dir = Path(local_tex_dir_raw).expanduser().resolve() if local_tex_dir_raw else None

    # Librarian settings
    librarian_model = cfg["models"]["librarian"]
    agentic = cfg["features"]["agentic"]
    librarian = LibrarianSettings(
        model_name=librarian_model,
        delegate_concurrency=cfg["execution"]["concurrency"],
        top_k_headers=cfg["retrieval"]["top_k_headers"],
        agentic=agentic,
        max_query_attempts=cfg["retrieval"]["max_query_attempts"],
        max_replan_rounds=cfg["execution"]["max_replan_rounds"],
        timeout_seconds=timeout_seconds,
        query_planner_model_name=cfg["models"]["query_planner"],
    )

    # Ranking settings
    rerank = RerankSettings(
        strategy=cfg["ranking"]["reranker"],
        colbert_endpoint=cfg["ranking"]["colbert_endpoint"],
        bge_model=cfg["ranking"]["bge_model"],
        openrouter_model=cfg["ranking"].get("openrouter_model", "cohere/rerank-v3.5"),
        api_key=cfg.get("providers", {}).get("openrouter", {}).get("api_key"),
    )

    return AppSettings(
        discovery=discovery,
        sandbox=SandboxSettings(local_tex_dir=local_tex_dir),
        librarian=librarian,
        rerank=rerank,
    )
