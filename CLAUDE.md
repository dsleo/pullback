# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup & Development Commands

**Initial Setup**
```bash
uv venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
uv pip install -e ".[dev]"
```

**Run Server**
```bash
PYTHONPATH=src uvicorn pullback.api:app --reload --env-file .env.local
```

**Testing**
```bash
pytest                              # Run all tests
pytest tests/test_forager_agent.py  # Run specific test file
pytest -k "test_discovery"          # Run tests matching pattern
pytest -v                           # Verbose output
```

**Code Quality**
```bash
ruff check src/ tests/              # Lint with ruff
ruff format src/ tests/             # Auto-format code
mypy src/                           # Type checking
bandit -r src/                      # Security audit
pip-audit                           # Dependency audit
```

**Query the Running API**
```bash
curl -X POST http://127.0.0.1:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query":"Banach fixed point theorem","max_results":3,"strictness":0.2}'
```

## Configuration

All configuration is environment-based via `settings.py`. Key patterns:
- Copy `.env.example` → `.env.local` and customize
- Settings are loaded once at app startup via `load_settings()`
- Use helper functions for parsing: `_env_bool()`, `_env_float()`, `_env_list()`, etc.

Core knobs:
- **MATHGENT_AGENTIC**: Enable/disable LLM-driven query planning and agentic discovery (default: 1)
- **MATHGENT_LIBRARIAN_MODEL**: Model for query planning (set to `"test"` for deterministic behavior)
- **MATHGENT_TIMEOUT_SECONDS**: Shared timeout for discovery + metadata fetching
- **MATHGENT_DISCOVERY_PROVIDERS**: Comma-separated set of parallel discovery providers (default: `openalex,zbmath_open,arxiv_api,tavily`)
- **MATHGENT_DELEGATE_CONCURRENCY**: Parallel forager executions per batch (default: 4)
- **MATHGENT_TOP_K_HEADERS**: Headers to rescore per paper (default: 10)

See `.env.example` for logging, sandbox, and reranking options.

## Optimization & Iteration Tracking

The repository includes an autonomous optimization loop for improving benchmark metrics (paper@20, theorem@20, avg_latency_s).

**Core Infrastructure**:
- `config.json` — Single source of truth for all tunable parameters
- `configs/ITERATIONS.jsonl` — Append-only log of all iterations with metrics
- `configs/iteration_N_*.json` — Immutable config snapshot for each iteration
- `CHANGELOG.md` — Narrative documentation (hypotheses, insights, decisions)
- `scripts/save_iteration.py` — Capture config + metrics after benchmark
- `scripts/analyze_iterations.py` — Query and compare iterations
- `scripts/run_iteration.sh` — One-command benchmark + save workflow

**Typical Iteration Workflow**:

1. Edit `config.json` with one parameter change (e.g., `top_k_headers: 10 → 15`)
2. Run benchmark:
   ```bash
   set -a && source .env.local && set +a
   python scripts/eval_benchmark.py \
     --data data/benchmark_clean_71.jsonl \
     --max-results 20 --strictness 0.2 --validate-labels \
     --output logs/benchmark_iter_N.jsonl
   ```
3. Save iteration (auto-snapshots config, extracts metrics, appends to ITERATIONS.jsonl):
   ```bash
   python scripts/save_iteration.py N "config_name" logs/benchmark_iter_N.jsonl \
     --hypothesis "Why this change improves performance" \
     --status ACCEPT
   ```
4. Analyze results:
   ```bash
   python scripts/analyze_iterations.py list                    # All iterations
   python scripts/analyze_iterations.py compare 0 N            # vs baseline
   python scripts/analyze_iterations.py best paper@20          # Best by metric
   python scripts/analyze_iterations.py diff-config \
     configs/iteration_0_baseline.json configs/iteration_N_*.json  # Config diff
   ```
5. Document in `CHANGELOG.md` (narrative only; metrics are in ITERATIONS.jsonl)
6. Commit `config.json`, `CHANGELOG.md`, `configs/`, and `.env.local` changes

**Decision Rule**: ACCEPT if paper@20 OR theorem@20 improves AND avg_latency_s ≤ +20% vs previous; else REVERT.

**Key Concepts**:
- `configs/iteration_N_*.json` is a frozen copy of `config.json` at that iteration
- `ITERATIONS.jsonl` is queryable via `scripts/analyze_iterations.py` (not human-edited)
- See `CHANGELOG.md` for iteration workflow details and template

## Architecture Overview

### Three-Layer Search Pipeline

1. **LibrarianOrchestrator** (`orchestration/librarian.py`): Entry point for search queries
   - Orchestrates query planning, discovery, delegation, and result aggregation
   - Manages parallel forager tasks via `PaperWorkerState`
   - Applies `ResultPolicy` (dedup, deranking by strictness)

