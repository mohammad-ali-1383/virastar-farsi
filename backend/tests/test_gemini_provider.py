"""Tests for the Gemini provider adapter (`app/services/gemini.py`).

Every test uses ``httpx.MockTransport`` so no real API key is required and no
network request is made (`testing.md`, `coding-rules.md` -> Testing).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from app.errors import (
    INTERNAL_ERROR,
    PROVIDER_AUTH_ERROR,
    PROVIDER_RATE_LIMIT,
    PROVIDER_TIMEOUT,
    PROVIDER_UNAVAILABLE,
    CorrectionError,
    InternalError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.services.gemini import GeminiProvider
from app.services.prompt import PERSIAN_PROOFREADING_SYSTEM_PROMPT

MODEL = "gemini-3.5-flash-lite"
API_KEY = "unit-test-key-not-real"

Handler = Callable[[httpx.Request], httpx.Response]


def _success_body(text: str) -> dict[str, object]:
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": text}]},
                "finishReason": "STOP",
                "index": 0,
            }
        ],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 8},
    }


def _make_provider(handler: Handler, *, api_key: str = API_KEY) -> GeminiProvider:
    """Provider wired to a mock transport (no network, no credentials)."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GeminiProvider(
        api_key=api_key,
        model=MODEL,
        timeout_seconds=5.0,
        client=client,
    )


def _generate(provider: GeminiProvider, user_text: str = "متن آزمایشی") -> str:
    return asyncio.run(
        provider.generate(system_instruction=PERSIAN_PROOFREADING_SYSTEM_PROMPT, user_text=user_text)
    )


# --- Success ----------------------------------------------------------------


def test_successful_response_returns_corrected_text() -> None:
    provider = _make_provider(lambda _: httpx.Response(200, json=_success_body("متن اصلاح‌شده")))

    assert _generate(provider) == "متن اصلاح‌شده"


def test_endpoint_uses_configured_model_and_v1beta() -> None:
    provider = _make_provider(lambda _: httpx.Response(200, json=_success_body("ok")))

    assert provider.endpoint == (
        "https://generativelanguage.googleapis.com/v1beta"
        "/models/gemini-3.5-flash-lite:generateContent"
    )


def test_multiple_parts_are_joined_and_trimmed() -> None:
    body = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": "  بخش اول "}, {"text": "بخش دوم  "}],
                }
            }
        ]
    }
    provider = _make_provider(lambda _: httpx.Response(200, json=body))

    assert _generate(provider) == "بخش اول بخش دوم"


def test_surrounding_whitespace_is_trimmed_from_provider_output() -> None:
    provider = _make_provider(
        lambda _: httpx.Response(200, json=_success_body("  متن اصلاح‌شده  \n"))
    )

    assert _generate(provider) == "متن اصلاح‌شده"


# --- Request construction and prompt usage ----------------------------------


def test_prompt_and_user_text_travel_as_separate_fields() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_success_body("ok"))

    _generate(_make_provider(handler), user_text="متن کاربر برای اصلاح")

    body = json.loads(captured[0].content)

    # The prompt is the system instruction; the user's text is separate content.
    assert body["systemInstruction"] == {
        "parts": [{"text": PERSIAN_PROOFREADING_SYSTEM_PROMPT}]
    }
    assert body["contents"] == [{"parts": [{"text": "متن کاربر برای اصلاح"}]}]

    # The user's text must appear only in its own entry, never inside the prompt.
    assert "متن کاربر برای اصلاح" not in PERSIAN_PROOFREADING_SYSTEM_PROMPT


def test_api_key_is_sent_in_the_goog_header_and_never_in_the_url() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_success_body("ok"))

    _generate(_make_provider(handler))

    request = captured[0]
    assert request.headers["x-goog-api-key"] == API_KEY
    assert request.method == "POST"
    assert API_KEY not in str(request.url)


def test_model_id_comes_from_the_configured_adapter() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_success_body("ok"))

    other_model = "gemini-3.5-pro"
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GeminiProvider(
        api_key=API_KEY, model=other_model, timeout_seconds=5.0, client=client
    )

    assert provider.endpoint.endswith(f"/models/{other_model}:generateContent")

    _generate(provider)

    assert f"/models/{other_model}:generateContent" in str(captured[0].url)


