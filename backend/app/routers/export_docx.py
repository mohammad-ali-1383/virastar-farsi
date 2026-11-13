"""Public Word export endpoint (`POST /api/v1/export-docx`).

The endpoint is the only place that validates the submitted text for export —
with the same trimming, empty-text rule, and configurable length limit as
``POST /api/v1/correct`` — and then streams the generated file. Generation is
synchronous CPU work with no provider call, so the handler is a regular ``def``
and FastAPI runs it in its thread pool.
"""

from fastapi import APIRouter, Depends, Response

from app.config import get_max_text_length
from app.errors import InvalidInputError, TextTooLongError
from app.schemas import ErrorResponse, ExportDocxRequest
from app.services.docx_export import (
    DOCX_MEDIA_TYPE,
    EXPORT_FILENAME,
    DocxExportService,
    get_docx_export_service,
)

router = APIRouter(prefix="/api/v1", tags=["export"])

# The responses this operation documents (`api-contract.md` → Error Codes).
ERROR_RESPONSES = {
    400: {"model": ErrorResponse, "description": "INVALID_INPUT or TEXT_TOO_LONG"},
    500: {"model": ErrorResponse, "description": "INTERNAL_ERROR"},
}

# The browser saves the file under this name (`word-export-spec.md`).
CONTENT_DISPOSITION = f'attachment; filename="{EXPORT_FILENAME}"'


@router.post(
    "/export-docx",
    response_class=Response,
    responses={
        200: {
            "description": "Generated Word document.",
            "content": {DOCX_MEDIA_TYPE: {}},
        },
        **ERROR_RESPONSES,
    },
    summary="Export Persian text as a Word document",
)
def export_docx(
    payload: ExportDocxRequest,
    service: DocxExportService = Depends(get_docx_export_service),
    max_text_length: int = Depends(get_max_text_length),
) -> Response:
    """Validate the text, build the ``.docx`` in memory, and return it."""
    # Validation is enforced here (never only in the frontend). The length is
    # measured after trimming, exactly like the correction endpoint.
    text = payload.text.strip()

    if not text:
        raise InvalidInputError("text is empty after trimming")

    if len(text) > max_text_length:
        raise TextTooLongError(f"text length {len(text)} > limit {max_text_length}")

    document = service.render(text=text, options=payload.options)

    return Response(
        content=document,
        media_type=DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": CONTENT_DISPOSITION},
    )
