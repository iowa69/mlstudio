"""Lightweight per-assembly QC.

One-pass FASTA scan computing the metrics that gate a clinical-grade
cgMLST call: total assembly length, N50, number of contigs, longest
contig, GC%, and the fraction of ambiguous bases (N's). The result is
combined with a per-organism expected-size band into a simple
PASS / WARN / FAIL verdict so the user can see at a glance which
isolates can be trusted.

This is intentionally pure Python (no Biopython) so it's fast on large
batches and adds zero new runtime deps.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class AssemblyQC:
    sample: str
    n_contigs: int = 0
    total_length: int = 0
    longest_contig: int = 0
    n50: int = 0
    n90: int = 0
    gc_percent: float = 0.0
    n_count: int = 0          # ambiguous bases ("N")
    n_fraction: float = 0.0
    verdict: str = "PASS"     # PASS / WARN / FAIL
    reasons: list[str] | None = None


# Expected genome size (bp) and tolerance per organism family. Pulled
# from refseq median + 1.5× sigma; numbers chosen wide enough that real
# strains pass but a Saureus assembly accidentally pointed at a Listeria
# scheme is flagged immediately.
EXPECTED_GENOME_SIZE: dict[str, tuple[int, int]] = {
    "Saureus":             (2_600_000, 3_200_000),
    "Sargenteus":          (2_600_000, 3_200_000),
    "Scapitis":            (2_400_000, 3_000_000),
    "Spyogenes":           (1_700_000, 2_100_000),
    "Spneumoniae":         (1_900_000, 2_300_000),
    "Lmonocytogenes":      (2_700_000, 3_200_000),
    "Kpneumoniae_complex": (5_000_000, 6_500_000),
    "Koxytoca_complex":    (5_500_000, 7_000_000),
    "Ecoli":               (4_500_000, 6_000_000),
    "Senterica":           (4_400_000, 5_500_000),
    "Efaecium":            (2_500_000, 3_300_000),
    "Efaecalis":           (2_700_000, 3_400_000),
    "Abaumannii":          (3_700_000, 4_400_000),
    "Paeruginosa":         (6_000_000, 7_500_000),
    "Cdifficile":          (3_900_000, 4_800_000),
    "Cjejuni_complex":     (1_500_000, 2_000_000),
}


def assembly_qc(assembly: Path, organism_slug: str | None = None) -> AssemblyQC:
    """One-pass FASTA QC."""
    sample = assembly.stem.replace(".fna", "").replace(".fasta", "").replace(".fa", "")
    qc = AssemblyQC(sample=sample, reasons=[])

    contigs: list[int] = []
    cur_len = 0
    gc = 0
    n_n = 0
    total = 0
    base_count = 0

    with assembly.open("rb") as fh:
        for line in fh:
            if line.startswith(b">"):
                if cur_len:
                    contigs.append(cur_len)
                cur_len = 0
                continue
            seq = line.strip().upper()
            cur_len += len(seq)
            total += len(seq)
            # Branchless-ish counting: rely on bytes.count() per call (fast
            # in C). The four loops are still O(N) but in tight C code.
            gc += seq.count(b"G") + seq.count(b"C")
            n_n += seq.count(b"N")
            base_count += len(seq) - seq.count(b"-")
    if cur_len:
        contigs.append(cur_len)

    if not contigs:
        qc.verdict = "FAIL"
        qc.reasons = ["empty FASTA"]
        return qc

    contigs.sort(reverse=True)
    qc.n_contigs = len(contigs)
    qc.total_length = total
    qc.longest_contig = contigs[0]
    qc.gc_percent = 100.0 * gc / max(1, base_count)
    qc.n_count = n_n
    qc.n_fraction = n_n / max(1, total)

    # N50 / N90: shortest contig length such that ≥X% of total bases come
    # from contigs that long or longer.
    def _nx(pct: float) -> int:
        cutoff = total * pct
        acc = 0
        for c in contigs:
            acc += c
            if acc >= cutoff:
                return c
        return contigs[-1]

    qc.n50 = _nx(0.50)
    qc.n90 = _nx(0.90)

    # Verdict logic. Generic thresholds first; organism-specific size band
    # only checks if we know the organism.
    reasons: list[str] = []
    if qc.n_contigs > 500:
        reasons.append(f"{qc.n_contigs} contigs (very fragmented)")
    if qc.n50 < 5_000:
        reasons.append(f"N50 {qc.n50} bp < 5 kb")
    if qc.n_fraction > 0.01:
        reasons.append(f"{qc.n_fraction:.1%} ambiguous (N) bases")
    if organism_slug and organism_slug in EXPECTED_GENOME_SIZE:
        lo, hi = EXPECTED_GENOME_SIZE[organism_slug]
        if total < lo * 0.8:
            reasons.append(f"assembly {total:,} bp is below expected ~{lo:,}-{hi:,}")
        elif total > hi * 1.3:
            reasons.append(f"assembly {total:,} bp is above expected ~{lo:,}-{hi:,}")

    # FAIL on size mismatch or extreme fragmentation; WARN on softer issues.
    if any("below expected" in r or "above expected" in r or "very fragmented" in r
           for r in reasons):
        qc.verdict = "FAIL"
    elif reasons:
        qc.verdict = "WARN"
    else:
        qc.verdict = "PASS"
    qc.reasons = reasons
    return qc
