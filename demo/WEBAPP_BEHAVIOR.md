# Demo Webapp Behavior (Current)

This document describes the *current* behavior of the theorem-search demo webapp as implemented in this repo, based on code inspection + a local run of the FastAPI app.

## What the demo is

- A single-page static UI (no framework) served from `public/index.html` with `public/app.js` + `public/style.css`.
- A FastAPI backend that exposes an SSE endpoint (`text/event-stream`) used by the UI to stream the full search pipeline in real time.

There are two ways to run/serve the demo:

1. **Local demo server**: `demo/app.py` (FastAPI + `demo/static/index.html`).
2. **Vercel-style serverless entrypoint**: `api/index.py` (FastAPI with embedded/static assets + SSE).

The frontend logic is effectively the same in both cases (same HTML structure + the same JS behavior).

## HTTP routes

### Local demo (`demo/app.py`)

- `GET /` → serves `demo/static/index.html`
- `GET /static/*` → serves static assets under `demo/static/`
- `GET /stream` → SSE stream (the UI can point to this in the local demo variant)

### Vercel entrypoint (`api/index.py`)

The FastAPI `app` in `api/index.py` provides:

- `GET /` → serves `public/index.html` if present, otherwise an embedded HTML fallback
- `GET /app.js` → serves `public/app.js` if present, otherwise embedded JS fallback
- `GET /style.css` → serves `public/style.css` if present, otherwise embedded CSS fallback
- `GET /healthz` → `{"ok": true, "service": "pullback-api"}`
- `GET /stream` → SSE stream
- `GET /api/stream` → SSE stream (duplicate path for convenience)

Static asset cache-busting:

- `api/index.py` injects a `?v=<12chars>` query string into `/app.js` and `/style.css` references when serving HTML.
- It derives that version from `VERCEL_GIT_COMMIT_SHA` (preferred), falling back to `VERCEL_URL`, then `"dev"`.

## Frontend UI behavior (`public/*`)

### Main user flow

1. User enters a query in the text input.
2. Search starts on:
   - clicking **Search** button, or
   - pressing **Enter** in the input, or
   - typing (debounced auto-search after ~450ms) if the text differs from the last issued query.
3. The UI opens an SSE connection using:
   - `new EventSource("/api/stream?query=" + encodeURIComponent(query))`
4. As SSE events arrive, the UI:
   - displays pipeline status text,
   - shows query badges (original + generated variants),
   - incrementally adds paper cards once metadata is available,
   - marks papers as “working” while they are being processed,
   - renders matched results (and optionally non-matches in advanced mode),
   - closes the stream and re-enables **Search** when `search_done` arrives.

### Pipeline strip

The pipeline strip (`#pipeline`) is hidden by default and is shown immediately on search start (before the first SSE message arrives).

It contains:

- `#stage-text`: a human-readable stage/status message
- `#pipeline-counts`: counts updated incrementally:
  - `discovered` (cards created with metadata)
  - `reviewed` (papers that have completed execution)
  - `matched` (papers with a theorem match)
- `#adv-btn` (ⓘ): toggles advanced mode
- `#query-badges`: clickable query badges for filtering

### Query badges + filtering

- Badges are shown for:
  - the original query immediately, and
  - additional query variants after the backend emits `queries_planned`.
- Clicking a badge toggles a filter:
  - When active, only papers discovered by that specific query are shown.
  - Clicking again clears the filter.
- In advanced mode, each badge includes a strategy label (e.g. `original`, `synonym`, `keyword`), based on badge index.

### Results list and cards

- Results section is hidden until a `discovery` event arrives with IDs.
- Cards are rendered as papers become known (only created when `title` + `authors` exist).
- Each card shows:
  - title (italic serif),
  - authors,
  - year (if known),
  - citation count (if provided),
  - optional theorem label (right side),
  - arXiv link (`https://arxiv.org/abs/<id>`) that opens in a new tab.

Card expansion:

- Only **matched** papers with a `snippet` are expandable.
- Clicking the card toggles a hidden body that shows:
  - optional `header` (theorem header line), and
  - the extracted theorem snippet (`pre`).

### Sorting

- Default sort is by `score` (descending) *within* matched results.
- Clicking `⇅` toggles between:
  - `score` (default), and
  - `year` (newest first; unknown year sorts last among matched).

### Advanced mode

When advanced mode is enabled:

