"""Configuration tests: defaults, environment overrides, and slot discovery.

The LLM provider layer is configured only through numbered slots, so these tests
cover the discovery rules: where the variables are read from, which of them win,
which slots count as configured, and how the optional extras are parsed. Only
placeholder values are used; the deployment's real `.env` is never read here.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

import app.config as app_config
from app.config import (
    DEFAULT_SLOT_TIMEOUT_SECONDS,
    Settings,
    load_provider_slots,
    settings,
)
from tests.conftest import define_slot

# Expected env-file locations, derived the same way the loader derives them.
_BACKEND_DIR = Path(app_config.__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_DIR.parent

_SLOT_VARIABLE_PATTERN = re.compile(
    r"^(API_KEY|BASE_URL|MODEL|TIMEOUT_SECONDS|EXTRA_BODY|NAME)_\d+$"
)


@pytest.fixture(autouse=True)
def isolated_slot_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep these tests off the machine's `.env` files and slot variables."""
    monkeypatch.setattr(app_config, "_ENV_FILES", ())

    for key in list(os.environ):
        if _SLOT_VARIABLE_PATTERN.match(key.upper()):
            monkeypatch.delenv(key, raising=False)


# --- Existing settings ------------------------------------------------------


def test_default_max_text_length() -> None:
    assert Settings(_env_file=None).max_text_length == 1000


def test_max_text_length_is_configurable() -> None:
    assert Settings(_env_file=None, max_text_length=42).max_text_length == 42


def test_default_cors_origins_are_development_origins() -> None:
    origins = Settings(_env_file=None).cors_origins

    assert "http://localhost:5173" in origins
    assert "http://127.0.0.1:5173" in origins


def test_module_settings_load() -> None:
    assert settings.max_text_length > 0


def test_max_text_length_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAX_TEXT_LENGTH", "55")

    assert Settings(_env_file=None).max_text_length == 55


@pytest.fixture
def root_env_file(tmp_path: Path) -> Path:
    """A root-style dotenv file (no real secrets, only a text limit)."""
    path = tmp_path / "root.env"
    path.write_text("MAX_TEXT_LENGTH=111\n", encoding="utf-8")
    return path


@pytest.fixture
def backend_env_file(tmp_path: Path) -> Path:
    path = tmp_path / "backend.env"
    path.write_text("MAX_TEXT_LENGTH=222\n", encoding="utf-8")
    return path


def test_env_files_cover_the_repo_root_and_backend() -> None:
    # Guards MODEL-002: `.env` must not be resolved relative to the current
    # working directory, otherwise the documented root `.env` is ignored when
    # running `cd backend && uvicorn app.main:app`.
    env_files = tuple(Path(path) for path in Settings.model_config["env_file"])

    assert env_files == (_REPO_ROOT / ".env", _BACKEND_DIR / ".env")
    # Absolute paths are what make the lookup independent of the CWD.
    assert all(path.is_absolute() for path in env_files)


def test_root_env_file_works_from_any_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Changing the CWD must not change which env files the loader targets.
    monkeypatch.chdir(tmp_path)

    env_files = tuple(Path(path) for path in Settings.model_config["env_file"])

    assert env_files[0] == (_REPO_ROOT / ".env").resolve()


def test_later_env_file_wins(root_env_file: Path, backend_env_file: Path) -> None:
    # The loader lists the root file first and `backend/.env` second, so the
    # backend file overrides the root one. This locks that contract.
    loaded = Settings(_env_file=(root_env_file, backend_env_file))

    assert loaded.max_text_length == 222


def test_a_root_env_file_is_loaded(root_env_file: Path) -> None:
    # End-to-end check that a root-style `.env` value reaches the settings.
    loaded = Settings(_env_file=root_env_file)

    assert loaded.max_text_length == 111


def test_legacy_provider_selection_key_is_ignored(tmp_path: Path) -> None:
    # There is no provider-selection setting: the slots are ordered by index.
    # A `.env` written for the old configuration must still start the
    # application instead of failing on the unknown key.
    path = tmp_path / "root.env"
    path.write_text(
        "CORRECTION_PROVIDER=gemini\nMAX_TEXT_LENGTH=123\n", encoding="utf-8"
    )

    loaded = Settings(_env_file=path)

    assert not hasattr(loaded, "correction_provider")
    assert loaded.max_text_length == 123


