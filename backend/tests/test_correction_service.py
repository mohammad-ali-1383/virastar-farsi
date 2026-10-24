"""Tests for the correction service: Groq first, Gemini as the fallback.

The service-level tests use provider doubles; the end-to-end tests drive the
real adapters through ``httpx.MockTransport`` (installed by the
``provider_transport`` fixture) and the same FastAPI dependency the app uses, so
the real HTTP layer, validation, prompt handling, and error handlers are covered
without a real API key or network access.

The fallback failure matrix through ``POST /api/v1/correct`` — quota, timeout,
connection, server error, invalid provider output, and the both-providers-fail
case — lives in ``tests/test_provider_fallback.py``.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.errors import (
    PROVIDER_UNAVAILABLE,
    ProviderAuthError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.services.correction import (
    GEMINI_PROVIDER,
    GROQ_PROVIDER,
    CorrectionResult,
    LLMCorrectionService,
    LLMProvider,
    ProviderSlot,
    get_correction_service,
)
from app.services.prompt import PERSIAN_PROOFREADING_SYSTEM_PROMPT
from tests.conftest import (
    GROQ_TEST_KEY,
    GROQ_URL_MARKER,
    ProviderTransport,
    groq_response,
)

CORRECT_URL = "/api/v1/correct"
PERSIAN_TEXT = "سلام این یک متن ازمایشی است"
CORRECTED_TEXT = "سلام، این یک متن آزمایشی است."

# Stand-in model IDs: the service reports the model of the adapter it used.
GROQ_MODEL = "stub-groq-model"
GEMINI_MODEL = "stub-gemini-model"


class _RecordingProvider:
    """Provider double that records the arguments and returns or raises."""

    def __init__(self, result: str = "corrected", error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[tuple[str, str]] = []

    async def generate(self, *, system_instruction: str, user_text: str) -> str:
        self.calls.append((system_instruction, user_text))
        if self._error is not None:
            raise self._error
        return self._result


def _service(primary: LLMProvider, fallback: LLMProvider) -> LLMCorrectionService:
    """The production chain: Groq as the primary, Gemini as the fallback."""
    return LLMCorrectionService(
        primary=ProviderSlot(GROQ_PROVIDER, GROQ_MODEL, primary),
        fallback=ProviderSlot(GEMINI_PROVIDER, GEMINI_MODEL, fallback),
    )


def _correct(service: LLMCorrectionService, text: str = PERSIAN_TEXT) -> CorrectionResult:
    return asyncio.run(service.correct(text))


# --- Service behaviour ------------------------------------------------------


def test_service_sends_the_prompt_and_user_text_separately() -> None:
    primary = _RecordingProvider(result="متن اصلاح‌شده")
    fallback = _RecordingProvider(result="unused")

    result = _correct(_service(primary, fallback))

    assert result.text == "متن اصلاح‌شده"
    assert primary.calls == [(PERSIAN_PROOFREADING_SYSTEM_PROMPT, PERSIAN_TEXT)]
    # A successful primary means the fallback is never touched.
    assert fallback.calls == []


def test_result_names_the_provider_and_model_that_succeeded() -> None:
    result = _correct(_service(_RecordingProvider(result="ok"), _RecordingProvider()))

    # What the evaluation log records: the adapter that produced the text and
    # the model ID it sends.
    assert (result.provider, result.model) == (GROQ_PROVIDER, GROQ_MODEL)


def test_fallback_returns_its_own_result_when_the_primary_fails() -> None:
    primary = _RecordingProvider(error=ProviderAuthError("no key"))
    fallback = _RecordingProvider(result="اصلاح‌شده با گیمینی")

    result = _correct(_service(primary, fallback))

    assert result.text == "اصلاح‌شده با گیمینی"
    # The fallback that actually ran is the one reported, with its own model.
    assert (result.provider, result.model) == (GEMINI_PROVIDER, GEMINI_MODEL)


def test_fallback_receives_the_same_prompt_and_user_text() -> None:
    primary = _RecordingProvider(error=ProviderTimeoutError("timed out"))
    fallback = _RecordingProvider(result="ok")

    _correct(_service(primary, fallback), text="متن کاربر برای اصلاح")

    # Same request, other provider: the fallback cannot change what is asked.
    assert fallback.calls == [(PERSIAN_PROOFREADING_SYSTEM_PROMPT, "متن کاربر برای اصلاح")]


def test_each_provider_is_attempted_at_most_once() -> None:
    primary = _RecordingProvider(error=ProviderUnavailableError("down"))
    fallback = _RecordingProvider(error=ProviderUnavailableError("down"))

    with pytest.raises(ProviderUnavailableError):
        _correct(_service(primary, fallback))

    # One attempt per provider: no retry loop, no duplicate request.
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1


def test_both_providers_failing_raise_one_safe_error() -> None:
    primary = _RecordingProvider(error=ProviderAuthError("GROQ_API_KEY is not configured"))
    fallback = _RecordingProvider(error=ProviderAuthError("GEMINI_API_KEY is not configured"))

    with pytest.raises(ProviderUnavailableError) as excinfo:
        _correct(_service(primary, fallback))

    error = excinfo.value
    assert error.code == PROVIDER_UNAVAILABLE
    # The client-facing message is the documented generic one.
    assert "در دسترس نیست" in error.message
    # Only provider names and public codes are recorded for the server log.
    assert error.detail is not None
    assert GROQ_PROVIDER in error.detail
    assert GEMINI_PROVIDER in error.detail
    assert "GROQ_API_KEY" not in error.detail
    assert "GEMINI_API_KEY" not in error.detail


def test_a_programming_error_is_not_masked_by_the_fallback() -> None:
    primary = _RecordingProvider(error=RuntimeError("bug in our code"))
    fallback = _RecordingProvider(result="should not be reached")

    with pytest.raises(RuntimeError):
        _correct(_service(primary, fallback))

    # Only provider-related failures trigger the fallback.
    assert fallback.calls == []


# --- Dependency wiring ------------------------------------------------------


def test_dependency_builds_an_llm_backed_service() -> None:
    assert isinstance(get_correction_service(), LLMCorrectionService)


# --- End-to-end through POST /api/v1/correct --------------------------------


def test_endpoint_returns_the_groq_correction(
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_keys: None,
) -> None:
    # The default handler answers every non-routed request with a 500, so only
    # the Groq endpoint can produce a successful response.
    provider_transport.respond(lambda _: httpx.Response(500))
    provider_transport.respond_for(
        GROQ_URL_MARKER, lambda _: groq_response(CORRECTED_TEXT)
    )

    response = provider_client.post(CORRECT_URL, json={"text": PERSIAN_TEXT})

    assert response.status_code == 200
    assert response.json() == {"corrected_text": CORRECTED_TEXT}


def test_endpoint_sends_the_correction_prompt_to_groq(
    monkeypatch: pytest.MonkeyPatch,
    provider_transport: ProviderTransport,
    provider_client: TestClient,
    provider_keys: None,
) -> None:
    monkeypatch.setattr(settings, "groq_model", "openai/gpt-oss-120b")
    provider_transport.respond(lambda _: groq_response(CORRECTED_TEXT))

    provider_client.post(CORRECT_URL, json={"text": "متن"})

    request = provider_transport.requests[0]
    body = json.loads(request.content)
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["messages"] == [
        {"role": "system", "content": PERSIAN_PROOFREADING_SYSTEM_PROMPT},
        {"role": "user", "content": "متن"},
    ]
    # The key travels in the provider's header, never in the URL.
    assert request.headers["Authorization"] == f"Bearer {GROQ_TEST_KEY}"
    assert GROQ_TEST_KEY not in str(request.url)
