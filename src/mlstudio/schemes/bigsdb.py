"""BIGSdb / PubMLST REST API client.

Works against any BIGSdb instance — PubMLST.org, BIGSdb-Pasteur, etc. The hostname
is configurable. The scheme registry below maps friendly organism names to
(host, database, scheme_id) tuples for the most common organisms.
"""

from __future__ import annotations

import asyncio
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
    scheme_label: str  # e.g. "MLST" or "cgMLST"
    kind: str = "mlst"  # "mlst" | "cgmlst" | "accessory" | "amr"
    cluster_threshold: int = 0  # suggested default for MST cluster grouping


REGISTRY: dict[str, SchemeRef] = {
    # ---- MLST (7-gene) ----
    "lmonocytogenes_mlst": SchemeRef(
        organism="Listeria monocytogenes", host="https://bigsdb.pasteur.fr",
        database="pubmlst_listeria_seqdef", scheme_id=2, scheme_label="MLST",
        kind="mlst", cluster_threshold=0,
    ),
    "saureus_mlst": SchemeRef(
        organism="Staphylococcus aureus", host="https://rest.pubmlst.org",
        database="pubmlst_saureus_seqdef", scheme_id=1, scheme_label="MLST",
        kind="mlst", cluster_threshold=0,
    ),
    "ecoli_mlst": SchemeRef(
        organism="Escherichia coli", host="https://rest.pubmlst.org",
        database="pubmlst_ecoli_achtman_seqdef", scheme_id=1, scheme_label="MLST (Achtman)",
        kind="mlst", cluster_threshold=0,
    ),
    "kpneumoniae_mlst": SchemeRef(
        organism="Klebsiella pneumoniae", host="https://bigsdb.pasteur.fr",
        database="pubmlst_klebsiella_seqdef", scheme_id=1, scheme_label="MLST",
        kind="mlst", cluster_threshold=0,
    ),
    "efaecium_mlst": SchemeRef(
        organism="Enterococcus faecium", host="https://rest.pubmlst.org",
        database="pubmlst_efaecium_seqdef", scheme_id=1, scheme_label="MLST",
        kind="mlst", cluster_threshold=0,
    ),
    "abaumannii_mlst": SchemeRef(
        organism="Acinetobacter baumannii", host="https://rest.pubmlst.org",
        database="pubmlst_abaumannii_seqdef", scheme_id=2, scheme_label="MLST (Pasteur)",
        kind="mlst", cluster_threshold=0,
    ),
    "paeruginosa_mlst": SchemeRef(
        organism="Pseudomonas aeruginosa", host="https://rest.pubmlst.org",
        database="pubmlst_paeruginosa_seqdef", scheme_id=1, scheme_label="MLST",
        kind="mlst", cluster_threshold=0,
    ),
    # ---- cgMLST ----
    "lmonocytogenes_cgmlst": SchemeRef(
        organism="Listeria monocytogenes", host="https://bigsdb.pasteur.fr",
        database="pubmlst_listeria_seqdef", scheme_id=15, scheme_label="cgMLST1748_v2",
        kind="cgmlst", cluster_threshold=7,
    ),
    "saureus_cgmlst": SchemeRef(
        organism="Staphylococcus aureus", host="https://rest.pubmlst.org",
        database="pubmlst_saureus_seqdef", scheme_id=20, scheme_label="cgMLST",
        kind="cgmlst", cluster_threshold=5,
    ),
}


class BigsdbClient:
    """Thin client around a BIGSdb instance."""

    def __init__(self, host: str, timeout: float = 60.0) -> None:
        self.host = host.rstrip("/")
        self._client = httpx.Client(timeout=timeout, follow_redirects=True)

    def __enter__(self) -> BigsdbClient:
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


class AuthRequiredError(RuntimeError):
    """Raised when the remote API returns no alleles anonymously — typically
    because the curating institution gates cgMLST behind authentication."""


