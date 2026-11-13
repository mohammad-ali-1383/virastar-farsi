"""Tests for the correction service: an ordered chain of provider slots.

The service-level tests use provider doubles; the end-to-end tests drive the
real adapter through ``httpx.MockTransport`` (installed by the
``provider_transport`` fixture) and the same FastAPI dependency the app uses, so
the real HTTP layer, validation, prompt handling, and error handlers are covered
without a real API key or network access.

The fallback failure matrix through ``POST /api/v1/correct`` — 401/402/403/404,
rate limit, server error, timeout, connection error, empty content, and the
all-slots-fail case — lives in ``tests/test_provider_fallback.py``.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.errors import (
    PROVIDER_UNAVAILABLE,
    ProviderAuthError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.services.correction import (
    CorrectionResult,
    LLMCorrectionService,
    LLMProvider,
    ProviderSlot,
    get_correction_service,
)
from app.services.prompt import PERSIAN_PROOFREADING_SYSTEM_PROMPT
from tests.conftest import (
    SLOT_1_KEY,
    SLOT_1_MODEL,
    SLOT_1_URL_MARKER,
    ProviderTransport,
    chat_completion_response,
)

CORRECT_URL = "/api/v1/correct"
PERSIAN_TEXT = "سلام این یک متن ازمایشی است"
CORRECTED_TEXT = "سلام، این یک متن آزمایشی است."

# Stand-in model ID: the service reports the model of the slot it used.
SLOT_1_TEST_MODEL = SLOT_1_MODEL


class _RecordingProvider:
    """Provider double that records the arguments and returns or raises."""

    def __init__(self, result: str = "corrected", error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[tuple[str, str, float | None]] = []

    async def generate(
        self,
        *,
        system_instruction: str,
        user_text: str,
        timeout_seconds: float | None = None,
    ) -> str:
        self.calls.append((system_instruction, user_text, timeout_seconds))
        if self._error is not None:
            raise self._error
        return self._result


def _service(*providers: LLMProvider) -> LLMCorrectionService:
    """The production shape: one slot per provider, in order."""
    return LLMCorrectionService(
        [
            ProviderSlot(
                f"slot-{index}", f"model-{index}", provider
            )
            for index, provider in enumerate(providers, start=1)
        ]
    )


def _correct(service: LLMCorrectionService, text: str = PERSIAN_TEXT) -> CorrectionResult:
    return asyncio.run(service.correct(text))


# --- Service behaviour ------------------------------------------------------


def test_service_sends_the_prompt_and_user_text_separately() -> None:
    first = _RecordingProvider(result="متن اصلاح‌شده")
    second = _RecordingProvider(result="unused")

    result = _correct(_service(first, second))

    assert result.text == "متن اصلاح‌شده"
    assert [(prompt, text) for prompt, text, _ in first.calls] == [
        (PERSIAN_PROOFREADING_SYSTEM_PROMPT, PERSIAN_TEXT)
    ]
    # A successful first slot means the second one is never touched.
    assert second.calls == []


def test_result_names_the_slot_and_model_that_succeeded() -> None:
    result = _correct(_service(_RecordingProvider(result="ok"), _RecordingProvider()))

    # What the evaluation log records: the slot that produced the text and the
    # model ID it sends.
    assert (result.provider, result.model) == ("slot-1", "model-1")


def test_fallback_returns_its_own_result_when_the_first_slot_fails() -> None:
    first = _RecordingProvider(error=ProviderAuthError("no key"))
    second = _RecordingProvider(result="اصلاح‌شده با اسلات دوم")

    result = _correct(_service(first, second))

    assert result.text == "اصلاح‌شده با اسلات دوم"
    # The slot that actually ran is the one reported, with its own model.
    assert (result.provider, result.model) == ("slot-2", "model-2")


def test_fallback_receives_the_same_prompt_and_user_text() -> None:
    first = _RecordingProvider(error=ProviderTimeoutError("timed out"))
    second = _RecordingProvider(result="ok")

    _correct(_service(first, second), text="متن کاربر برای اصلاح")

    # Same request, other slot: a fallback cannot change what is asked.
    assert [(prompt, text) for prompt, text, _ in second.calls] == [
        (PERSIAN_PROOFREADING_SYSTEM_PROMPT, "متن کاربر برای اصلاح")
    ]


def test_each_slot_is_attempted_at_most_once() -> None:
    first = _RecordingProvider(error=ProviderUnavailableError("down"))
    second = _RecordingProvider(error=ProviderUnavailableError("down"))

    with pytest.raises(ProviderUnavailableError):
        _correct(_service(first, second))

    # One attempt per slot: no retry loop, no duplicate request.
    assert len(first.calls) == 1
    assert len(second.calls) == 1


def test_all_slots_failing_raise_one_safe_error() -> None:
    first = _RecordingProvider(error=ProviderAuthError("slot-1 has no API key configured"))
    second = _RecordingProvider(error=ProviderAuthError("slot-2 has no API key configured"))

    with pytest.raises(ProviderUnavailableError) as excinfo:
        _correct(_service(first, second))

    error = excinfo.value
    assert error.code == PROVIDER_UNAVAILABLE
    # The client-facing message is the documented generic one.
    assert "در دسترس نیست" in error.message
    # Only slot names and public codes are recorded for the server log.
    assert error.detail is not None
    assert "slot-1" in error.detail
    assert "slot-2" in error.detail
    assert "API key" not in error.detail


def test_a_programming_error_is_not_masked_by_the_fallback() -> None:
    first = _RecordingProvider(error=RuntimeError("bug in our code"))
    second = _RecordingProvider(result="should not be reached")

    with pytest.raises(RuntimeError):
        _correct(_service(first, second))

    # Only provider-related failures trigger the fallback.
    assert second.calls == []


def test_no_configured_slot_is_the_documented_service_error() -> None:
    with pytest.raises(ProviderUnavailableError) as excinfo:
        _correct(LLMCorrectionService([]))

    assert excinfo.value.code == PROVIDER_UNAVAILABLE


def test_every_slot_is_given_the_overall_deadline_while_it_fits() -> None:
    first = _RecordingProvider(result="ok")

    _correct(_service(first))

    # The chain passes the time it has left (the 60 second budget here).
    assert first.calls[0][2] is not None
    assert 0 < first.calls[0][2] <= 60


# --- Dependency wiring ------------------------------------------------------


def test_dependency_builds_an_llm_backed_service() -> None:
    assert isinstance(get_correction_service(), LLMCorrectionService)


# --- End-to-end through POST /api/v1/correct --------------------------------


def test_endpoint_returns_the_correction(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_slots: None,
) -> None:
    # The default handler answers every non-routed request with a 500, so only
    # the first slot's endpoint can produce a successful response.
    provider_transport.respond(lambda _: httpx.Response(500))
    provider_transport.respond_for(
        SLOT_1_URL_MARKER, lambda _: chat_completion_response(CORRECTED_TEXT)
    )

    response = provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert response.status_code == 200
    assert response.json() == {"corrected_text": CORRECTED_TEXT}


def test_endpoint_sends_the_correction_prompt_and_only_that_slots_key(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_slots: None,
) -> None:
    provider_transport.respond(lambda _: chat_completion_response(CORRECTED_TEXT))

    provider_client.post(CORRECT_URL, json={"text": "متن"})

    request = provider_transport.requests[0]
    body = json.loads(request.content)
    assert body["model"] == SLOT_1_TEST_MODEL
    assert body["messages"] == [
        {"role": "system", "content": PERSIAN_PROOFREADING_SYSTEM_PROMPT},
        {"role": "user", "content": "متن"},
    ]
    # The key travels in the header, never in the URL, and only slot 1's key.
    assert request.headers["Authorization"] == f"Bearer {SLOT_1_KEY}"
    assert SLOT_1_KEY not in str(request.url)
