"""Scheme management: PubMLST, cgMLST.org, public Ridom schemes.

Responsibilities:
    - Discover available schemes from upstream registries.
    - Download and version-pin scheme files into the local cache.
    - Verify integrity (SHA-256 manifest).
    - Expose a uniform Scheme object to the rest of the codebase.

Layout on disk (planned):
    ~/.mlstudio/schemes/<species>/<scheme>/v<version>/
        loci/*.fasta
        profiles.tsv
        manifest.json   # SHA-256 hashes + source URL + fetched_at
"""
