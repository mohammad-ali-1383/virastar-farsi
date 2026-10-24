"""Tests for the Groq provider adapter (`app/services/groq.py`).

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
from app.services.groq import GROQ_CHAT_COMPLETIONS_URL, GroqProvider
from app.services.prompt import PERSIAN_PROOFREADING_SYSTEM_PROMPT

MODEL = "openai/gpt-oss-20b"
API_KEY = "unit-test-key-not-real"

Handler = Callable[[httpx.Request], httpx.Response]


def _success_body(text: str) -> dict[str, object]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
    }


def _make_provider(handler: Handler, *, api_key: str = API_KEY) -> GroqProvider:
    """Provider wired to a mock transport (no network, no credentials)."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GroqProvider(
        api_key=api_key,
        model=MODEL,
        timeout_seconds=5.0,
        client=client,
    )


def _generate(provider: GroqProvider, user_text: str = "متن آزمایشی") -> str:
    return asyncio.run(
        provider.generate(system_instruction=PERSIAN_PROOFREADING_SYSTEM_PROMPT, user_text=user_text)
    )


# --- Success ----------------------------------------------------------------


def test_successful_response_returns_corrected_text() -> None:
    provider = _make_provider(lambda _: httpx.Response(200, json=_success_body("متن اصلاح‌شده")))

    assert _generate(provider) == "متن اصلاح‌شده"


def test_endpoint_uses_the_documented_openai_compatible_chat_completions_url() -> None:
    provider = _make_provider(lambda _: httpx.Response(200, json=_success_body("ok")))

    assert provider.endpoint == "https://api.groq.com/openai/v1/chat/completions"
    assert provider.endpoint == GROQ_CHAT_COMPLETIONS_URL


def test_surrounding_whitespace_is_trimmed_from_provider_output() -> None:
    provider = _make_provider(
        lambda _: httpx.Response(200, json=_success_body("  متن اصلاح‌شده  \n"))
    )

    assert _generate(provider) == "متن اصلاح‌شده"


# --- Request construction and prompt usage ----------------------------------


def test_prompt_and_user_text_are_sent_as_separate_messages() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_success_body("ok"))

    _generate(_make_provider(handler), user_text="متن کاربر برای اصلاح")

    body = json.loads(captured[0].content)

    # The prompt is the system message; the user's text is a separate message.
    assert body["model"] == MODEL
    assert body["messages"] == [
        {"role": "system", "content": PERSIAN_PROOFREADING_SYSTEM_PROMPT},
        {"role": "user", "content": "متن کاربر برای اصلاح"},
    ]

    # The user's text must appear only in its own message, never inside the prompt.
    assert "متن کاربر برای اصلاح" not in PERSIAN_PROOFREADING_SYSTEM_PROMPT


def test_system_prompt_matches_the_correction_prompt_spec() -> None:
    # Guardrail: the role line and the closing output rule of the instruction
    # committed in `correction-prompt.md`. The full text, its rule coverage, and
    # its synchronization with the document are pinned by
    # `tests/test_correction_prompt.py`.
    assert PERSIAN_PROOFREADING_SYSTEM_PROMPT.startswith(
        "You are a professional Persian-language proofreader."
    )
    assert "Return only the corrected Persian text" in PERSIAN_PROOFREADING_SYSTEM_PROMPT


def test_api_key_is_sent_in_the_authorization_header_and_never_in_the_url() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_success_body("ok"))

    _generate(_make_provider(handler))

    request = captured[0]
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert request.method == "POST"
    assert API_KEY not in str(request.url)


# --- Provider failure mapping (`api-contract.md` -> Provider Error Mapping) ---


@pytest.mark.parametrize(
    ("status_code", "reason", "expected_error"),
    [
        (401, "invalid_api_key", ProviderAuthError),
        (403, "permission_denied", ProviderAuthError),
        (429, "rate_limit_exceeded", ProviderRateLimitError),
        (500, "server_error", ProviderUnavailableError),
        (502, "bad_gateway", ProviderUnavailableError),
        (503, "service_unavailable", ProviderUnavailableError),
        (504, "gateway_timeout", ProviderTimeoutError),
        # Model/resource missing: treated as the provider being unavailable.
        (404, "model_not_found", ProviderUnavailableError),
        # A malformed request built by us is an internal fault, not user input.
        (400, "invalid_request_error", InternalError),
    ],
)
def test_provider_http_errors_map_to_documented_codes(
    status_code: int, reason: str, expected_error: type[CorrectionError]
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={"error": {"message": "provider detail", "type": reason, "code": reason}},
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
        return httpx.Response(status_code, json={"error": {"type": "X"}})

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
                    "message": f"quota for key {secret_like} exceeded",
                    "type": "rate_limit_exceeded",
                    "code": "rate_limit_exceeded",
                }
            },
        )

    with pytest.raises(ProviderRateLimitError) as excinfo:
        _generate(_make_provider(handler))

    # Only the machine token is kept for logs; the message is discarded.
    assert excinfo.value.detail is not None
    assert "rate_limit_exceeded" in excinfo.value.detail
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
        pytest.param({"choices": []}, id="no-choices"),
        pytest.param({"choices": [{}]}, id="choice-without-message"),
        pytest.param({"choices": [{"message": {}}]}, id="message-without-content"),
        pytest.param({"choices": [{"message": {"content": ""}}]}, id="empty-content"),
        pytest.param({"choices": [{"message": {"content": "   \n "}}]}, id="blank-content"),
        pytest.param({"choices": [{"message": {"content": None}}]}, id="null-content"),
        pytest.param({"choices": [{"message": {"content": [{"type": "text"}]}}]}, id="non-string-content"),
        pytest.param("not an object", id="non-object-body"),
        pytest.param({"choices": "nope"}, id="choices-not-a-list"),
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
