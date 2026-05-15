# The Pullback

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

## Pipeline details

### Query planning (LLM reformulation)

In agentic mode, the Librarian expands the original query into a small set of diverse reformulations (paper-style, statement-style, keyword-style). These variants are what get sent to discovery providers. The UI streams these variants as they are planned.

### Discovery fallback & recovery

Discovery is optimized for returning **arXiv IDs** (so we can fetch raw LaTeX). In practice, providers may be rate-limited or return results without arXiv IDs. The pipeline has two explicit “second chances”:

- **Provider-internal fallback**  
  Example: `arxiv_api` first tries the arXiv export API; if it times out / rate-limits it falls back to arXiv HTML search; if that fails it falls back to a web-search backend constrained to arXiv results.

- **Title → arXiv ID recovery (cross-provider)**  
  If a provider returns *no arXiv IDs*, it still exposes `title_candidates()`. The pipeline can then resolve titles to arXiv IDs via an arXiv-only resolver that:
  1) searches for the title (preferring web-search when configured), and  
  2) **verifies** a candidate by checking the arXiv abstract-page title matches (normalized).

---

## Quick Start

### Option A — Live Demo (browser)

```bash
# 1. Install
uv venv && source .venv/bin/activate
uv pip install -e .

# 2. Configure
cp .env.example .env.local
# Edit .env.local — add your keys (see API Keys section below)

# 3. Launch
set -a && source .env.local && set +a
python demo/app.py
# Browser opens automatically at http://localhost:7860
```

The demo streams the full pipeline in real time: query reformulation → provider discovery → per-paper foraging → ranked results. Each paper card shows the matched theorem snippet, score, and a direct link to the arXiv page. An **ⓘ** icon reveals advanced details (per-query attribution, strategy labels, raw scores).

### Option B — HTTP API

```bash
# 1. Install
uv venv && source .venv/bin/activate
uv pip install -e .

# 2. Configure
cp .env.example .env.local
# Edit .env.local — add your keys (see API Keys section below)

# 3. Start the server
PYTHONPATH=src uvicorn mathgent.api:app --reload --env-file .env.local

# 4. Search
curl -X POST http://127.0.0.1:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query": "Banach fixed point theorem", "max_results": 5, "strictness": 0.2}'
```

### Option C — Direct Python usage

```python
import asyncio
from mathgent.settings import load_settings
from mathgent.api.deps import build_orchestrator

async def search(query: str):
    settings = load_settings()          # reads config.json + .env.local
    orchestrator = build_orchestrator(settings)
    response = await orchestrator.search(query, max_results=5, strictness=0.2)
    for result in response.results:
        print(f"{result.arxiv_id} | score={result.match.score:.2f}")
        print(result.match.snippet)
        print()

asyncio.run(search("Banach fixed point theorem"))
```

```bash
# Load your keys and run
set -a && source .env.local && set +a
PYTHONPATH=src python your_script.py
```

---

## Benchmark

The benchmark evaluates retrieval accuracy on a curated set of mathematical queries, each paired with a ground-truth arXiv paper and theorem label.

