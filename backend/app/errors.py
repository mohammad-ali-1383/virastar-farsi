"""Public error contract for the API.

Every error response — validation, unknown route, unsupported method, provider
failure, or unexpected failure — uses the single structure documented in
`api-contract.md`::

    {"error": "ERROR_CODE", "message": "..."}

:class:`CorrectionError` keeps the public ``message`` and the internal
``detail`` in separate attributes, so provider errors and other internal
information are never serialised into a response. The provider-specific codes
are internal classifications used for the fallback decision and for sanitized
logs; the endpoint reports every provider failure as ``503
PROVIDER_UNAVAILABLE`` (`PROVIDER_FAILURE_CODES`).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

# Public error codes (`api-contract.md` → Error Codes).
INVALID_INPUT = "INVALID_INPUT"
TEXT_TOO_LONG = "TEXT_TOO_LONG"
PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
INTERNAL_ERROR = "INTERNAL_ERROR"
NOT_FOUND = "NOT_FOUND"
METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
HTTP_ERROR = "HTTP_ERROR"

# Internal provider classifications. Adapters raise the matching
# :class:`CorrectionError` so the correction service can decide whether to fall
# back and can log a sanitized category; clients never receive these codes.
PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
PROVIDER_RATE_LIMIT = "PROVIDER_RATE_LIMIT"
PROVIDER_AUTH_ERROR = "PROVIDER_AUTH_ERROR"

# The endpoint's public contract for provider failures: whichever class was
# raised and whichever attempt failed, the client gets one generic 503.
PROVIDER_FAILURE_CODES = frozenset(
    {
        PROVIDER_UNAVAILABLE,
        PROVIDER_TIMEOUT,
        PROVIDER_RATE_LIMIT,
        PROVIDER_AUTH_ERROR,
    }
)

# Framework-level HTTP errors (unknown path, unsupported method). The messages
# are fixed and Persian, and the original status and headers are preserved, so
# no ``detail`` from Starlette or a dependency can reach the client.
_HTTP_ERROR_RESPONSES: dict[int, tuple[str, str]] = {
    404: (NOT_FOUND, "مسیر درخواستی یافت نشد."),
    405: (METHOD_NOT_ALLOWED, "این روش درخواست برای این مسیر پشتیبانی نمی‌شود."),
}
_HTTP_ERROR_FALLBACK_MESSAGE = "درخواست قابل پردازش نبود."


class CorrectionError(Exception):
    """Base class for failures that map to a documented API error response.

    Subclasses declare the public ``code``, ``http_status``, and ``message``.
    ``detail`` is optional context for server-side logs only.

    Provider adapters (Phase 3) raise these errors instead of returning
    invented text; see `coding-rules.md` → LLM Integration.
    """

    code: str = INTERNAL_ERROR
    http_status: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    message: str = "خطای داخلی سرور رخ داد."

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.code)
        self.detail = detail


class InvalidInputError(CorrectionError):
    """Missing, empty, or malformed input."""

    code = INVALID_INPUT
    http_status = status.HTTP_400_BAD_REQUEST
    message = "متن ارسالی نامعتبر است."


class TextTooLongError(CorrectionError):
    """Text longer than the configured maximum length."""

    code = TEXT_TOO_LONG
    http_status = status.HTTP_400_BAD_REQUEST
    message = "متن واردشده بیش از حداکثر مجاز است."


class ProviderUnavailableError(CorrectionError):
    """The LLM provider could not be reached or returned a server error."""

    code = PROVIDER_UNAVAILABLE
    http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    message = "سرویس اصلاح متن در حال حاضر در دسترس نیست."


class ProviderTimeoutError(CorrectionError):
    """The LLM request exceeded the configured timeout."""

    code = PROVIDER_TIMEOUT
    http_status = status.HTTP_504_GATEWAY_TIMEOUT
    message = "زمان انتظار برای پاسخ سرویس اصلاح متن به پایان رسید."


class ProviderRateLimitError(CorrectionError):
    """The provider reported a rate or quota limit."""

    code = PROVIDER_RATE_LIMIT
    http_status = status.HTTP_429_TOO_MANY_REQUESTS
    message = "محدودیت استفاده از سرویس اصلاح متن فعال شده است. لطفاً بعداً تلاش کنید."


class ProviderAuthError(CorrectionError):
    """Provider authentication or authorisation failed."""

    code = PROVIDER_AUTH_ERROR
    http_status = status.HTTP_502_BAD_GATEWAY
    message = "ارتباط با سرویس اصلاح متن با خطا مواجه شد."


class InternalError(CorrectionError):
    """Unexpected internal failure, including invalid provider responses."""

    code = INTERNAL_ERROR
    http_status = status.HTTP_500_INTERNAL_SERVER_ERROR
    message = "خطای داخلی سرور رخ داد."


def error_response(
    code: str,
    message: str,
    http_status: int,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build the documented error body.

    ``headers`` carries framework headers that belong to the error (for example
    ``Allow`` on a 405); it never contains provider or credential material.
    """
    return JSONResponse(
        status_code=http_status,
        content={"error": code, "message": message},
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Guarantee the documented error format for every failure.

    FastAPI's default validation response (``422`` with a ``detail`` list), its
    ``{"detail": ...}`` 404/405 bodies, and its bare ``500`` are all replaced by
    the contract structure.
    """

    @app.exception_handler(CorrectionError)
    async def handle_correction_error(_: Request, exc: CorrectionError) -> JSONResponse:
        if exc.detail:
            logger.warning("Correction error %s: %s", exc.code, exc.detail)

        # Provider failures are one generic 503 on the wire (`api-contract.md`),
        # even if a service implementation raises an adapter classification
        # directly. The specific code is already in the sanitized log above.
        if exc.code in PROVIDER_FAILURE_CODES:
            return error_response(
                ProviderUnavailableError.code,
                ProviderUnavailableError.message,
                ProviderUnavailableError.http_status,
            )

        return error_response(exc.code, exc.message, exc.http_status)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Unknown route (404) and unsupported method (405) keep their status and
        # framework headers but lose Starlette's ``detail`` body. Any other
        # framework HTTP error keeps its status too, with a generic public code.
        code, message = _HTTP_ERROR_RESPONSES.get(
            exc.status_code, (HTTP_ERROR, _HTTP_ERROR_FALLBACK_MESSAGE)
        )
        return error_response(code, message, exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        _: Request, __: RequestValidationError
    ) -> JSONResponse:
        # Malformed JSON, missing fields, and wrong types are all invalid input.
        return error_response(
            InvalidInputError.code, InvalidInputError.message, InvalidInputError.http_status
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        # Log the exception type only: user text and provider payloads stay out
        # of the logs, and no stack trace is returned to the client.
        logger.error("Unhandled error: %s", type(exc).__name__)
        return error_response(InternalError.code, InternalError.message, InternalError.http_status)
