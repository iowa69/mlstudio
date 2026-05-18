"""Core-genome MLST calling: prodigal CDS prediction + batched BLAST.

The naive "BLAST the whole assembly against a concatenated allele DB" approach
breaks on real cgMLST schemes because individual loci can have 4 000+ allelic
variants. With 100 loci per batch DB that's 100 000+ subject sequences, and
BLAST's `-max_target_seqs` cap (which is applied early during preliminary
alignment, not as a final filter) gets fully saturated by hits to one or two
hyper-variable loci. The other 98 loci in the batch return zero alignments
and are silently marked LNF.

The chewBBACA-style fix is to flip the problem on its head: run prodigal once
per assembly to extract the ~5 000 predicted coding sequences, then BLAST
each CDS (a short, clean ORF) against the batched DBs. Each CDS naturally
hits at most one locus' alleles, so `-max_target_seqs` per query is more than
enough and no locus is silently dropped.

CDS predictions are cached per-assembly (keyed by size + mtime) under
`<scheme_root>/cds_cache/<sample>.cds.fna`, so subsequent runs against the
same input are instant.
"""

from __future__ import annotations

import hashlib
import logging
import multiprocessing as mp
import re
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from mlstudio.calling.mlst import AlleleCall, MLSTResult
from mlstudio.schemes import Scheme

log = logging.getLogger(__name__)

# Default thresholds — chewBBACA-style permissive defaults. Subject coverage
# is intentionally low (80%) because cgMLST loci frequently land at contig
# boundaries in fragmented assemblies, so a fully-conserved allele with the
# tail truncated by the contig break should still be called (it'll be flagged
# INF if identity slips below 100%). 90/90 was too strict for real-world data.
DEFAULT_IDENTITY = 90.0
DEFAULT_COVERAGE = 80.0
LOCUS_SEP = "__"

# Loci per BLAST DB batch. With CDS queries (rather than the whole assembly)
# each batch returns a handful of hits per query, so we can keep the batch
# size reasonably large without hitting -max_target_seqs saturation.
BATCH_SIZE = 200

# Per-CDS-query cap inside one BLAST call. Each CDS realistically matches
# alleles of one locus only, so 50 is generous.
MAX_HITS_PER_QUERY = 50

# How many BLAST batches to run concurrently. BLAST itself releases the GIL
# during subprocess.run, so a ThreadPoolExecutor is enough; each worker gets
# `total_threads // BATCH_PARALLELISM` BLAST threads (min 2). Total CPU =
# total_threads regardless of fanout. Pure win on multi-batch schemes — the
# previous sequential loop wasted ~70% of wall-clock between BLAST startup
# and output flushing.
BATCH_PARALLELISM = 4


# ---------------------------------------------------------------------------
# Step 1 — prodigal CDS prediction (cached per assembly)
# ---------------------------------------------------------------------------

def _cache_key(assembly: Path) -> str:
    st = assembly.stat()
    return hashlib.sha1(
        f"{assembly.resolve()}:{st.st_size}:{int(st.st_mtime)}".encode()
    ).hexdigest()[:16]


def predict_cds(assembly: Path, cache_root: Path) -> Path:
    """Run prodigal in single-genome mode and cache the predicted CDS FASTA.

    Returns the path to the predicted-CDS nucleotide FASTA. Re-runs are
    skipped when the cached file matches the assembly's size + mtime.
    """
    cache_root.mkdir(parents=True, exist_ok=True)
    key = _cache_key(assembly)
    out = cache_root / f"{assembly.stem}.{key}.cds.fna"
    if out.exists() and out.stat().st_size > 0:
        log.info("Reusing cached CDS predictions at %s", out)
        return out

    log.info("Predicting CDS with prodigal on %s…", assembly.name)
    # `-p single` is the right mode for bacterial WGS assemblies; the
    # alternative `-p meta` is for metagenomes. `-q` suppresses the noisy
    # banner; we throw away the .gff translation file (`-f gff -o /dev/null`).
    subprocess.run(
        ["prodigal", "-i", str(assembly), "-d", str(out),
         "-o", "/dev/null", "-f", "gff", "-p", "single", "-q"],
        check=True, capture_output=True,
    )
    return out


