"""FastAPI server: REST endpoints + WebSocket job progress + static frontend mount."""

from __future__ import annotations

import asyncio
import csv
import hashlib
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
from mlstudio.schemes.bigsdb import (
    REGISTRY,
    AuthRequiredError,
    SchemeRef,
    _classify_scheme,
    cache_root,
    discover_remote_schemes,
    list_local,
    pull_scheme,
)
from mlstudio.schemes.cgmlst_org import (
    load_registry as load_cgmlst_org_registry,
)
from mlstudio.schemes.cgmlst_org import (
    pull_cgmlst_org_scheme,
)

log = logging.getLogger(__name__)

# In-memory job/result store. For v1 this is plenty; persistence comes later.
JOBS: dict[str, Job] = {}


def projects_root() -> Path:
    """Where saved projects live."""
    return cache_root().parent / "projects"


def _slug_to_key_for_response(slug: str) -> str:
    return f"{slug.lower()}_cgmlst_orgio"


class Job:
    def __init__(self, job_id: str, folder: Path, scheme_key: str, threads: int,
                 use_fastp: bool, run_amr: bool = False,
                 run_mlst: bool = True,
                 output_folder: Path | None = None) -> None:
        self.id = job_id
        self.folder = folder
        self.scheme_key = scheme_key
        self.threads = threads
        self.use_fastp = use_fastp
        self.run_amr = run_amr
        # run_mlst gates the auto-pairing of classical 7-gene MLST when the
        # primary scheme is cgMLST. Default on — the user can untick the
        # "Also compute classical MLST" checkbox to skip it.
        self.run_mlst = run_mlst
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
    run_mlst: bool = True
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

        # 1b. Auto-pair: when running a cgMLST scheme *and* the user ticked
        # "Also compute classical 7-gene MLST", also run the paired MLST so
        # they get a classical ST alongside the cgMLST profile. pull_scheme
        # is sync and uses asyncio.run() internally — call it from a thread
        # so it gets its own event loop instead of clashing with ours.
        mlst_companion: Scheme | None = None
        paired_key: str | None = None
        from mlstudio.schemes.bigsdb import paired_mlst_key
        if scheme.kind == "cgmlst" and job.run_mlst:
            paired_key = paired_mlst_key(job.scheme_key)
            if paired_key:
                try:
                    mlst_companion = await asyncio.get_running_loop().run_in_executor(
                        None, pull_scheme, paired_key,
                    )
                    job.message = (
                        f"Auto-paired classical MLST: {mlst_companion.name} "
                        f"({len(mlst_companion.loci)} loci)"
                    )
                    job.notify()
                except Exception as e:
                    log.warning("Could not auto-pair MLST scheme %s: %s", paired_key, e)
                    paired_key = None

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

                # Per-sample assembly QC: N50, total length, GC%, # contigs,
                # ambiguous-base count, plus a PASS / WARN / FAIL verdict.
                # Computed in one pass over the FASTA — fast even on big
                # batches and adds no new runtime deps.
                from mlstudio.qc.assembly import assembly_qc as _qc
                organism_slug = None
                if scheme.kind == "cgmlst":
                    # Derive the slug from the cgMLST.org key, e.g.
                    # "efaecium_cgmlst_orgio" → "Efaecium".
                    base = job.scheme_key.replace("_cgmlst_orgio", "").replace("_cgmlst", "")
                    organism_slug = base[:1].upper() + base[1:]
                sample_qc = _qc(sample.assembly, organism_slug=organism_slug)

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
                ck = _sample_cache_key(sample, scheme, mlst_paired=paired_key)
                cached = _load_cached_result(cache_dir, ck)

                if cached is not None:
                    job.results.append(cached)
                    # Make sure cached results also flow into the library —
                    # otherwise re-running on a folder full of cached samples
                    # would never repopulate the library on a fresh sqlite.
                    try:
                        from mlstudio.library import save_sample
                        save_sample(cached, folder=str(job.folder),
                                    scheme_key=job.scheme_key,
                                    organism=scheme.organism)
                    except Exception as e:
                        log.debug("library write skipped (cached) for %s: %s", sample.name, e)
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

                # When a cgMLST scheme has an auto-paired classical MLST, run
                # the 7-gene call too and graft the classical ST onto the
                # cgMLST result. The user gets *both* an ST and a cgST in the
                # GUI without ever having to launch two analyses.
                if mlst_companion is not None and scheme.kind == "cgmlst":
                    try:
                        mlst_res = await loop.run_in_executor(
                            executor, call_mlst, sample.assembly, mlst_companion,
                            None, max(1, t // 2),
                        )
                        result.mlst_scheme = mlst_companion.name
                        result.mlst_st = mlst_res.st
                        result.st = mlst_res.st            # primary ST column
                        # Pull clonal complex through from the companion
                        # MLST run if it found one — otherwise the cgMLST
                        # result has no CC info.
                        if mlst_res.clonal_complex:
                            result.clonal_complex = mlst_res.clonal_complex
                        result.notes.append(
                            f"MLST ({mlst_companion.name}): "
                            f"ST {mlst_res.st or '?'}"
                            + (f" / {mlst_res.clonal_complex}"
                               if mlst_res.clonal_complex else "")
                        )
                    except Exception as e:
                        log.warning("Companion MLST failed for %s: %s", sample.name, e)

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
                            # Run AMR in the default thread executor (None) rather
                            # than the shared cgmlst ProcessPoolExecutor.
                            # run_amrfinderplus is a subprocess wrapper that
                            # releases the GIL during subprocess.run, so threads
                            # are sufficient — and the process executor was
                            # silently dropping AmrResult unpickle errors,
                            # leaving job.amr_results empty.
                            amr = await loop.run_in_executor(
                                None, run_amrfinderplus, sample.assembly, org,
                                min(4, job.threads or 4), None,
                            )
                            log.info("AMR %s: %d hits%s", sample.name, len(amr.hits),
                                     f" (error: {amr.error})" if amr.error else "")
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
                            # Clinical interpretation: drug-class buckets +
                            # MRSA/VRE/ESBL/CPE flags + MDR/XDR proxy.
                            from mlstudio.amr.interpretation import interpret
                            summary = interpret(amr.hits, sample.name, org)
                            result.amr_flags = summary.flags
                            result.amr_classes = summary.drug_classes
                            result.amr_summary = summary.summary_line
                            if summary.flags:
                                result.notes.append(
                                    f"AMR phenotype flags: {', '.join(summary.flags)}"
                                )
                        except Exception as e:
                            log.warning("AMRFinderPlus failed for %s: %s", sample.name, e)
                result_dict = {
                    "sample": result.sample,
                    "st": result.st,
                    "scheme": result.scheme,
                    "cgst": result.cgst,           # 8-char hash
                    "cgst_id": None,                # sequential int filled in post-MST
                    "mlst_st": result.mlst_st,
                    "mlst_scheme": result.mlst_scheme,
                    "clonal_complex": result.clonal_complex,
                    "amr_flags": list(result.amr_flags),
                    "amr_classes": dict(result.amr_classes),
                    "amr_summary": result.amr_summary,
                    "qc": {
                        "verdict":      sample_qc.verdict,
                        "n_contigs":    sample_qc.n_contigs,
                        "total_length": sample_qc.total_length,
                        "longest_contig": sample_qc.longest_contig,
                        "n50":          sample_qc.n50,
                        "n90":          sample_qc.n90,
                        "gc_percent":   round(sample_qc.gc_percent, 2),
                        "n_count":      sample_qc.n_count,
                        "n_fraction":   round(sample_qc.n_fraction, 4),
                        "reasons":      sample_qc.reasons or [],
                    },
                    "calls": {loc: asdict(c) for loc, c in result.calls.items()},
                    "notes": result.notes,
                    "input": _sample_to_dict(sample),
                }
                job.results.append(result_dict)
                # Persist each result into the sample library so it can be
                # browsed / re-loaded later. This is best-effort — a failed
                # library write must never crash the analysis.
                try:
                    from mlstudio.library import save_sample
                    save_sample(result_dict, folder=str(job.folder),
                                scheme_key=job.scheme_key,
                                organism=scheme.organism)
                except Exception as e:
                    log.debug("library write skipped for %s: %s", sample.name, e)
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
            # Persistent local nomenclature: stable cgST integers + HC
            # cluster IDs that stay consistent across runs against this
            # scheme on this machine. NOT globally curated like Ridom CTs
            # or Enterobase HCs — those need a central server — but the
            # numbering is reproducible for the user's own lab work.
            from mlstudio.nomenclature import NomenclatureStore
            store = NomenclatureStore(job.scheme_key)
            cgst_int_by_sample: dict[str, int] = {}
            for r in job.results:
                h = r.get("cgst")
                if h:
                    cgst_int_by_sample[r["sample"]] = store.assign_cgst(h)
            # HierCC-style hierarchical cluster IDs (Enterobase nomenclature).
            # For each Enterobase-style threshold {0, 2, 5, 10, 25, 50}, find
            # connected components on the complete pairwise-distance graph
            # then push them through the nomenclature store so the numbers
            # stick across runs.
            hier_thresholds = [0, 2, 5, 10, 25, 50]
            hier_by_sample = _hier_clusters(
                dm, members_by_rep, hier_thresholds,
                store=store, cgst_int_by_sample=cgst_int_by_sample,
            )
            for r in job.results:
                r["hier"] = hier_by_sample.get(r["sample"], {})
                if r["sample"] in cgst_int_by_sample:
                    r["cgst_id"] = cgst_int_by_sample[r["sample"]]
            # Library second-pass save: the per-sample save earlier wrote
            # the result before the cgst_id / hier were known. Re-save now
            # with the post-MST fields filled in so the library shows the
            # cgST integer + HC10 cluster ID.
            try:
                from mlstudio.library import save_sample
                for r in job.results:
                    save_sample(r, folder=str(job.folder),
                                scheme_key=job.scheme_key,
                                organism=scheme.organism)
                log.info("Library: re-saved %d samples with post-MST fields",
                         len(job.results))
            except Exception as e:
                log.warning("Library second-pass save failed: %s", e)
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
        # NB: the run_amr / output_folder kwargs used to be silently dropped
        # here, which is why ticking "Run AMR gene scan" appeared to do nothing.
        job = Job(
            job_id, folder, req.scheme, req.threads, req.use_fastp,
            run_amr=req.run_amr,
            run_mlst=req.run_mlst,
            output_folder=Path(req.output_folder).expanduser() if req.output_folder else None,
        )
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

    # ---- Citations (published basis for thresholds + clinical flags) ----
    @app.get("/api/citations")
    async def citations_list() -> dict[str, Any]:
        """All literature references baked into the clinical interpretation."""
        from mlstudio.citations import CITATIONS, citation_link
        out: dict[str, Any] = {}
        for k, c in CITATIONS.items():
            out[k] = {**c, "url": citation_link(c)}
        return {"citations": out}

    # ---- Sample library (persistent per-isolate store) ------------------
    @app.get("/api/library")
    async def library_list(q: str | None = None, scheme: str | None = None,
                           organism: str | None = None,
                           flag: str | None = None, limit: int = 500) -> dict[str, Any]:
        """List previously analyzed samples, newest first."""
        from mlstudio.library import list_samples, stats
        return {
            "samples": list_samples(q=q, scheme=scheme, organism=organism,
                                    flag=flag, limit=limit),
            "stats": stats(),
        }

    @app.get("/api/library/{sample_key}")
    async def library_get(sample_key: str) -> dict[str, Any]:
        from mlstudio.library import get_sample
        snap = get_sample(sample_key)
        if not snap:
            raise HTTPException(404, "Unknown sample_key")
        return snap

    @app.delete("/api/library/{sample_key}")
    async def library_delete(sample_key: str) -> dict[str, Any]:
        from mlstudio.library import delete_sample
        if not delete_sample(sample_key):
            raise HTTPException(404, "Unknown sample_key")
        return {"ok": True}

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
                if c["flag"] == "EXC":
                    exc += 1
                elif c["flag"] == "INF":
                    inf += 1
                elif c["flag"] == "LNF":
                    lnf += 1
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
            # Rewrite the static-asset URLs with a per-file mtime cache-busting
            # token so every edit to app.js / style.css gets a fresh URL and
            # the browser can't serve a stale cached copy. (A fixed
            # ?v={__version__} was useless for our editable-install workflow:
            # every push since v1.3.0 has been the same version, so Chrome
            # served the cached JS regardless of Cache-Control headers.)
            try:
                html = (frontend_dir / "index.html").read_text()
                def _bust(name: str) -> str:
                    p = frontend_dir / name
                    return f"{int(p.stat().st_mtime)}" if p.exists() else "0"
                html = html.replace('/static/app.js',
                                    f'/static/app.js?v={_bust("app.js")}')
                html = html.replace('/static/style.css',
                                    f'/static/style.css?v={_bust("style.css")}')
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


# Bumped when the result_dict schema changes so old cached entries don't
# silently mask new fields.
#   v2 → cgst + mlst_st + mlst_scheme columns.
#   v3 → cgst_id + clonal_complex + HierCC hier + AMR interpretation flags
#         + per-sample assembly QC verdict.
_CALL_CACHE_VERSION = "v3"


def _sample_cache_key(sample: Sample, scheme: Scheme, *, mlst_paired: str | None = None) -> str:
    """Fingerprint that changes only when the assembly, scheme, or analysis
    surface changes. `mlst_paired` is the cache key of the auto-paired MLST
    scheme (or None when MLST pairing is off), so toggling the checkbox
    invalidates the cache rather than returning a stale MLST-less result."""
    st = sample.assembly.stat()
    scheme_hash = scheme.manifest_path.stat().st_mtime_ns if scheme.manifest_path.exists() else 0
    raw = (f"{_CALL_CACHE_VERSION}|{sample.assembly.resolve()}|"
           f"{st.st_size}|{st.st_mtime_ns}|{scheme.name}|{scheme_hash}|"
           f"mlst={mlst_paired or ''}")
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


def _hier_clusters(
    dm: Any,
    members_by_rep: dict[str, list[str]],
    thresholds: list[int],
    *,
    store: Any = None,
    cgst_int_by_sample: dict[str, int] | None = None,
) -> dict[str, dict[str, str]]:
    """Compute HierCC-style cluster IDs per sample at multiple thresholds.

    With a NomenclatureStore, cluster IDs persist across runs against the
    same scheme — an outbreak group keeps its number when you add more
    isolates later. Without a store, IDs are numbered fresh per run.
    """
    if dm is None or dm.n == 0:
        return {}
    samples = list(dm.samples)
    out: dict[str, dict[str, str]] = {s: {} for rep in members_by_rep for s in members_by_rep[rep]}
    cgst_int_by_sample = cgst_int_by_sample or {}

    def _find(parent: dict[str, str], x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for th in thresholds:
        # Union-find: connect reps whose pairwise distance ≤ threshold.
        parent = {s: s for s in samples}
        for i in range(dm.n):
            for j in range(i + 1, dm.n):
                if int(dm.matrix[i, j]) <= th:
                    a, b = _find(parent, samples[i]), _find(parent, samples[j])
                    if a != b:
                        parent[a] = b
        groups: dict[str, list[str]] = {}
        for s in samples:
            groups.setdefault(_find(parent, s), []).append(s)

        rep_to_id: dict[str, str] = {}
        if store is not None and cgst_int_by_sample:
            # Persistent IDs: each component is pushed through the store
            # which merges with any existing cluster that shares cgST IDs.
            for grp in groups.values():
                cgst_ids = [cgst_int_by_sample[s] for s in grp
                            if s in cgst_int_by_sample]
                if not cgst_ids:
                    continue
                cid = store.assign_cluster(th, cgst_ids)
                for rep in grp:
                    rep_to_id[rep] = str(cid)
        else:
            # Fresh-per-run numbering: largest cluster first.
            ordered = sorted(groups.values(), key=lambda g: (-len(g), min(g)))
            for idx, grp in enumerate(ordered, start=1):
                for rep in grp:
                    rep_to_id[rep] = str(idx)

        for rep, members in members_by_rep.items():
            cid = rep_to_id.get(rep, "?")
            for s in members:
                out[s][f"HC{th}"] = cid
    return out


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
        amr_lines = ["sample\tgene_symbol\tsequence_name\telement_type\tclass\tsubclass\tmethod\tpercent_identity\tpercent_coverage"]
        for sample, hits in job.amr_results.items():
            for h in hits:
                amr_lines.append("\t".join([
                    sample,
                    h.get("gene_symbol", ""),
                    h.get("sequence_name", ""),
                    h.get("element_type", ""),
                    h.get("class_", ""),
                    h.get("subclass", ""),
                    h.get("method", ""),
                    str(h.get("percent_identity", "")),
                    str(h.get("percent_coverage", "")),
                ]))
        (out / "amr.tsv").write_text("\n".join(amr_lines) + "\n")
