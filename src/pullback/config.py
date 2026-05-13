"""Config loader for The Pullback from config.json with env var override support."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    # Load .env.local as priority if it exists
    if Path(".env.local").exists():
        load_dotenv(".env.local")
    else:
        load_dotenv()
except ImportError:
    pass


def _find_config_path() -> Path:
    """Find config.json starting from repo root."""
    current = Path(__file__).resolve()
    while current != current.parent:
        config_path = current.parent / "config.json"
        if config_path.exists():
            return config_path
        current = current.parent
    raise FileNotFoundError("config.json not found in any parent directory")


def _validate_config(cfg: dict[str, Any]) -> None:
    """Validate config structure and required keys."""
    required_top_level = {"retrieval", "execution", "models", "ranking"}
    missing = required_top_level - set(cfg.keys())
    if missing:
        raise ValueError(f"Missing required config sections: {missing}")

    # Validate retrieval section
    retrieval = cfg.get("retrieval", {})
    if not isinstance(retrieval.get("discovery_providers"), list):
        raise ValueError("retrieval.discovery_providers must be a list of strings")
    if not isinstance(retrieval.get("top_k_headers"), int) or retrieval["top_k_headers"] < 1:
        raise ValueError("retrieval.top_k_headers must be a positive integer")
    if not isinstance(retrieval.get("max_query_attempts"), int) or retrieval["max_query_attempts"] < 1:
        raise ValueError("retrieval.max_query_attempts must be a positive integer")

    # Validate execution section
    execution = cfg.get("execution", {})
    if not isinstance(execution.get("concurrency"), int) or execution["concurrency"] < 1:
        raise ValueError("execution.concurrency must be a positive integer")
    if not isinstance(execution.get("timeout_seconds"), (int, float)) or execution["timeout_seconds"] <= 0:
        raise ValueError("execution.timeout_seconds must be a positive number")
    if not isinstance(execution.get("max_replan_rounds"), int) or execution["max_replan_rounds"] < 1:
        raise ValueError("execution.max_replan_rounds must be a positive integer")

    # Validate models section
    models = cfg.get("models", {})
    if not isinstance(models.get("librarian"), str):
        raise ValueError("models.librarian must be a string")

    # Validate ranking section
    ranking = cfg.get("ranking", {})
    if not isinstance(ranking.get("reranker"), str):
        raise ValueError("ranking.reranker must be a string")


def load_config() -> dict[str, Any]:
    """
    Load config from config.json with environment variable overrides.

    Environment variables are read ONLY as overrides after config.json is loaded.
    If an env var is set, it takes precedence over the config file value.

    Returns:
        Merged configuration dictionary
    """
    config_path = _find_config_path()

    with config_path.open("r", encoding="utf-8") as f:
        cfg = json.load(f)

    _validate_config(cfg)

    # Apply environment variable overrides
    _apply_env_overrides(cfg)

    return cfg


def _apply_env_overrides(cfg: dict[str, Any]) -> None:
    """Apply environment variable overrides to config."""
    # Retrieval overrides
    if discovery_providers := os.getenv("PULLBACK_DISCOVERY_PROVIDERS"):
        cfg["retrieval"]["discovery_providers"] = [s.strip() for s in discovery_providers.split(",")]
    if top_k := os.getenv("PULLBACK_TOP_K_HEADERS"):
        cfg["retrieval"]["top_k_headers"] = int(top_k)
    if max_attempts := os.getenv("PULLBACK_MAX_QUERY_ATTEMPTS"):
        cfg["retrieval"]["max_query_attempts"] = int(max_attempts)

    # Execution overrides
    if concurrency := os.getenv("PULLBACK_DELEGATE_CONCURRENCY"):
        cfg["execution"]["concurrency"] = int(concurrency)
    if timeout := os.getenv("PULLBACK_TIMEOUT_SECONDS"):
        cfg["execution"]["timeout_seconds"] = float(timeout)
    if replan := os.getenv("PULLBACK_MAX_REPLAN_ROUNDS"):
        cfg["execution"]["max_replan_rounds"] = int(replan)

    # Models overrides
    if librarian := os.getenv("PULLBACK_LIBRARIAN_MODEL"):
        cfg["models"]["librarian"] = librarian
    if query_planner := os.getenv("PULLBACK_QUERY_PLANNER_MODEL"):
        cfg["models"]["query_planner"] = query_planner
    if llm_search_model := os.getenv("PULLBACK_LLM_SEARCH_MODEL"):
        cfg["models"]["llm_search"] = llm_search_model

    # Ranking overrides
    if reranker := os.getenv("PULLBACK_RERANKER"):
        cfg["ranking"]["reranker"] = reranker
    if colbert := os.getenv("PULLBACK_COLBERT_ENDPOINT"):
        cfg["ranking"]["colbert_endpoint"] = colbert
    if bge := os.getenv("PULLBACK_RERANKER_BGE_MODEL"):
        cfg["ranking"]["bge_model"] = bge
    if openrouter_model := os.getenv("PULLBACK_RERANKER_OPENROUTER_MODEL"):
        cfg["ranking"]["openrouter_model"] = openrouter_model

    # Sandbox overrides
    if local_dir := os.getenv("PULLBACK_LOCAL_TEX_DIR"):
        cfg["sandbox"]["local_tex_dir"] = local_dir
    if e2b_timeout := os.getenv("PULLBACK_E2B_TIMEOUT_S"):
        cfg["sandbox"]["e2b_timeout_seconds"] = float(e2b_timeout)

    # Observability overrides
    if log_level := os.getenv("PULLBACK_LOG_LEVEL"):
        cfg["observability"]["log_level"] = log_level
    if log_json := os.getenv("PULLBACK_LOG_JSON"):
        cfg["observability"]["log_json"] = log_json.lower() in {"1", "true", "yes", "on"}
    if log_file_enabled := os.getenv("PULLBACK_LOG_FILE_ENABLED"):
        cfg["observability"]["log_file_enabled"] = log_file_enabled.lower() in {"1", "true", "yes", "on"}
    if log_file := os.getenv("PULLBACK_LOG_FILE"):
        cfg["observability"]["log_file"] = log_file
    if log_rotation := os.getenv("PULLBACK_LOG_FILE_ROTATION"):
        cfg["observability"]["log_file_rotation"] = log_rotation
    if log_retention := os.getenv("PULLBACK_LOG_FILE_RETENTION"):
        cfg["observability"]["log_file_retention"] = log_retention
    if enable_logfire := os.getenv("PULLBACK_ENABLE_LOGFIRE"):
        cfg["observability"]["enable_logfire"] = enable_logfire.lower() in {"1", "true", "yes", "on"}
    if logfire_send := os.getenv("PULLBACK_LOGFIRE_SEND"):
        cfg["observability"]["logfire_send"] = logfire_send.lower() in {"1", "true", "yes", "on"}
    if pydantic_ai := os.getenv("PULLBACK_PYDANTICAI_INSTRUMENT"):
        cfg["observability"]["pydanticai_instrument"] = pydantic_ai.lower() in {"1", "true", "yes", "on"}

    # Provider API keys (prefer env vars, fallback to config)
    if openalex_key := os.getenv("OPENALEX_API_KEY"):
        cfg["providers"]["openalex"]["api_key"] = openalex_key
    if openalex_mailto := os.getenv("OPENALEX_MAILTO"):
        cfg["providers"]["openalex"]["mailto"] = openalex_mailto
    if openrouter_key := os.getenv("OPENROUTER_API_KEY"):
        if "openrouter" in cfg["providers"]:
            cfg["providers"]["openrouter"]["api_key"] = openrouter_key
    if openrouter_tokens := os.getenv("PULLBACK_OPENROUTER_SEARCH_MAX_OUTPUT_TOKENS"):
        if "openrouter" in cfg["providers"]:
            cfg["providers"]["openrouter"]["max_output_tokens"] = int(openrouter_tokens)

    # Features overrides
    if agentic := os.getenv("PULLBACK_AGENTIC"):
        cfg["features"]["agentic"] = agentic.lower() in {"1", "true", "yes", "on"}
    if disable_metadata := os.getenv("PULLBACK_DISABLE_METADATA_FETCH"):
        cfg["features"]["disable_metadata_fetch"] = disable_metadata.lower() in {"1", "true", "yes", "on"}
    if numbering := os.getenv("PULLBACK_NUMBERING_COUNT_LABELS"):
        cfg["features"]["numbering_count_labels"] = numbering.lower() in {"1", "true", "yes", "on"}


# Global config instance
_global_config: dict[str, Any] | None = None


def get_config() -> dict[str, Any]:
    """Get the global config instance (lazy-loaded)."""
    global _global_config
    if _global_config is None:
        _global_config = load_config()
    return _global_config


def reset_config() -> None:
    """Reset global config (useful for testing)."""
    global _global_config
    _global_config = None