def test_vendor_settings_are_gone() -> None:
    # The vendor-specific settings were replaced by the generic slot variables.
    assert "groq_api_key" not in Settings.model_fields
    assert "gemini_api_key" not in Settings.model_fields


# --- Settings tolerance for the slot variables ------------------------------


def test_settings_ignore_slot_variables_without_losing_validation() -> None:
    loaded = Settings(
        _env_file=None,
        api_key_1="placeholder-not-real",
        base_url_1="https://slot.test/v1",
        model_1="placeholder-model",
        extra_body_1='{"thinking":{"type":"disabled"}}',
        max_text_length=7,
    )

    # The slot variables are not settings fields; they belong to the discovery.
    assert not hasattr(loaded, "api_key_1")
    assert loaded.max_text_length == 7
    # Existing validation is unchanged: a bad value is still rejected.
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_text_length="not-a-number")


# --- Slot discovery ---------------------------------------------------------


def test_slots_are_discovered_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    define_slot(
        monkeypatch,
        1,
        base_url="https://one.test/v1",
        api_key="placeholder-one",
        model="model-one",
    )
    define_slot(
        monkeypatch, 2, base_url="https://two.test/v1", api_key="placeholder-two", model="model-two"
    )

    slots = load_provider_slots()

    assert [slot.index for slot in slots] == [1, 2]
    assert [(slot.name, slot.base_url, slot.model) for slot in slots] == [
        ("slot-1", "https://one.test/v1", "model-one"),
        ("slot-2", "https://two.test/v1", "model-two"),
    ]
    # The key stays a secret even on the configuration object.
    assert slots[0].api_key.get_secret_value() == "placeholder-one"
    assert "placeholder-one" not in repr(slots[0])
    # The optional timeout defaults to 20 seconds.
    assert slots[0].timeout_seconds == DEFAULT_SLOT_TIMEOUT_SECONDS


def test_slots_are_discovered_from_a_dotenv_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "API_KEY_1=placeholder-one\n"
        "BASE_URL_1=https://one.test/v1\n"
        "MODEL_1=model-one\n"
        "API_KEY_5=placeholder-five\r\n"
        "BASE_URL_5=https://five.test/v1\r\n"
        "MODEL_5=model-five\r\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(app_config, "_ENV_FILES", (path,))

    slots = load_provider_slots()

    # Gaps in the numbering are allowed and the order is numeric.
    assert [slot.index for slot in slots] == [1, 5]
    assert slots[1].model == "model-five"
    # Carriage returns from a CRLF file never become part of a value.
    assert slots[1].api_key.get_secret_value() == "placeholder-five"


def test_the_process_environment_wins_over_the_dotenv_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "API_KEY_1=from-file\n"
        "BASE_URL_1=https://file.test/v1\n"
        "MODEL_1=file-model\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(app_config, "_ENV_FILES", (path,))
    define_slot(
        monkeypatch, 1, base_url="https://env.test/v1", api_key="from-env", model="env-model"
    )

    slots = load_provider_slots()

    assert len(slots) == 1
    assert slots[0].api_key.get_secret_value() == "from-env"
    assert slots[0].base_url == "https://env.test/v1"
    assert slots[0].model == "env-model"


def test_more_than_ten_slots_are_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    for index in range(1, 13):
        define_slot(monkeypatch, index, base_url=f"https://slot{index}.test/v1")

    slots = load_provider_slots()

    assert len(slots) == 12
    assert [slot.index for slot in slots] == list(range(1, 13))
    assert [slot.name for slot in slots] == [f"slot-{index}" for index in range(1, 13)]


