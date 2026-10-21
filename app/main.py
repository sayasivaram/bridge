"""FastAPI entry point. Serves the JSON API under /api and the single-page frontend at /.

There is no real login: this is a local demo, so the frontend sends the chosen partner's id
in the X-User-Id header ("switch user" dropdown).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.config import REPO_DIR, load_settings
from app.seed import seed

STATIC_DIR = REPO_DIR / "static"

settings = load_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    seed(db.DB_PATH)  # creates and fills data/closer.db on first run; no-op afterwards
    yield


app = FastAPI(title="Closer", version="0.1.0", lifespan=lifespan)


def current_user(conn, user_id: int | None) -> dict:
    if user_id is None:
        raise HTTPException(400, "Pick a partner first (X-User-Id header missing).")
    row = conn.execute("SELECT id, couple_id, role, name, city FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Unknown user.")
    return dict(row)


@app.get("/api/health")
def health() -> dict:
    """Lets the UI show whether an API key was found, without exposing it."""
    return {"status": "ok", **settings.public_status()}


@app.get("/api/users")
def list_users() -> list[dict]:
    """The two partners, for the switch-user dropdown."""
    with db.session() as conn:
        rows = conn.execute("SELECT id, role, name, city FROM users ORDER BY role").fetchall()
    return [dict(r) for r in rows]


@app.get("/api/me")
def me(x_user_id: int | None = Header(default=None)) -> dict:
    with db.session() as conn:
        user = current_user(conn, x_user_id)
        partner = conn.execute(
            "SELECT name, city FROM users WHERE couple_id = ? AND id != ?", (user["couple_id"], user["id"])
        ).fetchone()
        couple = conn.execute("SELECT name FROM couples WHERE id = ?", (user["couple_id"],)).fetchone()
    return {"user": user, "partner": dict(partner) if partner else None, "couple": couple["name"]}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
