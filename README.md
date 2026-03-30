# Mathgent

Agentic theorem/lemma search over arXiv LaTeX sources.

## Stack
- PydanticAI: Librarian orchestration + optional agentic query/discovery.
- FastAPI: `/search` API.
- Discovery providers: OpenAlex semantic + OpenAI web search.
- Sandbox: E2B Code Interpreter (or local `.tex` directory).
- Reranking: token overlap (default), optional BGE/ColBERT.
- Logging/Tracing: Loguru + Logfire instrumentation.

## Current Structure
- `src/mathgent/api/`: app factory, middleware, routes, dependency wiring.
- `src/mathgent/settings.py`: single env/settings source.
- `src/mathgent/models/`: request/response and domain models.
- `src/mathgent/discovery/`: provider adapters, chain pipeline, arXiv metadata, ID parsing.
- `src/mathgent/extraction/`: header grep parsing + bounded environment extraction.
- `src/mathgent/tools/`: minimal agent-facing facades (`discovery`, `extraction`).
- `src/mathgent/agents/`: forager implementation.
- `src/mathgent/orchestration/`: librarian, query planner, discovery execution, result policy.
- `src/mathgent/sandbox/`: local/E2B runners + source fetch.

## Search Pipeline
1. Librarian receives `query`, `max_results`, `strictness`.
2. Query planner generates attempt queries (deterministic heuristics or agentic model).
3. Discovery chain runs providers in order (`openalex`, then `openai_search` by default).
4. Candidate IDs are deduped.
5. Forager runs on candidates in parallel:
   - `get_paper_headers` (scan for theorem-like environments)
   - `fetch_header_block` (single environment extraction around selected header)
   - score and strictness gate
6. Top results returned, enriched with title/authors when metadata is available.

Constraint: no full `.tex` file is put into model context; extraction stays bounded per environment.

## Modes
- Deterministic mode:
  - `MATHGENT_AGENTIC_QUERY_LOOP=0`
  - `MATHGENT_AGENTIC_DISCOVERY=0`
- Agentic mode:
  - `MATHGENT_AGENTIC_QUERY_LOOP=1`
  - `MATHGENT_AGENTIC_DISCOVERY=1`

## API
### Request
```json
{
  "query": "Banach fixed point theorem for non-reflexive spaces",
  "max_results": 3,
  "strictness": 0.2
}
```

### Response
```json
{
  "query": "...",
  "max_results": 3,
  "strictness": 0.2,
  "results": [
    {
      "arxiv_id": "2509.13121",
      "title": "...",
      "authors": ["..."],
      "match": {
        "arxiv_id": "2509.13121",
        "line_number": 179,
        "header_line": "\\begin{theorem}...",
        "snippet": "...",
        "score": 0.62
      }
    }
  ]
}
```

## Config (minimal)
See `.env.example` for the full list.

Core:
- `MATHGENT_TIMEOUT_SECONDS` (single shared timeout across core operations)
- `MATHGENT_DISCOVERY_ORDER` (default `openalex,openai_search`)
- `OPENALEX_API_KEY`, `OPENAI_API_KEY`, `OPENALEX_MAILTO`

Key orchestration knobs:
- `MATHGENT_MAX_QUERY_ATTEMPTS`
- `MATHGENT_MAX_REPLAN_ROUNDS`
- `MATHGENT_DELEGATE_CONCURRENCY`
- `MATHGENT_TOP_K_HEADERS`

## Run (uv-first)
```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
python -m pytest
PYTHONPATH=src uvicorn mathgent.api:app --reload --env-file .env.local
```