async def _pull_locus(client: httpx.AsyncClient, url: str, target: Path,
                      sem: asyncio.Semaphore, retries: int = 6) -> str:
    async with sem:
        for attempt in range(retries):
            try:
                r = await client.get(url)
                if r.status_code == 429:
                    # Respect Retry-After header if present, else exponential backoff.
                    ra = float(r.headers.get("retry-after", 0)) or (2 ** attempt)
                    await asyncio.sleep(min(60.0, max(2.0, ra)))
                    continue
                if r.status_code == 404:
                    # Distinguish "scheme has no public alleles" (auth-gated)
                    # from "transient 404 / retry". A 404 won't be fixed by retries.
                    body = r.text or ""
                    if "No alleles" in body or '"records":0' in body:
                        raise AuthRequiredError(body[:300])
                    raise httpx.HTTPStatusError(
                        f"404 for {url}", request=r.request, response=r)
                r.raise_for_status()
                target.write_text(r.text)
                return hashlib.sha256(r.text.encode()).hexdigest()
            except (AuthRequiredError, httpx.HTTPError):
                if attempt == retries - 1:
                    raise
                await asyncio.sleep(2.0 * (attempt + 1))
        return ""


async def _pull_scheme_async(ref: SchemeRef, root: Path, concurrency: int = 20) -> dict:
    loci_dir = root / "loci"
    loci_dir.mkdir(parents=True, exist_ok=True)

    timeout = httpx.Timeout(120.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        meta_url = f"{ref.host}/api/db/{ref.database}/schemes/{ref.scheme_id}"
        r = await client.get(meta_url)
        r.raise_for_status()
        meta = r.json()
        loci = [loc.rsplit("/", 1)[-1] for loc in meta["loci"]]

        # profiles_csv is optional — many large cgMLST schemes are loci-only
        # (no central ST registry). 404 here is normal; smaller schemes (7-gene
        # MLST) do have one. Either way write a stub so call_mlst can read it.
        prof_url = f"{ref.host}/api/db/{ref.database}/schemes/{ref.scheme_id}/profiles_csv"
        try:
            rp = await client.get(prof_url)
            if rp.status_code == 200:
                (root / "profiles.tsv").write_text(rp.text)
            else:
                log.info("No profiles_csv for %s (HTTP %d) — scheme is loci-only.",
                         ref.scheme_label, rp.status_code)
                (root / "profiles.tsv").write_text("ST\n")
        except httpx.HTTPError as e:
            log.info("profiles_csv fetch failed for %s: %s — writing stub.",
                     ref.scheme_label, e)
            (root / "profiles.tsv").write_text("ST\n")

        sem = asyncio.Semaphore(concurrency)

        # Probe the first locus synchronously. If THAT fails with the
        # auth-gated pattern, abort early instead of pelting the server with
        # 2700+ doomed requests.
        if loci:
            probe_url = f"{ref.host}/api/db/{ref.database}/loci/{loci[0]}/alleles_fasta"
            probe_target = loci_dir / f"{loci[0]}.fasta"
            if not (probe_target.exists() and probe_target.stat().st_size > 0):
                try:
                    await _pull_locus(client, probe_url, probe_target, sem, retries=2)
                except AuthRequiredError:
                    raise AuthRequiredError(
                        f"{ref.host} returned no public alleles for "
                        f"{ref.database!r} scheme {ref.scheme_id}. This scheme "
                        "is gated behind authentication (typical for curated "
                        "cgMLST nomenclatures). Either register at the host's "
                        "BIGSdb portal, or build an ad-hoc cgMLST scheme from "
                        "a reference genome:\n"
                        "  mlstudio schemes build-adhoc --reference REF.fasta "
                        "--key mygenus_v1 --organism 'My Genus species'"
                    )

        tasks = []
        for _i, locus in enumerate(loci):
            url = f"{ref.host}/api/db/{ref.database}/loci/{locus}/alleles_fasta"
            target = loci_dir / f"{locus}.fasta"
            if target.exists() and target.stat().st_size > 0:
                continue
            tasks.append((locus, _pull_locus(client, url, target, sem)))

        hashes: dict[str, str] = {}
        if tasks:
            log.info("Pulling %d loci (concurrency=%d)…", len(tasks), concurrency)
            results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)
            for (locus, _), res in zip(tasks, results, strict=False):
                if isinstance(res, Exception):
                    log.error("Failed locus %s: %s", locus, res)
                else:
                    hashes[locus] = res

        # Hash any pre-existing files (resume case)
        for locus in loci:
            if locus not in hashes:
                target = loci_dir / f"{locus}.fasta"
                if target.exists():
                    hashes[locus] = hashlib.sha256(target.read_bytes()).hexdigest()

    return {"meta": meta, "loci": loci, "hashes": hashes}


