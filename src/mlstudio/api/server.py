"""FastAPI server: REST endpoints + WebSocket job progress + static frontend mount."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import uuid
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from mlstudio import __version__
import hashlib
import dataclasses

from mlstudio.amr.amrfinderplus import ORGANISM_MAP, amrfinder_available, run_amrfinderplus
from mlstudio.calling.cgmlst import call_cgmlst
from mlstudio.calling.fastp_wrapper import run_fastp
from mlstudio.calling.mlst import call_mlst, MLSTResult, AlleleCall
from mlstudio.io.scanner import Sample, scan
from mlstudio.profiles.distance import hamming_matrix
from mlstudio.profiles.mst import build_mst, mst_to_cytoscape
from mlstudio.schemes import Scheme
from mlstudio.schemes.bigsdb import (
    REGISTRY, AuthRequiredError, BigsdbClient, SchemeRef, _classify_scheme,
    cache_root, discover_remote_schemes, list_local, pull_scheme,
)
from mlstudio.schemes.cgmlst_org import (
    load_registry as load_cgmlst_org_registry,
    pull_cgmlst_org_scheme,
)

log = logging.getLogger(__name__)

# In-memory job/result store. For v1 this is plenty; persistence comes later.
JOBS: dict[str, "Job"] = {}


def projects_root() -> Path:
    """Where saved projects live."""
    return cache_root().parent / "projects"


def _slug_to_key_for_response(slug: str) -> str:
    return f"{slug.lower()}_cgmlst_orgio"


class Job:
    def __init__(self, job_id: str, folder: Path, scheme_key: str, threads: int,
                 use_fastp: bool, run_amr: bool = False,
                 output_folder: Path | None = None) -> None:
        self.id = job_id
        self.folder = folder
        self.scheme_key = scheme_key
        self.threads = threads
        self.use_fastp = use_fastp
        self.run_amr = run_amr
        self.output_folder = output_folder or (folder / ".mlstudio")
        self.status = "pending"
        self.progress = 0.0
        self.message = ""
        self.results: list[dict[str, Any]] = []
        self.amr_results: dict[str, list[dict[str, Any]]] = {}
        self.amr_warning: str | None = None
        self.mst: dict[str, Any] | None = None
        self.metadata: dict[str, dict[str, Any]] = {}
        self.profiles: dict[str, list[str | None]] = {}
        self.scheme_loci: list[str] = []
        self.scheme_cluster_threshold: int = 0
        self.distance_policy: str = "pairwise_complete"
        self.distance_matrix = None  # type: ignore[var-annotated]
        self.error: str | None = None
        self.subscribers: list[asyncio.Queue] = []

    def notify(self) -> None:
        for q in list(self.subscribers):
            try:
                q.put_nowait(self.snapshot())
            except asyncio.QueueFull:
                pass

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "n_results": len(self.results),
            "error": self.error,
        }


class AnalyzeRequest(BaseModel):
    folder: str
    scheme: str = "lmonocytogenes_mlst"
    threads: int = 0
    use_fastp: bool = True
    run_amr: bool = False
    output_folder: str | None = None
    project_name: str | None = None
    min_identity: float | None = None
    min_coverage: float | None = None
    skip_st_lookup: bool = False


def _sample_to_dict(s: Sample) -> dict[str, Any]:
    return {
        "name": s.name,
        "assembly": str(s.assembly),
        "r1": str(s.r1) if s.r1 else None,
        "r2": str(s.r2) if s.r2 else None,
        "has_reads": s.has_reads,
        "notes": s.notes,
    }


async def _run_job(job: Job) -> None:
    try:
        job.status = "running"
        job.message = f"Loading scheme {job.scheme_key}…"
        job.notify()

        # 1. Make sure scheme is present
        local_dir = cache_root() / job.scheme_key
        if job.scheme_key in REGISTRY:
            scheme = pull_scheme(job.scheme_key)
        elif (local_dir / "manifest.json").exists():
            scheme = Scheme.from_dir(local_dir)
        else:
            raise RuntimeError(f"Scheme {job.scheme_key} not cached. Pull it first.")

        # 2. Scan folder
        samples = scan(job.folder)
        if not samples:
            raise RuntimeError(f"No FASTA files found in {job.folder}")

        # 3. Process each sample
        loop = asyncio.get_running_loop()
        executor = ProcessPoolExecutor(max_workers=max(1, job.threads or 4))

        try:
            for i, sample in enumerate(samples):
                job.message = f"[{i+1}/{len(samples)}] {sample.name}"
                job.progress = i / len(samples)
                job.notify()

                # Optional fastp QC
                if job.use_fastp and sample.has_reads:
                    fp_out = job.folder / ".mlstudio" / "fastp" / sample.name
                    try:
                        run_fastp(sample.r1, sample.r2, fp_out, sample.name,
                                  threads=min(4, job.threads or 4))
                    except Exception as exc:
                        log.warning("fastp failed for %s: %s", sample.name, exc)

                # Incremental cache: skip work if this assembly was already
                # called against this scheme version. Key = path + size + mtime
                # + scheme manifest mtime.
                cache_dir = job.output_folder / "calls"
                ck = _sample_cache_key(sample, scheme)
                cached = _load_cached_result(cache_dir, ck)

                if cached is not None:
                    job.results.append(cached)
                    job.message = f"[{i+1}/{len(samples)}] {sample.name} (cached)"
                    job.notify()
                    continue

                # Call: cgMLST uses concatenated DB; MLST uses per-locus
                caller = call_cgmlst if scheme.kind == "cgmlst" else call_mlst
                t = max(1, job.threads or 4)
                if scheme.kind == "cgmlst":
                    result = await loop.run_in_executor(
                        executor, caller, sample.assembly, scheme, t,
                    )
                else:
                    result = await loop.run_in_executor(
                        executor, caller, sample.assembly, scheme, None, max(1, t // 2),
                    )

                # Optional AMRFinderPlus pass (display only — never feeds the MST)
                if job.run_amr:
                    if not amrfinder_available():
                        # Surface the skip loudly and visibly so the user knows
                        # why AMR=0 across the board instead of debugging the
                        # GUI. Add it once to the job message; per-sample notes
                        # don't exist for AMR.
                        if not getattr(job, "_amr_unavail_logged", False):
                            log.warning(
                                "Run AMR requested but `amrfinder` is not on PATH. "
                                "Install with `conda install -c bioconda ncbi-amrfinderplus`."
                            )
                            job._amr_unavail_logged = True
                            # Persist the warning on the job snapshot so the UI
                            # can show it.
                            job.amr_warning = (
                                "AMR scan was requested but the `amrfinder` "
                                "binary isn't installed. Install with "
                                "`conda install -c bioconda ncbi-amrfinderplus` "
                                "and then `amrfinder -u` to fetch the database."
                            )
                    else:
                        org_prefix = job.scheme_key.split("_")[0]
                        org = ORGANISM_MAP.get(org_prefix)
                        try:
                            amr = await loop.run_in_executor(
                                executor, run_amrfinderplus, sample.assembly, org,
                                min(4, job.threads or 4), None,
                            )
                            # Use the canonical AmrHit field names verbatim so
                            # the frontend's AMR matrix can read them without
                            # mapping. Previous code shipped truncated keys
                            # (gene/class/pident) that the matrix didn't know.
                            job.amr_results[sample.name] = [
                                {
                                    "gene_symbol": h.gene_symbol,
                                    "sequence_name": h.sequence_name,
                                    "scope": h.scope,
                                    "element_type": h.element_type,
                                    "element_subtype": h.element_subtype,
                                    "class_": h.class_,
                                    "subclass": h.subclass,
                                    "method": h.method,
                                    "percent_identity": h.percent_identity,
                                    "percent_coverage": h.percent_coverage,
                                }
                                for h in amr.hits
                            ]
                        except Exception as e:
                            log.warning("AMRFinderPlus failed for %s: %s", sample.name, e)
                result_dict = {
                    "sample": result.sample,
                    "st": result.st,
                    "scheme": result.scheme,
                    "calls": {loc: asdict(c) for loc, c in result.calls.items()},
                    "notes": result.notes,
                    "input": _sample_to_dict(sample),
                }
                job.results.append(result_dict)
                _save_cached_result(cache_dir, ck, result_dict)
        finally:
            executor.shutdown(wait=True)

        # 4. Distance + MST
        profiles = {
            r["sample"]: [r["calls"][loc]["allele"] for loc in scheme.loci]
            for r in job.results
        }
        job.profiles = profiles  # keep for recompute
        job.scheme_loci = list(scheme.loci)
        job.scheme_cluster_threshold = scheme.cluster_threshold
        if len(profiles) >= 2:
            # Collapse identical genotypes into one representative node.
            merged, members_by_rep = _merge_identical(profiles)
            dm = hamming_matrix(merged, policy="pairwise_complete")
            job.distance_matrix = dm  # kept for non-tree edge requests
            mst = build_mst(dm)
            st_by_sample = {r["sample"]: r["st"] for r in job.results}
            # Default non-tree connection cutoff: 2× cluster threshold (or 10).
            nt_cut = max(10, scheme.cluster_threshold * 2)
            nt_pairs = dm.pairs_under(nt_cut)
            job.mst = mst_to_cytoscape(
                mst, job.metadata or None, st_by_sample,
                cluster_threshold=scheme.cluster_threshold,
                members_by_rep=members_by_rep,
                non_tree_pairs=nt_pairs,
            )
        else:
            sname = next(iter(profiles.keys()))
            job.mst = {"elements": [{"data": {
                "id": sname, "label": sname,
                "st": job.results[0]["st"], "cluster_id": "C1",
            }}]}

        # Write outputs to disk if requested
        job.output_folder.mkdir(parents=True, exist_ok=True)
        _write_outputs(job, scheme)

        job.status = "done"
        job.progress = 1.0
        job.message = f"Analyzed {len(samples)} sample(s)"
    except Exception as exc:
        log.exception("Job %s failed", job.id)
        job.status = "error"
        job.error = str(exc)
        job.message = f"Error: {exc}"
    finally:
        job.notify()


def create_app() -> FastAPI:
    app = FastAPI(title="MLSTudio", version=__version__)

    # ---- meta -----------------------------------------------------------
    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "version": __version__,
            "author": "Giovanni Lorenzin",
            "byline": "Developed by Giovanni Lorenzin",
        }

    @app.get("/api/schemes")
    async def schemes() -> dict[str, Any]:
        local = list_local()
        local_by_key = {Path(s.root).name: s for s in local}
        out: list[dict[str, Any]] = []
        # Built-in registry entries
        for k, r in REGISTRY.items():
            out.append({
                "key": k, "organism": r.organism, "scheme": r.scheme_label,
                "host": r.host, "kind": r.kind,
                "cluster_threshold": r.cluster_threshold,
                "cached": k in local_by_key, "adhoc": False,
            })
        # Ad-hoc (local-only) schemes not in registry
        for k, s in local_by_key.items():
            if k in REGISTRY:
                continue
            out.append({
                "key": k, "organism": s.organism, "scheme": s.name,
                "host": "local", "kind": s.kind,
                "cluster_threshold": s.cluster_threshold,
                "cached": True, "adhoc": True,
            })
        return {"registry": out}

    # ---- discoverable scheme browser ------------------------------------
    # Define discover routes BEFORE /{key}/pull so they don't get
    # swallowed by the wildcard path parameter.
    _discovery_cache: dict[str, Any] = {}

    @app.get("/api/schemes/discover")
    async def schemes_discover(refresh: bool = False) -> dict[str, Any]:
        """Live-discover every scheme on PubMLST.org and BIGSdb-Pasteur,
        plus our static cgMLST.org registry (40 organisms, anonymous download)."""
        if refresh or "schemes" not in _discovery_cache:
            loop = asyncio.get_running_loop()
            schemes = await loop.run_in_executor(None, discover_remote_schemes)
            # Inject cgMLST.org schemes from the bundled registry
            for s in load_cgmlst_org_registry():
                schemes.append({
                    "organism": s["organism"],
                    "host": "https://www.cgmlst.org",
                    "database": s["slug"],
                    "scheme_id": 0,
                    "description": f"cgMLST.org · {s['locus_count']} loci · {s['ct_count']:,} CTs",
                    "kind": "cgmlst",
                    "source": "cgmlst_org",
                })
            _discovery_cache["schemes"] = schemes
        return {"schemes": _discovery_cache["schemes"]}

    @app.post("/api/schemes/discover/pull")
    async def schemes_pull_discovered(payload: dict[str, Any]) -> dict[str, Any]:
        """Pull a scheme not already in the static REGISTRY by giving us its
        host / database / scheme_id / organism. Stored in the local cache and
        auto-appears in the catalog."""
        source = payload.get("source")
        # cgMLST.org schemes use a different code path (bulk ZIP download).
        if source == "cgmlst_org":
            slug = payload["database"]
            organism = payload.get("organism") or slug
            try:
                scheme = await asyncio.get_running_loop().run_in_executor(
                    None, pull_cgmlst_org_scheme, slug, organism, 5, None, False,
                )
            except Exception as e:
                raise HTTPException(500, f"cgMLST.org pull failed: {e}")
            return {"ok": True, "key": _slug_to_key_for_response(slug),
                    "name": scheme.name, "loci": len(scheme.loci)}

        host = payload["host"].rstrip("/")
        # Strip /api if present (BIGSdb-Pasteur returns /api/db URLs, our
        # bigsdb client adds /api back).
        if host.endswith("/api"):
            host = host[:-4]
        database = payload["database"]
        scheme_id = int(payload["scheme_id"])
        organism = payload["organism"]
        description = payload.get("description", "")
        kind = payload.get("kind") or _classify_scheme(description, database)
        # Build a stable key: try to derive the species from the database name
        # (e.g. pubmlst_klebsiella_seqdef -> klebsiella). Falls back to first
        # word of organism.
        import re as _re
        m = _re.match(r"^pubmlst_([a-z0-9]+)_seqdef$", database)
        species_slug = m.group(1) if m else organism.split()[0].lower()
        key = f"{species_slug}_{kind}_{scheme_id}"
        # Register on the fly
        REGISTRY[key] = SchemeRef(
            organism=organism, host=host, database=database,
            scheme_id=scheme_id, scheme_label=description or kind.upper(),
            kind=kind,
            cluster_threshold=5 if kind == "cgmlst" else 0,
        )
        try:
            scheme = await asyncio.get_running_loop().run_in_executor(
                None, pull_scheme, key, False, None, 8,
            )
        except AuthRequiredError as e:
            REGISTRY.pop(key, None)
            # 403 makes the failure mode unambiguous in the client + logs.
            raise HTTPException(403, str(e))
        except Exception as e:
            # Pop the bogus registry entry so the user can retry
            REGISTRY.pop(key, None)
            raise HTTPException(500, f"Pull failed: {e}")
        return {"ok": True, "key": key, "name": scheme.name, "loci": len(scheme.loci)}

    @app.post("/api/schemes/{key}/pull")
    async def schemes_pull(key: str) -> dict[str, Any]:
        scheme = pull_scheme(key)
        return {"ok": True, "name": scheme.name, "loci": scheme.loci}

    # ---- scanning -------------------------------------------------------
    @app.get("/api/scan")
    async def scan_folder(folder: str) -> dict[str, Any]:
        path = Path(folder).expanduser()
        if not path.is_dir():
            raise HTTPException(404, f"Not a directory: {folder}")
        samples = scan(path)
        return {
            "folder": str(path),
            "samples": [_sample_to_dict(s) for s in samples],
        }

    # ---- server-side filesystem browser ---------------------------------
    @app.get("/api/fs/list")
    async def fs_list(path: str = "~") -> dict[str, Any]:
        """List immediate children of a directory; used by the folder picker."""
        try:
            p = Path(path).expanduser().resolve()
        except Exception as e:
            raise HTTPException(400, f"Bad path: {e}")
        if not p.is_dir():
            raise HTTPException(404, f"Not a directory: {path}")
        entries: list[dict[str, Any]] = []
        try:
            children = sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))
        except PermissionError:
            raise HTTPException(403, "Permission denied")
        n_fasta = 0
        for c in children:
            if c.name.startswith("."):
                continue
            is_dir = c.is_dir()
            if is_dir:
                entries.append({"name": c.name, "path": str(c), "is_dir": True})
            else:
                name_l = c.name.lower()
                if name_l.endswith((".fasta", ".fa", ".fna",
                                   ".fasta.gz", ".fa.gz", ".fna.gz")):
                    n_fasta += 1
        parent = str(p.parent) if p.parent != p else None
        return {
            "path": str(p),
            "parent": parent,
            "entries": entries,
            "n_fasta_in_dir": n_fasta,
        }

    # ---- analyze --------------------------------------------------------
    @app.post("/api/analyze")
    async def analyze(req: AnalyzeRequest) -> dict[str, Any]:
        folder = Path(req.folder).expanduser()
        if not folder.is_dir():
            raise HTTPException(404, f"Not a directory: {req.folder}")
        # Accept any scheme in REGISTRY or any locally-cached one (e.g.
        # cgMLST.org pulls + ad-hoc schemes).
        if req.scheme not in REGISTRY:
            local_dir = cache_root() / req.scheme
            if not (local_dir / "manifest.json").exists():
                raise HTTPException(400, f"Unknown scheme: {req.scheme}")

        job_id = uuid.uuid4().hex[:8]
        job = Job(job_id, folder, req.scheme, req.threads, req.use_fastp)
        JOBS[job_id] = job
        asyncio.create_task(_run_job(job))
        return {"job_id": job_id}

    @app.post("/api/jobs/{job_id}/recompute")
    async def job_recompute(job_id: str, policy: str = "pairwise_complete") -> dict[str, Any]:
        """Recompute the distance matrix + MST with a different missing-data policy."""
        if policy not in ("pairwise_complete", "count_missing", "scaled", "missing_as_category"):
            raise HTTPException(400, f"Bad policy: {policy}")
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown job")
        if not job.profiles or len(job.profiles) < 2:
            raise HTTPException(400, "Not enough samples for MST recompute")
        merged, members_by_rep = _merge_identical(job.profiles)
        dm = hamming_matrix(merged, policy=policy)
        job.distance_matrix = dm
        mst = build_mst(dm)
        st_by_sample = {r["sample"]: r["st"] for r in job.results}
        nt_cut = max(10, job.scheme_cluster_threshold * 2)
        nt_pairs = dm.pairs_under(nt_cut)
        job.mst = mst_to_cytoscape(
            mst, job.metadata or None, st_by_sample,
            cluster_threshold=job.scheme_cluster_threshold,
            members_by_rep=members_by_rep,
            non_tree_pairs=nt_pairs,
        )
        job.distance_policy = policy
        return {"ok": True, "policy": policy, "mst": job.mst}

    # ---- Projects (saved named runs) ------------------------------------
    @app.get("/api/projects")
    async def projects_list() -> dict[str, Any]:
        """All previously saved projects, newest first."""
        root = projects_root()
        if not root.is_dir():
            return {"projects": []}
        items: list[dict[str, Any]] = []
        for d in root.iterdir():
            m = d / "manifest.json"
            if not m.exists():
                continue
            try:
                items.append({**json.loads(m.read_text()), "path": str(d)})
            except Exception:
                pass
        items.sort(key=lambda p: p.get("created_at", ""), reverse=True)
        return {"projects": items}

    @app.post("/api/jobs/{job_id}/save")
    async def save_job_as_project(job_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Persist a finished job under a friendly name."""
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown job")
        if job.status != "done":
            raise HTTPException(400, f"Job not finished (status={job.status})")
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name required")
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
        target = projects_root() / safe
        target.mkdir(parents=True, exist_ok=True)
        # Save results, MST, metadata
        (target / "results.json").write_text(json.dumps(job.results))
        if job.mst:
            (target / "mst.json").write_text(json.dumps(job.mst))
        if job.metadata:
            (target / "metadata.json").write_text(json.dumps(job.metadata))
        if job.amr_results:
            (target / "amr.json").write_text(json.dumps(job.amr_results))
        # Manifest
        from datetime import datetime
        manifest = {
            "name": name, "safe_name": safe,
            "folder": str(job.folder), "scheme_key": job.scheme_key,
            "n_samples": len(job.results),
            "scheme_loci": len(job.scheme_loci),
            "scheme_cluster_threshold": job.scheme_cluster_threshold,
            "distance_policy": job.distance_policy,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        (target / "manifest.json").write_text(json.dumps(manifest, indent=2))
        return {"ok": True, "name": name, "path": str(target)}

    @app.get("/api/projects/{name}")
    async def project_load(name: str) -> dict[str, Any]:
        """Hydrate a saved project back into a snapshot the UI can render."""
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
        d = projects_root() / safe
        if not d.is_dir():
            raise HTTPException(404, "Unknown project")
        manifest = json.loads((d / "manifest.json").read_text())
        results = json.loads((d / "results.json").read_text()) if (d / "results.json").exists() else []
        mst = json.loads((d / "mst.json").read_text()) if (d / "mst.json").exists() else None
        metadata = json.loads((d / "metadata.json").read_text()) if (d / "metadata.json").exists() else {}
        amr = json.loads((d / "amr.json").read_text()) if (d / "amr.json").exists() else {}
        return {
            "manifest": manifest, "results": results, "mst": mst,
            "metadata": metadata, "amr": amr,
        }

    @app.delete("/api/projects/{name}")
    async def project_delete(name: str) -> dict[str, Any]:
        import shutil as _sh
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
        d = projects_root() / safe
        if not d.is_dir():
            raise HTTPException(404, "Unknown project")
        _sh.rmtree(d)
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/stats")
    async def job_stats(job_id: str) -> dict[str, Any]:
        """Return distance-distribution + cluster summary for the Statistics tab."""
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown job")
        if job.distance_matrix is None:
            return {"empty": True}
        mat = job.distance_matrix.matrix
        n = job.distance_matrix.n
        if n < 2:
            return {"n_samples": n}
        upper = []
        for i in range(n):
            for j in range(i + 1, n):
                upper.append(int(mat[i, j]))
        upper.sort()
        # LNF/INF/EXC totals from job.results
        exc = inf = lnf = 0
        for r in job.results:
            for c in r["calls"].values():
                if c["flag"] == "EXC": exc += 1
                elif c["flag"] == "INF": inf += 1
                elif c["flag"] == "LNF": lnf += 1
        nloci = len(job.scheme_loci)
        return {
            "n_samples": n,
            "n_loci": nloci,
            "exc": exc, "inf": inf, "lnf": lnf,
            "mean_lnf_per_sample": lnf / max(1, n),
            "missing_pct": 100.0 * lnf / max(1, exc + inf + lnf),
            "distance": {
                "min": upper[0],
                "p25": upper[len(upper) // 4],
                "median": upper[len(upper) // 2],
                "p75": upper[3 * len(upper) // 4],
                "max": upper[-1],
            },
            "histogram": _histogram(upper, bins=30),
        }

    @app.get("/api/jobs/{job_id}")
    async def job_status(job_id: str) -> dict[str, Any]:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown job")
        snap = job.snapshot()
        snap["results"] = job.results
        snap["mst"] = job.mst
        snap["amr"] = job.amr_results
        snap["amr_warning"] = job.amr_warning
        snap["output_folder"] = str(job.output_folder)
        return snap

    @app.websocket("/api/jobs/{job_id}/ws")
    async def job_ws(websocket: WebSocket, job_id: str) -> None:
        await websocket.accept()
        job = JOBS.get(job_id)
        if not job:
            await websocket.send_json({"error": "unknown job"})
            await websocket.close()
            return
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        job.subscribers.append(q)
        try:
            await websocket.send_json(job.snapshot())
            while True:
                snap = await q.get()
                await websocket.send_json(snap)
                if snap["status"] in ("done", "error"):
                    break
        except WebSocketDisconnect:
            pass
        finally:
            if q in job.subscribers:
                job.subscribers.remove(q)

    # ---- metadata upload ------------------------------------------------
    @app.post("/api/jobs/{job_id}/metadata")
    async def upload_metadata(job_id: str, file: UploadFile) -> dict[str, Any]:
        """Upload a CSV/TSV. First column = sample name, remaining columns = metadata fields."""
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown job")
        text = (await file.read()).decode("utf-8", errors="replace")
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;")
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        meta: dict[str, dict[str, Any]] = {}
        for row in reader:
            keys = list(row.keys())
            if not keys:
                continue
            name = row[keys[0]]
            meta[name] = {k: row[k] for k in keys[1:] if k}
        job.metadata = meta
        # Re-color the MST
        if job.mst:
            for el in job.mst.get("elements", []):
                if "source" in el["data"]:
                    continue
                if el["data"]["id"] in meta:
                    el["data"].update(meta[el["data"]["id"]])
        return {"ok": True, "fields": _metadata_fields(meta), "n_samples": len(meta)}

    @app.get("/api/jobs/{job_id}/metadata/fields")
    async def metadata_fields(job_id: str) -> dict[str, Any]:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown job")
        return {"fields": _metadata_fields(job.metadata)}

    # ---- static frontend ------------------------------------------------
    frontend_dir = Path(__file__).parent.parent / "webui"
    if frontend_dir.is_dir():
        app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

        @app.get("/")
        async def index():
            # Rewrite the static-asset URLs with a per-version query string so
            # browsers reliably pick up new app.js / style.css after a release
            # instead of serving the cached previous version. Falls back to
            # serving the raw file if templating fails for any reason.
            try:
                html = (frontend_dir / "index.html").read_text()
                html = html.replace('/static/app.js',
                                    f'/static/app.js?v={__version__}')
                html = html.replace('/static/style.css',
                                    f'/static/style.css?v={__version__}')
                from fastapi.responses import HTMLResponse
                return HTMLResponse(html, headers={"Cache-Control": "no-store"})
            except Exception:
                return FileResponse(frontend_dir / "index.html")

    return app


def _histogram(vals: list[int], bins: int = 30) -> dict[str, list[int]]:
    if not vals:
        return {"bins": [], "counts": []}
    lo, hi = vals[0], vals[-1]
    if lo == hi:
        return {"bins": [lo], "counts": [len(vals)]}
    width = max(1, (hi - lo + bins - 1) // bins)
    edges = [lo + i * width for i in range(bins + 1)]
    counts = [0] * bins
    for v in vals:
        idx = min((v - lo) // width, bins - 1)
        counts[idx] += 1
    return {"bins": edges, "counts": counts}


def _sample_cache_key(sample: Sample, scheme: Scheme) -> str:
    """Fingerprint that changes only when the assembly or scheme changes."""
    st = sample.assembly.stat()
    scheme_hash = scheme.manifest_path.stat().st_mtime_ns if scheme.manifest_path.exists() else 0
    raw = f"{sample.assembly.resolve()}|{st.st_size}|{st.st_mtime_ns}|{scheme.name}|{scheme_hash}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _load_cached_result(cache_dir: Path, key: str) -> dict[str, Any] | None:
    p = cache_dir / f"{key}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _save_cached_result(cache_dir: Path, key: str, result: dict[str, Any]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{key}.json").write_text(json.dumps(result))


def _merge_identical(
    profiles: dict[str, list[str | None]],
) -> tuple[dict[str, list[str | None]], dict[str, list[str]]]:
    """Group samples with byte-identical allele profiles.

    Returns (merged_profiles_by_rep, members_by_rep). Representative = first
    sample (alphabetical) in each group.
    """
    groups: dict[tuple, list[str]] = {}
    for sample in sorted(profiles):
        prof = tuple(profiles[sample])
        groups.setdefault(prof, []).append(sample)
    merged: dict[str, list[str | None]] = {}
    members_by_rep: dict[str, list[str]] = {}
    for prof, samples in groups.items():
        rep = samples[0]
        merged[rep] = list(prof)
        members_by_rep[rep] = samples
    return merged, members_by_rep


def _metadata_fields(meta: dict[str, dict[str, Any]]) -> list[str]:
    fields: set[str] = set()
    for v in meta.values():
        fields.update(v.keys())
    return sorted(fields)


def _write_outputs(job: Job, scheme: Scheme) -> None:
    """Write results.tsv, summary.json and mst.json to the job's output folder."""
    out = job.output_folder
    # Summary CSV (one row per sample)
    summary_lines = ["sample\tst\texc\tinf\tlnf\tn_loci\tnotes"]
    for r in job.results:
        exc = sum(1 for c in r["calls"].values() if c["flag"] == "EXC")
        inf = sum(1 for c in r["calls"].values() if c["flag"] == "INF")
        lnf = sum(1 for c in r["calls"].values() if c["flag"] == "LNF")
        summary_lines.append(
            f"{r['sample']}\t{r['st'] or ''}\t{exc}\t{inf}\t{lnf}\t{len(scheme.loci)}\t{'; '.join(r['notes'])}"
        )
    (out / "summary.tsv").write_text("\n".join(summary_lines) + "\n")

    # Full per-locus allele table (only when scheme isn't too wide)
    if len(scheme.loci) <= 200:
        header = "sample\tst\t" + "\t".join(scheme.loci)
        rows = [header]
        for r in job.results:
            cells = [r["calls"][loc]["allele"] or "0" for loc in scheme.loci]
            rows.append(f"{r['sample']}\t{r['st'] or ''}\t" + "\t".join(cells))
        (out / "alleles.tsv").write_text("\n".join(rows) + "\n")

    # MST as JSON for re-use
    if job.mst:
        (out / "mst.json").write_text(json.dumps(job.mst, indent=2))

    # AMR results if any
    if job.amr_results:
        amr_lines = ["sample\tgene\tclass\tsubclass\tmethod\tpident\tpcov"]
        for sample, hits in job.amr_results.items():
            for h in hits:
                amr_lines.append(f"{sample}\t{h['gene']}\t{h['class']}\t{h['subclass']}\t{h['method']}\t{h['pident']}\t{h['pcov']}")
        (out / "amr.tsv").write_text("\n".join(amr_lines) + "\n")
