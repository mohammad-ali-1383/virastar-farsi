"""Tests for the optional, consent-based evaluation log.

The endpoint enforces consent (`correction-logging.md`): a record is appended
only when the request explicitly asks for it, it names the provider and model
that actually produced the correction (including the Gemini fallback), and a
failed write never breaks the correction and never leaks the text.

All provider traffic is mocked and every log path is a temporary file, so these
tests need no key, make no network request, and touch no real ``logs/`` file.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.errors import INVALID_INPUT, TEXT_TOO_LONG, ProviderAuthError
from app.services.correction import GEMINI_PROVIDER, GROQ_PROVIDER
from app.services.correction_log import CorrectionLogStore, record_line
from tests.conftest import (
    GEMINI_TEST_KEY,
    GEMINI_URL_MARKER,
    GROQ_TEST_KEY,
    GROQ_URL_MARKER,
    ProviderTransport,
    StubCorrectionService,
    gemini_response,
    groq_error,
)

CORRECT_URL = "/api/v1/correct"
PERSIAN_TEXT = "سلام این یک متن ازمایشی است"
CORRECTED_TEXT = "سلام، این یک متن آزمایشی است."

# The documented record keys, in the order the store writes them.
RECORD_KEYS = ["timestamp", "original_text", "provider", "model", "corrected_text"]


def _rows(path: Path) -> list[dict[str, object]]:
    """Parse the JSON Lines file the endpoint wrote."""
    assert path.exists(), f"expected records in {path.name}"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _body(store: bool) -> dict[str, object]:
    return {"text": PERSIAN_TEXT, "store_for_evaluation": store}


def _file_signature(path: Path) -> tuple[int, int] | None:
    """Size and modification time of ``path``, or ``None`` when it is absent.

    Used to prove a real evaluation log is left untouched without reading it:
    the stored text is user content and must not be opened by the tests. A real
    log may legitimately exist once the service has been used with consent, so
    the guard compares its state rather than requiring it to be missing.
    """
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_size, stat.st_mtime_ns)


# The deployment's configured log file (the autouse fixture must keep tests off it).
REPOSITORY_LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "corrections.jsonl"


def test_tests_never_write_to_the_repository_log(
    client: TestClient, correction_log_path: Path
) -> None:
    # Guard for the whole suite: even a consented request through the endpoint
    # writes to the temporary path, never to a real `logs/corrections.jsonl`.
    #
    # The real file is not required to be absent: a deployment that has served
    # a consented request owns one, and deleting it is not a test's job. What
    # must hold is that the request is recorded in the temporary path and the
    # real path is byte-for-byte untouched (same size and mtime).
    assert settings.correction_log_path == correction_log_path
    assert settings.correction_log_path != REPOSITORY_LOG_PATH

    repository_before = _file_signature(REPOSITORY_LOG_PATH)

    client.post(CORRECT_URL, json=_body(True))

    assert correction_log_path.exists()
    assert len(_rows(correction_log_path)) == 1
    assert _file_signature(REPOSITORY_LOG_PATH) == repository_before


# --- Consent given ----------------------------------------------------------


def test_consented_request_appends_one_record(
    client: TestClient, stub_service: StubCorrectionService, correction_log_path: Path
) -> None:
    stub_service.result = CORRECTED_TEXT
    stub_service.provider = GROQ_PROVIDER
    stub_service.model = "openai/gpt-oss-20b"

    response = client.post(CORRECT_URL, json=_body(True))

    assert response.status_code == 200
    assert response.json() == {"corrected_text": CORRECTED_TEXT}
    rows = _rows(correction_log_path)
    assert len(rows) == 1
    assert list(rows[0]) == RECORD_KEYS
    assert rows[0]["original_text"] == PERSIAN_TEXT
    assert rows[0]["corrected_text"] == CORRECTED_TEXT
    assert rows[0]["provider"] == GROQ_PROVIDER
    assert rows[0]["model"] == "openai/gpt-oss-20b"


def test_record_timestamp_is_iso_8601_with_a_timezone(
    client: TestClient, correction_log_path: Path
) -> None:
    client.post(CORRECT_URL, json=_body(True))

    raw = _rows(correction_log_path)[0]["timestamp"]
    assert isinstance(raw, str)
    stamp = datetime.fromisoformat(raw)
    assert stamp.tzinfo is not None
    assert stamp.utcoffset() == timezone.utc.utcoffset(None)


def test_record_is_written_as_one_json_line_per_request(
    client: TestClient, correction_log_path: Path
) -> None:
    client.post(CORRECT_URL, json=_body(True))
    client.post(CORRECT_URL, json=_body(True))

    lines = correction_log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert all(json.loads(line) for line in lines)
    # Appending must not rewrite or truncate the earlier record.
    assert _rows(correction_log_path)[1]["original_text"] == PERSIAN_TEXT


def test_consented_record_names_the_provider_that_succeeded(
    client: TestClient, stub_service: StubCorrectionService, correction_log_path: Path
) -> None:
    stub_service.provider = GEMINI_PROVIDER
    stub_service.model = "gemini-3.5-flash-lite"

    client.post(CORRECT_URL, json=_body(True))

    row = _rows(correction_log_path)[0]
    assert row["provider"] == GEMINI_PROVIDER
    assert row["model"] == "gemini-3.5-flash-lite"


def test_fallback_success_is_logged_with_the_fallback_provider(
    monkeypatch: pytest.MonkeyPatch,
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_keys: None,
    correction_log_path: Path,
) -> None:
    # Real adapters: Groq is rate limited, so Gemini answers — and Gemini is
    # what the record must name.
    monkeypatch.setattr(settings, "groq_model", "openai/gpt-oss-20b")
    monkeypatch.setattr(settings, "gemini_model", "gemini-test-model")
    provider_transport.respond_for(GROQ_URL_MARKER, lambda _: groq_error(429))
    provider_transport.respond_for(GEMINI_URL_MARKER, lambda _: gemini_response(CORRECTED_TEXT))

    response = provider_client.post(CORRECT_URL, json=_body(True))

    assert response.status_code == 200
    assert response.json() == {"corrected_text": CORRECTED_TEXT}
    assert len(provider_transport.requests) == 2
    row = _rows(correction_log_path)[0]
    assert row["provider"] == GEMINI_PROVIDER
    assert row["model"] == "gemini-test-model"
    assert row["original_text"] == PERSIAN_TEXT
    assert row["corrected_text"] == CORRECTED_TEXT


# --- Consent declined -------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"text": PERSIAN_TEXT}, id="flag-absent"),
        pytest.param({"text": PERSIAN_TEXT, "store_for_evaluation": False}, id="flag-false"),
    ],
)
def test_declined_request_writes_nothing(
    client: TestClient,
    stub_service: StubCorrectionService,
    correction_log_path: Path,
    payload: dict[str, object],
) -> None:
    stub_service.result = CORRECTED_TEXT

    response = client.post(CORRECT_URL, json=payload)

    # The correction still happens — only the record is skipped.
    assert response.status_code == 200
    assert response.json() == {"corrected_text": CORRECTED_TEXT}
    assert not correction_log_path.exists()
    assert not correction_log_path.parent.exists()


def test_declined_request_leaves_existing_records_untouched(
    client: TestClient, correction_log_path: Path
) -> None:
    client.post(CORRECT_URL, json=_body(True))
    before = correction_log_path.read_text(encoding="utf-8")

    client.post(CORRECT_URL, json=_body(False))

    # Appending only: a declined request neither adds nor truncates a record.
    assert correction_log_path.read_text(encoding="utf-8") == before
    assert len(_rows(correction_log_path)) == 1


def test_declined_texts_reach_neither_the_file_nor_the_application_logs(
    caplog: pytest.LogCaptureFixture,
    client: TestClient,
    correction_log_path: Path,
) -> None:
    with caplog.at_level(logging.DEBUG):
        client.post(CORRECT_URL, json=_body(False))

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert PERSIAN_TEXT not in logged
    assert CORRECTED_TEXT not in logged
    assert not correction_log_path.exists()


def test_declined_request_with_a_failing_provider_still_writes_nothing(
    client: TestClient,
    stub_service: StubCorrectionService,
    correction_log_path: Path,
) -> None:
    stub_service.error = ProviderAuthError("no key")

    response = client.post(CORRECT_URL, json=_body(True))

    # Every provider failure is the generic 503 (`api-contract.md`).
    assert response.status_code == 503
    # Consent was given, but there is no correction to evaluate: nothing stored.
    assert not correction_log_path.exists()


# --- Invalid input ----------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"text": "", "store_for_evaluation": True}, id="empty-text"),
        pytest.param({"text": "   ", "store_for_evaluation": True}, id="whitespace-only"),
        pytest.param({"text": "ا" * 1001, "store_for_evaluation": True}, id="text-too-long"),
        pytest.param({"text": None, "store_for_evaluation": True}, id="null-text"),
        # The flag must be a JSON boolean: a string or number is invalid input
        # rather than an ambiguous opt-in (`api-contract.md`).
        pytest.param({"text": PERSIAN_TEXT, "store_for_evaluation": "yes"}, id="flag-string-yes"),
        pytest.param({"text": PERSIAN_TEXT, "store_for_evaluation": "no"}, id="flag-string-no"),
        pytest.param({"text": PERSIAN_TEXT, "store_for_evaluation": "true"}, id="flag-string-true"),
        pytest.param({"text": PERSIAN_TEXT, "store_for_evaluation": 1}, id="flag-number"),
        pytest.param({"text": PERSIAN_TEXT, "store_for_evaluation": ["yes"]}, id="flag-list"),
        pytest.param({"text": PERSIAN_TEXT, "store_for_evaluation": None}, id="flag-null"),
    ],
)
def test_invalid_requests_store_nothing_even_with_consent(
    client: TestClient,
    stub_service: StubCorrectionService,
    correction_log_path: Path,
    payload: dict[str, object],
) -> None:
    response = client.post(CORRECT_URL, json=payload)

    assert response.status_code == 400
    assert response.json()["error"] in (INVALID_INPUT, TEXT_TOO_LONG)
    assert stub_service.received == []
    assert not correction_log_path.exists()


# --- Safe logging failures --------------------------------------------------


def test_write_failure_still_returns_the_correction(
    caplog: pytest.LogCaptureFixture,
    client: TestClient,
    stub_service: StubCorrectionService,
    correction_log_path: Path,
) -> None:
    stub_service.result = CORRECTED_TEXT
    # A directory where the log file should be: the append cannot succeed.
    correction_log_path.mkdir(parents=True)

    with caplog.at_level(logging.WARNING):
        response = client.post(CORRECT_URL, json=_body(True))

    # The user's correction is unaffected by the logging failure.
    assert response.status_code == 200
    assert response.json() == {"corrected_text": CORRECTED_TEXT}
    # The failure is reported, sanitized: no text, no keys, no traceback.
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "Could not append the consented evaluation record" in logged
    assert PERSIAN_TEXT not in logged
    assert CORRECTED_TEXT not in logged
    assert GROQ_TEST_KEY not in logged
    assert GEMINI_TEST_KEY not in logged
    assert "Traceback" not in logged


def test_write_failure_when_the_directory_cannot_be_created(
    caplog: pytest.LogCaptureFixture,
    client: TestClient,
    stub_service: StubCorrectionService,
    correction_log_path: Path,
) -> None:
    stub_service.result = CORRECTED_TEXT
    # A file where the directory should be: creating `logs/` must fail.
    correction_log_path.parent.write_text("not a directory", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        response = client.post(CORRECT_URL, json=_body(True))

    assert response.status_code == 200
    assert response.json() == {"corrected_text": CORRECTED_TEXT}
    assert not correction_log_path.exists()
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "Could not append the consented evaluation record" in logged
    assert PERSIAN_TEXT not in logged


def test_provider_failure_leaves_the_log_untouched(
    client: TestClient,
    stub_service: StubCorrectionService,
    correction_log_path: Path,
) -> None:
    stub_service.error = ProviderAuthError("provider rejected the key")

    response = client.post(CORRECT_URL, json=_body(True))

    assert response.status_code == 503
    assert "corrected_text" not in response.json()
    assert not correction_log_path.exists()


def test_second_request_is_recorded_after_a_failed_write(
    client: TestClient,
    stub_service: StubCorrectionService,
    correction_log_path: Path,
) -> None:
    stub_service.result = CORRECTED_TEXT
    correction_log_path.mkdir(parents=True)
    client.post(CORRECT_URL, json=_body(True))

    # Removing the obstacle lets the next consented request be recorded: the
    # failure is not cached or reported as success.
    correction_log_path.rmdir()
    response = client.post(CORRECT_URL, json=_body(True))

    assert response.status_code == 200
    assert len(_rows(correction_log_path)) == 1


# --- The store itself -------------------------------------------------------


def test_record_line_serializes_the_documented_fields_with_persian_text() -> None:
    stamp = datetime(2026, 10, 16, 12, 30, tzinfo=timezone.utc)

    line = record_line(
        timestamp=stamp,
        original_text=PERSIAN_TEXT,
        provider=GEMINI_PROVIDER,
        model="gemini-test-model",
        corrected_text=CORRECTED_TEXT,
    )

    assert "\n" not in line
    row = json.loads(line)
    assert list(row) == RECORD_KEYS
    assert row["timestamp"] == stamp.isoformat()
    # Persian is stored readably, not as \u escapes.
    assert PERSIAN_TEXT in line


def test_store_creates_missing_directories(tmp_path: Path) -> None:
    store = CorrectionLogStore(tmp_path / "nested" / "logs" / "corrections.jsonl")

    written = asyncio.run(
        store.append(
            original_text=PERSIAN_TEXT,
            provider=GROQ_PROVIDER,
            model="openai/gpt-oss-20b",
            corrected_text=CORRECTED_TEXT,
        )
    )

    assert written is True
    assert store.path.exists()
    assert len(_rows(store.path)) == 1


def test_store_returns_false_and_keeps_the_text_out_of_the_logs(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    store = CorrectionLogStore(tmp_path)  # a directory, not a file
    assert store.path.is_dir()

    with caplog.at_level(logging.WARNING):
        written = asyncio.run(
            store.append(
                original_text=PERSIAN_TEXT,
                provider=GROQ_PROVIDER,
                model="openai/gpt-oss-20b",
                corrected_text=CORRECTED_TEXT,
            )
        )

    assert written is False
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "Could not append the consented evaluation record" in logged
    assert PERSIAN_TEXT not in logged
    assert CORRECTED_TEXT not in logged


def test_openai_style_and_gemini_bodies_are_never_logged(
    caplog: pytest.LogCaptureFixture,
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_keys: None,
    correction_log_path: Path,
) -> None:
    # A provider failure whose body echoes request material must not reach the
    # application log when the request also consented to logging.
    provider_transport.respond_for(
        GROQ_URL_MARKER,
        lambda _: httpx.Response(
            503,
            json={"error": {"message": f"overloaded for key {GROQ_TEST_KEY}", "type": "server_error"}},
        ),
    )
    provider_transport.respond_for(GEMINI_URL_MARKER, lambda _: gemini_response(CORRECTED_TEXT))

    with caplog.at_level(logging.DEBUG):
        response = provider_client.post(CORRECT_URL, json=_body(True))

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert response.status_code == 200
    assert GROQ_TEST_KEY not in logged
    assert GROQ_TEST_KEY not in response.text
    assert "overloaded for key" not in logged
    # The consented record stores the text on purpose; the *logs* stay clean.
    assert CORRECTED_TEXT not in logged
