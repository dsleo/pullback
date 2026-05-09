"""Live browser demo for mathgent — streams pipeline events via SSE.

Run:
    set -a && source .env.local && set +a
    python scripts/demo.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mathgent.api.deps import build_orchestrator  # noqa: E402

app = FastAPI(title="mathgent demo")

# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mathgent</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Jost:wght@200;300;400;500;600&family=JetBrains+Mono:wght@300;400;500&family=Lora:ital,wght@1,400;1,500&display=swap" rel="stylesheet">
<style>
  /* ── Oxford Blue tokens ──────────────────────────────────────────── */
  :root {
    --c-bg:          #f8f9fc;
    --c-bg-alt:      #f1f5f9;
    --c-bg-inset:    #eef2f7;
    --c-surface:     #ffffff;
    --c-navy:        #0f2d5a;
    --c-navy-mid:    #1e4080;
    --c-navy-light:  #dbeafe;
    --c-fg:          #0f172a;
    --c-fg-2:        #475569;
    --c-fg-3:        #94a3b8;
    --c-border:      #e2e8f0;
    --c-border-mid:  #cbd5e1;
    --f-sans:   "Jost", system-ui, sans-serif;
    --f-mono:   "JetBrains Mono", monospace;
    --f-serif:  "Lora", Georgia, serif;
  }

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { background: var(--c-bg); color: var(--c-fg); }
  body { font-family: var(--f-sans); font-weight: 300; font-size: 15px;
         -webkit-font-smoothing: antialiased; }

  /* ── header ─────────────────────────────────────────────────────────── */
  header {
    background: var(--c-navy);
    padding: 18px 48px;
    display: flex; align-items: baseline; gap: 18px;
  }
  header h1 {
    font-family: var(--f-sans); font-weight: 300; font-size: 1.35rem;
    letter-spacing: -.01em; color: #fff;
  }
  header span {
    font-family: var(--f-mono); font-size: .62rem; letter-spacing: .16em;
    text-transform: uppercase; color: rgba(255,255,255,.4);
  }

  /* ── search bar ──────────────────────────────────────────────────────── */
  .search-bar {
    background: var(--c-surface);
    border-bottom: 1px solid var(--c-border);
    padding: 16px 48px; display: flex; gap: 14px; align-items: center;
    box-shadow: 0 1px 3px rgba(15,45,90,.06);
  }
  .search-bar input[type=text] {
    flex: 1; border: 1px solid var(--c-border-mid); border-radius: 4px;
    background: var(--c-bg); padding: 8px 12px; font-family: var(--f-sans);
    font-size: .92rem; font-weight: 300; color: var(--c-fg); outline: none;
    transition: border-color .15s;
  }
  .search-bar input[type=text]::placeholder { color: var(--c-fg-3); }
  .search-bar input[type=text]:focus {
    border-color: var(--c-navy-mid);
    box-shadow: 0 0 0 3px rgba(30,64,128,.1);
  }
  .search-bar label {
    font-family: var(--f-mono); font-size: .62rem; letter-spacing: .1em;
    text-transform: uppercase; color: var(--c-fg-3);
    display: flex; align-items: center; gap: 7px;
  }
  .search-bar input[type=number] {
    width: 52px; border: 1px solid var(--c-border-mid); border-radius: 4px;
    background: var(--c-bg); padding: 7px 6px; font-family: var(--f-mono);
    font-size: .85rem; color: var(--c-fg); outline: none; text-align: center;
  }
  #search-btn {
    font-family: var(--f-mono); font-size: .62rem; font-weight: 500;
    letter-spacing: .12em; text-transform: uppercase;
    background: var(--c-navy); color: #fff;
    border: none; border-radius: 4px; padding: 9px 22px; cursor: pointer;
    transition: background .15s;
  }
  #search-btn:hover { background: var(--c-navy-mid); }
  #search-btn:disabled { opacity: .4; cursor: default; }

  /* ── main ────────────────────────────────────────────────────────────── */
  .main { max-width: 880px; margin: 36px auto; padding: 0 48px;
          display: flex; flex-direction: column; gap: 24px; }

  /* ── status ──────────────────────────────────────────────────────────── */
  #status-text {
    font-family: var(--f-mono); font-size: .68rem; letter-spacing: .06em;
    color: var(--c-fg-3); min-height: 18px;
  }

  /* ── stats bar ───────────────────────────────────────────────────────── */
  .stats-bar {
    display: flex; gap: 0;
    border: 1px solid var(--c-border); border-radius: 6px;
    background: var(--c-surface); overflow: hidden;
    box-shadow: 0 1px 3px rgba(15,45,90,.05);
  }
  .stat-chip {
    flex: 1; padding: 12px 18px; border-right: 1px solid var(--c-border);
    display: flex; flex-direction: column; gap: 4px;
  }
  .stat-chip:last-child { border-right: none; }
  .stat-chip .slabel {
    font-family: var(--f-mono); font-size: .56rem; letter-spacing: .16em;
    text-transform: uppercase; color: var(--c-fg-3);
  }
  .stat-chip .val {
    font-family: var(--f-sans); font-weight: 300; font-size: 1.7rem;
    line-height: 1; color: var(--c-navy);
  }

  /* ── query panel ──────────────────────────────────────────────────────── */
  .section-label {
    font-family: var(--f-mono); font-size: .6rem; letter-spacing: .16em;
    text-transform: uppercase; color: var(--c-fg-3);
    padding-bottom: 8px; border-bottom: 1px solid var(--c-border);
  }
  .query-panel { display: flex; flex-direction: column; gap: 12px; }
  .query-list  { display: flex; flex-wrap: wrap; gap: 6px; min-height: 28px; }
  .query-badge {
    display: inline-flex; align-items: baseline; gap: 7px;
    border: 1px solid var(--c-navy); border-radius: 3px;
    padding: 5px 11px; background: var(--c-navy-light);
  }
  .query-badge.variant {
    border-color: var(--c-border-mid); background: var(--c-surface);
  }
  .qlabel {
    font-family: var(--f-mono); font-size: .56rem; letter-spacing: .14em;
    text-transform: uppercase; color: var(--c-navy);
  }
  .query-badge.variant .qlabel { color: var(--c-fg-3); }
  .qtext { font-size: .82rem; font-weight: 300; color: var(--c-fg); }

  /* ── paper cards ──────────────────────────────────────────────────────── */
  #papers {
    display: flex; flex-direction: column; gap: 8px;
  }

  .paper-card {
    background: var(--c-surface);
    border: 1px solid var(--c-border);
    border-radius: 6px;
    transition: border-color .15s, box-shadow .15s;
  }
  .paper-card.matched {
    border-left: 3px solid var(--c-navy);
    box-shadow: 0 1px 4px rgba(15,45,90,.08);
  }
  .paper-card.no-match { opacity: .5; }
  .paper-card.pending  { opacity: .35; }

  .card-toggle {
    width: 100%; background: none; border: none; cursor: pointer;
    padding: 14px 16px; display: grid;
    grid-template-columns: 24px 1fr auto;
    gap: 10px; text-align: left; align-items: start;
    border-radius: 6px;
  }
  .card-toggle:hover { background: var(--c-bg-inset); }

  .card-chevron {
    font-family: var(--f-mono); font-size: .6rem; color: var(--c-fg-3);
    padding-top: 4px; justify-self: center; transition: transform .12s;
    user-select: none;
  }
  .card-chevron.open { transform: rotate(90deg); }

  .card-main { min-width: 0; display: flex; flex-direction: column; gap: 3px; }
  .card-title {
    font-family: var(--f-serif); font-style: italic;
    font-size: .93rem; font-weight: 400; color: var(--c-fg);
    line-height: 1.35;
  }
  .card-authors {
    font-family: var(--f-sans); font-size: .74rem; font-weight: 300;
    color: var(--c-fg-2);
  }
  .card-arxiv-id {
    font-family: var(--f-mono); font-size: .68rem; color: var(--c-fg-3);
  }
  .card-substatus {
    font-family: var(--f-mono); font-size: .6rem; letter-spacing: .04em;
    color: var(--c-fg-3); margin-top: 2px;
  }

  .card-right {
    display: flex; flex-direction: column; align-items: flex-end;
    gap: 5px; flex-shrink: 0;
  }
  .card-score {
    font-family: var(--f-mono); font-size: .78rem; font-weight: 500;
    color: var(--c-navy);
  }
  .card-score.low { color: var(--c-fg-3); font-weight: 400; }
  .arxiv-link {
    font-family: var(--f-mono); font-size: .6rem; letter-spacing: .05em;
    color: var(--c-navy-mid); text-decoration: none;
  }
  .arxiv-link:hover { text-decoration: underline; }
  .card-icon {
    font-family: var(--f-mono); font-size: .72rem; color: var(--c-fg-3);
    padding-top: 3px; justify-self: center;
  }
  .paper-card.matched .card-icon { color: var(--c-navy); }

  .card-body { padding: 0 16px 14px 50px; }
  .snippet-box {
    background: var(--c-bg-alt); border: 1px solid var(--c-border);
    border-radius: 4px;
    padding: 12px 14px; font-family: var(--f-mono); font-size: .74rem;
    font-weight: 300; color: var(--c-fg); white-space: pre-wrap;
    line-height: 1.65;
  }
  .header-label {
    font-family: var(--f-mono); font-size: .6rem; letter-spacing: .1em;
    text-transform: uppercase; color: var(--c-navy-mid); margin-bottom: 6px;
  }

  @keyframes spin { to { transform: rotate(360deg); } }
  .spinner { display: inline-block; animation: spin .9s linear infinite; }
</style>
</head>
<body>

<header>
  <h1>mathgent</h1>
  <span>theorem search</span>
</header>

<div class="search-bar">
  <input type="text" id="query" placeholder="e.g. Banach fixed point theorem"
         value="Banach fixed point theorem" />
  <label>papers <input type="number" id="max-results" value="5" min="1" max="20" /></label>
  <button id="search-btn" onclick="startSearch()">Search</button>
</div>

<div class="main">
  <div id="status-text"></div>

  <!-- Stats bar -->
  <div class="stats-bar" id="stats-bar" style="display:none">
    <div class="stat-chip">
      <span class="slabel">Discovered</span>
      <span class="val" id="s-discovered">0</span>
    </div>
    <div class="stat-chip">
      <span class="slabel">Reviewed</span>
      <span class="val" id="s-reviewed">0</span>
    </div>
    <div class="stat-chip">
      <span class="slabel">Matched</span>
      <span class="val" id="s-matched">0</span>
    </div>
    <div class="stat-chip">
      <span class="slabel">Queries</span>
      <span class="val" id="s-queries">0</span>
    </div>
  </div>

  <!-- Query panel -->
  <div class="query-panel" id="query-panel" style="display:none">
    <div class="section-label" id="query-panel-label">Planning queries</div>
    <div class="query-list" id="query-badges"></div>
  </div>

  <!-- Results -->
  <div>
    <div class="section-label" id="papers-label" style="display:none">Results</div>
    <div id="papers" style="margin-top:12px"></div>
  </div>
</div>

<script>
let es = null;

const paperData = {};
let discovered = 0, reviewed = 0, matched = 0, queriesCount = 0;

function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function sortKey(p) {
  if (p.state === 'matched')   return 0 - (p.score || 0);
  if (p.state === 'no-match')  return 10 - (p.score || 0);
  if (p.state === 'working')   return 100;
  return 200;
}

function renderStats() {
  document.getElementById('s-discovered').textContent = discovered;
  document.getElementById('s-reviewed').textContent   = reviewed;
  document.getElementById('s-matched').textContent    = matched;
  document.getElementById('s-queries').textContent    = queriesCount;
}

function renderPapers() {
  const container = document.getElementById('papers');
  const sorted = Object.values(paperData).sort((a, b) => sortKey(a) - sortKey(b));

  sorted.forEach(p => {
    let card = document.getElementById('card-' + p.id);
    container.appendChild(card || (() => {
      const d = document.createElement('div');
      d.id = 'card-' + p.id;
      return d;
    })());
    card = document.getElementById('card-' + p.id);

    const stateClass = p.state || 'pending';
    card.className = 'paper-card ' + stateClass;

    const icon =
      p.state === 'matched'  ? '✓' :
      p.state === 'no-match' ? '✗' :
      p.state === 'working'  ? '<span class="spinner">·</span>' : '·';

    const scoreHtml = (p.score != null)
      ? `<div class="card-score${p.state !== 'matched' ? ' low' : ''}">${p.score.toFixed(3)}</div>`
      : '';

    const titleHtml = p.title
      ? `<div class="card-title">${esc(p.title)}</div>`
      : `<div class="card-arxiv-id">${esc(p.id)}</div>`;

    const authorsHtml = (p.authors && p.authors.length)
      ? `<div class="card-authors">${esc(p.authors.join(', '))}</div>`
      : (p.title ? `<div class="card-arxiv-id">${esc(p.id)}</div>` : '');

    const subHtml = p.substatus
      ? `<div class="card-substatus">${esc(p.substatus)}</div>` : '';

    const isOpen   = card.dataset.open === '1';
    const hasBody  = p.state === 'matched' && p.snippet;
    const chevHtml = hasBody
      ? `<span class="card-chevron${isOpen ? ' open' : ''}" id="chev-${p.id}">▶</span>`
      : `<span></span>`;

    const bodyHtml = hasBody
      ? `<div class="card-body" ${isOpen ? '' : 'style="display:none"'}>
           <div class="snippet-box">` +
             (p.header ? `<div class="header-label">${esc(p.header)}</div>` : '') +
             esc(p.snippet) +
         `</div></div>`
      : '';

    card.innerHTML = `
      <button class="card-toggle" onclick="toggleCard('${p.id}')">
        ${chevHtml}
        <div class="card-main">
          ${titleHtml}
          ${authorsHtml}
          ${subHtml}
        </div>
        <div class="card-right">
          ${scoreHtml}
          <a class="arxiv-link" href="https://arxiv.org/abs/${esc(p.id)}" target="_blank"
             onclick="event.stopPropagation()">↗ arXiv</a>
        </div>
      </button>
      ${bodyHtml}`;
  });
}

function toggleCard(id) {
  const card = document.getElementById('card-' + id);
  if (!card) return;
  const body = card.querySelector('.card-body');
  const chev = document.getElementById('chev-' + id);
  if (!body) return;
  const isOpen = card.dataset.open === '1';
  card.dataset.open = isOpen ? '0' : '1';
  body.style.display = isOpen ? 'none' : '';
  if (chev) chev.className = isOpen ? 'card-chevron' : 'card-chevron open';
}

function handle(ev) {
  const status = document.getElementById('status-text');

  if (ev.type === 'query_start') {
    status.textContent = 'Searching…';
    document.getElementById('stats-bar').style.display = 'flex';
    document.getElementById('query-panel').style.display = '';
    document.getElementById('papers-label').style.display = '';
  }

  else if (ev.type === 'queries_planned') {
    document.getElementById('query-panel-label').textContent = 'Query variants';
    const container = document.getElementById('query-badges');
    container.innerHTML = '';
    queriesCount = ev.queries.length;
    ev.queries.forEach((q, i) => {
      const badge = document.createElement('div');
      badge.className = 'query-badge' + (i > 0 ? ' variant' : '');
      badge.title = q;
      badge.innerHTML = `<span class="qlabel">${i === 0 ? 'original' : 'variant ' + i}</span>`
                      + `<span class="qtext">${esc(q)}</span>`;
      container.appendChild(badge);
    });
    renderStats();
  }

  else if (ev.type === 'discovery') {
    if (ev.arxiv_ids && ev.arxiv_ids.length) {
      ev.arxiv_ids.forEach(id => {
        if (!paperData[id]) {
          paperData[id] = { id, title: null, authors: null, score: null,
                            state: 'pending', snippet: null, header: null, substatus: null };
          discovered++;
        }
      });
      renderStats(); renderPapers();
    }
  }

  else if (ev.type === 'worker_start') {
    const p = paperData[ev.arxiv_id];
    if (p) { p.state = 'working'; p.substatus = 'fetching…'; renderPapers(); }
  }

  else if (ev.type === 'plan_complete') {
    const p = paperData[ev.arxiv_id];
    if (!p) return;
    p.substatus = ev.reason === 'no_headers'
      ? 'no theorem headers found'
      : `${ev.header_count} header${ev.header_count !== 1 ? 's' : ''} found`;
    renderPapers();
  }

  else if (ev.type === 'execute_complete') {
    const p = paperData[ev.arxiv_id];
    if (!p) return;
    reviewed++;
    p.score    = ev.score;
    p.snippet  = ev.snippet;
    p.header   = ev.header;
    p.substatus = null;
    p.state    = ev.matched ? 'matched' : 'no-match';
    if (ev.matched) matched++;
    renderStats(); renderPapers();
    status.textContent = `Reviewing… ${reviewed} done, ${matched} matched`;
  }

  else if (ev.type === 'search_done') {
    es.close(); es = null;
    document.getElementById('search-btn').disabled = false;
    status.textContent = `${ev.matched} match${ev.matched !== 1 ? 'es' : ''} · ${ev.total} papers reviewed · ${ev.latency_s.toFixed(1)}s`;
  }

  else if (ev.type === 'error') {
    status.textContent = 'error — ' + esc(ev.message);
    document.getElementById('search-btn').disabled = false;
    if (es) { es.close(); es = null; }
  }
}

function startSearch() {
  if (es) { es.close(); es = null; }
  Object.keys(paperData).forEach(k => delete paperData[k]);
  discovered = reviewed = matched = queriesCount = 0;
  document.getElementById('papers').innerHTML = '';
  document.getElementById('query-badges').innerHTML = '';
  document.getElementById('query-panel-label').textContent = 'Planning queries';
  document.getElementById('status-text').textContent = '';
  document.getElementById('stats-bar').style.display   = 'none';
  document.getElementById('query-panel').style.display = 'none';
  document.getElementById('papers-label').style.display = 'none';
  document.getElementById('search-btn').disabled = true;

  const query = document.getElementById('query').value.trim();
  const maxR  = document.getElementById('max-results').value;
  if (!query) { document.getElementById('search-btn').disabled = false; return; }

  es = new EventSource(`/stream?query=${encodeURIComponent(query)}&max_results=${maxR}`);
  es.onmessage = e => handle(JSON.parse(e.data));
  es.onerror   = () => {
    document.getElementById('status-text').textContent = 'connection error';
    document.getElementById('search-btn').disabled = false;
    if (es) { es.close(); es = null; }
  };
}

document.getElementById('query').addEventListener('keydown', e => {
  if (e.key === 'Enter') startSearch();
});
</script>
</body>
</html>
"""




# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(_HTML)


@app.get("/stream")
async def stream(
    query: str = "Banach fixed point theorem",
    max_results: int = 5,
    strictness: float = 0.0,
) -> StreamingResponse:
    return StreamingResponse(
        _search_stream(query, max_results, strictness),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _search_stream(query: str, max_results: int, strictness: float):
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def push(payload: dict) -> None:
        await queue.put(payload)

    orch = build_orchestrator()
    forager = orch.forager

    # ── intercept _query_attempts to emit queries_planned before discovery ──
    _orig_query_attempts = orch._query_attempts

    async def _patched_query_attempts(q: str) -> list[str]:
        attempts = await _orig_query_attempts(q)
        await push({"type": "queries_planned", "queries": attempts})
        return attempts

    orch._query_attempts = _patched_query_attempts  # type: ignore[method-assign]

    # ── librarian hooks ──────────────────────────────────────────────────────

    async def on_search_start(*, query, max_results, **_):
        await push({"type": "query_start", "query": query,
                    "max_results": max_results, "strictness": strictness})

    # Track which IDs have already had metadata fetched to avoid duplicate calls
    fetched_metadata_ids: set[str] = set()

    async def _fetch_and_push_metadata(ids: list[str]) -> None:
        if orch._metadata_fetcher is None:
            return
        new_ids = [aid for aid in ids if aid not in fetched_metadata_ids]
        if not new_ids:
            return
        fetched_metadata_ids.update(new_ids)
        try:
            meta = await orch._metadata_fetcher(new_ids)
            if meta:
                papers = [
                    {"arxiv_id": aid, "title": m.title, "authors": list(m.authors or [])}
                    for aid, m in meta.items()
                    if m.title
                ]
                if papers:
                    await push({"type": "metadata_update", "papers": papers})
        except Exception:
            pass  # metadata is best-effort

    async def on_discovery_done(*, query, arxiv_ids, **_):
        await push({"type": "discovery", "query": query, "arxiv_ids": list(arxiv_ids)})
        # Kick off metadata fetch in background so titles appear immediately
        if arxiv_ids:
            asyncio.create_task(_fetch_and_push_metadata(list(arxiv_ids)))

    async def on_worker_start(*, state, **_):
        await push({"type": "worker_start", "arxiv_id": state.arxiv_id})

    async def on_worker_done(*, state, result, **_):
        m = result.match
        if m is not None and m.score >= strictness:
            await push({"type": "execute_complete", "arxiv_id": state.arxiv_id,
                        "matched": True, "score": m.score,
                        "snippet": m.snippet, "header": m.header_line})
        else:
            await push({"type": "execute_complete", "arxiv_id": state.arxiv_id,
                        "matched": False, "score": m.score if m else 0.0,
                        "snippet": None, "header": None})

    async def on_search_done(*, results, matched, latency_s, **_):
        await push({"type": "search_done", "matched": matched,
                    "total": len(results), "latency_s": latency_s})
        await queue.put(None)

    orch.on("search_start",   on_search_start)
    orch.on("discovery_done", on_discovery_done)
    orch.on("worker_start",   on_worker_start)
    orch.on("worker_done",    on_worker_done)
    orch.on("search_done",    on_search_done)

    # ── forager hooks ────────────────────────────────────────────────────────

    async def on_plan_complete(*, plan, reason, **_):
        if plan is None:
            return
        await push({"type": "plan_complete", "arxiv_id": plan.arxiv_id,
                    "header_count": len(plan.headers), "reason": reason})

    forager.on("plan_complete", on_plan_complete)

    # ── run search in background ─────────────────────────────────────────────

    async def _run():
        try:
            await orch.search(query, max_results=max_results, strictness=strictness)
        except Exception as exc:
            await push({"type": "error", "message": str(exc)})
            await queue.put(None)
        finally:
            if orch.tools is not None:
                orch.close()

    task = asyncio.create_task(_run())

    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"
    except asyncio.CancelledError:
        task.cancel()
    finally:
        if not task.done():
            task.cancel()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = 7860
    url = f"http://localhost:{port}"
    print(f"\n  mathgent demo → {url}\n")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
