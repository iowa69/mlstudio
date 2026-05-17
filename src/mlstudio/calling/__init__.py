"""Typing pipelines.

- mlst.py    — classical 7-gene MLST via BLAST allele lookup.
- cgmlst.py  — core-genome MLST: BLAST primary call + Bowtie2 read-backed rescue.
- rescue.py  — Bowtie2 / pysam rescue for missing or low-confidence alleles.
- jobs.py    — multiprocessing job pool and progress reporting.
"""
