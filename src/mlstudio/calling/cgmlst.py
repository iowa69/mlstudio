"""Core-genome MLST calling via a single concatenated BLAST database.

For large schemes (1000+ loci) running one BLAST per locus is prohibitive. Instead
we concatenate every allele FASTA into one DB whose subject IDs are
`<locus>__<allele>`, then run one `blastn` per genome and group the hits by locus.

Per locus we keep the highest-bitscore hit that meets identity + coverage cutoffs.
An exact match yields the allele number; an inexact pass yields `<allele>~` (INF).
LNF = locus not found in the assembly above thresholds.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from mlstudio.calling.mlst import AlleleCall, MLSTResult, make_blastdb
from mlstudio.schemes import Scheme

log = logging.getLogger(__name__)

# Default thresholds — chewBBACA-style permissive defaults. Subject coverage
# is intentionally low (80%) because cgMLST loci frequently land at contig
# boundaries in fragmented assemblies, so a fully-conserved allele with the
# tail truncated by the contig break should still be called (it'll be flagged
# INF if identity slips below 100%). 90/90 was too strict and caused 95%+
# LNF on real-world data; this matches the chewBBACA / pyMLST consensus.
DEFAULT_IDENTITY = 90.0
DEFAULT_COVERAGE = 80.0
LOCUS_SEP = "__"
# Loci per BLAST DB batch. Big enough that the per-batch BLAST call has
# meaningful work; small enough that -max_target_seqs comfortably covers
# 1 hit per locus × ~50 alleles avg per locus = 5000 hits per batch.
BATCH_SIZE = 100
MAX_HITS_PER_BATCH = 8000


def _write_locus_batch(scheme: Scheme, batch_loci: list[str], fa_path: Path) -> None:
    """Concatenate a chunk of locus FASTAs with rewritten headers (locus__allele)."""
    with fa_path.open("w") as out:
        for locus in batch_loci:
            fa = scheme.locus_fasta(locus)
            if not fa.exists():
                continue
            for line in fa.read_text().splitlines():
                if line.startswith(">"):
                    rest = line[1:]
                    if "_" in rest:
                        _, allele_part = rest.rsplit("_", 1)
                        out.write(f">{locus}{LOCUS_SEP}{allele_part}\n")
                    else:
                        out.write(f">{locus}{LOCUS_SEP}{rest}\n")
                else:
                    out.write(line + "\n")


def build_concat_db(scheme: Scheme, db_root: Path | None = None,
                    batch_size: int = BATCH_SIZE) -> list[Path]:
    """Build BATCH-SIZED BLAST databases.

    Returns the list of batch FASTA paths (the corresponding BLAST DBs live
    next to them, indexed by makeblastdb). Splitting the scheme into 100-locus
    chunks keeps BLAST's per-call memory + output bounded: one big DB with
    millions of alleles makes -max_target_seqs saturate on a single
    high-diversity locus, and removes the cap means BLAST allocates GBs of
    state. A handful of small batches is far easier on memory and finishes
    in similar wall-clock time.
    """
    db_root = db_root or (scheme.root / "blast_db")
    db_root.mkdir(parents=True, exist_ok=True)

    loci = scheme.loci
    expected = [db_root / f"batch_{i:04d}.fasta"
                for i in range((len(loci) + batch_size - 1) // batch_size)]

    # If all expected DBs already exist + are fresh, reuse.
    if expected:
        first_nhr = Path(str(expected[0]) + ".nhr")
        last_nhr = Path(str(expected[-1]) + ".nhr")
        manifest_mtime = scheme.manifest_path.stat().st_mtime
        if (first_nhr.exists() and last_nhr.exists()
                and first_nhr.stat().st_mtime >= manifest_mtime):
            return expected

    log.info("Building %d BLAST DB batch(es) of %d loci each for %s…",
             len(expected), batch_size, scheme.name)
    out_paths: list[Path] = []
    for i, fa_path in enumerate(expected):
        start = i * batch_size
        batch = loci[start:start + batch_size]
        _write_locus_batch(scheme, batch, fa_path)
        subprocess.run(
            ["makeblastdb", "-in", str(fa_path), "-dbtype", "nucl",
             "-out", str(fa_path)],
            check=True, capture_output=True,
        )
        out_paths.append(fa_path)
    return out_paths


def _blast_batch(assembly: Path, db: Path, threads: int) -> dict[str, dict]:
    """BLAST one batch DB; return {locus: best-hit} (streaming aggregate)."""
    fmt = "6 qseqid sseqid pident length qstart qend sstart send evalue bitscore slen"
    cmd = ["blastn", "-query", str(assembly), "-db", str(db),
           "-outfmt", fmt, "-num_threads", str(threads),
           "-max_target_seqs", str(MAX_HITS_PER_BATCH),
           # 1e-20 is still highly specific for ~500–1500 bp loci but won't
           # filter out shorter true matches the way 1e-30 did.
           "-evalue", "1e-20", "-perc_identity", "80"]
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    best: dict[str, dict] = {}
    for line in proc.stdout.splitlines():
        c = line.split("\t")
        if len(c) < 11:
            continue
        sid = c[1]
        if LOCUS_SEP not in sid:
            continue
        locus, allele = sid.split(LOCUS_SEP, 1)
        bitscore = float(c[9])
        prev = best.get(locus)
        if prev is None or bitscore > prev["bitscore"]:
            best[locus] = {
                "locus": locus, "allele": allele,
                "pident": float(c[2]), "length": int(c[3]),
                "bitscore": bitscore, "slen": int(c[10]),
            }
    return best


def call_cgmlst(
    assembly: Path,
    scheme: Scheme,
    threads: int = 0,
    min_identity: float = DEFAULT_IDENTITY,
    min_coverage: float = DEFAULT_COVERAGE,
) -> MLSTResult:
    if threads == 0:
        threads = max(1, mp.cpu_count() // 2)

    batches = build_concat_db(scheme)

    sample = assembly.stem.replace(".fna", "").replace(".fasta", "").replace(".fa", "")
    result = MLSTResult(sample=sample, scheme=scheme.name, st=None)

    # Run BLAST against each batch DB sequentially; merge best hits per locus.
    by_locus: dict[str, list[dict]] = defaultdict(list)
    for batch_db in batches:
        best = _blast_batch(assembly, batch_db, threads=threads)
        for locus, hit in best.items():
            by_locus[locus].append(hit)

    for locus in scheme.loci:
        hits = by_locus.get(locus, [])
        if not hits:
            result.calls[locus] = AlleleCall(
                locus=locus, allele=None, identity=0, coverage=0,
                bitscore=0, flag="LNF",
            )
            continue
        hits.sort(key=lambda h: h["bitscore"], reverse=True)
        top = hits[0]
        cov = 100.0 * top["length"] / top["slen"]
        allele_id = top["allele"]
        if top["pident"] >= min_identity and cov >= min_coverage:
            if top["pident"] >= 99.999 and cov >= 99.999:
                flag, allele = "EXC", allele_id
            else:
                flag, allele = "INF", f"{allele_id}~"
            result.calls[locus] = AlleleCall(
                locus=locus, allele=allele, identity=top["pident"],
                coverage=cov, bitscore=top["bitscore"], flag=flag,
            )
        else:
            result.calls[locus] = AlleleCall(
                locus=locus, allele=None, identity=top["pident"],
                coverage=cov, bitscore=top["bitscore"], flag="LNF",
            )

    # ST lookup against the profile table (if it exists for cgMLST too)
    total_loci = len(scheme.loci)
    missing = sum(1 for c in result.calls.values() if c.allele is None)
    inexact = sum(1 for c in result.calls.values() if c.flag == "INF")
    exact = sum(1 for c in result.calls.values() if c.flag == "EXC")
    # Diagnostic: when LNF dominates, distinguish "no BLAST hit at all" from
    # "hit existed but didn't clear thresholds" — surfaces wrong-scheme /
    # too-strict-cutoff problems instead of looking like a silent failure.
    lnf_with_hit = sum(1 for loc in scheme.loci
                       if by_locus.get(loc) and result.calls[loc].flag == "LNF")
    lnf_no_hit = missing - lnf_with_hit
    pct_missing = 100.0 * missing / max(1, total_loci)
    result.notes.append(
        f"loci called: {exact} EXC + {inexact} INF; missing: {missing} "
        f"({pct_missing:.1f}%; {lnf_no_hit} no BLAST hit, "
        f"{lnf_with_hit} below identity/coverage cutoff)"
    )
    if pct_missing > 50:
        result.notes.append(
            "WARNING: more than half the scheme came back LNF — most likely "
            "the wrong scheme was chosen for this organism, or the assembly "
            "is unusually fragmented. Try lowering --min-coverage to 70 or "
            "verify the scheme matches the species."
        )

    if scheme.profile_table and scheme.profile_table.exists() and missing == 0:
        try:
            from mlstudio.calling.mlst import _load_profile_table
            loci_order, profile_lookup = _load_profile_table(scheme)
            tup = tuple(re.sub(r"[^\d]", "", result.calls[loc].allele or "0")
                        for loc in loci_order)
            if "0" not in tup:
                st = profile_lookup.get(tup)
                if st:
                    result.st = f"{st}*" if inexact else st
        except Exception as e:
            log.debug("ST lookup skipped: %s", e)

    return result
