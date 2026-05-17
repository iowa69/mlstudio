# MLSTudio Roadmap

> Living document. Last updated: 2026-05-17.

## Vision

A free, polished, integrated Linux desktop experience for bacterial MLST / cgMLST typing and visualization. Equal to Ridom SeqSphere in *daily usability* on the analyses 80% of users actually run; free and open where SeqSphere is commercial and closed.

## Non-goals

- No assembler — input is assembled contigs (FASTA) or paired reads for the rescue step.
- No Windows or macOS native build (PRs welcome later, not a v1 priority).
- No cloud / SaaS — fully local.
- No phylogenetic tree inference (ML / Bayesian) — MST only for v1.
- No proprietary scheme distribution — only public/free schemes (PubMLST, cgMLST.org, public Ridom schemes).

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                       Browser (localhost)                      │
│  Vue 3 + Pinia + Tailwind                                      │
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
│  schemes/   PubMLST + cgMLST.org + Ridom public                │
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
       ncbi-blast+ · bowtie2 · samtools · ncbi-amrfinderplus
       (all installed via conda dependency)
```

## Milestones

### M0 — Repo scaffold ✅ (this commit)
- README, LICENSE, .gitignore, ROADMAP
- Python package skeleton with submodules
- pyproject.toml with `mlstudio` console-script entry point
- CI workflow (lint + test stub)

### M1 — Scheme manager
- PubMLST API client (`/db` endpoint, schemes, loci, profile downloads)
- cgMLST.org scrape/API for public cgMLST schemes
- Local scheme cache (`~/.mlstudio/schemes/<species>/<scheme>/v<version>/`)
- CLI: `mlstudio schemes list`, `schemes pull <name>`, `schemes update <name>`
- SHA-256 manifest per scheme version for reproducibility

### M2 — MLST calling
- BLAST+ wrapper with multiprocessing job pool (1 job per genome)
- Allele lookup → ST assignment from profiles table
- Output: per-genome JSON + project-level TSV
- CLI: `mlstudio call mlst --scheme <name> --input <dir|file>`
- Benchmark vs `mlst` (Seemann) for correctness parity

### M3 — cgMLST calling with rescue
- Two-stage call: BLAST primary → Bowtie2 read-backed rescue for missing/spurious loci
- Configurable identity/coverage thresholds
- ASM/INF/LNF/PLOT5/PLOT3 chewBBACA-style allele class annotation
- Smart caching: skip recomputation for unchanged inputs

### M4 — AMRFinderPlus integration
- Auto-install AMRFinderPlus database
- Run alongside typing, join results into the profile table
- GUI panel: per-isolate AMR gene/mutation summary

### M5 — Profile DB + distance matrix
- SQLite schema: isolates, profiles, metadata, AMR hits, scheme version
- Hamming distance computation (vectorized numpy / numba)
- MST construction (goeBURST / classic Prim with allele-difference tie-breaking)
- Parquet caching for large matrices

### M6 — FastAPI server + minimal Vue frontend
- `mlstudio gui` launches local server on auto-picked port, opens browser
- REST endpoints: `/isolates`, `/distances`, `/mst`, `/schemes`, `/jobs`
- WebSocket for job progress
- Basic isolate table view in Vue

### M7 — Cytoscape.js MST 🎯 differentiator
- Force-directed layout with stable random seed
- Live threshold slider (slider value → edges collapsed → clusters recomputed)
- Drag nodes / pin positions
- Metadata-driven coloring (categorical / numeric / pie-chart composites)
- Selection / lasso / cluster highlight
- Smooth zoom & pan, minimap
- Export: SVG, PNG (high-DPI), GraphML, Newick (for cluster summaries)

### M8 — Project workspace
- Save/load project files (`.mlsproj` = zip of SQLite + metadata + figures)
- Metadata import (CSV/TSV, Excel)
- Custom color rules
- Comparison view: two MSTs side-by-side

### M9 — Packaging
- Bioconda recipe for the engine
- AppImage for the bundled GUI (includes Python + frontend dist)
- Documentation site (mkdocs-material)

### M10 — Benchmark paper
- Datasets: published cgMLST benchmarks (Listeria, S. aureus, K. pneumoniae outbreaks)
- Compare MLSTudio vs chewBBACA+GrapeTree vs SeqSphere (if access available) vs PHYLOViZ
- Metrics: calling accuracy, runtime, peak memory, time-to-figure
- Target journals: Microbial Genomics, Bioinformatics Advances

## Risk register

| Risk | Mitigation |
|------|------------|
| Cytoscape.js perf >5k nodes | Sigma.js/WebGL fallback for very large projects |
| Scheme licensing (Ridom proprietary schemes) | Only ship/redistribute public schemes; document how to import private schemes locally |
| Bowtie2 rescue slower than expected | Make rescue optional; offer minimap2 alternative |
| Bioconda recipe complexity (native deps) | Lean on existing recipes (blast, bowtie2, amrfinderplus already packaged) |
| Single-maintainer bus factor | Public dev from M3 onward, RFC-style design docs, encourage contributors |

## Open questions

- goeBURST vs classic Prim MST: do users want both?
- Should AMRFinderPlus run be optional per project, or always-on?
- WebSocket vs Server-Sent Events for job progress?
- AppImage vs Flatpak — which has lower friction for academic bioinformaticians?
