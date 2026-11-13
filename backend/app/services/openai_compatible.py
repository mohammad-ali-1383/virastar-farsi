"""Generic OpenAI-compatible provider adapter (the LLM adapter of `architecture.md`).

Every configured provider slot is served by this one adapter, because any vendor
with an OpenAI-compatible Chat Completions endpoint speaks the same protocol:

    POST {BASE_URL_N}/chat/completions
    Authorization: Bearer <that slot's API key>
    Content-Type: application/json

The fixed proofreading prompt travels as the ``system`` message and the user's
text as the ``user`` message; the response is read only from
``choices[0].message.content``. Nothing vendor-specific lives here: the endpoint,
model, timeout, and any extra body fields come from the slot configuration.

The API key is read from that slot's configuration only and is sent as a request
header (never in the URL) so it cannot leak through access logs. Provider
authentication, request construction, response parsing, and error mapping stay in
this adapter so the correction service and the HTTP layer remain provider-agnostic.
"""

from __future__ import annotations

import re

import httpx

from app.config import DEFAULT_SLOT_TIMEOUT_SECONDS, ProviderSlotConfig
from app.errors import (
    CorrectionError,
    InternalError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

# Every OpenAI-compatible provider serves chat completions at this path; the
# vendor-specific part is the configured base URL.
CHAT_COMPLETIONS_PATH = "/chat/completions"

# Request body keys that a slot's EXTRA_BODY_N may never override: they carry the
# prompt, the user's text, and the non-streaming request shape this API relies on.
_PROTECTED_BODY_KEYS = frozenset({"model", "messages", "stream"})

# Providers report a machine-readable token in ``error.type``/``error.code``.
# Only such a short token is ever recorded: raw provider messages are never
# logged because provider error bodies can echo request material back.
_REASON_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")

# HTTP status -> documented public error (`api-contract.md` -> Provider Error
# Mapping). Anything not listed falls back by class (4xx internal, 5xx provider).
# 402 (payment required) is an exhausted credential, like 401/403.
_STATUS_TO_ERROR: dict[int, type[CorrectionError]] = {
    400: InternalError,  # malformed request built by us -> our bug, not the user's
    401: ProviderAuthError,
    402: ProviderAuthError,
    403: ProviderAuthError,
    404: ProviderUnavailableError,  # configured model/resource is not available
    429: ProviderRateLimitError,
    500: ProviderUnavailableError,
    502: ProviderUnavailableError,
    503: ProviderUnavailableError,
    504: ProviderTimeoutError,
}


class OpenAICompatibleProvider:
    """Thin async client for an OpenAI-compatible chat completions endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        name: str = "slot-1",
        timeout_seconds: float = DEFAULT_SLOT_TIMEOUT_SECONDS,
        extra_body: dict[str, object] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._name = name
        self._timeout_seconds = timeout_seconds
        self._extra_body = extra_body
        # Optional injected client: tests supply one with a mock transport so no
        # real network call is made (`testing.md`).
        self._client = client

    @classmethod
    def from_slot(cls, slot: ProviderSlotConfig) -> OpenAICompatibleProvider:
        """Build an adapter from one discovered provider slot.

        A missing key is tolerated here and reported as an authentication error
        when a request is attempted, so importing the app never fails.
        """
        return cls(
            api_key=slot.api_key.get_secret_value(),
            base_url=slot.base_url,
            model=slot.model,
            name=slot.name,
            timeout_seconds=slot.timeout_seconds,
            extra_body=slot.extra_body,
        )

    @property
    def endpoint(self) -> str:
        """Full chat completions URL for the configured base URL."""
        return f"{self._base_url.rstrip('/')}{CHAT_COMPLETIONS_PATH}"

    @property
    def name(self) -> str:
        """The slot label used in the logs and in the evaluation log."""
        return self._name

    @property
    def model(self) -> str:
        """The model ID this adapter actually sends (``MODEL_N``).

        Exposed so the correction service can record the model that produced a
        correction instead of assuming configuration and request agree.
        """
        return self._model

    async def generate(
        self,
        *,
        system_instruction: str,
        user_text: str,
        timeout_seconds: float | None = None,
    ) -> str:
        """Return the provider's corrected text for ``user_text``.

        The system instruction and the user's text travel as separate messages.
        ``timeout_seconds`` lets the fallback chain cap the request to the time
        it has left; the configured per-slot timeout is an upper bound either way.
        Any provider failure raises a :class:`~app.errors.CorrectionError`
        subclass; this method never returns invented or unchanged text.
        """
        if not self._api_key:
            # Configuration problem, surfaced as the documented auth error.
            raise ProviderAuthError(f"{self._name} has no API key configured")

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        response = await self._post(
            self._payload(system_instruction, user_text), headers, timeout_seconds
        )

        if response.status_code != httpx.codes.OK:
            raise self._error_for_status(response)

        return self._extract_text(response)

    def _payload(self, system_instruction: str, user_text: str) -> dict[str, object]:
        """Build the request body, merging ``EXTRA_BODY_N`` safely.

        Extra fields may add provider-specific options (for example disabling a
        reasoning mode), but they can never replace the model, the messages, or
        the non-streaming flag.
        """
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.2,
            "stream": False,
        }

        extra_body = self._extra_body
        if isinstance(extra_body, dict):
            for key, value in extra_body.items():
                if key not in _PROTECTED_BODY_KEYS:
                    payload[key] = value

        return payload

    def _effective_timeout(self, timeout_seconds: float | None) -> float:
        """The per-slot timeout, capped by the time the chain has left."""
        if timeout_seconds is None:
            return self._timeout_seconds
        return min(self._timeout_seconds, timeout_seconds)

    async def _post(
        self,
        payload: dict[str, object],
        headers: dict[str, str],
        timeout_seconds: float | None,
    ) -> httpx.Response:
        """Send the request, translating transport failures to public errors."""
        timeout = self._effective_timeout(timeout_seconds)

        try:
            if self._client is not None:
                return await self._client.post(
                    self.endpoint, json=payload, headers=headers, timeout=timeout
                )

            async with httpx.AsyncClient(timeout=timeout) as client:
                return await client.post(
                    self.endpoint, json=payload, headers=headers, timeout=timeout
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"{self._name} request timed out") from exc
        except httpx.HTTPError as exc:
            # Connection errors, DNS failures, TLS and protocol errors: unreachable.
            raise ProviderUnavailableError(
                f"{self._name} request failed: {type(exc).__name__}"
            ) from exc

    def _provider_reason(self, response: httpx.Response) -> str | None:
        """Return the provider's machine token, or ``None`` if unusable.

        Only a short alphanumeric token is accepted; the free-form ``message`` is
        deliberately discarded so no provider detail (or echoed credential) can
        reach the logs.
        """
        try:
            body = response.json()
        except ValueError:
            return None

        error = body.get("error") if isinstance(body, dict) else None
        if not isinstance(error, dict):
            return None

        for key in ("type", "code"):
            value = error.get(key)
            if isinstance(value, str) and _REASON_PATTERN.match(value):
                return value
        return None

    def _error_for_status(self, response: httpx.Response) -> CorrectionError:
        """Map a non-200 response to a documented error (`api-contract.md`)."""
        status_code = response.status_code

        detail = f"{self._name} http_status={status_code}"
        reason = self._provider_reason(response)
        if reason:
            detail += f" reason={reason}"

        error_type = _STATUS_TO_ERROR.get(status_code)
        if error_type is None:
            error_type = ProviderUnavailableError if status_code >= 500 else InternalError

        return error_type(detail)

    def _extract_text(self, response: httpx.Response) -> str:
        """Validate the payload and return the corrected text.

        Only ``choices[0].message.content`` counts; ``reasoning_content`` and any
        other field are ignored. A missing, non-string, or whitespace-only answer
        is a failure rather than a successful (empty) correction.
        """
        try:
            body = response.json()
        except ValueError as exc:
            raise InternalError(f"{self._name} returned a non-JSON response") from exc

        if not isinstance(body, dict):
            raise InternalError(f"{self._name} returned an unexpected response shape")

        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise InternalError(f"{self._name} returned no choices")

        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None

        if not isinstance(content, str):
            raise InternalError(f"{self._name} choice contained no message content")

        text = content.strip()

        if not text:
            raise InternalError(f"{self._name} returned no usable text")

        return text
