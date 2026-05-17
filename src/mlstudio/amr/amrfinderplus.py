"""Wrapper around the `amrfinder` CLI."""

from __future__ import annotations

from pathlib import Path


def run_amrfinderplus(
    assembly: Path,
    output: Path,
    threads: int = 0,
) -> Path:
    raise NotImplementedError("M4 — AMRFinderPlus wrapper not yet implemented.")
