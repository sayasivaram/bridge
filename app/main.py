"""FastAPI entry point. Serves the JSON API under /api and the single-page frontend at /.

There is no real login: this is a local demo, so the frontend sends the chosen partner's id
in the X-User-Id header ("switch user" dropdown).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import db, moments
from app.agents import call_topics
from app.config import REPO_DIR, MissingAPIKeyError, load_settings
from app.llm import LLM, BudgetExceededError, StepLimitError
from app.seed import seed

STATIC_DIR = REPO_DIR / "static"

settings = load_settings()
_llm: LLM | None = None


def get_llm() -> LLM:
    """The shared model wrapper (tests replace this with one that uses a fake client)."""
    global _llm
    if _llm is None:
        _llm = LLM(settings)
    return _llm


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


class MomentIn(BaseModel):
    text: str = Field(max_length=500)
    mood: Literal["funny", "sweet", "stressful", "proud", "random"]
    visibility: Literal["shared", "private"] = "shared"
    song: str | None = Field(default=None, max_length=120)
    image: str | None = Field(default=None, max_length=3_000_000)  # data URL, resized in the browser


@app.get("/api/moments")
def list_moments(week_of: date | None = None, x_user_id: int | None = Header(default=None)) -> dict:
    """The current user's jar for one week: shared moments plus their own private ones."""
    week_of = week_of or date.today()
    with db.session() as conn:
        user = current_user(conn, x_user_id)
        items = moments.list_for_viewer(conn, user["id"], week_of)
    start = moments.monday_of(week_of)
    return {"week_start": start.isoformat(), "is_current_week": start == moments.monday_of(date.today()),
            "moments": items}


@app.post("/api/moments", status_code=201)
def add_moment(body: MomentIn, x_user_id: int | None = Header(default=None)) -> dict:
    with db.session() as conn:
        user = current_user(conn, x_user_id)
        try:
            return moments.add(conn, user["id"], body.text, body.mood, body.visibility, body.song, body.image)
        except moments.MomentError as err:
            raise HTTPException(400, str(err)) from None


@app.delete("/api/moments/{moment_id}", status_code=204)
def delete_moment(moment_id: int, x_user_id: int | None = Header(default=None)) -> None:
    with db.session() as conn:
        user = current_user(conn, x_user_id)
        if not moments.delete(conn, user["id"], moment_id):
            raise HTTPException(404, "Moment not found.")


@app.get("/api/moments/{moment_id}/image")
def moment_image(moment_id: int, x_user_id: int | None = Header(default=None)) -> FileResponse:
    """Photos go through the same privacy check as text (they are not in /static)."""
    with db.session() as conn:
        user = current_user(conn, x_user_id)
        path = moments.image_path_for_viewer(conn, user["id"], moment_id)
    if path is None or not path.is_file():
        raise HTTPException(404, "Photo not found.")
    return FileResponse(path, headers={"Cache-Control": "private, no-store"})


@app.post("/api/call-prep")
def call_prep(x_user_id: int | None = Header(default=None)) -> dict:
    """Call-Topic Agent: 5 starters + 1 deeper question from the last 7 days of shared moments."""
    with db.session() as conn:
        user = current_user(conn, x_user_id)
    try:
        return call_topics.run(get_llm(), user["id"])
    except BudgetExceededError as err:
        raise HTTPException(429, str(err)) from None
    except StepLimitError as err:
        raise HTTPException(503, str(err)) from None
    except MissingAPIKeyError:
        raise HTTPException(503, "No API key is set up, so AI features are off. See the README.") from None
    except Exception as err:  # network/API trouble: say so plainly, keep details in the server log
        print(f"call-prep failed: {type(err).__name__}: {err}")
        raise HTTPException(502, "The AI service didn't answer properly. Please try again in a moment.") from None


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
