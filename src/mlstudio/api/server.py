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
from mlstudio.amr.amrfinderplus import ORGANISM_MAP, amrfinder_available, run_amrfinderplus
from mlstudio.calling.cgmlst import call_cgmlst
from mlstudio.calling.fastp_wrapper import run_fastp
from mlstudio.calling.mlst import call_mlst
from mlstudio.io.scanner import Sample, scan
from mlstudio.profiles.distance import hamming_matrix
from mlstudio.profiles.mst import build_mst, mst_to_cytoscape
from mlstudio.schemes import Scheme
from mlstudio.schemes.bigsdb import REGISTRY, list_local, pull_scheme

log = logging.getLogger(__name__)

# In-memory job/result store. For v1 this is plenty; persistence comes later.
JOBS: dict[str, "Job"] = {}


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
        self.mst: dict[str, Any] | None = None
        self.metadata: dict[str, dict[str, Any]] = {}
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
        scheme = pull_scheme(job.scheme_key)

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

                # Optional AMRFinderPlus pass (display only)
                if job.run_amr and amrfinder_available():
                    org_key = scheme.organism.split()[0].lower() + scheme.organism.split()[-1][0:0]
                    org_prefix = job.scheme_key.split("_")[0]
                    org = ORGANISM_MAP.get(org_prefix)
                    try:
                        amr = await loop.run_in_executor(
                            executor, run_amrfinderplus, sample.assembly, org,
                            min(4, job.threads or 4), None,
                        )
                        job.amr_results[sample.name] = [
                            {"gene": h.gene_symbol, "class": h.class_,
                             "subclass": h.subclass, "method": h.method,
                             "pident": h.percent_identity,
                             "pcov": h.percent_coverage}
                            for h in amr.hits
                        ]
                    except Exception as e:
                        log.warning("AMRFinderPlus failed for %s: %s", sample.name, e)
                job.results.append({
                    "sample": result.sample,
                    "st": result.st,
                    "scheme": result.scheme,
                    "calls": {loc: asdict(c) for loc, c in result.calls.items()},
                    "notes": result.notes,
                    "input": _sample_to_dict(sample),
                })
        finally:
            executor.shutdown(wait=True)

        # 4. Distance + MST
        profiles = {
            r["sample"]: [r["calls"][loc]["allele"] for loc in scheme.loci]
            for r in job.results
        }
        if len(profiles) >= 2:
            dm = hamming_matrix(profiles)
            mst = build_mst(dm)
            st_by_sample = {r["sample"]: r["st"] for r in job.results}
            job.mst = mst_to_cytoscape(
                mst, job.metadata or None, st_by_sample,
                cluster_threshold=scheme.cluster_threshold,
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
        return {"status": "ok", "version": __version__}

    @app.get("/api/schemes")
    async def schemes() -> dict[str, Any]:
        local = list_local()
        local_keys = {Path(s.root).name for s in local}
        return {
            "registry": [
                {
                    "key": k,
                    "organism": r.organism,
                    "scheme": r.scheme_label,
                    "host": r.host,
                    "kind": r.kind,
                    "cluster_threshold": r.cluster_threshold,
                    "cached": k in local_keys,
                }
                for k, r in REGISTRY.items()
            ],
        }

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
        if req.scheme not in REGISTRY:
            raise HTTPException(400, f"Unknown scheme: {req.scheme}")

        job_id = uuid.uuid4().hex[:8]
        job = Job(job_id, folder, req.scheme, req.threads, req.use_fastp)
        JOBS[job_id] = job
        asyncio.create_task(_run_job(job))
        return {"job_id": job_id}

    @app.get("/api/jobs/{job_id}")
    async def job_status(job_id: str) -> dict[str, Any]:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown job")
        snap = job.snapshot()
        snap["results"] = job.results
        snap["mst"] = job.mst
        snap["amr"] = job.amr_results
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
        async def index() -> FileResponse:
            return FileResponse(frontend_dir / "index.html")

    return app


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
