"""Typing pipelines.

- mlst.py         — classical 7-gene MLST via BLAST allele lookup.
- cgmlst.py       — core-genome MLST: batched BLAST primary call against per-100-loci DBs.
- fastp_wrapper.py — read QC + sequencing-platform auto-detection.

Bowtie2 read-backed rescue of missing/low-confidence loci is on the roadmap (M3 follow-up)
but not yet implemented — primary BLAST calling is currently the only path.
"""
