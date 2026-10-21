"""The one wrapper every agent uses to call the model.

Each call:
  1. counts as a step in the user's request (hard cap: MAX_STEPS_PER_REQUEST),
  2. is refused up front if it would push the user past their daily token budget,
  3. goes to gpt-6-luna through Portkey (OpenAI SDK, Responses API, same as Homework 5),
  4. is rejected if the API says any other model served it,
  5. returns the text plus token counts and cost, and
  6. is logged to the llm_calls table.

Prompt layout for caching: `instructions` is the STABLE part (system prompt, safety rules,
couple profile) and goes first; `user_input` is the VARIABLE part (this week's data, the
request) and goes last. The provider reuses the longest exactly-matching start of earlier calls,
so keep `instructions` byte-identical between runs.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import config
from app.config import REPO_DIR, Settings
from app.db import session

PRICES_PATH = REPO_DIR / "data" / "prices.json"

# The API reports the deployment or snapshot it served, e.g. "gpt-6-luna-global" or
# "gpt-6-luna-2026-09-22". Anything else is rejected.
_SERVED_MODEL = re.compile(rf"^{re.escape(config.MODEL)}(-global)?(-\d{{4}}-\d{{2}}-\d{{2}})?$")


class BudgetExceededError(Exception):
    """The user has used up their daily token allowance. The message is safe to show in the UI."""


class StepLimitError(Exception):
    """One request tried to make more model/tool calls than allowed (stops runaway loops)."""


class ModelMismatchError(RuntimeError):
    """The API reported a model other than gpt-6-luna."""


@dataclass
class Usage:
    input_tokens: int = 0  # includes cached and cache-write tokens
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0  # includes reasoning tokens
    reasoning_tokens: int = 0

    @property
    def uncached_input_tokens(self) -> int:
        return max(self.input_tokens - self.cached_tokens - self.cache_write_tokens, 0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(*(getattr(self, f) + getattr(other, f) for f in self.__dataclass_fields__))


@dataclass
class LLMResult:
    text: str
    agent_name: str
    model: str
    usage: Usage
    cost: dict[str, float]  # per token type, plus "total"
    latency_ms: int


@dataclass
class RunContext:
    """One user request (e.g. "build call topics"). Every model and tool call counts as a step."""

    user_id: int
    max_steps: int = config.MAX_STEPS_PER_REQUEST
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    steps: int = 0
    results: list[LLMResult] = field(default_factory=list)

    def step(self, what: str) -> None:
        if self.steps >= self.max_steps:
            raise StepLimitError(
                f"Stopped after {self.max_steps} steps in one request (next was: {what}). "
                "This protects against an agent looping."
            )
        self.steps += 1

    def summary(self) -> dict[str, Any]:
        """Token counts and cost for the whole request, for the UI's token panel."""
        usage = sum((r.usage for r in self.results), Usage())
        return {
            "request_id": self.request_id,
            "calls": len(self.results),
            "steps": self.steps,
            "input_tokens": usage.input_tokens,
            "cached_tokens": usage.cached_tokens,
            "cache_write_tokens": usage.cache_write_tokens,
            "output_tokens": usage.output_tokens,
            "reasoning_tokens": usage.reasoning_tokens,
            "cost_usd": round(sum(r.cost["total"] for r in self.results), 8),
        }


def load_prices(path: Path = PRICES_PATH) -> dict[str, float]:
    return json.loads(path.read_text(encoding="utf-8"))[config.MODEL]


def price(usage: Usage, prices: dict[str, float]) -> dict[str, float]:
    """Dollar cost of each token type: tokens x (price per 1M / 1,000,000)."""
    cost = {
        "uncached_input": usage.uncached_input_tokens * prices["input_per_1m"] / 1_000_000,
        "cached_input": usage.cached_tokens * prices["cached_input_per_1m"] / 1_000_000,
        "cache_write": usage.cache_write_tokens * prices["cache_write_per_1m"] / 1_000_000,
        "output": usage.output_tokens * prices["output_per_1m"] / 1_000_000,
    }
    cost["total"] = sum(cost.values())
    return {k: round(v, 8) for k, v in cost.items()}


def usage_from_response(resp: Any) -> Usage:
    u = resp.usage
    in_details = getattr(u, "input_tokens_details", None)
    out_details = getattr(u, "output_tokens_details", None)
    return Usage(
        input_tokens=u.input_tokens or 0,
        cached_tokens=getattr(in_details, "cached_tokens", 0) or 0,
        cache_write_tokens=getattr(in_details, "cache_write_tokens", 0) or 0,
        output_tokens=u.output_tokens or 0,
        reasoning_tokens=getattr(out_details, "reasoning_tokens", 0) or 0,
    )


