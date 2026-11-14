"""Word (``.docx``) export of corrected Persian text (`word-export-spec.md`).

`python-docx` exposes only part of what a correctly rendered Persian document
needs, so the formatting is written as explicit WordprocessingML here instead of
relying on the library's high-level properties. The rules, in the order they are
applied:

- **Section**: A4 portrait, the requested margins, and ``w:sectPr/w:bidi`` for a
  right-to-left page direction.
- **Document defaults and the Normal style**: ``w:rFonts w:cs``, ``w:szCs``,
  ``w:rtl``, ``w:lang w:bidi="fa-IR"``, and ``w:pPr/w:bidi``. A paragraph that is
  still empty (a blank line in the input) is laid out by its paragraph mark, so
  the mark carries the same Complex Script formatting.
- **Paragraph**: ``w:pPr/w:bidi`` and an explicit ``w:jc``. In a bidi paragraph
  Word mirrors the physical ``left``/``right`` values, which is why the visual
  right alignment is written as the logical ``start`` value — ``w:jc="right"``
  would render on the visual left. ``justify`` stays ``both`` and ``center``
  stays ``center``.
- **Run**: ``w:rFonts w:cs``, ``w:szCs``, and ``w:rtl``. ``run.font.size`` alone
  only sets ``w:sz`` and has no effect on Persian text.
- **Footer page numbers**: a Word ``PAGE`` field in a bidi footer paragraph,
  aligned to the requested visual position.
- **Page border**: ``w:pgBorders`` with a thin single line on every side.

The document is built in memory and returned as bytes; nothing is written to
disk and no provider (LLM) is involved.
"""

from __future__ import annotations

from collections.abc import Sequence
from io import BytesIO
from typing import Final

from docx import Document
from docx.document import Document as DocumentType
from docx.enum.section import WD_ORIENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.oxml.text.font import CT_RPr
from docx.section import Section
from docx.shared import Cm, Length
from docx.text.paragraph import Paragraph

from app.schemas import ExportOptions

# Public response metadata (`word-export-spec.md` → Response).
DOCX_MEDIA_TYPE: Final = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
EXPORT_FILENAME: Final = "virastar-export.docx"

# Every run and the document defaults carry this as the bidirectional language
# (``w:lang w:bidi``), which is the language Word uses for complex-script text.
BIDI_LANGUAGE: Final = "fa-IR"

# A4 portrait, in the units python-docx converts to twips.
A4_WIDTH: Final = Cm(21)
A4_HEIGHT: Final = Cm(29.7)

# `word-export-spec.md` → Margin mapping (A4).
MARGINS: Final[dict[str, Length]] = {
    "normal": Cm(2.5),
    "narrow": Cm(1.27),
    "wide": Cm(3.5),
}

# Visual alignment → `w:jc` value inside a bidi paragraph. `right` is the logical
# leading edge (`start`); `right`/`end` would be mirrored by Word and render on
# the visual left.
JUSTIFICATION: Final[dict[str, str]] = {
    "right": "start",
    "justify": "both",
    "center": "center",
}

# A thin single line: 4 eighths of a point, 24 pt away from the page edge.
PAGE_BORDER_SIZE = "4"
PAGE_BORDER_SPACE = "24"
PAGE_BORDER_COLOR = "auto"

# Element sequences required by ECMA-376. The schema is order-sensitive, and
# python-docx does not model the bidi, szCs, rtl, lang, or pgBorders elements, so
# they are inserted at the position the sequence prescribes.
_RPR_ORDER: Final = tuple(
    qn(tag)
    for tag in (
        "w:rStyle", "w:rFonts", "w:b", "w:bCs", "w:i", "w:iCs", "w:caps",
        "w:smallCaps", "w:strike", "w:dstrike", "w:outline", "w:shadow",
        "w:emboss", "w:imprint", "w:noProof", "w:snapToGrid", "w:vanish",
        "w:webHidden", "w:color", "w:spacing", "w:w", "w:kern", "w:position",
        "w:sz", "w:szCs", "w:highlight", "w:u", "w:effect", "w:bdr", "w:shd",
        "w:fitText", "w:vertAlign", "w:rtl", "w:cs", "w:em", "w:lang",
        "w:eastAsianLayout", "w:specVanish", "w:oMath",
    )
)

