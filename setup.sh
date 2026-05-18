#!/usr/bin/env bash
# MLSTudio one-shot setup.
# Creates the `mlstudio` conda environment with all bio + Python deps, then
# installs the Python package in editable mode.

set -euo pipefail

ENV_NAME="mlstudio"
PY_VERSION="3.11"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { printf "\033[1;36m[setup]\033[0m %s\n" "$*"; }
die() { printf "\033[1;31m[setup]\033[0m %s\n" "$*" >&2; exit 1; }

# --- Sanity checks ---------------------------------------------------------

command -v conda >/dev/null 2>&1 || die "conda not found. Install Miniconda or Anaconda first."

# shellcheck disable=SC1091
source "$(conda info --base)/etc/profile.d/conda.sh"

if [[ "$(uname)" != "Linux" ]]; then
    die "MLSTudio is Linux-only."
fi

# --- Create env ------------------------------------------------------------

if conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
    log "Conda env '$ENV_NAME' already exists — reusing."
else
    log "Creating conda env '$ENV_NAME' (this can take a few minutes)..."
    conda create -y -n "$ENV_NAME" \
        -c conda-forge -c bioconda \
        "python=${PY_VERSION}" \
        "blast>=2.15" \
        "bowtie2>=2.5" \
        "samtools>=1.20" \
        "fastp>=0.23" \
        "seqkit>=2.8" \
        "prodigal>=2.6" \
        "ncbi-amrfinderplus>=3.12" \
        "minimap2>=2.28" \
        pip
fi

# --- Install Python package -----------------------------------------------

log "Installing mlstudio Python package..."
conda run -n "$ENV_NAME" pip install --upgrade pip
conda run -n "$ENV_NAME" pip install -e "${REPO_DIR}[dev]"

# --- Verify ----------------------------------------------------------------

log "Verifying tools..."
for tool in blastn makeblastdb bowtie2 samtools fastp seqkit amrfinder; do
    if ! conda run -n "$ENV_NAME" which "$tool" >/dev/null; then
        die "Tool '$tool' missing from env after install."
    fi
done

conda run -n "$ENV_NAME" mlstudio --version

# --- Bundled data: AMRFinderPlus database + ESKAPEE schemes ---------------
# Optional but recommended — this is what makes a fresh git clone usable
# clinically out-of-the-box. Pass --no-data to skip if you only want the
# code installed.
if [[ "${1:-}" != "--no-data" ]]; then
    log "Downloading AMRFinderPlus database (~250 MB, one-time)..."
    conda run -n "$ENV_NAME" amrfinder -u || \
        log "  (amrfinder DB download failed — re-run 'amrfinder -u' later)"

    log "Pulling ESKAPEE cgMLST schemes from cgMLST.org (~1–2 GB, one-time)..."
    conda run -n "$ENV_NAME" mlstudio schemes pull-eskapee || \
        log "  (ESKAPEE pull failed — re-run 'mlstudio schemes pull-eskapee')"

    log "Pulling companion classical MLST schemes from PubMLST + Pasteur..."
    for s in efaecium_mlst saureus_mlst kpneumoniae_mlst ecoli_mlst \
             lmonocytogenes_mlst abaumannii_mlst paeruginosa_mlst; do
        conda run -n "$ENV_NAME" mlstudio schemes pull "$s" 2>/dev/null \
            && log "  ✓ $s" \
            || log "  ✗ $s (skipped; pull later)"
    done
fi

log "Done."
log "  Activate with: conda activate $ENV_NAME"
log "  Launch GUI:    mlstudio gui /path/to/folder"
log "  Or skip schemes/AMR DB next time with: ./setup.sh --no-data"
