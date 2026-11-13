"""Tests for the generic OpenAI-compatible adapter (`app/services/openai_compatible.py`).

Every configured provider slot is served by this adapter, so its contract is the
OpenAI-compatible chat completions protocol plus the sanitized error mapping.
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
from app.services.openai_compatible import OpenAICompatibleProvider
from app.services.prompt import PERSIAN_PROOFREADING_SYSTEM_PROMPT

MODEL = "some-vendor/model"
API_KEY = "unit-test-key-not-real"
BASE_URL = "https://api.example.test/v1"
SLOT_NAME = "slot-1"

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


def _make_provider(
    handler: Handler,
    *,
    api_key: str = API_KEY,
    base_url: str = BASE_URL,
    timeout_seconds: float = 20.0,
    extra_body: dict[str, object] | None = None,
) -> OpenAICompatibleProvider:
    """Provider wired to a mock transport (no network, no credentials)."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatibleProvider(
        api_key=api_key,
        base_url=base_url,
        model=MODEL,
        name=SLOT_NAME,
        timeout_seconds=timeout_seconds,
        extra_body=extra_body,
        client=client,
    )


def _generate(
    provider: OpenAICompatibleProvider,
    user_text: str = "متن آزمایشی",
    *,
    timeout_seconds: float | None = None,
) -> str:
    return asyncio.run(
        provider.generate(
            system_instruction=PERSIAN_PROOFREADING_SYSTEM_PROMPT,
            user_text=user_text,
            timeout_seconds=timeout_seconds,
        )
    )


# --- Success ----------------------------------------------------------------


def test_successful_response_returns_corrected_text() -> None:
    provider = _make_provider(lambda _: httpx.Response(200, json=_success_body("متن اصلاح‌شده")))

    assert _generate(provider) == "متن اصلاح‌شده"


def test_endpoint_appends_chat_completions_to_the_configured_base_url() -> None:
    provider = _make_provider(lambda _: httpx.Response(200, json=_success_body("ok")))

    assert provider.endpoint == "https://api.example.test/v1/chat/completions"


def test_a_trailing_slash_in_the_base_url_is_stripped() -> None:
    provider = _make_provider(
        lambda _: httpx.Response(200, json=_success_body("ok")),
        base_url="https://api.example.test/v1/",
    )

    assert provider.endpoint == "https://api.example.test/v1/chat/completions"


def test_surrounding_whitespace_is_trimmed_from_provider_output() -> None:
    provider = _make_provider(
        lambda _: httpx.Response(200, json=_success_body("  متن اصلاح‌شده  \n"))
    )

    assert _generate(provider) == "متن اصلاح‌شده"


# --- Request construction ---------------------------------------------------


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
    assert body["temperature"] == 0.2
    assert body["stream"] is False
    # The user's text must appear only in its own message, never in the prompt.
    assert "متن کاربر برای اصلاح" not in PERSIAN_PROOFREADING_SYSTEM_PROMPT


def test_the_request_is_a_post_to_the_chat_completions_endpoint() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_success_body("ok"))

    _generate(_make_provider(handler))

    assert captured[0].method == "POST"
    assert str(captured[0].url) == "https://api.example.test/v1/chat/completions"
    assert str(captured[0].url).endswith("/chat/completions")


def test_api_key_is_sent_in_the_authorization_header_and_never_in_the_url() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_success_body("ok"))

    _generate(_make_provider(handler))

    request = captured[0]
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert request.headers["Content-Type"] == "application/json"
    assert API_KEY not in str(request.url)
    # Only this slot's own key is ever sent.
    assert list(request.headers.get_list("authorization")) == [f"Bearer {API_KEY}"]


# --- EXTRA_BODY merging -----------------------------------------------------


def test_extra_body_is_merged_into_the_request() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_success_body("ok"))

    _generate(
        _make_provider(
            handler, extra_body={"thinking": {"type": "disabled"}, "top_p": 0.9}
        )
    )

    body = json.loads(captured[0].content)
    assert body["thinking"] == {"type": "disabled"}
    assert body["top_p"] == 0.9
    # The core request is still there.
    assert body["model"] == MODEL
    assert body["stream"] is False


