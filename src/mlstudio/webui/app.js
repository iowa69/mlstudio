// MLSTudio WebUI

const $ = (id) => document.getElementById(id);
const state = {
  jobId: null,
  scheme: null,
  results: [],
  mst: null,
  metaFields: ['st'],
  maxEdge: 0,
  cy: null,
};

// ---- Helpers ---------------------------------------------------------------

function setStatus(kind, text) {
  $('status-dot').className = 'dot ' + kind;
  $('status-text').textContent = text;
}

function setProgress(pct, msg) {
  const el = $('job-progress');
  el.classList.remove('hidden');
  el.querySelector('.bar-fill').style.width = (pct * 100).toFixed(0) + '%';
  $('progress-text').textContent = msg || '';
}

async function api(path, opts = {}) {
  const r = await fetch('/api' + path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

// ---- Schemes ---------------------------------------------------------------

async function loadSchemes() {
  const data = await api('/schemes');
  const sel = $('scheme-select');
  sel.innerHTML = '';
  for (const s of data.registry) {
    const opt = document.createElement('option');
    opt.value = s.key;
    opt.textContent = `${s.organism} — ${s.scheme}` + (s.cached ? ' ✓' : '');
    sel.appendChild(opt);
  }
}

// ---- Scan ------------------------------------------------------------------

$('scan-btn').addEventListener('click', async () => {
  const folder = $('folder-input').value.trim();
  if (!folder) return;
  $('scan-result').textContent = 'Scanning…';
  try {
    const data = await api('/scan?folder=' + encodeURIComponent(folder));
    const withReads = data.samples.filter(s => s.has_reads).length;
    $('scan-result').textContent = `${data.samples.length} sample(s) found — ${withReads} with paired reads`;
    $('run-btn').disabled = data.samples.length === 0;
  } catch (e) {
    $('scan-result').textContent = 'Error: ' + e.message;
    $('run-btn').disabled = true;
  }
});

// ---- Analyze ---------------------------------------------------------------

$('run-btn').addEventListener('click', async () => {
  const req = {
    folder: $('folder-input').value.trim(),
    scheme: $('scheme-select').value,
    threads: parseInt($('threads').value) || 0,
    use_fastp: $('use-fastp').checked,
  };
  setStatus('running', 'Starting…');
  $('run-btn').disabled = true;
  $('empty-state').classList.add('hidden');
  try {
    const { job_id } = await api('/analyze', { method: 'POST', body: JSON.stringify(req) });
    state.jobId = job_id;
    subscribe(job_id);
  } catch (e) {
    setStatus('error', 'Error: ' + e.message);
    $('run-btn').disabled = false;
  }
});

function subscribe(jobId) {
  const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/api/jobs/' + jobId + '/ws');
  ws.onmessage = async (ev) => {
    const snap = JSON.parse(ev.data);
    setProgress(snap.progress, snap.message);
    if (snap.status === 'done') {
      setStatus('done', snap.message);
      $('run-btn').disabled = false;
      const result = await api('/jobs/' + jobId);
      state.results = result.results;
      state.mst = result.mst;
      renderResults();
      renderMst();
    } else if (snap.status === 'error') {
      setStatus('error', snap.error || 'Error');
      $('run-btn').disabled = false;
    } else {
      setStatus('running', snap.message);
    }
  };
  ws.onerror = () => setStatus('error', 'WebSocket error');
}

// ---- Results table ---------------------------------------------------------

function renderResults() {
  const panel = $('results-panel');
  panel.classList.remove('hidden');
  const thead = panel.querySelector('thead');
  const tbody = panel.querySelector('tbody');
  if (!state.results.length) return;

  const loci = Object.keys(state.results[0].calls);
  thead.innerHTML = '<tr><th>Sample</th><th>ST</th>' +
    loci.map(l => `<th>${l}</th>`).join('') +
    '<th>Notes</th></tr>';
  tbody.innerHTML = state.results.map(r => {
    const cells = loci.map(l => {
      const c = r.calls[l];
      const flag = c.flag !== 'EXC' ? ` <span class="muted">(${c.flag})</span>` : '';
      return `<td>${c.allele ?? '-'}${flag}</td>`;
    }).join('');
    return `<tr><td><b>${r.sample}</b></td><td>${r.st ?? '<span class="muted">none</span>'}</td>${cells}<td class="muted small">${r.notes.join('; ')}</td></tr>`;
  }).join('');
}

$('toggle-results').addEventListener('click', () => {
  const panel = $('results-panel');
  const tableWrap = panel.querySelector('.table-wrap');
  tableWrap.classList.toggle('hidden');
  $('toggle-results').textContent = tableWrap.classList.contains('hidden') ? 'expand ▴' : 'collapse ▾';
});

// ---- MST -------------------------------------------------------------------

function renderMst() {
  if (!state.mst) return;
  state.maxEdge = Math.max(0, ...state.mst.elements
    .filter(e => e.data.source)
    .map(e => e.data.weight));
  $('threshold').max = Math.max(1, state.maxEdge);
  $('threshold').value = state.maxEdge;
  $('threshold-val').textContent = state.maxEdge;

  if (state.cy) state.cy.destroy();

  state.cy = cytoscape({
    container: $('cy'),
    elements: state.mst.elements,
    layout: { name: 'cose-bilkent', randomize: false, animate: false, nodeDimensionsIncludeLabels: true, idealEdgeLength: 80 },
    wheelSensitivity: 0.2,
    style: [
      {
        selector: 'node',
        style: {
          'background-color': '#4a9eff',
          'label': 'data(label)',
          'color': '#e4e8ef',
          'font-size': '11px',
          'text-valign': 'bottom',
          'text-halign': 'center',
          'text-margin-y': 4,
          'border-width': 2,
          'border-color': '#1a1f29',
          'width': 32, 'height': 32,
          'text-outline-width': 2,
          'text-outline-color': '#0a0d12',
        }
      },
      {
        selector: 'edge',
        style: {
          'width': 'data(weight)',
          'width': (ele) => Math.max(1, Math.min(6, 7 - ele.data('weight') / Math.max(1, state.maxEdge) * 6)),
          'line-color': '#4a5468',
          'curve-style': 'straight',
          'label': 'data(label)',
          'font-size': '10px',
          'color': '#8a93a6',
          'text-rotation': 'autorotate',
          'text-background-color': '#0a0d12',
          'text-background-opacity': 0.8,
          'text-background-padding': '2px',
        }
      },
      {
        selector: 'node:selected',
        style: { 'border-color': '#fbbf24', 'border-width': 3 }
      },
      {
        selector: 'edge.hidden',
        style: { 'display': 'none' }
      },
    ],
  });

  state.cy.on('tap', 'node', (evt) => {
    const d = evt.target.data();
    console.log('Node:', d);
  });

  populateColorFields();
  applyColoring();
}

function populateColorFields() {
  const sel = $('color-field');
  const cur = sel.value;
  sel.innerHTML = '';
  for (const f of state.metaFields) {
    const opt = document.createElement('option');
    opt.value = f;
    opt.textContent = f;
    sel.appendChild(opt);
  }
  if ([...sel.options].some(o => o.value === cur)) sel.value = cur;
}

function colorFor(values) {
  // Stable hash → HSL
  const palette = {};
  let i = 0;
  for (const v of values) {
    if (v == null || v === '') continue;
    if (!(v in palette)) {
      const hue = (i * 137.5) % 360;
      palette[v] = `hsl(${hue} 65% 60%)`;
      i++;
    }
  }
  return palette;
}

function applyColoring() {
  if (!state.cy) return;
  const field = $('color-field').value;
  const values = state.cy.nodes().map(n => n.data(field));
  const palette = colorFor(values);
  state.cy.nodes().forEach(n => {
    const v = n.data(field);
    n.style('background-color', palette[v] || '#4a9eff');
  });
  renderLegend(field, palette);
}

function renderLegend(field, palette) {
  const keys = Object.keys(palette);
  const legend = $('legend');
  if (!keys.length) { legend.classList.add('hidden'); return; }
  legend.classList.remove('hidden');
  legend.innerHTML = `<h3>${field}</h3>` +
    keys.slice(0, 30).map(k => `<div class="legend-item"><span class="legend-swatch" style="background:${palette[k]}"></span>${k}</div>`).join('') +
    (keys.length > 30 ? `<div class="muted small">+${keys.length - 30} more…</div>` : '');
}

$('color-field').addEventListener('change', applyColoring);

$('threshold').addEventListener('input', (e) => {
  const t = parseInt(e.target.value);
  $('threshold-val').textContent = t;
  if (!state.cy) return;
  state.cy.edges().forEach(ed => {
    if (ed.data('weight') > t) ed.addClass('hidden'); else ed.removeClass('hidden');
  });
});

// ---- Metadata --------------------------------------------------------------

$('meta-file').addEventListener('change', async (e) => {
  const f = e.target.files[0];
  if (!f || !state.jobId) return;
  const fd = new FormData();
  fd.append('file', f);
  const r = await fetch('/api/jobs/' + state.jobId + '/metadata', { method: 'POST', body: fd });
  if (!r.ok) { alert('Metadata upload failed'); return; }
  const data = await r.json();
  state.metaFields = ['st', ...data.fields];
  // Refresh MST from server (now with metadata baked into nodes)
  const fresh = await api('/jobs/' + state.jobId);
  state.mst = fresh.mst;
  renderMst();
});

// ---- Export ----------------------------------------------------------------

$('export-png').addEventListener('click', () => {
  if (!state.cy) return;
  const png = state.cy.png({ output: 'blob', scale: 2, bg: '#0a0d12' });
  const url = URL.createObjectURL(png);
  const a = document.createElement('a');
  a.href = url; a.download = 'mst.png'; a.click();
  URL.revokeObjectURL(url);
});

$('export-svg').addEventListener('click', () => {
  if (!state.cy) return;
  // Cytoscape doesn't ship SVG export out of the box; the cytoscape-svg plugin would
  // add it. For now: PNG works, and SVG can be wired in M8.
  alert('SVG export is planned for M8. PNG works now.');
});

// ---- Init ------------------------------------------------------------------

loadSchemes().catch(e => console.error(e));
