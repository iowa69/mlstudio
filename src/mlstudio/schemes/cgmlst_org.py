"""cgMLST.org client.

cgMLST.org hosts publicly downloadable cgMLST allele bundles for ~40 bacterial
species. Each scheme has:
  - a stable slug (e.g. ``Lmonocytogenes``, ``Kpneumoniae_complex``)
  - a locus table at ``/schema/<slug>/locus/?content-type=csv``
  - a ZIP of per-locus FASTAs at ``/schema/<slug>/alleles/``

Unlike BIGSdb-Pasteur, the bundle is anonymous-downloadable for every scheme,
so this is the most reliable cgMLST source. We pull once, unpack into the
local scheme cache, and never re-download.

The static registry of the 40 organisms ships in ``cgmlst_org_registry.json``.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import zipfile
from pathlib import Path

import httpx

from mlstudio.schemes import Scheme
from mlstudio.schemes.bigsdb import cache_root

log = logging.getLogger(__name__)

BASE_URL = "https://www.cgmlst.org/ncs/schema"
REGISTRY_FILE = Path(__file__).with_name("cgmlst_org_registry.json")


def load_registry() -> list[dict]:
    """Return the bundled list of cgMLST.org schemes (organism / slug / counts)."""
    return json.loads(REGISTRY_FILE.read_text())["schemes"]


def _slug_to_key(slug: str) -> str:
    return f"{slug.lower()}_cgmlst_orgio"


def pull_cgmlst_org_scheme(
    slug: str,
    organism: str | None = None,
    cluster_threshold: int = 5,
    cache_dir: Path | None = None,
    force: bool = False,
) -> Scheme:
    """Download and unpack a cgMLST.org scheme into the local cache."""
    key = _slug_to_key(slug)
    root = (cache_dir or cache_root()) / key
    manifest_path = root / "manifest.json"
    loci_dir = root / "loci"
    if manifest_path.exists() and not force:
        log.info("cgMLST.org scheme %s already cached at %s", slug, root)
        return Scheme.from_dir(root)
    loci_dir.mkdir(parents=True, exist_ok=True)

    if organism is None:
        for s in load_registry():
            if s["slug"] == slug:
                organism = s["organism"]
                break
        organism = organism or slug

    bundle_url = f"{BASE_URL}/{slug}/alleles/"
    log.info("Downloading cgMLST.org bundle for %s (%s)…", slug, bundle_url)
    with httpx.Client(timeout=httpx.Timeout(600.0), follow_redirects=True) as client:
        with client.stream("GET", bundle_url) as r:
            r.raise_for_status()
            buf = io.BytesIO()
            total = 0
            for chunk in r.iter_bytes(chunk_size=1 << 20):
                buf.write(chunk); total += len(chunk)
            log.info("  downloaded %.1f MB", total / 1e6)
            buf.seek(0)
        zf = zipfile.ZipFile(buf)
        hashes: dict[str, str] = {}
        loci: list[str] = []
        for member in zf.namelist():
            if not member.endswith(".fasta"):
                continue
            locus = member[: -len(".fasta")]
            content = zf.read(member)
            (loci_dir / f"{locus}.fasta").write_bytes(content)
            hashes[locus] = hashlib.sha256(content).hexdigest()
            loci.append(locus)
        loci.sort()

    # No central profiles table at cgMLST.org — schemes are loci-only.
    (root / "profiles.tsv").write_text("ST\t" + "\t".join(loci) + "\n")

    manifest = {
        "key": key,
        "organism": organism,
        "name": "cgMLST.org",
        "kind": "cgmlst",
        "cluster_threshold": cluster_threshold,
        "source": bundle_url,
        "slug": slug,
        "loci": loci,
        "locus_sha256": hashes,
        "records": 0,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    log.info("cgMLST.org %s ready (%d loci) at %s", slug, len(loci), root)
    return Scheme.from_dir(root)
