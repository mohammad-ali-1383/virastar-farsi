"""Correction service boundary and the ordered provider-slot fallback chain.

The HTTP layer validates input and delegates proofreading to a
``CorrectionService``; how the text is corrected stays behind this interface
(`architecture.md`, section 3). :class:`LLMCorrectionService` owns the fixed
proofreading prompt and sends the user's text to the LLM adapter separately
(`correction-prompt.md` -> Implementation Rules).

Providers are not named in code any more: the configuration describes an ordered
list of OpenAI-compatible "slots" (`config.load_provider_slots`), and every slot
is served by the same generic adapter. Slots are tried in ascending numeric
order and any provider-related failure — authentication, payment, quota, missing
model, server error, timeout, connection/DNS/TLS error, or an unusable response —
moves on to the next slot. There is no retry on the same slot and no fabricated
result: when every slot fails, the API returns the single documented
``PROVIDER_UNAVAILABLE`` error (`api-contract.md`).

Two safeguards keep a long list of slots from making every request slow:

- an overall deadline for the whole chain (below the Nginx proxy timeout), which
  caps each attempt to the time that is left and stops before starting a slot
  that cannot finish;
- an in-memory cooldown per slot, so a slot that just reported a broken or
  exhausted credential or a rate limit is skipped for a while.

Only provider-related failures (a :class:`~app.errors.CorrectionError`, which is
what the adapter raises) trigger the fallback. Any other exception is a bug in
our own code and propagates instead of being masked by another attempt.

The successful call also reports which slot answered and with which model, so the
optional evaluation log records the slot that really produced the text rather
than the one that was tried first (`correction-logging.md`).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.config import load_provider_slots
from app.errors import (
    CorrectionError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from app.services.openai_compatible import OpenAICompatibleProvider
from app.services.prompt import PERSIAN_PROOFREADING_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# The whole chain must answer inside this budget. Nginx allows 75 seconds
# (`frontend/nginx.conf`), which leaves room for the request and the response
# while a slow slot cannot keep the browser waiting indefinitely.
OVERALL_DEADLINE_SECONDS = 60.0

# Do not start another slot with less time than this left: it could not answer.
MIN_SLOT_SECONDS = 5.0

# Seconds a slot is skipped after a failure that will not fix itself quickly.
# 401/402/403 mean the credential or its quota is unusable (a long pause),
# 429 means a rate limit (a short pause). Everything else is retried next time.
AUTH_COOLDOWN_SECONDS = 300.0
RATE_LIMIT_COOLDOWN_SECONDS = 30.0


def _cooldown_seconds(error: CorrectionError) -> float | None:
    """How long a slot is skipped after ``error``, or ``None`` if not at all."""
    if isinstance(error, ProviderRateLimitError):
        return RATE_LIMIT_COOLDOWN_SECONDS
    if isinstance(error, ProviderAuthError):
        return AUTH_COOLDOWN_SECONDS
    return None


class LLMProvider(Protocol):
    """Interface implemented by the provider adapter."""

    async def generate(
        self,
        *,
        system_instruction: str,
        user_text: str,
        timeout_seconds: float | None = None,
    ) -> str:
        """Return the provider's correction of ``user_text``.

        Implementations must return non-empty corrected text and raise a
        :class:`~app.errors.CorrectionError` subclass when they cannot, rather
        than fabricating a correction.
        """
        ...


@dataclass(frozen=True)
class CorrectionResult:
    """A successful correction together with the slot that produced it.

    ``provider`` and ``model`` describe the call that succeeded, which is the
    fallback slot and its configured model when an earlier slot failed.
    """

    text: str
    provider: str
    model: str


class CorrectionService(Protocol):
    """Interface implemented by every correction strategy."""

    async def correct(self, text: str) -> CorrectionResult:
        """Return the correction of ``text`` and the slot that produced it.

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


