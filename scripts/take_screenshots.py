"""Headless-browser screenshot generator for the MLSTudio WebUI.

Connects to a running mlstudio gui server, replays a finished job into the
page state, and captures publication-ready PNGs into docs/screens/. Covers
all five tabs of the redesigned UI (Setup / MST / Table / AMR / Statistics)
plus the folder-browser and scheme-discovery modals.

Usage:
    mlstudio gui --no-open-browser --port 8765 &
    python scripts/take_screenshots.py \
        --base-url http://127.0.0.1:8765 \
        --job-id <existing_completed_job_id> \
        --out docs/screens

Or replay a saved project from disk:
    python scripts/take_screenshots.py \
        --from-project ~/.local/share/mlstudio/projects/Efaecium_VRE_test \
        --base-url http://127.0.0.1:8765
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
                    help="Pull this completed job's snapshot via HTTP and inject it.")
    ap.add_argument("--from-project", default=None,
                    help="Path to a saved project (cache_root/projects/<name>) — uses its mst.json + summary.tsv + amr.json + metadata.")
    ap.add_argument("--out", default="docs/screens")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=1000)
    ap.add_argument("--suffix", default="_v7",
                    help="Append to filenames (cache-busting for the README).")
    ap.add_argument("--cluster-threshold", type=int, default=5,
                    help="Slider value at which to capture the halo shot.")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sfx = args.suffix

    # ---- Load injected state ----------------------------------------------
    injected = None
    if args.job_id:
        async with httpx.AsyncClient(base_url=args.base_url) as client:
            r = await client.get(f"/api/jobs/{args.job_id}")
            r.raise_for_status()
            injected = r.json()
            assert injected["status"] == "done", f"Job not finished: {injected['status']}"
            print(f"Loaded job {args.job_id}: {injected['n_results']} samples")
    elif args.from_project:
        disk = Path(args.from_project).expanduser()
        if not (disk / "mst.json").is_file():
            print(f"No mst.json under {disk}", file=sys.stderr); return 1
        mst = json.loads((disk / "mst.json").read_text())
        # results.json carries the full per-sample structure the GUI needs
        # (sample, st, scheme, calls{locus: {allele, flag, ...}}, notes).
        results = json.loads((disk / "results.json").read_text())
        amr = {}
        amr_path = disk / "amr.json"
        if amr_path.exists():
            amr = json.loads(amr_path.read_text())
        injected = {"results": results, "mst": mst, "amr": amr,
                    "n_results": len(results)}
        print(f"Loaded project {disk.name}: {len(results)} samples, "
              f"{sum(len(v) for v in amr.values())} AMR hits")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": args.width, "height": args.height},
            device_scale_factor=2,
        )
        page = await ctx.new_page()
        page.on("pageerror", lambda exc: print(f"!! pageerror: {exc}"))

        # ---- 1. Setup tab (landing) --------------------------------------
        await page.goto(args.base_url)
        await page.wait_for_function(
            "document.querySelectorAll('#scheme-select option').length > 0",
            timeout=30000,
        )
        await page.evaluate(
            """
            (() => {
              const sel = document.getElementById('scheme-select');
              const prefer = ['efaecium_cgmlst_orgio', 'kpneumoniae_complex_cgmlst_orgio',
                              'saureus_cgmlst', 'lmonocytogenes_cgmlst', 'saureus_mlst'];
              for (const p of prefer) {
                const opt = [...sel.options].find(o => o.value === p);
                if (opt) { sel.value = p; sel.dispatchEvent(new Event('change')); break; }
              }
              const folder = document.getElementById('folder-input');
              folder.value = '/home/iowa/Desktop/test_mlst/';
              document.getElementById('project-name').value = 'Efaecium_VRE_test';
            })()
            """
        )
        await page.wait_for_timeout(400)
        await page.screenshot(path=str(out / f"01_setup{sfx}.png"), full_page=False)
        print("  ✓ 01_setup.png")

        # ---- 2. Folder-browser modal -------------------------------------
        await page.evaluate("openBrowse('/home/iowa/Desktop')")
        await page.wait_for_timeout(700)
        await page.screenshot(path=str(out / f"02_browse{sfx}.png"), full_page=False)
        print("  ✓ 02_browse.png")
        await page.evaluate("document.getElementById('browse-modal').classList.add('hidden')")

        # ---- 3. Scheme-catalog modal -------------------------------------
        try:
            await page.evaluate("openDiscover()")
            await page.wait_for_function(
                "document.querySelectorAll('#discover-table tbody tr').length > 5",
                timeout=30000,
            )
            await page.evaluate(
                "document.getElementById('discover-search').value = 'faecium';"
                "document.getElementById('discover-search').dispatchEvent(new Event('input'));"
            )
            await page.wait_for_timeout(500)
            await page.screenshot(path=str(out / f"02b_discover{sfx}.png"), full_page=False)
            print("  ✓ 02b_discover.png")
            await page.evaluate("document.getElementById('discover-modal').classList.add('hidden')")
        except Exception as e:
            print(f"  (skipped discover modal: {e})")

        if not injected:
            print("No --job-id or --from-project supplied; only modal+setup shots captured.")
            await browser.close()
            return 0

        # ---- Inject results into the page state --------------------------
        await page.evaluate(
            """
            ({results, mst, amr, threshold}) => {
                state.jobId = 'replayed';
                state.results = results;
                state.mst = mst;
                state.amr_results = amr || {};
                state.schemeClusterThreshold = threshold;
                state.clusterThreshold = threshold;
                const nodes = mst.elements.filter(e => !e.data.source);
                if (!nodes.some(n => n.data.cluster_id)) {
                    attachClusterIds(mst, threshold);
                }
                const anySt = nodes.some(n => n.data.st);
                state.metaFields = anySt ? ['st', 'cluster_id'] : ['cluster_id', 'st'];
                populateColorFields();
                document.getElementById('color-field').value =
                    anySt ? 'st' : 'cluster_id';
                document.getElementById('cluster-threshold').value = threshold;
                document.getElementById('cluster-threshold-num').value = threshold;
                document.getElementById('empty-state')?.classList.add('hidden');
                document.getElementById('status-dot').className = 'dot done';
                document.getElementById('status-text').textContent =
                    `Analyzed ${results.length} samples`;
                // Activate the tree tab; subscribe-handler does this on real
                // runs, but we're injecting state cold.
                activateTab('tree');
                renderMst();
            }
            """,
            {
                "results": injected["results"],
                "mst": injected["mst"],
                "amr": injected.get("amr", {}),
                "threshold": args.cluster_threshold,
            },
        )
        # Give fcose time to settle.
        await page.wait_for_timeout(5000)

        # ---- 4. MST tab — default view -----------------------------------
        await page.screenshot(path=str(out / f"03_mst{sfx}.png"), full_page=False)
        print("  ✓ 03_mst.png")

        # ---- 5. MST tab — halos at higher cluster threshold ------------
        await page.evaluate(
            """
            (t) => {
                document.getElementById('cluster-threshold').value = t;
                document.getElementById('cluster-threshold-num').value = t;
                document.getElementById('cluster-threshold').dispatchEvent(new Event('input'));
            }
            """,
            max(5, args.cluster_threshold * 2),
        )
        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(out / f"04_clusters{sfx}.png"), full_page=False)
        print("  ✓ 04_clusters.png")

        # ---- 6. Comparison Table tab -------------------------------------
        await page.evaluate("activateTab('table')")
        await page.wait_for_timeout(800)
        await page.screenshot(path=str(out / f"05_table{sfx}.png"), full_page=False)
        print("  ✓ 05_table.png")

        # ---- 7. AMR tab — gene × sample matrix ---------------------------
        await page.evaluate("activateTab('amr')")
        await page.wait_for_timeout(800)
        await page.screenshot(path=str(out / f"06_amr{sfx}.png"), full_page=False)
        print("  ✓ 06_amr.png")

        # ---- 8. Statistics tab -------------------------------------------
        await page.evaluate("activateTab('stats')")
        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(out / f"07_stats{sfx}.png"), full_page=False)
        print("  ✓ 07_stats.png")

        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
