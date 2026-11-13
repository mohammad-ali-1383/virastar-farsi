"""Tests for ``POST /api/v1/correct`` (`api-contract.md`, `testing.md`)."""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.config import settings
from app.errors import (
    HTTP_ERROR,
    INTERNAL_ERROR,
    INVALID_INPUT,
    METHOD_NOT_ALLOWED,
    NOT_FOUND,
    PROVIDER_UNAVAILABLE,
    TEXT_TOO_LONG,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.main import app
from app.schemas import CorrectRequest
from tests.conftest import StubCorrectionService

CORRECT_URL = "/api/v1/correct"

# Documented message for TEXT_TOO_LONG (`api-contract.md` → Error Response).
TEXT_TOO_LONG_MESSAGE = "متن واردشده بیش از حداکثر مجاز است."

PERSIAN_TEXT = "سلام، این یک متن آزمایشی است."


# --- Success path -----------------------------------------------------------


def test_valid_text_returns_documented_success_shape(
    client: TestClient, stub_service: StubCorrectionService
) -> None:
    stub_service.result = "سلام، این یک متن آزمایشی است."

    response = client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"corrected_text": "سلام، این یک متن آزمایشی است."}
    assert list(response.json()) == ["corrected_text"]


def test_valid_text_returns_non_empty_corrected_text(
    client: TestClient, stub_service: StubCorrectionService
) -> None:
    response = client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert response.status_code == 200
    assert response.json()["corrected_text"].strip()


def test_text_is_trimmed_before_it_reaches_the_service(
    client: TestClient, stub_service: StubCorrectionService
) -> None:
    client.post(CORRECT_URL, json={"text": "  متن  با  فاصله  "})

    # Inner spacing and line breaks are preserved, only the edges are trimmed.
    assert stub_service.received == ["متن  با  فاصله"]