def test_extra_body_can_never_override_model_messages_or_stream() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_success_body("ok"))

    _generate(
        _make_provider(
            handler,
            extra_body={
                "model": "hijacked-model",
                "messages": [{"role": "user", "content": "hijacked"}],
                "stream": True,
                "reasoning_effort": "low",
            },
        ),
        user_text="متن کاربر",
    )

    body = json.loads(captured[0].content)
    assert body["model"] == MODEL
    assert body["messages"] == [
        {"role": "system", "content": PERSIAN_PROOFREADING_SYSTEM_PROMPT},
        {"role": "user", "content": "متن کاربر"},
    ]
    assert body["stream"] is False
    assert body["reasoning_effort"] == "low"


# --- Provider failure mapping (`api-contract.md` -> Provider Error Mapping) ---


@pytest.mark.parametrize(
    ("status_code", "reason", "expected_error"),
    [
        (401, "invalid_api_key", ProviderAuthError),
        (402, "insufficient_quota", ProviderAuthError),
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
        (402, PROVIDER_AUTH_ERROR),
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


def test_tls_error_maps_to_provider_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("TLS handshake failed")

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


# --- Timeout capping --------------------------------------------------------


def test_the_configured_timeout_is_used_by_default() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_success_body("ok"))

    _generate(_make_provider(handler, timeout_seconds=7.0))

    assert captured[0].extensions["timeout"]["read"] == 7.0


def test_a_shorter_remaining_time_caps_the_configured_timeout() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_success_body("ok"))

    _generate(_make_provider(handler, timeout_seconds=20.0), timeout_seconds=6.0)

    assert captured[0].extensions["timeout"]["read"] == 6.0


def test_a_longer_remaining_time_never_increases_the_configured_timeout() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_success_body("ok"))

    _generate(_make_provider(handler, timeout_seconds=5.0), timeout_seconds=40.0)

    assert captured[0].extensions["timeout"]["read"] == 5.0


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
        pytest.param(
            {"choices": [{"message": {"content": [{"type": "text"}]}}]},
            id="non-string-content",
        ),
        pytest.param(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning_content": "invented reasoning text",
                        }
                    }
                ]
            },
            id="reasoning-content-only",
        ),
        pytest.param("not an object", id="non-object-body"),
        pytest.param({"choices": "nope"}, id="choices-not-a-list"),
    ],
)
def test_invalid_or_empty_model_output_is_an_internal_error(body: object) -> None:
    provider = _make_provider(lambda _: httpx.Response(200, json=body))

    with pytest.raises(InternalError):
        _generate(provider)


def test_only_the_first_choice_is_used() -> None:
    body = {
        "choices": [
            {"message": {"content": "اولین"}},
            {"message": {"content": "دومین"}},
        ]
    }

    assert _generate(_make_provider(lambda _: httpx.Response(200, json=body))) == "اولین"


def test_reasoning_content_never_replaces_the_answer() -> None:
    body = {
        "choices": [
            {
                "message": {
                    "content": "  پاسخ  ",
                    "reasoning_content": "invented reasoning text",
                }
            }
        ]
    }

    assert _generate(_make_provider(lambda _: httpx.Response(200, json=body))) == "پاسخ"


def test_non_json_response_body_is_an_internal_error() -> None:
    provider = _make_provider(
        lambda _: httpx.Response(
            200, content=b"<html>gateway</html>", headers={"content-type": "text/html"}
        )
    )

    with pytest.raises(InternalError):
        _generate(provider)


def test_the_user_text_is_never_returned_unexpectedly_on_failure() -> None:
    # A provider failure must raise, never fall back to the submitted text.
    provider = _make_provider(lambda _: httpx.Response(503, json={}))

    with pytest.raises(ProviderUnavailableError):
        _generate(provider, user_text="متن کاربر")
