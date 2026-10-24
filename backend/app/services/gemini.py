"""Google Gemini provider adapter (the LLM adapter of `architecture.md`).

The adapter talks to the Gemini Developer API ``generateContent`` REST method:

    POST {base}/v1beta/models/{model}:generateContent
    x-goog-api-key: <API key>

The fixed proofreading prompt travels as the ``systemInstruction`` and the
user's text as a separate ``contents`` entry. The API key is read from backend
configuration only and is sent as a request header (never in the URL) so it
cannot leak through access logs.

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

# Gemini Developer API base URL and version (`https://ai.google.dev/api`). The
# adapter appends ``/models/{model}:generateContent``; only the API key and the
# model are configuration.
GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com"
GEMINI_API_VERSION = "v1beta"

# The provider reports the gRPC reason in ``error.status``. Only that short
# machine token is ever recorded: raw provider messages are never logged because
# Google error bodies can echo request material back to the caller.
_REASON_PATTERN = re.compile(r"^[A-Z_]{1,64}$")

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


class GeminiProvider:
    """Thin async client for the Gemini ``generateContent`` method."""

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
    def from_settings(cls, settings: object) -> GeminiProvider:
        """Build an adapter from the application settings.

        A missing key is tolerated here and reported as an authentication error
        when a request is attempted, so importing the app never fails.
        """
        return cls(
            api_key=settings.gemini_api_key.get_secret_value(),  # type: ignore[attr-defined]
            model=settings.gemini_model,  # type: ignore[attr-defined]
            timeout_seconds=settings.gemini_timeout_seconds,  # type: ignore[attr-defined]
        )

    @property
    def endpoint(self) -> str:
        """Full ``generateContent`` URL for the configured model."""

        return (
            f"{GEMINI_API_BASE_URL}/{GEMINI_API_VERSION}"
            f"/models/{self._model}:generateContent"
        )

    @property
    def model(self) -> str:
        """The model ID this adapter actually sends (``GEMINI_MODEL``).

        Exposed so the correction service can record the model that produced a
        correction instead of assuming configuration and request agree.
        """
        return self._model

    async def generate(self, *, system_instruction: str, user_text: str) -> str:
        """Return the provider's corrected text for ``user_text``.

        The system instruction and the user's text travel as separate fields.
        Any provider failure raises a :class:`~app.errors.CorrectionError`
        subclass; this method never returns invented text.
        """
        if not self._api_key:
            # Configuration problem, surfaced as the documented auth error.
            raise ProviderAuthError("GEMINI_API_KEY is not configured")

        payload = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"parts": [{"text": user_text}]}],
        }
        headers = {
            "x-goog-api-key": self._api_key,
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
            raise ProviderTimeoutError("gemini request timed out") from exc
        except httpx.HTTPError as exc:
            # Connection errors, DNS failures, protocol errors: unreachable.
            raise ProviderUnavailableError(
                f"gemini request failed: {type(exc).__name__}"
            ) from exc


def _provider_reason(response: httpx.Response) -> str | None:
    """Return the provider's gRPC reason token, or ``None`` if unusable.

    Only a short uppercase status token is accepted; the free-form ``message``
    is deliberately discarded so no provider detail (or echoed credential) can
    reach the logs.
    """
    try:
        body = response.json()
    except ValueError:
        return None

    error = body.get("error") if isinstance(body, dict) else None
    status = error.get("status") if isinstance(error, dict) else None

    if isinstance(status, str) and _REASON_PATTERN.match(status):
        return status
    return None


def _error_for_status(response: httpx.Response) -> CorrectionError:
    """Map a non-200 Gemini response to a documented error (`api-contract.md`)."""
    status_code = response.status_code

    detail = f"gemini http_status={status_code}"
    reason = _provider_reason(response)
    if reason:
        detail += f" reason={reason}"

    error_type = _STATUS_TO_ERROR.get(status_code)
    if error_type is None:
        error_type = ProviderUnavailableError if status_code >= 500 else InternalError

    return error_type(detail)


def _extract_text(response: httpx.Response) -> str:
    """Validate the provider payload and return the corrected text.

    A response with no candidate, no parts, or only whitespace is treated as a
    failure rather than a successful (empty) correction.
    """
    try:
        body = response.json()
    except ValueError as exc:
        raise InternalError("gemini returned a non-JSON response") from exc

    if not isinstance(body, dict):
        raise InternalError("gemini returned an unexpected response shape")

    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise InternalError("gemini returned no candidates")

    first = candidates[0]
    content = first.get("content") if isinstance(first, dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None

    if not isinstance(parts, list):
        raise InternalError("gemini candidate contained no parts")

    text = "".join(
        part["text"]
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ).strip()

    if not text:
        raise InternalError("gemini returned no usable text")

    return text
