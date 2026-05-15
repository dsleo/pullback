/* mathgent demo — frontend logic */
'use strict';

let es = null;
let advancedMode = false;
let activeQueryFilter = null;
let sortMode = 'score';  // 'score' | 'year'
let _debounceTimer = null;
let _lastIssuedQuery = '';

const paperData  = {};
const queryToIds = {};   // query string → Set of arxiv_ids
const idAliases  = {};   // bare (version-stripped) id → canonical id in paperData
let discovered = 0, reviewed = 0, matched = 0, queriesCount = 0;

// If metadata is delayed (e.g. arXiv 429), worker events can arrive before cards exist.
// Buffer them by arXiv id and apply once the card gets created.
const pendingById = {};  // arxiv_id -> { worker_start?, plan_complete?, execute_complete? }

// Strategy labels in planner prompt order (index 0 = original, 1-N = LLM variants)
const STRATEGY_LABELS = ['original', 'noun-phrase', 'synonym', 'abstraction', 'entity', 'keyword', 'subject'];

function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function _pending(id) {
  if (!pendingById[id]) pendingById[id] = {};
  return pendingById[id];
}

function applyPendingForId(id) {
  const p = paperData[id];
  const pend = pendingById[id];
  if (!p || !pend) return;

  if (pend.worker_start) {
    p.state = 'working';
    p.substatus = 'fetching…';
  }

  if (pend.plan_complete) {
    const ev = pend.plan_complete;
    p.substatus = ev.reason === 'no_headers'
      ? 'no theorem headers found'
      : `${ev.header_count} header${ev.header_count !== 1 ? 's' : ''} found`;
  }

  if (pend.execute_complete) {
    const ev = pend.execute_complete;
    reviewed++;
    p.score    = ev.score;
    p.snippet  = ev.snippet;
    p.header   = ev.header;
    p.label    = ev.label || null;
    p.substatus = null;
    p.state    = ev.matched ? 'matched' : 'no-match';
    if (ev.matched) matched++;
  }

  delete pendingById[id];
}