- Non-matched papers are also shown (e.g. pending / working / no-match).
- Each card shows its numeric score (including low score for no-match / non-matched states).
- Each card may show “attribution chips” indicating which query variant(s) discovered it.
- Badge strategy labels become visible.
- Cards show more state text (e.g. working / no theorem headers found).

When advanced mode is disabled:

- Only matched papers are shown.
- Scores are hidden.
- Strategy labels are hidden.

### Error handling

- SSE errors (`EventSource.onerror`) update the stage text to “Connection error — please try again”, re-enable the search button, and close the stream.
- Backend-sent `{"type": "error"}` events display “Error — <message>” and also close the stream.

## SSE protocol (what the backend streams)

The backend yields SSE events in the standard format:

- Each message is: `data: <JSON>\n\n`
- The frontend parses `e.data` as JSON and dispatches on `ev.type`.

Event types observed/handled by the frontend:

- `query_start`
  - Payload: `{ query, max_results, strictness }`
  - UI effect: updates stage text (“Searching math databases …”).

- `queries_planned`
  - Payload: `{ queries: string[] }`
  - UI effect:
    - merges / de-dupes queries (case/whitespace-insensitive),
    - rebuilds badges (`original` + variants),
    - updates stage text (“Expanding search with N rephrased variants in parallel…”).

- `discovery_start`
  - Payload: `{ query, max_results }`
  - UI effect: informational; stage text generally already set by `query_start`.

- `discovery`
  - Payload: `{ query, arxiv_ids: string[], papers?: PaperMeta[], provider_timeouts?: Record<string, number> }`
  - UI effect:
    - shows results section,
    - creates cards *only* when metadata has `title` and non-empty `authors`,
    - increments `discovered`.

- `metadata_update`
  - Payload: `{ query?: string, papers: PaperMeta[] }`
  - UI effect:
    - updates (or creates) cards with title/authors/year/citation count,
    - supports version-stripped ID updates via an `idAliases` map.

- `worker_start`
  - Payload: `{ query, arxiv_id }`
  - UI effect: sets card state to `working` with `substatus = "fetching…"`.

- `plan_complete`
  - Payload: `{ arxiv_id, header_count, reason }`
  - UI effect: sets `substatus` like:
    - “no theorem headers found” (if `reason === "no_headers"`), or
    - “<N> headers found”.

- `execute_complete`
  - Payload: `{ arxiv_id, matched: boolean, score: number, snippet, header, label, query }`
  - UI effect:
    - increments `reviewed`,
    - sets card `state` to `matched` or `no-match`,
    - increments `matched` when `matched=true`,
    - sets snippet/header/label when present,
    - updates stage text (“Extracting theorems — X papers scanned, Y matches”).

- `search_done`
  - Payload: `{ matched: number, total: number, latency_s: number }`
  - UI effect:
    - closes SSE,
    - re-enables search,
    - updates stage text (“Found … across … papers · …s”).

### Paper metadata shape

The frontend expects (from `discovery` and/or `metadata_update`):

```json
{
  "arxiv_id": "1511.04069",
  "title": "…",
  "authors": ["…", "…"],
  "year": 2015,
  "cited_by_count": 123
}
```

## Backend streaming implementation notes

The stream generator is implemented in `demo/stream.py` and is used both by:

- `demo/app.py` (local demo), and
- `api/index.py` (Vercel entrypoint).

Key behaviors:

- It monkey-patches `orch._query_attempts` to emit `queries_planned` early so the UI can show the original seed query before LLM query-planning completes.
- It emits metadata opportunistically:
  - uses provider-supplied metadata when available,
  - does a short (~2s) “best effort” wait for missing metadata before exposing the paper in `discovery`,
  - falls back to background metadata fetch tasks.
- It closes the orchestrator (`orch.close()`) after the search finishes (or errors).

## Deployment-critical assumptions (what must keep working)

The core product behavior depends on:

- **SSE streaming** being delivered as true streamed chunks (not buffered) with:
  - `Content-Type: text/event-stream`
  - `Cache-Control: no-cache` / `no-transform` style directives
- **Long-lived connections** staying open long enough for:
  - query planning,
  - multi-provider discovery,
  - per-paper theorem extraction + scoring.
- The frontend’s `EventSource("/api/stream?...")` resolving correctly on the deployed origin.
- External network access from the runtime to:
  - discovery providers (OpenAlex / arXiv / zbMATH / Semantic Scholar),
  - LLM and reranker providers (OpenAI),
  - (optionally) sandbox/extraction services (E2B), depending on configuration.