def test_an_incomplete_slot_is_skipped_with_a_safe_warning(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_KEY_3", "placeholder-three-not-real")
    monkeypatch.setenv("BASE_URL_3", "  \r\n")
    monkeypatch.setenv("MODEL_3", "model-three")

    with caplog.at_level(logging.WARNING):
        slots = load_provider_slots()

    assert slots == []
    logged = "\n".join(record.getMessage() for record in caplog.records)
    # One line naming the index and the missing variable, and nothing else.
    assert "slot 3" in logged
    assert "BASE_URL_3" in logged
    assert "placeholder-three-not-real" not in logged
    assert "API_KEY_3" not in logged


def test_an_incomplete_slot_does_not_hide_the_other_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    define_slot(monkeypatch, 1, base_url="https://one.test/v1")
    monkeypatch.setenv("API_KEY_2", "placeholder-two")
    monkeypatch.setenv("BASE_URL_2", "https://two.test/v1")
    # MODEL_2 is missing.
    define_slot(monkeypatch, 3, base_url="https://three.test/v1")

    slots = load_provider_slots()

    assert [slot.index for slot in slots] == [1, 3]


def test_a_slot_needs_an_api_key_to_exist_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # BASE_URL_4/MODEL_4 alone do not define a slot: API_KEY_<N> is the index.
    monkeypatch.setenv("BASE_URL_4", "https://four.test/v1")
    monkeypatch.setenv("MODEL_4", "model-four")

    assert load_provider_slots() == []


def test_values_are_trimmed_of_whitespace_and_carriage_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_KEY_1", "  placeholder-one\r\n")
    monkeypatch.setenv("BASE_URL_1", " https://one.test/v1 \r\n")
    monkeypatch.setenv("MODEL_1", " model-one \r")

    slots = load_provider_slots()

    assert slots[0].api_key.get_secret_value() == "placeholder-one"
    assert slots[0].base_url == "https://one.test/v1"
    assert slots[0].model == "model-one"


# --- Optional slot variables ------------------------------------------------


def test_slot_name_defaults_and_is_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    define_slot(monkeypatch, 1, base_url="https://one.test/v1")
    define_slot(monkeypatch, 2, base_url="https://two.test/v1", name="zai")

    slots = load_provider_slots()

    assert [slot.name for slot in slots] == ["slot-1", "zai"]


def test_slot_timeout_is_configurable_and_falls_back_to_the_default(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    define_slot(monkeypatch, 1, base_url="https://one.test/v1", timeout_seconds="7.5")
    define_slot(monkeypatch, 2, base_url="https://two.test/v1", timeout_seconds="soon")

    with caplog.at_level(logging.WARNING):
        slots = load_provider_slots()

    assert slots[0].timeout_seconds == 7.5
    assert slots[1].timeout_seconds == DEFAULT_SLOT_TIMEOUT_SECONDS
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "TIMEOUT_SECONDS_2" in logged
    assert "soon" not in logged


def test_extra_body_is_parsed_as_a_json_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    define_slot(
        monkeypatch,
        1,
        base_url="https://z.ai.test/v4",
        extra_body='{"thinking":{"type":"disabled"}}',
    )

    slots = load_provider_slots()

    assert slots[0].extra_body == {"thinking": {"type": "disabled"}}


@pytest.mark.parametrize("opening", ["'", '"'])
def test_extra_body_tolerates_surrounding_quotes(
    opening: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A shell or `docker run --env` argument often keeps its quotes.
    define_slot(
        monkeypatch,
        1,
        base_url="https://one.test/v1",
        extra_body=f'{opening}{{"reasoning_effort":"low"}}{opening}',
    )

    assert load_provider_slots()[0].extra_body == {"reasoning_effort": "low"}


@pytest.mark.parametrize(
    "raw_extra_body",
    [
        pytest.param("{not json at all", id="invalid-json"),
        pytest.param("[1, 2, 3]", id="json-array"),
        pytest.param('"disabled"', id="json-string"),
        pytest.param("42", id="json-number"),
    ],
)
def test_an_unusable_extra_body_is_ignored_without_printing_it(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    raw_extra_body: str,
) -> None:
    define_slot(
        monkeypatch, 1, base_url="https://one.test/v1", extra_body=raw_extra_body
    )

    with caplog.at_level(logging.WARNING):
        slots = load_provider_slots()

    assert len(slots) == 1
    assert slots[0].extra_body is None
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "EXTRA_BODY_1" in logged
    # Never the content of the value.
    assert raw_extra_body not in logged


# --- Evaluation log ---------------------------------------------------------


def test_default_correction_log_path_is_under_the_repository_root() -> None:
    path = Settings(_env_file=None).correction_log_path

    # CWD-independent, like the env-file lookup (MODEL-002), so both documented
    # local workflows write the consented records to the same place.
    assert path == _REPO_ROOT / "logs" / "corrections.jsonl"
    assert path.is_absolute()


def test_correction_log_path_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORRECTION_LOG_PATH", "custom/records.jsonl")

    assert Settings(_env_file=None).correction_log_path == Path("custom/records.jsonl")
