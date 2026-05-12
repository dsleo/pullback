/* mathgent demo — frontend logic */
'use strict';

let es = null;
let advancedMode = false;
let activeQueryFilter = null;

const paperData  = {};
const queryToIds = {};   // query string → Set of arxiv_ids
const idAliases  = {};   // bare (version-stripped) id → canonical id in paperData
let discovered = 0, reviewed = 0, matched = 0, queriesCount = 0;

// Strategy labels in planner prompt order (index 0 = original, 1-N = LLM variants)
const STRATEGY_LABELS = ['original', 'noun-phrase', 'synonym', 'abstraction', 'entity', 'keyword', 'subject'];

function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function extractSearchText(latex) {
  if (!latex) return '';
  let t = latex
    .replace(/\\(?:begin|end)\{[^}]+\}(?:\[[^\]]*\])?/g, ' ')
    .replace(/\\[a-zA-Z]+\{([^{}]*)\}/g, ' $1 ')
    .replace(/\\[a-zA-Z@]+\*?/g, ' ')
    .replace(/\$+|\\\[|\\\]|\\\(|\\\)/g, ' ')
    .replace(/[{}()\[\]_^~&%]/g, ' ');
  const words = t.split(/\s+/).filter(w => w.length > 3 && /^[a-zA-Z]+$/.test(w));
  return words.slice(0, 6).join(' ');
}

function sortKey(p) {
  if (p.state === 'matched')  return 0 - (p.score || 0);
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

    const searchText = hasBody ? extractSearchText(p.snippet) : '';
    const imgSrc = searchText
      ? `/pdf-snippet/${encodeURIComponent(p.id)}?q=${encodeURIComponent(searchText)}`
      : '';
    const bodyHtml = hasBody
      ? `<div class="card-body" ${isOpen ? '' : 'style="display:none"'}>
           <div class="snippet-box">` +
             (p.header ? `<div class="header-label">${esc(p.header)}</div>` : '') +
             (imgSrc
               ? `<img class="rendered-snippet" src="${imgSrc}" alt="theorem snippet"` +
                 ` onerror="this.style.display='none';this.nextElementSibling.style.display='block'">` +
                 `<pre class="snippet-fallback" style="display:none">${esc(p.snippet)}</pre>`
               : `<pre class="snippet-fallback">${esc(p.snippet)}</pre>`) +
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
    updateStage('Searching math databases (OpenAlex, arXiv, zbMATH)…');
  }

  else if (ev.type === 'queries_planned') {
    window._queryList = ev.queries;
    const container = document.getElementById('query-badges');
    // Replace placeholder original badge with full list
    container.innerHTML = '';
    queriesCount = ev.queries.length;
    ev.queries.forEach((q, i) => addQueryBadge(q, i));
    const variantCount = ev.queries.length - 1;
    if (variantCount > 0) {
      updateStage(`Expanding search with ${variantCount} rephrased variant${variantCount !== 1 ? 's' : ''} in parallel…`);
    }
    renderStats();
  }

  else if (ev.type === 'discovery') {
    if (ev.arxiv_ids && ev.arxiv_ids.length) {
      document.getElementById('results-section').style.display = '';
      if (!queryToIds[ev.query]) queryToIds[ev.query] = new Set();
      ev.arxiv_ids.forEach(id => {
        queryToIds[ev.query].add(id);
        if (!paperData[id]) {
          const entry = { id, title: null, authors: null, score: null,
                          state: 'pending', snippet: null, header: null,
                          substatus: null, discoveredBy: [ev.query] };
          paperData[id] = entry;
          // Register bare-ID alias for metadata_update lookup (metadata fetcher
          // normalizes IDs by stripping version suffix).
          const bare = id.replace(/v\d+$/, '');
          if (bare !== id) idAliases[bare] = id;
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
      // Metadata fetcher returns bare IDs (version stripped). Look up via idAliases
      // first, then try direct key match as fallback.
      const key = pm.arxiv_id;
      const bare = key.replace(/v\d+$/, '');
      const canonId = idAliases[bare] || idAliases[key] || key;
      const target = paperData[canonId];
      if (target) {
        target.title   = pm.title   || null;
        target.authors = pm.authors || null;
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
    updateStage(`Extracting theorems — ${reviewed} papers scanned, ${matched} match${matched !== 1 ? 'es' : ''}`);
  }

  else if (ev.type === 'search_done') {
    es.close(); es = null;
    document.getElementById('search-btn').disabled = false;
    document.getElementById('adv-btn').style.display = '';
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
  window._queryList = [];
  discovered = reviewed = matched = queriesCount = 0;
  document.getElementById('papers').innerHTML = '';
  document.getElementById('query-badges').innerHTML = '';
  document.getElementById('results-section').style.display = 'none';
  document.getElementById('adv-btn').style.display = 'none';
  document.getElementById('search-btn').disabled = true;

  const query = document.getElementById('query').value.trim();
  if (!query) { document.getElementById('search-btn').disabled = false; return; }

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

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('query').addEventListener('keydown', e => {
    if (e.key === 'Enter') startSearch();
  });
});