def build_client(settings: Settings) -> Any:
    """OpenAI SDK client: through Portkey for the course key, direct for an OpenAI key."""
    from openai import OpenAI

    key = settings.require_api_key()
    if settings.provider == "portkey":
        return OpenAI(
            api_key=key,
            base_url=config.PORTKEY_BASE_URL,
            default_headers={"x-portkey-api-key": key},
            timeout=60.0,
            max_retries=0,
        )
    return OpenAI(api_key=key, timeout=60.0, max_retries=0)


def tokens_used_today(conn: sqlite3.Connection, user_id: int) -> int:
    """Total tokens this user's calls used since midnight UTC."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM llm_calls "
        "WHERE user_id = ? AND created_at >= ?",
        (user_id, today),
    ).fetchone()
    return int(row[0])


def daily_budget(conn: sqlite3.Connection, user_id: int) -> int:
    row = conn.execute("SELECT daily_token_budget FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is not None and row[0] is not None:
        return int(row[0])
    return config.DEFAULT_DAILY_TOKEN_BUDGET


def estimate_tokens(text: str) -> int:
    """Rough count (about 4 characters per token), used only for the budget pre-check."""
    return len(text) // 4 + 1


class LLM:
    def __init__(
        self,
        settings: Settings,
        db_path: Path | str | None = None,  # None = data/closer.db
        client: Any = None,
        prices: dict[str, float] | None = None,
    ):
        self.settings = settings
        self.db_path = db_path
        self._client = client  # tests pass a fake client; otherwise built on first use
        self.prices = prices or load_prices()

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = build_client(self.settings)
        return self._client

    def check_budget(self, conn: sqlite3.Connection, user_id: int, estimated: int) -> None:
        used, budget = tokens_used_today(conn, user_id), daily_budget(conn, user_id)
        if used + estimated > budget:
            raise BudgetExceededError(
                f"You've used {used:,} of your {budget:,} AI tokens for today, and this request "
                "could go over. The allowance resets at midnight UTC. Everything that doesn't "
                "need AI (your Moment Jar, Pulse, and rituals) still works."
            )

    def call(
        self,
        run: RunContext,
        agent_name: str,
        instructions: str,
        user_input: str,
        max_output_tokens: int = config.MAX_OUTPUT_TOKENS,
        cache_key: str | None = None,
    ) -> LLMResult:
        """One model call. `instructions` = stable prefix, `user_input` = variable tail."""
        run.step(f"model call by {agent_name}")
        with session(self.db_path) as conn:
            self.check_budget(conn, run.user_id, estimate_tokens(instructions + user_input) + max_output_tokens)

        started = time.perf_counter()
        try:
            extra = {"prompt_cache_key": cache_key} if cache_key else {}  # e.g. "couple-1-topics"
            resp = self.client.responses.create(
                model=config.MODEL,
                instructions=instructions,
                input=user_input,
                max_output_tokens=max_output_tokens,
                **extra,
            )
            served = getattr(resp, "model", None)
            if not served or not _SERVED_MODEL.match(served):
                raise ModelMismatchError(f"Response came from {served!r}; only {config.MODEL!r} is allowed.")
        except Exception as err:
            self._log(run, agent_name, None, Usage(), {"total": 0.0}, started, error=err)
            raise

        usage = usage_from_response(resp)
        cost = price(usage, self.prices)
        self._log(run, agent_name, served, usage, cost, started)
        result = LLMResult(
            text=resp.output_text or "",
            agent_name=agent_name,
            model=served,
            usage=usage,
            cost=cost,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        run.results.append(result)
        return result

    def _log(self, run, agent_name, model, usage, cost, started, error: Exception | None = None) -> None:
        with session(self.db_path) as conn:
            conn.execute(
                "INSERT INTO llm_calls (user_id, agent_name, request_id, model, status, error, "
                "input_tokens, cached_tokens, cache_write_tokens, output_tokens, reasoning_tokens, "
                "cost_usd, latency_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.user_id, agent_name, run.request_id, model,
                    "error" if error else "ok",
                    f"{type(error).__name__}: {error}"[:300] if error else None,
                    usage.input_tokens, usage.cached_tokens, usage.cache_write_tokens,
                    usage.output_tokens, usage.reasoning_tokens, cost["total"],
                    int((time.perf_counter() - started) * 1000),
                ),
            )
