"""Tests for the ordered provider-slot fallback chain.

Every test drives the real adapter, the real correction service, and the real
endpoint through ``httpx.MockTransport`` (the ``provider_transport`` fixture
routes each slot's URL independently), with placeholder keys only. No real API
key is read and no network request is made (`testing.md`,
`coding-rules.md` -> Testing).

What is verified is the workflow: which slot is called, in which order, how often,
what it receives, when it is skipped (cooldown, deadline), and which public error
a failure produces. Real provider connectivity and live correction quality are
*not* verified by these mocked tests.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app.errors import (
    INVALID_INPUT,
    PROVIDER_UNAVAILABLE,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from app.services.correction import (
    AUTH_COOLDOWN_SECONDS,
    RATE_LIMIT_COOLDOWN_SECONDS,
    LLMCorrectionService,
    ProviderSlot,
    SlotCooldown,
)
from app.services.prompt import PERSIAN_PROOFREADING_SYSTEM_PROMPT
from tests.conftest import (
    SLOT_1_KEY,
    SLOT_1_MODEL,
    SLOT_1_URL_MARKER,
    SLOT_2_KEY,
    SLOT_2_MODEL,
    SLOT_2_URL_MARKER,
    Handler,
    ProviderTransport,
    chat_completion_error,
    chat_completion_response,
    define_slot,
)

CORRECT_URL = "/api/v1/correct"
PERSIAN_TEXT = "سلام این یک متن ازمایشی است"
FIRST_TEXT = "اصلاح‌شده با اسلات یک"
SECOND_TEXT = "اصلاح‌شده با اسلات دو"
THIRD_TEXT = "اصلاح‌شده با اسلات سه"

SLOT_3_BASE_URL = "https://slot-three.test/v1"
SLOT_3_URL_MARKER = "slot-three.test"

# The documented generic message for PROVIDER_UNAVAILABLE (`errors.py`).
SERVICE_ERROR_MESSAGE = "سرویس اصلاح متن در حال حاضر در دسترس نیست."


def _timeout(_: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("read timed out")


def _connection_error(_: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused")


def _tls_error(_: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("TLS handshake failed")


def _empty_content(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": "   "}}]})


def _no_choices(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"choices": []})


def _headers_of(request: httpx.Request) -> str:
    """All request headers as one lower-cased string (httpx folds the names)."""
    return " ".join(f"{name}:{value}".lower() for name, value in request.headers.items())


# --- Slot 1 failure, slot 2 success -----------------------------------------


FIRST_SLOT_FAILURES = [
    pytest.param(lambda _: chat_completion_error(401), id="auth-401"),
    pytest.param(lambda _: chat_completion_error(402), id="payment-402"),
    pytest.param(lambda _: chat_completion_error(403), id="forbidden-403"),
    pytest.param(lambda _: chat_completion_error(404), id="missing-model-404"),
    pytest.param(lambda _: chat_completion_error(429), id="rate-limit-429"),
    pytest.param(lambda _: chat_completion_error(500), id="server-500"),
    pytest.param(lambda _: chat_completion_error(503), id="server-503"),
    pytest.param(_timeout, id="timeout"),
    pytest.param(_connection_error, id="connection-error"),
    pytest.param(_tls_error, id="tls-error"),
    pytest.param(_empty_content, id="empty-content"),
    pytest.param(_no_choices, id="no-choices"),
    pytest.param(
        lambda _: httpx.Response(
            200, content=b"<html>gateway</html>", headers={"content-type": "text/html"}
        ),
        id="unparseable-response",
    ),
]


def test_the_first_slot_answering_ends_the_request(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_slots: None,
) -> None:
    provider_transport.respond_for(
        SLOT_1_URL_MARKER, lambda _: chat_completion_response(FIRST_TEXT)
    )
    provider_transport.respond_for(SLOT_2_URL_MARKER, lambda _: httpx.Response(500))

    response = provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert response.status_code == 200
    assert response.json() == {"corrected_text": FIRST_TEXT}
    # Exactly one request, to slot 1 only.
    assert len(provider_transport.requests) == 1
    assert provider_transport.requests_for(SLOT_2_URL_MARKER) == []


@pytest.mark.parametrize("first_slot_failure", FIRST_SLOT_FAILURES)
def test_any_slot_failure_falls_back_to_the_next_slot(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_slots: None,
    first_slot_failure: Handler,
) -> None:
    provider_transport.respond_for(SLOT_1_URL_MARKER, first_slot_failure)
    provider_transport.respond_for(
        SLOT_2_URL_MARKER, lambda _: chat_completion_response(SECOND_TEXT)
    )

    response = provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert response.status_code == 200
    assert response.json() == {"corrected_text": SECOND_TEXT}
    # One attempt per slot, in index order, and no retry of the failed slot.
    assert len(provider_transport.requests_for(SLOT_1_URL_MARKER)) == 1
    assert len(provider_transport.requests_for(SLOT_2_URL_MARKER)) == 1
    assert len(provider_transport.requests) == 2
    assert SLOT_1_URL_MARKER in provider_transport.urls[0]
    assert SLOT_2_URL_MARKER in provider_transport.urls[1]


def test_fallback_resends_the_same_prompt_and_user_text(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_slots: None,
) -> None:
    provider_transport.respond_for(SLOT_1_URL_MARKER, lambda _: chat_completion_error(429))
    provider_transport.respond_for(
        SLOT_2_URL_MARKER, lambda _: chat_completion_response(SECOND_TEXT)
    )

    provider_client.post(CORRECT_URL, json={"text": "متن کاربر برای اصلاح"})

    bodies = [
        json.loads(request.content)
        for request in provider_transport.requests
    ]

    # Same request body except the model, which belongs to each slot.
    assert [body["messages"] for body in bodies] == [
        [
            {"role": "system", "content": PERSIAN_PROOFREADING_SYSTEM_PROMPT},
            {"role": "user", "content": "متن کاربر برای اصلاح"},
        ]
    ] * 2
    assert [body["model"] for body in bodies] == [SLOT_1_MODEL, SLOT_2_MODEL]
    assert all(body["stream"] is False for body in bodies)
    assert all(body["temperature"] == 0.2 for body in bodies)


def test_each_request_carries_only_its_own_slots_key(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_slots: None,
) -> None:
    provider_transport.respond_for(SLOT_1_URL_MARKER, lambda _: chat_completion_error(503))
    provider_transport.respond_for(
        SLOT_2_URL_MARKER, lambda _: chat_completion_response(SECOND_TEXT)
    )

    provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    first_request = provider_transport.requests_for(SLOT_1_URL_MARKER)[0]
    second_request = provider_transport.requests_for(SLOT_2_URL_MARKER)[0]

    assert f"authorization:bearer {SLOT_1_KEY}" in _headers_of(first_request)
    assert f"authorization:bearer {SLOT_2_KEY}" in _headers_of(second_request)

    for request, other_key in (
        (first_request, SLOT_2_KEY),
        (second_request, SLOT_1_KEY),
    ):
        assert other_key not in _headers_of(request)
        assert other_key not in str(request.url)
        assert other_key not in request.content.decode("utf-8")


def test_a_third_slot_is_tried_when_the_first_two_fail(
    monkeypatch: pytest.MonkeyPatch,
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_slots: None,
) -> None:
    define_slot(monkeypatch, 3, base_url=SLOT_3_BASE_URL)
    provider_transport.respond_for(SLOT_1_URL_MARKER, lambda _: chat_completion_error(401))
    provider_transport.respond_for(SLOT_2_URL_MARKER, _timeout)
    provider_transport.respond_for(
        SLOT_3_URL_MARKER, lambda _: chat_completion_response(THIRD_TEXT)
    )

    response = provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert response.status_code == 200
    assert response.json() == {"corrected_text": THIRD_TEXT}
    assert [url.split("/")[2] for url in provider_transport.urls] == [
        "slot-one.test",
        "slot-two.test",
        "slot-three.test",
    ]


def test_slots_are_tried_in_numeric_index_order(
    monkeypatch: pytest.MonkeyPatch,
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_slots: None,
) -> None:
    # Numeric order, not lexical: 1, 2, 10 (a string sort would put 10 second).
    define_slot(monkeypatch, 10, base_url="https://slot-ten.test/v1")
    provider_transport.respond(lambda _: httpx.Response(503))

    provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert [url.split("/")[2] for url in provider_transport.urls] == [
        "slot-one.test",
        "slot-two.test",
        "slot-ten.test",
    ]


# --- Every slot fails -------------------------------------------------------


def test_all_slots_failing_returns_one_safe_error(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_slots: None,
) -> None:
    provider_transport.respond(lambda _: chat_completion_error(429))

    response = provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert response.status_code == 503
    body = response.json()
    assert body == {"error": PROVIDER_UNAVAILABLE, "message": SERVICE_ERROR_MESSAGE}
    assert "corrected_text" not in body
    # One attempt per configured slot.
    assert len(provider_transport.requests_for(SLOT_1_URL_MARKER)) == 1
    assert len(provider_transport.requests_for(SLOT_2_URL_MARKER)) == 1


def test_the_safe_error_is_the_same_for_every_failure_combination(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_slots: None,
) -> None:
    bodies = []

    for first_failure, second_failure in (
        (lambda _: chat_completion_error(429), lambda _: chat_completion_error(401)),
        (lambda _: chat_completion_error(503), _timeout),
        (_connection_error, _empty_content),
    ):
        provider_transport.respond_for(SLOT_1_URL_MARKER, first_failure)
        provider_transport.respond_for(SLOT_2_URL_MARKER, second_failure)
        bodies.append(provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT}).json())

    # The client cannot tell which slot failed or why: one stable body.
    assert bodies == [{"error": PROVIDER_UNAVAILABLE, "message": SERVICE_ERROR_MESSAGE}] * 3


def test_no_configured_slot_returns_the_service_error_without_a_request(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.config as app_config

    # No `.env` file, no slot variables: nothing is usable.
    monkeypatch.setattr(app_config, "_ENV_FILES", ())
    for index in (1, 2):
        for variable in ("API_KEY", "BASE_URL", "MODEL"):
            monkeypatch.delenv(f"{variable}_{index}", raising=False)

    response = provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert response.status_code == 503
    assert response.json() == {
        "error": PROVIDER_UNAVAILABLE,
        "message": SERVICE_ERROR_MESSAGE,
    }
    assert provider_transport.requests == []


def test_an_incomplete_slot_is_skipped_and_never_called(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_slots: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Slot 2 loses its model, so it is no longer configured.
    monkeypatch.delenv("MODEL_2")
    provider_transport.respond_for(SLOT_1_URL_MARKER, lambda _: chat_completion_error(503))
    provider_transport.respond_for(
        SLOT_2_URL_MARKER, lambda _: chat_completion_response(SECOND_TEXT)
    )

    response = provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert response.status_code == 503
    assert provider_transport.requests_for(SLOT_2_URL_MARKER) == []


def test_invalid_input_never_reaches_a_provider(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_slots: None,
) -> None:
    provider_transport.respond(lambda _: chat_completion_response(SECOND_TEXT))

    response = provider_client.post(CORRECT_URL, json={"text": "   "})

    assert response.status_code == 400
    assert response.json()["error"] == INVALID_INPUT
    assert provider_transport.requests == []


# --- Logging safety ---------------------------------------------------------


def test_a_success_logs_the_slot_that_served_it(
    caplog: pytest.LogCaptureFixture,
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_slots: None,
) -> None:
    provider_transport.respond_for(
        SLOT_1_URL_MARKER, lambda _: chat_completion_response(FIRST_TEXT)
    )

    with caplog.at_level(logging.INFO):
        provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "Correction served by slot=slot-1" in logged
    assert SLOT_1_KEY not in logged
    assert PERSIAN_TEXT not in logged


def test_failures_log_slot_names_and_codes_but_never_keys_or_text(
    caplog: pytest.LogCaptureFixture,
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_slots: None,
) -> None:
    # Provider error bodies that echo request material back, as real providers do.
    provider_transport.respond_for(
        SLOT_1_URL_MARKER,
        lambda _: chat_completion_error(503, message=f"overloaded for key {SLOT_1_KEY}"),
    )
    provider_transport.respond_for(
        SLOT_2_URL_MARKER,
        lambda _: chat_completion_error(429, message=f"quota for key {SLOT_2_KEY}"),
    )

    with caplog.at_level(logging.WARNING):
        response = provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    logged = "\n".join(record.getMessage() for record in caplog.records)

    assert response.status_code == 503
    assert "slot-1" in logged
    assert "slot-2" in logged
    assert "code=PROVIDER_UNAVAILABLE" in logged
    assert "code=PROVIDER_RATE_LIMIT" in logged

    # No key material, no raw provider detail, no headers, no user text.
    assert SLOT_1_KEY not in logged
    assert SLOT_2_KEY not in logged
    assert "overloaded for key" not in logged
    assert "quota for key" not in logged
    assert "Bearer" not in logged
    assert PERSIAN_TEXT not in logged
    assert PERSIAN_TEXT not in response.text


# --- Cooldown ---------------------------------------------------------------


class _Clock:
    """Deterministic clock for the cooldown and the overall deadline."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Provider:
    """Provider double: optionally consumes time and raises a given error."""

    def __init__(
        self,
        clock: _Clock,
        *,
        seconds: float = 0.0,
        error: Exception | None = None,
        result: str = "corrected",
    ) -> None:
        self._clock = clock
        self._seconds = seconds
        self._error = error
        self._result = result
        self.timeouts: list[float | None] = []

    async def generate(
        self,
        *,
        system_instruction: str,
        user_text: str,
        timeout_seconds: float | None = None,
    ) -> str:
        self.timeouts.append(timeout_seconds)
        self._clock.advance(self._seconds)
        if self._error is not None:
            raise self._error
        return self._result

    @property
    def calls(self) -> int:
        return len(self.timeouts)


