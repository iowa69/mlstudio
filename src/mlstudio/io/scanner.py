"""Scan a folder for FASTA assemblies and matching paired-end FASTQs.

Pairing rules — sample name is the FASTA stem (stripped of common suffixes); a
FASTQ pair is matched if its basename starts with that sample name and contains
an R1/R2 marker.

Supported FASTQ pair markers (case-insensitive):
    _R1_ / _R2_
    _R1. / _R2.
    _1.   / _2.
    .R1.  / .R2.
    .1.fq / .2.fq

Files must be gzipped or plain; both members of the pair must use the same
extension.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

FASTA_EXTS = (".fasta", ".fa", ".fna", ".fasta.gz", ".fa.gz", ".fna.gz")
FASTQ_EXTS = (".fastq", ".fq", ".fastq.gz", ".fq.gz")

# Strip these suffixes from FASTA stems before pairing
FASTA_STEM_STRIP = (".contigs", ".scaffolds", ".assembly", ".genomic", ".asm")

PAIR_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"_R1(?=[._])"), "_R2"),
    (re.compile(r"_1(?=\.f(?:ast)?q)"), "_2"),
    (re.compile(r"\.R1(?=[._])"), ".R2"),
    (re.compile(r"\.1(?=\.f(?:ast)?q)"), ".2"),
)


@dataclass(slots=True)
class Sample:
    name: str
    assembly: Path
    r1: Path | None = None
    r2: Path | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def has_reads(self) -> bool:
        return self.r1 is not None and self.r2 is not None


def _strip_fasta_ext(name: str) -> str:
    lower = name.lower()
    for ext in sorted(FASTA_EXTS, key=len, reverse=True):
        if lower.endswith(ext):
            return name[: -len(ext)]
    return name


def _normalize_sample_name(stem: str) -> str:
    """Strip common assembly-pipeline suffixes from the FASTA stem."""
    lower = stem.lower()
    for sfx in FASTA_STEM_STRIP:
        if lower.endswith(sfx):
            return stem[: -len(sfx)]
    return stem


def _find_r1(folder: Path, sample: str) -> tuple[Path, Path] | None:
    """Locate paired-end FASTQs whose names start with `sample`."""
    candidates = [
        p for p in folder.iterdir()
        if p.is_file() and any(p.name.lower().endswith(e) for e in FASTQ_EXTS)
    ]
    for c in candidates:
        if not c.name.startswith(sample):
            continue
        for r1_re, r2_token in PAIR_PATTERNS:
            if r1_re.search(c.name):
                r2_name = r1_re.sub(r2_token, c.name, count=1)
                r2 = c.with_name(r2_name)
                if r2.exists():
                    return c, r2
    return None


def scan(folder: Path) -> list[Sample]:
    """Return all samples discoverable in `folder`.

    A sample is an assembly FASTA. If matching paired-end FASTQs are present in
    the same folder, they are attached.
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise NotADirectoryError(folder)

    samples: list[Sample] = []
    for fasta in sorted(folder.iterdir()):
        if not fasta.is_file():
            continue
        if not any(fasta.name.lower().endswith(e) for e in FASTA_EXTS):
            continue

        stem = _strip_fasta_ext(fasta.name)
        sample_name = _normalize_sample_name(stem)

        sample = Sample(name=sample_name, assembly=fasta)

        pair = _find_r1(folder, sample_name)
        if pair:
            sample.r1, sample.r2 = pair
        else:
            sample.notes.append("no matching paired-end FASTQs found")

        samples.append(sample)

    return samples