_PPR_ORDER: Final = tuple(
    qn(tag)
    for tag in (
        "w:pStyle", "w:keepNext", "w:keepLines", "w:pageBreakBefore",
        "w:framePr", "w:widowControl", "w:numPr", "w:suppressLineNumbers",
        "w:pBdr", "w:shd", "w:tabs", "w:suppressAutoHyphens", "w:kinsoku",
        "w:wordWrap", "w:overflowPunct", "w:topLinePunct", "w:autoSpaceDE",
        "w:autoSpaceDN", "w:bidi", "w:adjustRightInd", "w:snapToGrid",
        "w:spacing", "w:ind", "w:contextualSpacing", "w:mirrorIndents",
        "w:suppressOverlap", "w:jc", "w:textDirection", "w:textAlignment",
        "w:textboxTightWrap", "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr",
        "w:sectPr", "w:pPrChange",
    )
)

_SECTPR_ORDER: Final = tuple(
    qn(tag)
    for tag in (
        "w:headerReference", "w:footerReference", "w:footnotePr", "w:endnotePr",
        "w:type", "w:pgSz", "w:pgMar", "w:paperSrc", "w:pgBorders",
        "w:lnNumType", "w:pgNumType", "w:cols", "w:formProt", "w:vAlign",
        "w:noEndnote", "w:titlePg", "w:textDirection", "w:bidi", "w:rtlGutter",
        "w:docGrid", "w:printerSettings", "w:sectPrChange",
    )
)

# Font slots that would override the explicit ``w:ascii``/``w:hAnsi``/``w:cs``
# attributes when the document uses theme fonts.
# Both spellings occur in the wild for the complex-script slot; a theme
# attribute would take precedence over the explicit ``w:cs`` font.
_THEME_FONT_ATTRIBUTES = (
    "w:asciiTheme",
    "w:asciitheme",
    "w:hAnsiTheme",
    "w:hansitheme",
    "w:eastAsiaTheme",
    "w:eastasiatheme",
    "w:csTheme",
    "w:cstheme",
)


# --- Ordered OOXML helpers -------------------------------------------------- #


def _insert_in_order(parent, element, order: Sequence[str]) -> None:
    """Insert ``element`` into ``parent`` at its schema position in ``order``."""
    position = order.index(element.tag)

    for existing in parent:
        if existing.tag in order and order.index(existing.tag) > position:
            existing.addprevious(element)
            return

    parent.append(element)


def _get_or_add_child(parent, tag: str, order: Sequence[str] | None = None):
    """Return the ``tag`` child of ``parent``, creating it in schema order."""
    existing = parent.find(qn(tag))
    if existing is not None:
        return existing

    child = OxmlElement(tag)
    if order is None:
        parent.append(child)
    else:
        _insert_in_order(parent, child, order)

    return child


def _enable_toggle(parent, tag: str, order: Sequence[str]):
    """Add a boolean property element; presence alone means "on"."""
    element = _get_or_add_child(parent, tag, order)
    element.attrib.pop(qn("w:val"), None)
    return element


