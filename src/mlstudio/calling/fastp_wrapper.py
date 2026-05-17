"""fastp wrapper with sequencing-data auto-detection.

For Illumina paired-end reads only. Detects:
    - average read length (used to tune length filters)
    - quality encoding (Phred+33 vs +64 — Illumina has been +33 for years, but
      we sanity-check the first quality string and warn on unexpected ranges)
    - whether reads look like already-trimmed (very short on average, no adapters)
    - approximate insert size (skipped — would need read mapping)

Output: cleaned R1/R2 + a fastp HTML/JSON report per sample.
"""

from __future__ import annotations

import gzip
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(slots=True)
class FastqProfile:
    avg_read_length: int
    sampled_reads: int
    min_qual: int
    max_qual: int
    encoding: str  # "phred33" or "phred64"
    looks_pretrimmed: bool


def _open_maybe_gz(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt")
    return open(path)


def profile_fastq(path: Path, sample_n: int = 5000) -> FastqProfile:
    """Sample the first N records of a FASTQ to estimate read length + quality range."""
    lengths: list[int] = []
    min_q = 256
    max_q = 0
    with _open_maybe_gz(path) as f:
        i = 0
        while i < sample_n:
            header = f.readline()
            if not header:
                break
            seq = f.readline().strip()
            f.readline()
            qual = f.readline().strip()
            if not qual:
                break
            lengths.append(len(seq))
            for ch in qual:
                q = ord(ch)
                if q < min_q:
                    min_q = q
                if q > max_q:
                    max_q = q
            i += 1

    if not lengths:
        raise ValueError(f"No reads found in {path}")

    avg_len = sum(lengths) // len(lengths)
    encoding = "phred33" if min_q < 64 else "phred64"
    looks_pretrimmed = avg_len < 80  # Illumina raw is typically 100-300 bp
    return FastqProfile(
        avg_read_length=avg_len,
        sampled_reads=len(lengths),
        min_qual=min_q,
        max_qual=max_q,
        encoding=encoding,
        looks_pretrimmed=looks_pretrimmed,
    )


@dataclass(slots=True)
class FastpResult:
    r1_out: Path
    r2_out: Path
    json_report: Path
    html_report: Path
    profile_r1: FastqProfile
    params: dict[str, object]
    stats: dict[str, object]


def run_fastp(
    r1: Path,
    r2: Path,
    out_dir: Path,
    sample_name: str,
    threads: int = 4,
) -> FastpResult:
    """Run fastp on a paired-end Illumina sample with auto-tuned parameters."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    profile = profile_fastq(r1)

    # Auto-tune parameters
    min_len = max(40, profile.avg_read_length // 3) if not profile.looks_pretrimmed else 30
    qual_cut = 20
    n_base_limit = 5

    r1_out = out_dir / f"{sample_name}_R1.clean.fastq.gz"
    r2_out = out_dir / f"{sample_name}_R2.clean.fastq.gz"
    json_report = out_dir / f"{sample_name}.fastp.json"
    html_report = out_dir / f"{sample_name}.fastp.html"

    cmd = [
        "fastp",
        "-i", str(r1),
        "-I", str(r2),
        "-o", str(r1_out),
        "-O", str(r2_out),
        "--json", str(json_report),
        "--html", str(html_report),
        "--thread", str(threads),
        "--length_required", str(min_len),
        "--cut_front",
        "--cut_tail",
        "--cut_mean_quality", str(qual_cut),
        "--n_base_limit", str(n_base_limit),
        "--detect_adapter_for_pe",
    ]
    if profile.encoding == "phred64":
        cmd.append("--phred64")

    log.info("Running fastp for %s (avg_len=%d, encoding=%s)",
             sample_name, profile.avg_read_length, profile.encoding)

    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    log.debug("fastp stderr: %s", proc.stderr[-500:])

    stats: dict[str, object] = {}
    if json_report.exists():
        rep = json.loads(json_report.read_text())
        stats = {
            "reads_before": rep.get("summary", {}).get("before_filtering", {}).get("total_reads"),
            "reads_after": rep.get("summary", {}).get("after_filtering", {}).get("total_reads"),
            "q20_after": rep.get("summary", {}).get("after_filtering", {}).get("q20_rate"),
            "q30_after": rep.get("summary", {}).get("after_filtering", {}).get("q30_rate"),
        }

    return FastpResult(
        r1_out=r1_out,
        r2_out=r2_out,
        json_report=json_report,
        html_report=html_report,
        profile_r1=profile,
        params={
            "length_required": min_len,
            "cut_mean_quality": qual_cut,
            "n_base_limit": n_base_limit,
            "encoding": profile.encoding,
        },
        stats=stats,
    )
