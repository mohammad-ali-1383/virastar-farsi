"""Regression tests for the Persian proofreading prompt.

The prompt is a specification artifact: it is the instruction the LLM receives
and the only part of the correction behaviour that is under our control. These
tests are deterministic and make no provider call — they pin the text sent to the
provider, keep it synchronized with ``context/correction-prompt.md``, and require
a documented rule for every Persian case we care about.

The cases below are the reference set for the manual quality check
(`testing.md` -> Correction Quality, `context/correction-prompt.md`): a live
model must reproduce them, which only a real (billable) API call can show.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.services.prompt import PERSIAN_PROOFREADING_SYSTEM_PROMPT

PROMPT = PERSIAN_PROOFREADING_SYSTEM_PROMPT
LOWERED = PROMPT.lower()

# ``backend/tests/test_correction_prompt.py`` -> repository root -> context/.
SPEC_PATH = Path(__file__).resolve().parents[2] / "context" / "correction-prompt.md"

SPEC_SYSTEM_PROMPT_HEADING = "## System Prompt"
SPEC_IMPLEMENTATION_HEADING = "## Implementation Rules"

# Zero-width non-joiner (Persian half-space). Persian words that need a
# half-space are built from this constant so the test never depends on how an
# editor or paste operation stores the character.
ZWNJ = "\u200c"

MI_KHAM = f"می{ZWNJ}خوام"  # colloquial "I want" — must survive unchanged
MI_KHAHAM = f"می{ZWNJ}خواهم"  # formal "I want" — not the target of a rewrite
MI_YAY = f"می{ZWNJ}ای"  # colloquial "you come"
MI_RAM = f"می{ZWNJ}رم"  # colloquial "I go"
MI_RI = f"می{ZWNJ}ری"  # colloquial "you go"
KHANE_HA = f"خانه{ZWNJ}ها"  # "houses"
NEMI_TAVANAM = f"نمی{ZWNJ}توانم"  # "I cannot"
NIM_FASELE = f"نیم{ZWNJ}فاصله"  # "half-space"
HICH_KAS_JOINED = f"هیچ{ZWNJ}کس"  # the joined variant of "nobody"

# The sample named in the task: three lines, each with a different kind of error.
GOLDEN_INPUT = f"سلا\n{MI_KHAM} برم شمال ایران\nمیایبریم"
GOLDEN_EXPECTED = f"سلام، {MI_KHAM} برم شمال ایران. {MI_YAY} بریم؟"

# Each correction in the golden sample, the text it must affect, and the prompt
# rule that authorises it.
GOLDEN_RULE_COVERAGE = [
    ("context-aware typo correction", "سلا", "سلام", "context"),
    ("word-boundary correction", "میایبریم", f"{MI_YAY} بریم", "word boundaries"),
    # Register preservation: the colloquial form survives the correction.
    ("colloquial register preservation", MI_KHAM, MI_KHAM, "colloquial"),
]

# The Persian words and punctuation the prompt is allowed to contain: language
# examples only, never Persian instructions.
ALLOWED_PERSIAN = (
    set("«»،؟") | set("سلا سلام") | set(MI_KHAM + MI_KHAHAM + NIM_FASELE) | {ZWNJ}
)


@dataclass(frozen=True)
class CorrectionCase:
    """A documented Persian case, its expected result, and the governing rule.

    ``expected == text`` means the prompt requires the text to be returned
    unchanged; any other ``expected`` is a correction the prompt must authorise.
    """

    name: str
    text: str
    expected: str
    prompt_rule: str
    rationale: str


CORRECTION_CASES = (
    CorrectionCase(
        name="missing-question-mark",
        text=f"کجا {MI_RI}",
        expected=f"کجا {MI_RI}؟",
        prompt_rule="question marks",
        rationale="A question with no punctuation at all still needs a question mark.",
    ),
    CorrectionCase(
        name="half-spaces-and-word-boundaries",
        text="من می خواهم به خانه ها بروم",
        expected=f"من {MI_KHAHAM} به {KHANE_HA} بروم",
        prompt_rule="half-space",
        rationale=f"{MI_KHAHAM} and {KHANE_HA} take a half-space, not a word space.",
    ),
    CorrectionCase(
        name="spacing-before-punctuation",
        text="سلام ، حالت چطور است ؟",
        expected="سلام، حالت چطور است؟",
        prompt_rule="spacing",
        rationale="A space before Persian punctuation is a spacing error.",
    ),
    CorrectionCase(
        name="joined-words",
        text="میایبریم",
        expected=f"{MI_YAY} بریم",
        prompt_rule="word boundaries",
        rationale="Two collapsed words must be separated; this is the golden sample's last line.",
    ),
    CorrectionCase(
        name="contextual-typo",
        text="سلا، حالت خوبه؟",
        expected="سلام، حالت خوبه؟",
        prompt_rule="context",
        rationale="The greeting is the only reading that fits the context.",
    ),
)

PRESERVATION_CASES = (
    CorrectionCase(
        name="already-correct-text",
        text="این متن از پیش درست است.",
        expected="این متن از پیش درست است.",
        prompt_rule="unchanged",
        rationale="Text with no error must come back unchanged.",
    ),
    CorrectionCase(
        name="formal-register-kept",
        text=f"من {NEMI_TAVANAM} در این جلسه شرکت کنم.",
        expected=f"من {NEMI_TAVANAM} در این جلسه شرکت کنم.",
        prompt_rule="formal",
        rationale="Correct formal Persian is not rewritten for style.",
    ),
    CorrectionCase(
        name="colloquial-register-kept",
        text=f"دیروز رفتم بازار، امروز {MI_KHAM} برم خونه.",
        expected=f"دیروز رفتم بازار، امروز {MI_KHAM} برم خونه.",
        prompt_rule="colloquial",
        rationale=f"Colloquial forms such as {MI_KHAM} are not errors and must not be formalized.",
    ),
    CorrectionCase(
        name="ambiguous-word-separation",
        text="هیچ کس جواب نداد.",
        expected="هیچ کس جواب نداد.",
        prompt_rule="ambiguous",
        rationale=f"Both «هیچ کس» and «{HICH_KAS_JOINED}» are standard; leave it rather than guess.",
    ),
    CorrectionCase(
        name="ambiguous-typo-not-guessed",
        text=f"{MI_RAM} خونه دوستم.",
        expected=f"{MI_RAM} خونه دوستم.",
        prompt_rule="ambiguous",
        rationale="Valid colloquial wording is not a typo; guessing a rewrite is what the prompt forbids.",
    ),
)

ALL_CASES = CORRECTION_CASES + PRESERVATION_CASES

HALF_SPACE_CASE = next(case for case in CORRECTION_CASES if case.name == "half-spaces-and-word-boundaries")
QUESTION_CASE = next(case for case in CORRECTION_CASES if case.name == "missing-question-mark")


@pytest.fixture(scope="module")
def spec_system_prompt() -> str:
    """The System Prompt section of the specification document, normalized."""
    document = SPEC_PATH.read_text(encoding="utf-8")

    assert SPEC_SYSTEM_PROMPT_HEADING in document
    assert SPEC_IMPLEMENTATION_HEADING in document

    start = document.index(SPEC_SYSTEM_PROMPT_HEADING) + len(SPEC_SYSTEM_PROMPT_HEADING)
    end = document.index(SPEC_IMPLEMENTATION_HEADING)

    return document[start:end].strip().replace("\r\n", "\n")


# --- The prompt text itself -------------------------------------------------


def test_prompt_opens_with_the_documented_role() -> None:
    assert PROMPT.startswith("You are a professional Persian-language proofreader.")


def test_prompt_keeps_six_numbered_rules() -> None:
    # The six documented instruction groups must survive future edits.
    missing = [f"{number}." for number in range(1, 7) if f"\n{number}. " not in PROMPT]

    assert missing == []


def test_prompt_matches_the_specification_document(spec_system_prompt: str) -> None:
    # Single source of truth: the constant and `context/correction-prompt.md`
    # must not drift apart.
    assert spec_system_prompt == PROMPT


def test_prompt_is_english_and_persian_appears_only_in_examples() -> None:
    unexpected = {character for character in PROMPT if ord(character) > 127} - ALLOWED_PERSIAN

    assert unexpected == set()


def test_prompt_documents_the_context_typo_example() -> None:
    assert "«سلا»" in PROMPT
    assert "«سلام»" in PROMPT


def test_prompt_documents_the_register_example() -> None:
    # The colloquial form is kept; the formal one is explicitly not a rewrite target.
    assert f"«{MI_KHAM}»" in PROMPT
    assert f"«{MI_KHAHAM}»" in PROMPT
    assert "never convert colloquial forms to formal ones" in LOWERED


@pytest.mark.parametrize(
    "requirement",
    [
        "context",
        "ambiguous",
        "colloquial",
        "formal",
        "spelling",
        "grammar",
        "word boundaries",
        "half-space",
        "zwnj",
        "spacing",
        "punctuation",
        "typography",
        "comma",
        "period",
        "question mark",
        "no explicit punctuation",
        "invent meaning",
        "add information",
        "summarize",
        "least invasive",
        "return only the corrected persian text",
        "line breaks",
        "quotation marks",
        "unchanged",
    ],
)
def test_prompt_covers_each_required_instruction(requirement: str) -> None:
    assert requirement in LOWERED


def test_prompt_requires_questions_to_be_recognized_without_punctuation() -> None:
    assert "recognize questions even when the original carries no explicit punctuation" in LOWERED


# --- The named golden sample ------------------------------------------------


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


@pytest.mark.parametrize(
    ("description", "before", "after", "rule"),
    GOLDEN_RULE_COVERAGE,
    ids=[entry[0] for entry in GOLDEN_RULE_COVERAGE],
)
def test_golden_sample_is_covered_by_a_prompt_rule(
    description: str, before: str, after: str, rule: str
) -> None:
    assert before in GOLDEN_INPUT
    assert after in GOLDEN_EXPECTED
    assert rule in LOWERED


# --- The documented case set ------------------------------------------------


def test_case_names_are_unique() -> None:
    names = [case.name for case in ALL_CASES]

    assert len(names) == len(set(names))


@pytest.mark.parametrize("case", ALL_CASES, ids=[case.name for case in ALL_CASES])
def test_every_case_is_justified_by_a_prompt_rule(case: CorrectionCase) -> None:
    # Each expectation must map to something the prompt actually instructs.
    assert case.prompt_rule.lower() in LOWERED
    assert case.rationale.strip()
    assert case.text.strip()


@pytest.mark.parametrize("case", CORRECTION_CASES, ids=[case.name for case in CORRECTION_CASES])
def test_documented_corrections_change_the_text(case: CorrectionCase) -> None:
    assert case.expected != case.text


@pytest.mark.parametrize(
    "case", PRESERVATION_CASES, ids=[case.name for case in PRESERVATION_CASES]
)
def test_documented_preservation_cases_return_the_text_unchanged(case: CorrectionCase) -> None:
    assert case.expected == case.text


def test_half_space_case_uses_a_real_half_space() -> None:
    assert ZWNJ not in HALF_SPACE_CASE.text
    assert ZWNJ in HALF_SPACE_CASE.expected
    assert ZWNJ in PROMPT


def test_question_case_adds_a_question_mark() -> None:
    assert "؟" not in QUESTION_CASE.text
    assert QUESTION_CASE.expected.endswith("؟")
