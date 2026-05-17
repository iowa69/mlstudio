"""Classical MLST calling (e.g. 7-gene schemes).

Pipeline per isolate:
    1. BLAST the assembly against the scheme's allele database.
    2. For each locus, take the best hit (identity + coverage thresholds).
    3. Look up the allele-combination in the ST profiles table.

Stub — M2.
"""

from __future__ import annotations

from pathlib import Path


def call_mlst(
    assembly: Path,
    scheme_dir: Path,
    threads: int = 0,
) -> dict[str, object]:
    raise NotImplementedError("M2 — MLST calling not yet implemented.")
