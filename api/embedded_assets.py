INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mathgent</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Jost:wght@200;300;400;500;600&family=JetBrains+Mono:wght@300;400;500&family=Lora:ital,wght@1,400;1,500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/style.css">
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
  <div class="pipeline-strip" id="pipeline" style="display:none">
    <div class="pipeline-row">
      <span class="stage-text" id="stage-text"></span>
      <span class="pipeline-counts" id="pipeline-counts"></span>
      <button id="adv-btn" onclick="toggleAdvanced()" style="display:none"
              title="Advanced mode: show scores and query attribution">ⓘ</button>
    </div>
    <div class="query-list" id="query-badges"></div>
  </div>

  <div id="results-section" style="display:none">
    <div class="results-header">
      <span class="section-label">Results</span>
      <button id="sort-btn" onclick="toggleSortMode()" title="Sort by score">⇅</button>
    </div>
    <div id="papers" style="margin-top:12px"></div>
  </div>
</div>

<script src="/app.js"></script>
</body>
</html>
"""

STYLE_CSS = """/* ── Dark Academic tokens ────────────────────────────────────────── */
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
#search-btn {
  font-family: var(--f-mono); font-size: .62rem; font-weight: 500;
  letter-spacing: .12em; text-transform: uppercase;
  background: var(--c-gold); color: #141920;
  border: none; border-radius: 4px; padding: 9px 22px; cursor: pointer;
  transition: background .15s;
}
#search-btn:hover { background: var(--c-gold-mid); }
#search-btn:disabled { opacity: .4; cursor: default; }
.main { max-width: 880px; margin: 36px auto; padding: 0 48px;
        display: flex; flex-direction: column; gap: 24px; }
