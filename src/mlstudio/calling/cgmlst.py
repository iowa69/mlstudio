"""Core-genome MLST calling with optional Bowtie2 rescue.

Pipeline per isolate:
    1. BLAST assembly vs scheme allele DB (multicore).
    2. Classify each locus call:
         EXC  — exact match
         INF  — inferred new allele (>= identity & coverage thresholds, novel sequence)
         ASM  — allele spanning contig break
         LNF  — locus not found
         PLOT3 / PLOT5  — partial at contig end
    3. For LNF / ASM / partial loci, if reads are provided, run Bowtie2 rescue:
         a. Build per-locus bowtie2 index from the scheme alleles.
         b. Map reads, call consensus, compare to alleles.
         c. Upgrade to EXC / INF where supported, else keep as missing with evidence.

Stub — M3.
"""

from __future__ import annotations

from pathlib import Path


def call_cgmlst(
    assembly: Path,
    scheme_dir: Path,
    reads: Path | None = None,
    threads: int = 0,
    rescue: bool = True,
) -> dict[str, object]:
    raise NotImplementedError("M3 — cgMLST calling not yet implemented.")