def test_correction_route_is_registered(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "post" in paths["/api/v1/correct"]


def test_openapi_documents_only_the_responses_the_api_returns(
    client: TestClient,
) -> None:
    schema = client.get("/openapi.json").json()
    responses = schema["paths"]["/api/v1/correct"]["post"]["responses"]

    # 422 is never returned: validation errors are 400 INVALID_INPUT.
    assert set(responses) == {"200", "400", "500", "503"}
    schemas = schema.get("components", {}).get("schemas", {})
    assert "HTTPValidationError" not in schemas
    assert "ValidationError" not in schemas


def test_health_endpoint_still_works(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- Consent flag (`correction-logging.md`) ---------------------------------


def test_consent_flag_is_optional_and_defaults_to_not_storing() -> None:
    assert CorrectRequest(text=PERSIAN_TEXT).store_for_evaluation is False


def test_consent_flag_is_documented_in_the_openapi_schema(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    request_schema = schema["components"]["schemas"]["CorrectRequest"]

    assert "store_for_evaluation" in request_schema["properties"]
    # Optional: the documented default is the privacy-preserving one.
    assert "store_for_evaluation" not in request_schema.get("required", [])
    assert request_schema["properties"]["store_for_evaluation"]["default"] is False


def test_a_valid_request_with_either_consent_value_succeeds(
    client: TestClient, stub_service: StubCorrectionService
) -> None:
    stub_service.result = PERSIAN_TEXT

    for store in (True, False):
        response = client.post(CORRECT_URL, json={"text": PERSIAN_TEXT, "store_for_evaluation": store})

        assert response.status_code == 200
        assert response.json() == {"corrected_text": PERSIAN_TEXT}


def test_a_non_boolean_consent_value_is_invalid_input(client: TestClient) -> None:
    response = client.post(
        CORRECT_URL, json={"text": PERSIAN_TEXT, "store_for_evaluation": "true"}
    )

    assert response.status_code == 400
    assert response.json()["error"] == INVALID_INPUT


# --- Invalid input ----------------------------------------------------------

INVALID_BODIES = [
    pytest.param({}, id="missing-text"),
    pytest.param({"text": None}, id="null-text"),
    pytest.param({"text": ""}, id="empty-text"),
    pytest.param({"text": "   "}, id="spaces-only"),
    pytest.param({"text": "\n\t \r\n"}, id="whitespace-only"),
    pytest.param({"text": 123}, id="wrong-type-number"),
    pytest.param({"text": ["متن"]}, id="wrong-type-list"),
]


@pytest.mark.parametrize("body", INVALID_BODIES)
def test_invalid_payloads_are_rejected(
    client: TestClient, body: dict[str, object]
) -> None:
    response = client.post(CORRECT_URL, json=body)

    assert response.status_code == 400
    assert response.json()["error"] == INVALID_INPUT


def test_unknown_fields_are_ignored(client: TestClient) -> None:
    response = client.post(CORRECT_URL, json={"text": PERSIAN_TEXT, "extra": "ignored"})

    assert response.status_code == 200


def test_malformed_json_is_rejected(client: TestClient) -> None:
    response = client.post(
        CORRECT_URL,
        content="{not valid json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == INVALID_INPUT


def test_non_json_body_is_rejected(client: TestClient) -> None:
    response = client.post(CORRECT_URL, content="متن ساده بدون JSON")

    assert response.status_code == 400
    assert response.json()["error"] == INVALID_INPUT


def test_validation_error_does_not_reach_the_service(
    client: TestClient, stub_service: StubCorrectionService
) -> None:
    client.post(CORRECT_URL, json={"text": "   "})

    assert stub_service.received == []


# --- Length boundary --------------------------------------------------------


def test_text_of_exactly_max_length_is_accepted(
    client: TestClient, stub_service: StubCorrectionService
) -> None:
    text = "ا" * 1000

    response = client.post(CORRECT_URL, json={"text": text})

    assert response.status_code == 200
    assert response.json() == {"corrected_text": text}
    assert len(stub_service.received[0]) == 1000


def test_text_one_character_over_max_length_is_rejected(
    client: TestClient, stub_service: StubCorrectionService
) -> None:
    response = client.post(CORRECT_URL, json={"text": "ا" * 1001})

    assert response.status_code == 400
    assert response.json() == {
        "error": TEXT_TOO_LONG,
        "message": TEXT_TOO_LONG_MESSAGE,
    }
    assert stub_service.received == []


def test_length_limit_ignores_surrounding_whitespace(client: TestClient) -> None:
    text = "  " + "ا" * 1000 + "  \n"

    response = client.post(CORRECT_URL, json={"text": text})

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("length", "expected_status"),
    [(5, 200), (6, 400)],
)
def test_limit_is_configurable(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    length: int,
    expected_status: int,
) -> None:
    monkeypatch.setattr(settings, "max_text_length", 5)

    response = client.post(CORRECT_URL, json={"text": "ا" * length})

    assert response.status_code == expected_status
    if expected_status == 400:
        assert response.json()["error"] == TEXT_TOO_LONG


# --- Provider failures ------------------------------------------------------


# Every provider classification the correction service can raise maps to the
# same public response (`api-contract.md`): one generic 503. The specific class
# is preserved only in the sanitized server log.
PROVIDER_ERRORS = [
    ProviderUnavailableError,
    ProviderTimeoutError,
    ProviderRateLimitError,
    ProviderAuthError,
]

# Documented message for PROVIDER_UNAVAILABLE (`api-contract.md`).
SERVICE_ERROR_MESSAGE = "سرویس اصلاح متن در حال حاضر در دسترس نیست."


@pytest.mark.parametrize("error", PROVIDER_ERRORS, ids=lambda error: error.__name__)
def test_every_provider_failure_returns_the_generic_service_error(
    client: TestClient,
    stub_service: StubCorrectionService,
    error: type[Exception],
) -> None:
    stub_service.error = error("raw provider detail: sk-live-000")

    response = client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert response.status_code == 503
    body = response.json()
    assert body == {"error": PROVIDER_UNAVAILABLE, "message": SERVICE_ERROR_MESSAGE}
    # Never fabricate corrected text when the provider fails.
    assert "corrected_text" not in body
    # Raw provider details must not leak into the response.
    assert "sk-live-000" not in response.text


@pytest.mark.parametrize("empty_result", ["", "   ", "\n"])
def test_empty_provider_response_is_not_a_success(
    client: TestClient, stub_service: StubCorrectionService, empty_result: str
) -> None:
    stub_service.result = empty_result

    response = client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert response.status_code == 500
    assert response.json()["error"] == INTERNAL_ERROR
    assert "corrected_text" not in response.json()


def test_unexpected_error_returns_documented_internal_error(
    tolerant_client: TestClient, stub_service: StubCorrectionService
) -> None:
    stub_service.error = RuntimeError("internal detail: sk-live-000")

    response = tolerant_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert response.status_code == 500
    assert response.json()["error"] == INTERNAL_ERROR
    assert "sk-live-000" not in response.text
    assert "Traceback" not in response.text


# --- Error format consistency ----------------------------------------------


# --- Framework errors -------------------------------------------------------


def test_unknown_route_uses_the_documented_error_structure(client: TestClient) -> None:
    response = client.get("/api/v1/unknown")

    assert response.status_code == 404
    assert response.json() == {
        "error": NOT_FOUND,
        "message": "مسیر درخواستی یافت نشد.",
    }
    # Starlette's `{"detail": ...}` body never reaches the client.
    assert "detail" not in response.json()
    assert "Not Found" not in response.text


def test_method_not_allowed_uses_the_documented_error_structure(
    client: TestClient,
) -> None:
    response = client.get(CORRECT_URL)

    assert response.status_code == 405
    assert response.json() == {
        "error": METHOD_NOT_ALLOWED,
        "message": "این روش درخواست برای این مسیر پشتیبانی نمی‌شود.",
    }
    # The framework's Allow header is part of the error response contract.
    assert "POST" in response.headers["allow"]


def test_other_framework_http_errors_use_the_generic_envelope(
    client: TestClient,
) -> None:
    def teapot() -> None:
        raise HTTPException(status_code=418, detail="internal detail: sk-live-000")

    # A temporary route exercises the catch-all handler without changing the
    # application's real routes; it is removed even if the request fails.
    route = APIRoute("/__test__/teapot", teapot, methods=["GET"])
    app.router.routes.append(route)
    try:
        response = client.get("/__test__/teapot")
    finally:
        app.router.routes.remove(route)

    assert response.status_code == 418
    assert response.json() == {
        "error": HTTP_ERROR,
        "message": "درخواست قابل پردازش نبود.",
    }
    assert "internal detail" not in response.text
    assert "sk-live-000" not in response.text


def test_every_error_uses_the_documented_structure(
    client: TestClient, stub_service: StubCorrectionService
) -> None:
    stub_service.error = ProviderUnavailableError("provider down")

    responses = [
        client.post(CORRECT_URL, json={"text": ""}),
        client.post(CORRECT_URL, json={"text": "ا" * 1001}),
        client.post(CORRECT_URL, content="{not valid json", headers={"Content-Type": "application/json"}),
        client.post(CORRECT_URL, json={"text": PERSIAN_TEXT}),
        client.get("/api/v1/unknown"),
        client.get(CORRECT_URL),
    ]

    for response in responses:
        assert response.status_code >= 400
        body = json.loads(response.text)
        assert sorted(body) == ["error", "message"]
        assert isinstance(body["error"], str) and body["error"]
        assert isinstance(body["message"], str) and body["message"]
        assert "corrected_text" not in body
