"""Pydantic models for the public API (`api-contract.md`).

The models describe the wire format only; validation of the configured text
limit lives in the API layer because the limit is runtime configuration.
"""

from pydantic import BaseModel, Field


class CorrectRequest(BaseModel):
    """Request body of ``POST /api/v1/correct``."""

    text: str = Field(description="Persian text to proofread.")

    # Consent flag for the evaluation log (`correction-logging.md`). The default
    # is deliberately the privacy-preserving one: a client that does not state a
    # choice gets no stored text. ``strict=True`` means only a real JSON boolean
    # counts — a string such as "yes" or the number 1 is invalid input rather
    # than an accidental opt-in.
    store_for_evaluation: bool = Field(
        default=False,
        strict=True,
        description=(
            "When true, the backend may store the submitted and corrected text "
            "for quality evaluation. Defaults to false; nothing is stored otherwise."
        ),
    )


class CorrectResponse(BaseModel):
    """Successful response body."""

    corrected_text: str = Field(description="Corrected Persian text.")


class ErrorResponse(BaseModel):
    """Error body shared by every documented error code."""

    error: str = Field(description="Machine-readable error code.")
    message: str = Field(description="Human-readable Persian message.")
