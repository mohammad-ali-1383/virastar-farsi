"""Correction service boundary and the Groq-first, Gemini-fallback workflow.

The HTTP layer validates input and delegates proofreading to a
``CorrectionService``; how the text is corrected stays behind this interface
(`architecture.md`, section 3). :class:`LLMCorrectionService` owns the fixed
proofreading prompt and sends the user's text to the LLM adapter separately
(`correction-prompt.md` -> Implementation Rules).

Two adapters are supported — Groq and Google Gemini (`tech-stack.md`). Groq is
always tried first; when it fails for a provider-related reason the same request
is sent once to Gemini, and whichever provider succeeds supplies the corrected
text. There is no other retry, no reordering, and no fabricated result: when
both providers fail the API returns the single documented
``PROVIDER_UNAVAILABLE`` error (`api-contract.md`).

Only provider-related failures (a :class:`~app.errors.CorrectionError`, which is
what the adapters raise for quota, authentication, timeout, connection, server,
and unusable-response errors) trigger the fallback. Any other exception is a bug
in our own code and propagates instead of being masked by a second attempt.

The successful call also reports which adapter answered and with which model, so
the optional evaluation log records the provider that really produced the text
rather than the one that was tried first (`correction-logging.md`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from app.config import settings
from app.errors import CorrectionError, ProviderUnavailableError
from app.services.gemini import GeminiProvider
from app.services.groq import GroqProvider
from app.services.prompt import PERSIAN_PROOFREADING_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Provider names, used for the fallback order and for sanitized log lines. The
# order is fixed in code on purpose: there is no provider-selection setting.
GROQ_PROVIDER = "groq"
GEMINI_PROVIDER = "gemini"


class LLMProvider(Protocol):
    """Interface implemented by every provider adapter."""

    async def generate(self, *, system_instruction: str, user_text: str) -> str:
        """Return the provider's correction of ``user_text``.

        Implementations must return non-empty corrected text and raise a
        :class:`~app.errors.CorrectionError` subclass when they cannot, rather
        than fabricating a correction.
        """
        ...


@dataclass(frozen=True)
class CorrectionResult:
    """A successful correction together with the adapter that produced it.

    ``provider`` and ``model`` describe the call that succeeded, which is the
    fallback adapter and its configured model when the primary provider failed.
    """

    text: str
    provider: str
    model: str


class CorrectionService(Protocol):
    """Interface implemented by every correction strategy."""

    async def correct(self, text: str) -> CorrectionResult:
        """Return the correction of ``text`` and the provider that produced it.

        ``text`` is already validated, non-empty, and within the configured
        length limit. Implementations must return non-empty corrected text and
        raise a :class:`~app.errors.CorrectionError` subclass when they cannot,
        rather than fabricating a correction.
        """
        ...


@dataclass(frozen=True)
class ProviderSlot:
    """One provider adapter, the name it is logged under, and its model."""

    name: str
    model: str
    adapter: LLMProvider


class LLMCorrectionService:
    """Proofreading workflow: Groq first, Gemini when Groq fails.

    The service owns the correction prompt; the adapters own provider-specific
    authentication, requests, and response parsing. Both receive the prompt and
    the user's text as separate inputs, so the fallback cannot change what the
    model is asked to do.
    """

    def __init__(self, primary: ProviderSlot, fallback: ProviderSlot) -> None:
        self._primary = primary
        self._fallback = fallback

    async def correct(self, text: str) -> CorrectionResult:
        primary_failure = ""

        try:
            return await self._generate(self._primary, text)
        except CorrectionError as error:
            # Log the provider name and the public error code only: provider
            # response bodies, keys, and the user's text never reach the logs.
            primary_failure = f"{self._primary.name}={error.code}"
            logger.warning(
                "Provider %s failed (code=%s), falling back to %s",
                self._primary.name,
                error.code,
                self._fallback.name,
            )

        try:
            return await self._generate(self._fallback, text)
        except CorrectionError as error:
            logger.warning(
                "Provider %s failed (code=%s) as well, returning a service error",
                self._fallback.name,
                error.code,
            )
            # One safe, generic failure for the client; the detail keeps the two
            # sanitized codes for the server log only.
            raise ProviderUnavailableError(
                f"both providers failed: {primary_failure} {self._fallback.name}={error.code}"
            ) from error

    async def _generate(self, slot: ProviderSlot, text: str) -> CorrectionResult:
        corrected_text = await slot.adapter.generate(
            system_instruction=PERSIAN_PROOFREADING_SYSTEM_PROMPT,
            user_text=text,
        )
        return CorrectionResult(text=corrected_text, provider=slot.name, model=slot.model)


def get_correction_service() -> CorrectionService:
    """FastAPI dependency returning the Groq-then-Gemini correction service.

    The adapters are built from the current settings on every request so a
    configuration change (environment variable or ``.env``) takes effect
    without recreating the application.
    """
    groq = GroqProvider.from_settings(settings)
    gemini = GeminiProvider.from_settings(settings)

    return LLMCorrectionService(
        primary=ProviderSlot(GROQ_PROVIDER, groq.model, groq),
        fallback=ProviderSlot(GEMINI_PROVIDER, gemini.model, gemini),
    )
