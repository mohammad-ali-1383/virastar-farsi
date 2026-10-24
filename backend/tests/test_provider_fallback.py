"""Tests for the automatic Groq -> Gemini fallback.

Every test drives the real adapters, the real correction service, and the real
endpoint through ``httpx.MockTransport`` (the ``provider_transport`` fixture
routes each provider's URL independently), with placeholder keys only. No real
API key is read and no network request is made (`testing.md`,
`coding-rules.md` -> Testing).

What is verified is the workflow: which provider is called, in which order, how
often, what it receives, and which public error a failure produces. Real
provider connectivity and live correction quality are *not* verified by these
mocked tests.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import settings
from app.errors import INVALID_INPUT, PROVIDER_UNAVAILABLE, TEXT_TOO_LONG
from app.services.correction import GEMINI_PROVIDER, GROQ_PROVIDER
from app.services.gemini import GEMINI_API_BASE_URL, GEMINI_API_VERSION
from app.services.groq import GROQ_CHAT_COMPLETIONS_URL
from app.services.prompt import PERSIAN_PROOFREADING_SYSTEM_PROMPT
from tests.conftest import (
    GEMINI_TEST_KEY,
    GEMINI_URL_MARKER,
    GROQ_TEST_KEY,
    GROQ_URL_MARKER,
    Handler,
    ProviderTransport,
    gemini_error,
    gemini_response,
    groq_error,
    groq_response,
)

CORRECT_URL = "/api/v1/correct"
PERSIAN_TEXT = "سلام این یک متن ازمایشی است"
GROQ_TEXT = "اصلاح‌شده با گروک"
GEMINI_TEXT = "اصلاح‌شده با گیمینی"

# The documented generic message for PROVIDER_UNAVAILABLE (`errors.py`).
SERVICE_ERROR_MESSAGE = "سرویس اصلاح متن در حال حاضر در دسترس نیست."


def _timeout(_: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("read timed out")


def _connection_error(_: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused")


# Groq failures that must trigger the fallback, one per documented category:
# quota/rate limit, authentication, server error, timeout, connection failure,
# and an invalid or empty provider response.
GROQ_FAILURES = [
    pytest.param(lambda _: groq_error(429), id="rate-limit-429"),
    pytest.param(lambda _: groq_error(401), id="auth-401"),
    pytest.param(lambda _: groq_error(503), id="server-503"),
    pytest.param(lambda _: groq_error(504), id="gateway-timeout-504"),
    pytest.param(_timeout, id="timeout"),
    pytest.param(_connection_error, id="connection-error"),
    pytest.param(lambda _: httpx.Response(200, json={"choices": []}), id="empty-response"),
    pytest.param(
        lambda _: httpx.Response(200, content=b"<html>gateway</html>", headers={"content-type": "text/html"}),
        id="unparseable-response",
    ),
]

GEMINI_FAILURES = [
    pytest.param(lambda _: gemini_error(429), id="rate-limit-429"),
    pytest.param(lambda _: gemini_error(401), id="auth-401"),
    pytest.param(lambda _: gemini_error(503), id="server-503"),
    pytest.param(_timeout, id="timeout"),
    pytest.param(_connection_error, id="connection-error"),
    pytest.param(lambda _: httpx.Response(200, json={"candidates": []}), id="empty-response"),
]


def _body_of(request: httpx.Request) -> dict[str, object]:
    return json.loads(request.content)


def _headers_of(request: httpx.Request) -> str:
    """All request headers as one lower-cased string (httpx folds the names)."""
    return " ".join(f"{name}:{value}".lower() for name, value in request.headers.items())


# --- Groq success -----------------------------------------------------------


def test_groq_success_is_returned_and_gemini_is_never_called(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_keys: None,
) -> None:
    # Any unexpected Gemini request fails the call, so a 200 proves Groq alone
    # answered it.
    provider_transport.respond_for(GROQ_URL_MARKER, lambda _: groq_response(GROQ_TEXT))
    provider_transport.respond_for(GEMINI_URL_MARKER, lambda _: httpx.Response(500))

    response = provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert response.status_code == 200
    assert response.json() == {"corrected_text": GROQ_TEXT}
    # Exactly one request, to Groq only.
    assert len(provider_transport.requests) == 1
    assert provider_transport.requests_for(GEMINI_URL_MARKER) == []
    assert GROQ_CHAT_COMPLETIONS_URL in provider_transport.urls[0]


def test_groq_is_always_tried_first(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_keys: None,
) -> None:
    provider_transport.respond_for(GROQ_URL_MARKER, lambda _: groq_response(GROQ_TEXT))

    provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert GROQ_CHAT_COMPLETIONS_URL in provider_transport.urls[0]


# --- Groq failure, Gemini success -------------------------------------------


@pytest.mark.parametrize("groq_failure", GROQ_FAILURES)
def test_groq_failure_falls_back_to_gemini(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_keys: None,
    groq_failure: Handler,
) -> None:
    provider_transport.respond_for(GROQ_URL_MARKER, groq_failure)
    provider_transport.respond_for(GEMINI_URL_MARKER, lambda _: gemini_response(GEMINI_TEXT))

    response = provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert response.status_code == 200
    assert response.json() == {"corrected_text": GEMINI_TEXT}
    # One attempt per provider, Groq first, and no retry of either.
    assert len(provider_transport.requests_for(GROQ_URL_MARKER)) == 1
    assert len(provider_transport.requests_for(GEMINI_URL_MARKER)) == 1
    assert len(provider_transport.requests) == 2
    assert GROQ_URL_MARKER in provider_transport.urls[0]


def test_fallback_resends_the_same_prompt_and_user_text(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_keys: None,
) -> None:
    provider_transport.respond_for(GROQ_URL_MARKER, lambda _: groq_error(429))
    provider_transport.respond_for(GEMINI_URL_MARKER, lambda _: gemini_response(GEMINI_TEXT))

    provider_client.post(CORRECT_URL, json={"text": "متن کاربر برای اصلاح"})

    gemini_request = provider_transport.requests_for(GEMINI_URL_MARKER)[0]
    body = _body_of(gemini_request)

    # The prompt stays the system instruction and the user's text a separate
    # entry — identical to what Groq received.
    assert body["systemInstruction"] == {
        "parts": [{"text": PERSIAN_PROOFREADING_SYSTEM_PROMPT}]
    }
    assert body["contents"] == [{"parts": [{"text": "متن کاربر برای اصلاح"}]}]
    assert "متن کاربر برای اصلاح" not in PERSIAN_PROOFREADING_SYSTEM_PROMPT
    # Identical instruction text: the fallback is the same request elsewhere.
    assert body["systemInstruction"]["parts"][0]["text"] == _body_of(
        provider_transport.requests_for(GROQ_URL_MARKER)[0]
    )["messages"][0]["content"]
    # The Gemini resource is selected by the configured model.
    assert f"/{GEMINI_API_VERSION}/models/" in str(gemini_request.url)
    assert GEMINI_API_BASE_URL in str(gemini_request.url)


def test_each_request_carries_only_its_own_providers_key(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_keys: None,
) -> None:
    provider_transport.respond_for(GROQ_URL_MARKER, lambda _: groq_error(503))
    provider_transport.respond_for(GEMINI_URL_MARKER, lambda _: gemini_response(GEMINI_TEXT))

    provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    groq_request = provider_transport.requests_for(GROQ_URL_MARKER)[0]
    gemini_request = provider_transport.requests_for(GEMINI_URL_MARKER)[0]

    assert f"authorization:bearer {GROQ_TEST_KEY}" in _headers_of(groq_request)
    assert f"x-goog-api-key:{GEMINI_TEST_KEY}" in _headers_of(gemini_request)

    for request, other_key in ((groq_request, GEMINI_TEST_KEY), (gemini_request, GROQ_TEST_KEY)):
        assert other_key not in _headers_of(request)
        assert other_key not in str(request.url)
        assert other_key not in request.content.decode("utf-8")


def test_fallback_without_a_configured_key_makes_no_request(
    monkeypatch: pytest.MonkeyPatch,
    provider_transport: ProviderTransport,
    provider_client: TestClient,
) -> None:
    # Groq is configured and fails; Gemini has no credential at all.
    monkeypatch.setattr(settings, "groq_api_key", SecretStr(GROQ_TEST_KEY))
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr(""))
    provider_transport.respond_for(GROQ_URL_MARKER, lambda _: groq_error(429))

    response = provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert response.status_code == 503
    assert response.json()["error"] == PROVIDER_UNAVAILABLE
    # The unconfigured credential is never used for a request.
    assert provider_transport.requests_for(GEMINI_URL_MARKER) == []


# --- Both providers fail ----------------------------------------------------


BOTH_FAIL_COMBINATIONS = [
    pytest.param(lambda _: groq_error(429), lambda _: gemini_error(429), id="rate-limit-both"),
    pytest.param(lambda _: groq_error(401), _timeout, id="auth-then-timeout"),
    pytest.param(_connection_error, lambda _: gemini_error(500), id="connection-then-server"),
    pytest.param(
        lambda _: httpx.Response(200, json={"choices": []}),
        lambda _: httpx.Response(200, json={"candidates": []}),
        id="both-empty-responses",
    ),
]


@pytest.mark.parametrize(("groq_failure", "gemini_failure"), BOTH_FAIL_COMBINATIONS)
def test_both_providers_failing_returns_one_safe_error(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_keys: None,
    groq_failure: Handler,
    gemini_failure: Handler,
) -> None:
    provider_transport.respond_for(GROQ_URL_MARKER, groq_failure)
    provider_transport.respond_for(GEMINI_URL_MARKER, gemini_failure)

    response = provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert response.status_code == 503
    body = response.json()
    assert body == {"error": PROVIDER_UNAVAILABLE, "message": SERVICE_ERROR_MESSAGE}
    assert "corrected_text" not in body
    # Never more than one attempt per provider.
    assert len(provider_transport.requests_for(GROQ_URL_MARKER)) == 1
    assert len(provider_transport.requests_for(GEMINI_URL_MARKER)) == 1


@pytest.mark.parametrize("gemini_failure", GEMINI_FAILURES)
def test_every_gemini_failure_after_a_groq_failure_returns_the_safe_error(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_keys: None,
    gemini_failure: Handler,
) -> None:
    # Groq is unavailable, so each fallback-side failure category — quota, auth,
    # server error, timeout, connection failure, empty response — must still be
    # reported as the same single generic error.
    provider_transport.respond_for(GROQ_URL_MARKER, lambda _: groq_error(429))
    provider_transport.respond_for(GEMINI_URL_MARKER, gemini_failure)

    response = provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert response.status_code == 503
    assert response.json() == {"error": PROVIDER_UNAVAILABLE, "message": SERVICE_ERROR_MESSAGE}
    # One attempt per provider, and the failed attempt is never repeated.
    assert len(provider_transport.requests_for(GROQ_URL_MARKER)) == 1
    assert len(provider_transport.requests_for(GEMINI_URL_MARKER)) == 1


def test_the_safe_error_is_the_same_for_every_failure_combination(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_keys: None,
) -> None:
    bodies = []

    for groq_failure, gemini_failure in (
        (lambda _: groq_error(429), lambda _: gemini_error(401)),
        (lambda _: groq_error(503), _timeout),
        (_connection_error, lambda _: httpx.Response(200, json={"candidates": []})),
    ):
        provider_transport.respond_for(GROQ_URL_MARKER, groq_failure)
        provider_transport.respond_for(GEMINI_URL_MARKER, gemini_failure)
        bodies.append(provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT}).json())

    # The client cannot tell which provider failed or why: one stable body.
    assert bodies == [{"error": PROVIDER_UNAVAILABLE, "message": SERVICE_ERROR_MESSAGE}] * 3


def test_both_providers_failing_never_leaks_detail_or_credentials(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_keys: None,
) -> None:
    # Provider error bodies that echo request material back, as real providers do.
    provider_transport.respond_for(
        GROQ_URL_MARKER,
        lambda _: groq_error(503, message=f"overloaded for key {GROQ_TEST_KEY}"),
    )
    provider_transport.respond_for(
        GEMINI_URL_MARKER,
        lambda _: gemini_error(503, message=f"overloaded for key {GEMINI_TEST_KEY}"),
    )

    response = provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert response.status_code == 503
    assert response.json()["error"] == PROVIDER_UNAVAILABLE
    assert GROQ_TEST_KEY not in response.text
    assert GEMINI_TEST_KEY not in response.text
    assert "overloaded for key" not in response.text
    assert GROQ_PROVIDER not in response.text
    assert GEMINI_PROVIDER not in response.text
    assert PERSIAN_TEXT not in response.text


def test_logs_name_the_providers_and_codes_but_never_secrets_or_user_text(
    caplog: pytest.LogCaptureFixture,
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_keys: None,
) -> None:
    provider_transport.respond_for(GROQ_URL_MARKER, lambda _: groq_error(429))
    provider_transport.respond_for(GEMINI_URL_MARKER, lambda _: gemini_error(504))

    with caplog.at_level(logging.WARNING):
        provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    logged = "\n".join(record.getMessage() for record in caplog.records)
    # Sanitized categories only: the two provider names and their public codes.
    assert GROQ_PROVIDER in logged
    assert GEMINI_PROVIDER in logged
    assert "code=PROVIDER_RATE_LIMIT" in logged
    assert "code=PROVIDER_TIMEOUT" in logged
    # No key material and no user text anywhere in the logs.
    assert GROQ_TEST_KEY not in logged
    assert GEMINI_TEST_KEY not in logged
    assert PERSIAN_TEXT not in logged
    # Nor the raw provider response body.
    assert "provider detail" not in logged
    assert "Bearer" not in logged


# --- Invalid input never reaches a provider ---------------------------------


INVALID_PAYLOADS = [
    pytest.param({"text": ""}, id="empty-text"),
    pytest.param({"text": "   "}, id="whitespace-only"),
    pytest.param({"text": "\n\t \r\n"}, id="whitespace-lines"),
    pytest.param({}, id="missing-text"),
    pytest.param({"text": None}, id="null-text"),
    pytest.param({"text": "ا" * 1001}, id="text-too-long"),
]


@pytest.mark.parametrize("payload", INVALID_PAYLOADS)
def test_invalid_input_is_rejected_without_calling_a_provider(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_keys: None,
    payload: dict[str, object],
) -> None:
    # Both providers are ready to answer, so any request would be recorded.
    provider_transport.respond(lambda _: httpx.Response(500))

    response = provider_client.post(CORRECT_URL, json=payload)

    assert response.status_code == 400
    assert response.json()["error"] in (INVALID_INPUT, TEXT_TOO_LONG)
    assert provider_transport.requests == []


@pytest.mark.parametrize(
    ("content", "content_type"),
    [
        ("{not valid json", "application/json"),
        ("متن ساده بدون JSON", None),
    ],
)
def test_malformed_requests_are_rejected_without_calling_a_provider(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_keys: None,
    content: str,
    content_type: str | None,
) -> None:
    provider_transport.respond(lambda _: httpx.Response(500))
    headers = {"Content-Type": content_type} if content_type else {}

    response = provider_client.post(CORRECT_URL, content=content, headers=headers)

    assert response.status_code == 400
    assert response.json()["error"] == INVALID_INPUT
    assert provider_transport.requests == []


def test_a_failing_provider_is_not_called_for_invalid_input(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_keys: None,
) -> None:
    # Even with Groq failing, validation errors must not produce a provider call
    # or a fallback attempt.
    provider_transport.respond_for(GROQ_URL_MARKER, lambda _: groq_error(500))
    provider_transport.respond_for(GEMINI_URL_MARKER, lambda _: gemini_response(GEMINI_TEXT))

    response = provider_client.post(CORRECT_URL, json={"text": "   "})

    assert response.status_code == 400
    assert response.json()["error"] == INVALID_INPUT
    assert provider_transport.requests == []


def test_empty_output_from_the_fallback_is_not_a_false_success(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_keys: None,
) -> None:
    # Groq answers with a blank correction and Gemini with no candidates at all:
    # neither may be returned as a successful correction.
    provider_transport.respond_for(
        GROQ_URL_MARKER, lambda _: httpx.Response(200, json={"choices": [{"message": {"content": "   "}}]})
    )
    provider_transport.respond_for(GEMINI_URL_MARKER, lambda _: httpx.Response(200, json={"candidates": []}))

    response = provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert response.status_code == 503
    body = response.json()
    assert body["error"] == PROVIDER_UNAVAILABLE
    assert "corrected_text" not in body
    assert provider_transport.requests_for(GEMINI_URL_MARKER)
