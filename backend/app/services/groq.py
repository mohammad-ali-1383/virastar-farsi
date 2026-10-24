"""Groq provider adapter (the LLM adapter of `architecture.md`).

Groq serves an OpenAI-compatible Chat Completions API, so the adapter speaks
that protocol:

    POST https://api.groq.com/openai/v1/chat/completions
    Authorization: Bearer <API key>

The fixed proofreading prompt travels as the ``system`` message and the user's
text as the ``user`` message. The API key is read from backend configuration
only and is sent as a request header (never in the URL) so it cannot leak
through access logs.

Provider-specific authentication, request construction, response parsing, and
error mapping live here so the correction service and the HTTP layer stay
provider-agnostic and another adapter can be added behind the same interface.
"""

from __future__ import annotations

import re

import httpx

from app.errors import (
    CorrectionError,
    InternalError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

# Groq's OpenAI-compatible chat completions endpoint (fixed by the provider;
# only the key and model are configuration).
GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"

# The provider reports a machine-readable token in ``error.type``/``error.code``.
# Only such a short token is ever recorded: raw provider messages are never
# logged because provider error bodies can echo request material back.
_REASON_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")

# HTTP status -> documented public error (`api-contract.md` -> Provider Error
# Mapping). Anything not listed falls back by class (4xx internal, 5xx provider).
_STATUS_TO_ERROR: dict[int, type[CorrectionError]] = {
    400: InternalError,  # malformed request built by us -> our bug, not the user's
    401: ProviderAuthError,
    403: ProviderAuthError,
    404: ProviderUnavailableError,  # configured model/resource is not available
    429: ProviderRateLimitError,
    500: ProviderUnavailableError,
    502: ProviderUnavailableError,
    503: ProviderUnavailableError,
    504: ProviderTimeoutError,
}


class GroqProvider:
    """Thin async client for Groq's OpenAI-compatible chat completions."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        # Optional injected client: tests supply one with a mock transport so no
        # real network call is made (`testing.md`).
        self._client = client

    @classmethod
    def from_settings(cls, settings: object) -> GroqProvider:
        """Build an adapter from the application settings.

        A missing key is tolerated here and reported as an authentication error
        when a request is attempted, so importing the app never fails.
        """
        return cls(
            api_key=settings.groq_api_key.get_secret_value(),  # type: ignore[attr-defined]
            model=settings.groq_model,  # type: ignore[attr-defined]
            timeout_seconds=settings.groq_timeout_seconds,  # type: ignore[attr-defined]
        )

    @property
    def endpoint(self) -> str:
        """Full chat completions URL."""

        return GROQ_CHAT_COMPLETIONS_URL

    @property
    def model(self) -> str:
        """The model ID this adapter actually sends (``GROQ_MODEL``).

        Exposed so the correction service can record the model that produced a
        correction instead of assuming configuration and request agree.
        """
        return self._model

    async def generate(self, *, system_instruction: str, user_text: str) -> str:
        """Return the provider's corrected text for ``user_text``.

        The system instruction and the user's text travel as separate messages.
        Any provider failure raises a :class:`~app.errors.CorrectionError`
        subclass; this method never returns invented text.
        """
        if not self._api_key:
            # Configuration problem, surfaced as the documented auth error.
            raise ProviderAuthError("GROQ_API_KEY is not configured")

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_text},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        response = await self._post(payload, headers)

        if response.status_code != httpx.codes.OK:
            raise _error_for_status(response)

        return _extract_text(response)

    async def _post(
        self, payload: dict[str, object], headers: dict[str, str]
    ) -> httpx.Response:
        """Send the request, translating transport failures to public errors."""
        try:
            if self._client is not None:
                return await self._client.post(self.endpoint, json=payload, headers=headers)

            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                return await client.post(self.endpoint, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("groq request timed out") from exc
        except httpx.HTTPError as exc:
            # Connection errors, DNS failures, protocol errors: unreachable.
            raise ProviderUnavailableError(
                f"groq request failed: {type(exc).__name__}"
            ) from exc


def _provider_reason(response: httpx.Response) -> str | None:
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


def _error_for_status(response: httpx.Response) -> CorrectionError:
    """Map a non-200 Groq response to a documented error (`api-contract.md`)."""
    status_code = response.status_code

    detail = f"groq http_status={status_code}"
    reason = _provider_reason(response)
    if reason:
        detail += f" reason={reason}"

    error_type = _STATUS_TO_ERROR.get(status_code)
    if error_type is None:
        error_type = ProviderUnavailableError if status_code >= 500 else InternalError

    return error_type(detail)


def _extract_text(response: httpx.Response) -> str:
    """Validate the provider payload and return the corrected text.

    A response with no choice, no message, or only whitespace is treated as a
    failure rather than a successful (empty) correction.
    """
    try:
        body = response.json()
    except ValueError as exc:
        raise InternalError("groq returned a non-JSON response") from exc

    if not isinstance(body, dict):
        raise InternalError("groq returned an unexpected response shape")

    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise InternalError("groq returned no choices")

    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None

    if not isinstance(content, str):
        raise InternalError("groq choice contained no message content")

    text = content.strip()

    if not text:
        raise InternalError("groq returned no usable text")

    return text
