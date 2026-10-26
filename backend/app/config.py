"""Application configuration loaded from environment variables."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve the env files from this module's location instead of the current
# working directory, so the same configuration works whether the process is
# started from the repository root, from ``backend/``
# (``cd backend && uvicorn app.main:app``), or in a container (``WORKDIR /app``).
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_DIR.parent

# The shared, git-ignored secret file at the repository root is the documented
# location for both Docker Compose and local runs. ``backend/.env`` is still
# honored for backward compatibility and, being listed second, takes precedence.
_ENV_FILES = (_REPO_ROOT / ".env", _BACKEND_DIR / ".env")

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Runtime settings for the backend service.

    Values are read from environment variables and/or a local ``.env`` file.
    Secrets must never be hardcoded here.
    """

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"

    # Maximum accepted text length. Configurable per `api-contract.md`.
    max_text_length: int = 1000

    # Where consented correction records are appended, one JSON Lines row per
    # successful correction (`correction-logging.md`). Anchored to the repository
    # root like the env files, so the documented local workflows write to the
    # same place; overridable for tests and deployments. Nothing is written
    # unless a request opts in, and the directory is created on first use.
    correction_log_path: Path = _REPO_ROOT / "logs" / "corrections.jsonl"

    # Development origins allowed by CORS (`architecture.md`, section 4).
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    # --- Provider slots -----------------------------------------------------
    # The LLM provider layer is an ordered list of OpenAI-compatible "slots"
    # configured purely through the environment (`tech-stack.md`). For every
    # integer N >= 1 a slot may be defined with API_KEY_N (secret), BASE_URL_N
    # and MODEL_N, plus the optional TIMEOUT_SECONDS_N, EXTRA_BODY_N and NAME_N.
    # No vendor is special and the number of slots is not limited, so the slots
    # are not fields of this model: `load_provider_slots` below discovers them
    # dynamically. This model ignores those variables (``extra="ignore"``) while
    # still validating every setting it owns.


settings = Settings()


def get_max_text_length() -> int:
    """FastAPI dependency: the currently configured maximum text length.

    The value is read on every request so a configuration change (environment
    variable or `.env`) takes effect without recreating the application.
    """
    return settings.max_text_length


# --- Provider slot discovery ------------------------------------------------ #
# A slot is a numbered group of variables. Only ``API_KEY_<N>`` decides whether
# an index exists; the other variables of a group are then validated together.
_SLOT_KEY_PATTERN = re.compile(r"^API_KEY_([1-9]\d*)$")

DEFAULT_SLOT_TIMEOUT_SECONDS = 20.0
DEFAULT_SLOT_NAME_PREFIX = "slot-"


@dataclass(frozen=True)
class ProviderSlotConfig:
    """One fully configured provider slot, as read from the environment."""

    index: int
    name: str
    base_url: str
    model: str
    api_key: SecretStr
    timeout_seconds: float = DEFAULT_SLOT_TIMEOUT_SECONDS
    extra_body: dict[str, Any] | None = None


def _clean(raw: str | None) -> str:
    """Trim surrounding whitespace and the carriage returns of CRLF `.env` files."""
    if not raw:
        return ""
    return raw.replace("\r", "").strip()


def _read_env_files() -> dict[str, str]:
    """Values from the documented `.env` locations, later file winning.

    ``python-dotenv`` is used directly (it is already a dependency of
    ``pydantic-settings``) because slot names are discovered dynamically and
    therefore cannot be declared as model fields.
    """
    values: dict[str, str] = {}

    for env_file in _ENV_FILES:
        path = Path(env_file)
        if not path.is_file():
            continue
        for key, value in dotenv_values(path).items():
            if value is not None:
                values[key.upper()] = value

    return values


def _env_values() -> dict[str, str]:
    """All slot variables: `.env` files first, the process environment on top.

    Docker passes the same file through ``env_file``, so a container sees these
    values in the process environment; a local Uvicorn run sees them in `.env`.
    Real environment variables always win on conflicts.
    """
    values = _read_env_files()
    values.update({key.upper(): value for key, value in os.environ.items()})
    return values


def _extra_body(index: int, raw: str) -> dict[str, Any] | None:
    """Parse ``EXTRA_BODY_N`` as a JSON object, ignoring anything else.

    A value wrapped in single or double quotes (as a shell or `docker --env`
    argument often is) is unwrapped first. The raw value is never logged because
    it belongs to provider configuration.
    """
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()

    try:
        parsed = json.loads(text)
    except ValueError:
        logger.warning(
            "Provider slot %d: EXTRA_BODY_%d is not valid JSON and was ignored",
            index,
            index,
        )
        return None

    if not isinstance(parsed, dict):
        logger.warning(
            "Provider slot %d: EXTRA_BODY_%d is not a JSON object and was ignored",
            index,
            index,
        )
        return None

    return parsed


def _timeout_seconds(index: int, raw: str) -> float:
    """Per-slot timeout, defaulting to 20 seconds when absent or unusable."""
    if not raw:
        return DEFAULT_SLOT_TIMEOUT_SECONDS

    try:
        value = float(raw)
    except ValueError:
        value = 0.0

    if value <= 0:
        logger.warning(
            "Provider slot %d: TIMEOUT_SECONDS_%d is unusable; using the default",
            index,
            index,
        )
        return DEFAULT_SLOT_TIMEOUT_SECONDS

    return value


def _build_slot(index: int, values: Mapping[str, str]) -> ProviderSlotConfig | None:
    """Build one slot, or ``None`` when it is incomplete (skipped with a warning).

    Only the index and the name of the missing variable are logged; the values,
    and in particular the key, never reach the logs.
    """
    api_key = _clean(values.get(f"API_KEY_{index}"))
    base_url = _clean(values.get(f"BASE_URL_{index}"))
    model = _clean(values.get(f"MODEL_{index}"))

    missing = [
        f"{field}_{index}"
        for field, value in (
            ("API_KEY", api_key),
            ("BASE_URL", base_url),
            ("MODEL", model),
        )
        if not value
    ]

    if missing:
        logger.warning(
            "Provider slot %d is incomplete and will be skipped (missing %s)",
            index,
            ", ".join(missing),
        )
        return None

    name = _clean(values.get(f"NAME_{index}")) or f"{DEFAULT_SLOT_NAME_PREFIX}{index}"
    raw_extra_body = values.get(f"EXTRA_BODY_{index}") or ""
    extra_body = _extra_body(index, raw_extra_body) if raw_extra_body.strip() else None

    return ProviderSlotConfig(
        index=index,
        name=name,
        base_url=base_url,
        model=model,
        api_key=SecretStr(api_key),
        timeout_seconds=_timeout_seconds(
            index, _clean(values.get(f"TIMEOUT_SECONDS_{index}"))
        ),
        extra_body=extra_body,
    )


def load_provider_slots(env: Mapping[str, str] | None = None) -> list[ProviderSlotConfig]:
    """Discover the configured provider slots, ordered by their numeric index.

    Indexes may have gaps. When ``env`` is given it is used instead of the
    process environment and the `.env` files (tests), which keeps the discovery
    rule identical without depending on the machine's configuration.
    """
    if env is None:
        values = _env_values()
    else:
        values = {key.strip().upper(): value for key, value in env.items()}

    indexes = sorted(
        {
            int(match.group(1))
            for key in values
            if (match := _SLOT_KEY_PATTERN.match(key))
        }
    )

    return [
        slot
        for index in indexes
        if (slot := _build_slot(index, values)) is not None
    ]