# ---------------------------------------------------------------------------
# Step 2 — batched allele-DB construction (reused across all samples)
# ---------------------------------------------------------------------------

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
    """Build batched BLAST databases of the scheme's alleles.

    Returns the list of batch FASTA paths (the corresponding BLAST DBs live
    next to them, indexed by makeblastdb). DBs are cached and only rebuilt
    when the scheme manifest changes.
    """
    # Scope batched DBs by batch_size so changing it forces a rebuild (the
    # previous behaviour silently reused 100-loci batches when batch_size was
    # bumped to 200, so half the scheme stopped being queried).
    db_root = db_root or (scheme.root / f"blast_db_bs{batch_size}")
    db_root.mkdir(parents=True, exist_ok=True)

    loci = scheme.loci
    expected = [db_root / f"batch_{i:04d}.fasta"
                for i in range((len(loci) + batch_size - 1) // batch_size)]

    if expected:
        first_nhr = Path(str(expected[0]) + ".nhr")
        last_nhr = Path(str(expected[-1]) + ".nhr")
        # All expected batch files must actually exist, AND there must be no
        # *extra* stale batches sitting in the directory from a previous
        # batch_size — otherwise we'd happily query a partial scheme.
        existing = sorted(db_root.glob("batch_*.fasta"))
        manifest_mtime = scheme.manifest_path.stat().st_mtime
        if (first_nhr.exists() and last_nhr.exists()
                and len(existing) == len(expected)
                and first_nhr.stat().st_mtime >= manifest_mtime):
            return expected
        # Stale set on disk — wipe before rebuilding so we don't end up with
        # a mixed-batch-size mess.
        for f in existing:
            for ext in ("", ".nhr", ".nin", ".nsq", ".ndb", ".njs",
                        ".not", ".ntf", ".nto"):
                p = Path(str(f) + ext) if ext else f
                if p.exists():
                    p.unlink()

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


# ---------------------------------------------------------------------------
# Step 3 — CDS-vs-allele BLAST + best-hit-per-locus aggregation
# ---------------------------------------------------------------------------

def _blast_batch_cds(cds_fa: Path, db: Path, threads: int) -> dict[str, dict]:
    """BLAST predicted CDS against one batch DB; return {locus: best hit}.

    Uses `-task megablast` (word size 28) instead of the default `blastn`
    word-size-11 task. cgMLST calls are expected to be ≥80% identity by
    construction, well within megablast's sensitivity envelope — and it's
    3–5× faster on bacterial allele DBs. `-perc_identity 80` keeps the
    low-end identity floor as a safety net.

    Each CDS query matches alleles of (usually) a single locus, so
    `-max_target_seqs` operates per-query and never saturates the way it
    did with whole-assembly queries. Best hit per locus is aggregated as
    we stream the output.
    """
    fmt = "6 qseqid sseqid pident length qstart qend sstart send evalue bitscore slen qlen"
    cmd = [
        "blastn", "-task", "megablast",
        "-query", str(cds_fa), "-db", str(db),
        "-outfmt", fmt, "-num_threads", str(threads),
        "-max_target_seqs", str(MAX_HITS_PER_QUERY),
        "-evalue", "1e-20",
        "-perc_identity", "80",
        "-dust", "no",   # turn off low-complexity masking; bioinformatic alleles aren't repetitive
    ]
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    best: dict[str, dict] = {}
    for line in proc.stdout.splitlines():
        c = line.split("\t")
        if len(c) < 12:
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
                "bitscore": bitscore,
                "slen": int(c[10]),
                "qlen": int(c[11]),
            }
    return best


def call_cgmlst(
    assembly: Path,
    scheme: Scheme,
    threads: int = 0,
    min_identity: float = DEFAULT_IDENTITY,
    min_coverage: float = DEFAULT_COVERAGE,
) -> MLSTResult:
    """Call cgMLST using the prodigal → BLAST(CDS-vs-alleles) workflow."""
    if threads == 0:
        threads = max(1, mp.cpu_count() // 2)

    # 1. Allele DB (cached across samples)
    batches = build_concat_db(scheme)

    # 2. Predict CDS (cached per assembly)
    cds_cache = scheme.root / "cds_cache"
    cds_fa = predict_cds(assembly, cds_cache)

    sample = assembly.stem.replace(".fna", "").replace(".fasta", "").replace(".fa", "")
    result = MLSTResult(sample=sample, scheme=scheme.name, st=None)

    # 3. BLAST CDS against each batch DB *in parallel*; merge best hits per
    # locus. Each worker uses a fraction of the user's thread budget so the
    # total CPU stays the same (default: 4 concurrent batches × threads/4
    # BLAST threads each). On multi-batch schemes this is a clean 2–3×
    # wall-clock win over the previous sequential loop because BLAST's
    # startup + serial output flushing was the dominant cost.
    per_batch_threads = max(2, threads // BATCH_PARALLELISM)
    by_locus: dict[str, list[dict]] = defaultdict(list)
    with ThreadPoolExecutor(max_workers=min(BATCH_PARALLELISM, len(batches))) as pool:
        futures = {pool.submit(_blast_batch_cds, cds_fa, b, per_batch_threads): b
                   for b in batches}
        for fut in as_completed(futures):
            best = fut.result()
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
        # Take the max of subject coverage and query coverage. The CDS query
        # is usually slightly longer than the matched allele (it includes the
        # start codon and stop codon); using max() captures both directions.
        slen = max(1, top["slen"])
        qlen = max(1, top["qlen"])
        cov_subj = 100.0 * top["length"] / slen
        cov_query = 100.0 * top["length"] / qlen
        cov = max(cov_subj, cov_query)
        allele_id = top["allele"]
        if top["pident"] >= min_identity and cov >= min_coverage:
            if top["pident"] >= 99.999 and cov_subj >= 99.0:
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

    # Diagnostics
    total_loci = len(scheme.loci)
    missing = sum(1 for c in result.calls.values() if c.allele is None)
    inexact = sum(1 for c in result.calls.values() if c.flag == "INF")
    exact = sum(1 for c in result.calls.values() if c.flag == "EXC")
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

    # ST lookup against the profile table (cgMLST schemes ship loci-only,
    # so this branch is only hit for MLST/wgMLST-style schemes that include
    # a profiles.tsv).
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

    # cgST profile hash. Stable 8-hex-char fingerprint of the sorted
    # (locus, allele) tuple over called loci (EXC + INF). Two assemblies with
    # the exact same cgMLST profile share a cgST; one allele difference flips
    # it. This is the closest thing to an "ST number" cgMLST.org schemes can
    # offer without a centrally-maintained profile table.
    called_tuples = sorted(
        (locus, re.sub(r"[^\d]", "", c.allele or ""))
        for locus, c in result.calls.items()
        if c.allele is not None
    )
    if called_tuples:
        digest_input = "\t".join(f"{loc}={al}" for loc, al in called_tuples)
        result.cgst = hashlib.sha256(digest_input.encode()).hexdigest()[:8]
        result.notes.append(
            f"cgST: {result.cgst} ({len(called_tuples)} called loci)"
        )

    return result
