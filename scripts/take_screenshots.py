"""Headless-browser screenshot generator for the MLSTudio WebUI.

Connects to a running mlstudio gui server, drives it through a sample workflow,
and captures publication-ready PNGs into docs/screens/.

Usage:
    mlstudio gui --no-open-browser --port 8765 &
    python scripts/take_screenshots.py \
        --base-url http://127.0.0.1:8765 \
        --job-id <existing_completed_job_id>     # optional: replay this job's MST
        --out docs/screens
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx
from playwright.async_api import async_playwright


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8765")
    ap.add_argument("--job-id", default=None,
                    help="If set, inject this job's MST into the running UI for shots.")
    ap.add_argument("--from-disk", default=None,
                    help="Path to a job output folder containing mst.json + summary.tsv.")
    ap.add_argument("--out", default="docs/screens")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=1000)
    ap.add_argument("--suffix", default="_v5",
                    help="Append to filenames (cache-busting for the README).")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sfx = args.suffix

    # Pre-fetch job state via HTTP or load from disk
    injected = None
    if args.job_id:
        async with httpx.AsyncClient(base_url=args.base_url) as client:
            r = await client.get(f"/api/jobs/{args.job_id}")
            r.raise_for_status()
            injected = r.json()
            assert injected["status"] == "done", f"Job not finished: {injected['status']}"
            print(f"Loaded job {args.job_id}: {injected['n_results']} samples")
    elif args.from_disk:
        disk = Path(args.from_disk)
        mst = json.loads((disk / "mst.json").read_text())
        # Reconstruct minimal results shape from summary.tsv
        summary_lines = (disk / "summary.tsv").read_text().splitlines()
        header = summary_lines[0].split("\t")
        results = []
        for line in summary_lines[1:]:
            cols = line.split("\t")
            row = dict(zip(header, cols))
            exc = int(row.get("exc", 0)); inf = int(row.get("inf", 0))
            lnf = int(row.get("lnf", 0)); n = int(row.get("n_loci", 0))
            # Build a synthetic per-locus dict so the table renderer works
            calls = {}
            calls.update({f"loc{i}": {"allele": "*", "flag": "EXC"} for i in range(min(exc, 5))})
            calls.update({f"loc{i}": {"allele": "*~", "flag": "INF"} for i in range(min(inf, 3))})
            results.append({
                "sample": row["sample"], "st": row.get("st") or None,
                "calls": calls, "notes": [row.get("notes", "")],
                "exc": exc, "inf": inf, "lnf": lnf, "n_loci": n,
            })
        injected = {"results": results, "mst": mst, "n_results": len(results)}
        print(f"Loaded from disk: {len(results)} samples")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": args.width, "height": args.height},
            device_scale_factor=2,
        )
        page = await ctx.new_page()

        # 1. Welcome screen — set scheme dropdown so it reads correctly in all shots
        await page.goto(args.base_url)
        await page.wait_for_function(
            "document.querySelectorAll('#scheme-select option').length > 0",
        )
        # Prefer saureus_cgmlst when present, otherwise first cached cgMLST
        await page.evaluate(
            """
            (() => {
              const sel = document.getElementById('scheme-select');
              const prefer = ['saureus_cgmlst', 'lmonocytogenes_cgmlst', 'saureus_mlst'];
              for (const p of prefer) {
                const opt = [...sel.options].find(o => o.value === p);
                if (opt) { sel.value = p; sel.dispatchEvent(new Event('change')); break; }
              }
              const folder = document.getElementById('folder-input');
              folder.value = '/home/iowa/Desktop/MORI/test/';
              document.getElementById('cluster-threshold').value = 5;
            })()
            """
        )
        await page.wait_for_timeout(400)
        await page.screenshot(path=str(out / f"01_welcome{sfx}.png"), full_page=False)
        print("  ✓ 01_welcome.png")

        # 2. Folder browser modal
        await page.evaluate("openBrowse('/home/iowa/Desktop')")
        await page.wait_for_timeout(700)
        await page.screenshot(path=str(out / f"02_browse{sfx}.png"), full_page=False)
        print("  ✓ 02_browse.png")
        await page.evaluate("document.getElementById('browse-modal').classList.add('hidden')")

        # 2b. Discover scheme modal
        await page.evaluate("openDiscover()")
        # Wait for discovery to populate (HTTP call takes ~5-10s)
        await page.wait_for_function(
            "document.querySelectorAll('#discover-table tbody tr').length > 5",
            timeout=30000,
        )
        await page.evaluate(
            "document.getElementById('discover-search').value = 'aureus';"
            "document.getElementById('discover-search').dispatchEvent(new Event('input'));"
        )
        await page.wait_for_timeout(500)
        await page.screenshot(path=str(out / f"02b_discover{sfx}.png"), full_page=False)
        print("  ✓ 02b_discover.png")
        await page.evaluate("document.getElementById('discover-modal').classList.add('hidden')")

        if injected:
            await page.evaluate(
                """
                ({results, mst}) => {
                    state.jobId = 'replayed';
                    state.results = results;
                    state.mst = mst;
                    // Honor scheme cluster threshold from select
                    const t = parseInt(document.getElementById('cluster-threshold').value) || 5;
                    state.schemeClusterThreshold = t;
                    state.clusterThreshold = t;
                    const nodes = mst.elements.filter(e => !e.data.source);
                    if (!nodes.some(n => n.data.cluster_id)) {
                        attachClusterIds(mst, t);
                    }
                    const anySt = nodes.some(n => n.data.st);
                    state.metaFields = anySt ? ['st', 'cluster_id'] : ['cluster_id', 'st'];
                    populateColorFields();
                    document.getElementById('color-field').value = anySt ? 'st' : 'cluster_id';
                    document.getElementById('empty-state').classList.add('hidden');
                    renderComparisonTable();
                    renderMst();
                    document.getElementById('status-dot').className = 'dot done';
                    document.getElementById('status-text').textContent = `Analyzed ${results.length} samples`;
                }
                """,
                {"results": injected["results"], "mst": injected["mst"]},
            )
            # Let Cytoscape settle — fcose's "proof" quality needs more time
            await page.wait_for_timeout(5000)

            # Hide results panel for a clean MST shot
            await page.evaluate(
                "document.getElementById('results-panel').classList.add('hidden')"
            )
            await page.wait_for_timeout(300)
            await page.screenshot(path=str(out / f"03_mst{sfx}.png"), full_page=False)
            print("  ✓ 03_mst.png")

            # Cluster halos at a threshold that produces multiple visible
            # nebulas on this dataset (S. aureus cgMLST median edge ~ 43).
            await page.evaluate(
                """
                const t = 32;
                document.getElementById('cluster-threshold').value = t;
                document.getElementById('cluster-threshold').dispatchEvent(new Event('input'));
                // Also turn on non-tree connections to demo
                document.getElementById('show-nontree').checked = true;
                document.getElementById('show-nontree').dispatchEvent(new Event('change'));
                """
            )
            await page.wait_for_timeout(2200)
            await page.screenshot(path=str(out / f"04_clusters{sfx}.png"), full_page=False)
            print("  ✓ 04_clusters.png")

            # Comparison Table tab
            await page.evaluate(
                "document.querySelector('.tab[data-tab=table]').click()"
            )
            await page.wait_for_timeout(800)
            await page.screenshot(path=str(out / f"06_table{sfx}.png"), full_page=False)
            print("  ✓ 06_table.png")

            # Statistics tab
            await page.evaluate(
                "document.querySelector('.tab[data-tab=stats]').click()"
            )
            await page.wait_for_timeout(1200)
            await page.screenshot(path=str(out / f"07_stats{sfx}.png"), full_page=False)
            print("  ✓ 07_stats.png")

            # Back to tree for final shot
            await page.evaluate(
                "document.querySelector('.tab[data-tab=tree]').click()"
            )
            await page.wait_for_timeout(700)

            # (Old bottom results panel removed — Comparison Table tab covers this.)

        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
