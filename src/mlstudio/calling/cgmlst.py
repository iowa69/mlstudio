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

DEFAULT_IDENTITY = 90.0
DEFAULT_COVERAGE = 90.0
LOCUS_SEP = "__"


def build_concat_db(scheme: Scheme, db_root: Path | None = None) -> Path:
    """Concatenate all locus FASTAs into a single multi-FASTA and BLAST-index it."""
    db_root = db_root or (scheme.root / "blast_db")
    db_root.mkdir(parents=True, exist_ok=True)
    concat = db_root / "concat.fasta"

    if concat.exists() and concat.with_suffix(".fasta.nhr").exists():
        return concat.with_suffix(".fasta")

    if not concat.exists() or concat.stat().st_mtime < scheme.manifest_path.stat().st_mtime:
        log.info("Building concatenated FASTA for %s (%d loci)…", scheme.name, len(scheme.loci))
        with concat.open("w") as out:
            for locus in scheme.loci:
                fa = scheme.locus_fasta(locus)
                if not fa.exists():
                    log.warning("Missing locus FASTA: %s", locus)
                    continue
                for line in fa.read_text().splitlines():
                    if line.startswith(">"):
                        # ">abcZ_1" -> ">abcZ__1"
                        rest = line[1:]
                        if "_" in rest:
                            loc_part, allele_part = rest.rsplit("_", 1)
                            out.write(f">{locus}{LOCUS_SEP}{allele_part}\n")
                        else:
                            out.write(f">{locus}{LOCUS_SEP}{rest}\n")
                    else:
                        out.write(line + "\n")

    out_prefix = db_root / "concat.fasta"
    if not (out_prefix.with_suffix(".fasta.nhr").exists() or
            Path(str(out_prefix) + ".nhr").exists()):
        subprocess.run(
            ["makeblastdb", "-in", str(concat), "-dbtype", "nucl",
             "-out", str(concat), "-parse_seqids"],
            check=True, capture_output=True,
        )
    return concat


def _blast_concat(assembly: Path, db: Path, threads: int) -> list[dict]:
    fmt = "6 qseqid sseqid pident length qstart qend sstart send evalue bitscore slen"
    cmd = ["blastn", "-query", str(assembly), "-db", str(db),
           "-outfmt", fmt, "-num_threads", str(threads),
           "-max_target_seqs", "5000", "-evalue", "1e-30",
           "-perc_identity", "85"]
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    hits = []
    for line in proc.stdout.splitlines():
        c = line.split("\t")
        if len(c) < 11:
            continue
        sid = c[1]
        if LOCUS_SEP not in sid:
            continue
        locus, allele = sid.split(LOCUS_SEP, 1)
        hits.append({
            "locus": locus, "allele": allele,
            "pident": float(c[2]), "length": int(c[3]),
            "bitscore": float(c[9]), "slen": int(c[10]),
        })
    return hits


def call_cgmlst(
    assembly: Path,
    scheme: Scheme,
    threads: int = 0,
    min_identity: float = DEFAULT_IDENTITY,
    min_coverage: float = DEFAULT_COVERAGE,
) -> MLSTResult:
    if threads == 0:
        threads = max(1, mp.cpu_count() // 2)

    db = build_concat_db(scheme)

    sample = assembly.stem.replace(".fna", "").replace(".fasta", "").replace(".fa", "")
    result = MLSTResult(sample=sample, scheme=scheme.name, st=None)

    all_hits = _blast_concat(assembly, db, threads=threads)
    by_locus: dict[str, list[dict]] = defaultdict(list)
    for h in all_hits:
        by_locus[h["locus"]].append(h)

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
    missing = sum(1 for c in result.calls.values() if c.allele is None)
    inexact = sum(1 for c in result.calls.values() if c.flag == "INF")
    exact = sum(1 for c in result.calls.values() if c.flag == "EXC")
    result.notes.append(f"loci called: {exact} EXC + {inexact} INF; missing: {missing}")

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
