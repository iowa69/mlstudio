"""FastAPI app factory."""

from __future__ import annotations

from fastapi import FastAPI

from mlstudio import __version__


def create_app() -> FastAPI:
    app = FastAPI(title="MLSTudio", version=__version__)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app
