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

# Per-organism cgMLST outbreak thresholds, in alleles. These are the
# published / SeqSphere default cluster cutoffs used in clinical practice
# (Ruppitsch 2015 Lmono ≤7; Higgins 2019 Efaecium ≤3; Leopold 2014 Saureus
# ≤24; etc.). Used as the *default* cluster_threshold when pulling a
# scheme so the GUI's outbreak halo starts at a biologically sensible
# value instead of a one-size-fits-all 5.
ORGANISM_CLUSTER_THRESHOLD: dict[str, int] = {
    "Lmonocytogenes":        7,    # Ruppitsch 2015
    "Saureus":               24,   # SeqSphere / Leopold 2014
    "Sargenteus":            24,
    "Scapitis":              24,
    "Kpneumoniae_complex":   15,   # Ridom cgMLST.org default
    "Koxytoca_complex":      15,
    "Ecoli":                 10,   # SeqSphere default
    "Senterica":             10,
    "Efaecium":              3,    # Higgins 2019 (low diversity → tight cut)
    "Efaecalis":             5,
    "Abaumannii":            6,    # Higgins 2017
    "Paeruginosa":           7,
    "Cjejuni_complex":       12,
    "Cdifficile":            6,
    "Cperfringens":          5,
    "Cdiphtheriae":          25,
    "Csakazakii_complex":    10,
    "Cfreundii":             10,
    "Cfreundii_complex":     10,
    "Ehormaechei":           12,
    "Mtuberculosis_complex": 12,   # SNP-based usually but cgMLST roughly equivalent
    "Mabscessus":            25,
    "Spyogenes":             10,
    "Pmirabilis":            10,
    "Ftularensis":            6,
    "Yenterocolitica":       15,
    "Lpneumophila":          10,
    "Smarcescens":           10,
    "Bpertussis":            10,
    "Brucella":               6,
    "Bmelitensis":            6,
    "Bmallei_fli":            6,
    "Bmallei_rki":            6,
    "Bpseudomallei":          6,
    "Banthracis":             5,
}


def load_registry() -> list[dict]:
    """Return the bundled list of cgMLST.org schemes (organism / slug / counts)."""
    return json.loads(REGISTRY_FILE.read_text())["schemes"]


def _slug_to_key(slug: str) -> str:
    return f"{slug.lower()}_cgmlst_orgio"


def pull_cgmlst_org_scheme(
    slug: str,
    organism: str | None = None,
    cluster_threshold: int | None = None,
    cache_dir: Path | None = None,
    force: bool = False,
) -> Scheme:
    """Download and unpack a cgMLST.org scheme into the local cache.

    `cluster_threshold` defaults to the published per-organism value in
    ORGANISM_CLUSTER_THRESHOLD (5 if the slug is unknown). This is the
    initial outbreak-cluster threshold the GUI uses.
    """
    if cluster_threshold is None:
        cluster_threshold = ORGANISM_CLUSTER_THRESHOLD.get(slug, 5)
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
                buf.write(chunk)
                total += len(chunk)
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
