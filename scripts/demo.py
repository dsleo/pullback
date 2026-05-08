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
<title>mathgent demo</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #f5f5f7; color: #1d1d1f; }

  /* ── header ── */
  header { background: #1d1d1f; color: #fff; padding: 14px 28px;
           display: flex; align-items: center; gap: 14px; }
  header h1 { font-size: 1.15rem; font-weight: 700; letter-spacing: -.4px; }
  header span { font-size: .8rem; opacity: .45; }

  /* ── search bar ── */
  .search-bar { background: #fff; border-bottom: 1px solid #e0e0e0;
                padding: 16px 28px; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
  .search-bar input[type=text] { flex: 1; min-width: 200px; padding: 9px 13px;
    border: 1px solid #ccc; border-radius: 8px; font-size: .95rem; outline: none; }
  .search-bar input[type=text]:focus { border-color: #0066cc;
    box-shadow: 0 0 0 3px rgba(0,102,204,.12); }
  .search-bar input[type=number] { width: 72px; padding: 9px 8px;
    border: 1px solid #ccc; border-radius: 8px; font-size: .95rem; outline: none; }
  .search-bar label { font-size: .82rem; color: #666; white-space: nowrap;
                      display: flex; align-items: center; gap: 5px; }
  #search-btn { padding: 9px 20px; background: #0066cc; color: #fff; border: none;
    border-radius: 8px; font-size: .95rem; font-weight: 500; cursor: pointer; }
  #search-btn:hover { background: #0055aa; }
  #search-btn:disabled { background: #aaa; cursor: default; }

  /* ── main layout ── */
  .main { max-width: 960px; margin: 24px auto; padding: 0 20px; display: flex;
          flex-direction: column; gap: 16px; }

  /* ── stats bar ── */
  .stats-bar { display: flex; gap: 10px; flex-wrap: wrap; }
  .stat-chip { background: #fff; border: 1px solid #e0e0e0; border-radius: 20px;
    padding: 5px 14px; font-size: .82rem; color: #555; display: flex; align-items: center; gap: 6px; }
  .stat-chip .val { font-weight: 700; color: #1d1d1f; }
  .stat-chip.accent { border-color: #a5d6a7; background: #f1f8f1; }

  /* ── query panel ── */
  .panel { background: #fff; border: 1px solid #e0e0e0; border-radius: 10px; overflow: hidden; }
  .panel-head { padding: 10px 16px; font-size: .82rem; font-weight: 600; color: #555;
                text-transform: uppercase; letter-spacing: .5px; border-bottom: 1px solid #f0f0f0;
                display: flex; align-items: center; gap: 8px; }
  .panel-body { padding: 12px 16px; display: flex; flex-wrap: wrap; gap: 8px; min-height: 38px; }

  .query-badge { display: flex; align-items: center; gap: 6px; border-radius: 20px;
                 padding: 5px 12px; font-size: .82rem; border: 1px solid; cursor: default; }
  .query-badge.original { background: #e8f0fe; border-color: #a8c3fb; color: #1a5dc5; }
  .query-badge.variant  { background: #f3e8fd; border-color: #cca8f6; color: #6a1fa8; }
  .query-badge .qlabel  { font-size: .72rem; font-weight: 700; opacity: .7; text-transform: uppercase; }
  .query-badge .qtext   { max-width: 260px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  /* ── paper cards ── */
  #papers { display: flex; flex-direction: column; gap: 8px; }

  .paper-card { background: #fff; border: 1px solid #e0e0e0; border-radius: 10px; overflow: hidden;
                transition: border-color .15s; }
  .paper-card.matched  { border-left: 4px solid #27ae60; }
  .paper-card.no-match { border-left: 4px solid #ccc; opacity: .72; }
  .paper-card.working  { border-left: 4px solid #f0a500; }
  .paper-card.pending  { border-left: 4px solid #ddd; opacity: .6; }

  .card-toggle { width: 100%; background: none; border: none; cursor: pointer; padding: 12px 16px;
                 display: flex; align-items: flex-start; gap: 10px; text-align: left; }
  .card-toggle:hover { background: #fafafa; }

  .card-chevron { font-size: .75rem; color: #aaa; margin-top: 3px; flex-shrink: 0;
                  transition: transform .15s; }
  .card-chevron.open { transform: rotate(90deg); }

  .card-icon { font-size: 1rem; width: 20px; text-align: center; flex-shrink: 0; margin-top: 1px; }
  .card-main { flex: 1; min-width: 0; }

  .card-title { font-size: .9rem; font-weight: 600; color: #1d1d1f;
                white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .card-authors { font-size: .78rem; color: #777; margin-top: 2px;
                  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .card-arxiv-id { font-size: .75rem; color: #aaa; }

  .card-right { display: flex; flex-direction: column; align-items: flex-end;
                flex-shrink: 0; gap: 4px; }
  .card-score { font-size: .9rem; font-weight: 700; color: #27ae60; }
  .card-score.low { color: #999; }
  .arxiv-link { font-size: .75rem; color: #0066cc; text-decoration: none; white-space: nowrap; }
  .arxiv-link:hover { text-decoration: underline; }
  .card-substatus { font-size: .75rem; color: #aaa; }

  .card-body { padding: 0 16px 12px 46px; }
  .snippet-box { background: #f8f8f8; border: 1px solid #ebebeb; border-radius: 6px;
    padding: 10px 14px; font-family: monospace; font-size: .8rem; color: #333;
    white-space: pre-wrap; line-height: 1.55; }
  .header-label { font-size: .75rem; font-weight: 700; color: #27ae60; margin-bottom: 5px; }

  /* ── status / summary ── */
  #status-text { font-size: .85rem; color: #777; min-height: 20px; }
  .summary-banner { background: #e8f5e9; border: 1px solid #a5d6a7; border-radius: 10px;
    padding: 12px 18px; font-size: .92rem; color: #2e7d32; font-weight: 500; }

  @keyframes spin { to { transform: rotate(360deg); } }
  .spinner { display: inline-block; animation: spin .9s linear infinite; }
</style>
</head>
<body>

<header>
  <h1>mathgent</h1>
  <span>live theorem search</span>
</header>

<div class="search-bar">
  <input type="text" id="query" placeholder="e.g. Banach fixed point theorem"
         value="Banach fixed point theorem" />
  <label>results <input type="number" id="max-results" value="5" min="1" max="20" /></label>
  <label>strictness <input type="number" id="strictness" value="0.2" min="0" max="1" step="0.05" style="width:68px" /></label>
  <button id="search-btn" onclick="startSearch()">Search</button>
</div>

<div class="main">
  <div id="status-text"></div>

  <!-- Stats bar -->
  <div class="stats-bar" id="stats-bar" style="display:none">
    <div class="stat-chip"><span>📄</span> Discovered <span class="val" id="s-discovered">0</span></div>
    <div class="stat-chip"><span>🔍</span> Reviewed <span class="val" id="s-reviewed">0</span></div>
    <div class="stat-chip accent"><span>✓</span> Matched <span class="val" id="s-matched">0</span></div>
    <div class="stat-chip"><span>💬</span> Queries <span class="val" id="s-queries">0</span></div>
  </div>

  <!-- Query panel -->
  <div class="panel" id="query-panel" style="display:none">
    <div class="panel-head">⟳ <span id="query-panel-label">Planning queries…</span></div>
    <div class="panel-body" id="query-badges"></div>
  </div>

  <!-- Results -->
  <div id="papers"></div>
</div>

<script>
let es = null;

// State
const paperData = {};   // arxiv_id → {title, authors, score, matched, state, snippet, header, substatus}
let discovered = 0, reviewed = 0, matched = 0, queriesCount = 0;
let strictnessVal = 0.2;

// ── helpers ─────────────────────────────────────────────────────────────────

function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function scoreColor(score, isMatched) {
  if (!isMatched) return 'low';
  return '';
}

function sortKey(p) {
  if (p.state === 'matched')   return 0 - (p.score || 0);
  if (p.state === 'no-match')  return 10 - (p.score || 0);
  if (p.state === 'working')   return 100;
  return 200;  // pending
}

// ── rendering ───────────────────────────────────────────────────────────────

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
    const isNew = !card;

    if (isNew) {
      card = document.createElement('div');
      card.id = 'card-' + p.id;
      container.appendChild(card);
    }

    // Reorder DOM to match sort
    container.appendChild(card);

    const stateClass = p.state || 'pending';
    card.className = 'paper-card ' + stateClass;

    const icon =
      p.state === 'matched'  ? '✓' :
      p.state === 'no-match' ? '✗' :
      p.state === 'working'  ? '<span class="spinner">⟳</span>' : '·';

    const scoreHtml = (p.score != null)
      ? `<div class="card-score ${scoreColor(p.score, p.state === 'matched')}">${p.score.toFixed(3)}</div>`
      : '';

    const titleHtml = p.title
      ? `<div class="card-title">${esc(p.title)}</div>`
      : `<div class="card-title card-arxiv-id">${esc(p.id)}</div>`;

    const authorsHtml = p.authors && p.authors.length
      ? `<div class="card-authors">${esc(p.authors.join(', '))}</div>`
      : (p.title ? `<div class="card-arxiv-id">${esc(p.id)}</div>` : '');

    const substatusHtml = p.substatus
      ? `<div class="card-substatus">${esc(p.substatus)}</div>`
      : '';

    const isOpen = card.dataset.open === '1';
    const chevronClass = isOpen ? 'card-chevron open' : 'card-chevron';

    const bodyHtml = (p.state === 'matched' && p.snippet)
      ? `<div class="card-body" ${isOpen ? '' : 'style="display:none"'}>
           <div class="snippet-box">` +
             (p.header ? `<div class="header-label">${esc(p.header)}</div>` : '') +
             esc(p.snippet) +
         `</div></div>`
      : '';

    const hasBody = p.state === 'matched' && p.snippet;

    card.innerHTML = `
      <button class="card-toggle" onclick="toggleCard('${p.id}')">
        ${hasBody ? `<span class="${chevronClass}" id="chev-${p.id}">▶</span>` : '<span style="width:16px;flex-shrink:0"></span>'}
        <span class="card-icon">${icon}</span>
        <div class="card-main">
          ${titleHtml}
          ${authorsHtml}
          ${substatusHtml}
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

// ── event handlers ──────────────────────────────────────────────────────────

function handle(ev) {
  const status = document.getElementById('status-text');

  if (ev.type === 'query_start') {
    status.textContent = `Searching: "${esc(ev.query)}"`;
    document.getElementById('stats-bar').style.display = 'flex';
    document.getElementById('query-panel').style.display = '';
    strictnessVal = ev.strictness || 0.2;
  }

  else if (ev.type === 'queries_planned') {
    document.getElementById('query-panel-label').textContent = 'Query variants';
    const container = document.getElementById('query-badges');
    container.innerHTML = '';
    queriesCount = ev.queries.length;
    ev.queries.forEach((q, i) => {
      const isOrig = i === 0;
      const badge = document.createElement('div');
      badge.className = 'query-badge ' + (isOrig ? 'original' : 'variant');
      badge.title = q;
      badge.innerHTML = `<span class="qlabel">${isOrig ? 'original' : 'variant ' + i}</span>`
                      + `<span class="qtext">${esc(q)}</span>`;
      container.appendChild(badge);
    });
    renderStats();
  }

  else if (ev.type === 'discovery') {
    if (ev.arxiv_ids && ev.arxiv_ids.length) {
      ev.arxiv_ids.forEach(id => {
        if (!paperData[id]) {
          paperData[id] = { id, title: null, authors: null, score: null, matched: false,
                            state: 'pending', snippet: null, header: null, substatus: null };
          discovered++;
        }
      });
      renderStats();
      renderPapers();
    }
  }

  else if (ev.type === 'worker_start') {
    const p = paperData[ev.arxiv_id];
    if (p) { p.state = 'working'; p.substatus = 'fetching headers…'; renderPapers(); }
  }

  else if (ev.type === 'plan_complete') {
    const p = paperData[ev.arxiv_id];
    if (!p) return;
    if (ev.reason === 'no_headers') {
      p.substatus = 'no theorem-like headers found';
    } else {
      p.substatus = `scoring ${ev.header_count} header${ev.header_count !== 1 ? 's' : ''}…`;
    }
    renderPapers();
  }

  else if (ev.type === 'execute_complete') {
    const p = paperData[ev.arxiv_id];
    if (!p) return;
    reviewed++;
    p.score     = ev.score;
    p.snippet   = ev.snippet;
    p.header    = ev.header;
    p.substatus = null;
    if (ev.matched) {
      p.state   = 'matched';
      matched++;
    } else {
      p.state = 'no-match';
    }
    renderStats();
    renderPapers();
    status.textContent = `Processing… (${reviewed} reviewed, ${matched} matched so far)`;
  }

  else if (ev.type === 'metadata_update') {
    ev.papers.forEach(m => {
      const p = paperData[m.arxiv_id];
      if (p) {
        if (m.title)   p.title   = m.title;
        if (m.authors) p.authors = m.authors;
      }
    });
    renderPapers();
  }

  else if (ev.type === 'search_done') {
    es.close(); es = null;
    document.getElementById('search-btn').disabled = false;
    // Apply metadata from final results
    if (ev.papers) {
      ev.papers.forEach(m => {
        const p = paperData[m.arxiv_id];
        if (p) {
          if (m.title)   p.title   = m.title;
          if (m.authors) p.authors = m.authors;
        }
      });
      renderPapers();
    }
    const banner = document.createElement('div');
    banner.className = 'summary-banner';
    banner.textContent = `Done — ${ev.matched} match${ev.matched !== 1 ? 'es' : ''} from ${ev.total} papers · ${ev.latency_s.toFixed(1)}s`;
    document.getElementById('papers').insertBefore(banner, document.getElementById('papers').firstChild);
    status.textContent = '';
  }

  else if (ev.type === 'error') {
    status.textContent = 'Error: ' + esc(ev.message);
    document.getElementById('search-btn').disabled = false;
    if (es) { es.close(); es = null; }
  }
}

// ── search control ───────────────────────────────────────────────────────────

function startSearch() {
  if (es) { es.close(); es = null; }

  // Reset state
  Object.keys(paperData).forEach(k => delete paperData[k]);
  discovered = reviewed = matched = queriesCount = 0;

  document.getElementById('papers').innerHTML = '';
  document.getElementById('query-badges').innerHTML = '';
  document.getElementById('query-panel-label').textContent = 'Planning queries…';
  document.getElementById('status-text').textContent = 'Starting…';
  document.getElementById('stats-bar').style.display = 'none';
  document.getElementById('query-panel').style.display = 'none';
  document.getElementById('search-btn').disabled = true;

  const query    = document.getElementById('query').value.trim();
  const maxR     = document.getElementById('max-results').value;
  const strict   = document.getElementById('strictness').value;
  if (!query) { document.getElementById('search-btn').disabled = false; return; }

  es = new EventSource(`/stream?query=${encodeURIComponent(query)}&max_results=${maxR}&strictness=${strict}`);
  es.onmessage = e => handle(JSON.parse(e.data));
  es.onerror   = () => {
    document.getElementById('status-text').textContent = 'Connection error.';
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
    strictness: float = 0.2,
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

    async def on_discovery_done(*, query, arxiv_ids, **_):
        await push({"type": "discovery", "query": query, "arxiv_ids": list(arxiv_ids)})

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
        papers_meta = [
            {"arxiv_id": r.arxiv_id,
             "title": r.title,
             "authors": list(r.authors) if r.authors else []}
            for r in results
        ]
        await push({"type": "search_done", "matched": matched,
                    "total": len(results), "latency_s": latency_s,
                    "papers": papers_meta})
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
