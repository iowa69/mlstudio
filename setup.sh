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
        pip
fi

# --- Install Python package -----------------------------------------------

log "Installing mlstudio Python package..."
conda run -n "$ENV_NAME" pip install --upgrade pip
conda run -n "$ENV_NAME" pip install -e "${REPO_DIR}[dev]"

# --- Verify ----------------------------------------------------------------

log "Verifying tools..."
for tool in blastn makeblastdb bowtie2 samtools fastp seqkit; do
    if ! conda run -n "$ENV_NAME" which "$tool" >/dev/null; then
        die "Tool '$tool' missing from env after install."
    fi
done

conda run -n "$ENV_NAME" mlstudio --version

log "Done. Activate with: conda activate $ENV_NAME"
log "Launch the GUI with:  mlstudio gui /path/to/folder"
