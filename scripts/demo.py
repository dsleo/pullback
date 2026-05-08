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

# Allow running from repo root without installing
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mathgent.api.deps import build_orchestrator  # noqa: E402

app = FastAPI(title="mathgent demo")

# ---------------------------------------------------------------------------
# HTML — single-page UI, no external deps
# ---------------------------------------------------------------------------

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mathgent demo</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #f5f5f7; color: #1d1d1f; min-height: 100vh; }
  header { background: #1d1d1f; color: #fff; padding: 16px 32px;
           display: flex; align-items: center; gap: 16px; }
  header h1 { font-size: 1.2rem; font-weight: 600; letter-spacing: -.3px; }
  header span { font-size: .85rem; opacity: .55; }
  .search-bar { background: #fff; border-bottom: 1px solid #e0e0e0;
                padding: 20px 32px; display: flex; gap: 10px; align-items: center; }
  .search-bar input[type=text] { flex: 1; padding: 10px 14px; border: 1px solid #d0d0d0;
    border-radius: 8px; font-size: 1rem; outline: none; }
  .search-bar input[type=text]:focus { border-color: #0066cc; box-shadow: 0 0 0 3px rgba(0,102,204,.15); }
  .search-bar input[type=number] { width: 80px; padding: 10px 10px; border: 1px solid #d0d0d0;
    border-radius: 8px; font-size: 1rem; outline: none; }
  .search-bar label { font-size: .85rem; color: #555; white-space: nowrap; }
  button#search-btn { padding: 10px 22px; background: #0066cc; color: #fff; border: none;
    border-radius: 8px; font-size: 1rem; font-weight: 500; cursor: pointer; transition: background .15s; }
  button#search-btn:hover { background: #0055aa; }
  button#search-btn:disabled { background: #aaa; cursor: default; }
  .main { max-width: 900px; margin: 32px auto; padding: 0 24px; }
  #status-bar { font-size: .9rem; color: #555; margin-bottom: 16px; min-height: 22px; }
  #queries-bar { font-size: .82rem; color: #888; margin-bottom: 20px; min-height: 18px; }
  #papers { display: flex; flex-direction: column; gap: 10px; }
  .paper-card { background: #fff; border: 1px solid #e0e0e0; border-radius: 10px;
    padding: 14px 18px; transition: border-color .2s; }
  .paper-card.pending  { border-left: 4px solid #aaa; }
  .paper-card.working  { border-left: 4px solid #f0a500; }
  .paper-card.matched  { border-left: 4px solid #27ae60; }
  .paper-card.no-match { border-left: 4px solid #ccc; opacity: .7; }
  .card-header { display: flex; align-items: center; gap: 10px; }
  .icon { font-size: 1.1rem; width: 22px; text-align: center; }
  .arxiv-id { font-weight: 600; font-size: .95rem; color: #333; }
  .card-meta { font-size: .82rem; color: #888; margin-left: auto; }
  .arxiv-link { margin-left: 8px; font-size: .8rem; color: #0066cc; text-decoration: none; }
  .arxiv-link:hover { text-decoration: underline; }
  .card-status { font-size: .82rem; color: #777; margin-top: 4px; margin-left: 32px; }
  .snippet-box { margin-top: 10px; margin-left: 32px; background: #f8f8f8; border: 1px solid #e8e8e8;
    border-radius: 6px; padding: 10px 14px; font-family: monospace; font-size: .82rem;
    color: #333; white-space: pre-wrap; line-height: 1.5; }
  .header-label { font-size: .78rem; font-weight: 600; color: #27ae60; margin-bottom: 4px; }
  .summary-banner { background: #e8f5e9; border: 1px solid #a5d6a7; border-radius: 10px;
    padding: 14px 20px; font-size: .95rem; color: #2e7d32; margin-bottom: 16px; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .spinner { display: inline-block; animation: spin 1s linear infinite; }
</style>
</head>
<body>

<header>
  <h1>mathgent</h1>
  <span>live theorem search demo</span>
</header>

<div class="search-bar">
  <input type="text" id="query" placeholder="e.g. Banach fixed point theorem" value="Banach fixed point theorem" />
  <label>results&nbsp;<input type="number" id="max-results" value="5" min="1" max="20" /></label>
  <label>strictness&nbsp;<input type="number" id="strictness" value="0.2" min="0" max="1" step="0.05" style="width:70px"/></label>
  <button id="search-btn" onclick="startSearch()">Search</button>
</div>

<div class="main">
  <div id="status-bar"></div>
  <div id="queries-bar"></div>
  <div id="papers"></div>
</div>

<script>
let es = null;
const cards = {};
const queriesSeen = [];

function esc(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function startSearch() {
  if (es) { es.close(); es = null; }
  Object.keys(cards).forEach(k => delete cards[k]);
  queriesSeen.length = 0;
  document.getElementById('papers').innerHTML = '';
  document.getElementById('queries-bar').textContent = '';
  document.getElementById('status-bar').textContent = 'Starting search…';
  const btn = document.getElementById('search-btn');
  btn.disabled = true;

  const query = document.getElementById('query').value.trim();
  const maxR   = document.getElementById('max-results').value;
  const strict = document.getElementById('strictness').value;
  if (!query) { btn.disabled = false; return; }

  const url = `/stream?query=${encodeURIComponent(query)}&max_results=${maxR}&strictness=${strict}`;
  es = new EventSource(url);

  es.onmessage = function(e) {
    const ev = JSON.parse(e.data);
    handle(ev);
  };
  es.onerror = function() {
    document.getElementById('status-bar').textContent = 'Connection error.';
    btn.disabled = false;
    if (es) { es.close(); es = null; }
  };
}

function handle(ev) {
  const papers = document.getElementById('papers');
  const status = document.getElementById('status-bar');
  const qbar   = document.getElementById('queries-bar');
  const btn    = document.getElementById('search-btn');

  if (ev.type === 'query_start') {
    status.textContent = `Searching: "${esc(ev.query)}"`;
    queriesSeen.push(ev.query);
    qbar.textContent = 'Queries: ' + queriesSeen.map(q => `"${q}"`).join(' · ');
  }
  else if (ev.type === 'discovery') {
    if (ev.arxiv_ids && ev.arxiv_ids.length > 0) {
      status.textContent = `Discovered ${ev.arxiv_ids.length} papers from "${esc(ev.query)}"`;
      if (!queriesSeen.includes(ev.query)) {
        queriesSeen.push(ev.query);
        qbar.textContent = 'Queries: ' + queriesSeen.map(q => `"${q}"`).join(' · ');
      }
      ev.arxiv_ids.forEach(id => {
        if (!cards[id]) {
          const div = document.createElement('div');
          div.id = 'card-' + id;
          div.className = 'paper-card pending';
          div.innerHTML = `
            <div class="card-header">
              <span class="icon">·</span>
              <span class="arxiv-id">${esc(id)}</span>
              <a class="arxiv-link" href="https://arxiv.org/abs/${esc(id)}" target="_blank">↗ arXiv</a>
              <span class="card-meta"></span>
            </div>
            <div class="card-status">queued</div>`;
          papers.appendChild(div);
          cards[id] = div;
        }
      });
    }
  }
  else if (ev.type === 'worker_start') {
    const id = ev.arxiv_id;
    if (cards[id]) {
      cards[id].className = 'paper-card working';
      cards[id].querySelector('.icon').innerHTML = '<span class="spinner">⟳</span>';
      cards[id].querySelector('.card-status').textContent = 'fetching headers…';
    }
  }
  else if (ev.type === 'plan_complete') {
    const id = ev.arxiv_id;
    if (cards[id]) {
      if (ev.reason === 'no_headers') {
        cards[id].querySelector('.card-status').textContent = 'no theorem-like headers found';
      } else {
        cards[id].querySelector('.card-status').textContent =
          `scoring ${ev.header_count} header${ev.header_count !== 1 ? 's' : ''}…`;
      }
    }
  }
  else if (ev.type === 'execute_complete') {
    const id = ev.arxiv_id;
    if (cards[id]) {
      if (ev.matched) {
        cards[id].className = 'paper-card matched';
        cards[id].querySelector('.icon').textContent = '✓';
        cards[id].querySelector('.card-meta').textContent =
          `score: ${ev.score.toFixed(3)}`;
        cards[id].querySelector('.card-status').textContent = '';
        const snip = document.createElement('div');
        snip.className = 'snippet-box';
        snip.innerHTML =
          (ev.header ? `<div class="header-label">${esc(ev.header)}</div>` : '') +
          esc(ev.snippet || '');
        cards[id].appendChild(snip);
      } else {
        cards[id].className = 'paper-card no-match';
        cards[id].querySelector('.icon').textContent = '✗';
        cards[id].querySelector('.card-status').textContent = 'no match above threshold';
      }
    }
  }
  else if (ev.type === 'search_done') {
    es.close(); es = null;
    btn.disabled = false;
    const total = ev.total || Object.keys(cards).length;
    const banner = document.createElement('div');
    banner.className = 'summary-banner';
    banner.textContent =
      `Done — ${ev.matched} match${ev.matched !== 1 ? 'es' : ''} from ${total} papers · ${ev.latency_s.toFixed(1)}s`;
    papers.insertBefore(banner, papers.firstChild);
    status.textContent = '';
  }
  else if (ev.type === 'error') {
    status.textContent = 'Error: ' + esc(ev.message || 'unknown');
    btn.disabled = false;
    if (es) { es.close(); es = null; }
  }
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
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _search_stream(query: str, max_results: int, strictness: float):
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def push(payload: dict) -> None:
        await queue.put(payload)

    # --- Build a fresh orchestrator so hooks don't bleed between requests ---
    orch = build_orchestrator()
    forager = orch.forager

    # --- Librarian hooks ---
    async def on_search_start(*, query, max_results, **_):
        await push({"type": "query_start", "query": query, "max_results": max_results})

    async def on_discovery_done(*, query, arxiv_ids, **_):
        await push({"type": "discovery", "query": query, "arxiv_ids": list(arxiv_ids)})

    async def on_worker_start(*, state, **_):
        await push({"type": "worker_start", "arxiv_id": state.arxiv_id})

    async def on_worker_done(*, state, result, **_):
        match = result.match
        if match is not None and match.score >= strictness:
            await push({
                "type": "execute_complete",
                "arxiv_id": state.arxiv_id,
                "matched": True,
                "score": match.score,
                "snippet": match.snippet,
                "header": match.header_line,
            })
        else:
            await push({
                "type": "execute_complete",
                "arxiv_id": state.arxiv_id,
                "matched": False,
                "score": match.score if match else 0.0,
                "snippet": None,
                "header": None,
            })

    async def on_search_done(*, results, matched, latency_s, **_):
        await push({"type": "search_done", "matched": matched,
                    "total": len(results), "latency_s": latency_s})
        await queue.put(None)  # sentinel — end of stream

    orch.on("search_start", on_search_start)
    orch.on("discovery_done", on_discovery_done)
    orch.on("worker_start", on_worker_start)
    orch.on("worker_done", on_worker_done)
    orch.on("search_done", on_search_done)

    # --- Forager hooks ---
    async def on_plan_complete(*, plan, reason, **_):
        if plan is None:
            return  # arxiv_id unavailable; worker_done handles the final state
        await push({"type": "plan_complete", "arxiv_id": plan.arxiv_id,
                    "header_count": len(plan.headers), "reason": reason})

    forager.on("plan_complete", on_plan_complete)

    # --- Run search in background ---
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

    # --- Yield SSE events ---
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
