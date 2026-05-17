#!/usr/bin/env bash
# Download the demo dataset used by the end-to-end tests.
#
# Three Listeria monocytogenes reference genomes (one with simulated Illumina
# PE reads). Total download ~9 MB after gzip.
#
#   EGD-e   (ST 35, CC9, lineage II)   — assembly + simulated R1/R2 FASTQs
#   10403S  (ST 85, CC7, lineage II)   — assembly only
#   F2365   (ST 1,  CC1, lineage I)    — assembly only

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/demo_folder"
mkdir -p "$DIR"

fetch() {
    local url="$1" dest="$2"
    if [[ -f "$dest" ]]; then
        echo "  exists  $dest"; return
    fi
    echo "  fetch   $url"
    curl -sSL "$url" -o "${dest}.gz"
    gunzip -f "${dest}.gz"
}

echo "[fetch_demo] Downloading Listeria reference genomes…"
fetch "https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/196/035/GCF_000196035.1_ASM19603v1/GCF_000196035.1_ASM19603v1_genomic.fna.gz" "$DIR/EGD-e.fasta"
fetch "https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/168/695/GCF_000168695.2_ASM16869v2/GCF_000168695.2_ASM16869v2_genomic.fna.gz" "$DIR/10403S.fasta"
fetch "https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/008/285/GCF_000008285.1_ASM828v1/GCF_000008285.1_ASM828v1_genomic.fna.gz" "$DIR/F2365.fasta"

if [[ ! -f "$DIR/EGD-e_R1.fastq.gz" ]]; then
    echo "[fetch_demo] Simulating Illumina PE reads from EGD-e (50k pairs, 150bp)…"
    if ! command -v wgsim >/dev/null; then
        echo "[fetch_demo] ERROR: 'wgsim' not found. Run setup.sh first or 'conda activate mlstudio'." >&2
        exit 1
    fi
    wgsim -1 150 -2 150 -N 50000 -e 0.005 -r 0.001 -d 350 -S 42 \
        "$DIR/EGD-e.fasta" "$DIR/EGD-e_R1.fastq" "$DIR/EGD-e_R2.fastq" >/dev/null
    gzip -f "$DIR/EGD-e_R1.fastq" "$DIR/EGD-e_R2.fastq"
fi

echo "[fetch_demo] Done. Files:"
ls -lh "$DIR"
