"""AMRFinderPlus wrapper.

Runs `amrfinder` on an assembly and parses the TSV output. Results are *displayed*
alongside typing but never contribute to the cgMLST allele-difference distance.

Configurable points:
    - organism (e.g. "Staphylococcus_aureus") for organism-specific point mutations
    - threads
    - database location (default: AMRFinderPlus's bundled `--update` path)
"""

from __future__ import annotations

import csv
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Map our scheme registry keys to AMRFinderPlus --organism values
ORGANISM_MAP: dict[str, str] = {
    "saureus": "Staphylococcus_aureus",
    "lmonocytogenes": "Listeria",
    "ecoli": "Escherichia",
    "kpneumoniae": "Klebsiella_pneumoniae",
}


@dataclass(slots=True)
class AmrHit:
    gene_symbol: str
    sequence_name: str
    scope: str          # "core" | "plus"
    element_type: str   # "AMR" | "STRESS" | "VIRULENCE"
    element_subtype: str
    class_: str
    subclass: str
    method: str         # "EXACTX" | "BLASTX" | "PARTIALX" etc.
    percent_identity: float
    percent_coverage: float


@dataclass(slots=True)
class AmrResult:
    sample: str
    organism: str | None
    hits: list[AmrHit] = field(default_factory=list)
    error: str | None = None

    @property
    def gene_symbols(self) -> list[str]:
        return sorted({h.gene_symbol for h in self.hits})


def amrfinder_available() -> bool:
    return shutil.which("amrfinder") is not None


def run_amrfinderplus(
    assembly: Path,
    organism: str | None = None,
    threads: int = 4,
    db_path: Path | None = None,
) -> AmrResult:
    """Run amrfinder on a nucleotide assembly. Returns parsed hits."""
    sample = assembly.stem.replace(".fna", "").replace(".fasta", "").replace(".fa", "")

    if not amrfinder_available():
        return AmrResult(sample=sample, organism=organism,
                         error="amrfinder binary not found in PATH")

    cmd = ["amrfinder", "-n", str(assembly), "--threads", str(threads)]
    if organism:
        cmd += ["--organism", organism]
    if db_path:
        cmd += ["-d", str(db_path)]

    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        return AmrResult(sample=sample, organism=organism,
                         error=f"amrfinder failed: {e.stderr[-200:]}")

    hits: list[AmrHit] = []
    reader = csv.DictReader(proc.stdout.splitlines(), dialect="excel-tab")
    for row in reader:
        try:
            hits.append(AmrHit(
                gene_symbol=row.get("Element symbol") or row.get("Gene symbol", ""),
                sequence_name=row.get("Element name") or row.get("Sequence name", ""),
                scope=row.get("Scope", ""),
                element_type=row.get("Type", "") or row.get("Element type", ""),
                element_subtype=row.get("Subtype", "") or row.get("Element subtype", ""),
                class_=row.get("Class", ""),
                subclass=row.get("Subclass", ""),
                method=row.get("Method", ""),
                percent_identity=float(row.get("% Identity to reference") or row.get("% Identity to reference sequence") or 0),
                percent_coverage=float(row.get("% Coverage of reference") or row.get("% Coverage of reference sequence") or 0),
            ))
        except (ValueError, KeyError) as e:
            log.debug("Skipping AMR row (parse): %s", e)
    return AmrResult(sample=sample, organism=organism, hits=hits)