**Dataset** — available on HuggingFace: [`uw-math-ai/theorem-search-dataset`](https://huggingface.co/datasets/uw-math-ai/theorem-search-dataset)

| File | Queries | Description |
|-------|---------|-------------|
| `benchmark_clean_106.jsonl` | 106 | Full set (71 original + 35 additional) |
| `benchmark_clean_71.jsonl` | 71 | Original validated set |
| `benchmark_new_35.jsonl` | 35 | Additional harder queries |

Each entry has the form:
```json
{
  "query": "Smooth DM stack is uniquely determined by codimension one behaviour",
  "gt_arxiv_id": "2310.15076",
  "gt_theorem_label": "Theorem 3.1",
  "gt_paper_title": "A criterion for smooth weighted blowdowns"
}
```

**Metrics**: `paper@20` (ground-truth paper in top 20), `theorem@20` (ground-truth theorem in top 20).

**Run the benchmark:**

```bash
set -a && source .env.local && set +a

# Original 71-query set
python scripts/eval_benchmark.py \
  --data data/benchmark_clean_106.jsonl \  # full 106-query set
  --max-results 20 --strictness 0.0 --validate-labels \
  --output logs/my_benchmark_106.jsonl

# Additional 35-query set
python scripts/eval_benchmark.py \
  --data data/benchmark_new_35.jsonl \
  --max-results 20 --strictness 0.0 --validate-labels \
  --output logs/my_benchmark_35.jsonl
```

> **Note on reproducibility** — results vary across runs due to LLM stochasticity in query planning. For deterministic evaluation, set `MATHGENT_LIBRARIAN_MODEL=test` (disables LLM expansion) or fix `MATHGENT_MAX_QUERY_ATTEMPTS=1`. The `--resume` flag lets you continue an interrupted run.

---

## API Keys

| Key | Required? | Purpose |
|-----|-----------|---------|
| `OPENAI_API_KEY` | One of these two¹ | LLM query planning + OpenAI embedding reranker |
| `OPENROUTER_API_KEY` | One of these two¹ | LLM query planning via OpenRouter |
| `E2B_API_KEY` | Yes² | Fetch arXiv LaTeX sources via E2B sandbox |
| `OPENALEX_API_KEY` | Optional | Higher rate limits on OpenAlex discovery |
| `OPENALEX_MAILTO` | Optional | Polite-pool access for OpenAlex (your email) |
| `SERP_API_KEY` | Optional | Web-search fallback for arXiv discovery + title→arXiv-ID recovery |

¹ **OpenAI or OpenRouter** — set `MATHGENT_LIBRARIAN_MODEL` accordingly:
  - `openai:gpt-4o-mini` → uses `OPENAI_API_KEY`
  - `openrouter:anthropic/claude-3-haiku` → uses `OPENROUTER_API_KEY`

  Note: if you use OpenRouter, also set `MATHGENT_RERANKER=token_overlap` (the default reranker requires `OPENAI_API_KEY`).

² Not needed if you supply a local TeX cache via `MATHGENT_LOCAL_TEX_DIR`. See [data/tex_cache/README.md](data/tex_cache/README.md).

> **Minimal free setup** — `MATHGENT_AGENTIC=0`, `MATHGENT_LIBRARIAN_MODEL=test`, `MATHGENT_RERANKER=token_overlap`, and a local TeX dir. No API keys required.

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
│   └── arxiv/recovery/   Title→arXiv-ID recovery + verification
│   └── cache/            Shared cache utilities (TTL, etc.)
├── extraction/       HeaderGrepper, BoundedBlockExtractor, theorem numbering
├── agents/           ForagerAgent (per-paper extraction + scoring)
├── rerank/           Reranker backends (token overlap, BGE, ColBERT, OpenAI, hybrid)
├── sandbox/          E2B and local LaTeX source runners
├── models/           Domain and API models
├── tools/            Agent tool facades (discovery, extraction)
└── observability/    Loguru logging, Logfire tracing, hook registry
```

---
### Deploy — Vercel (FastAPI + SSE)

This repo includes a Vercel-ready entrypoint:

- `app.py` (FastAPI `app` instance)
- `public/**` (static assets served by Vercel CDN)
- `requirements.txt` (for Vercel's Python runtime)

On Vercel:

1. Import the Git repo as a new project
2. Set Environment Variables (at minimum):
   - `OPENAI_API_KEY` (LLM + reranker)
   - `E2B_API_KEY` (LaTeX extraction sandbox; required unless you run local TeX mode)
   - Optional: `OPENALEX_API_KEY`, `OPENALEX_MAILTO`, `SEMANTIC_SCHOLAR_API_KEY`
3. Deploy

---

## License

MIT — see [LICENSE](LICENSE).