def _chain(
    clock: _Clock,
    *providers: _Provider,
    deadline_seconds: float = 60.0,
) -> LLMCorrectionService:
    cooldowns = SlotCooldown(clock=clock)
    return LLMCorrectionService(
        [
            ProviderSlot(f"slot-{index}", f"model-{index}", provider)
            for index, provider in enumerate(providers, start=1)
        ],
        cooldowns=cooldowns,
        clock=clock,
        deadline_seconds=deadline_seconds,
    )


@pytest.mark.parametrize("status_code", [401, 402, 403])
def test_auth_and_payment_failures_cool_a_slot_down_for_300_seconds(
    caplog: pytest.LogCaptureFixture, status_code: int
) -> None:
    clock = _Clock()
    first = _Provider(clock, error=ProviderAuthError(f"http_status={status_code}"))
    second = _Provider(clock, result="ok")
    service = _chain(clock, first, second)

    with caplog.at_level(logging.WARNING):
        asyncio.run(service.correct(PERSIAN_TEXT))

    assert first.calls == 1
    assert second.calls == 1
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "Provider slot slot-1 is in cooldown (code=PROVIDER_AUTH_ERROR)" in logged
    assert "http_status" not in logged

    # Still cooling down just before the deadline...
    clock.advance(AUTH_COOLDOWN_SECONDS - 1)
    asyncio.run(service.correct(PERSIAN_TEXT))
    assert first.calls == 1
    assert second.calls == 2

    # ...and available again afterwards.
    clock.advance(1)
    asyncio.run(service.correct(PERSIAN_TEXT))
    assert first.calls == 2


