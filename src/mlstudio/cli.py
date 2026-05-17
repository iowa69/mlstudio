"""MLSTudio command-line interface."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from mlstudio import __version__

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


@app.callback()
def main(version: bool = typer.Option(False, "--version", help="Show version and exit.")) -> None:
    if version:
        console.print(f"mlstudio {__version__}")
        raise typer.Exit()


@app.command()
def setup(
    species: str = typer.Option(..., "--species", help='e.g. "Listeria monocytogenes"'),
) -> None:
    """One-time setup: download schemes + verify external dependencies."""
    console.print(f"[yellow]TODO[/yellow] setup for species: {species}")
    raise typer.Exit(code=1)


@app.command()
def gui(
    host: str = typer.Option("127.0.0.1", help="Bind address."),
    port: int = typer.Option(0, help="Port (0 = auto-pick)."),
    open_browser: bool = typer.Option(True, "--open-browser/--no-open-browser"),
) -> None:
    """Launch the local web GUI."""
    console.print(f"[yellow]TODO[/yellow] launch server on {host}:{port} (open={open_browser})")
    raise typer.Exit(code=1)


@schemes_app.command("list")
def schemes_list() -> None:
    """List locally cached schemes."""
    console.print("[yellow]TODO[/yellow] list local schemes")


@schemes_app.command("pull")
def schemes_pull(name: str = typer.Argument(...)) -> None:
    """Download a scheme from PubMLST / cgMLST.org."""
    console.print(f"[yellow]TODO[/yellow] pull scheme {name}")


@call_app.command("mlst")
def call_mlst(
    scheme: str = typer.Option(..., "--scheme"),
    input_path: Path = typer.Option(..., "--input", exists=True),
    output: Path = typer.Option(Path("results"), "--output", "-o"),
    threads: int = typer.Option(0, "--threads", "-t", help="0 = all available cores."),
) -> None:
    """Run MLST calling on assembled genomes."""
    console.print(f"[yellow]TODO[/yellow] mlst scheme={scheme} input={input_path} -> {output}")


@call_app.command("cgmlst")
def call_cgmlst(
    scheme: str = typer.Option(..., "--scheme"),
    input_path: Path = typer.Option(..., "--input", exists=True),
    reads: Path | None = typer.Option(None, "--reads", help="Reads dir for Bowtie2 rescue."),
    output: Path = typer.Option(Path("results"), "--output", "-o"),
    threads: int = typer.Option(0, "--threads", "-t"),
    rescue: bool = typer.Option(True, "--rescue/--no-rescue"),
) -> None:
    """Run cgMLST calling with optional Bowtie2 read-backed rescue."""
    console.print(
        f"[yellow]TODO[/yellow] cgmlst scheme={scheme} input={input_path} "
        f"reads={reads} rescue={rescue}"
    )


if __name__ == "__main__":
    app()
