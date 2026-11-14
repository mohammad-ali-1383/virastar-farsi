"""Tests for ``POST /api/v1/export-docx`` (`word-export-spec.md`)."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from fastapi.testclient import TestClient

from app.config import settings
from app.errors import INVALID_INPUT, TEXT_TOO_LONG
from app.main import app
from app.services.docx_export import (
    DOCX_MEDIA_TYPE,
    EXPORT_FILENAME,
    get_docx_export_service,
)
from app.schemas import ExportOptions

EXPORT_URL = "/api/v1/export-docx"

PERSIAN_TEXT = "سلام، این یک متن آزمایشی است.\nخط دوم"

TEXT_TOO_LONG_MESSAGE = "متن واردشده بیش از حداکثر مجاز است."


# --- Helpers --------------------------------------------------------------- #


def _post(client: TestClient, **payload: object):
    return client.post(EXPORT_URL, json=payload)


def _part(content: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return archive.read(name)


def _xml(content: bytes, name: str = "word/document.xml"):
    return parse_xml(_part(content, name))


def _body_paragraphs(content: bytes) -> list:
    return _xml(content).find(qn("w:body")).findall(qn("w:p"))


def _run_properties(paragraph) -> list:
    return [
        run.find(qn("w:rPr"))
        for run in paragraph.findall(qn("w:r"))
        if run.find(qn("w:rPr")) is not None
    ]


def _value(element) -> str:
    return element.get(qn("w:val"))


# --- Success path ---------------------------------------------------------- #


def test_export_returns_a_downloadable_docx(client: TestClient) -> None:
    response = _post(client, text=PERSIAN_TEXT)

    assert response.status_code == 200
    assert response.headers["content-type"] == DOCX_MEDIA_TYPE
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="{EXPORT_FILENAME}"'
    )
    # A .docx is a ZIP container; the body must be the file itself.
    assert response.content[:2] == b"PK"
    document = Document(io.BytesIO(response.content))
    assert [paragraph.text for paragraph in document.paragraphs] == [
        "سلام، این یک متن آزمایشی است.",
        "خط دوم",
    ]


def test_export_route_is_registered_and_documented(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    operation = schema["paths"][EXPORT_URL]["post"]

    assert operation["responses"].keys() == {"200", "400", "500"}
    assert DOCX_MEDIA_TYPE in operation["responses"]["200"]["content"]
    assert "ExportOptions" in schema["components"]["schemas"]
    assert "ExportDocxRequest" in schema["components"]["schemas"]


def test_blank_lines_become_empty_paragraphs(client: TestClient) -> None:
    response = _post(client, text="خط اول\n\nخط سوم")

    document = Document(io.BytesIO(response.content))
    assert [paragraph.text for paragraph in document.paragraphs] == [
        "خط اول",
        "",
        "خط سوم",
    ]


def test_text_edges_are_trimmed(client: TestClient) -> None:
    response = _post(client, text="  متن  \n")

    document = Document(io.BytesIO(response.content))
    assert [paragraph.text for paragraph in document.paragraphs] == ["متن"]


# --- Mandatory RTL and Complex Script rules ------------------------------- #


def test_section_paragraphs_and_runs_are_right_to_left(client: TestClient) -> None:
    response = _post(client, text=PERSIAN_TEXT)

    body = _xml(response.content).find(qn("w:body"))
    section_properties = body.find(qn("w:sectPr"))
    assert section_properties.find(qn("w:bidi")) is not None

    paragraphs = _body_paragraphs(response.content)
    assert len(paragraphs) == 2
    for paragraph in paragraphs:
        assert paragraph.find(qn("w:pPr")).find(qn("w:bidi")) is not None
        for rPr in _run_properties(paragraph):
            assert rPr.find(qn("w:rtl")) is not None
            assert rPr.find(qn("w:szCs")) is not None
            assert rPr.find(qn("w:lang")).get(qn("w:bidi")) == "fa-IR"


def test_complex_script_font_and_size_are_set_in_defaults(client: TestClient) -> None:
    response = _post(client, text=PERSIAN_TEXT)

    styles = _xml(response.content, "word/styles.xml")
    rPr = (
        styles.find(qn("w:docDefaults"))
        .find(qn("w:rPrDefault"))
        .find(qn("w:rPr"))
    )
    rFonts = rPr.find(qn("w:rFonts"))

    assert rFonts.get(qn("w:cs")) == "Vazirmatn"
    # A surviving theme attribute would override the explicit Persian font.
    assert rFonts.get(qn("w:cstheme")) is None
    assert _value(rPr.find(qn("w:szCs"))) == "28"  # 14 pt in half-points
    assert rPr.find(qn("w:rtl")) is not None
    assert rPr.find(qn("w:lang")).get(qn("w:bidi")) == "fa-IR"

    document = Document(io.BytesIO(response.content))
    normal = document.styles["Normal"]
    assert normal.font.size.pt == 14


def test_page_setup_is_a4_portrait_with_normal_margins(client: TestClient) -> None:
    response = _post(client, text=PERSIAN_TEXT)

    section = Document(io.BytesIO(response.content)).sections[0]
    assert section.orientation == WD_ORIENT.PORTRAIT
    assert round(section.page_width.cm, 2) == 21.0
    assert round(section.page_height.cm, 2) == 29.7
    for margin in (
        section.top_margin,
        section.bottom_margin,
        section.left_margin,
        section.right_margin,
    ):
        assert round(margin.cm, 2) == 2.5


# --- Options --------------------------------------------------------------- #


def test_omitting_options_uses_the_documented_defaults() -> None:
    options = ExportOptions()

    assert options.font == "Vazirmatn"
    assert options.font_size == 14
    assert options.line_spacing == 1.5
    assert options.alignment == "justify"
    assert options.margins == "normal"
    assert options.page_numbers is True
    assert options.page_number_position == "center"
    assert options.page_border is False


@pytest.mark.parametrize(
    ("alignment", "jc_value"),
    [
        ("justify", "both"),
        # Visual right is the logical start edge in a bidi paragraph; the
        # physical ``right`` value would render on the visual left.
        ("right", "start"),
        ("center", "center"),
    ],
)
def test_alignment_uses_the_correct_jc_value_for_rtl(
    client: TestClient, alignment: str, jc_value: str
) -> None:
    response = _post(
        client,
        text=PERSIAN_TEXT,
        options={"alignment": alignment},
    )

    paragraph = _body_paragraphs(response.content)[0]
    assert _value(paragraph.find(qn("w:pPr")).find(qn("w:jc"))) == jc_value


def test_font_and_size_apply_to_persian_runs(client: TestClient) -> None:
    response = _post(
        client,
        text=PERSIAN_TEXT,
        options={"font": "Tahoma", "font_size": 18},
    )

    for rPr in _run_properties(_body_paragraphs(response.content)[0]):
        rFonts = rPr.find(qn("w:rFonts"))
        assert rFonts.get(qn("w:cs")) == "Tahoma"
        assert rFonts.get(qn("w:ascii")) == "Tahoma"
        assert _value(rPr.find(qn("w:szCs"))) == "36"
        assert _value(rPr.find(qn("w:sz"))) == "36"


@pytest.mark.parametrize(
    ("spacing", "line"),
    [(1.0, "240"), (1.15, "276"), (1.5, "360"), (2.0, "480")],
)
def test_line_spacing_is_stored_as_a_multiplier(
    client: TestClient, spacing: float, line: str
) -> None:
    response = _post(
        client,
        text=PERSIAN_TEXT,
        options={"line_spacing": spacing},
    )

    paragraph = _body_paragraphs(response.content)[0]
    element = paragraph.find(qn("w:pPr")).find(qn("w:spacing"))
    assert element.get(qn("w:line")) == line
    assert element.get(qn("w:lineRule")) == "auto"


@pytest.mark.parametrize(
    ("preset", "centimetres"),
    [("narrow", 1.27), ("wide", 3.5)],
)
def test_margin_presets(
    client: TestClient, preset: str, centimetres: float
) -> None:
    response = _post(
        client,
        text=PERSIAN_TEXT,
        options={"margins": preset},
    )

    section = Document(io.BytesIO(response.content)).sections[0]
    assert round(section.top_margin.cm, 2) == centimetres
    assert round(section.left_margin.cm, 2) == centimetres


def _footer_xml(content: bytes):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        footers = [name for name in archive.namelist() if name.startswith("word/footer")]
        assert len(footers) == 1
        return parse_xml(archive.read(footers[0]))


def test_page_numbers_are_a_page_field_in_the_centered_footer(
    client: TestClient,
) -> None:
    response = _post(client, text=PERSIAN_TEXT)

    footer = _footer_xml(response.content)
    paragraph = footer.findall(qn("w:p"))[0]

    assert _value(paragraph.find(qn("w:pPr")).find(qn("w:jc"))) == "center"
    instructions = footer.findall(".//" + qn("w:instrText"))
    assert [element.text.strip() for element in instructions] == ["PAGE"]
    field_types = [
        element.get(qn("w:fldCharType"))
        for element in footer.findall(".//" + qn("w:fldChar"))
    ]
    assert field_types == ["begin", "separate", "end"]


def test_page_number_position_right_is_the_logical_start(
    client: TestClient,
) -> None:
    response = _post(
        client,
        text=PERSIAN_TEXT,
        options={"page_number_position": "right"},
    )

    paragraph = _footer_xml(response.content).findall(qn("w:p"))[0]
    assert _value(paragraph.find(qn("w:pPr")).find(qn("w:jc"))) == "start"


def test_page_numbers_can_be_disabled(client: TestClient) -> None:
    response = _post(client, text=PERSIAN_TEXT, options={"page_numbers": False})

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert not [
            name for name in archive.namelist() if name.startswith("word/footer")
        ]


def test_page_border_is_added_only_when_requested(client: TestClient) -> None:
    bordered = _post(client, text=PERSIAN_TEXT, options={"page_border": True})
    plain = _post(client, text=PERSIAN_TEXT, options={"page_border": False})

    section_properties = (
        _xml(bordered.content).find(qn("w:body")).find(qn("w:sectPr"))
    )
    pgBorders = section_properties.find(qn("w:pgBorders"))
    assert pgBorders is not None
    assert pgBorders.get(qn("w:offsetFrom")) == "page"
    for edge in ("top", "left", "bottom", "right"):
        border = pgBorders.find(qn(f"w:{edge}"))
        assert _value(border) == "single"
        assert border.get(qn("w:sz")) == "4"

    plain_section = _xml(plain.content).find(qn("w:body")).find(qn("w:sectPr"))
    assert plain_section.find(qn("w:pgBorders")) is None


# --- Invalid input --------------------------------------------------------- #


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="missing-text"),
        pytest.param({"text": None}, id="null-text"),
        pytest.param({"text": ""}, id="empty-text"),
        pytest.param({"text": "   "}, id="spaces-only"),
        pytest.param({"text": "\n\t \r\n"}, id="whitespace-only"),
        pytest.param({"text": 5}, id="wrong-type"),
        pytest.param({"text": ["متن"]}, id="list-text"),
    ],
)
def test_invalid_text_is_rejected(client: TestClient, body: dict[str, object]) -> None:
    response = _post(client, **body)

    assert response.status_code == 400
    assert response.json()["error"] == INVALID_INPUT


@pytest.mark.parametrize(
    "options",
    [
        pytest.param({"font": "Comic Sans"}, id="unknown-font"),
        pytest.param({"font_size": 13}, id="unsupported-size"),
        pytest.param({"line_spacing": 1.2}, id="unsupported-spacing"),
        pytest.param({"alignment": "left"}, id="unsupported-alignment"),
        pytest.param({"margins": "huge"}, id="unknown-margin"),
        pytest.param({"page_numbers": "true"}, id="non-boolean-page-numbers"),
        pytest.param({"page_border": 1}, id="non-boolean-page-border"),
        pytest.param({"page_number_position": "left"}, id="unknown-position"),
    ],
)
def test_invalid_options_are_rejected(
    client: TestClient, options: dict[str, object]
) -> None:
    response = _post(client, text=PERSIAN_TEXT, options=options)

    assert response.status_code == 400
    assert response.json()["error"] == INVALID_INPUT


def test_over_length_text_is_rejected_without_generating_a_file(
    client: TestClient,
) -> None:
    response = _post(client, text="ا" * 1001)

    assert response.status_code == 400
    assert response.json() == {
        "error": TEXT_TOO_LONG,
        "message": TEXT_TOO_LONG_MESSAGE,
    }


def test_length_limit_is_configurable_and_ignores_surrounding_whitespace(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "max_text_length", 5)

    accepted = _post(client, text="  " + "ا" * 5 + "  ")
    rejected = _post(client, text="ا" * 6)

    assert accepted.status_code == 200
    assert rejected.status_code == 400
    assert rejected.json()["error"] == TEXT_TOO_LONG


def test_invalid_input_never_calls_the_document_generator(
    client: TestClient,
) -> None:
    calls: list[tuple[str, ExportOptions]] = []

    class RecordingService:
        def render(self, *, text: str, options: ExportOptions) -> bytes:
            calls.append((text, options))
            return b"unused"

    app.dependency_overrides[get_docx_export_service] = lambda: RecordingService()
    try:
        _post(client, text="   ")
        _post(client, text="ا" * 1001)
    finally:
        app.dependency_overrides.pop(get_docx_export_service, None)

    assert calls == []


def test_errors_keep_the_documented_envelope(client: TestClient) -> None:
    responses = [
        _post(client, text=""),
        _post(client, text="ا" * 1001),
        _post(client, text=PERSIAN_TEXT, options={"font": "Arial"}),
        client.get(EXPORT_URL),
    ]

    for response in responses:
        assert response.status_code >= 400
        body = json.loads(response.text)
        assert sorted(body) == ["error", "message"]
