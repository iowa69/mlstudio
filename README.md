# MLSTudio

**A free, open-source, Linux-only alternative to Ridom SeqSphere for MLST and cgMLST analysis — with a polished interactive Minimum Spanning Tree GUI.**

> Status: **pre-alpha / scaffold**. See [ROADMAP.md](ROADMAP.md) for the milestone plan.

---

## What it is

MLSTudio combines best-in-class free typing engines with a GUI that's actually pleasant to use. The goal is to make running and exploring a cgMLST analysis on Linux as friction-free as the commercial tools — without the licence cost.

- **MLST + cgMLST calling** — multicore BLAST primary call, Bowtie2 read-backed rescue for missing/spurious alleles
- **AMRFinderPlus** integration for resistance gene annotation alongside typing
- **Auto-setup of schemes & dependencies** — one command pulls PubMLST, cgMLST.org schemes, BLAST+, Bowtie2, samtools, AMRFinderPlus via conda
- **Interactive Minimum Spanning Tree** — Cytoscape.js-powered, movable nodes, live threshold slider, metadata coloring, publication-quality export
- **Local web app** — runs entirely on your machine, no cloud, no telemetry. `mlstudio gui` opens it in your browser.

## What it is *not*

- Not a genome assembler. Input is assembled contigs (FASTA) or short reads (for the rescue step).
- Not Windows/macOS. Linux only, by design.
- Not a SeqSphere clone in week one. v1 fills the integration gap; full UX parity is a multi-year goal.

## Why this exists

Existing free tools each cover a slice:

| Tool          | cgMLST calling | Interactive MST | Integrated |
|---------------|:--------------:|:---------------:|:----------:|
| chewBBACA     | ✅             | ❌              | ❌         |
| GrapeTree     | ❌             | ✅              | ❌         |
| PHYLOViZ      | ❌             | ⚠️ (older)      | ❌         |
| mlst (Seemann)| ⚠️ (MLST only) | ❌              | ❌         |
| **MLSTudio**  | ✅             | ✅              | ✅         |

There is no integrated, polished, *local* Linux tool combining calling + interactive visualization. MLSTudio fills that gap.

## Quickstart

> Not functional yet — placeholders below for the v1 UX.

```bash
# Install (planned via bioconda)
conda install -c bioconda mlstudio

# One-time setup: download schemes + dependencies
mlstudio setup --species "Listeria monocytogenes"

# Run a typing job
mlstudio call --scheme listeria_cgmlst --input assemblies/ --reads reads/ -o results/

# Launch the GUI
mlstudio gui
```

## Architecture (planned)

```
┌──────────────────────────────────────────────────────────┐
│  Browser (Vue 3 + Cytoscape.js)                          │
│   ├─ MST viewer (movable nodes, threshold slider)        │
│   ├─ Profile/metadata tables                             │
│   └─ Cluster analysis panels                             │
└──────────────────────────┬───────────────────────────────┘
                           │  HTTP + WebSocket (localhost)
┌──────────────────────────▼───────────────────────────────┐
│  FastAPI backend (mlstudio.api)                          │
│   ├─ schemes/  — PubMLST / cgMLST.org / Ridom            │
│   ├─ calling/  — BLAST (multicore) + Bowtie2 rescue      │
│   ├─ amr/      — AMRFinderPlus wrapper                   │
│   └─ profiles/ — SQLite store, distance matrix, MST      │
└──────────────────────────┬───────────────────────────────┘
                           │  subprocess
                ┌──────────▼──────────┐
                │  BLAST+ / Bowtie2 / │
                │  samtools / AMR+    │
                └─────────────────────┘
```

## Tech stack

- **Backend**: Python 3.11+, FastAPI, multiprocessing, BioPython, pysam
- **Frontend**: Vue 3, Vite, Pinia, Tailwind, Cytoscape.js
- **Storage**: SQLite (profiles, metadata), Parquet (distance matrices)
- **Packaging**: Bioconda (engine), AppImage (bundled GUI)

## License

MIT — see [LICENSE](LICENSE).

## Author

Built by [@iowa69](https://github.com/iowa69).
