"""Environment-backed settings for discovery, orchestration, and reranking."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
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
class OpenAISearchSettings:
    api_key: str | None
    model_name: str = "gpt-5-mini"
    max_output_tokens: int = 2000


@dataclass(frozen=True)
class DiscoverySettings:
    provider_order: list[str]
    timeout_seconds: float
    openalex: OpenAlexSettings
    openai_search: OpenAISearchSettings


@dataclass(frozen=True)
class SandboxSettings:
    local_tex_dir: Path | None


@dataclass(frozen=True)
class LibrarianSettings:
    model_name: str
    delegate_concurrency: int
    top_k_headers: int
    agentic_query_loop: bool
    agentic_discovery: bool
    max_query_attempts: int
    max_replan_rounds: int
    timeout_seconds: float
    query_planner_model_name: str | None


@dataclass(frozen=True)
class RerankSettings:
    strategy: str
    colbert_endpoint: str | None
    bge_model: str | None


@dataclass(frozen=True)
class AppSettings:
    discovery: DiscoverySettings
    sandbox: SandboxSettings
    librarian: LibrarianSettings
    rerank: RerankSettings


def load_settings() -> AppSettings:
    timeout_seconds = _env_float("MATHGENT_TIMEOUT_SECONDS", 30.0, minimum=0.1)
    provider_order = _env_list("MATHGENT_DISCOVERY_ORDER", ["openalex", "openai_search"])

    discovery = DiscoverySettings(
        provider_order=provider_order,
        timeout_seconds=timeout_seconds,
        openalex=OpenAlexSettings(
            api_key=os.getenv("OPENALEX_API_KEY"),
            mailto=os.getenv("OPENALEX_MAILTO"),
        ),
        openai_search=OpenAISearchSettings(
            api_key=os.getenv("OPENAI_API_KEY"),
            model_name=os.getenv("MATHGENT_OPENAI_SEARCH_MODEL", "gpt-4.1-mini"),
            max_output_tokens=_env_int("MATHGENT_OPENAI_SEARCH_MAX_OUTPUT_TOKENS", 400, minimum=50),
        ),
    )

    local_tex_dir_raw = os.getenv("MATHGENT_LOCAL_TEX_DIR")
    local_tex_dir = Path(local_tex_dir_raw).expanduser().resolve() if local_tex_dir_raw else None

    librarian_model = os.getenv("MATHGENT_LIBRARIAN_MODEL", "test")
    librarian = LibrarianSettings(
        model_name=librarian_model,
        delegate_concurrency=_env_int("MATHGENT_DELEGATE_CONCURRENCY", 4, minimum=1),
        top_k_headers=_env_int("MATHGENT_TOP_K_HEADERS", 10, minimum=1),
        agentic_query_loop=_env_bool("MATHGENT_AGENTIC_QUERY_LOOP", True),
        agentic_discovery=_env_bool("MATHGENT_AGENTIC_DISCOVERY", True),
        max_query_attempts=_env_int("MATHGENT_MAX_QUERY_ATTEMPTS", 2, minimum=1),
        max_replan_rounds=_env_int("MATHGENT_MAX_REPLAN_ROUNDS", 2, minimum=1),
        timeout_seconds=timeout_seconds,
        query_planner_model_name=os.getenv("MATHGENT_QUERY_PLANNER_MODEL"),
    )

    rerank = RerankSettings(
        strategy=os.getenv("MATHGENT_RERANKER", "auto"),
        colbert_endpoint=os.getenv("MATHGENT_COLBERT_ENDPOINT"),
        bge_model=os.getenv("MATHGENT_BGE_MODEL"),
    )

    return AppSettings(
        discovery=discovery,
        sandbox=SandboxSettings(local_tex_dir=local_tex_dir),
        librarian=librarian,
        rerank=rerank,
    )
