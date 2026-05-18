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

# Permissive defaults: contig boundaries cut classical MLST genes more often
# than people realise (e.g. Efaecium pstS / ddl), and a 95/90 cutoff drops the
# call. 90/70 still keeps the call specific (the allele table is small and
# very different alleles get distinct numbers) while picking up real matches
# that span contig breaks. HSP aggregation in _best_hit recovers split-HSP
# cases on top of this.
DEFAULT_IDENTITY = 90.0
DEFAULT_COVERAGE = 70.0


@dataclass(slots=True)
class AlleleCall:
    locus: str
    allele: str | None        # e.g. "3" for exact, "3?" for inexact, None for not found
    identity: float
    coverage: float
    bitscore: float
    flag: str                  # EXC, NIPHEM, NIPH, ASM, LNF, INF
    # When `flag == "INF"`, several alleles often share the partial
    # alignment we did see — populated with every allele whose hit is
    # within 0.5 % identity and 5 % alignment length of the top. The
    # profile-table lookup uses these as a candidate set instead of
    # locking onto the single highest-bitscore one (which is essentially
    # a tie-break under low coverage).
    candidates: list[str] = field(default_factory=list)

    @property
    def is_exact(self) -> bool:
        return self.flag == "EXC"


@dataclass(slots=True)
class MLSTResult:
    sample: str
    scheme: str
    st: str | None             # classical 7-gene ST: "1", "1*" (inexact) or None
    calls: dict[str, AlleleCall] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    # For cgMLST runs: stable 8-hex-char hash of the sorted (locus, allele)
    # tuple, computed only for called loci (EXC / INF). Two isolates with the
    # same cgMLST profile get the same `cgst`. This is the closest thing to
    # an ST number for cgMLST schemes that don't ship a profile table.
    cgst: str | None = None
    # When a cgMLST run is auto-paired with a classical MLST one, the MLST
    # scheme key and its ST land here so the GUI can show both.
    mlst_scheme: str | None = None
    mlst_st: str | None = None

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
    # BLAST emits one row per HSP, so a single allele match can be split into
    # multiple rows (one per gap-broken segment). Group by subject and sum
    # alignment length per subject — that fixes the case where a real allele
    # hits at 95% identity but the best individual HSP covers only 20% of it
    # (we were seeing this on Efaecium pstS, which has a small gap in its
    # alignment to many test isolates).
    by_subj: dict[str, dict] = {}
    for h in hits:
        s = h["sseqid"]
        cur = by_subj.get(s)
        if cur is None:
            by_subj[s] = {**h, "_aligned": h["length"]}
        else:
            cur["_aligned"] += h["length"]
            if h["bitscore"] > cur["bitscore"]:
                cur["pident"] = h["pident"]
                cur["bitscore"] = h["bitscore"]
                cur["length"] = h["length"]
                cur["slen"] = h["slen"]
    # Rank by (coverage, identity, bitscore). The first key is critical:
    # without it, a long allele variant with one big HSP can outrank the
    # actually-correct shorter allele that aligns at 100 %. Real example
    # from Efaecium ddl: ddl_67 is 1 512 bp with five scattered HSPs
    # summing to 71 % subject coverage; ddl_1 is the canonical 465 bp
    # allele at 100 % cov / 100 % identity. Bitscore-sort picks ddl_67;
    # coverage-sort picks ddl_1 — and ST1478 (which expects ddl=1) then
    # falls out of the profile lookup cleanly.
    for h in by_subj.values():
        h["_coverage"] = h["_aligned"] / max(1, h["slen"])
    agg = sorted(by_subj.values(),
                 key=lambda h: (h["_coverage"], h["pident"], h["bitscore"]),
                 reverse=True)
    top = agg[0]
    cov = 100.0 * top["_coverage"]
    allele_id = top["sseqid"].rsplit("_", 1)[-1]
    candidates: list[str] = []
    if top["pident"] >= min_id and cov >= min_cov:
        if top["pident"] >= 99.999 and cov >= 99.0:
            flag = "EXC"
            allele = allele_id
            candidates = [allele_id]
        else:
            flag = "INF"
            allele = f"{allele_id}~"
            # Near-tied candidates: anything within 0.5 % identity and
            # 5 percentage points of coverage of the top.
            for h in agg:
                if (abs(h["pident"] - top["pident"]) <= 0.5
                        and abs(h["_coverage"] - top["_coverage"]) <= 0.05):
                    candidates.append(h["sseqid"].rsplit("_", 1)[-1])
    else:
        flag = "LNF"
        allele = None
    return AlleleCall(locus=locus, allele=allele,
                      identity=top["pident"], coverage=cov,
                      bitscore=top["bitscore"], flag=flag,
                      candidates=candidates)


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
    # Direct exact-match first
    st = None
    if "0" not in allele_tuple:
        st = profile_lookup.get(allele_tuple)
        if st is not None:
            inexact = any(c.flag == "INF" for c in result.calls.values())
            result.st = f"{st}*" if inexact else st

    # Fuzzy lookup. When an exact-tuple match failed, treat each EXC call as
    # a hard constraint and each INF / LNF call as a wildcard ("we are not
    # certain enough about this allele to use it for ST disambiguation"). A
    # short BLAST hit at 100 % identity could plausibly belong to several
    # very similar alleles, so penalising it would lose real STs — e.g.
    # Efaecium ST1478, which has a 71-%-covered ddl-67~ that's most likely
    # actually ddl-1 in this sequence context, would have been rejected.
    #
    # Special handling: PubMLST "0" in the profile table means "this locus
    # is intentionally absent in this ST" (e.g. Efaecium ST1478 with the
    # known pstS deletion). A null observed allele matches "0" exactly.
    if result.st is None:
        # Per-locus acceptance set: a profile's allele at this locus must
        # be one of these values. EXC → single value. INF → all near-tied
        # candidates BLAST returned. LNF (or null call) → any allele,
        # including PubMLST's "0" sentinel for an intentionally absent
        # locus (Efaecium ST1478 with the known pstS deletion is exactly
        # this case).
        accept: list[set[str] | None] = []
        wildcard_loci: list[str] = []
        for idx, loc in enumerate(loci_order):
            call = result.calls[loc]
            obs = allele_tuple[idx]
            if call.flag == "EXC" and obs != "0":
                accept.append({obs})
            elif call.flag == "INF" and call.candidates:
                accept.append(set(call.candidates))
                wildcard_loci.append(loc + "?")
            else:
                accept.append(None)
                wildcard_loci.append(loc)

        # Score every profile by how specifically it fits our partial call.
        # `bonus` rewards profiles where a locus we couldn't call is
        # *also* "0" (intentionally absent) in the profile — that turns a
        # wildcard slot into evidence rather than just a free pass. Without
        # this, partial profiles with one missing locus produce many tied
        # STs (10–60 for Efaecium); the bonus lifts the one biologically
        # consistent ST out of the tie. Example: ST1478 has pstS=0 in
        # PubMLST and our pstS observed is LNF → +1 bonus → ST1478 picked
        # over other STs that have a real pstS allele we couldn't recover.
        scored: list[tuple[str, int]] = []   # (st, bonus)
        for profile_key, profile_st in profile_lookup.items():
            ok = True
            bonus = 0
            for idx, exp in enumerate(profile_key):
                allowed = accept[idx]
                obs = allele_tuple[idx]
                if allowed is None:
                    if exp == "0":
                        bonus += 1
                    continue
                if exp == "0" and obs != "0":
                    ok = False
                    break
                if exp not in allowed:
                    ok = False
                    break
            if ok:
                scored.append((profile_st, bonus))

        if scored:
            # Highest bonus wins; tie-break by ST number.
            scored.sort(key=lambda x: (-x[1],
                                       int(x[0]) if x[0].isdigit() else 1_000_000))
            best_bonus = scored[0][1]
            top_sts = sorted({st for st, b in scored if b == best_bonus},
                             key=lambda s: int(s) if s.isdigit() else 1_000_000)
            if len(top_sts) == 1:
                result.st = f"{top_sts[0]}~"
                result.notes.append(
                    f"closest ST: {top_sts[0]} (matches all certain loci"
                    + (f"; +{best_bonus} consistent null locus / loci"
                       if best_bonus else "")
                    + f"; uncertain: {', '.join(wildcard_loci) or 'none'})"
                )
            else:
                result.st = f"{top_sts[0]}~?"
                result.notes.append(
                    f"ambiguous: {len(top_sts)} STs compatible with "
                    f"partial profile — {', '.join(top_sts[:5])}"
                    + (f", … (+{len(top_sts) - 5})" if len(top_sts) > 5 else "")
                )
        elif "0" in allele_tuple:
            result.notes.append("missing allele(s) — no ST assigned")
        else:
            result.notes.append("novel allele combination — no matching ST")
    return result