.results-header {
  display: flex; align-items: baseline; gap: 8px;
  padding-bottom: 8px; border-bottom: 1px solid var(--c-border);
}
.section-label {
  font-family: var(--f-mono); font-size: 1.1rem; letter-spacing: .12em;
  text-transform: uppercase; color: var(--c-fg);
}
#sort-btn {
  font-family: var(--f-mono); font-size: .6rem; letter-spacing: .08em;
  color: var(--c-fg-3); background: none; border: 1px solid var(--c-border);
  border-radius: 3px; padding: 2px 8px; cursor: pointer;
  transition: color .15s, border-color .15s;
}
#sort-btn:hover { color: var(--c-fg); border-color: var(--c-border-mid); }
#sort-btn.active { color: var(--c-gold); border-color: var(--c-gold); }
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
#adv-btn {
  background: none; border: none; cursor: pointer;
  font-size: .85rem; color: var(--c-fg-3); padding: 0 0 0 6px;
  vertical-align: middle; line-height: 1; transition: color .15s;
}
#adv-btn:hover { color: var(--c-fg-2); }
#adv-btn.active { color: var(--c-gold); }
.query-list { display: flex; flex-wrap: wrap; gap: 6px; min-height: 28px; }
.query-badge {
  display: inline-flex; align-items: baseline; gap: 7px;
  border: 1px solid var(--c-border-mid); border-radius: 3px;
  padding: 5px 11px; background: var(--c-bg-alt);
  cursor: pointer; transition: opacity .12s, border-color .12s, background .12s;
}
.query-badge.variant { opacity: 0.7; }
.query-badge.active-filter {
  border-color: var(--c-gold);
  background: var(--c-gold-light);
  box-shadow: 0 0 0 2px rgba(212,168,67,.25);
  opacity: 1;
}
.query-badge:hover { opacity: 1; }
.qlabel {
  font-family: var(--f-mono); font-size: .56rem; letter-spacing: .14em;
  text-transform: uppercase; color: var(--c-gold);
}
.query-badge.variant .qlabel { color: var(--c-fg-3); }
.qtext { font-size: .82rem; font-weight: 300; color: var(--c-fg); }
#papers { display: flex; flex-direction: column; gap: 8px; }
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
.card-meta {
  font-family: var(--f-sans); font-size: .68rem; color: var(--c-fg-3);
  margin-top: 2px;
}
.theorem-label {
  font-family: var(--f-mono); font-size: .65rem; font-weight: 600;
  background: #1e3a5f; color: #7eb8f7;
  padding: 2px 6px; border-radius: 3px; white-space: nowrap;
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
.card-body { padding: 0 16px 14px 50px; }
.snippet-box {
  background: var(--c-bg); border: 1px solid var(--c-border);
  border-radius: 4px;
  padding: 12px 14px; overflow: hidden;
}
.header-label {
  font-family: var(--f-mono); font-size: .6rem; letter-spacing: .1em;
  text-transform: uppercase; color: var(--c-gold-mid); margin-bottom: 8px;
}
.snippet-raw {
  font-family: var(--f-mono); font-size: .74rem; font-weight: 300;
  color: var(--c-fg); white-space: pre-wrap; line-height: 1.65;
  margin: 0; padding: 0;
}
.card-title.placeholder { color: var(--c-fg-2); }
.card-attr { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.attr-chip {
  font-family: var(--f-mono); font-size: .54rem; letter-spacing: .08em;
  text-transform: uppercase; color: var(--c-fg-3);
  border: 1px solid var(--c-border); border-radius: 2px;
  padding: 2px 6px;
}
@keyframes spin { to { transform: rotate(360deg); } }
.spinner { display: inline-block; animation: spin .9s linear infinite; }
"""

APP_JS = """/* mathgent demo — frontend logic */
'use strict';

let es = null;
let advancedMode = false;
let activeQueryFilter = null;
let sortMode = 'score';
let _debounceTimer = null;
let _lastIssuedQuery = '';

const paperData  = {};
const queryToIds = {};
const idAliases  = {};
let discovered = 0, reviewed = 0, matched = 0, queriesCount = 0;

const STRATEGY_LABELS = ['original', 'noun-phrase', 'synonym', 'abstraction', 'entity', 'keyword', 'subject'];

function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function sortKey(p) {
  if (p.state === 'matched') {
    if (sortMode === 'year') return p.year ? -p.year : 1;
    return 0 - (p.score || 0);
  }
  if (p.state === 'no-match') return 10 - (p.score || 0);
  if (p.state === 'working')  return 100;
  return 200;
}

function renderStats() {
  const el = document.getElementById('pipeline-counts');
  if (!el) return;
  const parts = [];
  if (discovered > 0) parts.push(`<span>${discovered} discovered</span>`);
  if (reviewed > 0) parts.push(`<span>${reviewed} reviewed</span>`);
  if (matched > 0) parts.push(`<span class="cnt-match">${matched} matched</span>`);
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

  Array.from(container.children).forEach(el => {
    const id = el.id.replace('card-', '');
    if (!sorted.find(p => p.id === id)) el.remove();
  });

  sorted.forEach(p => {
    let card = document.getElementById('card-' + p.id);
    if (!card) {
      card = document.createElement('div');
      card.id = 'card-' + p.id;
    }
    container.appendChild(card);
    card.className = 'paper-card ' + (p.state || 'pending');

    const showScore = p.score != null && advancedMode;
    const scoreHtml = showScore
      ? `<div class="card-score${p.state !== 'matched' ? ' low' : ''}">${p.score.toFixed(3)}</div>`
      : '';
    const titleHtml = p.title
      ? `<div class="card-title">${esc(p.title)}</div>`
      : `<div class="card-title placeholder">Loading...</div>`;
    const authorsHtml = (p.authors && p.authors.length)
      ? `<div class="card-authors">${esc(p.authors.join(', '))}</div>`
      : '';
    const metaParts = [];
    if (p.year) metaParts.push(esc(String(p.year)));
    if (p.citedBy != null) metaParts.push(`${p.citedBy.toLocaleString()} citations`);
    const metaHtml = metaParts.length ? `<div class="card-meta">${metaParts.join(' · ')}</div>` : '';
    const labelHtml = p.label ? `<span class="theorem-label">${esc(p.label)}</span>` : '';
    const subHtml = p.substatus ? `<div class="card-substatus">${esc(p.substatus)}</div>` : '';
    const attrHtml = (advancedMode && p.discoveredBy && p.discoveredBy.length)
      ? `<div class="card-attr">${p.discoveredBy.map(q => {
          const idx = (window._queryList || []).indexOf(q);
          const lbl = idx >= 0 ? (STRATEGY_LABELS[idx] || 'variant ' + idx) : 'unknown';
          return `<span class="attr-chip" title="${esc(q)}">${esc(lbl)}</span>`;
        }).join('')}</div>`
      : '';
    const isOpen = card.dataset.open === '1';
    const hasBody = p.state === 'matched' && p.snippet;
    const chevHtml = hasBody
      ? `<span class="card-chevron${isOpen ? ' open' : ''}" id="chev-${p.id}">▶</span>`
      : `<span></span>`;
    const bodyHtml = hasBody
      ? `<div class="card-body" ${isOpen ? '' : 'style="display:none"'}>
           <div class="snippet-box">` +
             (p.header ? `<div class="header-label">${esc(p.header)}</div>` : '') +
             `<pre class="snippet-raw">${esc(p.snippet)}</pre>` +
         `</div></div>`
      : '';

    card.innerHTML = `
      <button class="card-toggle" onclick="toggleCard('${p.id}')">
        ${chevHtml}
        <div class="card-main">
          ${titleHtml}
          ${authorsHtml}
          ${metaHtml}
          ${subHtml}
          ${attrHtml}
        </div>
        <div class="card-right">
          ${labelHtml}
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
  badge.title = 'Click to filter results\\n\\n' + q;
  badge.onclick = () => filterByQuery(q);
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

function toggleSortMode() {
  sortMode = sortMode === 'score' ? 'year' : 'score';
  const btn = document.getElementById('sort-btn');
  if (btn) {
    btn.title = sortMode === 'year' ? 'Sort by score' : 'Sort by newest';
    btn.classList.toggle('active', sortMode === 'year');
  }
  renderPapers();
}

function handle(ev) {
  if (ev.type === 'query_start') {
    updateStage('Searching math databases (OpenAlex, arXiv, zbMATH)…');
  } else if (ev.type === 'queries_planned') {
    const mergedQueries = [];
    const seen = new Set();
    [ ...(window._queryList || []), ...(ev.queries || []) ].forEach(q => {
      const key = String(q || '').trim().toLowerCase().replace(/\\s+/g, ' ');
      if (!key || seen.has(key)) return;
      seen.add(key);
      mergedQueries.push(q);
    });
    window._queryList = mergedQueries;
    const container = document.getElementById('query-badges');
    container.innerHTML = '';
    queriesCount = window._queryList.length;
    window._queryList.forEach((q, i) => addQueryBadge(q, i));
    const variantCount = Math.max(0, window._queryList.length - 1);
    if (variantCount > 0) {
      updateStage(`Expanding search with ${variantCount} rephrased variant${variantCount !== 1 ? 's' : ''} in parallel…`);
    }
    renderStats();
  } else if (ev.type === 'discovery') {
    if (ev.arxiv_ids && ev.arxiv_ids.length) {
      document.getElementById('results-section').style.display = '';
      document.getElementById('adv-btn').style.display = '';
      if (!queryToIds[ev.query]) queryToIds[ev.query] = new Set();
      const metaById = {};
      if (ev.papers && Array.isArray(ev.papers)) {
        ev.papers.forEach(pm => { if (pm && pm.arxiv_id) metaById[pm.arxiv_id] = pm; });
      }
      ev.arxiv_ids.forEach(id => {
        const meta = metaById[id] || null;
        if (!meta || !meta.title || !meta.authors || !meta.authors.length) {
          return;
        }
        queryToIds[ev.query].add(id);
        if (!paperData[id]) {
          const entry = { id, title: null, authors: null, year: null, citedBy: null,
                          score: null, state: 'pending', snippet: null, header: null,
                          label: null, substatus: null, discoveredBy: [ev.query] };
          entry.title = meta.title || null;
          entry.authors = meta.authors || null;
          if (meta.year != null) entry.year = meta.year;
          if (meta.cited_by_count != null) entry.citedBy = meta.cited_by_count;
          paperData[id] = entry;
          const bare = id.replace(/v\\d+$/, '');
          if (bare !== id) idAliases[bare] = id;
          discovered++;
        } else if (!paperData[id].discoveredBy.includes(ev.query)) {
          paperData[id].discoveredBy.push(ev.query);
        }
      });
      renderStats();
      renderPapers();
    }
  } else if (ev.type === 'metadata_update') {
    const q = ev.query || null;
    ev.papers.forEach(pm => {
      const key = pm.arxiv_id;
      const bare = key.replace(/v\\d+$/, '');
      const canonId = idAliases[bare] || idAliases[key] || key;
      let target = paperData[canonId];
      const hasCore = pm.title && pm.authors && pm.authors.length;
      if (!target && hasCore) {
        target = {
          id: canonId,
          title: pm.title || null,
          authors: pm.authors || null,
          year: pm.year ?? null,
          citedBy: pm.cited_by_count ?? null,
          score: null,
          state: 'pending',
          snippet: null,
          header: null,
          label: null,
          substatus: null,
          discoveredBy: q ? [q] : [],
        };
        paperData[canonId] = target;
        if (q) {
          if (!queryToIds[q]) queryToIds[q] = new Set();
          queryToIds[q].add(canonId);
        }
        discovered++;
      }
      if (target && hasCore) {
        target.title = pm.title || null;
        target.authors = pm.authors || null;
        if (pm.year != null) target.year = pm.year;
        if (pm.cited_by_count != null) target.citedBy = pm.cited_by_count;
      }
    });
    renderStats();
    renderPapers();
  } else if (ev.type === 'worker_start') {
    const p = paperData[ev.arxiv_id];
    if (p) { p.state = 'working'; p.substatus = 'fetching...'; renderPapers(); }
  } else if (ev.type === 'plan_complete') {
    const p = paperData[ev.arxiv_id];
    if (!p) return;
    p.substatus = ev.reason === 'no_headers'
      ? 'no theorem headers found'
      : `${ev.header_count} header${ev.header_count !== 1 ? 's' : ''} found`;
    renderPapers();
  } else if (ev.type === 'execute_complete') {
    const p = paperData[ev.arxiv_id];
    if (!p) return;
    reviewed++;
    p.score = ev.score;
    p.snippet = ev.snippet;
    p.header = ev.header;
    p.label = ev.label || null;
    p.substatus = null;
    p.state = ev.matched ? 'matched' : 'no-match';
    if (ev.matched) matched++;
    renderStats();
    renderPapers();
    updateStage(`Extracting theorems — ${reviewed} papers scanned, ${matched} match${matched !== 1 ? 'es' : ''}`);
  } else if (ev.type === 'search_done') {
    es.close();
    es = null;
    document.getElementById('search-btn').disabled = false;
    updateStage(`Found ${ev.matched} theorem match${ev.matched !== 1 ? 'es' : ''} across ${ev.total} papers · ${ev.latency_s.toFixed(1)}s`);
  } else if (ev.type === 'error') {
    updateStage('Error — ' + esc(ev.message));
    document.getElementById('search-btn').disabled = false;
    if (es) { es.close(); es = null; }
  }
}

function startSearch() {
  if (es) { es.close(); es = null; }
  Object.keys(paperData).forEach(k => delete paperData[k]);
  Object.keys(queryToIds).forEach(k => delete queryToIds[k]);
  Object.keys(idAliases).forEach(k => delete idAliases[k]);
  activeQueryFilter = null;
  sortMode = 'score';
  const sortBtn = document.getElementById('sort-btn');
  if (sortBtn) { sortBtn.title = 'Sort by newest'; sortBtn.classList.remove('active'); }
  window._queryList = [];
  discovered = reviewed = matched = queriesCount = 0;
  document.getElementById('papers').innerHTML = '';
  document.getElementById('query-badges').innerHTML = '';
  document.getElementById('results-section').style.display = 'none';
  document.getElementById('adv-btn').style.display = 'none';
  document.getElementById('search-btn').disabled = true;

  const query = document.getElementById('query').value.trim();
  if (!query) { document.getElementById('search-btn').disabled = false; return; }
  _lastIssuedQuery = query;
  window._queryList = [query];
  document.getElementById('pipeline').style.display = '';
  updateStage('Searching math databases (OpenAlex, arXiv, zbMATH)…');
  document.getElementById('pipeline-counts').innerHTML = '';
  document.getElementById('query-badges').innerHTML = '';
  addQueryBadge(query, 0);

  es = new EventSource(`/api/stream?query=${encodeURIComponent(query)}`);
  es.onmessage = e => handle(JSON.parse(e.data));
  es.onerror = () => {
    updateStage('Connection error — please try again');
    document.getElementById('search-btn').disabled = false;
    if (es) { es.close(); es = null; }
  };
}

function scheduleLiveSearch() {
  const query = document.getElementById('query').value.trim();
  if (!query) {
    if (_debounceTimer) { clearTimeout(_debounceTimer); _debounceTimer = null; }
    if (es) { es.close(); es = null; }
    _lastIssuedQuery = '';
    document.getElementById('search-btn').disabled = false;
    return;
  }
  if (query === _lastIssuedQuery) return;
  if (_debounceTimer) clearTimeout(_debounceTimer);
  _debounceTimer = setTimeout(() => {
    _debounceTimer = null;
    if (document.getElementById('query').value.trim() !== query) return;
    startSearch();
  }, 450);
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('query').addEventListener('keydown', e => {
    if (e.key === 'Enter') startSearch();
  });
  document.getElementById('query').addEventListener('input', () => scheduleLiveSearch());
});
"""