def pull_scheme(key: str, force: bool = False, cache_dir: Path | None = None,
                concurrency: int = 8) -> Scheme:
    """Download a scheme by registry key into the local cache. Idempotent."""
    if key not in REGISTRY:
        raise KeyError(f"Unknown scheme '{key}'. Available: {sorted(REGISTRY)}")

    ref = REGISTRY[key]
    root = (cache_dir or cache_root()) / key
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"

    if manifest_path.exists() and not force:
        log.info("Scheme %s already cached at %s", key, root)
        return Scheme.from_dir(root)

    log.info("Pulling scheme %s from %s", key, ref.host)
    result = asyncio.run(_pull_scheme_async(ref, root, concurrency=concurrency))

    manifest = {
        "key": key,
        "organism": ref.organism,
        "name": ref.scheme_label,
        "kind": ref.kind,
        "cluster_threshold": ref.cluster_threshold,
        "source": f"{ref.host}/api/db/{ref.database}/schemes/{ref.scheme_id}",
        "loci": result["loci"],
        "locus_sha256": result["hashes"],
        "last_updated": result["meta"].get("last_updated", ""),
        "records": result["meta"].get("records"),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return Scheme.from_dir(root)


async def _discover_one(client: httpx.AsyncClient, host: str) -> list[dict]:
    """Walk a BIGSdb host: list groups → seqdef DBs → schemes."""
    out: list[dict] = []
    try:
        rdb = await client.get(f"{host}/db", timeout=30.0)
        rdb.raise_for_status()
        groups = rdb.json()
    except Exception as e:
        log.warning("discover %s: %s", host, e)
        return out
    for grp in groups:
        organism = grp.get("description", "").strip()
        for db in grp.get("databases", []):
            name = db.get("name", "")
            if not name.endswith("_seqdef"):
                continue  # only sequence-definition DBs
            try:
                rs = await client.get(f"{host}/db/{name}/schemes", timeout=30.0)
                rs.raise_for_status()
                schemes = rs.json().get("schemes", [])
            except Exception:
                continue
            for s in schemes:
                desc = s.get("description", "").strip()
                sid = int(s["scheme"].rsplit("/", 1)[-1])
                kind = _classify_scheme(desc, name)
                out.append({
                    "organism": organism, "host": host,
                    "database": name, "scheme_id": sid,
                    "description": desc, "kind": kind,
                })
    return out


def _classify_scheme(description: str, db: str) -> str:
    d = description.lower()
    if "cgmlst" in d:
        return "cgmlst"
    if d == "mlst" or ("mlst" in d and "cgmlst" not in d and "core" not in d):
        return "mlst"
    if "virulence" in d or "resistance" in d or "accessory" in d:
        return "accessory"
    return "other"


def discover_remote_schemes(
    hosts: tuple[str, ...] = (
        "https://rest.pubmlst.org",
        "https://bigsdb.pasteur.fr/api",
    ),
) -> list[dict]:
    """One-shot discovery of every scheme exposed by the given BIGSdb hosts."""
    async def _go() -> list[dict]:
        results: list[dict] = []
        async with httpx.AsyncClient(follow_redirects=True) as client:
            for host in hosts:
                results.extend(await _discover_one(client, host))
        return results
    return asyncio.run(_go())


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


def paired_mlst_key(scheme_key: str) -> str | None:
    """Return the 7-gene MLST registry key paired with a cgMLST scheme, or None.

    The convention is that a cgMLST scheme named `<organism>_cgmlst(_orgio)`
    pairs with the BIGSdb-side MLST scheme `<organism>_mlst`. We try a few
    fallbacks so that, e.g., `kpneumoniae_complex_cgmlst_orgio` still maps to
    `kpneumoniae_mlst` (the cgMLST.org complex covers several Klebsiella
    species but the classical ST scheme is just one).
    """
    if scheme_key.endswith("_cgmlst_orgio"):
        prefix = scheme_key[: -len("_cgmlst_orgio")]
    elif scheme_key.endswith("_cgmlst"):
        prefix = scheme_key[: -len("_cgmlst")]
    else:
        return None
    candidates = [
        f"{prefix}_mlst",                  # straight rename
        f"{prefix.split('_')[0]}_mlst",    # drop "_complex" etc.
    ]
    for c in candidates:
        if c in REGISTRY:
            return c
    return None
