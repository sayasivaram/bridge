"""FastAPI entry point. Serves the JSON API under /api and the single-page frontend at /."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import REPO_DIR, load_settings

STATIC_DIR = REPO_DIR / "static"

settings = load_settings()

app = FastAPI(title="Closer", version="0.1.0")


@app.get("/api/health")
def health() -> dict:
    """Lets the UI show whether an API key was found, without exposing it."""
    return {"status": "ok", **settings.public_status()}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
