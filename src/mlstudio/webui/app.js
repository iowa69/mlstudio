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

  // Group by organism — both the dropdown (optgroup) and the catalog list
  const byOrg = {};
  for (const s of data.registry) {
    (byOrg[s.organism] = byOrg[s.organism] || []).push(s);
  }

  // Sort: cached organisms first, then alphabetical
  const sortedOrgs = Object.entries(byOrg).sort((a, b) => {
    const aCached = a[1].some(s => s.cached);
    const bCached = b[1].some(s => s.cached);
    if (aCached !== bCached) return aCached ? -1 : 1;
    return a[0].localeCompare(b[0]);
  });

  for (const [org, schemes] of sortedOrgs) {
    // optgroup per organism in the dropdown
    const grp = document.createElement('optgroup');
    grp.label = org;
    // Preferred order inside an organism: MLST, cgMLST, accessory, other
    const kindOrder = { mlst: 0, cgmlst: 1, accessory: 2, other: 3 };
    schemes.sort((a, b) => (kindOrder[a.kind] ?? 9) - (kindOrder[b.kind] ?? 9));
    for (const s of schemes) {
      const opt = document.createElement('option');
      opt.value = s.key;
      opt.textContent = `${s.kind} · ${s.scheme}${s.cached ? ' ✓' : ''}`;
      opt.dataset.kind = s.kind;
      opt.dataset.cluster = s.cluster_threshold;
      grp.appendChild(opt);
    }
    sel.appendChild(grp);

    // Catalog rows — grouped header + one row per scheme
    const header = document.createElement('div');
    header.className = 'catalog-org';
    header.textContent = org;
    cat.appendChild(header);
    for (const s of schemes) {
      const row = document.createElement('div');
      row.className = 'catalog-row';
      row.innerHTML = `
        <div>
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

// ---- Scheme discovery (searchable catalog) ------------------------------

let discoverData = null;

async function openDiscover() {
  $('discover-modal').classList.remove('hidden');
  if (discoverData) return renderDiscover();
  $('discover-status').textContent = 'Querying PubMLST.org and BIGSdb-Pasteur (~10 s)…';
  try {
    const r = await api('/schemes/discover');
    discoverData = r.schemes;
    renderDiscover();
  } catch (e) {
    $('discover-status').textContent = 'Error: ' + e.message;
  }
}

function renderDiscover() {
  if (!discoverData) return;
  const q = $('discover-search').value.toLowerCase().trim();
  const wantMlst = $('filter-mlst').checked;
  const wantCg = $('filter-cgmlst').checked;
  const wantAcc = $('filter-accessory').checked;

  const rows = discoverData.filter(s => {
    if (q) {
      const haystack = (s.organism + ' ' + s.description + ' ' + s.database).toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    if (s.kind === 'mlst' && !wantMlst) return false;
    if (s.kind === 'cgmlst' && !wantCg) return false;
    if (s.kind === 'accessory' && !wantAcc) return false;
    if (s.kind === 'other' && !wantAcc) return false;
    return true;
  });
  rows.sort((a, b) => a.organism.localeCompare(b.organism) ||
                       a.kind.localeCompare(b.kind));

  $('discover-status').textContent =
    `${rows.length} scheme${rows.length === 1 ? '' : 's'} match — total catalogued ${discoverData.length}`;

  const tbody = $('discover-table').querySelector('tbody');
  tbody.innerHTML = rows.slice(0, 250).map(s => {
    // Derive species from database name (e.g. pubmlst_saureus_seqdef -> "saureus")
    const m = s.database.match(/^pubmlst_([^_]+)_seqdef$/);
    const species = m ? m[1] : '';
    return `
    <tr>
      <td>
        <b>${escapeHtml(s.organism)}</b>
        ${species ? `<div class="muted small">db: <code>${species}</code></div>` : ''}
      </td>
      <td>${escapeHtml(s.description)}</td>
      <td><span class="kind ${s.kind}">${s.kind}</span></td>
      <td class="muted">${s.host.replace(/^https?:\/\//, '')}</td>
      <td><button class="primary pull-btn"
            data-host="${s.host}" data-db="${s.database}"
            data-sid="${s.scheme_id}" data-org="${escapeHtml(s.organism)}"
            data-desc="${escapeHtml(s.description)}" data-kind="${s.kind}">Pull</button></td>
    </tr>`;
  }).join('');
  tbody.querySelectorAll('.pull-btn').forEach(b => {
    b.addEventListener('click', async (e) => {
      const btn = e.target;
      btn.textContent = '…'; btn.disabled = true;
      try {
        await api('/schemes/discover/pull', {
          method: 'POST',
          body: JSON.stringify({
            host: btn.dataset.host, database: btn.dataset.db,
            scheme_id: parseInt(btn.dataset.sid),
            organism: btn.dataset.org, description: btn.dataset.desc,
            kind: btn.dataset.kind,
          }),
        });
        btn.textContent = '✓'; btn.style.background = '#10b981';
        loadCatalog();
      } catch (err) {
        btn.textContent = 'fail'; alert(err.message);
      }
    });
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

$('discover-btn').addEventListener('click', openDiscover);
$('discover-close').addEventListener('click', () => $('discover-modal').classList.add('hidden'));
$('discover-modal').addEventListener('click', (e) => {
  if (e.target.id === 'discover-modal') $('discover-modal').classList.add('hidden');
});
$('discover-search').addEventListener('input', () => renderDiscover());
$('filter-mlst').addEventListener('change', () => renderDiscover());
$('filter-cgmlst').addEventListener('change', () => renderDiscover());
$('filter-accessory').addEventListener('change', () => renderDiscover());
$('discover-refresh').addEventListener('click', async () => {
  $('discover-status').textContent = 'Re-fetching…';
  try {
    const r = await api('/schemes/discover?refresh=true');
    discoverData = r.schemes;
    renderDiscover();
  } catch (e) { $('discover-status').textContent = 'Error: ' + e.message; }
});

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
    // Probe cache to surface incremental status (rough heuristic — counts
    // cache files in the standard output folder).
    let cacheInfo = '';
    try {
      const fs = await api('/fs/list?path=' + encodeURIComponent(folder + '/.mlstudio/calls'));
      // fs.entries are sub-folders; cache files are not listed (only dirs are).
      // So we approximate using the .mlstudio folder existence.
      cacheInfo = '  ·  cache exists — re-run will be incremental';
    } catch {}
    $('scan-result').textContent =
      `${data.samples.length} sample(s) · ${withReads} with paired reads${cacheInfo}`;
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
    skip_st_lookup: $('skip-st').checked,
    project_name: $('project-name').value.trim() || null,
    min_identity: parseFloat($('min-identity').value) || null,
    min_coverage: parseFloat($('min-coverage').value) || null,
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
      $('save-project-btn').disabled = false;
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
      renderMst();
      renderComparisonTable();
    } else if (snap.status === 'error') {
      setStatus('error', snap.error || 'Error');
      $('run-btn').disabled = false;
    } else {
      setStatus('running', snap.message);
    }
  };
  ws.onerror = () => setStatus('error', 'WebSocket error');
}

// ---- Tabs -----------------------------------------------------------------

document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    const t = btn.dataset.tab;
    document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b === btn));
    document.querySelectorAll('.tab-pane').forEach(p => {
      p.classList.toggle('active', p.id === `tab-${t}`);
    });
    if (t === 'table') renderComparisonTable();
    if (t === 'stats') renderStats();
    if (t === 'tree' && state.cy) { state.cy.resize(); state.cy.fit(null, 50); }
  });
});

// ---- Comparison table ---------------------------------------------------

let sortField = 'sample';
let sortAsc = true;

function renderComparisonTable() {
  const root = $('comparison-table');
  if (!state.results.length) {
    root.innerHTML = '<p class="muted">No results yet.</p>';
    return;
  }
  const loci = Object.keys(state.results[0].calls);
  const compact = loci.length > 15;
  const colorField = $('color-field').value;
  const metaCols = (state.metaFields || []).filter(f => f !== 'cluster_id');

  const cols = [
    { key: 'sample', label: 'Sample', cls: '', getter: r => r.sample },
    { key: 'st', label: 'ST', cls: '', getter: r => r.st || '—' },
    { key: 'cluster_id', label: 'Cluster', cls: colorField === 'cluster_id' ? 'color-key' : '', getter: r => state.clusterOf?.[r.sample] || '—' },
  ];
  if (compact) {
    cols.push({ key: 'exc', label: 'EXC', cls: 'distance-key', getter: r => Object.values(r.calls).filter(c => c.flag==='EXC').length });
    cols.push({ key: 'inf', label: 'INF', cls: 'distance-key', getter: r => Object.values(r.calls).filter(c => c.flag==='INF').length });
    cols.push({ key: 'lnf', label: 'LNF', cls: '', getter: r => Object.values(r.calls).filter(c => c.flag==='LNF').length });
  } else {
    for (const l of loci) {
      cols.push({ key: l, label: l, cls: 'distance-key',
        getter: r => {
          const c = r.calls[l];
          if (!c) return '—';
          if (c.flag === 'EXC') return c.allele || '—';
          if (c.flag === 'INF') return `${c.allele || '?'}~`;
          return '—';
        }});
    }
  }
  // Metadata columns
  for (const f of metaCols.filter(f => f !== 'st')) {
    cols.push({ key: f, label: f, cls: colorField === f ? 'color-key' : '',
                getter: r => state.metaBySample?.[r.sample]?.[f] || '' });
  }

  // Sort
  const rows = [...state.results].sort((a, b) => {
    const col = cols.find(c => c.key === sortField) || cols[0];
    const va = col.getter(a), vb = col.getter(b);
    const cmp = (typeof va === 'number' && typeof vb === 'number')
      ? va - vb
      : String(va).localeCompare(String(vb), undefined, { numeric: true });
    return sortAsc ? cmp : -cmp;
  });

  const palette = state.currentPalette || {};
  const html = [];
  html.push('<table><thead><tr>');
  for (const c of cols) {
    const arrow = sortField === c.key ? (sortAsc ? ' ↑' : ' ↓') : '';
    html.push(`<th class="${c.cls}" data-key="${c.key}">${c.label}${arrow}</th>`);
  }
  html.push('</tr></thead><tbody>');
  for (const r of rows) {
    const cluster = (state.clusters || []).find(c => c.members.includes(r.sample));
    const trAttrs = cluster
      ? ` data-cluster="${cluster.id}" style="--cluster-color:${cluster.color}"`
      : '';
    html.push(`<tr${trAttrs}>`);
    for (const c of cols) {
      let v = c.getter(r);
      if (c.key === 'sample') {
        const colorVal = r[colorField] !== undefined ? r[colorField] : state.metaBySample?.[r.sample]?.[colorField];
        const swatch = palette[colorVal] ? `<span class="swatch" style="background:${palette[colorVal]}"></span>` : '';
        v = `${swatch}<b>${v}</b>`;
      }
      html.push(`<td>${v ?? ''}</td>`);
    }
    html.push('</tr>');
  }
  html.push('</tbody></table>');
  root.innerHTML = html.join('');

  root.querySelectorAll('th[data-key]').forEach(th => {
    th.addEventListener('click', () => {
      if (sortField === th.dataset.key) sortAsc = !sortAsc;
      else { sortField = th.dataset.key; sortAsc = true; }
      renderComparisonTable();
    });
  });
}

// ---- Statistics tab -----------------------------------------------------

async function renderStats() {
  const root = $('stats-content');
  if (!state.jobId) {
    root.innerHTML = '<p class="muted">Run an analysis to see statistics.</p>';
    return;
  }
  root.innerHTML = '<p class="muted">Loading…</p>';
  let s;
  try { s = await api(`/jobs/${state.jobId}/stats`); }
  catch (e) { root.innerHTML = `<p class="muted">Error: ${e.message}</p>`; return; }

  if (!s || s.empty) { root.innerHTML = '<p class="muted">No analysis loaded.</p>'; return; }

  const clusters = state.clusters || [];
  const sizes = clusters.map(c => c.members.length).sort((a,b)=>b-a);
  const warning = (s.missing_pct || 0) > 10 ? `
    <div class="warn-banner">
      ${s.missing_pct.toFixed(1)}% of locus calls are missing (LNF). For cgMLST,
      treat anything &gt; 10% with caution — the pairwise-complete distance can
      become unreliable. Consider removing low-coverage samples.
    </div>` : '';

  const cards = [
    ['Isolates',        s.n_samples],
    ['Scheme loci',     s.n_loci || '—'],
    ['EXC calls',       (s.exc || 0).toLocaleString()],
    ['INF calls',       (s.inf || 0).toLocaleString()],
    ['LNF (missing)',   (s.lnf || 0).toLocaleString(), `${(s.missing_pct || 0).toFixed(1)}% of total`],
    ['Median pairwise', s.distance?.median ?? '—', `range ${s.distance?.min}–${s.distance?.max}`],
    ['Clusters @ thr ' + (state.clusterThreshold ?? 0), clusters.length, sizes.length ? `largest: ${sizes[0]}` : ''],
  ];
  let cardHtml = '<div class="stat-grid">';
  for (const [label, value, sub] of cards) {
    cardHtml += `<div class="stat-card"><div class="label">${label}</div><div class="value">${value}</div>${sub ? `<div class="sub">${sub}</div>` : ''}</div>`;
  }
  cardHtml += '</div>';

  // Distance histogram
  let histHtml = '';
  const h = s.histogram;
  if (h && h.bins && h.bins.length) {
    const maxC = Math.max(...h.counts);
    histHtml = '<div class="stats-section"><h3>Pairwise distance distribution</h3>';
    for (let i = 0; i < h.counts.length; i++) {
      const w = maxC ? (h.counts[i] / maxC * 100) : 0;
      const lo = h.bins[i], hi = h.bins[i + 1];
      histHtml += `<div class="hist-row"><span class="lo">${lo}–${hi}</span><span class="bar-wrap"><div class="hist-bar" style="width:${w}%"></div></span><span class="count">${h.counts[i]}</span></div>`;
    }
    histHtml += '</div>';
  }

  // Cluster sizes
  let clHtml = '';
  if (clusters.length) {
    clHtml = '<div class="stats-section"><h3>Cluster membership</h3><table><thead><tr><th>Name</th><th>Members</th><th>Samples</th></tr></thead><tbody>';
    for (const c of clusters) {
      clHtml += `<tr><td><span class="swatch" style="background:${c.color}"></span><b>${c.name}</b></td><td>${c.members.length}</td><td class="muted small">${c.members.join(', ')}</td></tr>`;
    }
    clHtml += '</tbody></table></div>';
  }

  root.innerHTML = warning + cardHtml + histHtml + clHtml;
}

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
  if (nNodes <= 30) return { nodeSize: 50, fontSize: 14, edgeLabel: true,
                              labels: true, ideal: 170, repulse: 32000, edgeMax: 5.5 };
  if (nNodes <= 100) return { nodeSize: 42, fontSize: 13, edgeLabel: true,
                              labels: true, ideal: 150, repulse: 24000, edgeMax: 4.5 };
  if (nNodes <= 300) return { nodeSize: 28, fontSize: 12, edgeLabel: false,
                              labels: true, ideal: 120, repulse: 16000, edgeMax: 3.5 };
  if (nNodes <= 800) return { nodeSize: 18, fontSize: 11, edgeLabel: false,
                              labels: false, ideal: 90, repulse: 9000, edgeMax: 2.2 };
  return { nodeSize: 11, fontSize: 10, edgeLabel: false, labels: false,
           ideal: 65, repulse: 6000, edgeMax: 1.5 };
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
          'background-opacity': 1.0,
          'label': $('show-labels').checked ? 'data(label)' : '',
          'color': '#0f172a',
          'font-size': scale.fontSize + 'px',
          'font-weight': 600,
          'text-valign': 'bottom',
          'text-halign': 'center',
          'text-margin-y': 7,
          'border-width': nNodes > 300 ? 1 : 2.2,
          'border-color': '#1e293b',
          'border-opacity': 0.7,
          // Node radius grows with sqrt(member_count) so dense clones don't dominate
          'width': (ele) => scale.nodeSize * Math.sqrt(ele.data('size') || 1),
          'height': (ele) => scale.nodeSize * Math.sqrt(ele.data('size') || 1),
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
      {
        selector: 'edge.nontree',
        style: {
          'line-color': '#ef4444',
          'line-style': 'dashed',
          'line-opacity': 0.65,
          'width': 1.5,
          'label': '',
          'curve-style': 'bezier',
          'display': $('show-nontree').checked ? 'element' : 'none',
        }
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
  applyPieStyles();
  attachLockOnDrag();
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
    name: clusterDisplayName(`C${i + 1}`, members, i),
    members,
    color: softColor(i),
  }));
  // Reverse map for quick lookup
  state.clusterOf = {};
  for (const c of state.clusters) for (const m of c.members) state.clusterOf[m] = c.name;
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
  if (!clusters.length) return;

  const zoom = state.cy.zoom();
  const nodeR = 24 + 12 * Math.min(2, zoom);
  const edgeW = 24 + 14 * Math.min(2, zoom);
  const blurPx = 10 + 4 * Math.min(1.5, zoom);

  // Draw the soft halo for each cluster on its own pass so colors don't pile
  // up to black where clusters overlap. globalAlpha keeps fills airy even
  // after the blur convolution intensifies the centre.
  for (const c of clusters) {
    const positions = {};
    c.members.forEach(id => {
      const n = state.cy.getElementById(id);
      if (n && !n.empty()) {
        const p = n.renderedPosition();
        positions[id] = [p.x, p.y];
      }
    });
    const pts = Object.values(positions);
    if (pts.length < 1) continue;

    ctx.save();
    ctx.filter = `blur(${blurPx}px)`;
    ctx.globalAlpha = 0.32;            // soft wash; the blur further softens it
    ctx.fillStyle = c.color;
    ctx.strokeStyle = c.color;
    ctx.lineWidth = edgeW;
    ctx.lineCap = 'round';

    // Soft circle behind each member node
    for (const [x, y] of pts) {
      ctx.beginPath();
      ctx.arc(x, y, nodeR, 0, 2 * Math.PI);
      ctx.fill();
    }
    // Soft strokes along edges joining cluster members
    if (pts.length >= 2) {
      const memberSet = new Set(c.members);
      state.cy.edges().forEach(edge => {
        const s = edge.source().id(), t = edge.target().id();
        if (memberSet.has(s) && memberSet.has(t) && positions[s] && positions[t]) {
          ctx.beginPath();
          ctx.moveTo(positions[s][0], positions[s][1]);
          ctx.lineTo(positions[t][0], positions[t][1]);
          ctx.stroke();
        }
      });
    }
    ctx.restore();
  }

  // Phase 2: sharp text labels (no blur)
  ctx.font = 'bold 14px -apple-system, system-ui, sans-serif';
  ctx.textAlign = 'center';
  for (const c of clusters) {
    const pts = c.members.map(id => {
      const n = state.cy.getElementById(id);
      if (!n || n.empty()) return null;
      const p = n.renderedPosition();
      return [p.x, p.y];
    }).filter(Boolean);
    if (pts.length < 1) continue;
    const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
    const topY = Math.min(...pts.map(p => p[1]));
    // Solid color label
    ctx.fillStyle = '#1c2026';
    ctx.fillText(c.name, cx, topY - nodeR - 12);
  }
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
  applyPieStyles();
  renderLegend(field, palette);
}

// Pie-chart rendering: when a merged node has >1 members, color by the
// composition of the chosen field within those members. Up to 16 slices
// (Cytoscape's hard cap).
function applyPieStyles() {
  if (!state.cy) return;
  const field = $('color-field').value;
  state.cy.nodes().forEach(n => {
    const members = n.data('members') || [n.id()];
    if (members.length <= 1) {
      // Clear any prior pie slices
      const reset = {};
      for (let i = 1; i <= 16; i++) reset[`pie-${i}-background-size`] = 0;
      n.style(reset);
      return;
    }
    // composition keyed by metadata field; for st/cluster_id all members agree
    let counts;
    const comp = n.data('composition');
    if (comp && comp[field]) {
      counts = comp[field];
    } else {
      // No metadata composition available — single colored slice
      counts = { [n.data(field)]: members.length };
    }
    const total = Object.values(counts).reduce((s, v) => s + v, 0);
    const slices = Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 16);
    const styles = { 'pie-size': '100%' };
    slices.forEach(([val, count], i) => {
      styles[`pie-${i + 1}-background-color`] = state.currentPalette[val] || softColor(i);
      styles[`pie-${i + 1}-background-size`] = (count / total) * 100;
    });
    // Zero out unused slots
    for (let i = slices.length + 1; i <= 16; i++) {
      styles[`pie-${i}-background-size`] = 0;
    }
    n.style(styles);
  });
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
  populateClusterNameFields();
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

$('show-nontree').addEventListener('change', (e) => {
  if (!state.cy) return;
  state.cy.style().selector('edge.nontree')
    .style('display', e.target.checked ? 'element' : 'none').update();
});

// Cluster naming by column
function clusterDisplayName(cluster, members, ci) {
  const field = $('cluster-name-field').value;
  if (!field) return `Cluster ${ci + 1}`;
  const counts = {};
  for (const m of members) {
    const v = state.metaBySample?.[m]?.[field] ?? state.cy?.getElementById(m)?.data(field) ?? '';
    counts[v] = (counts[v] || 0) + 1;
  }
  const top = Object.entries(counts).sort((a,b) => b[1] - a[1])[0];
  if (!top) return `Cluster ${ci + 1}`;
  return top[1] === members.length ? top[0] : `${top[0]} (+${members.length - top[1]})`;
}

$('cluster-name-field').addEventListener('change', () => applyClusters());

function populateClusterNameFields() {
  const sel = $('cluster-name-field');
  const cur = sel.value;
  sel.innerHTML = '<option value="">(default: Cluster 1, 2, …)</option>';
  for (const f of state.metaFields || []) {
    if (f === 'cluster_id') continue;
    const opt = document.createElement('option');
    opt.value = f; opt.textContent = f;
    sel.appendChild(opt);
  }
  if ([...sel.options].some(o => o.value === cur)) sel.value = cur;
}

$('fit-btn').addEventListener('click', () => state.cy && state.cy.fit(null, 50));

$('relax-btn').addEventListener('click', () => {
  if (!state.cy) return;
  const nNodes = state.cy.nodes(':childless').length;
  const scale = autoScale(nNodes);
  const layout = mstLayout(nNodes, scale, state.mst.elements);
  state.cy.layout({ ...layout, randomize: true }).run();
  setTimeout(redrawHulls, 700);
});

// Auto-lock dragged nodes (Ridom convention: manual drag = pinned)
function attachLockOnDrag() {
  if (!state.cy) return;
  state.cy.on('drag', 'node', (evt) => {
    evt.target.data('_locked', true);
    evt.target.style({ 'border-color': '#f59e0b', 'border-width': 2 });
  });
}

// ---- Metadata --------------------------------------------------------------

$('meta-file').addEventListener('change', async (e) => {
  const f = e.target.files[0];
  if (!f || !state.jobId) return;
  const fd = new FormData();
  fd.append('file', f);
  const r = await fetch('/api/jobs/' + state.jobId + '/metadata', { method: 'POST', body: fd });
  if (!r.ok) { alert('Metadata upload failed'); return; }
  const data = await r.json();
  state.metaFields = ['st', ...data.fields, 'cluster_id'];
  // Parse CSV client-side too so we have a per-sample lookup for table + clusters
  const text = await f.text();
  state.metaBySample = parseMetaCsv(text);
  const fresh = await api('/jobs/' + state.jobId);
  state.mst = fresh.mst;
  populateColorFields();
  renderMst();
  renderComparisonTable();
});

function parseMetaCsv(text) {
  const lines = text.split(/\r?\n/).filter(l => l.trim());
  if (!lines.length) return {};
  const sep = lines[0].includes('\t') ? '\t' : (lines[0].includes(';') ? ';' : ',');
  const header = lines[0].split(sep);
  const out = {};
  for (const line of lines.slice(1)) {
    const cols = line.split(sep);
    if (!cols.length) continue;
    const name = cols[0];
    out[name] = {};
    for (let i = 1; i < header.length; i++) out[name][header[i]] = cols[i] || '';
  }
  return out;
}

// ---- Export ----------------------------------------------------------------

$('export-png').addEventListener('click', () => {
  if (!state.cy) return;
  const png = state.cy.png({ output: 'blob', scale: 2, bg: '#ffffff' });
  const url = URL.createObjectURL(png);
  const a = document.createElement('a');
  a.href = url; a.download = 'mst.png'; a.click();
  URL.revokeObjectURL(url);
});

$('export-svg').addEventListener('click', () => {
  if (!state.cy) return;
  try {
    const svg = state.cy.svg ? state.cy.svg({ bg: '#ffffff', full: true, scale: 2 })
                              : null;
    if (!svg) { alert('SVG export extension failed to load.'); return; }
    const blob = new Blob([svg], { type: 'image/svg+xml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'mst.svg'; a.click();
    URL.revokeObjectURL(url);
  } catch (e) { alert('SVG export failed: ' + e.message); }
});

// ---- Projects (save / load named runs) ----------------------------------

async function loadProjects() {
  const root = $('project-list');
  try {
    const data = await api('/projects');
    if (!data.projects.length) {
      root.innerHTML = '<div class="project-empty">No saved projects yet. Run an analysis and click <b>Save current as project</b>.</div>';
      return;
    }
    root.innerHTML = data.projects.map(p => `
      <div class="project-row" data-name="${escapeHtml(p.safe_name)}">
        <div>
          <div class="pj-name">${escapeHtml(p.name)}</div>
          <div class="pj-meta">${p.n_samples} samples · ${p.scheme_key} · ${(p.created_at || '').slice(0, 16)}</div>
        </div>
        <span class="pj-del" data-name="${escapeHtml(p.safe_name)}" title="Delete">×</span>
      </div>
    `).join('');
    root.querySelectorAll('.project-row').forEach(r => {
      r.addEventListener('click', (e) => {
        if (e.target.classList.contains('pj-del')) return;
        loadProject(r.dataset.name);
      });
    });
    root.querySelectorAll('.pj-del').forEach(d => {
      d.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!confirm(`Delete project "${d.dataset.name}"?`)) return;
        await fetch('/api/projects/' + encodeURIComponent(d.dataset.name), { method: 'DELETE' });
        loadProjects();
      });
    });
  } catch (e) {
    root.innerHTML = `<div class="project-empty">Error: ${e.message}</div>`;
  }
}

async function loadProject(name) {
  try {
    const p = await api('/projects/' + encodeURIComponent(name));
    state.jobId = 'project:' + name;
    state.results = p.results;
    state.mst = p.mst;
    state.metaBySample = p.metadata || {};
    state.amr_results = p.amr || {};
    state.schemeClusterThreshold = p.manifest.scheme_cluster_threshold || 0;
    state.clusterThreshold = state.schemeClusterThreshold;
    $('cluster-threshold').value = state.schemeClusterThreshold;
    $('cluster-threshold-val').textContent = state.schemeClusterThreshold;
    // Rebuild metaFields from results + metadata
    const nodes = state.mst.elements.filter(e => !e.data.source);
    if (!nodes.some(n => n.data.cluster_id)) attachClusterIds(state.mst, state.clusterThreshold);
    const anySt = nodes.some(n => n.data.st);
    const metaFs = new Set();
    for (const v of Object.values(state.metaBySample)) Object.keys(v).forEach(k => metaFs.add(k));
    state.metaFields = anySt ? ['st', 'cluster_id', ...metaFs] : ['cluster_id', 'st', ...metaFs];
    populateColorFields();
    $('empty-state').classList.add('hidden');
    $('save-project-btn').disabled = false;
    setStatus('done', `Loaded project "${p.manifest.name}" (${p.results.length} samples)`);
    renderComparisonTable();
    renderMst();
  } catch (e) {
    alert('Load failed: ' + e.message);
  }
}

$('save-project-btn').addEventListener('click', async () => {
  if (!state.jobId) return;
  const suggested = $('project-name').value.trim() ||
                    `${state.schemeKey || 'run'}_${new Date().toISOString().slice(0,10)}`;
  const name = prompt('Project name:', suggested);
  if (!name) return;
  // If the current "job" is a loaded project, save it under a job id we
  // don't have — the server only knows live jobs. So we route through a
  // fresh save endpoint that takes the in-memory state. For now: only
  // saving live jobs. Show a hint.
  if (state.jobId.startsWith('project:')) {
    alert('This is already a loaded project. Re-run the analysis to save under a new name.');
    return;
  }
  try {
    const r = await fetch(`/api/jobs/${state.jobId}/save`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name}),
    });
    if (!r.ok) throw new Error(await r.text());
    await r.json();
    await loadProjects();
    alert(`Saved as "${name}"`);
  } catch (e) {
    alert('Save failed: ' + e.message);
  }
});

// Enable Save button when a job finishes
const _origSubscribe = subscribe;
subscribe = function(jobId) {
  _origSubscribe(jobId);
};
// hooks into existing onmessage path — already calls renderMst which sets state.jobId

// ---- Init ------------------------------------------------------------------

loadCatalog().catch(e => console.error(e));
loadProjects().catch(e => console.error(e));

// Auto-fill folder from ?folder= URL param
const urlParams = new URLSearchParams(location.search);
if (urlParams.get('folder')) $('folder-input').value = urlParams.get('folder');
