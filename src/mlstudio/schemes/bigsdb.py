"""BIGSdb / PubMLST REST API client.

Works against any BIGSdb instance — PubMLST.org, BIGSdb-Pasteur, etc. The hostname
is configurable. The scheme registry below maps friendly organism names to
(host, database, scheme_id) tuples for the most common organisms.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from mlstudio.schemes import Scheme

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SchemeRef:
    organism: str
    host: str
    database: str
    scheme_id: int
    scheme_label: str  # e.g. "MLST" or "cgMLST1748"


REGISTRY: dict[str, SchemeRef] = {
    "lmonocytogenes_mlst": SchemeRef(
        organism="Listeria monocytogenes",
        host="https://bigsdb.pasteur.fr",
        database="pubmlst_listeria_seqdef",
        scheme_id=2,
        scheme_label="MLST",
    ),
    "lmonocytogenes_cgmlst": SchemeRef(
        organism="Listeria monocytogenes",
        host="https://bigsdb.pasteur.fr",
        database="pubmlst_listeria_seqdef",
        scheme_id=15,
        scheme_label="cgMLST1748_v2",
    ),
    "saureus_mlst": SchemeRef(
        organism="Staphylococcus aureus",
        host="https://rest.pubmlst.org",
        database="pubmlst_saureus_seqdef",
        scheme_id=1,
        scheme_label="MLST",
    ),
    "ecoli_mlst": SchemeRef(
        organism="Escherichia coli",
        host="https://rest.pubmlst.org",
        database="pubmlst_ecoli_achtman_seqdef",
        scheme_id=1,
        scheme_label="MLST (Achtman)",
    ),
    "kpneumoniae_mlst": SchemeRef(
        organism="Klebsiella pneumoniae",
        host="https://bigsdb.pasteur.fr",
        database="pubmlst_klebsiella_seqdef",
        scheme_id=1,
        scheme_label="MLST",
    ),
}


class BigsdbClient:
    """Thin client around a BIGSdb instance."""

    def __init__(self, host: str, timeout: float = 60.0) -> None:
        self.host = host.rstrip("/")
        self._client = httpx.Client(timeout=timeout, follow_redirects=True)

    def __enter__(self) -> "BigsdbClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.close()

    def scheme_metadata(self, database: str, scheme_id: int) -> dict:
        url = f"{self.host}/api/db/{database}/schemes/{scheme_id}"
        r = self._client.get(url)
        r.raise_for_status()
        return r.json()

    def profiles_csv(self, database: str, scheme_id: int) -> str:
        url = f"{self.host}/api/db/{database}/schemes/{scheme_id}/profiles_csv"
        r = self._client.get(url)
        r.raise_for_status()
        return r.text

    def locus_alleles_fasta(self, database: str, locus: str) -> str:
        url = f"{self.host}/api/db/{database}/loci/{locus}/alleles_fasta"
        r = self._client.get(url)
        r.raise_for_status()
        return r.text


def cache_root() -> Path:
    """Local scheme cache directory: ~/.mlstudio/schemes/."""
    from platformdirs import user_data_dir

    return Path(user_data_dir("mlstudio")) / "schemes"


def pull_scheme(key: str, force: bool = False, cache_dir: Path | None = None) -> Scheme:
    """Download a scheme by registry key into the local cache.

    Returns a Scheme handle. Re-running is a no-op unless force=True.
    """
    if key not in REGISTRY:
        raise KeyError(f"Unknown scheme '{key}'. Available: {sorted(REGISTRY)}")

    ref = REGISTRY[key]
    root = (cache_dir or cache_root()) / key
    loci_dir = root / "loci"
    loci_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"

    if manifest_path.exists() and not force:
        log.info("Scheme %s already cached at %s", key, root)
        return Scheme.from_dir(root)

    log.info("Pulling scheme %s from %s", key, ref.host)
    with BigsdbClient(ref.host) as client:
        meta = client.scheme_metadata(ref.database, ref.scheme_id)
        loci = [loc.rsplit("/", 1)[-1] for loc in meta["loci"]]

        profiles_text = client.profiles_csv(ref.database, ref.scheme_id)
        (root / "profiles.tsv").write_text(profiles_text)

        hashes: dict[str, str] = {}
        for locus in loci:
            fasta = client.locus_alleles_fasta(ref.database, locus)
            target = loci_dir / f"{locus}.fasta"
            target.write_text(fasta)
            hashes[locus] = hashlib.sha256(fasta.encode()).hexdigest()

    manifest = {
        "key": key,
        "organism": ref.organism,
        "name": ref.scheme_label,
        "source": f"{ref.host}/api/db/{ref.database}/schemes/{ref.scheme_id}",
        "loci": loci,
        "locus_sha256": hashes,
        "last_updated": meta.get("last_updated", ""),
        "records": meta.get("records"),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return Scheme.from_dir(root)


def list_local(cache_dir: Path | None = None) -> list[Scheme]:
    """Return all locally-cached schemes."""
    root = cache_dir or cache_root()
    if not root.exists():
        return []
    out: list[Scheme] = []
    for child in sorted(root.iterdir()):
        if (child / "manifest.json").exists():
            out.append(Scheme.from_dir(child))
    return out
