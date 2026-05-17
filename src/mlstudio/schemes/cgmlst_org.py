"""cgMLST.org scheme downloader.

cgMLST.org hosts public cgMLST schemes (some originally curated by Ridom). Schemes are
distributed as ZIP archives containing per-locus allele FASTA files plus a target list.

Stub — to be implemented in M1.
"""

from __future__ import annotations


class CgMLSTOrgClient:
    """Client for fetching public cgMLST schemes from cgmlst.org."""

    BASE_URL = "https://www.cgmlst.org"

    def __init__(self) -> None:
        raise NotImplementedError("M1 — cgMLST.org client not yet implemented.")
