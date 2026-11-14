"""Pydantic models for the public API (`api-contract.md`).

The models describe the wire format only; validation of the configured text
limit lives in the API layer because the limit is runtime configuration.
"""

from typing import Literal

from pydantic import BaseModel, Field


class CorrectRequest(BaseModel):
    """Request body of ``POST /api/v1/correct``.

    ``style`` selects the proofreading behaviour (`api-contract.md` -> Request):
    ``"preserve"`` keeps the writer's tone and register, ``"formal"`` rewrites
    the text in a formal, administrative register. The allowed values are a
    closed set, so an unsupported style is invalid input rather than a silently
    substituted default.
    """

    text: str = Field(description="Persian text to proofread.")
    style: Literal["preserve", "formal"] = Field(
        default="preserve",
        description=(
            "Proofreading style: 'preserve' corrects errors without changing the "
            "writer's tone and register; 'formal' also rewrites the text in a "
            "formal, administrative register."
        ),
    )


class CorrectResponse(BaseModel):
    """Successful response body.

    ``model_used`` and ``provider_name`` describe the provider slot that
    actually produced the correction, which is the fallback slot when an
    earlier slot failed. They are additive: a client that only reads
    ``corrected_text`` keeps working unchanged.
    """

    corrected_text: str = Field(description="Corrected Persian text.")
    model_used: str = Field(
        description="Model of the provider slot that produced the correction."
    )
    provider_name: str = Field(
        description="Name of the provider slot that produced the correction."
    )


class ExportOptions(BaseModel):
    """Formatting options of ``POST /api/v1/export-docx`` (`word-export-spec.md`).

    Every field has the documented default, so an omitted ``options`` object
    produces the reference document. The allowed values are closed sets: the
    specification deliberately limits the font list to fonts that are safe for
    Persian text, and an unsupported value is invalid input rather than a
    silently substituted default.

    The two switches are ``strict``: only a real JSON boolean is accepted, so a
    string such as ``"true"`` cannot silently enable a feature.
    """

    font: Literal["Vazirmatn", "Sahel", "Tahoma"] = Field(
        default="Vazirmatn",
        description="Font family applied to the document text (Latin and complex script).",
    )
    font_size: Literal[12, 14, 16, 18] = Field(
        default=14,
        description="Body font size in points.",
    )
    line_spacing: Literal[1.0, 1.15, 1.5, 2.0] = Field(
        default=1.5,
        description="Line spacing multiplier.",
    )
    alignment: Literal["right", "justify", "center"] = Field(
        default="justify",
        description="Visual alignment in the right-to-left document.",
    )
    margins: Literal["normal", "narrow", "wide"] = Field(
        default="normal",
        description="Page margin preset: normal 2.5 cm, narrow 1.27 cm, wide 3.5 cm.",
    )
    page_numbers: bool = Field(
        default=True,
        strict=True,
        description="Add a page number field to the footer.",
    )
    page_number_position: Literal["center", "right"] = Field(
        default="center",
        description="Footer position of the page number; used only when page_numbers is true.",
    )
    page_border: bool = Field(
        default=False,
        strict=True,
        description="Draw a thin single-line border around the page.",
    )


class ExportDocxRequest(BaseModel):
    """Request body of ``POST /api/v1/export-docx``."""

    text: str = Field(description="Corrected Persian text to export as a Word document.")
    options: ExportOptions = Field(
        default_factory=ExportOptions,
        description="Formatting options; every field defaults to the documented value.",
    )


class ErrorResponse(BaseModel):
    """Error body shared by every documented error code."""

    error: str = Field(description="Machine-readable error code.")
    message: str = Field(description="Human-readable Persian message.")
