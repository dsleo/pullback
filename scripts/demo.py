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

from mathgent.settings import load_settings as _load_settings
app = FastAPI(title="mathgent demo")
_demo_settings = _load_settings()
_MAX_RESULTS = _demo_settings.librarian.max_results

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
  /* ── Dark Academic tokens ────────────────────────────────────────── */
  :root {
    --c-bg:          #141920;
    --c-bg-alt:      #1c2333;
    --c-bg-inset:    #212940;
    --c-surface:     #1c2333;
    --c-gold:        #d4a843;
    --c-gold-mid:    #b8912e;
    --c-gold-light:  rgba(212,168,67,.13);
    --c-fg:          #e0d8cc;
    --c-fg-2:        #9ba3af;
    --c-fg-3:        #5c6472;
    --c-border:      #273045;
    --c-border-mid:  #38445e;
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
    background: var(--c-bg);
    border-bottom: 1px solid var(--c-border);
    padding: 18px 48px;
    display: flex; align-items: baseline; gap: 18px;
  }
  header h1 {
    font-family: var(--f-sans); font-weight: 300; font-size: 1.35rem;
    letter-spacing: -.01em; color: var(--c-fg);
  }
  header span {
    font-family: var(--f-mono); font-size: .62rem; letter-spacing: .16em;
    text-transform: uppercase; color: var(--c-fg-3);
  }

  /* ── search bar ──────────────────────────────────────────────────────── */
  .search-bar {
    background: var(--c-bg-alt);
    border-bottom: 1px solid var(--c-border);
    padding: 16px 48px; display: flex; gap: 14px; align-items: center;
    box-shadow: 0 1px 3px rgba(0,0,0,.3);
  }
  .search-bar input[type=text] {
    flex: 1; border: 1px solid var(--c-border-mid); border-radius: 4px;
    background: var(--c-bg); padding: 8px 12px; font-family: var(--f-sans);
    font-size: .92rem; font-weight: 300; color: var(--c-fg); outline: none;
    transition: border-color .15s;
  }
  .search-bar input[type=text]::placeholder { color: var(--c-fg-3); }
  .search-bar input[type=text]:focus {
    border-color: var(--c-gold);
    box-shadow: 0 0 0 3px rgba(212,168,67,.15);
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
    background: var(--c-gold); color: #141920;
    border: none; border-radius: 4px; padding: 9px 22px; cursor: pointer;
    transition: background .15s;
  }
  #search-btn:hover { background: var(--c-gold-mid); }
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
    background: var(--c-bg-alt); overflow: hidden;
    box-shadow: 0 1px 3px rgba(0,0,0,.25);
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
    line-height: 1; color: var(--c-gold);
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
    border: 1px solid var(--c-gold); border-radius: 3px;
    padding: 5px 11px; background: var(--c-gold-light);
  }
  .query-badge.variant {
    border-color: var(--c-border-mid); background: var(--c-bg-alt);
  }
  .qlabel {
    font-family: var(--f-mono); font-size: .56rem; letter-spacing: .14em;
    text-transform: uppercase; color: var(--c-gold);
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
    border-left: 3px solid var(--c-gold);
    box-shadow: 0 1px 4px rgba(212,168,67,.12);
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
    color: var(--c-gold);
  }
  .card-score.low { color: var(--c-fg-3); font-weight: 400; }
  .arxiv-link {
    font-family: var(--f-mono); font-size: .6rem; letter-spacing: .05em;
    color: var(--c-fg-2); text-decoration: none;
  }
  .arxiv-link:hover { text-decoration: underline; }
  .card-icon {
    font-family: var(--f-mono); font-size: .72rem; color: var(--c-fg-3);
    padding-top: 3px; justify-self: center;
  }
  .paper-card.matched .card-icon { color: var(--c-gold); }

  .card-body { padding: 0 16px 14px 50px; }
  .snippet-box {
    background: var(--c-bg); border: 1px solid var(--c-border);
    border-radius: 4px;
    padding: 12px 14px; font-family: var(--f-mono); font-size: .74rem;
    font-weight: 300; color: var(--c-fg); white-space: pre-wrap;
    line-height: 1.65;
  }
  .header-label {
    font-family: var(--f-mono); font-size: .6rem; letter-spacing: .1em;
    text-transform: uppercase; color: var(--c-gold-mid); margin-bottom: 6px;
  }


  #adv-btn {
    background: none; border: none; cursor: pointer;
    font-size: .85rem; color: var(--c-fg-3); padding: 0 0 0 6px;
    vertical-align: middle; line-height: 1; transition: color .15s;
  }
  #adv-btn:hover { color: var(--c-fg-2); }
  #adv-btn.active { color: var(--c-gold); }

  /* pipeline strip */
  .pipeline-strip { display: flex; flex-direction: column; gap: 10px; }
  .pipeline-row   { display: flex; align-items: baseline; gap: 16px; }
  .stage-text {
    font-family: var(--f-mono); font-size: .68rem; letter-spacing: .06em;
    color: var(--c-fg-3);
  }
  .pipeline-counts {
    font-family: var(--f-mono); font-size: .62rem; color: var(--c-fg-3);
  }
  .pipeline-counts span { margin-right: 10px; }
  .pipeline-counts .cnt-match { color: var(--c-gold); }

  .query-badge { cursor: pointer; transition: opacity .12s; }
  .query-badge.active-filter {
    border-color: var(--c-gold) !important;
    background: var(--c-gold-light) !important;
    box-shadow: 0 0 0 2px rgba(212,168,67,.25);
  }
  .query-badge:hover { opacity: .85; }

  .card-attr {
    display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px;
  }
  .attr-chip {
    font-family: var(--f-mono); font-size: .54rem; letter-spacing: .08em;
    text-transform: uppercase; color: var(--c-fg-3);
    border: 1px solid var(--c-border); border-radius: 2px;
    padding: 2px 6px;
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
  <button id="search-btn" onclick="startSearch()">Search</button>
</div>

<div class="main">
  <!-- Pipeline strip: shown immediately on search click -->
  <div class="pipeline-strip" id="pipeline" style="display:none">
    <div class="pipeline-row">
      <span class="stage-text" id="stage-text">Reformulating query…</span>
      <span class="pipeline-counts" id="pipeline-counts"></span>
    </div>
    <div class="query-list" id="query-badges"></div>
  </div>

  <!-- Results -->
  <div id="results-section" style="display:none">
    <div class="section-label">
      Results
      <button id="adv-btn" onclick="toggleAdvanced()" title="Advanced mode: show scores and query attribution">ⓘ</button>
    </div>
    <div id="papers" style="margin-top:12px"></div>
  </div>
</div>

<script>
let es = null;
let advancedMode = false;
let activeQueryFilter = null;

const paperData  = {};
const queryToIds = {};   // query string → Set of arxiv_ids
let discovered = 0, reviewed = 0, matched = 0, queriesCount = 0;

// Strategy labels in planner prompt order (index 0 = original, 1-N = LLM variants)
const STRATEGY_LABELS = ['original', 'noun-phrase', 'synonym', 'abstraction', 'entity', 'keyword', 'subject'];

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
  const el = document.getElementById('pipeline-counts');
  if (!el) return;
  const parts = [];
  if (discovered > 0) parts.push(`<span>${discovered} discovered</span>`);
  if (reviewed   > 0) parts.push(`<span>${reviewed} reviewed</span>`);
  if (matched    > 0) parts.push(`<span class="cnt-match">${matched} matched</span>`);
  el.innerHTML = parts.join('');
}

