"""End-to-end smoke test against the local demo_folder.

Requires the conda env (blast, fastp, etc.) and the cached Listeria scheme.
Skipped automatically if the test data isn't present.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parents[1] / "test_data" / "demo_folder"


pytestmark = pytest.mark.skipif(not DEMO.is_dir(), reason="demo_folder not present")


def test_scanner_pairs_reads() -> None:
    from mlstudio.io.scanner import scan

    samples = scan(DEMO)
    by_name = {s.name: s for s in samples}
    assert "EGD-e" in by_name
    assert by_name["EGD-e"].has_reads, "EGD-e should have paired FASTQs detected"
    assert "10403S" in by_name
    assert not by_name["10403S"].has_reads  # FASTA only
    assert "F2365" in by_name


def test_call_mlst_egd_e() -> None:
    from mlstudio.calling.mlst import call_mlst
    from mlstudio.schemes.bigsdb import pull_scheme

    scheme = pull_scheme("lmonocytogenes_mlst")
    result = call_mlst(DEMO / "EGD-e.fasta", scheme, threads=4)
    assert result.st == "35", f"Expected EGD-e ST=35, got {result.st}"
    assert all(c.flag == "EXC" for c in result.calls.values())


def test_fastp_runs_on_simulated_reads(tmp_path: Path) -> None:
    from mlstudio.calling.fastp_wrapper import run_fastp

    r1 = DEMO / "EGD-e_R1.fastq.gz"
    r2 = DEMO / "EGD-e_R2.fastq.gz"
    result = run_fastp(r1, r2, tmp_path, "EGD-e", threads=2)
    assert result.r1_out.exists() and result.r1_out.stat().st_size > 0
    assert result.stats["reads_before"] == 100000  # 50k pairs
    assert result.profile_r1.encoding == "phred33"
    assert result.profile_r1.avg_read_length >= 140  # wgsim default 150bp


@pytest.mark.asyncio
async def test_api_full_pipeline() -> None:
    """Run analyze through the ASGI app and verify ST + MST round-trip."""
    import httpx

    from mlstudio.api.server import JOBS, create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/analyze", json={
            "folder": str(DEMO),
            "scheme": "lmonocytogenes_mlst",
            "threads": 4,
            "use_fastp": False,
        })
        assert r.status_code == 200
        job_id = r.json()["job_id"]

        snap = None
        for _ in range(180):
            await asyncio.sleep(1)
            snap = (await client.get(f"/api/jobs/{job_id}")).json()
            if snap["status"] in ("done", "error"):
                break
        assert snap is not None and snap["status"] == "done", snap

        results_by_sample = {r["sample"]: r for r in snap["results"]}
        # Known STs for these well-characterized Listeria reference strains
        assert results_by_sample["EGD-e"]["st"] == "35"
        assert results_by_sample["10403S"]["st"] == "85"
        assert results_by_sample["F2365"]["st"] == "1"
        assert snap["mst"] is not None
        node_ids = {el["data"]["id"] for el in snap["mst"]["elements"]
                    if "source" not in el["data"]}
        assert node_ids == {"EGD-e", "10403S", "F2365"}
        edges = [el for el in snap["mst"]["elements"] if "source" in el["data"]]
        assert len(edges) == 2  # n-1 edges for n=3 nodes in an MST
        for e in edges:
            assert e["data"]["weight"] > 0  # distinct STs => positive distance
    JOBS.clear()
