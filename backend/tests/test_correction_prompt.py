"""Regression tests for the two Persian proofreading prompts.

The prompts are specification artifacts: they are the instructions the LLM
receives and the only part of the correction behaviour that is under our control.
Each style has one constant and one document, and these tests keep them in sync:

- ``preserve`` -> ``PRESERVE_SYSTEM_PROMPT`` / ``context/correction-prompt.md``
- ``formal``   -> ``FORMAL_SYSTEM_PROMPT``   / ``context/correction-prompt-formal.md``

They are deterministic and make no provider call — they pin the text sent to the
provider and require a documented rule for every Persian case we care about. A
live model reproducing these cases needs a real (billable) API call, which is the
manual quality check in `testing.md` -> Correction Quality.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.services.prompt import (
    FORMAL_SYSTEM_PROMPT,
    PRESERVE_SYSTEM_PROMPT,
    SYSTEM_PROMPTS,
    get_system_prompt,
)

# Zero-width non-joiner (Persian half-space). Persian words that need a
# half-space are built from this constant so the test never depends on how an
# editor or paste operation stores the character.
ZWNJ = "\u200c"

MI_KHAM = f"می{ZWNJ}خوام"  # colloquial "I want" — preserved by "preserve"
MI_KHAHAM = f"می{ZWNJ}خواهم"  # formal "I want" — required by "formal"
MI_YAY = f"می{ZWNJ}ای"  # colloquial "you come"
MI_RAM = f"می{ZWNJ}رم"  # colloquial "I go"
MI_ROUM = f"می{ZWNJ}روم"  # formal "I go"
KHANE_HA = f"خانه{ZWNJ}ها"  # "houses"

# ``backend/tests/test_correction_prompt.py`` -> repository root -> context/.
CONTEXT_DIR = Path(__file__).resolve().parents[2] / "context"

SPEC_SYSTEM_PROMPT_HEADING = "## System Prompt"
SPEC_IMPLEMENTATION_HEADING = "## Implementation"

# style -> (constant, specification document, the rule set that must be there).
PROMPTS = {
    "preserve": (PRESERVE_SYSTEM_PROMPT, "correction-prompt.md"),
    "formal": (FORMAL_SYSTEM_PROMPT, "correction-prompt-formal.md"),
}

STYLE_IDS = list(PROMPTS)


def _system_prompt_section(document: str) -> str:
    """The System Prompt section of a specification document, normalized."""
    if SPEC_SYSTEM_PROMPT_HEADING not in document:
        raise AssertionError("the specification has no System Prompt section")
    if SPEC_IMPLEMENTATION_HEADING not in document:
        raise AssertionError("the specification has no Implementation section")

    start = document.index(SPEC_SYSTEM_PROMPT_HEADING) + len(SPEC_SYSTEM_PROMPT_HEADING)
    end = document.index(SPEC_IMPLEMENTATION_HEADING)

    return document[start:end].strip().replace("\r\n", "\n")


@dataclass(frozen=True)
class CorrectionCase:
    """A documented Persian case, its expected result, and the governing rule.

    ``expected == text`` means the prompt requires the text to be returned
    unchanged; any other ``expected`` is a correction the prompt must authorize.
    ``style`` is the prompt whose rules must cover the case.
    """

    name: str
    style: str
    text: str
    expected: str
    prompt_rule: str
    rationale: str


# Corrections both styles must make: a spelling/context fix, half-spaces, joined
# words, spacing before punctuation, and a recovered question mark.
SHARED_CASES = (
    CorrectionCase(
        name="missing-question-mark",
        style="preserve",
        text=f"کجا {MI_YAY}",
        expected=f"کجا {MI_YAY}؟",
        prompt_rule="question mark",
        rationale="A question with no punctuation at all still needs a question mark.",
    ),
    CorrectionCase(
        name="half-spaces-and-word-boundaries",
        style="preserve",
        text="من می خواهم به خانه ها بروم",
        expected=f"من {MI_KHAHAM} به {KHANE_HA} بروم",
        prompt_rule="half-space",
        rationale=f"{MI_KHAHAM} and {KHANE_HA} take a half-space, not a word space.",
    ),
    CorrectionCase(
        name="spacing-before-punctuation",
        style="preserve",
        text="سلام ، حالت چطور است ؟",
        expected="سلام، حالت چطور است؟",
        prompt_rule="spacing",
        rationale="A space before Persian punctuation is a spacing error.",
    ),
    CorrectionCase(
        name="joined-words",
        style="preserve",
        text="میایبریم",
        expected=f"{MI_YAY} بریم",
        prompt_rule="word boundaries",
        rationale="Two collapsed words must be separated; this is the golden sample's last line.",
    ),
    CorrectionCase(
        name="contextual-typo",
        style="preserve",
        text="سلا، حالت خوبه؟",
        expected="سلام، حالت خوبه؟",
        prompt_rule="context",
        rationale="The greeting is the only reading that fits the context.",
    ),
    CorrectionCase(
        name="formal-question-mark",
        style="formal",
        text=f"کجا {MI_YAY}",
        expected=f"کجا {MI_YAY}؟",
        prompt_rule="question mark",
        rationale="The formal prompt requires complete punctuation as well.",
    ),
    CorrectionCase(
        name="formal-half-spaces",
        style="formal",
        text="من می خواهم به خانه ها بروم",
        expected=f"من {MI_KHAHAM} به {KHANE_HA} بروم",
        prompt_rule="half-space",
        rationale=f"The formal prompt also fixes {MI_KHAHAM} and {KHANE_HA}.",
    ),
    CorrectionCase(
        name="formal-contextual-typo",
        style="formal",
        text="سلا، حالت خوبه؟",
        expected="سلام، حالت خوبه؟",
        prompt_rule="spelling",
        rationale="Spelling is corrected before the register is adjusted.",
    ),
)

# Behaviour that belongs to the "preserve" style only: the writer's wording and
# register survive the correction.
PRESERVATION_CASES = (
    CorrectionCase(
        name="already-correct-text",
        style="preserve",
        text="این متن از پیش درست است.",
        expected="این متن از پیش درست است.",
        prompt_rule="unchanged",
        rationale="Text with no error must come back unchanged.",
    ),
    CorrectionCase(
        name="formal-register-kept",
        style="preserve",
        text=f"من {MI_KHAHAM} در این جلسه شرکت کنم.",
        expected=f"من {MI_KHAHAM} در این جلسه شرکت کنم.",
        prompt_rule="formal",
        rationale="Correct formal Persian is not rewritten for style.",
    ),
    CorrectionCase(
        name="colloquial-register-kept",
        style="preserve",
        text=f"دیروز رفتم بازار، امروز {MI_KHAM} برم خونه.",
        expected=f"دیروز رفتم بازار، امروز {MI_KHAM} برم خونه.",
        prompt_rule="colloquial",
        rationale=f"Colloquial forms such as {MI_KHAM} are not errors and must not be formalized.",
    ),
    CorrectionCase(
        name="ambiguous-word-separation",
        style="preserve",
        text="هیچ کس جواب نداد.",
        expected="هیچ کس جواب نداد.",
        prompt_rule="ambiguous",
        rationale=f"Both «هیچ کس» and «هیچ{ZWNJ}کس» are standard; leave it rather than guess.",
    ),
    CorrectionCase(
        name="ambiguous-typo-not-guessed",
        style="preserve",
        text=f"{MI_RAM} خونه دوستم.",
        expected=f"{MI_RAM} خونه دوستم.",
        prompt_rule="ambiguous",
        rationale="Valid colloquial wording is not a typo; guessing a rewrite is what the prompt forbids.",
    ),
)

# The core behaviour of the "formal" style: colloquial wording becomes formal.
FORMAL_CONVERSION_CASES = (
    CorrectionCase(
        name="colloquial-to-formal-verb",
        style="formal",
        text=f"امروز {MI_KHAM} برم بازار.",
        expected=f"امروز {MI_KHAHAM} بروم بازار.",
        prompt_rule="formal",
        rationale=f"The formal style converts {MI_KHAM} to {MI_KHAHAM}.",
    ),
    CorrectionCase(
        name="colloquial-go-verb",
        style="formal",
        text="دیروز رفتم بازار، فردا می رم.",
        expected=f"دیروز رفتم بازار، فردا {MI_ROUM}.",
        prompt_rule=MI_RAM,
        rationale=f"The formal style converts «{MI_RAM}» to «{MI_ROUM}».",
    ),
    CorrectionCase(
        name="colloquial-particles",
        style="formal",
        text="اگه واسه من وقت داری، بگو.",
        expected="اگر برای من وقت دارید، بفرمایید.",
        prompt_rule="اگر",
        rationale="«اگه» and «واسه» are colloquial and become «اگر» and «برای».",
    ),
)

ALL_CASES = SHARED_CASES + PRESERVATION_CASES + FORMAL_CONVERSION_CASES

# The documented Persian examples of the "preserve" prompt, as before/after
# pairs. They are pinned so the prompt cannot silently lose its examples.
PRESERVE_EXAMPLES = (
    ("غشنگ", "قشنگ"),
    ("میخوام", MI_KHAM),
    ("سلا", "سلام"),
    ("ک برم", "که برم"),
    ("می خوام", MI_KHAM),
    ("نمی دونم", f"نمی{ZWNJ}دونم"),
    ("خانه ها", KHANE_HA),
    ("کتاب های", f"کتاب{ZWNJ}های"),
)

# The documented colloquial -> formal conversions of the "formal" prompt.
FORMAL_EXAMPLES = (
    (MI_KHAM, MI_KHAHAM),
    (MI_RAM, MI_ROUM),
    ("اگه", "اگر"),
    ("واسه", "برای"),
    (f"نمی{ZWNJ}دونم", f"نمی{ZWNJ}دانم"),
)

# The sample named in `correction-prompt.md`: three lines, each a different error.
GOLDEN_INPUT = f"سلا\n{MI_KHAM} برم شمال ایران\nمیایبریم"
GOLDEN_EXPECTED = f"سلام، {MI_KHAM} برم شمال ایران. {MI_YAY} بریم؟"


@pytest.fixture(scope="module", params=STYLE_IDS)
def styled_prompt(request: pytest.FixtureRequest) -> tuple[str, str, str]:
    """(style, prompt constant, specification document) for one style."""
    style = str(request.param)
    prompt, document_name = PROMPTS[style]
    document = (CONTEXT_DIR / document_name).read_text(encoding="utf-8")

    return style, prompt, document


# --- Prompt text and specification sync -------------------------------------


def test_each_documented_style_has_a_prompt() -> None:
    assert set(SYSTEM_PROMPTS) == {"preserve", "formal"}
    for style, (prompt, _) in PROMPTS.items():
        assert get_system_prompt(style) == prompt


def test_the_two_prompts_are_different() -> None:
    assert PRESERVE_SYSTEM_PROMPT != FORMAL_SYSTEM_PROMPT


def test_the_prompt_matches_its_specification_document(
    styled_prompt: tuple[str, str, str],
) -> None:
    style, prompt, document = styled_prompt

    # Single source of truth: the constant and its document must not drift apart.
    assert _system_prompt_section(document) == prompt, style


def test_the_prompt_opens_with_the_documented_role(
    styled_prompt: tuple[str, str, str],
) -> None:
    _, prompt, _ = styled_prompt

    assert prompt.startswith("You are a professional Persian-language proofreader")


def test_the_prompt_returns_only_corrected_persian_text(
    styled_prompt: tuple[str, str, str],
) -> None:
    _, prompt, _ = styled_prompt
    lowered = prompt.lower()

    assert "return **only** the corrected" in lowered
    assert "do not add" in lowered
    assert "quotation marks" in lowered


# Instructions every style must keep, plus the ones that define each style
# (register preservation vs. conversion to a formal register).
REQUIRED_INSTRUCTIONS: dict[str, tuple[str, ...]] = {
    "preserve": (
        "spelling",
        "context",
        "word boundaries",
        "half-space",
        "spacing",
        "punctuation",
        "typography",
        "question mark",
        "invent",
        "meaning",
        "line breaks",
        "colloquial",
    ),
    "formal": (
        "spelling",
        "half-space",
        "spacing",
        "punctuation",
        "typography",
        "question mark",
        "invent",
        "meaning",
        "line breaks",
        "colloquial",
        "formal",
    ),
}


@pytest.mark.parametrize(
    ("style", "requirement"),
    [
        (style, requirement)
        for style, requirements in REQUIRED_INSTRUCTIONS.items()
        for requirement in requirements
    ],
)
def test_the_prompt_covers_each_required_instruction(
    style: str, requirement: str
) -> None:
    assert requirement in get_system_prompt(style).lower()


def test_the_preserve_prompt_forbids_formalizing_colloquial_text() -> None:
    lowered = PRESERVE_SYSTEM_PROMPT.lower()

    assert "do **not** convert colloquial language into formal language" in lowered
    assert f"keep «{MI_KHAM}»" in lowered
    assert "formal register" not in lowered


def test_the_formal_prompt_requires_a_formal_register() -> None:
    lowered = FORMAL_SYSTEM_PROMPT.lower()

    assert "convert to formal register" in lowered
    assert "formal, polite, and administrative style" in lowered
    # The formal style deliberately does not keep colloquial forms.
    assert "do **not** convert colloquial language" not in lowered


def test_the_preserve_prompt_documents_its_persian_examples() -> None:
    missing = [
        f"«{before}» → «{after}»"
        for before, after in PRESERVE_EXAMPLES
        if f"«{before}» → «{after}»" not in PRESERVE_SYSTEM_PROMPT
    ]

    assert missing == []


def test_the_formal_prompt_documents_its_colloquial_to_formal_examples() -> None:
    missing = [
        f"«{before}» → «{after}»"
        for before, after in FORMAL_EXAMPLES
        if f"«{before}» → «{after}»" not in FORMAL_SYSTEM_PROMPT
    ]

    assert missing == []


def test_both_prompts_use_real_half_spaces() -> None:
    for prompt, _ in PROMPTS.values():
        assert ZWNJ in prompt


# --- The named golden sample (the "preserve" style) -------------------------


def test_golden_sample_is_well_formed() -> None:
    assert GOLDEN_INPUT.count("\n") == 2
    assert len(GOLDEN_INPUT.splitlines()) == 3
    assert GOLDEN_EXPECTED != GOLDEN_INPUT


def test_golden_sample_applies_the_contextual_typo_correction() -> None:
    # Compared as whole words: «سلام» contains «سلا» as a substring.
    assert GOLDEN_INPUT.splitlines()[0] == "سلا"
    assert GOLDEN_EXPECTED.split("،")[0] == "سلام"


def test_golden_sample_preserves_the_colloquial_register() -> None:
    assert MI_KHAM in GOLDEN_EXPECTED
    assert MI_KHAHAM not in GOLDEN_EXPECTED


def test_golden_sample_separates_the_joined_words() -> None:
    assert "میایبریم" not in GOLDEN_EXPECTED
    assert f"{MI_YAY} بریم" in GOLDEN_EXPECTED


def test_golden_sample_recovers_the_missing_punctuation() -> None:
    assert not any(mark in GOLDEN_INPUT for mark in "،.؟")
    assert GOLDEN_EXPECTED.endswith("؟")
    assert GOLDEN_EXPECTED.count("،") == 1
    assert GOLDEN_EXPECTED.count(".") == 1


# --- The documented case set ------------------------------------------------


def test_case_names_are_unique() -> None:
    names = [case.name for case in ALL_CASES]

    assert len(names) == len(set(names))


@pytest.mark.parametrize("case", ALL_CASES, ids=[case.name for case in ALL_CASES])
def test_every_case_is_justified_by_its_prompt(case: CorrectionCase) -> None:
    # Each expectation must map to something its style's prompt instructs.
    assert case.style in PROMPTS
    assert case.prompt_rule.lower() in get_system_prompt(case.style).lower()
    assert case.rationale.strip()
    assert case.text.strip()


@pytest.mark.parametrize(
    "case",
    [case for case in ALL_CASES if case.expected != case.text],
    ids=[case.name for case in ALL_CASES if case.expected != case.text],
)
def test_documented_corrections_change_the_text(case: CorrectionCase) -> None:
    assert case.expected != case.text


@pytest.mark.parametrize(
    "case", PRESERVATION_CASES, ids=[case.name for case in PRESERVATION_CASES]
)
def test_documented_preservation_cases_return_the_text_unchanged(
    case: CorrectionCase,
) -> None:
    assert case.expected == case.text


def test_the_colloquial_case_is_a_half_space_correction() -> None:
    case = next(case for case in SHARED_CASES if case.name == "half-spaces-and-word-boundaries")

    assert ZWNJ not in case.text
    assert ZWNJ in case.expected


def test_the_question_case_adds_a_question_mark() -> None:
    case = next(case for case in SHARED_CASES if case.name == "missing-question-mark")

    assert "؟" not in case.text
    assert case.expected.endswith("؟")
