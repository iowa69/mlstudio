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
    ap.add_argument("--out", default="docs/screens")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=1000)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # Pre-fetch job state via HTTP if requested
    injected = None
    if args.job_id:
        async with httpx.AsyncClient(base_url=args.base_url) as client:
            r = await client.get(f"/api/jobs/{args.job_id}")
            r.raise_for_status()
            injected = r.json()
            assert injected["status"] == "done", f"Job not finished: {injected['status']}"
            print(f"Loaded job {args.job_id}: {injected['n_results']} samples")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": args.width, "height": args.height},
            device_scale_factor=2,
        )
        page = await ctx.new_page()

        # 1. Welcome screen
        await page.goto(args.base_url)
        await page.wait_for_function(
            "document.querySelectorAll('#scheme-select option').length > 0",
        )
        await page.wait_for_timeout(400)
        await page.screenshot(path=str(out / "01_welcome.png"), full_page=False)
        print("  ✓ 01_welcome.png")

        if injected:
            # Bypass the UI flow: inject the completed job state directly.
            await page.evaluate(
                """
                ({results, mst}) => {
                    state.jobId = 'replayed';
                    state.results = results;
                    state.mst = mst;
                    state.metaFields = ['st'];
                    document.getElementById('empty-state').classList.add('hidden');
                    renderResults();
                    renderMst();
                    document.getElementById('status-dot').className = 'dot done';
                    document.getElementById('status-text').textContent = `Analyzed ${results.length} samples`;
                }
                """,
                {"results": injected["results"], "mst": injected["mst"]},
            )
            # Let Cytoscape settle
            await page.wait_for_timeout(2500)

            # Hide results panel for a clean MST shot
            await page.evaluate(
                "document.getElementById('results-panel').classList.add('hidden')"
            )
            await page.wait_for_timeout(300)
            await page.screenshot(path=str(out / "03_mst.png"), full_page=False)
            print("  ✓ 03_mst.png")

            # Threshold demo: collapse close clusters (e.g. distance <= 1)
            max_edge = max(
                (e["data"]["weight"] for e in injected["mst"]["elements"]
                 if "source" in e["data"]),
                default=0,
            )
            if max_edge > 0:
                t = max(1, max_edge // 2)
                await page.evaluate(
                    f"""
                    document.getElementById('threshold').value = {t};
                    document.getElementById('threshold-val').textContent = '{t}';
                    document.getElementById('threshold').dispatchEvent(new Event('input'));
                    """
                )
                await page.wait_for_timeout(500)
                await page.screenshot(path=str(out / "04_threshold.png"), full_page=False)
                print("  ✓ 04_threshold.png")

                # Reset threshold
                await page.evaluate(
                    f"""
                    document.getElementById('threshold').value = {max_edge};
                    document.getElementById('threshold-val').textContent = '{max_edge}';
                    document.getElementById('threshold').dispatchEvent(new Event('input'));
                    """
                )
                await page.wait_for_timeout(300)

            # Results table shot
            await page.evaluate(
                "document.getElementById('results-panel').classList.remove('hidden')"
            )
            await page.evaluate(
                "document.getElementById('results-panel').scrollIntoView()"
            )
            await page.wait_for_timeout(300)
            await page.screenshot(path=str(out / "05_table.png"), full_page=False)
            print("  ✓ 05_table.png")

        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
