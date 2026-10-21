"""Tests for .env loading: repo folder first, then one level up, key names, model."""

from pathlib import Path

from fastapi.testclient import TestClient

import app.main
from app.config import MODEL, SOURCE_PARENT, SOURCE_REPO, SOURCE_SHELL, load_settings


def make_layout(tmp_path: Path) -> Path:
    """submissions/closer, like the graders' folder layout. Returns the repo dir."""
    repo = tmp_path / "submissions" / "closer"
    repo.mkdir(parents=True)
    return repo


def test_parent_env_is_used_when_repo_has_none(tmp_path):
    repo = make_layout(tmp_path)
    (repo.parent / ".env").write_text("PORTKEY_API_KEY=parent-key\n")

    s = load_settings(repo, environ={})

    assert s.api_key == "parent-key"
    assert s.provider == "portkey"
    assert s.key_source == SOURCE_PARENT
    assert s.public_status(repo)["env_files"] == ["folder above repo"]


def test_repo_env_wins_over_parent_env(tmp_path):
    repo = make_layout(tmp_path)
    (repo / ".env").write_text("PORTKEY_API_KEY=repo-key\n")
    (repo.parent / ".env").write_text("PORTKEY_API_KEY=parent-key\n")

    s = load_settings(repo, environ={})

    assert s.api_key == "repo-key"
    assert s.key_source == SOURCE_REPO
    assert s.public_status(repo)["env_files"] == ["repo folder", "folder above repo"]


def test_parent_fills_in_keys_missing_from_repo_env(tmp_path):
    repo = make_layout(tmp_path)
    (repo / ".env").write_text("PORTKEY_API_KEY=\n")  # empty value
    (repo.parent / ".env").write_text("PORTKEY_API_KEY=parent-key\n")

    assert load_settings(repo, environ={}).api_key == "parent-key"


def test_shell_environment_wins_and_is_reported(tmp_path):
    repo = make_layout(tmp_path)
    (repo / ".env").write_text("PORTKEY_API_KEY=repo-key\n")

    s = load_settings(repo, environ={"PORTKEY_API_KEY": "shell-key"})

    assert (s.api_key, s.key_source) == ("shell-key", SOURCE_SHELL)


def test_portkey_preferred_when_both_keys_present(tmp_path):
    repo = make_layout(tmp_path)
    (repo.parent / ".env").write_text("PORTKEY_API_KEY=pk\nOPENAI_API_KEY=oa\n")

    s = load_settings(repo, environ={})

    assert (s.provider, s.api_key) == ("portkey", "pk")


def test_openai_key_used_when_no_portkey_key(tmp_path):
    repo = make_layout(tmp_path)
    (repo.parent / ".env").write_text("OPENAI_API_KEY=oa\n")

    assert load_settings(repo, environ={}).provider == "openai"


def test_other_key_names_are_ignored(tmp_path):
    repo = make_layout(tmp_path)
    (repo / ".env").write_text("PORTKEY_KEY=wrong-name\nopenai_api_key=wrong-case\n")

    s = load_settings(repo, environ={})

    assert s.api_key is None
    assert s.ai_enabled is False


def test_no_env_files_means_ai_disabled(tmp_path):
    repo = make_layout(tmp_path)

    s = load_settings(repo, environ={})

    assert s.provider is None
    assert s.ai_enabled is False
    assert s.env_files_loaded == ()


def test_model_is_always_gpt_6_luna(tmp_path):
    repo = make_layout(tmp_path)
    (repo / ".env").write_text("OPENAI_API_KEY=oa\nPORTKEY_MODEL=gpt-5.6-luna\n")

    assert load_settings(repo, environ={}).model == MODEL == "gpt-6-luna"


def test_key_never_appears_in_repr_or_health_endpoint(tmp_path, monkeypatch):
    repo = make_layout(tmp_path)
    (repo / ".env").write_text("PORTKEY_API_KEY=super-secret-value\n")
    s = load_settings(repo, environ={})
    monkeypatch.setattr(app.main, "settings", s)

    body = TestClient(app.main.app).get("/api/health").text

    assert "super-secret-value" not in repr(s)
    assert "super-secret-value" not in body
    assert '"ai_enabled":true' in body
