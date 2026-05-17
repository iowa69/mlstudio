"""Smoke tests — verify the package imports and CLI is wired up."""

from __future__ import annotations

from typer.testing import CliRunner

import mlstudio
from mlstudio.api.server import create_app
from mlstudio.cli import app


def test_version_string() -> None:
    assert mlstudio.__version__


def test_cli_version() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "mlstudio" in result.stdout


def test_api_health() -> None:
    fastapi_app = create_app()
    assert fastapi_app.title == "MLSTudio"