class SlotCooldown:
    """In-memory, per-process cooldown for slots that are known to be unusable.

    The clock is injectable so tests can prove both the duration and the expiry
    without waiting. Nothing is persisted: a restart starts with every slot
    available again, which is exactly what a fixed key or a new quota needs.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._until: dict[str, float] = {}

    def active(self, name: str) -> bool:
        """Whether ``name`` is still cooling down (expired entries are dropped)."""
        until = self._until.get(name)
        if until is None:
            return False
        if self._clock() >= until:
            del self._until[name]
            return False
        return True

    def enter(self, name: str, error: CorrectionError) -> None:
        """Start a cooldown for ``name`` when ``error`` calls for one."""
        seconds = _cooldown_seconds(error)
        if seconds is None:
            return

        self._until[name] = self._clock() + seconds
        # One safe line: the slot label and the public code only.
        logger.warning(
            "Provider slot %s is in cooldown (code=%s)", name, error.code
        )

    def clear(self, name: str) -> None:
        """Forget a cooldown after a success."""
        self._until.pop(name, None)

    def reset(self) -> None:
        """Forget every cooldown (tests)."""
        self._until.clear()


class LLMCorrectionService:
    """Proofreading workflow over the configured slot list, in order.

    The service owns the correction prompt; the adapter owns authentication,
    requests, and response parsing. Every slot receives the prompt and the user's
    text as separate inputs, so a fallback cannot change what the model is asked.
    """

    def __init__(
        self,
        slots: Sequence[ProviderSlot],
        *,
        cooldowns: SlotCooldown | None = None,
        clock: Callable[[], float] = time.monotonic,
        deadline_seconds: float = OVERALL_DEADLINE_SECONDS,
    ) -> None:
        self._slots = tuple(slots)
        self._clock = clock
        self._deadline_seconds = deadline_seconds
        self._cooldowns = cooldowns if cooldowns is not None else SlotCooldown(clock=clock)

    async def correct(self, text: str) -> CorrectionResult:
        """Try the configured slots in order and return the first correction.

        Every failed slot is logged with its name and a sanitized error code only;
        the user's text, the keys, and the provider response bodies never reach
        the logs. When no slot can answer, one generic service error is raised.
        """
        if not self._slots:
            raise ProviderUnavailableError("no provider slot is configured")

        failures: list[str] = []
        deadline = self._clock() + self._deadline_seconds

        for slot in self._slots:
            if self._cooldowns.active(slot.name):
                failures.append(f"{slot.name}=cooldown")
                continue

            remaining = deadline - self._clock()
            if remaining < MIN_SLOT_SECONDS:
                # The chain is out of time: no further slot could finish.
                failures.append(f"{slot.name}=deadline")
                logger.warning(
                    "Provider deadline reached; the remaining slots were not tried"
                )
                break

            try:
                corrected_text = await slot.adapter.generate(
                    system_instruction=PERSIAN_PROOFREADING_SYSTEM_PROMPT,
                    user_text=text,
                    timeout_seconds=remaining,
                )
            except CorrectionError as error:
                failures.append(f"{slot.name}={error.code}")
                logger.warning(
                    "Provider slot %s failed (code=%s)", slot.name, error.code
                )
                self._cooldowns.enter(slot.name, error)
                continue

            self._cooldowns.clear(slot.name)
            logger.info("Correction served by slot=%s", slot.name)

            return CorrectionResult(
                text=corrected_text, provider=slot.name, model=slot.model
            )

        raise ProviderUnavailableError("all provider slots failed: " + " ".join(failures))


# Cooldowns belong to the process, not to a request: the dependency builds a
# fresh service per request, while exhausted slots must stay skipped for longer.
_cooldowns = SlotCooldown()


def get_correction_service() -> CorrectionService:
    """FastAPI dependency returning the ordered slot chain.

    The slots are discovered from the current configuration on every request, so
    a changed `.env` (or environment) takes effect without recreating the
    application. When no slot is usable the returned service answers with the
    documented ``503 PROVIDER_UNAVAILABLE``.
    """
    slots = tuple(
        ProviderSlot(
            config.name,
            config.model,
            OpenAICompatibleProvider.from_slot(config),
        )
        for config in load_provider_slots()
    )

    return LLMCorrectionService(slots, cooldowns=_cooldowns)
