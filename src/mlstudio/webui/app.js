// MLSTudio WebUI

const $ = (id) => document.getElementById(id);
const state = {
  jobId: null,
  schemeKey: null,
  schemeKind: 'mlst',
  schemeClusterThreshold: 0,
  results: [],
  mst: null,
  metaFields: ['st'],
  maxEdge: 0,
  cy: null,
  currentPalette: {},
  clusterThreshold: 0,
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

// ---- Catalog ---------------------------------------------------------------

async function loadCatalog() {
  const data = await api('/schemes');
  const sel = $('scheme-select');
  sel.innerHTML = '';
  const cat = $('catalog-list');
  cat.innerHTML = '';

  // Group by organism
  const byOrg = {};
  for (const s of data.registry) {
    (byOrg[s.organism] = byOrg[s.organism] || []).push(s);
  }

  for (const [org, schemes] of Object.entries(byOrg).sort()) {
    for (const s of schemes) {
      const opt = document.createElement('option');
      opt.value = s.key;
      opt.textContent = `${s.organism} · ${s.scheme}` + (s.cached ? ' ✓' : '');
      opt.dataset.kind = s.kind;
      opt.dataset.cluster = s.cluster_threshold;
      sel.appendChild(opt);

      const row = document.createElement('div');
      row.className = 'catalog-row';
      row.innerHTML = `
        <div>
          <div class="name">${s.organism}</div>
          <div class="muted small">${s.scheme} <span class="kind ${s.kind}">${s.kind}</span></div>
        </div>
        ${s.cached ? '<span class="ok">✓</span>' : `<button class="mini" data-key="${s.key}">Pull</button>`}
      `;
      cat.appendChild(row);
    }
  }

  cat.querySelectorAll('button[data-key]').forEach(b => {
    b.addEventListener('click', async (e) => {
      const k = e.target.dataset.key;
      e.target.textContent = '…';
      e.target.disabled = true;
      try { await api(`/schemes/${k}/pull`, { method: 'POST' }); }
      catch (err) { alert('Pull failed: ' + err.message); }
      await loadCatalog();
    });
  });

  schemeChanged();
}

function schemeChanged() {
  const sel = $('scheme-select');
  const opt = sel.options[sel.selectedIndex];
  if (!opt) return;
  state.schemeKey = opt.value;
  state.schemeKind = opt.dataset.kind || 'mlst';
  state.schemeClusterThreshold = parseInt(opt.dataset.cluster) || 0;
  $('cluster-threshold').value = state.schemeClusterThreshold;
  state.clusterThreshold = state.schemeClusterThreshold;
}

$('scheme-select').addEventListener('change', schemeChanged);

// ---- Scan ------------------------------------------------------------------

// ---- Folder browser ------------------------------------------------------

let browseCwd = null;

async function openBrowse(initial) {
  $('browse-modal').classList.remove('hidden');
  await loadBrowse(initial || $('folder-input').value || '~');
}

async function loadBrowse(path) {
  try {
    const data = await api('/fs/list?path=' + encodeURIComponent(path));
    browseCwd = data.path;
    $('browse-cwd').textContent = data.path;
    $('browse-summary').textContent = data.n_fasta_in_dir
      ? `${data.n_fasta_in_dir} FASTA file(s) here`
      : 'No FASTA files in this directory';
    $('browse-up').disabled = !data.parent;
    const list = $('browse-list');
    list.innerHTML = '';
    for (const e of data.entries) {
      const li = document.createElement('li');
      li.innerHTML = `<span class="ico">📁</span> ${e.name}`;
      li.addEventListener('click', () => loadBrowse(e.path));
      list.appendChild(li);
    }
    if (!data.entries.length) {
      list.innerHTML = '<li class="muted small" style="cursor:default">(no sub-folders)</li>';
    }
  } catch (e) { alert('Cannot list: ' + e.message); }
}

$('browse-btn').addEventListener('click', () => openBrowse());
$('browse-close').addEventListener('click', () => $('browse-modal').classList.add('hidden'));
$('browse-up').addEventListener('click', async () => {
  const data = await api('/fs/list?path=' + encodeURIComponent(browseCwd));
  if (data.parent) loadBrowse(data.parent);
});
$('browse-select').addEventListener('click', () => {
  $('folder-input').value = browseCwd;
  $('browse-modal').classList.add('hidden');
  $('scan-btn').click();
});
$('browse-modal').addEventListener('click', (e) => {
  if (e.target.id === 'browse-modal') $('browse-modal').classList.add('hidden');
});

$('scan-btn').addEventListener('click', async () => {
  const folder = $('folder-input').value.trim();
  if (!folder) return;
  $('scan-result').textContent = 'Scanning…';
  try {
    const data = await api('/scan?folder=' + encodeURIComponent(folder));
    const withReads = data.samples.filter(s => s.has_reads).length;
    $('scan-result').textContent = `${data.samples.length} sample(s) · ${withReads} with paired reads`;
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
    run_amr: $('run-amr').checked,
    output_folder: $('output-folder').value.trim() || null,
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

function attachClusterIds(mst, threshold) {
  // Connected components when edges with weight > threshold are removed.
  const parent = {};
  const find = (x) => parent[x] === x ? x : (parent[x] = find(parent[x]));
  const union = (a, b) => { const ra = find(a), rb = find(b); if (ra !== rb) parent[ra] = rb; };
  for (const e of mst.elements) if (!e.data.source) parent[e.data.id] = e.data.id;
  for (const e of mst.elements) {
    if (e.data.source && e.data.weight <= threshold) union(e.data.source, e.data.target);
  }
  // Number components by size desc, then by min-id for stability
  const comps = {};
  for (const k of Object.keys(parent)) {
    const r = find(k);
    (comps[r] = comps[r] || []).push(k);
  }
  const ordered = Object.values(comps).sort((a, b) => b.length - a.length || a[0].localeCompare(b[0]));
  const idOf = {};
  ordered.forEach((g, i) => g.forEach(m => idOf[m] = `C${i + 1}`));
  for (const e of mst.elements) {
    if (!e.data.source) e.data.cluster_id = idOf[e.data.id];
  }
}

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
      // Compute cluster_id from current scheme threshold if not present
      const nodes = state.mst.elements.filter(e => !e.data.source);
      if (!nodes.some(n => n.data.cluster_id)) {
        attachClusterIds(state.mst, state.schemeClusterThreshold || 0);
      }
      const anySt = nodes.some(n => n.data.st);
      state.metaFields = anySt ? ['st', 'cluster_id'] : ['cluster_id', 'st'];
      populateColorFields();
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
  const showAllLoci = loci.length <= 15;
  const summary = showAllLoci ? loci : ['(too many loci — counts only)'];

  thead.innerHTML = '<tr><th>Sample</th><th>ST</th>' +
    (showAllLoci ? loci.map(l => `<th>${l}</th>`).join('') :
      '<th>EXC</th><th>INF</th><th>LNF</th>') +
    '<th>Notes</th></tr>';

  tbody.innerHTML = state.results.map(r => {
    let cells;
    if (showAllLoci) {
      cells = loci.map(l => {
        const c = r.calls[l];
        const flag = c.flag !== 'EXC' ? ` <span class="muted">(${c.flag})</span>` : '';
        return `<td>${c.allele ?? '-'}${flag}</td>`;
      }).join('');
    } else {
      let exc=0,inf=0,lnf=0;
      for (const c of Object.values(r.calls)) {
        if (c.flag === 'EXC') exc++;
        else if (c.flag === 'INF') inf++;
        else if (c.flag === 'LNF') lnf++;
      }
      cells = `<td>${exc}</td><td>${inf}</td><td>${lnf}</td>`;
    }
    return `<tr><td><b>${r.sample}</b></td><td>${r.st ?? '<span class="muted">none</span>'}</td>${cells}<td class="muted small">${r.notes.join('; ')}</td></tr>`;
  }).join('');
}

$('toggle-results').addEventListener('click', () => {
  const tableWrap = $('results-panel').querySelector('.table-wrap');
  tableWrap.classList.toggle('hidden');
  $('toggle-results').textContent = tableWrap.classList.contains('hidden') ? 'expand ▴' : 'collapse ▾';
});

// ---- Coloring / palette ----------------------------------------------------

function softColor(i, alpha = 1.0) {
  const hue = (i * 137.508) % 360;
  return alpha === 1.0
    ? `hsl(${hue}, 55%, 65%)`
    : `hsla(${hue}, 55%, 65%, ${alpha})`;
}

function colorFor(values) {
  const palette = {};
  let i = 0;
  for (const v of values) {
    if (v == null || v === '') continue;
    if (!(v in palette)) palette[v] = softColor(i++);
  }
  return palette;
}

function paletteFor(elements, field) {
  const values = elements.filter(e => !e.data.source).map(e => e.data[field]);
  return colorFor(values);
}

// ---- Clustering ------------------------------------------------------------

function computeClusters(mst, threshold) {
  // Union-Find on nodes connected by edges with weight <= threshold
  const parent = {};
  const find = (x) => parent[x] === x ? x : (parent[x] = find(parent[x]));
  const union = (a, b) => { const ra = find(a), rb = find(b); if (ra !== rb) parent[ra] = rb; };

  for (const el of mst.elements) {
    if (!el.data.source) parent[el.data.id] = el.data.id;
  }
  for (const el of mst.elements) {
    if (el.data.source && el.data.weight <= threshold) {
      union(el.data.source, el.data.target);
    }
  }
  const groups = {};
  for (const id of Object.keys(parent)) {
    const root = find(id);
    (groups[root] = groups[root] || []).push(id);
  }
  // Only return clusters of size >= 2
  return Object.values(groups).filter(g => g.length >= 2);
}

// ---- MST rendering ---------------------------------------------------------

// Auto-tune visual parameters based on dataset size so the layout stays
// legible from 5 to 5000 isolates.
function autoScale(nNodes) {
  if (nNodes <= 30) return { nodeSize: 50, fontSize: 12, edgeLabel: true,
                              labels: true, ideal: 160, repulse: 30000, edgeMax: 5.5 };
  if (nNodes <= 100) return { nodeSize: 38, fontSize: 11, edgeLabel: true,
                              labels: true, ideal: 140, repulse: 22000, edgeMax: 4.5 };
  if (nNodes <= 300) return { nodeSize: 24, fontSize: 10, edgeLabel: false,
                              labels: false, ideal: 110, repulse: 14000, edgeMax: 3 };
  if (nNodes <= 800) return { nodeSize: 16, fontSize: 9, edgeLabel: false,
                              labels: false, ideal: 80, repulse: 8000, edgeMax: 2 };
  return { nodeSize: 10, fontSize: 8, edgeLabel: false, labels: false,
           ideal: 60, repulse: 5500, edgeMax: 1.5 };
}

// Kept as a fallback. Not the primary layout — MSTs are centroid-free by
// definition, so the main layout uses fcose with edge-weight-proportional
// ideal lengths. radialTreeLayout is only used if fcose fails to register.
function radialTreeLayout(elements, scale) {
  const nodeEls = elements.filter(e => !e.data.source);
  const edgeEls = elements.filter(e => e.data.source);
  const ids = nodeEls.map(n => n.data.id);
  if (ids.length === 0) return {};
  if (ids.length === 1) return { [ids[0]]: { x: 0, y: 0 } };

  const adj = {};
  for (const id of ids) adj[id] = [];
  for (const e of edgeEls) {
    adj[e.data.source].push({ to: e.data.target, w: e.data.weight });
    adj[e.data.target].push({ to: e.data.source, w: e.data.weight });
  }

  // Pick center = node with smallest sum of unweighted hops (cheap & robust).
  function bfsHops(start) {
    const d = { [start]: 0 };
    const q = [start];
    let sum = 0, max = 0;
    while (q.length) {
      const u = q.shift();
      sum += d[u]; if (d[u] > max) max = d[u];
      for (const { to } of adj[u]) if (d[to] === undefined) { d[to] = d[u] + 1; q.push(to); }
    }
    return { sum, max };
  }
  let center = ids[0], bestSum = Infinity, bestEcc = Infinity;
  for (const id of ids) {
    const { sum, max } = bfsHops(id);
    if (max < bestEcc || (max === bestEcc && sum < bestSum)) {
      bestEcc = max; bestSum = sum; center = id;
    }
  }

  // BFS tree from center
  const parent = { [center]: null };
  const parentW = { [center]: 0 };
  const children = {}; for (const id of ids) children[id] = [];
  const visited = new Set([center]);
  const queue = [center];
  while (queue.length) {
    const u = queue.shift();
    // Sort neighbors by id for determinism then by edge weight (small first)
    const sorted = [...adj[u]].sort((a, b) => a.w - b.w || a.to.localeCompare(b.to));
    for (const { to, w } of sorted) {
      if (!visited.has(to)) {
        visited.add(to);
        parent[to] = u;
        parentW[to] = w;
        children[u].push(to);
        queue.push(to);
      }
    }
  }

  // Leaf counts
  const leaves = {};
  (function count(n) {
    if (children[n].length === 0) { leaves[n] = 1; return 1; }
    leaves[n] = children[n].reduce((s, c) => s + count(c), 0);
    return leaves[n];
  })(center);

  // Place
  const pos = {};
  const baseR = Math.max(80, scale.ideal * 0.9);
  const wScale = Math.max(2, baseR / 10);
  function place(n, a0, a1, depth) {
    const angle = (a0 + a1) / 2;
    if (n === center) {
      pos[n] = { x: 0, y: 0 };
    } else {
      const p = parent[n];
      const r0 = Math.hypot(pos[p].x, pos[p].y);
      const r = r0 + baseR * 0.7 + Math.log2(parentW[n] + 1) * wScale;
      pos[n] = { x: r * Math.cos(angle), y: r * Math.sin(angle) };
    }
    if (children[n].length === 0) return;
    let cur = a0;
    for (const ch of children[n]) {
      const span = (a1 - a0) * (leaves[ch] / leaves[n]);
      // Apply a tiny offset so the child angles are not exactly identical to parent angle
      place(ch, cur, cur + span, depth + 1);
      cur += span;
    }
  }
  place(center, 0, 2 * Math.PI, 0);
  return pos;
}

// fcose probe — check if the extension actually registered
function hasFcose() {
  try {
    const probe = cytoscape({ headless: true, elements: [] });
    const ok = !!probe.layout({ name: 'fcose' });
    probe.destroy();
    return ok;
  } catch {
    return false;
  }
}

function mstLayout(nNodes, scale, elements) {
  // Map edge weight (allele distance) → ideal pixel length.
  // Linear with a small constant so even identical isolates still
  // get a visible gap, and capped to keep very-distant edges sane.
  const wToPx = (w) => Math.min(420, 40 + w * 4);

  if (hasFcose()) {
    return {
      name: 'fcose',
      quality: nNodes <= 200 ? 'proof' : 'default',
      randomize: true,
      animate: false,
      nodeDimensionsIncludeLabels: true,
      fit: true,
      padding: 60,
      // Edge length proportional to its weight — this is the whole point.
      idealEdgeLength: (edge) => wToPx(edge.data('weight') || 1),
      nodeRepulsion: () => scale.repulse,
      edgeElasticity: () => 0.45,
      nestingFactor: 0.1,
      gravity: 0.18,
      gravityRange: 3.5,
      gravityCompound: 1.0,
      numIter: nNodes <= 100 ? 5000 : 3000,
      tile: false,
      uniformNodeDimensions: false,
      packComponents: true,
    };
  }
  // Fallback: deterministic radial tree (less ideal but always works)
  const positions = radialTreeLayout(elements, scale);
  return {
    name: 'preset',
    positions: (n) => positions[n.id()] || { x: 0, y: 0 },
    fit: true, padding: 60, animate: false,
  };
}

function renderMst() {
  if (!state.mst) return;
  const nNodes = state.mst.elements.filter(e => !e.data.source).length;
  state.maxEdge = Math.max(0, ...state.mst.elements.filter(e => e.data.source).map(e => e.data.weight));
  $('threshold').max = Math.max(1, state.maxEdge);
  $('threshold').value = state.maxEdge;
  $('threshold-val').textContent = state.maxEdge;

  const scale = autoScale(nNodes);
  state.scale = scale;
  // Respect user override on labels checkbox; otherwise use scale default.
  const userOverride = $('show-labels').dataset.userSet === '1';
  if (!userOverride) $('show-labels').checked = scale.labels;

  if (state.cy) state.cy.destroy();

  const initialField = $('color-field').value || 'st';
  state.currentPalette = paletteFor(state.mst.elements, initialField);

  const elements = state.mst.elements.map(el => {
    if (!el.data.source) {
      const v = el.data[initialField];
      return { ...el, data: { ...el.data, _color: state.currentPalette[v] || '#94a3b8' } };
    }
    return el;
  });

  state.cy = cytoscape({
    container: $('cy'),
    elements: elements,
    layout: mstLayout(nNodes, scale, elements),
    wheelSensitivity: 0.2,
    style: [
      {
        selector: 'node',
        style: {
          'background-color': 'data(_color)',
          'background-opacity': 0.92,
          'label': $('show-labels').checked ? 'data(label)' : '',
          'color': '#334155',
          'font-size': scale.fontSize + 'px',
          'font-weight': 500,
          'text-valign': 'bottom',
          'text-halign': 'center',
          'text-margin-y': 6,
          'border-width': nNodes > 300 ? 1 : 2,
          'border-color': '#ffffff',
          'border-opacity': 0.95,
          'width': scale.nodeSize, 'height': scale.nodeSize,
          'text-outline-width': 3,
          'text-outline-color': '#ffffff',
        }
      },
      // Cluster halos are now rendered on an overlay canvas — no compound parents.
      {
        selector: 'edge',
        style: {
          'width': (ele) => {
            const w = ele.data('weight');
            const norm = w / Math.max(1, state.maxEdge);
            return Math.max(0.8, scale.edgeMax * (1 - 0.7 * norm));
          },
          'line-color': '#94a3b8',
          'line-opacity': nNodes > 300 ? 0.5 : 0.7,
          'curve-style': 'bezier',
          'control-point-step-size': 30,
          'label': scale.edgeLabel ? 'data(label)' : '',
          'font-size': scale.fontSize + 'px',
          'color': '#64748b',
          'text-rotation': 'autorotate',
          'text-background-color': '#ffffff',
          'text-background-opacity': 0.92,
          'text-background-padding': '2px',
        }
      },
      {
        selector: 'node:selected',
        style: { 'border-color': '#f59e0b', 'border-width': 3.5, 'border-opacity': 1 }
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
  // Re-render hulls on every viewport change
  state.cy.on('render pan zoom drag', () => redrawHulls());

  ensureHullCanvas();
  applyClusters();
  renderLegend(initialField, state.currentPalette);
}

// Cluster info is computed but rendered as canvas hulls — see drawHulls()
function applyClusters() {
  if (!state.cy || !state.mst) return;
  const threshold = parseInt($('cluster-threshold').value) || 0;
  state.clusterThreshold = threshold;

  const groups = (threshold > 0) ? computeClusters(state.mst, threshold) : [];
  state.clusters = groups.map((members, i) => ({
    id: `C${i + 1}`,
    name: `Cluster ${i + 1}`,
    members,
    color: softColor(i),
  }));
  if (state.cy) {
    state.cy.scratch('_clusters', state.clusters);
    redrawHulls();
  }
}

// ---- Canvas hulls (the cluster nebula replacement) ----------------------

let hullCanvas = null;
let hullCtx = null;

function ensureHullCanvas() {
  if (hullCanvas) return;
  const cyDiv = $('cy');
  hullCanvas = document.createElement('canvas');
  hullCanvas.style.position = 'absolute';
  hullCanvas.style.inset = '0';
  hullCanvas.style.pointerEvents = 'none';
  hullCanvas.style.zIndex = '1';
  cyDiv.appendChild(hullCanvas);
  hullCtx = hullCanvas.getContext('2d');
  new ResizeObserver(resizeHullCanvas).observe(cyDiv);
  resizeHullCanvas();
}

function resizeHullCanvas() {
  if (!hullCanvas) return;
  const cyDiv = $('cy');
  const r = cyDiv.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  hullCanvas.width = r.width * dpr;
  hullCanvas.height = r.height * dpr;
  hullCanvas.style.width = r.width + 'px';
  hullCanvas.style.height = r.height + 'px';
  hullCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  redrawHulls();
}

// Andrew's monotone chain convex hull
function convexHull(pts) {
  pts = pts.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (pts.length < 3) return pts;
  const cross = (O, A, B) => (A[0]-O[0])*(B[1]-O[1]) - (A[1]-O[1])*(B[0]-O[0]);
  const lower = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length-2], lower[lower.length-1], p) <= 0)
      lower.pop();
    lower.push(p);
  }
  const upper = [];
  for (let i = pts.length - 1; i >= 0; i--) {
    const p = pts[i];
    while (upper.length >= 2 && cross(upper[upper.length-2], upper[upper.length-1], p) <= 0)
      upper.pop();
    upper.push(p);
  }
  upper.pop(); lower.pop();
  return lower.concat(upper);
}

function expandHull(hull, pad) {
  // Move each point outward from the hull centroid by `pad` pixels.
  const cx = hull.reduce((s, p) => s + p[0], 0) / hull.length;
  const cy = hull.reduce((s, p) => s + p[1], 0) / hull.length;
  return hull.map(([x, y]) => {
    const dx = x - cx, dy = y - cy;
    const r = Math.hypot(dx, dy) || 1;
    return [x + dx / r * pad, y + dy / r * pad];
  });
}

function redrawHulls() {
  if (!hullCanvas || !state.cy) return;
  ensureHullCanvas();
  const ctx = hullCtx;
  ctx.clearRect(0, 0, hullCanvas.width, hullCanvas.height);
  const clusters = state.clusters || [];
  for (const c of clusters) {
    const pts = c.members.map(id => {
      const n = state.cy.getElementById(id);
      if (!n || n.empty()) return null;
      const p = n.renderedPosition();
      return [p.x, p.y];
    }).filter(Boolean);
    if (pts.length < 2) continue;
    const zoom = state.cy.zoom();
    const pad = 28 + 18 * Math.min(2, zoom);
    let outline;
    if (pts.length === 2) {
      // Two points: draw an ellipse rotated to align with them
      const [a, b] = pts;
      const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
      const dx = b[0] - a[0], dy = b[1] - a[1];
      const len = Math.hypot(dx, dy);
      const angle = Math.atan2(dy, dx);
      ctx.save();
      ctx.translate(mx, my); ctx.rotate(angle);
      ctx.beginPath();
      ctx.ellipse(0, 0, len / 2 + pad, pad, 0, 0, 2 * Math.PI);
      ctx.fillStyle = c.color + '40';
      ctx.fill();
      ctx.strokeStyle = c.color;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([5, 4]);
      ctx.stroke();
      ctx.restore();
      // Label
      ctx.fillStyle = c.color;
      ctx.font = 'bold 13px -apple-system, system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(c.name, mx, my - pad - 6);
      continue;
    } else {
      const hull = convexHull(pts);
      outline = expandHull(hull, pad);
    }
    // Smooth rounded polygon
    ctx.beginPath();
    for (let i = 0; i < outline.length; i++) {
      const p = outline[i];
      const prev = outline[(i - 1 + outline.length) % outline.length];
      const next = outline[(i + 1) % outline.length];
      const mx1 = (prev[0] + p[0]) / 2, my1 = (prev[1] + p[1]) / 2;
      const mx2 = (p[0] + next[0]) / 2, my2 = (p[1] + next[1]) / 2;
      if (i === 0) ctx.moveTo(mx1, my1);
      ctx.quadraticCurveTo(p[0], p[1], mx2, my2);
    }
    ctx.closePath();
    ctx.fillStyle = c.color + '38';
    ctx.fill();
    ctx.strokeStyle = c.color;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([5, 4]);
    ctx.stroke();
    // Label at hull centroid (slightly above)
    const cx = outline.reduce((s, p) => s + p[0], 0) / outline.length;
    const cy = outline.reduce((s, p) => s + p[1], 0) / outline.length;
    const topY = Math.min(...outline.map(p => p[1]));
    ctx.fillStyle = c.color;
    ctx.font = 'bold 13px -apple-system, system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(c.name, cx, topY - 8);
  }
  ctx.setLineDash([]);
}

$('cluster-threshold').addEventListener('input', (e) => {
  $('cluster-threshold-val').textContent = e.target.value;
  applyClusters();
});

$('distance-policy').addEventListener('change', async (e) => {
  if (!state.jobId) { $('policy-status').textContent = '(no analysis loaded)'; return; }
  $('policy-status').textContent = 'Recomputing…';
  try {
    const res = await api(`/jobs/${state.jobId}/recompute?policy=${e.target.value}`, { method: 'POST' });
    state.mst = res.mst;
    // Recompute cluster_id from new MST
    const t = parseInt($('cluster-threshold').value) || 0;
    attachClusterIds(state.mst, t);
    populateColorFields();
    renderMst();
    $('policy-status').textContent = `Policy: ${e.target.value}`;
  } catch (err) {
    $('policy-status').textContent = 'Error: ' + err.message;
  }
});

function applyColoring() {
  if (!state.cy) return;
  const field = $('color-field').value;
  const palette = paletteFor(state.mst.elements, field);
  state.currentPalette = palette;
  state.cy.nodes().forEach(n => {
    const v = n.data(field);
    n.data('_color', palette[v] || '#94a3b8');
  });
  applyClusters();
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

function populateColorFields() {
  const sel = $('color-field');
  const cur = sel.value;
  sel.innerHTML = '';
  for (const f of state.metaFields) {
    const opt = document.createElement('option');
    opt.value = f; opt.textContent = f;
    sel.appendChild(opt);
  }
  if ([...sel.options].some(o => o.value === cur)) sel.value = cur;
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

$('show-labels').addEventListener('change', (e) => {
  e.target.dataset.userSet = '1';
  if (!state.cy) return;
  state.cy.style().selector('node:childless').style('label', e.target.checked ? 'data(label)' : '').update();
});

$('fit-btn').addEventListener('click', () => state.cy && state.cy.fit(null, 50));

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
  const fresh = await api('/jobs/' + state.jobId);
  state.mst = fresh.mst;
  populateColorFields();
  renderMst();
});

// ---- Export ----------------------------------------------------------------

$('export-png').addEventListener('click', () => {
  if (!state.cy) return;
  const png = state.cy.png({ output: 'blob', scale: 2, bg: '#ffffff' });
  const url = URL.createObjectURL(png);
  const a = document.createElement('a');
  a.href = url; a.download = 'mst.png'; a.click();
  URL.revokeObjectURL(url);
});

// ---- Init ------------------------------------------------------------------

loadCatalog().catch(e => console.error(e));

// Auto-fill folder from ?folder= URL param
const urlParams = new URLSearchParams(location.search);
if (urlParams.get('folder')) $('folder-input').value = urlParams.get('folder');