def test_a_rate_limit_cools_a_slot_down_for_30_seconds() -> None:
    clock = _Clock()
    first = _Provider(clock, error=ProviderRateLimitError("http_status=429"))
    second = _Provider(clock, result="ok")
    service = _chain(clock, first, second)

    asyncio.run(service.correct(PERSIAN_TEXT))
    assert first.calls == 1

    clock.advance(RATE_LIMIT_COOLDOWN_SECONDS - 1)
    asyncio.run(service.correct(PERSIAN_TEXT))
    assert first.calls == 1

    clock.advance(1)
    asyncio.run(service.correct(PERSIAN_TEXT))
    assert first.calls == 2


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(ProviderUnavailableError("http_status=503"), id="server-error"),
        pytest.param(ProviderUnavailableError("timed out"), id="timeout"),
    ],
)
def test_a_transient_failure_does_not_cool_a_slot_down(error: Exception) -> None:
    clock = _Clock()
    first = _Provider(clock, error=error)
    second = _Provider(clock, result="ok")
    service = _chain(clock, first, second)

    asyncio.run(service.correct(PERSIAN_TEXT))
    asyncio.run(service.correct(PERSIAN_TEXT))

    # Tried again on the next request: there is nothing to wait for.
    assert first.calls == 2


def test_a_success_clears_an_existing_cooldown() -> None:
    clock = _Clock()
    cooldowns = SlotCooldown(clock=clock)
    cooldowns.enter("slot-1", ProviderAuthError("http_status=401"))
    recovering = _Provider(clock, result="ok")
    service = LLMCorrectionService(
        [ProviderSlot("slot-1", "model-1", recovering)],
        cooldowns=cooldowns,
        clock=clock,
    )

    # While cooling down the slot is skipped, so no request is made at all.
    with pytest.raises(ProviderUnavailableError):
        asyncio.run(service.correct(PERSIAN_TEXT))
    assert recovering.calls == 0

    # Once the cooldown expires it answers, and the success clears the entry.
    clock.advance(AUTH_COOLDOWN_SECONDS)
    result = asyncio.run(service.correct(PERSIAN_TEXT))

    assert result.text == "ok"
    assert cooldowns.active("slot-1") is False