def _format_run_properties(rPr: CT_RPr, *, font: str, font_size_pt: int) -> None:
    """Apply the Complex Script formatting a Persian run needs.

    ``w:rFonts w:cs`` and ``w:szCs`` are mandatory: Word selects the complex
    script font slot and size for Persian characters, and ``w:sz`` alone is
    ignored for them. ``w:rtl`` makes the run right-to-left and ``w:lang
    w:bidi`` marks it as Persian.
    """
    rFonts = _get_or_add_child(rPr, "w:rFonts", _RPR_ORDER)
    for attribute in _THEME_FONT_ATTRIBUTES:
        rFonts.attrib.pop(qn(attribute), None)
    for attribute in ("w:ascii", "w:hAnsi", "w:cs"):
        rFonts.set(qn(attribute), font)

    half_points = str(font_size_pt * 2)
    for tag in ("w:sz", "w:szCs"):
        _get_or_add_child(rPr, tag, _RPR_ORDER).set(qn("w:val"), half_points)

    _enable_toggle(rPr, "w:rtl", _RPR_ORDER)
    _get_or_add_child(rPr, "w:lang", _RPR_ORDER).set(qn("w:bidi"), BIDI_LANGUAGE)


# --- Document structure ----------------------------------------------------- #


def _apply_document_defaults(
    document: DocumentType, *, font: str, font_size_pt: int
) -> None:
    """Set the RTL and Complex Script baseline for the whole document.

    The defaults cover every paragraph and run that inherits them — including
    an empty paragraph's mark — and the Normal style repeats them so a paragraph
    that explicitly references it is formatted the same way.
    """
    styles = document.styles.element
    doc_defaults = _get_or_add_child(styles, "w:docDefaults")
    rpr_default = _get_or_add_child(doc_defaults, "w:rPrDefault")
    ppr_default = _get_or_add_child(doc_defaults, "w:pPrDefault")

    _format_run_properties(
        _get_or_add_child(rpr_default, "w:rPr"),
        font=font,
        font_size_pt=font_size_pt,
    )
    _enable_toggle(_get_or_add_child(ppr_default, "w:pPr"), "w:bidi", _PPR_ORDER)

    _format_run_properties(
        document.styles["Normal"].element.get_or_add_rPr(),
        font=font,
        font_size_pt=font_size_pt,
    )


def _apply_paragraph_format(
    paragraph: Paragraph,
    *,
    justification: str,
    font: str,
    font_size_pt: int,
    line_spacing: float | None = None,
) -> None:
    """Make one paragraph right-to-left with an explicit, visual alignment.

    The paragraph mark itself is given the same run properties, so an empty
    paragraph still has the correct line height and direction.
    """
    pPr = paragraph._p.get_or_add_pPr()

    _enable_toggle(pPr, "w:bidi", _PPR_ORDER)
    _get_or_add_child(pPr, "w:jc", _PPR_ORDER).set(qn("w:val"), justification)

    if line_spacing is not None:
        spacing = _get_or_add_child(pPr, "w:spacing", _PPR_ORDER)
        spacing.set(qn("w:line"), str(round(line_spacing * 240)))
        spacing.set(qn("w:lineRule"), "auto")

    _format_run_properties(
        _get_or_add_child(pPr, "w:rPr", _PPR_ORDER),
        font=font,
        font_size_pt=font_size_pt,
    )


def _add_text_run(
    paragraph: Paragraph, text: str, *, font: str, font_size_pt: int
) -> None:
    """Append ``text`` as one formatted, right-to-left run."""
    run = paragraph.add_run(text)
    _format_run_properties(
        run._r.get_or_add_rPr(), font=font, font_size_pt=font_size_pt
    )


def _configure_section(section: Section, options: ExportOptions) -> None:
    """A4 portrait page with the requested margins, bidi, and optional border."""
    section.orientation = WD_ORIENT.PORTRAIT
    section.page_width = A4_WIDTH
    section.page_height = A4_HEIGHT

    margin = MARGINS[options.margins]
    section.top_margin = margin
    section.bottom_margin = margin
    section.left_margin = margin
    section.right_margin = margin

    sectPr = section._sectPr
    _enable_toggle(sectPr, "w:bidi", _SECTPR_ORDER)

    if not options.page_border:
        return

    pgBorders = _get_or_add_child(sectPr, "w:pgBorders", _SECTPR_ORDER)
    # The border is measured from the page edge, so it frames the whole page
    # regardless of the chosen margins.
    pgBorders.set(qn("w:offsetFrom"), "page")

    for edge in ("top", "left", "bottom", "right"):
        border = OxmlElement(f"w:{edge}")
        border.set(qn("w:val"), "single")
        border.set(qn("w:sz"), PAGE_BORDER_SIZE)
        border.set(qn("w:space"), PAGE_BORDER_SPACE)
        border.set(qn("w:color"), PAGE_BORDER_COLOR)
        pgBorders.append(border)


