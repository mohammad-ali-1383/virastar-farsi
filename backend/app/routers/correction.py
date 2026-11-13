"""Public correction endpoint (`POST /api/v1/correct`).

The endpoint validates the request, delegates the proofreading to the correction
service, and passes the requested style through: the style decides which fixed
system prompt the service sends to the provider (`prompt.py`). Nothing is
persisted, so a correction is stored nowhere.
"""

from fastapi import APIRouter, Depends

from app.config import get_max_text_length
from app.errors import InternalError, InvalidInputError, TextTooLongError
from app.schemas import CorrectRequest, CorrectResponse, ErrorResponse
from app.services.correction import CorrectionService, get_correction_service

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
) -> CorrectResponse:
    """Validate the submitted text, correct it in the requested style."""
    # Validation is enforced here (never only in the frontend) and the length
    # is measured after trimming, as required by api-contract.md.
    text = payload.text.strip()

    if not text:
        raise InvalidInputError("text is empty after trimming")

    if len(text) > max_text_length:
        raise TextTooLongError(f"text length {len(text)} > limit {max_text_length}")

    result = await service.correct(text, payload.style)
    corrected_text = result.text

    # A provider that returns nothing usable is a failure, not a success.
    if not corrected_text or not corrected_text.strip():
        raise InternalError("correction service returned empty text")

    # The slot that answered is part of the success body, so a client can tell
    # which provider produced the text after a fallback. Failure responses stay
    # the unchanged error envelope.
    return CorrectResponse(
        corrected_text=corrected_text,
        model_used=result.model,
        provider_name=result.provider,
    )
