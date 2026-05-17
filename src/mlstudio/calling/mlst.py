"""Classical MLST calling via BLAST.

Per isolate:
    1. Make (cached) BLAST database for each scheme locus.
    2. BLAST the assembly contigs against each per-locus DB.
    3. Best-hit allele = highest bitscore among hits with identity & coverage
       above thresholds.
    4. Look up the resulting allele-combination in the ST profile table.

Output: AlleleCall objects per locus + an overall ST string for the isolate.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from mlstudio.schemes import Scheme

log = logging.getLogger(__name__)

DEFAULT_IDENTITY = 95.0
DEFAULT_COVERAGE = 90.0


@dataclass(slots=True)
class AlleleCall:
    locus: str
    allele: str | None        # e.g. "3" for exact, "3?" for inexact, None for not found
    identity: float
    coverage: float
    bitscore: float
    flag: str                  # EXC, NIPHEM, NIPH, ASM, LNF, INF

    @property
    def is_exact(self) -> bool:
        return self.flag == "EXC"


@dataclass(slots=True)
class MLSTResult:
    sample: str
    scheme: str
    st: str | None             # e.g. "1" or "1*" (inexact) or None
    calls: dict[str, AlleleCall] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def allele_vector(self, loci_order: list[str]) -> list[str | None]:
        """Return allele numbers in scheme order; None for missing."""
        out: list[str | None] = []
        for loc in loci_order:
            call = self.calls.get(loc)
            if call is None or call.allele is None:
                out.append(None)
            else:
                # strip any trailing '?' or '~' suffix
                out.append(re.sub(r"[^\d]", "", call.allele) or None)
        return out


def make_blastdb(fasta: Path, db_root: Path) -> Path:
    """Build a BLAST nucl DB for `fasta` under `db_root/<stem>/db`."""
    db_root.mkdir(parents=True, exist_ok=True)
    out_prefix = db_root / fasta.stem
    if (out_prefix.with_suffix(".nhr").exists()):
        return out_prefix
    cmd = [
        "makeblastdb", "-in", str(fasta), "-dbtype", "nucl", "-out", str(out_prefix),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_prefix


def _build_locus_dbs(scheme: Scheme, db_root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for locus in scheme.loci:
        out[locus] = make_blastdb(scheme.locus_fasta(locus), db_root)
    return out


def _blast_locus(assembly: Path, locus_db: Path, threads: int = 1) -> list[dict[str, float]]:
    """BLAST assembly against a per-locus DB; return parsed hits."""
    fmt = "6 qseqid sseqid pident length mismatch gapopen qstart qend sstart send evalue bitscore qlen slen"
    cmd = [
        "blastn",
        "-query", str(assembly),
        "-db", str(locus_db),
        "-outfmt", fmt,
        "-max_target_seqs", "10",
        "-num_threads", str(threads),
    ]
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    hits = []
    for line in proc.stdout.strip().splitlines():
        cols = line.split("\t")
        if len(cols) < 14:
            continue
        hits.append({
            "qseqid": cols[0], "sseqid": cols[1],
            "pident": float(cols[2]),
            "length": int(cols[3]),
            "bitscore": float(cols[11]),
            "slen": int(cols[13]),
        })
    return hits


def _best_hit(hits: list[dict], locus: str,
              min_id: float, min_cov: float) -> AlleleCall:
    if not hits:
        return AlleleCall(locus=locus, allele=None,
                          identity=0, coverage=0, bitscore=0, flag="LNF")
    hits.sort(key=lambda h: h["bitscore"], reverse=True)
    top = hits[0]
    cov = 100.0 * top["length"] / top["slen"]
    allele_id = top["sseqid"].rsplit("_", 1)[-1]
    if top["pident"] >= min_id and cov >= min_cov:
        if top["pident"] >= 99.999 and cov >= 99.999:
            flag = "EXC"
            allele = allele_id
        else:
            flag = "INF"
            allele = f"{allele_id}~"
    else:
        flag = "LNF"
        allele = None
    return AlleleCall(locus=locus, allele=allele,
                      identity=top["pident"], coverage=cov,
                      bitscore=top["bitscore"], flag=flag)


def _load_profile_table(scheme: Scheme) -> tuple[list[str], dict[tuple[str, ...], str]]:
    """Parse profiles.tsv; return (locus_order, {tuple_of_alleles: ST})."""
    if scheme.profile_table is None or not scheme.profile_table.exists():
        raise FileNotFoundError(f"No profile table for scheme {scheme.name}")

    lines = scheme.profile_table.read_text().splitlines()
    header = lines[0].split("\t")
    st_idx = header.index("ST")
    # Use the locus order from the scheme manifest (matches BIGSdb order)
    loc_idx = [header.index(loc) for loc in scheme.loci]

    table: dict[tuple[str, ...], str] = {}
    for line in lines[1:]:
        if not line.strip():
            continue
        cols = line.split("\t")
        key = tuple(cols[i] for i in loc_idx)
        table[key] = cols[st_idx]
    return scheme.loci, table


def call_mlst(
    assembly: Path,
    scheme: Scheme,
    db_root: Path | None = None,
    threads: int = 0,
    min_identity: float = DEFAULT_IDENTITY,
    min_coverage: float = DEFAULT_COVERAGE,
) -> MLSTResult:
    """Run MLST calling for one assembly against one scheme."""
    if threads == 0:
        threads = max(1, mp.cpu_count() // 2)

    db_root = db_root or (scheme.root / "blast_db")
    locus_dbs = _build_locus_dbs(scheme, db_root)
    loci_order, profile_lookup = _load_profile_table(scheme)

    sample = assembly.stem.replace(".fna", "").replace(".fasta", "").replace(".fa", "")
    result = MLSTResult(sample=sample, scheme=scheme.name, st=None)

    per_locus_threads = max(1, threads // max(1, len(scheme.loci)))
    for locus in scheme.loci:
        hits = _blast_locus(assembly, locus_dbs[locus], threads=per_locus_threads)
        result.calls[locus] = _best_hit(hits, locus, min_identity, min_coverage)

    allele_tuple = tuple(
        result.calls[loc].allele.rstrip("~") if result.calls[loc].allele else "0"
        for loc in loci_order
    )
    exact_only = tuple(a for a in allele_tuple)
    if "0" in exact_only:
        result.st = None
        result.notes.append("missing allele(s) — no ST assigned")
    else:
        st = profile_lookup.get(exact_only)
        if st is None:
            result.st = None
            result.notes.append("novel allele combination — no matching ST")
        else:
            any_inexact = any(c.flag == "INF" for c in result.calls.values())
            result.st = f"{st}*" if any_inexact else st
    return result
