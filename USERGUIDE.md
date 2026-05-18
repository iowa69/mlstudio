# MLSTudio user guide

A complete walkthrough for everyday use, from a fresh install through outbreak investigation, scheme management, and the CLI. If you only have 60 seconds, the [README](README.md) quickstart is faster — this document covers the long tail.

> MLSTudio is developed by **Giovanni Lorenzin** ([@iowa69](https://github.com/iowa69)).
> Released under the MIT license — see [LICENSE](LICENSE).

## Contents

1. [Installation](#1-installation)
2. [First run — the 5-minute tour](#2-first-run--the-5-minute-tour)
3. [Preparing your input](#3-preparing-your-input)
4. [Scheme management](#4-scheme-management)
5. [Running an analysis (web UI)](#5-running-an-analysis-web-ui)
6. [Understanding the MST](#6-understanding-the-mst)
7. [Reading the results table](#7-reading-the-results-table)
8. [Metadata and coloring](#8-metadata-and-coloring)
9. [The Statistics tab](#9-the-statistics-tab)
10. [Projects: save / load](#10-projects-save--load)
11. [Antimicrobial resistance](#11-antimicrobial-resistance)
12. [The Sidebar — every option explained](#12-the-sidebar--every-option-explained)
13. [Command-line reference](#13-command-line-reference)
14. [Building an ad-hoc cgMLST scheme](#14-building-an-ad-hoc-cgmlst-scheme)
15. [Caches and file locations](#15-caches-and-file-locations)
16. [Troubleshooting](#16-troubleshooting)
17. [FAQ](#17-faq)

---

## 1. Installation

### Option A — Bioconda (recommended once the recipe lands)

```bash
conda install -c bioconda mlstudio
mlstudio --version    # 1.3.0
mlstudio gui          # opens the web UI in your browser
```

### Option B — from source with the bundled environment

```bash
git clone https://github.com/iowa69/mlstudio.git
cd mlstudio
./setup.sh            # creates conda env "mlstudio" with all deps
conda activate mlstudio
mlstudio gui
```

The `setup.sh` script installs: Python ≥ 3.11, `blast`, `prodigal`, `fastp`, `ncbi-amrfinderplus`, and the Python dependencies declared in `pyproject.toml`. If you already have these in another env, `pip install -e .` from the repo root works equally well.

### Verify the install

```bash
mlstudio --version            # → mlstudio 1.3.0
blastn -version | head -1     # → blastn 2.14 (or newer)
fastp --version 2>&1 | head -1
amrfinder --version           # optional, only if you use AMR scanning
```

If any of those four are missing, see [Troubleshooting](#16-troubleshooting).

---

## 2. First run — the 5-minute tour

```bash
# 1. Pre-cache the 7 WHO-priority ESKAPEE cgMLST schemes (one-time, ~1–2 GB)
mlstudio schemes pull-eskapee

# 2. Launch the GUI
mlstudio gui
```

A browser tab opens at `http://127.0.0.1:<port>/`. The first thing you'll see is the **Welcome** panel and a sidebar with seven numbered sections — those are the steps of an analysis.

To see something happen immediately:

1. Click **Browse…** in section 1, pick the bundled `test_data/demo_folder/`.
2. In section 2, pick **Listeria monocytogenes — MLST**.
3. Skip the rest of the options.
4. Click ▶ **Analyze**.

You should see three nodes (EGD-e, 10403S, F2365), an MST connecting them with allele-distance labels, and STs assigned in the **Comparison Table** tab.

---

## 3. Preparing your input

MLSTudio accepts a single folder containing:

- **FASTA assemblies** with extensions `.fa`, `.fasta`, `.fna`. The sample name is the file stem, after stripping `.contigs`, `.scaffolds`, `.assembly`, `.genomic`, and `.asm` suffixes. So `isolate_001.contigs.fasta` becomes sample `isolate_001`.
- **Optional paired-end Illumina FASTQs** named to pair with a FASTA. Patterns recognized: `_R1_/_R2_`, `_1./_2.`, `.R1./.R2.`, `.1.fastq/.2.fastq`. Compressed (`.gz`) is fine. When reads are present and **Run fastp on FASTQ pairs** is enabled, fastp trims and quality-checks the reads before BLAST.

```
my_outbreak/
├── isolate_001.fasta
├── isolate_001_R1.fastq.gz
├── isolate_001_R2.fastq.gz
├── isolate_002.fasta            # FASTA-only sample
└── isolate_003.scaffolds.fa     # becomes sample "isolate_003"
```

A folder can contain dozens to thousands of isolates. Analyses on the same folder are **incremental**: a per-sample call cache keyed by file size + mtime + scheme version means MLSTudio skips work for unchanged inputs.

---

## 4. Scheme management

MLSTudio knows about four scheme sources:

| Source | Examples | Pulled with |
|--------|----------|-------------|
| **PubMLST** | *S. aureus, E. coli, S. pyogenes…* | `mlstudio schemes pull saureus_mlst` |
| **BIGSdb-Pasteur** | *L. monocytogenes, K. pneumoniae…* | `mlstudio schemes pull lmonocytogenes_mlst` |
| **cgMLST.org** | All 40 organisms on cgmlst.org | `mlstudio schemes pull-eskapee` (bulk) or **Browse all** in the UI |
| **Ad-hoc** | Anything you have a reference genome for | `mlstudio schemes build-adhoc --reference ref.fasta --key …` |

### The bundled ESKAPEE shortcut

The seven [WHO-priority ESKAPEE](https://en.wikipedia.org/wiki/ESKAPE) pathogens are the most-typed in clinical micro:

```bash
mlstudio schemes pull-eskapee     # all 7 cgMLST schemes from cgmlst.org
mlstudio schemes pull-eskapee --force    # re-download
```

If the live download fails (slow network, firewall, cgmlst.org down), grab the offline tarball from the [GitHub releases page](https://github.com/iowa69/mlstudio/releases) and unpack it manually — see the README "Offline / manual scheme install" section.

### Inspecting the cache

```bash
mlstudio schemes list             # locally cached schemes only
mlstudio schemes list --remote    # everything MLSTudio knows about, with ✓/— cached state
```

Or in the UI: section 7 **Scheme catalog** shows the live catalog; **Browse all PubMLST schemes…** opens a live-searchable modal of every BIGSdb / PubMLST / cgMLST.org scheme.

---

## 5. Running an analysis (web UI)

```bash
mlstudio gui /optional/path/to/folder
```

The seven sidebar sections walk you through a run.

1. **Input folder** — Browse or paste a path → **Scan folder**. The result shows N FASTA files + which have paired reads.
2. **Scheme** — pick one from the dropdown. First use of a scheme triggers a one-time download.
3. **Options** — at minimum set a **Project name** (optional; used when you save). All other knobs in section 3 are sensible defaults; the [Sidebar reference](#12-the-sidebar--every-option-explained) explains each one.
4. ▶ **Analyze** — progress bar and WebSocket log. For a 100-isolate run this is typically 5–30 minutes depending on cgMLST scheme size and core count.
5. **Metadata** (optional) — drop in a CSV/TSV. The first column must contain sample names; other columns become color and grouping fields.
6. **Distance** — change the [missing-data policy](#missing-data-policy) live without re-running analyze.
7. **Display** — tune colors, sliders, and the Cytoscape layout in real time.

You can change anything in sections 5, 6, and 7 *after* the analysis is done; MLSTudio recomputes the MST on the server in a couple of seconds and re-renders.

---

## 6. Understanding the MST

A minimum spanning tree on allele distances is the workhorse of outbreak investigation: it connects each isolate to its nearest neighbor at the lowest possible total distance, with no cycles and no arbitrary tip ordering. Two key consequences:

- **An MST has exactly N − 1 edges for N isolates** — there is no "right" choice when two isolates are equidistant from a third. MLSTudio uses Kruskal's algorithm with deterministic tie-breaking by sorted node ID.
- **Edge length on screen is proportional to allele distance** (under the fcose layout). Visually close isolates really are genetically close — unlike phylogenies, where branch placement is influenced by the choice of outgroup or rooting.

Three things to read in the picture:

- **Number on the edge** — allele-distance between the two endpoints under the current missing-data policy.
- **Cluster halo** — controlled by the **Cluster halo distance** slider. Soft pastel hulls drawn around any group of isolates that are all reachable via edges of weight ≤ the slider's value. Labeled `Cluster 1, Cluster 2, …` (or by a metadata field if you set **Name clusters by**).
- **Red dashed edges** — only shown when **Show non-tree close connections** is on. These are extra pairs of isolates whose distance is ≤ the cluster threshold but not part of the spanning tree. Useful to see when an outbreak cluster contains "almost-equivalent" alternative MST paths.

### Interactions

| Action | How |
|--------|-----|
| Drag a node | Click and hold. Manually-placed nodes auto-lock (border turns amber). |
| Pan the canvas | Right-click drag |
| Zoom | Scroll wheel |
| Fit everything | **Fit to screen** button |
| Re-run the layout | **Relax layout** (re-randomizes seeds, useful when the auto layout overlaps) |
| Hide a node | Lasso-select → right-click → hide (planned) |

### Identical genotypes collapse

When two or more samples share the exact same allele profile they collapse into a single node sized by member count. With metadata uploaded, that node is rendered as a **pie chart** whose slices represent how the members are distributed across the selected color field — matching the convention used by professional MLST tools (Ridom SeqSphere, BioNumerics).

---

## 7. Reading the results table

The **Comparison Table** tab is one row per sample with:

| Column | Meaning |
|--------|---------|
| Sample | File stem (after suffix stripping) |
| ST | Sequence Type if the allele tuple matches a known profile; appended `*` if any locus was INF |
| EXC | Number of loci called as Exact match |
| INF | Number of loci called Inexact (close but not 100 % identity/coverage) |
| LNF | Number of loci that could not be called (Locus Not Found) |
| Notes | Free-text from the calling step (errors, edge cases) |
| AMR genes | Comma-separated AMR gene symbols (only when AMR scan was enabled) |

The full per-locus matrix is available in `<output_folder>/.mlstudio/alleles.tsv` for schemes with ≤ 200 loci (cgMLST is too wide for the UI table).

---

## 8. Metadata and coloring

A **metadata CSV/TSV** has:

- **First column = sample name** (matching the FASTA stem after suffix stripping).
- One row per sample.
- Any number of additional columns; each becomes a selectable **Color nodes by** option and (for categorical fields) a **Name clusters by** option.

Example:

```csv
sample,ward,collection_date,phenotype,patient_age
isolate_001,ICU-A,2025-09-12,MRSA,67
isolate_002,ICU-A,2025-09-14,MRSA,42
isolate_003,Surgery,2025-09-18,MSSA,55
```

Once uploaded:

- **Color nodes by ward** → all three are colored by which ward they came from.
- **Name clusters by ward** → cluster halos labeled by majority-ward.
- For pie-collapsed nodes (identical genotypes), the pie slices show the composition of the selected color field across the members.

Numeric metadata fields (dates, ages, lab values) are auto-bucketed; categorical fields use a colorblind-safe pastel palette.

---

## 9. The Statistics tab

A compact summary view: number of isolates analyzed, number of distinct STs, MST diameter, cluster counts at the current halo threshold, AMR gene frequency table (when AMR was enabled), and missing-locus distribution. Useful as the first artifact to share when discussing a run.

---

## 10. Projects: save / load

Click **Save project** (top of the sidebar, after a run finishes) to write a `.mlsproj` file: a zip containing the run inputs (folder path, scheme, parameters), the call results, the MST JSON, your metadata CSV, and the current display settings. **Load project** restores the exact view, even on a different machine — assuming the same FASTA folder is reachable.

`.mlsproj` files are plain zips; you can `unzip` one to inspect.

---

## 11. Antimicrobial resistance

Tick **Run AMR gene scan** in section 3 to invoke [AMRFinderPlus](https://www.ncbi.nlm.nih.gov/pathogens/antimicrobial-resistance/AMRFinder/) alongside typing. Results are:

- Stored in `<output_folder>/.mlstudio/amr.tsv` with one row per gene hit per sample.
- Shown in the **Comparison Table** in an extra column.
- **Never contributing to the cgMLST allele-difference distance** — exactly as a clinician would want for outbreak investigation.

The AMRFinderPlus database must be installed. After installing the bioconda package, run once:

```bash
amrfinder -u
```

This downloads ~200 MB into AMRFinderPlus's own cache.

When the scheme's organism is one of the AMRFinderPlus-supported species (S. aureus, E. coli, K. pneumoniae, Listeria, Salmonella, …), MLSTudio passes `--organism` to enable point-mutation calling.

---

## 12. The Sidebar — every option explained

### 1 · Input folder
- **Folder path** — typed or filled by **Browse**.
- **Browse…** — opens an in-app file picker.
- **Scan folder** — counts FASTA files and detects paired reads. No analysis happens yet.

### 2 · Scheme
- **Scheme** dropdown — every locally cached + registry-known scheme.
- First-time selection downloads the scheme into `~/.local/share/mlstudio/schemes/<key>/`.

### 3 · Options
- **Project name** — used when you save the analysis; otherwise the output folder name.
- **Run fastp on FASTQ pairs** — trim adapters + quality-filter Illumina reads. Adds ~1 min per sample. Off by default.
- **Run AMR gene scan** — invoke AMRFinderPlus alongside typing.
- **Skip ST lookup** — just compute distances; useful for cgMLST schemes with no profile table or when you only care about the MST.
- **Threads** — `0` means auto (= half of detected CPU cores).
- **Min identity %** — BLAST percent-identity threshold for a locus call to count as INF or EXC. Auto-defaults: 90 for cgMLST, 95 for MLST.
- **Min coverage %** — fraction of the reference allele that must align. Auto-default 90.
- **Output folder** — where `.mlstudio/` is written. Auto = `<input_folder>/.mlstudio/`.

### 4 · Metadata
- **CSV/TSV upload** — first column is sample, rest become color fields.

### 5 · Distance
- **Missing-data policy** — see [the missing-data policy table](#missing-data-policy) below.

### 6 · Display

| Option | What it controls |
|--------|------------------|
| Color nodes by | Categorical field used to fill node interiors. |
| Name clusters by | Replaces "Cluster 1, 2, …" with majority of the chosen metadata field per cluster. |
| Cluster halo distance | Pastel hulls drawn around isolates connected by edges ≤ this many alleles. |
| Show non-tree close connections | Toggle red dashed edges for close-but-non-MST pairs. |
| Edge label threshold | Hide weight labels on edges above this distance — declutter for noisy datasets. |
| Show sample labels | Toggle per-node text labels. |
| Layout algorithm | `fcose` (default, force-directed with edge-weight ideal lengths) · `radial tree` (deterministic, crossing-free) · `cose` (older but simpler force layout). |
| Layout iterations | Force-directed layouts (`fcose`, `cose`) refine for this many steps. More = better packing, slower. |
| Node size scale | Multiplier on auto-tuned node radius (0.5×–2×). |
| Label font scale | Multiplier on auto-tuned label size (0.5×–2×). |
| Relax layout | Re-run the layout with a fresh random seed. |
| Re-render | Apply size/label/layout changes without re-running the layout. |
| Fit to screen | Re-fit the viewport to all visible nodes. |
| Export PNG | High-DPI raster export, white background. |
| Export SVG | Vector export suitable for publication. |

### 7 · Scheme catalog
- Live list of cached schemes with a **Pull** button per row for un-cached ones.
- **Browse all PubMLST schemes…** opens a search modal of every BIGSdb / PubMLST / cgMLST.org scheme.

### Missing-data policy

| Policy | Behavior | Best for |
|--------|----------|----------|
| **Pairwise complete** (default) | Only loci called in both isolates contribute. | Standard for cgMLST. |
| **Missing as own category** | A missing call equals other missing calls; missing-vs-called is a difference. | 7-gene MLST and small schemes. |
| **Count missing as differences** | Every missing locus = 1 difference. | Conservative; isolates with poor calling get pushed apart. |
| **Pairwise complete, scaled** | Pairwise-complete distance scaled to full locus count: d × n_loci / n_shared. | Mixing assemblies of variable quality where the overlap differs across pairs. |

---

## 13. Command-line reference

```bash
mlstudio --help                       # global help
mlstudio --version
mlstudio gui [folder]                 # launch the web UI
mlstudio schemes list                 # cached schemes
mlstudio schemes list --remote        # everything in the registry
mlstudio schemes pull <key>           # download one scheme
mlstudio schemes pull-eskapee         # download all 7 ESKAPEE cgMLST schemes
mlstudio schemes build-adhoc \
    --reference path/to/ref.fasta \
    --key mygenus_v1 \
    --organism "My genus species"
mlstudio call mlst   --scheme <key> --input genome.fasta
mlstudio call cgmlst --scheme <key> --input genome.fasta [--threads 24] \
                     [--min-identity 90] [--min-coverage 90]
```

All commands accept `-v` / `--verbose` for debug logging.

---

## 14. Building an ad-hoc cgMLST scheme

No public scheme for your organism of interest? Build one from a single annotated reference:

```bash
mlstudio schemes build-adhoc \
    --reference path/to/reference.fasta \
    --key mygenus_v1 \
    --organism "My genus species" \
    --threads 16 \
    --min-length 200 \
    --cluster-threshold 5
```

This:

1. Runs **prodigal** in single-genome mode to predict coding sequences.
2. Self-BLASTs the CDS set against itself to drop **paralogs** (loci with ≥ 1 hit to another locus at 80 %+ identity).
3. Length-filters to drop fragmented CDS shorter than `--min-length`.
4. Writes a complete scheme directory under `~/.local/share/mlstudio/schemes/<key>/` with one FASTA per locus, a manifest, and an empty profiles table.

The scheme is immediately usable:

```bash
mlstudio call cgmlst --scheme mygenus_v1 --input my_genome.fasta
```

and appears in the GUI scheme dropdown.

---

## 15. Caches and file locations

| What | Where | Override |
|------|-------|----------|
| Scheme allele FASTAs + manifests | `~/.local/share/mlstudio/schemes/<key>/` | `MLSTUDIO_CACHE_DIR` |
| Per-analysis output | `<input_folder>/.mlstudio/` | `--output-folder` flag / UI option |
| Per-sample call cache (incremental) | `<output_folder>/calls/` | (same as above) |
| BLAST DBs for cgMLST schemes | `<scheme_root>/blast_db/batch_*.fasta.{nhr,nin,nsq}` | rebuilt automatically if scheme manifest changes |

The scheme cache is **shareable across users** — point `MLSTUDIO_CACHE_DIR` at a shared filesystem path for a multi-user lab setup. The per-analysis cache (`.mlstudio/`) is per-folder and per-user.

---

## 16. Troubleshooting

### `blastn: command not found`
You're not in the `mlstudio` conda env, or the env wasn't built from `setup.sh`. Activate with `conda activate mlstudio` or install BLAST: `conda install -c bioconda blast`.

### `pull-eskapee` times out
Use the offline tarball — see the README "Offline / manual scheme install" section.

### cgMLST analysis uses too much RAM
The cgMLST batched-BLAST design caps memory at ~200 MB per BLAST call (regardless of scheme size). If you still hit OOM, lower the **Threads** option in section 3 — each thread runs its own BLAST.

### MST shows nodes piled on top of each other
The auto layout occasionally lands a local minimum. Click **Relax layout** to re-seed. If it keeps happening, switch the **Layout algorithm** to `radial tree` for a deterministic crossing-free layout.

### "Unknown scheme key" in the UI dropdown
The scheme is in the registry but not cached. Click **Pull** next to it in section 7, or `mlstudio schemes pull <key>` from the CLI.

### AMR scan says "amrfinder not found"
Either install the tool: `conda install -c bioconda ncbi-amrfinderplus && amrfinder -u`, or untick **Run AMR gene scan**.

### Browser shows a blank page
- Check the terminal for a startup error.
- Try a different port: the launcher picks one automatically but you can override with `MLSTUDIO_PORT=9999 mlstudio gui`.
- Check that `localhost` resolves (`ping 127.0.0.1`).

### Old project file won't open
`.mlsproj` files include the scheme key and the absolute paths of the FASTA inputs. If you moved either, edit the embedded `project.json` (it's just a zip) and re-save.

---

## 17. FAQ

**Can MLSTudio do phylogenetic trees (ML, Bayesian)?**
Not yet. MSTs are the v1 target; ML / NJ / Bayesian trees are on the wishlist. For now, MLSTudio writes a `mst.json` you can re-render in other tools.

**Can it handle long reads (Nanopore / PacBio HiFi)?**
Indirectly — assemble first (`flye`, `unicycler`), then point MLSTudio at the contigs. There's no integrated assembler by design.

**Does it run on macOS or Windows?**
Linux-first; the Python code itself is portable but the bundled bioconda env is Linux-only. PRs to support macOS are welcome.

**Can two users share a scheme cache?**
Yes — set `MLSTUDIO_CACHE_DIR` to a shared directory. The cache is read-only after the first download; concurrent reads are safe.

**How is this different from chewBBACA?**
chewBBACA is a powerful cgMLST allele-caller with no GUI; MLSTudio integrates scheme management + calling + interactive MST + AMR in one tool. Use chewBBACA when you need its specific calling features; use MLSTudio when you want the full clinical workflow without stitching tools together.

**How is this different from GrapeTree?**
GrapeTree is a brilliant MST/NJ viewer that takes profiles as input — it does no calling. MLSTudio is the calling + viewing combination.

**Is the recipe on bioconda yet?**
Recipe is staged in `recipe/meta.yaml`. The bioconda PR is opened against [bioconda-recipes](https://github.com/bioconda/bioconda-recipes) once the first PyPI sdist is published.

**Where do I report bugs?**
[GitHub issues](https://github.com/iowa69/mlstudio/issues). Please include `mlstudio --version`, your conda env list, and the relevant part of the terminal log.
