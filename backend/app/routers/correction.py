"""Public correction endpoint (`POST /api/v1/correct`).

The endpoint owns two things besides validation: it never calls a provider for
invalid input, and it is the **only** place that decides whether the optional
evaluation record is written (`correction-logging.md`). Logging is enforced here,
not in the browser: a record is appended only when the request explicitly asks
for it, and a failed write never changes the response the user receives.
"""

from fastapi import APIRouter, Depends

from app.config import get_max_text_length, settings
from app.errors import InternalError, InvalidInputError, TextTooLongError
from app.schemas import CorrectRequest, CorrectResponse, ErrorResponse
from app.services.correction import CorrectionService, get_correction_service
from app.services.correction_log import CorrectionLogStore

router = APIRouter(prefix="/api/v1", tags=["correction"])

# The responses this operation documents (`api-contract.md` → Error Codes).
# Provider failures of every category (rate limit, authentication, timeout,
# unavailable) reach the client as the single 503 below, so 429/502/504 are not
# part of this operation's public responses.
ERROR_RESPONSES = {
    400: {"model": ErrorResponse, "description": "INVALID_INPUT or TEXT_TOO_LONG"},
    500: {"model": ErrorResponse, "description": "INTERNAL_ERROR"},
    503: {"model": ErrorResponse, "description": "PROVIDER_UNAVAILABLE"},
}


def get_correction_log_store() -> CorrectionLogStore:
    """FastAPI dependency returning the evaluation-log store.

    The path is read from the current settings on every request, so a
    configuration change (environment variable or ``.env``) takes effect without
    recreating the application, and tests can point it at a temporary file.
    """
    return CorrectionLogStore(settings.correction_log_path)


@router.post(
    "/correct",
    response_model=CorrectResponse,
    responses=ERROR_RESPONSES,
    summary="Correct Persian text",
)
async def correct_text(
    payload: CorrectRequest,
    service: CorrectionService = Depends(get_correction_service),
    max_text_length: int = Depends(get_max_text_length),
    log_store: CorrectionLogStore = Depends(get_correction_log_store),
) -> CorrectResponse:
    """Validate the submitted text, correct it, and honor the consent flag."""
    # Validation is enforced here (never only in the frontend) and the length
    # is measured after trimming, as required by api-contract.md.
    text = payload.text.strip()

    if not text:
        raise InvalidInputError("text is empty after trimming")

    if len(text) > max_text_length:
        raise TextTooLongError(f"text length {len(text)} > limit {max_text_length}")

    result = await service.correct(text)
    corrected_text = result.text

    # A provider that returns nothing usable is a failure, not a success.
    if not corrected_text or not corrected_text.strip():
        raise InternalError("correction service returned empty text")

    # Consent decides everything: without an explicit opt-in neither text is
    # written anywhere. The record names the provider that actually succeeded,
    # which is the fallback provider after a Groq failure. A failed write is
    # reported by the store itself (sanitized warning) and never fails or
    # retries the request.
    if payload.store_for_evaluation:
        await log_store.append(
            original_text=text,
            provider=result.provider,
            model=result.model,
            corrected_text=corrected_text,
        )

    return CorrectResponse(corrected_text=corrected_text)
