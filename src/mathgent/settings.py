"""Application settings loader and typed config groups sourced from environment variables."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None:
        return default
    items = [item.strip().lower() for item in raw.split(",") if item.strip()]
    return items or default


def _env_int(name: str, default: int, *, minimum: int | None = None) -> int:
    value = int(os.getenv(name, str(default)))
    if minimum is not None:
        return max(minimum, value)
    return value


def _env_float(name: str, default: float, *, minimum: float | None = None) -> float:
    value = float(os.getenv(name, str(default)))
    if minimum is not None:
        return max(minimum, value)
    return value


@dataclass(frozen=True)
class RetrySettings:
    max_retries: int = 3
    base_backoff_seconds: float = 1.0


@dataclass(frozen=True)
class OpenAlexSettings:
    api_key: str | None
    mailto: str | None
    timeout_seconds: float = 20.0
    title_resolution_enabled: bool = True
    title_match_threshold: float = 0.90
    max_title_resolutions: int = 4
    arxiv_query_max_results: int = 5
    arxiv_resolver_delay_seconds: float = 0.5


@dataclass(frozen=True)
class ExaSettings:
    api_key: str | None
    timeout_seconds: float = 20.0


@dataclass(frozen=True)
class DiscoverySettings:
    provider_order: list[str]
    provider_timeout_seconds: float
    retry: RetrySettings
    openalex: OpenAlexSettings
    exa: ExaSettings


@dataclass(frozen=True)
class SandboxSettings:
    local_tex_dir: Path | None


@dataclass(frozen=True)
class ForagerSettings:
    model_name: str
    use_llm_header_pick: bool
    selector_request_limit: int
    selector_total_tokens_limit: int | None


@dataclass(frozen=True)
class LibrarianSettings:
    model_name: str
    candidate_multiplier: int
    candidate_cap: int
    delegate_concurrency: int
    early_stop_on_matches: bool
    agentic_query_loop: bool
    agentic_discovery: bool
    max_query_attempts: int
    max_replan_rounds: int
    query_planner_timeout_seconds: float
    query_planner_model_name: str | None
    planner_request_limit: int
    planner_total_tokens_limit: int | None
    discovery_request_limit: int
    discovery_total_tokens_limit: int | None


@dataclass(frozen=True)
class RerankSettings:
    strategy: str
    colbert_endpoint: str
    bge_model: str


@dataclass(frozen=True)
class AppSettings:
    discovery: DiscoverySettings
    sandbox: SandboxSettings
    forager: ForagerSettings
    librarian: LibrarianSettings
    rerank: RerankSettings


def load_settings() -> AppSettings:
    retry = RetrySettings(
        max_retries=_env_int("MATHGENT_DISCOVERY_MAX_RETRIES", 3, minimum=0),
        base_backoff_seconds=_env_float("MATHGENT_DISCOVERY_BACKOFF_SECONDS", 1.0, minimum=0.0),
    )

    provider_order = _env_list("MATHGENT_DISCOVERY_ORDER", ["openalex", "exa"])
    provider_timeout = _env_float("MATHGENT_PROVIDER_TIMEOUT_SECONDS", 8.0, minimum=0.0)

    openalex = OpenAlexSettings(
        api_key=os.getenv("OPENALEX_API_KEY"),
        mailto=os.getenv("OPENALEX_MAILTO"),
        timeout_seconds=_env_float("MATHGENT_OPENALEX_TIMEOUT_SECONDS", 20.0, minimum=0.1),
        title_resolution_enabled=_env_bool("MATHGENT_OPENALEX_TITLE_RESOLUTION", True),
        title_match_threshold=_env_float("MATHGENT_OPENALEX_TITLE_MATCH_THRESHOLD", 0.90, minimum=0.0),
        max_title_resolutions=_env_int("MATHGENT_OPENALEX_TITLE_RESOLUTION_LIMIT", 4, minimum=1),
        arxiv_query_max_results=_env_int("MATHGENT_OPENALEX_ARXIV_QUERY_MAX_RESULTS", 5, minimum=1),
        arxiv_resolver_delay_seconds=_env_float(
            "MATHGENT_OPENALEX_ARXIV_QUERY_DELAY_SECONDS",
            0.5,
            minimum=0.0,
        ),
    )

    exa = ExaSettings(
        api_key=os.getenv("EXA_API_KEY"),
        timeout_seconds=_env_float("MATHGENT_EXA_TIMEOUT_SECONDS", 20.0, minimum=0.1),
    )

    discovery = DiscoverySettings(
        provider_order=provider_order,
        provider_timeout_seconds=provider_timeout,
        retry=retry,
        openalex=openalex,
        exa=exa,
    )

    local_tex_dir_raw = os.getenv("MATHGENT_LOCAL_TEX_DIR")
    local_tex_dir = Path(local_tex_dir_raw).expanduser().resolve() if local_tex_dir_raw else None

    sandbox = SandboxSettings(local_tex_dir=local_tex_dir)
    forager = ForagerSettings(
        model_name=os.getenv("MATHGENT_FORAGER_MODEL", "test"),
        use_llm_header_pick=_env_bool("MATHGENT_FORAGER_USE_LLM_HEADER_PICK", False),
        selector_request_limit=_env_int("MATHGENT_SELECTOR_REQUEST_LIMIT", 3, minimum=1),
        selector_total_tokens_limit=(
            _env_int("MATHGENT_SELECTOR_TOTAL_TOKENS_LIMIT", 600, minimum=1)
            if os.getenv("MATHGENT_SELECTOR_TOTAL_TOKENS_LIMIT")
            else None
        ),
    )

    librarian_model = os.getenv("MATHGENT_LIBRARIAN_MODEL", "test")
    query_planner_model_name = os.getenv("MATHGENT_QUERY_PLANNER_MODEL")
    librarian = LibrarianSettings(
        model_name=librarian_model,
        candidate_multiplier=_env_int("MATHGENT_CANDIDATE_MULTIPLIER", 2, minimum=1),
        candidate_cap=_env_int("MATHGENT_CANDIDATE_CAP", 30, minimum=1),
        delegate_concurrency=_env_int("MATHGENT_DELEGATE_CONCURRENCY", 4, minimum=1),
        early_stop_on_matches=_env_bool("MATHGENT_EARLY_STOP_ON_MATCHES", True),
        agentic_query_loop=_env_bool("MATHGENT_AGENTIC_QUERY_LOOP", True),
        agentic_discovery=_env_bool("MATHGENT_AGENTIC_DISCOVERY", True),
        max_query_attempts=_env_int("MATHGENT_MAX_QUERY_ATTEMPTS", 2, minimum=1),
        max_replan_rounds=_env_int("MATHGENT_MAX_REPLAN_ROUNDS", 2, minimum=1),
        query_planner_timeout_seconds=_env_float("MATHGENT_QUERY_PLANNER_TIMEOUT_SECONDS", 4.0, minimum=0.0),
        query_planner_model_name=query_planner_model_name,
        planner_request_limit=_env_int("MATHGENT_PLANNER_REQUEST_LIMIT", 4, minimum=1),
        planner_total_tokens_limit=(
            _env_int("MATHGENT_PLANNER_TOTAL_TOKENS_LIMIT", 1000, minimum=1)
            if os.getenv("MATHGENT_PLANNER_TOTAL_TOKENS_LIMIT")
            else None
        ),
        discovery_request_limit=_env_int("MATHGENT_DISCOVERY_REQUEST_LIMIT", 4, minimum=1),
        discovery_total_tokens_limit=(
            _env_int("MATHGENT_DISCOVERY_TOTAL_TOKENS_LIMIT", 1000, minimum=1)
            if os.getenv("MATHGENT_DISCOVERY_TOTAL_TOKENS_LIMIT")
            else None
        ),
    )

    rerank = RerankSettings(
        strategy=os.getenv("MATHGENT_RERANKER", "auto"),
        colbert_endpoint=os.getenv("MATHGENT_COLBERT_ENDPOINT", "http://127.0.0.1:8001/rerank"),
        bge_model=os.getenv("MATHGENT_BGE_MODEL", "BAAI/bge-reranker-v2-m3"),
    )

    return AppSettings(
        discovery=discovery,
        sandbox=sandbox,
        forager=forager,
        librarian=librarian,
        rerank=rerank,
    )