def _add_field_run(
    paragraph: Paragraph,
    *,
    font: str,
    font_size_pt: int,
    field_char: str | None = None,
    instruction: str | None = None,
    text: str | None = None,
) -> None:
    """Append one run of a Word field: begin/separate/end, instruction, result."""
    run = paragraph.add_run()
    _format_run_properties(
        run._r.get_or_add_rPr(), font=font, font_size_pt=font_size_pt
    )

    if field_char is not None:
        element = OxmlElement("w:fldChar")
        element.set(qn("w:fldCharType"), field_char)
        run._r.append(element)

    if instruction is not None:
        element = OxmlElement("w:instrText")
        element.set(qn("xml:space"), "preserve")
        element.text = instruction
        run._r.append(element)

    if text is not None:
        run.text = text


def _add_page_numbers(section: Section, options: ExportOptions) -> None:
    """Add a ``PAGE`` field to the footer at the requested visual position.

    The footer paragraph is bidi like the body, so the visual right position is
    again the logical ``start`` edge.
    """
    section.footer.is_linked_to_previous = False
    paragraph = section.footer.paragraphs[0]

    _apply_paragraph_format(
        paragraph,
        justification=JUSTIFICATION[options.page_number_position],
        font=options.font,
        font_size_pt=options.font_size,
    )

    # A complex field is four pieces: begin, instruction, separator, and the
    # cached result between separator and end. Word recalculates the field and
    # readers that do not evaluate fields still display the cached number.
    _add_field_run(
        paragraph, font=options.font, font_size_pt=options.font_size, field_char="begin"
    )
    _add_field_run(
        paragraph,
        font=options.font,
        font_size_pt=options.font_size,
        instruction=" PAGE ",
    )
    _add_field_run(
        paragraph,
        font=options.font,
        font_size_pt=options.font_size,
        field_char="separate",
    )
    _add_field_run(
        paragraph, font=options.font, font_size_pt=options.font_size, text="1"
    )
    _add_field_run(
        paragraph, font=options.font, font_size_pt=options.font_size, field_char="end"
    )


def _split_paragraphs(text: str) -> list[str]:
    """Split the input on ``\\n`` after normalizing Windows line endings."""
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


class DocxExportService:
    """Builds the downloadable Word document from corrected text."""

    def render(self, *, text: str, options: ExportOptions) -> bytes:
        """Return the generated ``.docx`` file as bytes.

        ``text`` is already validated and non-empty. Each line of it becomes one
        right-to-left paragraph; a blank line becomes an empty paragraph.
        """
        document = Document()
        _apply_document_defaults(
            document, font=options.font, font_size_pt=options.font_size
        )

        section = document.sections[0]
        _configure_section(section, options)

        for line in _split_paragraphs(text):
            paragraph = document.add_paragraph()
            _apply_paragraph_format(
                paragraph,
                justification=JUSTIFICATION[options.alignment],
                font=options.font,
                font_size_pt=options.font_size,
                line_spacing=options.line_spacing,
            )
            if line:
                _add_text_run(
                    paragraph,
                    line,
                    font=options.font,
                    font_size_pt=options.font_size,
                )

        if options.page_numbers:
            _add_page_numbers(section, options)

        buffer = BytesIO()
        document.save(buffer)
        return buffer.getvalue()


def get_docx_export_service() -> DocxExportService:
    """FastAPI dependency returning the document generator."""
    return DocxExportService()
