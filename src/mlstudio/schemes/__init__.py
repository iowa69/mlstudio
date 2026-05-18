"""Scheme management.

Layout on disk:
    ~/.mlstudio/schemes/<organism>/<scheme>/v<version>/
        loci/<locus>.fasta
        profiles.tsv
        manifest.json
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class Scheme:
    """A locally-cached MLST or cgMLST scheme."""

    organism: str
    name: str
    root: Path
    loci: list[str] = field(default_factory=list)
    profile_table: Path | None = None
    source: str = ""
    last_updated: str = ""
    kind: str = "mlst"
    cluster_threshold: int = 0

    @property
    def loci_dir(self) -> Path:
        return self.root / "loci"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def concat_fasta(self) -> Path:
        return self.root / "all_loci.fasta"

    def locus_fasta(self, locus: str) -> Path:
        return self.loci_dir / f"{locus}.fasta"

    @classmethod
    def from_dir(cls, root: Path) -> Scheme:
        import json

        manifest = json.loads((root / "manifest.json").read_text())
        return cls(
            organism=manifest["organism"],
            name=manifest["name"],
            root=root,
            loci=manifest["loci"],
            profile_table=root / "profiles.tsv",
            source=manifest.get("source", ""),
            last_updated=manifest.get("last_updated", ""),
            kind=manifest.get("kind", "mlst"),
            cluster_threshold=manifest.get("cluster_threshold", 0),
        )
