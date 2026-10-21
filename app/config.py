"""Settings and API-key loading.

The graders clone every repo into one folder and keep their .env one level
above it:

    submissions/
        .env            <- graders' keys
        closer/         <- this repo
            .env        <- (optional) your own keys

So we look for .env in the repo folder first, then in the folder above it.
Values from the repo .env win; the parent .env only fills in what is missing.
Real environment variables (e.g. exported in ~/.zshrc) win over both, so the
status reports exactly which source the key in use came from.

Only two key names are supported, spelled exactly: PORTKEY_API_KEY and
OPENAI_API_KEY. The model is always gpt-6-luna (the only model allowed on the
course key); there is no fallback model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values

REPO_DIR = Path(__file__).resolve().parent.parent

MODEL = "gpt-6-luna"
KEY_NAMES = ("PORTKEY_API_KEY", "OPENAI_API_KEY")


def env_file_candidates(repo_dir: Path = REPO_DIR) -> list[Path]:
    """The .env files we check, in priority order: repo folder, then one level up."""
    return [repo_dir / ".env", repo_dir.parent / ".env"]


SOURCE_REPO = "repo folder .env"
SOURCE_PARENT = "folder above repo .env"
SOURCE_SHELL = "shell environment"


@dataclass(frozen=True)
class Settings:
    provider: str | None  # "portkey", "openai", or None when no key was found
    api_key: str | None = field(repr=False)  # never printed or sent to the UI
    model: str
    env_files_loaded: tuple[Path, ...]
    key_source: str | None = None  # where the key in use came from (SOURCE_* above)

    @property
    def ai_enabled(self) -> bool:
        return self.api_key is not None

    def public_status(self, repo_dir: Path = REPO_DIR) -> dict:
        """Safe-to-show status for the UI. Says where keys came from, never the key itself."""
        labels = []
        for path in self.env_files_loaded:
            labels.append("repo folder" if path.parent == repo_dir else "folder above repo")
        return {
            "ai_enabled": self.ai_enabled,
            "provider": self.provider,
            "model": self.model,
            "key_source": self.key_source,
            "env_files": labels,
        }


def load_settings(
    repo_dir: Path = REPO_DIR,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Read keys from the environment and the two .env files without modifying os.environ."""
    environ = os.environ if environ is None else environ

    merged: dict[str, tuple[str, str]] = {}  # key name -> (value, source)
    loaded: list[Path] = []
    repo_env, parent_env = env_file_candidates(repo_dir)
    # Walk lowest priority first so higher-priority sources overwrite.
    for path, source in ((parent_env, SOURCE_PARENT), (repo_env, SOURCE_REPO)):
        if path.is_file():
            for k, v in dotenv_values(path).items():
                if k in KEY_NAMES and v:
                    merged[k] = (v, source)
            loaded.insert(0, path)  # report in priority order: repo, then parent
    for k in KEY_NAMES:
        if environ.get(k):
            merged[k] = (environ[k], SOURCE_SHELL)

    # Prefer the course key (Portkey). The graders' .env has both keys.
    for name, provider in (("PORTKEY_API_KEY", "portkey"), ("OPENAI_API_KEY", "openai")):
        if name in merged:
            key, source = merged[name]
            break
    else:
        provider, key, source = None, None, None

    return Settings(
        provider=provider,
        api_key=key,
        model=MODEL,
        env_files_loaded=tuple(loaded),
        key_source=source,
    )