# --- Provider failure mapping (`api-contract.md` -> Provider Error Mapping) ---


@pytest.mark.parametrize(
    ("status_code", "reason", "expected_error"),
    [
        (400, "INVALID_ARGUMENT", InternalError),
        (401, "UNAUTHENTICATED", ProviderAuthError),
        (403, "PERMISSION_DENIED", ProviderAuthError),
        (404, "NOT_FOUND", ProviderUnavailableError),
        (429, "RESOURCE_EXHAUSTED", ProviderRateLimitError),
        (500, "INTERNAL", ProviderUnavailableError),
        (502, "BAD_GATEWAY", ProviderUnavailableError),
        (503, "UNAVAILABLE", ProviderUnavailableError),
        (504, "DEADLINE_EXCEEDED", ProviderTimeoutError),
    ],
)
def test_provider_http_errors_map_to_documented_codes(
    status_code: int, reason: str, expected_error: type[CorrectionError]
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={"error": {"code": status_code, "message": "provider detail", "status": reason}},
        )

    with pytest.raises(expected_error):
        _generate(_make_provider(handler))


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (401, PROVIDER_AUTH_ERROR),
        (403, PROVIDER_AUTH_ERROR),
        (429, PROVIDER_RATE_LIMIT),
        (500, PROVIDER_UNAVAILABLE),
        (503, PROVIDER_UNAVAILABLE),
        (504, PROVIDER_TIMEOUT),
        (400, INTERNAL_ERROR),
    ],
)
def test_provider_errors_carry_the_public_code(status_code: int, expected_code: str) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": {"status": "INVALID_ARGUMENT"}})

    with pytest.raises(CorrectionError) as excinfo:
        _generate(_make_provider(handler))

    assert excinfo.value.code == expected_code


def test_provider_error_detail_never_includes_the_raw_message() -> None:
    secret_like = "leaked-api-key-abc123"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "error": {
                    "code": 429,
                    "message": f"quota exceeded for key {secret_like}",
                    "status": "RESOURCE_EXHAUSTED",
                }
            },
        )

    with pytest.raises(ProviderRateLimitError) as excinfo:
        _generate(_make_provider(handler))

    # Only the machine token is kept for logs; the message is discarded.
    assert excinfo.value.detail is not None
    assert "RESOURCE_EXHAUSTED" in excinfo.value.detail
    assert secret_like not in excinfo.value.detail


def test_timeout_exception_maps_to_provider_timeout() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    with pytest.raises(ProviderTimeoutError):
        _generate(_make_provider(handler))


def test_connection_error_maps_to_provider_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ProviderUnavailableError):
        _generate(_make_provider(handler))


def test_missing_api_key_is_an_auth_error_and_makes_no_request() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_success_body("should not happen"))

    with pytest.raises(ProviderAuthError):
        _generate(_make_provider(handler, api_key=""))

    assert calls == []


# --- Invalid or empty model output ------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="empty-object"),
        pytest.param({"candidates": []}, id="no-candidates"),
        pytest.param({"candidates": [{}]}, id="candidate-without-content"),
        pytest.param({"candidates": [{"content": {}}]}, id="content-without-parts"),
        pytest.param(
            {"candidates": [{"content": {"parts": [{"text": ""}]}}]}, id="empty-part"
        ),
        pytest.param(
            {"candidates": [{"content": {"parts": [{"text": "   \n "}]}}]}, id="blank-part"
        ),
        pytest.param(
            {"candidates": [{"content": {"parts": [{"text": None}]}}]}, id="null-part-text"
        ),
        pytest.param({"candidates": [{"content": {"parts": "nope"}}]}, id="parts-not-a-list"),
        pytest.param("not an object", id="non-object-body"),
        pytest.param({"candidates": "nope"}, id="candidates-not-a-list"),
    ],
)
def test_invalid_or_empty_model_output_is_an_internal_error(body: object) -> None:
    provider = _make_provider(lambda _: httpx.Response(200, json=body))

    with pytest.raises(InternalError):
        _generate(provider)


def test_non_json_response_body_is_an_internal_error() -> None:
    provider = _make_provider(
        lambda _: httpx.Response(
            200, content=b"<html>gateway</html>", headers={"content-type": "text/html"}
        )
    )

    with pytest.raises(InternalError):
        _generate(provider)