def test_an_endpoint_request_skips_a_cooling_slot(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_slots: None,
) -> None:
    provider_transport.respond_for(SLOT_1_URL_MARKER, lambda _: chat_completion_error(429))
    provider_transport.respond_for(
        SLOT_2_URL_MARKER, lambda _: chat_completion_response(SECOND_TEXT)
    )

    first_response = provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})
    second_response = provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    # The exhausted slot is not asked again: only slot 2 is called the second time.
    assert len(provider_transport.requests_for(SLOT_1_URL_MARKER)) == 1
    assert len(provider_transport.requests_for(SLOT_2_URL_MARKER)) == 2


def test_every_slot_cooling_down_returns_the_service_error(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_slots: None,
) -> None:
    provider_transport.respond(lambda _: chat_completion_error(401))

    provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})
    requests_after_first = len(provider_transport.requests)
    second_response = provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert second_response.status_code == 503
    assert second_response.json()["error"] == PROVIDER_UNAVAILABLE
    # Nothing was attempted: every slot is cooling down.
    assert len(provider_transport.requests) == requests_after_first


# --- Overall deadline -------------------------------------------------------


def test_the_chain_stops_before_a_slot_that_cannot_finish() -> None:
    clock = _Clock()
    first = _Provider(clock, seconds=40.0, error=ProviderUnavailableError("http_status=503"))
    second = _Provider(clock, seconds=17.0, error=ProviderUnavailableError("http_status=503"))
    third = _Provider(clock, result="ok")
    service = _chain(clock, first, second, third)

    with pytest.raises(ProviderUnavailableError):
        asyncio.run(service.correct(PERSIAN_TEXT))

    # 60 - 40 = 20 seconds were left for slot 2, then only 3 were left for slot 3.
    assert first.timeouts == [60.0]
    assert second.timeouts == [20.0]
    assert third.calls == 0


def test_each_slot_timeout_never_exceeds_the_configured_deadline() -> None:
    clock = _Clock()
    first = _Provider(clock, result="ok")
    service = _chain(clock, first, deadline_seconds=12.0)

    asyncio.run(service.correct(PERSIAN_TEXT))

    assert first.timeouts == [12.0]


def test_a_long_chain_is_capped_by_the_overall_deadline() -> None:
    # Three slow slots: 60s, then 35s, then the 10 seconds that are left.
    clock = _Clock()
    providers = [
        _Provider(clock, seconds=25.0, error=ProviderUnavailableError("http_status=503"))
        for _ in range(3)
    ]
    service = _chain(clock, *providers)

    with pytest.raises(ProviderUnavailableError):
        asyncio.run(service.correct(PERSIAN_TEXT))

    assert [provider.calls for provider in providers] == [1, 1, 1]
    assert [provider.timeouts[0] for provider in providers] == [60.0, 35.0, 10.0]
