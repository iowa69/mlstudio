#!/usr/bin/env bash
# Produce an offline-installable bundle of the 7 ESKAPEE cgMLST schemes.
#
# Run this once on a machine with good network → `mlstudio schemes pull-eskapee`
# populates the local scheme cache → this script tars the cache into a single
# archive that can be uploaded as a GitHub release asset. End users on
# air-gapped or slow-network systems can then untar that archive in place of
# running the live downloader.
#
# Usage:
#   scripts/bundle_eskapee_schemes.sh [output.tar.zst]
#
# Default output: ./mlstudio-eskapee-schemes-<version>.tar.zst
set -euo pipefail

CACHE_DIR="${MLSTUDIO_CACHE_DIR:-$HOME/.local/share/mlstudio/schemes}"
VERSION="$(python3 -c 'import mlstudio; print(mlstudio.__version__)' 2>/dev/null || echo dev)"
OUT="${1:-mlstudio-eskapee-schemes-${VERSION}.tar.zst}"

ESKAPEE_KEYS=(
    "efaecium_cgmlst_orgio"
    "saureus_cgmlst_orgio"
    "kpneumoniae_complex_cgmlst_orgio"
    "abaumannii_cgmlst_orgio"
    "paeruginosa_cgmlst_orgio"
    "ehormaechei_cgmlst_orgio"
    "ecoli_cgmlst_orgio"
)

if [[ ! -d "$CACHE_DIR" ]]; then
    echo "✗ Scheme cache not found at $CACHE_DIR" >&2
    echo "  Run \`mlstudio schemes pull-eskapee\` first." >&2
    exit 1
fi

missing=()
for key in "${ESKAPEE_KEYS[@]}"; do
    [[ -d "$CACHE_DIR/$key" ]] || missing+=("$key")
done
if (( ${#missing[@]} )); then
    echo "✗ Missing schemes (run pull-eskapee first):" >&2
    printf '   • %s\n' "${missing[@]}" >&2
    exit 1
fi

echo "→ Bundling ${#ESKAPEE_KEYS[@]} schemes from $CACHE_DIR"
tar --use-compress-program='zstd -19 -T0' \
    -cf "$OUT" \
    -C "$CACHE_DIR" \
    "${ESKAPEE_KEYS[@]}"

size_mb=$(( $(stat -c%s "$OUT") / 1024 / 1024 ))
echo "✓ Wrote $OUT (${size_mb} MB)"
echo
echo "Next: attach $OUT to a GitHub release on iowa69/mlstudio."
echo "End users install with:"
echo "  mkdir -p ~/.local/share/mlstudio/schemes"
echo "  tar --use-compress-program=unzstd -xf $OUT -C ~/.local/share/mlstudio/schemes"
