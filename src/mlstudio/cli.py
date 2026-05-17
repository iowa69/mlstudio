"""MLSTudio command-line interface."""

from __future__ import annotations

import logging
import os
import socket
import sys
import webbrowser
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from mlstudio import __version__
from mlstudio.schemes.bigsdb import REGISTRY, list_local, pull_scheme

app = typer.Typer(
    name="mlstudio",
    help="MLST / cgMLST typing and interactive MST visualization.",
    no_args_is_help=True,
    add_completion=False,
)
schemes_app = typer.Typer(help="Manage MLST / cgMLST scheme downloads.", no_args_is_help=True)
call_app = typer.Typer(help="Run typing pipelines.", no_args_is_help=True)
app.add_typer(schemes_app, name="schemes")
app.add_typer(call_app, name="call")

console = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-6s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"mlstudio {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", help="Show version and exit.",
        callback=_version_callback, is_eager=True,
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    _setup_logging(verbose)


def _pick_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


@app.command()
def gui(
    folder: Path = typer.Argument(None, help="Optional folder to pre-fill in the UI."),
    host: str = typer.Option("127.0.0.1", help="Bind address."),
    port: int = typer.Option(0, help="Port (0 = auto-pick)."),
    open_browser: bool = typer.Option(True, "--open-browser/--no-open-browser"),
) -> None:
    """Launch the local web GUI."""
    import uvicorn

    from mlstudio.api.server import create_app

    if port == 0:
        port = _pick_free_port(host)

    app_obj = create_app()
    url = f"http://{host}:{port}/"
    if folder:
        url += f"?folder={folder.absolute()}"
    console.print(f"[cyan]MLSTudio[/cyan] serving on [bold]{url}[/bold]")
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(app_obj, host=host, port=port, log_level="warning")


@schemes_app.command("list")
def schemes_list(remote: bool = typer.Option(False, "--remote", help="Show registry too.")) -> None:
    """List locally cached (and optionally remote) schemes."""
    local = list_local()
    local_keys = {Path(s.root).name for s in local}

    table = Table(title="MLSTudio schemes", expand=True)
    table.add_column("Key", style="cyan")
    table.add_column("Organism")
    table.add_column("Scheme")
    table.add_column("Host", style="dim")
    table.add_column("Cached")

    keys = sorted(REGISTRY) if remote else sorted(local_keys)
    for k in keys:
        ref = REGISTRY.get(k)
        if not ref:
            continue
        table.add_row(
            k, ref.organism, ref.scheme_label, ref.host,
            "✓" if k in local_keys else "—",
        )
    console.print(table)


@schemes_app.command("pull")
def schemes_pull(
    key: str = typer.Argument(..., help="Scheme registry key (see `schemes list --remote`)."),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Download a scheme from PubMLST / BIGSdb-Pasteur."""
    scheme = pull_scheme(key, force=force)
    console.print(f"[green]✓[/green] {scheme.name} ({len(scheme.loci)} loci) cached at {scheme.root}")


@call_app.command("mlst")
def call_mlst_cmd(
    scheme: str = typer.Option(..., "--scheme", help="Scheme registry key."),
    assembly: Path = typer.Option(..., "--input", exists=True, help="FASTA file."),
    threads: int = typer.Option(0, "--threads", "-t", help="0 = auto."),
    min_identity: float = typer.Option(95.0, "--min-identity"),
    min_coverage: float = typer.Option(90.0, "--min-coverage"),
) -> None:
    """Call MLST on a single assembly (smoke-test command)."""
    from mlstudio.calling.mlst import call_mlst as _call_mlst

    sch = pull_scheme(scheme)
    result = _call_mlst(assembly, sch, threads=threads,
                       min_identity=min_identity, min_coverage=min_coverage)
    table = Table(title=f"MLST: {result.sample} → ST {result.st or 'none'}")
    table.add_column("Locus")
    table.add_column("Allele")
    table.add_column("Flag")
    table.add_column("%id", justify="right")
    table.add_column("%cov", justify="right")
    for loc in sch.loci:
        c = result.calls[loc]
        table.add_row(loc, c.allele or "-", c.flag, f"{c.identity:.2f}", f"{c.coverage:.2f}")
    console.print(table)
    for note in result.notes:
        console.print(f"[yellow]note:[/yellow] {note}")


@call_app.command("cgmlst")
def call_cgmlst_cmd() -> None:
    """Run cgMLST calling (M3 — not yet implemented)."""
    console.print("[yellow]cgMLST calling is stubbed; coming in M3.[/yellow]")
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
