# Mathgent

Agentic search engine for mathematical theorems and lemmas over arXiv LaTeX sources.

---

## How It Works

```
Your query: "Banach fixed point theorem for non-reflexive spaces"
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│  Librarian  (LibrarianOrchestrator)                     │
│                                                         │
│  1. Expands your query into 3–4 diverse variants        │
│     (paper-style, statement-style, keyword-style)       │
│                                                         │
│  2. Sends all variants to discovery providers           │
│     in parallel → collects arXiv paper IDs              │
│                                                         │
│  3. Dispatches a Forager to each candidate paper        │
└─────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│  Forager  (ForagerAgent)   [one per paper]              │
│                                                         │
│  1. Downloads the paper's LaTeX source                  │
│  2. Scans for \begin{theorem}, \begin{lemma}, etc.      │
│  3. Extracts the best-matching block                    │
│  4. Scores it against your query                        │
└─────────────────────────────────────────────────────────┘
      │
      ▼
Ranked results — each with the matched theorem snippet
```

**Discovery providers** (run in parallel, configured via `MATHGENT_DISCOVERY_PROVIDERS`):
- `openalex` — semantic search over 250M+ papers
- `zbmath_open` — zbMATH Open mathematics database
- `arxiv_api` — arXiv keyword/title search
- `semantic_scholar` — Semantic Scholar API

---

## API Keys

| Key | Required? | Purpose |
|-----|-----------|---------|
| `OPENAI_API_KEY` | One of these two¹ | LLM query planning + OpenAI embedding reranker |
| `OPENROUTER_API_KEY` | One of these two¹ | LLM query planning via OpenRouter |
| `E2B_API_KEY` | Yes² | Fetch arXiv LaTeX sources via E2B sandbox |
| `OPENALEX_API_KEY` | Optional | Higher rate limits on OpenAlex discovery |
| `OPENALEX_MAILTO` | Optional | Polite-pool access for OpenAlex (your email) |

¹ **OpenAI or OpenRouter** — set `MATHGENT_LIBRARIAN_MODEL` accordingly:
  - `openai:gpt-5-mini` → uses `OPENAI_API_KEY`
  - `openrouter:anthropic/claude-3-haiku` → uses `OPENROUTER_API_KEY`

  Note: if you use OpenRouter, also set `MATHGENT_RERANKER=token_overlap` (the default reranker requires `OPENAI_API_KEY`).

² Not needed if you supply a local TeX cache via `MATHGENT_LOCAL_TEX_DIR`. See [data/tex_cache/README.md](data/tex_cache/README.md).

> **Minimal free setup** — `MATHGENT_AGENTIC=0`, `MATHGENT_LIBRARIAN_MODEL=test`, `MATHGENT_RERANKER=token_overlap`, and a local TeX dir. No API keys required.

---

## Quick Start

### Option A — HTTP API (recommended)

```bash
# 1. Install
uv venv && source .venv/bin/activate
uv pip install -e .
```

# 2. Configure
````bash
cp .env.example .env.local
```
# Edit .env.local — add your keys

# 3. Start the server
```bash
PYTHONPATH=src uvicorn mathgent.api:app --reload --env-file .env.local
```

# 4. Search
```bash
curl -X POST http://127.0.0.1:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query": "Banach fixed point theorem", "max_results": 5, "strictness": 0.2}'
````

### Option B — Direct Python usage

```python
import asyncio
from mathgent.settings import load_settings
from mathgent.api.deps import build_orchestrator

async def search(query: str):
    settings = load_settings()          # this reads config.json + .env.local
    orchestrator = build_orchestrator(settings)
    response = await orchestrator.search(query, max_results=5, strictness=0.2)
    for result in response.results:
        print(f"{result.arxiv_id} | score={result.match.score:.2f}")
        print(result.match.snippet)
        print()

asyncio.run(search("Banach fixed point theorem"))
```

Run with:
```bash
PYTHONPATH=src python your_script.py
# or load your .env.local first:
set -a && source .env.local && set +a && PYTHONPATH=src python your_script.py
```

---

## Request & Response

```json
// POST /search
{
  "query": "Banach fixed point theorem for non-reflexive spaces",
  "max_results": 5,
  "strictness": 0.2
}
```

```json
// Response
{
  "results": [
    {
      "arxiv_id": "2509.13121",
      "title": "On fixed points in Banach spaces",
      "authors": ["J. Smith"],
      "match": {
        "header_line": "\\begin{theorem}[Main]",
        "snippet": "Let $X$ be a Banach space and $T: X \\to X$ a contraction...",
        "score": 0.62
      }
    }
  ]
}
```

**Parameters:**
- `strictness` `[0.0–1.0]` — minimum relevance score to return a result. Start at `0.2`.
- `max_results` — number of papers to return (default 5, max 20).

---

## Configuration

Settings come from `config.json` (base) with `.env.local` overrides. Copy `.env.example` → `.env.local`.

| Variable | Default | Effect |
|----------|---------|--------|
| `MATHGENT_LIBRARIAN_MODEL` | `openai:gpt-4o-mini` | LLM for query planning. `test` = no LLM |
| `MATHGENT_AGENTIC` | `1` | Enable query planning and replanning |
| `MATHGENT_DISCOVERY_PROVIDERS` | `openalex,zbmath_open,arxiv_api,semantic_scholar` | Active providers |
| `MATHGENT_RERANKER` | `auto` | `token_overlap`, `openai_embedding`, `hybrid_token_openai` |
| `MATHGENT_TOP_K_HEADERS` | `15` | Max theorem headers extracted per paper |
| `MATHGENT_DELEGATE_CONCURRENCY` | `5` | Parallel paper workers |
| `MATHGENT_TIMEOUT_SECONDS` | `60.0` | Shared timeout for all I/O |
| `MATHGENT_LOCAL_TEX_DIR` | _(unset)_ | Path to local `.tex` file cache (avoids E2B) |

---

## Development

```bash
pytest                        # run all tests
ruff check src/ tests/        # lint
ruff format src/ tests/       # format
mypy src/                     # type check
```

---

## Architecture

```
src/mathgent/
├── api/              FastAPI app, routes, dependency injection
├── orchestration/    LibrarianOrchestrator, QueryPlannerService
├── discovery/        Provider adapters (OpenAlex, zbMATH, arXiv, Semantic Scholar)
│   └── providers/
├── extraction/       HeaderGrepper, BoundedBlockExtractor, theorem numbering
├── agents/           ForagerAgent (per-paper extraction + scoring)
├── rerank/           Reranker backends (token overlap, BGE, ColBERT, OpenAI, hybrid)
├── sandbox/          E2B and local LaTeX source runners
├── models/           Domain and API models
├── tools/            Agent tool facades (discovery, extraction)
└── observability/    Loguru logging, Logfire tracing, hook registry
```

---

## License

MIT — see [LICENSE](LICENSE).
