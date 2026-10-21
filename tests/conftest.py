"""Shared fixtures: a seeded throwaway database and a fake model client (no real API calls)."""

from datetime import date
from types import SimpleNamespace

import pytest

from app import db
from app.seed import seed

TODAY = date(2026, 10, 21)  # a Wednesday; fixed so date tests are stable


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    path = tmp_path / "closer.db"
    seed(path, today=TODAY)
    monkeypatch.setattr(db, "DB_PATH", path)  # the API endpoints use this database
    monkeypatch.setattr(db, "UPLOADS_DIR", tmp_path / "uploads")
    return path


def fake_response(text="hello", model="gpt-6-luna-global", input_tokens=1000, cached=600,
                  cache_write=300, output=200, reasoning=50):
    return SimpleNamespace(
        output_text=text,
        model=model,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            input_tokens_details=SimpleNamespace(cached_tokens=cached, cache_write_tokens=cache_write),
            output_tokens=output,
            output_tokens_details=SimpleNamespace(reasoning_tokens=reasoning),
        ),
    )


class FakeClient:
    """Stands in for openai.OpenAI. Records every request so tests can inspect it."""

    def __init__(self, response=None):
        self.requests = []
        self.response = response or fake_response()
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self.response


@pytest.fixture
def fake_client():
    return FakeClient()
