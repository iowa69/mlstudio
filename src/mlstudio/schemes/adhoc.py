"""Build an ad-hoc cgMLST scheme from a single reference assembly.

One command — the user doesn't pick filters, doesn't review buckets, doesn't
save templates. They give us a reference FASTA and a friendly key, we give
them a usable scheme in the local cache.

Pipeline:
    1. prodigal -p single   →   predict CDS from the reference
    2. length filter        →   drop genes shorter than --min-length (default 200 nt)
    3. self-blastn          →   group genes by sequence similarity
                                (>= 90% identity over >= 60% length = paralog pair)
    4. paralog filter       →   keep only genes with NO paralog hit
    5. write per-locus FASTA, empty profiles.tsv, manifest.json

The resulting directory matches the layout of pulled BIGSdb schemes, so it
plugs straight into the existing calling pipeline.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from mlstudio.schemes import Scheme
from mlstudio.schemes.bigsdb import cache_root

log = logging.getLogger(__name__)

DEFAULT_MIN_LENGTH = 200
DEFAULT_PARALOG_IDENTITY = 90.0
DEFAULT_PARALOG_COVERAGE = 60.0


def _which_or_raise(tool: str) -> None:
    if not shutil.which(tool):
        raise RuntimeError(f"{tool!r} not found in PATH. Install via `./setup.sh`.")


def _parse_cds_fasta(path: Path) -> dict[str, str]:
    """Read a multi-FASTA into {id: seq}."""
    out: dict[str, str] = {}
    cur_id: str | None = None
    cur: list[str] = []
    for line in path.read_text().splitlines():
        if line.startswith(">"):
            if cur_id is not None:
                out[cur_id] = "".join(cur)
            cur_id = line[1:].split()[0]
            cur = []
        elif cur_id is not None:
            cur.append(line.strip())
    if cur_id is not None:
        out[cur_id] = "".join(cur)
    return out


def _self_blast_paralogs(
    cds_fasta: Path,
    threads: int,
    pident_cut: float,
    cov_cut: float,
) -> set[str]:
    """Return the set of CDS IDs that have at least one paralog hit."""
    db = cds_fasta.with_suffix(".blastdb")
    subprocess.run(
        ["makeblastdb", "-in", str(cds_fasta), "-dbtype", "nucl", "-out", str(db)],
        check=True, capture_output=True,
    )
    fmt = "6 qseqid sseqid pident length qlen slen"
    proc = subprocess.run(
        ["blastn", "-query", str(cds_fasta), "-db", str(db),
         "-outfmt", fmt, "-num_threads", str(threads),
         "-perc_identity", str(pident_cut), "-evalue", "1e-30"],
        check=True, capture_output=True, text=True,
    )
    paralogs: set[str] = set()
    for line in proc.stdout.splitlines():
        c = line.split("\t")
        if len(c) < 6:
            continue
        q, s = c[0], c[1]
        if q == s:
            continue
        pident = float(c[2])
        length = int(c[3])
        qlen, slen = int(c[4]), int(c[5])
        cov = 100.0 * length / max(1, min(qlen, slen))
        if pident >= pident_cut and cov >= cov_cut:
            paralogs.add(q)
            paralogs.add(s)
    return paralogs


def _sanitize_locus_name(raw: str) -> str:
    # Avoid double-underscore (our locus__allele separator in concat DBs).
    return raw.replace("__", "-").replace(" ", "_")


def build_adhoc_scheme(
    reference: Path,
    key: str,
    organism: str,
    cluster_threshold: int = 5,
    output_root: Path | None = None,
    threads: int = 8,
    min_length: int = DEFAULT_MIN_LENGTH,
    paralog_identity: float = DEFAULT_PARALOG_IDENTITY,
    paralog_coverage: float = DEFAULT_PARALOG_COVERAGE,
    force: bool = False,
) -> Scheme:
    """Build an ad-hoc cgMLST scheme from a single reference assembly.

    Returns a Scheme handle pointing at the newly created cache directory.
    """
    _which_or_raise("prodigal")
    _which_or_raise("makeblastdb")
    _which_or_raise("blastn")

    reference = Path(reference).resolve()
    if not reference.is_file():
        raise FileNotFoundError(reference)

    root = (output_root or cache_root()) / key
    if root.exists() and not force:
        raise FileExistsError(
            f"Scheme {key!r} already exists at {root}. Pick a different --key or pass --force."
        )
    if root.exists():
        shutil.rmtree(root)
    loci_dir = root / "loci"
    loci_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        cds_fna = tmpd / "cds.fna"

        log.info("Predicting CDS with prodigal (single-genome mode)…")
        subprocess.run(
            ["prodigal", "-i", str(reference), "-d", str(cds_fna),
             "-p", "single", "-o", "/dev/null", "-q"],
            check=True, capture_output=True,
        )
        cds = _parse_cds_fasta(cds_fna)
        log.info("  %d predicted CDS", len(cds))

        kept = {k: v for k, v in cds.items() if len(v) >= min_length}
        log.info("  %d after length filter (>= %d nt)", len(kept), min_length)

        # Write the length-filtered set for self-BLAST
        filt = tmpd / "filtered.fna"
        with filt.open("w") as fh:
            for k, v in kept.items():
                fh.write(f">{k}\n{v}\n")

        log.info("Self-BLAST for paralogs (>=%g%% id, >=%g%% cov)…",
                 paralog_identity, paralog_coverage)
        paralogs = _self_blast_paralogs(filt, threads, paralog_identity, paralog_coverage)
        log.info("  %d CDS in paralog clusters → dropped", len(paralogs))

        final = {k: v for k, v in kept.items() if k not in paralogs}
        log.info("Final cgMLST targets: %d", len(final))

    # Write each surviving CDS as its own per-locus FASTA (allele 1 = reference seq).
    hashes: dict[str, str] = {}
    locus_names: list[str] = []
    for raw_locus, seq in sorted(final.items()):
        locus = _sanitize_locus_name(raw_locus)
        content = f">{locus}_1\n{seq}\n"
        (loci_dir / f"{locus}.fasta").write_text(content)
        hashes[locus] = hashlib.sha256(content.encode()).hexdigest()
        locus_names.append(locus)

    # Profiles table starts empty: the user will accumulate STs as they call samples.
    (root / "profiles.tsv").write_text("ST\t" + "\t".join(locus_names) + "\n")

    manifest = {
        "key": key,
        "organism": organism,
        "name": "cgMLST (ad-hoc)",
        "kind": "cgmlst",
        "cluster_threshold": cluster_threshold,
        "source": f"adhoc:{reference}",
        "loci": locus_names,
        "locus_sha256": hashes,
        "last_updated": "",
        "records": 0,
        "adhoc": True,
        "min_length": min_length,
        "paralog_identity": paralog_identity,
        "paralog_coverage": paralog_coverage,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2))
    log.info("Wrote scheme %s with %d loci to %s", key, len(locus_names), root)
    return Scheme.from_dir(root)