function sortKey(p) {
  if (p.state === 'matched') {
    // Year sort: newest first (unknown year sorts last among matched)
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
    // In normal mode, show only matched papers (hide grey/in-progress/no-match cards).
    .filter(p => advancedMode || p.state === 'matched')
    .sort((a, b) => sortKey(a) - sortKey(b));

  // Remove cards no longer in filtered view
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
    // Always appendChild to enforce sorted order — moves existing cards too.
    container.appendChild(card);

    card.className = 'paper-card ' + (p.state || 'pending');

    // Score: always shown in advanced mode; otherwise only for matched papers
    const showScore = p.score != null && advancedMode;
    const scoreHtml = showScore
      ? `<div class="card-score${p.state !== 'matched' ? ' low' : ''}">${p.score.toFixed(3)}</div>`
      : '';

    const titleHtml = p.title
      ? `<div class="card-title">${esc(p.title)}</div>`
      : `<div class="card-title placeholder">Loading…</div>`;

    const authorsHtml = (p.authors && p.authors.length)
      ? `<div class="card-authors">${esc(p.authors.join(', '))}</div>`
      : '';

    const metaParts = [];
    if (p.year) metaParts.push(esc(String(p.year)));
    if (p.citedBy != null) metaParts.push(`${p.citedBy.toLocaleString()} citations`);
    const metaHtml = metaParts.length
      ? `<div class="card-meta">${metaParts.join(' · ')}</div>` : '';

    const labelHtml = p.label
      ? `<span class="theorem-label">${esc(p.label)}</span>` : '';

    const showStatus = advancedMode && p.state && p.state !== 'matched';
    const statusTxt = showStatus ? p.state : '';
    const subText = p.substatus
      ? (showStatus ? `${statusTxt} · ${p.substatus}` : p.substatus)
      : (showStatus ? statusTxt : '');
    const subHtml = subText ? `<div class="card-substatus">${esc(subText)}</div>` : '';

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
    // pipeline strip already shown by startSearch(); just update stage
    updateStage('Searching math databases (OpenAlex, arXiv, zbMATH)…');
  }

  else if (ev.type === 'queries_planned') {
    const mergedQueries = [];
    const seen = new Set();
    [ ...(window._queryList || []), ...(ev.queries || []) ].forEach(q => {
      const key = String(q || '').trim().toLowerCase().replace(/\s+/g, ' ');
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
  }

  else if (ev.type === 'discovery') {
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
        queryToIds[ev.query].add(id);
        if (!paperData[id]) {
          const entry = { id, title: null, authors: null, year: null, citedBy: null,
                          score: null, state: 'pending', snippet: null, header: null,
                          label: null, substatus: null, discoveredBy: [ev.query] };
          // Prefer real metadata when available; otherwise still create the card.
          entry.title = (meta && meta.title) ? meta.title : `arXiv:${id}`;
          entry.authors = (meta && meta.authors) ? meta.authors : [];
          if (meta && meta.year != null) entry.year = meta.year;
          if (meta && meta.cited_by_count != null) entry.citedBy = meta.cited_by_count;
          paperData[id] = entry;
          // Register bare-ID alias for metadata_update lookup (metadata fetcher
          // normalizes IDs by stripping version suffix).
          const bare = id.replace(/v\d+$/, '');
          if (bare !== id) idAliases[bare] = id;
          discovered++;
        } else if (!paperData[id].discoveredBy.includes(ev.query)) {
          paperData[id].discoveredBy.push(ev.query);
        }
        applyPendingForId(id);
      });
      renderStats(); renderPapers();
    }
  }

  else if (ev.type === 'metadata_update') {
    const q = ev.query || null;
    ev.papers.forEach(pm => {
      // Metadata fetcher returns bare IDs (version stripped). Look up via idAliases
      // first, then try direct key match as fallback.
      const key = pm.arxiv_id;
      const bare = key.replace(/v\d+$/, '');
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
        target.title   = pm.title   || null;
        target.authors = pm.authors || null;
        if (pm.year != null)           target.year    = pm.year;
        if (pm.cited_by_count != null) target.citedBy = pm.cited_by_count;
      }
      applyPendingForId(canonId);
    });
    renderStats();
    renderPapers();
  }

  else if (ev.type === 'worker_start') {
    const p = paperData[ev.arxiv_id];
    if (!p) { _pending(ev.arxiv_id).worker_start = ev; return; }
    p.state = 'working';
    p.substatus = 'fetching…';
    renderPapers();
  }

  else if (ev.type === 'plan_complete') {
    const p = paperData[ev.arxiv_id];
    if (!p) { _pending(ev.arxiv_id).plan_complete = ev; return; }
    p.substatus = ev.reason === 'no_headers'
      ? 'no theorem headers found'
      : `${ev.header_count} header${ev.header_count !== 1 ? 's' : ''} found`;
    renderPapers();
  }

  else if (ev.type === 'execute_complete') {
    const p = paperData[ev.arxiv_id];
    if (!p) { _pending(ev.arxiv_id).execute_complete = ev; return; }
    reviewed++;
    p.score    = ev.score;
    p.snippet  = ev.snippet;
    p.header   = ev.header;
    p.label    = ev.label || null;
    p.substatus = null;
    p.state    = ev.matched ? 'matched' : 'no-match';
    if (ev.matched) matched++;
    renderStats(); renderPapers();
    updateStage(`Extracting theorems — ${reviewed} papers scanned, ${matched} match${matched !== 1 ? 'es' : ''}`);
  }

  else if (ev.type === 'search_done') {
    es.close(); es = null;
    document.getElementById('search-btn').disabled = false;
    updateStage(`Found ${ev.matched} theorem match${ev.matched !== 1 ? 'es' : ''} across ${ev.total} papers · ${ev.latency_s.toFixed(1)}s`);
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

  // Show pipeline strip immediately — don't wait for SSE
  window._queryList = [query];
  document.getElementById('pipeline').style.display = '';
  updateStage('Searching math databases (OpenAlex, arXiv, zbMATH)…');
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
