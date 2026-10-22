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

from datetime import timedelta

from app import db, moments, orchestrator, pulses, rituals, safety, timeutil, usage
from app.agents import call_topics, drift, ritual_builder
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
    seed(db.DB_PATH)  # creates and fills data/bridge.db on first run; no-op afterwards
    yield


app = FastAPI(title="Bridge", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def revalidate_static(request, call_next):
    """Browsers must re-check the frontend files on each load (a cheap 304 if unchanged), so an
    updated app never runs new JavaScript against a stale cached stylesheet."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def current_user(conn, user_id: int | None) -> dict:
    if user_id is None:
        raise HTTPException(400, "Pick a partner first (X-User-Id header missing).")
    row = conn.execute("SELECT id, couple_id, role, name, city, timezone FROM users WHERE id = ?",
                       (user_id,)).fetchone()
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
    """The current user's jar for one week (in their time zone): shared moments plus their own private ones."""
    with db.session() as conn:
        user = current_user(conn, x_user_id)
        today = timeutil.local_today(user["timezone"])
        week_of = week_of or today
        items = moments.list_for_viewer(conn, user["id"], week_of)
    start = moments.monday_of(week_of)
    return {"week_start": start.isoformat(), "is_current_week": start == moments.monday_of(today),
            "timezone": user["timezone"], "moments": items}


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


class PulseIn(BaseModel):
    score: int = Field(ge=1, le=5)
    contact: Literal["too_little", "right", "too_much"]
    note: str | None = Field(default=None, max_length=pulses.NOTE_MAX)
    note_shared: bool = False


HISTORY_WEEKS = 6


@app.get("/api/pulse")
def get_pulse(x_user_id: int | None = Header(default=None)) -> dict:
    """This week's check-in (if any) and the last weeks, with the reveal rule applied."""
    with db.session() as conn:
        user = current_user(conn, x_user_id)
        week = pulses.current_week(conn, user["id"])
        weeks = [week - timedelta(weeks=k) for k in range(HISTORY_WEEKS - 1, -1, -1)]
        history = pulses.history_for_viewer(conn, user["id"], weeks)
        partner = conn.execute("SELECT name FROM users WHERE couple_id = ? AND id != ?",
                               (user["couple_id"], user["id"])).fetchone()
    return {"week_start": week.isoformat(), "partner": partner["name"] if partner else None,
            "this_week": history[-1], "history": history}


@app.post("/api/pulse")
def save_pulse(body: PulseIn, x_user_id: int | None = Header(default=None)) -> dict:
    with db.session() as conn:
        user = current_user(conn, x_user_id)
        try:
            saved = pulses.upsert(conn, user["id"], body.score, body.contact, body.note, body.note_shared)
        except pulses.PulseError as err:
            raise HTTPException(400, str(err)) from None
    flag = safety.precheck(saved["note"])
    return {"saved": saved, "support": safety.support_message(flag, user["timezone"]) if flag else None}


@app.get("/api/pulse/nudge")
def pulse_nudge(x_user_id: int | None = Header(default=None)) -> dict:
    """Drift Detector: rules first ($0); the agent only if they flag drift."""
    with db.session() as conn:
        user = current_user(conn, x_user_id)
    try:
        return drift.run(get_llm(), user["id"])
    except StepLimitError as err:
        raise HTTPException(503, str(err)) from None
    except MissingAPIKeyError:
        raise HTTPException(503, "No API key is set up, so AI features are off. See the README.") from None


class SuggestIn(BaseModel):
    time: Literal["15", "60", "120"]
    energy: Literal["low", "medium", "high"]


class PlanIn(BaseModel):
    catalog_id: str = Field(max_length=60)
    variation: str | None = Field(default=None, max_length=300)


class StatusIn(BaseModel):
    status: Literal["planned", "done", "skipped"]


class RatingIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    note: str | None = Field(default=None, max_length=rituals.NOTE_MAX)


@app.get("/api/rituals/catalog")
def ritual_catalog() -> list[dict]:
    return list(rituals.catalog().values())


@app.get("/api/rituals")
def list_rituals(x_user_id: int | None = Header(default=None)) -> list[dict]:
    with db.session() as conn:
        user = current_user(conn, x_user_id)
        return rituals.list_for_viewer(conn, user["id"])


@app.post("/api/rituals/suggest")
def suggest_rituals(body: SuggestIn, x_user_id: int | None = Header(default=None)) -> dict:
    """Ritual Builder: 3 catalog rituals for tonight, personalized from the couple's ratings."""
    with db.session() as conn:
        user = current_user(conn, x_user_id)
    try:
        return ritual_builder.run(get_llm(), user["id"], body.time, body.energy)
    except BudgetExceededError as err:
        raise HTTPException(429, str(err)) from None
    except StepLimitError as err:
        raise HTTPException(503, str(err)) from None
    except MissingAPIKeyError:
        raise HTTPException(503, "No API key is set up, so AI features are off. See the README.") from None
    except Exception as err:
        print(f"ritual suggest failed: {type(err).__name__}: {err}")
        raise HTTPException(502, "The AI service didn't answer properly. Please try again in a moment.") from None


@app.post("/api/rituals", status_code=201)
def plan_ritual(body: PlanIn, x_user_id: int | None = Header(default=None)) -> dict:
    with db.session() as conn:
        user = current_user(conn, x_user_id)
        try:
            return rituals.add_planned(conn, user["id"], body.catalog_id, body.variation)
        except rituals.RitualError as err:
            raise HTTPException(400, str(err)) from None


@app.post("/api/rituals/{ritual_id}/status")
def ritual_status(ritual_id: int, body: StatusIn, x_user_id: int | None = Header(default=None)) -> dict:
    with db.session() as conn:
        user = current_user(conn, x_user_id)
        try:
            rituals.set_status(conn, user["id"], ritual_id, body.status)
        except rituals.RitualError as err:
            raise HTTPException(404, str(err)) from None
    return {"ok": True}


@app.post("/api/rituals/{ritual_id}/feedback")
def rate_ritual(ritual_id: int, body: RatingIn, x_user_id: int | None = Header(default=None)) -> dict:
    """Rate a ritual 1-5 with an optional note. Feeds the Ritual Builder's next suggestions."""
    with db.session() as conn:
        user = current_user(conn, x_user_id)
        try:
            return rituals.rate(conn, user["id"], ritual_id, body.rating, body.note)
        except rituals.RitualError as err:
            raise HTTPException(400 if "Rate" in str(err) or "note" in str(err) else 404, str(err)) from None


class ChatIn(BaseModel):
    message: str = Field(max_length=2000)  # the guard enforces the real (shorter) limit, politely


@app.post("/api/chat")
def chat(body: ChatIn, x_user_id: int | None = Header(default=None)) -> dict:
    """The home-page chat box: guard -> router -> agent -> guard, logged to agent_runs."""
    with db.session() as conn:
        user = current_user(conn, x_user_id)
    try:
        return orchestrator.handle(get_llm(), user["id"], body.message)
    except MissingAPIKeyError:
        raise HTTPException(503, "No API key is set up, so AI features are off. See the README.") from None


@app.get("/api/runs")
def recent_runs(limit: int = 10, x_user_id: int | None = Header(default=None)) -> list[dict]:
    """Your own recent chat runs with their full traces (for the run log / screenshots)."""
    with db.session() as conn:
        user = current_user(conn, x_user_id)
        return orchestrator.runs_for_viewer(conn, user["id"], min(max(limit, 1), 50))


@app.get("/api/usage")
def usage_summary(x_user_id: int | None = Header(default=None)) -> dict:
    """Token usage and cost for this couple from llm_calls: per agent, per feature, per user."""
    from app.llm import load_prices

    with db.session() as conn:
        user = current_user(conn, x_user_id)
        rows = usage.load_rows(conn, user["couple_id"])
        budgets = usage.budget_status(conn, user["couple_id"])
    recent = [{k: r.get(k) for k in ("created_at", "agent_name", "user_name", "input_tokens", "cached_tokens",
                                     "output_tokens", "cost_usd", "gateway_cache", "status")} | {
        "agent_label": usage.AGENT_LABEL.get(r["agent_name"], r["agent_name"]), "feature": usage.feature_of(r)}
        for r in reversed(rows[-15:])]
    prices = load_prices()
    return usage.summarize(rows) | {"budgets": budgets, "recent": recent, "prices": prices,
                                    "ceiling_per_user_month": usage.monthly_ceiling_per_user(prices)}


@app.get("/usage", include_in_schema=False)
def usage_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")  # the single page opens its Usage tab


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