2. **QueryPlannerService** (`orchestration/query_planner.py`): LLM-based query expansion
   - Generates diverse query variants (paper-style, statement-style, keyword-style)
   - Optionally triggers replans if no matches found
   - Uses PydanticAI agents with request limits

3. **DiscoveryExecutionService** (`orchestration/discovery_execution.py`): Provider chain execution
   - Runs discovery providers in order (OpenAlex → zbMATH → arXiv API → Tavily)
   - Deduplicates arxiv_id across providers
   - Returns metadata when available

4. **ForagerAgent** (`agents/forager.py`): Per-paper extraction and reranking
   - **Plan phase**: Fetches theorem-like headers from paper
   - **Execute phase**: Extracts snippets, scores against query, applies strictness threshold
   - Best-scoring block per paper is returned if score ≥ strictness

### Key Components

- **Discovery**: `src/pullback/discovery/`
  - `PaperDiscoveryClient`: Chains providers
  - Provider adapters: `providers/openalex.py`, `providers/zbmath_open.py`, etc.
  - arXiv helpers: ID normalization (`arxiv/ids.py`), metadata caching (`arxiv/metadata.py`)

- **Extraction**: `src/pullback/extraction/`
  - `HeaderGrepper` (`headers.py`): Regex-based scan for `\begin{theorem}`, `\begin{lemma}`, etc.
  - `BoundedBlockExtractor` (`blocks.py`): Extracts balanced LaTeX blocks with context lines
  - Numbering helpers (`numbering.py`) for environment parsing

- **Reranking**: `src/pullback/rerank/`
  - Base interface: `Reranker`
  - Implementations: `TokenOverlapReranker` (default), `BGEReranker`, `ColBERTReranker`
  - Lazy loading via `RerankerFactory`

- **Sandbox**: `src/pullback/sandbox/`
  - `LocalSandbox`: Reads `.tex` files from disk (for testing)
  - `E2BSandbox`: Fetches via E2B Code Interpreter (for arXiv papers)
  - `SourceFetcher`: Downloads papers from arXiv via `arxiv` library

- **API & Dependency Injection**: `src/pullback/api/`
  - `app.py`: FastAPI factory with lifespan setup
  - `deps.py`: Builds orchestrator and wires discovery client, forager, sandbox
  - `routes.py`: `/search` POST endpoint

- **Observability**: `src/pullback/observability/`
  - Loguru-based logging with JSON/file output options
  - Logfire optional integration for distributed tracing
  - Hook system (`HookRegistry`) for async event listeners (used by forager, librarian)

## Search Behavior & Strictness

- **strictness** is a threshold `[0, 1]` applied to the best theorem-like block per paper
  - Higher = fewer results, higher confidence matches
  - Default: `0.2` (accepts weak matches)
- Query planner generates 3-4 query variants per attempt
- If no results after discovery, librarian may replan (if agentic + max_replan_rounds > 1)
- Non-agentic mode still runs query planner if `MATHGENT_LIBRARIAN_MODEL != "test"`

## Testing Patterns

- **Hypothesis-based property tests**: `test_properties.py` (arXiv ID normalization, etc.)
- **Integration tests**: `test_api_integration.py`, `test_orchestration.py` (full pipeline)
- **Unit tests**: Individual components (forager, discovery, extraction)
- **Fixtures**: Use `@pytest.mark.asyncio` for async tests; `PYTHONPATH=src` in `pyproject.toml`

## Code Organization Principles

1. **Dataclasses for models**: Request/response types in `models/api.py`, domain models in `models/domain.py`
2. **Settings as frozen dataclasses**: Single source of truth in `settings.py`, loaded once
3. **Async/await throughout**: All I/O uses async (discovery, extraction, reranking)
4. **Dependency injection**: FastAPI app state passes orchestrator; orchestrator composes discovery client, forager, tools
5. **Hook-based observability**: Agents emit events (e.g., `plan_start`, `execute_complete`) for logging/tracing
6. **Type hints**: Full coverage with mypy strict checks (except tests)

## Common Debugging Patterns

- **Set MATHGENT_LIBRARIAN_MODEL=test** to disable LLM-based planning (deterministic)
- **Set MATHGENT_AGENTIC=0** to disable query replanning and agentic discovery wrapper
- **Check logs**: `logs/pullback.log` (rotates at 20MB, retains 14 days)
- **Enable Logfire traces**: Set `MATHGENT_LOGFIRE_SEND=1` (requires logfire account)
- **Test single discovery provider**: Modify `MATHGENT_DISCOVERY_PROVIDERS` to isolate provider behavior