function updateStage(text) {
  const el = document.getElementById('stage-text');
  if (el) el.textContent = text;
}

function renderPapers() {
  const container = document.getElementById('papers');
  const filterSet = activeQueryFilter ? (queryToIds[activeQueryFilter] || new Set()) : null;

  const sorted = Object.values(paperData)
    .filter(p => !filterSet || filterSet.has(p.id))
    .sort((a, b) => sortKey(a) - sortKey(b));

  // Remove cards no longer in filtered view
  Array.from(container.children).forEach(el => {
    const id = el.id.replace('card-', '');
    if (!sorted.find(p => p.id === id)) el.remove();
  });

  sorted.forEach(p => {
    let card = document.getElementById('card-' + p.id);
    container.appendChild(card || (() => {
      const d = document.createElement('div');
      d.id = 'card-' + p.id;
      return d;
    })());
    card = document.getElementById('card-' + p.id);

    card.className = 'paper-card ' + (p.state || 'pending');

    // Score: always shown in advanced mode; otherwise only for matched papers
    const showScore = p.score != null && (p.state === 'matched' || advancedMode);
    const scoreHtml = showScore
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

    // Attribution chips (advanced mode only)
    const attrHtml = (advancedMode && p.discoveredBy && p.discoveredBy.length)
      ? `<div class="card-attr">${p.discoveredBy.map(q => {
          const idx = (window._queryList || []).indexOf(q);
          const lbl = idx >= 0 ? (STRATEGY_LABELS[idx] || 'variant ' + idx) : 'unknown';
          return `<span class="attr-chip" title="${esc(q)}">${esc(lbl)}</span>`;
        }).join('')}</div>`
      : '';

    const isOpen  = card.dataset.open === '1';
    const hasBody = p.state === 'matched' && p.snippet;
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
          ${attrHtml}
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

function toggleAdvanced() {
  advancedMode = !advancedMode;
  document.getElementById('adv-btn').classList.toggle('active', advancedMode);
  // Rebuild badges to show/hide strategy labels
  const ql = window._queryList || [];
  const container = document.getElementById('query-badges');
  container.innerHTML = '';
  ql.forEach((q, i) => addQueryBadge(q, i));
  if (activeQueryFilter) {
    document.querySelectorAll('.query-badge').forEach(b => {
      b.classList.toggle('active-filter', b.dataset.query === activeQueryFilter);
    });
  }
  renderPapers();
}

function addQueryBadge(q, i) {
  const lbl = STRATEGY_LABELS[i] || ('variant ' + i);
  const badge = document.createElement('div');
  badge.className = 'query-badge' + (i > 0 ? ' variant' : '');
  badge.dataset.query = q;
  badge.title = 'Click to filter results\n\n' + q;
  badge.onclick = () => filterByQuery(q);
  // Label only visible in advanced mode
  const lblHtml = advancedMode ? `<span class="qlabel">${esc(lbl)}</span>` : '';
  badge.innerHTML = lblHtml + `<span class="qtext">${esc(q)}</span>`;
  document.getElementById('query-badges').appendChild(badge);
  return badge;
}

function filterByQuery(q) {
  activeQueryFilter = (activeQueryFilter === q) ? null : q;
  document.querySelectorAll('.query-badge').forEach(b => {
    b.classList.toggle('active-filter', b.dataset.query === activeQueryFilter);
  });
  renderPapers();
}

function handle(ev) {
  if (ev.type === 'query_start') {
    // pipeline strip already shown by startSearch(); just update stage
    updateStage('Reformulating query…');
  }

  else if (ev.type === 'queries_planned') {
    window._queryList = ev.queries;
    const container = document.getElementById('query-badges');
    // Replace the placeholder original badge with full list
    container.innerHTML = '';
    queriesCount = ev.queries.length;
    ev.queries.forEach((q, i) => {
      addQueryBadge(q, i);
    });
    updateStage('Calling providers…');
    renderStats();
  }

  else if (ev.type === 'discovery') {
    if (ev.arxiv_ids && ev.arxiv_ids.length) {
      document.getElementById('results-section').style.display = '';
      if (!queryToIds[ev.query]) queryToIds[ev.query] = new Set();
      ev.arxiv_ids.forEach(id => {
        queryToIds[ev.query].add(id);
        if (!paperData[id]) {
          paperData[id] = { id, title: null, authors: null, score: null,
                            state: 'pending', snippet: null, header: null,
                            substatus: null, discoveredBy: [ev.query] };
          discovered++;
        } else if (!paperData[id].discoveredBy.includes(ev.query)) {
          paperData[id].discoveredBy.push(ev.query);
        }
      });
      renderStats(); renderPapers();
    }
  }

  else if (ev.type === 'metadata_update') {
    ev.papers.forEach(pm => {
      if (paperData[pm.arxiv_id]) {
        paperData[pm.arxiv_id].title   = pm.title   || null;
        paperData[pm.arxiv_id].authors = pm.authors || null;
      }
    });
    renderPapers();
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
    updateStage(`Reviewing papers — ${reviewed} done, ${matched} matched`);
  }

  else if (ev.type === 'search_done') {
    es.close(); es = null;
    document.getElementById('search-btn').disabled = false;
    updateStage(`Done — ${ev.matched} match${ev.matched !== 1 ? 'es' : ''} from ${ev.total} papers · ${ev.latency_s.toFixed(1)}s`);
  }

  else if (ev.type === 'error') {
    updateStage('Error — ' + esc(ev.message));
    document.getElementById('search-btn').disabled = false;
    if (es) { es.close(); es = null; }
  }
}

function startSearch() {
  if (es) { es.close(); es = null; }
  Object.keys(paperData).forEach(k => delete paperData[k]);
  Object.keys(queryToIds).forEach(k => delete queryToIds[k]);
  activeQueryFilter = null;
  window._queryList = [];
  discovered = reviewed = matched = queriesCount = 0;
  document.getElementById('papers').innerHTML = '';
  document.getElementById('query-badges').innerHTML = '';
  document.getElementById('results-section').style.display = 'none';
  document.getElementById('search-btn').disabled = true;

  const query = document.getElementById('query').value.trim();
  if (!query) { document.getElementById('search-btn').disabled = false; return; }

  // Show pipeline strip immediately — don't wait for SSE
  window._queryList = [query];
  document.getElementById('pipeline').style.display = '';
  updateStage('Reformulating query…');
  document.getElementById('pipeline-counts').innerHTML = '';
  document.getElementById('query-badges').innerHTML = '';
  addQueryBadge(query, 0);  // original query visible right away

  es = new EventSource(`/stream?query=${encodeURIComponent(query)}`);
  es.onmessage = e => handle(JSON.parse(e.data));
  es.onerror   = () => {
    updateStage('Connection error — please try again');
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
    strictness: float = 0.0,
) -> StreamingResponse:
    return StreamingResponse(
        _search_stream(query, _MAX_RESULTS, strictness),
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

    # Track metadata fetches — tasks are awaited before the stream sentinel
    fetched_metadata_ids: set[str] = set()
    _metadata_tasks: list[asyncio.Task] = []

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
        # Kick off metadata fetch; track task so on_search_done can await it
        if arxiv_ids:
            t = asyncio.create_task(_fetch_and_push_metadata(list(arxiv_ids)))
            _metadata_tasks.append(t)

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
        # Flush any in-flight metadata fetches before closing the stream
        if _metadata_tasks:
            await asyncio.gather(*_metadata_tasks, return_exceptions=True)
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
