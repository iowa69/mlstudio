# MLSTudio Roadmap

> Living document. Last updated: 2026-05-17.

## Vision

A polished, integrated Linux desktop experience for bacterial MLST / cgMLST typing and visualization. Built around the daily workflow of public-health microbiology, lab QC, and outbreak investigation — open-source, local-first, no licence cost.

## Non-goals

- No assembler — input is assembled contigs (FASTA) or paired reads for the rescue step.
- No Windows or macOS native build (PRs welcome later, not a v1 priority).
- No cloud / SaaS — fully local.
- No phylogenetic tree inference (ML / Bayesian) — MST only for v1.
- No redistribution of any scheme that is not freely licensed for redistribution.

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                       Browser (localhost)                      │
│                                                                │
│  ┌─ MST Viewer (Cytoscape.js) ──────────────────────────────┐  │
│  │  • Movable nodes, lasso, threshold slider                │  │
│  │  • Metadata coloring, pie-slice strain composition       │  │
│  │  • Export SVG / PNG / GraphML                            │  │
│  └──────────────────────────────────────────────────────────┘  │
│  ┌─ Profile Table ─────────┐  ┌─ Metadata Panel ────────────┐  │
│  │  Allele profiles, AMR   │  │  Import CSV/TSV, fields,    │  │
│  │  hits, ST assignments   │  │  filters, color rules       │  │
│  └─────────────────────────┘  └─────────────────────────────┘  │
└──────────────────┬─────────────────────────────────────────────┘
                   │  HTTP REST + WebSocket
                   ▼
┌────────────────────────────────────────────────────────────────┐
│           FastAPI backend  (mlstudio.api.server)               │
│                                                                │
│  schemes/   BIGSdb-compatible clients (PubMLST / Pasteur)      │
│             auto-download, version-pinning, scheme registry    │
│                                                                │
│  calling/   MLST  →  BLAST allele lookup                       │
│             cgMLST →  BLAST primary call + Bowtie2 rescue      │
│                       multiprocessing job pool                 │
│                                                                │
│  amr/       AMRFinderPlus wrapper, joined to profiles          │
│                                                                │
│  profiles/  SQLite store · Hamming distance · MST (goeBURST)   │
│             Incremental updates, Parquet distance matrix cache │
│                                                                │
│  cli/       Typer-based CLI                                    │
└──────────────────┬─────────────────────────────────────────────┘
                   │  subprocess
                   ▼
       ncbi-blast+ · bowtie2 · samtools · fastp · ncbi-amrfinderplus
       (all installed via conda dependency)
```

## Milestones

### M0 — Repo scaffold ✅
- Top-level docs (README, LICENSE, ROADMAP, CONTRIBUTING, CI)
- Python package skeleton with submodules
- pyproject.toml with `mlstudio` console-script entry point

### M1 — Scheme manager ✅
- BIGSdb / PubMLST REST client (works against PubMLST.org and BIGSdb-Pasteur)
- Local scheme cache (`~/.local/share/mlstudio/schemes/<key>/`)
- CLI: `mlstudio schemes list`, `schemes pull <key>`
- SHA-256 manifest per scheme version for reproducibility

### M2 — MLST calling ✅
- BLAST+ wrapper with multiprocessing job pool
- Allele lookup → ST assignment from profile table
- EXC / INF / LNF flagging per locus
- CLI: `mlstudio call mlst --scheme <key> --input <fasta>`

### M2.5 — Web UI ✅
- FastAPI local server with REST + WebSocket progress
- Cytoscape.js minimum spanning tree viewer
- Live threshold slider, metadata CSV upload, color-by-field, PNG export
- `mlstudio gui [folder]` launches the experience

### M3 — cgMLST calling with rescue
- Two-stage call: BLAST primary → Bowtie2 read-backed rescue for missing/spurious loci
- Configurable identity/coverage thresholds
- Smart caching: skip recomputation for unchanged inputs

### M4 — AMRFinderPlus integration
- Auto-install AMRFinderPlus database
- Run alongside typing, join results into the profile table
- GUI panel: per-isolate AMR gene/mutation summary

### M5 — Profile DB + distance polish
- SQLite schema: isolates, profiles, metadata, AMR hits, scheme version
- Vectorized Hamming over numpy/numba
- goeBURST tie-breaking on MST construction

### M6 — Species auto-detection
- Identify organism from assembly by hitting all locally-cached scheme allele DBs in parallel
- Pick the scheme with the most high-identity hits across its loci
- Fall back to a manual scheme picker when ambiguous

### M7 — Cytoscape.js polish 🎯 differentiator
- Stable layout with random-seed pinning
- Drag nodes / pin positions / lasso selection
- Pie-chart composite nodes when grouping samples
- Minimap, smooth zoom & pan
- Export: SVG, PNG (high-DPI), GraphML, Newick

### M8 — Project workspace
- Save/load project files (`.mlsproj` = zip of SQLite + metadata + figures)
- Metadata import (CSV/TSV, Excel)
- Custom color rules
- Comparison view: two MSTs side-by-side

### M9 — Packaging
- Bioconda recipe for the engine
- AppImage for the bundled GUI
- Documentation site (mkdocs-material)

### M10 — Benchmark paper
- Datasets: published outbreak panels (Listeria, S. aureus, K. pneumoniae)
- Metrics: calling accuracy, runtime, peak memory, time-to-figure
- Target journals: Microbial Genomics, Bioinformatics Advances

## Risk register

| Risk | Mitigation |
|------|------------|
| Cytoscape.js perf >5k nodes | Sigma.js/WebGL fallback for very large projects |
| Scheme licensing | Only redistribute / cache schemes that are freely licensed; document how to import private schemes locally |
| Bowtie2 rescue slower than expected | Make rescue optional; offer minimap2 alternative |
| Bioconda recipe complexity (native deps) | Lean on existing recipes (blast, bowtie2, fastp already packaged) |
| Single-maintainer bus factor | Public dev from now, RFC-style design docs, encourage contributors |

## Open questions

- goeBURST vs classic Prim MST: do users want both, or just one with sensible defaults?
- Should AMRFinderPlus run be optional per project, or always-on?
- WebSocket vs Server-Sent Events for job progress?
- AppImage vs Flatpak — which has lower friction for academic bioinformaticians?
