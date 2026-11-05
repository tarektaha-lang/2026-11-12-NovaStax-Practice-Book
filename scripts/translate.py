#!/usr/bin/env python3
"""Translate an English practice book into its Arabic sibling.

Reads the applied English specs, labels, budgets and accepted exercises, and
the English figure library. Writes the Arabic `03_` / `04_` / `05_` / `06_` /
`08_` / `09_` / `11_` tree (and a `10_` mirror of adopted figures). Arabic
section PDFs are terminology only. Mathematics follows the AR XML `notation`.
Figures start from English TikZ: printed text is translated; arabic notation
may reverse direction. Pictures are recompiled under the Arabic profile.

    python3 -B scripts/translate.py mathematics_en_eg08_t1
    python3 -B scripts/translate.py mathematics_en_eg08_t1 -s 1.1
    python3 -B scripts/translate.py mathematics_en_eg08_t1 --update -s 1.1
    python3 -B scripts/translate.py mathematics_en_eg08_t1 -s 1.1 --retranslate-on-prompt-change
    python3 -B scripts/translate.py mathematics_en_eg08_t1 -s 1.1 --rereview

Exercises are translated one model call per objective. Maths and prose numbers are
placeholder-protected. Counted-noun agreement is checked in code before review.
A rerun of an unchanged accepted section makes no model calls. `08_` and `09_` are never deleted.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import random
import re
import sys
import threading
import time
from contextlib import nullcontext
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    from google import genai
    from google.genai import types
    from pydantic import BaseModel, ConfigDict, Field
    from pypdf import PdfReader

    from src.api_logger import (
        ApiLogger,
        genai_answer_text,
        genai_request_files,
        genai_response_files,
        genai_usage,
        safe_log,
    )
    from src.book_map import (
        GLOSSARY_MERGE_TOKEN_LIMIT,
        MAP_SCHEMA_VERSION,
        book_map_from_glossary_reply,
        english_snippets,
        glossary_candidate_rows,
        glossary_fp,
        glossary_input_payload,
        glossary_input_tokens,
        glossary_merge_key,
        glossary_merge_problems,
        join_split_fragments,
        section_inventory,
        split_glossary_rows,
        validate_terminology_fragment,
    )
    from src.prompting import Profile, Prompt, assemble, read_part, relative, stage_dir
    from src.print_digits import (
        TIKZ_NODE,
        normalize_tikz_print_digits,
    )
    from src.protect import (
        Slots,
        iter_spec_strings,
        latin_function_output_problems,
        protect_group,
        protect_spec,
        restore_group,
        restore_spec,
        set_spec_strings,
        token_problems,
    )
    from src.twin import (
        ACCEPTED_STAGE,
        BUDGETS_STAGE,
        EXERCISES_STAGE,
        LABELS_STAGE,
        SPECS_STAGE,
        applied_book_path,
        matching_applied_sections,
        pair_books,
        require_english_id,
        require_english_review,
    )
    import extract
    from extract import FIGURE_DATA_STAGE
    from draw import FIGURES_STAGE, adopt_tikz, figure_key, figure_rows, library, picture_only, unclash_adopted
    from labels import label_problems
    from new_questions import source_artifact_problems
    from questions_review import recount
    from sources import (
        OUTPUT_DIR,
        PROJECT_ROOT,
        SECTIONS_STAGE,
        book_metadata,
        book_spans,
        number_pad_width,
        section_spans,
        split_book,
    )
    from specs import (
        Progress,
        delete_uploads,
        one_line,
        section_pdfs,
        since,
        upload_pdfs,
    )
    from src.arabic_verse import poem_shatrs
    from src.latex import CHOICE_LETTERS
    from src.geometry_arrows import (
        canonicalize_document,
        canonicalize_tree,
        section_is_vector_lesson,
    )
    from src.translation_quality import (
        atomic_write_json,
        json_fingerprint,
        merge_group as strict_merge_group,
        merge_spec as strict_merge_spec,
        repair_stored_controls,
        SPEC_LIST_FIELDS,
        spec_problems,
        terminology_context,
        text_problems,
        translatable_group,
        translatable_spec,
        validate_document,
        validate_tikz,
        tikz_problems,
        bump_lengthmark_offsets,
    )
except ModuleNotFoundError as error:  # pragma: no cover
    sys.exit(f"missing dependency ({error.name}): pip install -r requirements.txt")

PROMPT_STAGE = "translate"
TASK_SLOT = "{task}"
PROFILE_SLOT = "{profile}"
DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_THINKING = "medium"
THINKING_LEVELS = ("minimal", "low", "medium", "high")
API_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
MAX_ATTEMPTS = 4
UNTRANSLATABLE_SOURCE = "untranslatable English source"
DEFAULT_SWEEPS = 2
DEFAULT_WORKERS = 25
MAX_WORKERS = 100
RATE_LIMIT_WAITS = 8
RATE_LIMIT_PAUSE = 5.0
TRANSIENT_WAITS = 6
TRANSIENT_PAUSE = 10.0
REVIEW_BATCH_CHARS = 36_000
MAX_REVIEW_ROUNDS = 3
MAX_LENGTHMARK_NUDGES = 4
MAX_SECTION_WORKERS = 4
REPEATED_SEMANTIC_ERROR_LIMIT = 2
# Per-call HTTP timeout. Successful group calls here are ~15s (p99 ~34s);
# spec calls can reach ~80s. Without this, a hung generate_content parks the
# whole section because groups run one after another on that worker.
CALL_TIMEOUT_MS = 120_000
TERMINOLOGY_STEP = "terminology_extract"
GLOSSARY_MERGE_STEP = "glossary_merge"
GLOSSARY_MERGE_TIMEOUT_MS = 600_000
GLOSSARY_MERGE_MAX_OUTPUT_TOKENS = 65_536
GLOSSARY_DROPPED_FILE = "glossary_dropped.json"
TERMINOLOGY_TIMEOUT_MS = 600_000
TERMINOLOGY_MAX_OUTPUT_TOKENS = 32_768
SPEC_TIMEOUT_MS = 180_000
REVIEW_TIMEOUT_MS = 300_000
OBJECTIVE_BATCH_CHARS = 40_000
OBJECTIVE_CALL_TIMEOUT_MS = 600_000
OBJECTIVE_MAX_OUTPUT_TOKENS = 65_536
MAX_SALVAGE_ROUNDS = 2
MAX_DROPPED_NUMBERS_PER_FIELD = 2
ENGLISH_SNIPPET_CHARS = 15_000
CACHE_TTL_SECONDS = 3600
CACHE_REFRESH_MARGIN_SECONDS = 300
STEP_KEYS = ("spec", "label", "exercise", "review", "tikz", "terminology", "glossary_merge")
DEFAULT_THINKING_BY_STEP = {key: "medium" for key in STEP_KEYS}
DEFAULT_ESCALATION_THINKING = "high"
ESCALATION_FAILED = "escalation exhausted"
TRANSIENT_ONLY = "transient-only failure"
REPORT_JSON = "translation_report.json"
REPORT_MD = "translation_report.md"
GLOSSARY_CONFLICTS_FILE = "glossary_conflicts.json"
GLOSSARY_VERSION = 1

SPEC_STEP = "spec_translate"
LABEL_STEP = "label_translate"
EXERCISE_STEP = "exercise_translate"
TIKZ_STEP = "tikz_translate"
REVIEW_STEP = "translation_review"
TRANSLATION_STATE_STAGE = "00_translation_state"
TRANSLATION_MANIFEST_VERSION = 4
MAP_FRAGMENTS_DIR = "map_fragments"
BOOK_MAP_FILE = "book_map.json"

LETTER_MAP = {
    english: arabic
    for english, arabic in zip(CHOICE_LETTERS["en"], CHOICE_LETTERS["ar"])
}
BIDI_TABLE = dict.fromkeys(
    [0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)],
    None,
)
WORD_IN_TEXT = re.compile(r"\\text\{[^}]*[A-Za-z\u0600-\u06FF]")
AXIS_WORDS = re.compile(
    r"(xlabel|ylabel|xticklabels|yticklabels|legend entries)\s*=",
)
# Scripts and fullwidth punctuation that Noto Naskh Arabic has no glyph for.
# Papers.py fails the section on a Missing character log line.
FOREIGN_PRINT = re.compile(
    r"[\u0900-\u0DFF\u0E00-\u0E7F\u1100-\u11FF\u3040-\u30FF"
    r"\u3400-\u4DBF\u4E00-\u9FFF\uAC00-\uD7AF\uF900-\uFAFF\uFF00-\uFFEF]"
)
COMMAND_MARK = re.compile(r"\\[\u064b-\u065f\u0670]")
BARE_TABLE = re.compile(r"\\begin\{\|")
MODEL_CHATTER = re.compile(
    r"Brevity check:|standard math notation staying untouched|"
    r"accurately translated",
    re.I,
)
FOREIGN_TAIL = re.compile(
    r"(?:"
    r"[\u0900-\u0DFF\u4E00-\u9FFF\uFF00-\uFFEF]+"
    r"|Brevity check:.*"
    r"|on options standard math notation.*"
    r")+",
    re.I | re.S,
)
_FIGURE_LOCKS: dict[str, threading.Lock] = {}
_FIGURE_LOCKS_GATE = threading.Lock()


class StrictReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @classmethod
    def model_json_schema(cls, *args, **kwargs) -> dict:
        """Keep strict local parsing while omitting Gemini-unsupported schema keys."""
        schema = super().model_json_schema(*args, **kwargs)

        def compatible(value):
            if isinstance(value, dict):
                return {
                    key: compatible(item)
                    for key, item in value.items()
                    if key != "additionalProperties"
                }
            if isinstance(value, list):
                return [compatible(item) for item in value]
            return value

        return compatible(schema)


class TranslatedSpecFigure(StrictReply):
    purpose: str
    description: str


class TranslatedSpecText(StrictReply):
    id: str
    text: str


class TranslatedOutOfScope(StrictReply):
    id: str
    text: str
    reason: str


class TranslatedGlossary(StrictReply):
    id: str
    term: str
    definition: str


class TranslatedTeachingPoint(StrictReply):
    id: str
    title: str
    notes: str
    figure: TranslatedSpecFigure | None


class TranslatedPoemRow(StrictReply):
    shatrs: list[str]


class TranslatedPoem(StrictReply):
    kashida: bool = True
    rows: list[TranslatedPoemRow]


class TranslatedExcerpt(StrictReply):
    id: str
    text: str
    kashida: bool = True
    rows: list[TranslatedPoemRow] = Field(default_factory=list)


class TranslatedEquation(StrictReply):
    id: str
    latex: str


class TranslatedNotation(StrictReply):
    id: str
    notation: str
    meaning: str


class TranslatedSpec(StrictReply):
    overview: str
    objectives: list[TranslatedSpecText]
    prerequisites: list[TranslatedSpecText]
    out_of_scope: list[TranslatedOutOfScope]
    glossary: list[TranslatedGlossary]
    teaching_points: list[TranslatedTeachingPoint]
    excerpts: list[TranslatedExcerpt]
    equations: list[TranslatedEquation]
    notations: list[TranslatedNotation]


class TerminologyPair(StrictReply):
    english: str
    arabic: str


class MapEvidence(StrictReply):
    source_id: str
    split_page: int
    quote: str


class MapObservation(StrictReply):
    id: str
    status: Literal["confirmed", "inferred", "ambiguous"]
    plain_lemma: str
    math_text: str
    basis: str
    evidence: list[MapEvidence]


class MapFragment(StrictReply):
    section: str = Field(
        description="Copy the section number exactly, as 4.5 not 4-5 or 5"
    )
    observations: list[MapObservation]


class SpecTranslateReply(StrictReply):
    status: Literal["ok", "abort"]
    abort_reason: str = Field(description="Why translation failed; empty when ok")
    spec: TranslatedSpec | None
    terminology: list[TerminologyPair]
    map_fragment: MapFragment | None


class TranslatedLabel(StrictReply):
    id: str
    label: str


class LabelTranslateReply(StrictReply):
    status: Literal["ok", "abort"]
    abort_reason: str = Field(description="Why translation failed; empty when ok")
    objectives: list[TranslatedLabel]


class TranslatedFigure(StrictReply):
    """Printed figure fields the translation call is allowed to change."""

    description: str = Field(description="Arabic figure description")


class TranslatedExercise(StrictReply):
    """One exercise as the translation call returns it; ids stay English."""

    id: str = Field(description="Copy the English exercise id")
    text: str = Field(description="The exercise as the student reads it")
    options: list[str]
    answer: str
    figures: list[TranslatedFigure]
    poems: list[TranslatedPoem] = Field(default_factory=list)


class TranslatedGroup(StrictReply):
    """One group as the translation call returns it; ids and counts stay English."""

    id: str = Field(description="Copy the English group id")
    stem: str
    figures: list[TranslatedFigure]
    poems: list[TranslatedPoem] = Field(default_factory=list)
    exercises: list[TranslatedExercise]


class GroupTranslateReply(StrictReply):
    status: Literal["ok", "abort"]
    abort_reason: str = Field(description="Why translation failed; empty when ok")
    group: TranslatedGroup | None


class TranslationIssue(StrictReply):
    group: str
    exercise: str
    field: str
    reason: str


class TranslationReviewReply(StrictReply):
    status: Literal["ok", "abort"]
    abort_reason: str
    issues: list[TranslationIssue]


class TikzTranslateReply(StrictReply):
    status: Literal["ok", "abort"]
    abort_reason: str = Field(description="Why translation failed; empty when ok")
    tikz: str = Field(description="One tikzpicture environment; empty only when aborting")


class GroupAbort(StrictReply):
    id: str = Field(description="English group id that cannot be translated")
    reason: str


class DroppedNumber(StrictReply):
    field: str = Field(description="Location such as 'stem', 'exercise_3.text', 'exercise_3.options[1]', 'exercise_3.answer'")
    token: str = Field(description="The omitted number placeholder, e.g. N2")
    reason: str = Field(description="Why Arabic omits the digit, e.g. 'dual: دقيقتين'")


class TranslatedGroupV2(StrictReply):
    """TranslatedGroup plus the numbers Arabic legitimately leaves out."""

    id: str = Field(description="Copy the English group id")
    stem: str
    figures: list[TranslatedFigure]
    poems: list[TranslatedPoem] = Field(default_factory=list)
    exercises: list[TranslatedExercise]
    dropped_numbers: list[DroppedNumber] = Field(default_factory=list)


class ObjectiveTranslateReply(StrictReply):
    status: Literal["ok", "abort"]
    abort_reason: str = Field(description="Why nothing could be translated; empty when ok")
    groups: list[TranslatedGroupV2]
    aborted: list[GroupAbort] = Field(default_factory=list)


class UnitObservation(StrictReply):
    english: str = Field(description="Base unit exactly as the English prints it, without a power: cm, km/h, EGP, hour, minute, square is NOT a unit")
    status: Literal["confirmed", "inferred"]
    arabic: str = Field(description="The Arabic form the book prints in formulas, e.g. سم, كم/س, ج.م, ساعة")
    also: list[str] = Field(default_factory=list, description="Other Arabic forms the book prints for the same unit, e.g. جنيه")
    abbreviation_only: bool = Field(description="true when the Arabic form is an abbreviation that is never inflected (سم, كم/س, ج.م); false for a word unit that inflects (ساعة, دقيقة, جنيه)")
    basis: str
    evidence: list[MapEvidence]


class TerminologyFragment(StrictReply):
    section: str = Field(description="Copy the section number exactly, as 4.5 not 4-5 or 5")
    observations: list[MapObservation]
    units: list[UnitObservation]


class TerminologyReply(StrictReply):
    status: Literal["ok", "abort"]
    abort_reason: str = Field(description="Why extraction failed; empty when ok")
    map_fragment: TerminologyFragment | None


class GlossaryMergeForm(StrictReply):
    arabic: str
    sections: list[str]


class GlossaryMergeConflict(StrictReply):
    english: str
    kind: Literal["term", "symbol", "unit"]
    forms: list[GlossaryMergeForm]
    chosen: str
    reason: str


class GlossaryMergeEntry(StrictReply):
    kind: Literal["term", "symbol", "unit"]
    english: str
    arabic: str
    also: list[str]
    abbreviation_only: bool
    source_row_ids: list[str]
    reason: str


class GlossaryMergeDrop(StrictReply):
    row_id: str
    reason: str


class GlossaryMergeReply(StrictReply):
    entries: list[GlossaryMergeEntry]
    dropped: list[GlossaryMergeDrop]
    conflicts: list[GlossaryMergeConflict]


class SpecOnlyReply(StrictReply):
    status: Literal["ok", "abort"]
    abort_reason: str = Field(description="Why translation failed; empty when ok")
    spec: TranslatedSpec | None
    terminology: list[TerminologyPair]
    dropped_numbers: list[DroppedNumber] = Field(default_factory=list)


class SpecFieldFix(StrictReply):
    field: str = Field(description="Spec path such as excerpt_1.text or teaching_point_1.figure.description")
    text: str


class SpecSalvageReply(StrictReply):
    status: Literal["ok", "abort"]
    abort_reason: str = Field(description="Why the replacement failed; empty when ok")
    fields: list[SpecFieldFix] = Field(default_factory=list)
    dropped_numbers: list[DroppedNumber] = Field(default_factory=list)


IssueCategory = Literal[
    "meaning", "answer", "unit", "number", "notation",
    "terminology", "language", "figure_label", "other",
]


class TranslationIssueV2(StrictReply):
    group: str
    exercise: str
    field: str
    category: IssueCategory
    reason: str


class TranslationReviewReplyV2(StrictReply):
    status: Literal["ok", "abort"]
    abort_reason: str
    issues: list[TranslationIssueV2]


def _require_objective(result: ObjectiveTranslateReply) -> None:
    if result.status != "abort" and not result.groups and not result.aborted:
        raise ValueError("ok reply has no groups")


@dataclass(frozen=True)
class Run:
    client: genai.Client
    logger: ApiLogger
    en_id: str
    ar_id: str
    ar_meta: dict[str, str]
    model: str
    thinking: str
    prompt: Prompt
    pad_width: int
    sections_root: Path
    update: bool
    progress: Progress
    workers: int
    call_slots: threading.BoundedSemaphore
    call_executor: ThreadPoolExecutor
    attempt_lock: threading.Lock
    attempt_counts: dict[tuple[str, str], int]
    attempt_errors: dict[tuple[str, str], list[str]]
    blocked_items: set[tuple[str, str]]
    thinking_by_step: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_THINKING_BY_STEP))
    escalation_thinking: str = DEFAULT_ESCALATION_THINKING
    caches: PromptCaches | None = None
    book_map: dict = field(default_factory=dict)
    use_cache: bool = True
    retranslate_on_prompt_change: bool = False
    rereview: bool = False


def strip_bidi(value: Any) -> Any:
    """Drop bidi marks the typesetter cannot render, however deep they sit."""
    if isinstance(value, str):
        return value.translate(BIDI_TABLE)
    if isinstance(value, list):
        return [strip_bidi(item) for item in value]
    if isinstance(value, dict):
        return {key: strip_bidi(item) for key, item in value.items()}
    return value


def repair_translated_text(text: str) -> str:
    """Undo the model junk that has already broken Arabic papers."""
    text = re.sub(r"\\begin\{\|", r"\\begin{array}{|", text)
    text = re.sub(r"\\[\u064b-\u065f\u0670]+", r"\\", text)
    text = FOREIGN_TAIL.sub("", text)
    text = MODEL_CHATTER.sub("", text).strip()
    if (
        text.startswith("$$")
        and text.count("$$") == 1
        and text.endswith("$")
        and not text.endswith("$$")
    ):
        text = "$" + text[2:]
    return text.strip()


def assert_printable_arabic(text: str, where: str) -> None:
    """Fail a group so request_reply retries instead of writing unprintable JSON."""
    if BARE_TABLE.search(text):
        raise ValueError(f"{where}: table is \\begin{{|…}} not array")
    if COMMAND_MARK.search(text):
        raise ValueError(f"{where}: Arabic mark glued to a LaTeX command")
    if MODEL_CHATTER.search(text):
        raise ValueError(f"{where}: model chatter in the translation")
    match = FOREIGN_PRINT.search(text)
    if match:
        raise ValueError(f"{where}: missing-glyph character {match.group()!r}")
    for index, char in enumerate(text):
        if not ("\u064b" <= char <= "\u065f" or char == "\u0670"):
            continue
        look = index - 1
        while look >= 0 and (
            "\u064b" <= text[look] <= "\u065f" or text[look] == "\u0670"
        ):
            look -= 1
        if look < 0 or not ("\u0621" <= text[look] <= "\u064a"):
            raise ValueError(f"{where}: Arabic mark not after a letter")


def clean_translated_text(text: str, where: str) -> str:
    cleaned = repair_translated_text(text)
    assert_printable_arabic(cleaned, where)
    if text.strip() and not cleaned.strip():
        raise ValueError(f"{where}: translation became empty after stripping junk")
    return cleaned


def group_print_strings(group: dict) -> list[str]:
    texts: list[str] = []
    if isinstance(group.get("stem"), str):
        texts.append(group["stem"])
    for figure in group.get("figures") or []:
        texts.append(figure_description(figure))
    texts.extend(poem_shatrs(group))
    for exercise in group.get("exercises") or []:
        for name in ("text", "answer"):
            if isinstance(exercise.get(name), str):
                texts.append(exercise[name])
        texts.extend(str(item) for item in (exercise.get("options") or []))
        for figure in exercise.get("figures") or []:
            texts.append(figure_description(figure))
        texts.extend(poem_shatrs(exercise))
    return texts


def terminal_section_error(item: str) -> bool:
    """True when a section failure must not be sent back to the model."""
    return (
        "retry circuit open" in item
        or "retry budget exhausted" in item
        or "same semantic validation error repeated" in item
        or UNTRANSLATABLE_SOURCE in item
        or ESCALATION_FAILED in item
    )


def source_corruption_abort(reason: str) -> bool:
    """True when the model aborted because the English source is unusable."""
    folded = reason.casefold()
    return (
        "${" in reason
        or UNTRANSLATABLE_SOURCE.casefold() in folded
        or "unfilled template" in folded
        or "unresolved template" in folded
        or "prompt leak" in folded
        or "preference indicator" in folded
        or "corrupted source" in folded
        or "corrupted english" in folded
    )


def english_source_untranslatable(group: dict, where: str) -> str | None:
    """Unfilled ``${ident}`` placeholders and generator leakage in English."""
    problems = source_artifact_problems(str(group.get("stem") or ""), f"{where}.stem")
    for exercise in group.get("exercises") or []:
        spot = f"{where}/{exercise.get('id') or 'exercise'}"
        problems.extend(
            source_artifact_problems(str(exercise.get("text") or ""), f"{spot}.text")
        )
        problems.extend(
            source_artifact_problems(
                str(exercise.get("answer") or ""), f"{spot}.answer"
            )
        )
        for index, option in enumerate(exercise.get("options") or []):
            problems.extend(
                source_artifact_problems(str(option), f"{spot}.options[{index}]")
            )
    if not problems:
        return None
    return f"{where}: {UNTRANSLATABLE_SOURCE}: {'; '.join(problems)}"


def write_json(path: Path, document: dict) -> None:
    atomic_write_json(path, strip_bidi(document))


def manifest_path(book_id: str, stem: str) -> Path:
    return OUTPUT_DIR / book_id / TRANSLATION_STATE_STAGE / f"{stem}.json"


def map_fragment_path(book_id: str, stem: str) -> Path:
    return (
        OUTPUT_DIR
        / book_id
        / TRANSLATION_STATE_STAGE
        / MAP_FRAGMENTS_DIR
        / f"{stem}.json"
    )


def book_map_path(book_id: str) -> Path:
    return OUTPUT_DIR / book_id / TRANSLATION_STATE_STAGE / BOOK_MAP_FILE


def read_manifest(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def envelope(
    ar_id: str,
    meta: dict[str, str],
    number: str,
    title: str,
    module_path: str,
    **body: Any,
) -> dict:
    return {
        "book_id": ar_id,
        **meta,
        "number": number,
        "title": title,
        "module_path": module_path,
        **body,
    }


def already_written(path: Path, update: bool) -> bool:
    return (not update) and path.is_file() and path.stat().st_size > 2


def _require_spec(result: SpecTranslateReply) -> None:
    """Replay helper for the retired spec reply, which still carries a map fragment."""
    if result.status != "abort" and result.spec is None:
        raise ValueError("ok reply has no spec")


def _require_spec_only(result: SpecOnlyReply) -> None:
    if result.status != "abort" and result.spec is None:
        raise ValueError("ok reply has no spec")


def _require_terminology(result: TerminologyReply) -> None:
    if result.status != "abort" and result.map_fragment is None:
        raise ValueError("ok reply has no terminology fragment")


def _require_group(result: GroupTranslateReply) -> None:
    if result.status != "abort" and (
        result.group is None or not result.group.exercises
    ):
        raise ValueError("ok reply has no group")


def bind_profile(assembled: Prompt, meta: dict[str, str]) -> Prompt:
    """Name the AR book profile on the core so a long JSON body cannot bury it."""
    if PROFILE_SLOT not in assembled.text:
        raise RuntimeError(f"translate core.md has no {PROFILE_SLOT} slot")
    return Prompt(
        assembled.text.replace(PROFILE_SLOT, f"Profile: {Profile.of(meta).describe()}."),
        assembled.parts,
    )


def profile_heading(run: Run) -> str:
    return f"Profile: {Profile.of(run.ar_meta).describe()}."


def task_prompt(assembled: Prompt, task_file: str) -> Prompt:
    path = stage_dir(PROMPT_STAGE) / task_file
    if TASK_SLOT not in assembled.text:
        raise RuntimeError(f"translate core.md has no {TASK_SLOT} slot")
    body = read_part(path).strip()
    parts = assembled.parts + (relative(path),)
    slot = "{unit_agreement}"
    if slot in body:
        shared = stage_dir(PROMPT_STAGE) / "unit_agreement.md"
        body = body.replace(slot, read_part(shared).strip())
        parts = parts + (relative(shared),)
    return Prompt(assembled.text.replace(TASK_SLOT, body), parts)


def book_line(meta: dict[str, str]) -> str:
    return (
        f"{meta.get('country', '')}, grade {meta.get('grade', '')}, term {meta.get('term', '')}, "
        f"{meta.get('subject', '')} ({meta.get('language', '')}, {meta.get('numerals', '')} numerals, "
        f"{meta.get('notation', '')} notation)"
    )


def task_system_text(run: Run, task_file: str) -> Prompt:
    """core + language + numerals + notation + task file, without the glossary."""
    prompt = task_prompt(run.prompt, task_file)
    if task_file == "review.md" and "{book_line}" not in prompt.text:
        raise RuntimeError("review.md has no {book_line} slot")
    return Prompt(prompt.text.replace("{book_line}", book_line(run.ar_meta)), prompt.parts)


def system_prompt(run: Run, task_file: str) -> Prompt:
    """task_system_text plus the full book glossary once the map exists."""
    base = task_system_text(run, task_file)
    if task_file == "terminology.md" or not run.book_map.get("entries"):
        return base
    from src.book_map import glossary_block

    return Prompt(base.text + "\n\n" + glossary_block(run.book_map), base.parts)


class PromptCaches:
    """One explicit Gemini cache per (task, system text). Falls back to None."""

    def __init__(
        self,
        client,
        model: str,
        ar_id: str,
        enabled: bool = True,
        logger=None,
        *,
        now=None,
    ) -> None:
        self.client = client
        self.model = model
        self.ar_id = ar_id
        self.enabled = enabled
        self.logger = logger
        self.now = now if now is not None else (lambda: time.time())
        self._lock = threading.Lock()
        self._names: dict[tuple[str, str], tuple[str, float]] = {}
        self._leases: list[dict] = []
        self._in_use: dict[str, int] = {}
        self._replaced: set[str] = set()
        self._closed = False
        self.errors: dict[str, str] = {}
        self.failures: list[dict] = []

    def _ledger_path(self) -> Path:
        return OUTPUT_DIR / self.ar_id / "00_translation_state" / "cache_ledger.jsonl"

    def _read_ledger(self) -> list[dict]:
        path = self._ledger_path()
        if not path.is_file():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def _append_ledger(self, row: dict) -> None:
        path = self._ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _mark_ledger_closed(self, name: str) -> None:
        self._append_ledger({"name": name, "closed": True})

    def recover_orphans(self) -> None:
        """Delete caches a killed run left open, and bill each one once."""
        open_rows: dict[str, dict] = {}
        for row in self._read_ledger():
            name = str(row.get("name") or "")
            if not name:
                continue
            if row.get("closed"):
                open_rows.pop(name, None)
            else:
                open_rows[name] = row
        for name, row in open_rows.items():
            try:
                self.client.caches.delete(name=name)
            except Exception:  # noqa: BLE001
                pass
            created = float(row.get("created_at") or self.now())
            ttl = float(row.get("ttl") or CACHE_TTL_SECONDS)
            seconds = min(max(0.0, self.now() - created), ttl)
            lease = {
                "name": name,
                "task": row.get("task") or "",
                "tokens": int(row.get("tokens") or 0),
                "created_at": created,
                "created_at_iso": row.get("created_at_iso") or "",
                "model": row.get("model") or self.model,
                "seconds": seconds,
                "state": "recovered",
                "deleted": True,
                "error": "",
                "recovered": True,
            }
            self._leases.append(lease)
            self._log_storage([lease])
            self._mark_ledger_closed(name)

    def note_uncached(self, task: str) -> None:
        for row in self.failures:
            if row["task"] == task:
                row["uncached_calls"] = int(row.get("uncached_calls") or 0) + 1

    def acquire(self, name: str) -> None:
        with self._lock:
            self._in_use[name] = self._in_use.get(name, 0) + 1

    def release(self, name: str) -> None:
        with self._lock:
            self._in_use[name] = max(0, self._in_use.get(name, 1) - 1)
            idle = self._in_use[name] == 0 and name in self._replaced
        if idle:
            self._delete_named(name)

    def _create_once(self, task: str, text: str, fingerprint: str):
        return self.client.caches.create(
            model=self.model,
            config=types.CreateCachedContentConfig(
                system_instruction=text,
                ttl=f"{CACHE_TTL_SECONDS}s",
                display_name=f"{self.ar_id}:{task}:{fingerprint[:8]}",
            ),
        )

    def name_for(self, task: str, text: str) -> str | None:
        if not self.enabled or not text or task == "glossary_merge":
            return None
        from src.translation_quality import json_fingerprint

        fingerprint = json_fingerprint(text)
        key = (task, fingerprint)
        with self._lock:
            if task in self.errors:
                return None
            now = self.now()
            cached = self._names.get(key)
            if cached and now - cached[1] < CACHE_TTL_SECONDS - CACHE_REFRESH_MARGIN_SECONDS:
                return cached[0]
            previous = cached[0] if cached else ""
            created = None
            last_error = ""
            for _attempt in (1, 2):
                try:
                    created = self._create_once(task, text, fingerprint)
                    last_error = ""
                    break
                except Exception as error:  # noqa: BLE001
                    last_error = f"{type(error).__name__}: {error}"
                    created = None
            if created is None:
                self.errors[task] = last_error
                print(f"cache for {task} not created: {last_error}; running uncached", flush=True)
                self.failures.append(
                    {
                        "task": task,
                        "error": last_error,
                        "time": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                        "uncached_calls": 0,
                    }
                )
                return None
            name = str(getattr(created, "name", "") or "")
            if not name:
                message = "cache create returned no name"
                self.errors[task] = message
                print(f"cache for {task} not created: {message}; running uncached", flush=True)
                self.failures.append(
                    {
                        "task": task,
                        "error": message,
                        "time": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                        "uncached_calls": 0,
                    }
                )
                return None
            usage = getattr(created, "usage_metadata", None)
            tokens = int(getattr(usage, "total_token_count", 0) or 0) if usage is not None else 0
            iso = datetime.fromtimestamp(now, timezone.utc).isoformat()
            lease = {
                "name": name,
                "task": task,
                "tokens": tokens,
                "created_at": now,
                "created_at_iso": iso,
                "model": self.model,
                "deleted": False,
                "state": "",
                "seconds": float(CACHE_TTL_SECONDS),
                "error": "",
            }
            self._names[key] = (name, now)
            self._leases.append(lease)
            self._append_ledger(
                {
                    "name": name,
                    "task": task,
                    "model": self.model,
                    "tokens": tokens,
                    "created_at": now,
                    "created_at_iso": iso,
                    "ttl": CACHE_TTL_SECONDS,
                    "closed": False,
                }
            )
            if previous:
                self._replaced.add(previous)
                if self._in_use.get(previous, 0) == 0:
                    self._delete_named(previous)
            return name

    def _lease_named(self, name: str) -> dict | None:
        for lease in reversed(self._leases):
            if lease["name"] == name and not lease.get("state"):
                return lease
        return None

    def _finish_delete(self, lease: dict, error: str) -> None:
        age = max(0.0, self.now() - float(lease["created_at"]))
        if not error:
            lease["state"] = "deleted"
            lease["deleted"] = True
            lease["error"] = ""
            lease["seconds"] = age
        elif age >= CACHE_TTL_SECONDS:
            lease["state"] = "expired"
            lease["deleted"] = False
            lease["error"] = ""
            lease["seconds"] = float(CACHE_TTL_SECONDS)
        else:
            lease["state"] = "failed"
            lease["deleted"] = False
            lease["error"] = error
            lease["seconds"] = float(CACHE_TTL_SECONDS)
        self._mark_ledger_closed(lease["name"])
        self._log_storage([lease])

    def _delete_named(self, name: str) -> None:
        lease = self._lease_named(name)
        if lease is None or lease.get("state"):
            return
        error = ""
        try:
            self.client.caches.delete(name=name)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        self._finish_delete(lease, error)
        self._replaced.discard(name)

    def close(self) -> None:
        """Delete every cache this run has not already deleted."""
        if self._closed:
            return
        with self._lock:
            pending = [lease for lease in self._leases if not lease.get("state")]
            self._names.clear()
            self._closed = True
        for lease in pending:
            error = ""
            try:
                self.client.caches.delete(name=lease["name"])
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {exc}"
            self._finish_delete(lease, error)

    def _log_storage(self, leases: list[dict]) -> None:
        if self.logger is None:
            return
        for index, lease in enumerate(leases, start=1):
            try:
                self.logger.log(
                    page_number=f"cache_{index}",
                    step_name="cache_storage",
                    attempt=1,
                    api="gemini",
                    model=self.model,
                    usage={
                        "cache_storage_tokens": int(lease["tokens"]),
                        "cache_storage_seconds": round(float(lease["seconds"]), 3),
                    },
                    duration_seconds=0,
                    status="success",
                    error_message=lease.get("error") or None,
                    extra={
                        "book_id": self.ar_id,
                        "cache_name": lease["name"],
                        "cache_deleted": lease.get("state") == "deleted",
                        "cache_state": lease.get("state") or "",
                        "cache_delete_error": lease.get("error") or "",
                        "cache_ttl_seconds": CACHE_TTL_SECONDS,
                        "task": lease["task"],
                        "recovered": bool(lease.get("recovered")),
                    },
                )
            except Exception:  # noqa: BLE001
                continue

    def storage_rows(self) -> list[dict]:
        """One priced row per cache. Call after ``close``."""
        from cost import load_prices

        prices = load_prices()
        rows = []
        for lease in self._leases:
            seconds = float(lease["seconds"])
            tokens = int(lease["tokens"])
            model = str(lease.get("model") or self.model)
            stamp = str(lease.get("created_at_iso") or "")
            try:
                rate = prices.rates[model].period_for(stamp) if stamp else prices.rates[model].current
            except RuntimeError:
                rate = prices.rates[model].current
            hourly = rate.cache_storage_per_million_per_hour
            usd = tokens * (seconds / 3600) * hourly / 1_000_000
            rows.append(
                {
                    "name": lease["name"],
                    "task": lease["task"],
                    "tokens": tokens,
                    "seconds": round(seconds, 3),
                    "state": lease.get("state") or "",
                    "deleted": lease.get("state") == "deleted",
                    "usd": usd,
                }
            )
        return rows


def terminology_key(run: Run, section: dict, inventory, sources, snippets) -> str:
    return json_fingerprint(
        {
            "pdfs": sources,
            "inventory": inventory,
            "snippets": snippets,
            "task": read_part(stage_dir(PROMPT_STAGE) / "terminology.md"),
            "schema": TerminologyReply.model_json_schema(),
            "notation": run.ar_meta.get("notation"),
            "section": section.get("number"),
        }
    )


def spec_source_fp(run: Run, section: dict) -> str:
    """English spec plus glossary, without the prompt text."""
    return json_fingerprint(
        {
            "spec": translatable_spec(section["spec"]),
            "glossary_fp": glossary_fp(run.book_map),
        }
    )


def spec_key(run: Run, section: dict) -> str:
    return json_fingerprint(
        {
            "spec": translatable_spec(section["spec"]),
            "glossary_fp": glossary_fp(run.book_map),
            "system": task_system_text(run, "specs.md").text,
            "schema": SpecOnlyReply.model_json_schema(),
        }
    )


def spec_file_reusable(
    existing: dict,
    key: str,
    source_fp: str,
    *,
    update: bool,
    retranslate_on_prompt_change: bool,
) -> bool:
    """Reuse a finished spec unless --update, or the English spec/glossary moved.

    A prompt-only spec_key change does not redo the spec; that matches groups,
    which keep a reviewed row when only prompt_fp changed.
    """
    if update or not existing.get("spec"):
        return False
    translation = existing.get("translation") or {}
    if translation.get("spec_key") == key:
        return True
    stored_source = translation.get("spec_source_fp")
    if stored_source == source_fp:
        return True
    if stored_source is None:
        return not retranslate_on_prompt_change
    return False


def labels_source_fp(run: Run, en_labels: dict, ar_spec: dict) -> str:
    """English labels plus the Arabic spec and glossary, without the prompt text."""
    return json_fingerprint(
        {
            "labels": en_labels["labels"],
            "spec": ar_spec,
            "glossary_fp": glossary_fp(run.book_map),
        }
    )


def labels_key(run: Run, en_labels: dict, ar_spec: dict) -> str:
    return json_fingerprint(
        {
            "labels": en_labels["labels"],
            "spec": ar_spec,
            "glossary_fp": glossary_fp(run.book_map),
            "system": task_system_text(run, "labels.md").text,
        }
    )


def labels_reusable(
    *,
    file_exists: bool,
    stored_key: str | None,
    current_key: str,
    stored_source: str | None,
    current_source: str,
    update: bool,
    retranslate_on_prompt_change: bool,
) -> bool:
    """Keep finished labels unless --update, or the English labels/spec/glossary moved."""
    if update or not file_exists:
        return False
    if stored_key == current_key:
        return True
    if stored_source and stored_source == current_source:
        return True
    if not stored_source:
        return not retranslate_on_prompt_change
    return False


def accepted_section_reusable(
    run: Run,
    section: dict,
    state: dict,
    prepared: dict[str, Any],
) -> bool:
    """True when a finished section must not be sent to the model again.

    A whole-book run without --update / --rereview / --retranslate-on-prompt-change
    skips a section whose outputs are already on disk: labels, groups, review
    and figures stay as they were saved. Ctrl+C after that work, which leaves
    ``accepted`` false, still skips — the outputs are complete, not missing.
    """
    if run.update or run.rereview or run.retranslate_on_prompt_change:
        return False
    if state.get("version") != TRANSLATION_MANIFEST_VERSION:
        return False
    if state.get("failure"):
        return False
    if not state.get("accepted") and not state.get("exercises_accepted"):
        return False
    stem = section["stem"]
    root = OUTPUT_DIR / run.ar_id
    for folder in (SPECS_STAGE, LABELS_STAGE, BUDGETS_STAGE, ACCEPTED_STAGE):
        if not (root / folder / f"{stem}.json").is_file():
            return False
    en_document = prepared.get("en_document") if isinstance(prepared, dict) else None
    if not isinstance(en_document, dict):
        return False
    if figure_places(en_document) and (state.get("figures") or {}).get("status") != "passed":
        return False
    if section_needs_relabel(run.ar_id, run.en_id, section["number"]):
        return False
    reason = exercises_incomplete_reason(
        en_document,
        root / ACCEPTED_STAGE / f"{stem}.json",
        run.ar_meta,
        spec=section.get("spec") if isinstance(section.get("spec"), dict) else None,
        state_path=manifest_path(run.ar_id, stem),
    )
    return not reason


def group_keys(run: Run, en_group: dict, en_spec: dict, ar_spec: dict) -> dict[str, str]:
    objective = str(en_group.get("objective") or "")
    ctx = terminology_context(en_spec, ar_spec, [], objective)["objective"]
    return {
        "source_fp": json_fingerprint(
            {
                "group": translatable_group(en_group),
                "objective": objective,
                "objective_en": ctx["english"],
            }
        ),
        "glossary_fp": glossary_fp(run.book_map),
        "profile_fp": json_fingerprint(
            {
                "objective_ar": ctx["arabic"],
                "profile": {
                    key: run.ar_meta.get(key)
                    for key in ("language_code", "numerals", "notation")
                },
            }
        ),
        "prompt_fp": json_fingerprint(
            {
                "system": task_system_text(run, "exercises.md").text,
                "schema": ObjectiveTranslateReply.model_json_schema(),
            }
        ),
    }


def review_key(run: Run) -> str:
    return json_fingerprint(
        {
            "system": task_system_text(run, "review.md").text,
            "schema": TranslationReviewReplyV2.model_json_schema(),
        }
    )


def pdf_source_manifest(pdfs: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, path in enumerate(pdfs, start=1):
        rows.append(
            {
                "source_id": f"source_{index}",
                "filename": path.name,
                "pages": len(PdfReader(path).pages),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return rows


def ar_identity(ar_id: str, number: str) -> tuple[str, str]:
    spans = section_spans(ar_id, number).get(number)
    if not spans:
        raise RuntimeError(f"no Arabic section {number} in {ar_id}")
    return spans[0].title, spans[0].module_path


def section_source_pdfs(ar_id: str, number: str, pad_width: int) -> list[Path]:
    sources = section_spans(ar_id, number).get(number) or []
    return section_pdfs(
        sources, number, pad_width, OUTPUT_DIR / ar_id / SECTIONS_STAGE
    )


def overlay_spec(
    en_spec: dict,
    translated: TranslatedSpec,
    *,
    meta: dict,
    book_map: dict,
) -> dict:
    """Validate IDs/maths/language, then retain only English structural fields."""
    return strict_merge_spec(en_spec, translated.model_dump(), meta, book_map)


def map_choice_answer(answer: str) -> str:
    """Turn a leading English choice letter into the Arabic one the paper prints."""
    stripped = answer.lstrip()
    prefix_len = len(answer) - len(stripped)
    pad = answer[:prefix_len]
    for english, arabic in LETTER_MAP.items():
        if stripped.startswith(f"({english})"):
            return pad + f"({arabic})" + stripped[len(f"({english})"):]
        if stripped.startswith(english) and (
            len(stripped) == 1 or not stripped[1].isalpha()
        ):
            return pad + arabic + stripped[len(english):]
    return answer


def merge_figure(en_figure: Any, ar_figure: Any) -> Any:
    if not isinstance(en_figure, dict):
        if isinstance(ar_figure, str) and ar_figure.strip():
            return clean_translated_text(ar_figure, "figure")
        return en_figure
    out = copy.deepcopy(en_figure)
    if isinstance(ar_figure, dict) and ar_figure.get("description"):
        out["description"] = clean_translated_text(
            str(ar_figure["description"]), "figure.description"
        )
    elif isinstance(ar_figure, str) and ar_figure.strip():
        out["description"] = clean_translated_text(ar_figure, "figure.description")
    return out


def merge_exercise(en_row: dict, ar_row: dict) -> dict:
    out = copy.deepcopy(en_row)
    where = str(en_row.get("id") or "exercise")
    for name in ("text", "answer"):
        if name in ar_row and isinstance(ar_row[name], str) and ar_row[name].strip():
            out[name] = clean_translated_text(ar_row[name], f"{where}.{name}")
    if "answer" in out:
        out["answer"] = map_choice_answer(str(out["answer"]))
    if isinstance(ar_row.get("options"), list):
        expected = len(out.get("options") or [])
        got = len(ar_row["options"])
        if got != expected:
            raise ValueError(
                f"{where}: translated {got} option(s), expected {expected}"
            )
        out["options"] = [
            clean_translated_text(str(item), f"{where}.options[{index}]")
            for index, item in enumerate(ar_row["options"])
        ]
    en_figures = list(out.get("figures") or [])
    ar_figures = list(ar_row.get("figures") or [])
    if en_figures:
        out["figures"] = [
            merge_figure(en_figure, ar_figures[index] if index < len(ar_figures) else None)
            for index, en_figure in enumerate(en_figures)
        ]
    out["id"] = en_row["id"]
    out["objectives"] = list(en_row.get("objectives") or [])
    out["word_problem"] = bool(en_row.get("word_problem"))
    return out


def merge_group(
    en_group: dict,
    ar_group: dict,
    meta: dict,
    book_map: dict,
    *,
    spec: dict | None = None,
    dropped: dict[str, int] | None = None,
) -> dict:
    return strict_merge_group(
        en_group,
        ar_group,
        meta,
        book_map,
        vector_lesson=section_is_vector_lesson(spec) if spec is not None else True,
        dropped=dropped,
    )


def group_row_problem(
    en_group: dict,
    ar_group: dict | None,
    meta: dict,
    book_map: dict | None = None,
    *,
    spec: dict | None = None,
    dropped: dict[str, int] | None = None,
) -> str:
    """Empty when the Arabic group is complete; otherwise the check that failed."""
    del book_map
    gid = str(en_group.get("id") or "group")
    if not isinstance(ar_group, dict) or ar_group.get("id") != en_group.get("id"):
        return f"{gid}: Arabic group missing or id mismatch"
    try:
        merge_group(
            en_group,
            ar_group,
            meta,
            {},
            spec=spec,
            dropped=dropped,
        )
    except (ValueError, TypeError, KeyError) as error:
        return f"{gid}: {error}"
    en_exercises = list(en_group.get("exercises") or [])
    ar_exercises = list(ar_group.get("exercises") or [])
    if len(en_exercises) != len(ar_exercises):
        return (
            f"{gid}: translated {len(ar_exercises)} exercise(s), "
            f"expected {len(en_exercises)}"
        )
    for en_row, ar_row in zip(en_exercises, ar_exercises):
        if ar_row.get("id") != en_row.get("id"):
            return f"{gid}: exercise id is {ar_row.get('id')!r}, expected {en_row.get('id')!r}"
        if len(ar_row.get("options") or []) != len(en_row.get("options") or []):
            return f"{en_row.get('id')}: option count mismatch"
        if len(ar_row.get("figures") or []) != len(en_row.get("figures") or []):
            return f"{en_row.get('id')}: figure count mismatch"
    for text in group_print_strings(ar_group):
        try:
            assert_printable_arabic(text, gid)
        except ValueError as error:
            return f"{gid}: {error}"
    return ""


def group_row_complete(
    en_group: dict,
    ar_group: dict | None,
    meta: dict,
    book_map: dict | None = None,
    *,
    spec: dict | None = None,
    dropped: dict[str, int] | None = None,
) -> bool:
    """True when the Arabic group has the same ids and exercise counts as English."""
    return not group_row_problem(
        en_group,
        ar_group,
        meta,
        book_map,
        spec=spec,
        dropped=dropped,
    )


def load_translated_groups(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    groups = {}
    for group in document.get("exercises", {}).get("groups") or []:
        name = str(group.get("id") or "")
        if name:
            groups[name] = group
    return groups


def repair_stored_escapes(path: Path) -> int:
    """Put back LaTeX backslashes a stored document lost to JSON escapes.

    Groups accepted before the reply check repaired ``\\text`` read as TAB +
    ``ext`` still print the control. The repair is deterministic, so a kept
    group is fixed in place rather than sent to the model again.
    """
    if not path.is_file():
        return 0
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(document, dict) or "exercises" not in document:
        return 0
    repaired, count = repair_stored_controls(document["exercises"])
    if count:
        write_json(path, {**document, "exercises": repaired})
    return count


def exercises_incomplete_reason(
    en_document: dict,
    ar_path: Path,
    meta: dict,
    book_map: dict | None = None,
    *,
    dropped: dict[str, dict[str, int]] | None = None,
    spec: dict | None = None,
    state_path: Path | None = None,
) -> str:
    """The first completeness failure, or empty when Arabic 09_ is intact."""
    del book_map
    if dropped is None and state_path is not None:
        dropped = _manifest_drops(read_manifest(state_path).get("groups") or {})
    drops = dropped or {}
    kept = load_translated_groups(ar_path)
    en_groups = list(en_document.get("exercises", {}).get("groups") or [])
    if not en_groups:
        return "no English groups"
    if len(kept) != len(en_groups):
        return f"{len(kept)}/{len(en_groups)} group(s) on disk"
    for en_group in en_groups:
        gid = str(en_group.get("id") or "")
        reason = group_row_problem(
            en_group,
            kept.get(gid),
            meta,
            spec=spec,
            dropped=drops.get(gid) or None,
        )
        if reason:
            return reason
    return ""


def exercises_complete(
    en_document: dict,
    ar_path: Path,
    meta: dict,
    book_map: dict | None = None,
    *,
    dropped: dict[str, dict[str, int]] | None = None,
    spec: dict | None = None,
    state_path: Path | None = None,
) -> bool:
    """True when Arabic 09_ has every English accepted group, intact."""
    return not exercises_incomplete_reason(
        en_document,
        ar_path,
        meta,
        book_map,
        dropped=dropped,
        spec=spec,
        state_path=state_path,
    )


def tikz_has_words(tikz: str, book_map: dict | None = None) -> bool:
    """True when the picture prints Latin words a latin-notation call should change."""
    del book_map
    if re.search(r"\\text\{[^}]*[A-Za-z]{2,}", tikz) or AXIS_WORDS.search(tikz):
        return True
    for match in TIKZ_NODE.finditer(tikz):
        if re.search(r"[A-Za-z]{2,}", match.group(2)):
            return True
    return False


def tikz_needs_call(
    tikz: str, notation: str, book_map: dict | None = None
) -> bool:
    """Whether this picture needs a model call under the AR book's notation.

    Latin notation only asks when there are words. Arabic notation always asks:
    printed Latin maths letters still need converting, and the model may reverse
    the picture (a number line, a ray) even when nothing is printed.
    """
    if notation == "arabic":
        return True
    return tikz_has_words(tikz)


def figure_places(document: dict) -> list[tuple[str, str | None, int, Any]]:
    """`(group id, exercise id or None, figure index, figure)` in print order."""
    places: list[tuple[str, str | None, int, Any]] = []
    for group in document.get("exercises", {}).get("groups") or []:
        group_id = str(group.get("id") or "")
        for index, figure in enumerate(group.get("figures") or []):
            places.append((group_id, None, index, figure))
        for exercise in group.get("exercises") or []:
            exercise_id = str(exercise.get("id") or "")
            for index, figure in enumerate(exercise.get("figures") or []):
                places.append((group_id, exercise_id, index, figure))
    return places


def figure_description(figure: Any) -> str:
    if isinstance(figure, dict):
        return str(figure.get("description") or "")
    return str(figure or "")


def extra_filed_overlaps(existing: dict, source: dict) -> bool:
    """True when a filed Arabic figure added hard overlaps the English source did not have."""
    found = existing.get("overlaps")
    if found is None:
        return False
    return int(found) > int(source.get("overlaps") or 0)


def already_relabelled(existing: dict) -> bool:
    provenance = existing.get("provenance")
    return isinstance(provenance, dict) and bool(provenance.get("relabelled"))


def section_needs_relabel(ar_id: str, en_id: str, section_number: str) -> bool:
    """True when this section still has a translated figure with a new overlap."""
    en_overlaps = {
        str(row.get("key") or ""): int(row.get("overlaps") or 0)
        for _, row in figure_rows(en_id)
    }
    for _, row in figure_rows(ar_id):
        provenance = row.get("provenance")
        if not isinstance(provenance, dict):
            continue
        if provenance.get("section") != section_number:
            continue
        if already_relabelled(row):
            continue
        source = {"overlaps": en_overlaps.get(str(provenance.get("english_key") or ""), 0)}
        if extra_filed_overlaps(row, source):
            return True
    return False


def translated_figure_key(
    en_key: str,
    en_tikz: str,
    ar_description: str,
    meta: dict,
    map_fingerprint: str = "",
) -> str:
    return json_fingerprint(
        {
            "english_key": en_key,
            "english_tikz": en_tikz,
            "arabic_description": ar_description,
            "profile": {
                key: meta.get(key)
                for key in ("language_code", "numerals", "notation")
            },
            "map_fingerprint": map_fingerprint,
        }
    )[:12]


def figure_write_lock(key: str) -> threading.Lock:
    with _FIGURE_LOCKS_GATE:
        return _FIGURE_LOCKS.setdefault(key, threading.Lock())


def logged_json_call(
    run: Run,
    *,
    prompt: str,
    parts: list,
    schema: type[BaseModel],
    item: str,
    step: str,
    attempt: int,
    context: dict,
    check: Callable[[BaseModel], None] | None = None,
    extra_files: dict[str, str] | None = None,
    attached_paths: list[Path] | None = None,
    system_instruction: str | None = None,
    cache_task: str | None = None,
    thinking: str | None = None,
    timeout_ms: int | None = None,
    max_output_tokens: int | None = None,
) -> tuple[BaseModel | None, str | None]:
    level = thinking or run.thinking
    cached = None
    if run.caches and run.use_cache and cache_task and system_instruction and cache_task != "glossary_merge":
        cached = run.caches.name_for(cache_task, system_instruction)
        if cached is None and cache_task in run.caches.errors:
            run.caches.note_uncached(cache_task)
    config_kwargs: dict[str, Any] = dict(
        response_mime_type="application/json",
        response_schema=schema,
        thinking_config=types.ThinkingConfig(thinking_level=level),
        http_options=types.HttpOptions(timeout=timeout_ms or CALL_TIMEOUT_MS),
    )
    if max_output_tokens:
        config_kwargs["max_output_tokens"] = max_output_tokens
    if cached:
        config_kwargs["cached_content"] = cached
    elif system_instruction:
        config_kwargs["system_instruction"] = system_instruction
    config = types.GenerateContentConfig(**config_kwargs)
    contents = [prompt, *parts] if prompt else list(parts)
    response = None
    result = None
    error_message = None
    finish = ""
    started = time.perf_counter()
    try:
        if cached and run.caches:
            run.caches.acquire(cached)
        try:
            with run.call_slots:
                response = run.client.models.generate_content(
                    model=run.model, contents=contents, config=config
                )
        finally:
            if cached and run.caches:
                run.caches.release(cached)
        candidate = (getattr(response, "candidates", None) or [None])[0]
        finish = str(getattr(candidate, "finish_reason", "") or "")
        result = schema.model_validate_json(genai_answer_text(response))
        if check is not None:
            check(result)
    except Exception as error:  # noqa: BLE001
        result = None
        error_message = f"{type(error).__name__}: {error}"
        if "MAX_TOKENS" in finish:
            error_message = f"truncated reply: {error_message}"
    duration = time.perf_counter() - started
    prompt_paths = [
        PROJECT_ROOT / name
        for name in (context.get("prompt_files") or [])
        if isinstance(name, str)
    ]
    files = {
        **genai_request_files(
            prompt,
            parts,
            model=run.model,
            thinking=level,
            schema_name=schema.__name__,
            prompt_paths=prompt_paths,
            attached_paths=attached_paths,
            system_instruction=system_instruction,
            cached_content=cached,
        ),
        **(extra_files or {}),
    }
    if error_message:
        files["validation_error.txt"] = error_message
    safe_log(
        run.logger,
        files_builder=lambda captured=response, logged=files: {
            **logged,
            **(genai_response_files(captured) if captured is not None else {}),
        },
        page_number=item,
        step_name=step,
        attempt=attempt,
        api="gemini",
        model=run.model,
        usage=genai_usage(response) if response is not None else {},
        duration_seconds=duration,
        status="success" if result is not None else "error",
        error_message=error_message,
        extra={
            **context,
            "thinking_level": level,
            "finish_reason": finish,
            "cache": bool(cached),
        },
    )
    return result, error_message


def request_reply(
    run: Run,
    *,
    prompt: str,
    parts: list,
    schema: type[BaseModel],
    item: str,
    step: str,
    context: dict,
    check: Callable[[BaseModel], None] | None = None,
    extra_files: dict[str, str] | None = None,
    attached_paths: list[Path] | None = None,
    report: Callable[[str], None] | None = None,
    attempt_scope: str = "",
    system_instruction: str | None = None,
    cache_task: str | None = None,
    thinking: str | None = None,
    timeout_ms: int | None = None,
    max_output_tokens: int | None = None,
) -> BaseModel:
    last_error: str | None = None
    throttled = 0
    interrupted = 0
    key = (step, f"{item}#{attempt_scope}" if attempt_scope else item)
    while True:
        with run.attempt_lock:
            used = run.attempt_counts.get(key, 0)
            if key in run.blocked_items:
                repeated = (run.attempt_errors.get(key) or ["same semantic error"])[-1]
                raise RuntimeError(
                    f"retry circuit open after {used} attempt(s): {repeated}"
                )
            if used >= MAX_ATTEMPTS:
                message = (
                    f"run-wide retry budget exhausted after {used} attempts: "
                    f"{last_error or 'no usable response'}"
                )
                if _transient_only(run, key, last_error):
                    message += f" ({TRANSIENT_ONLY})"
                raise RuntimeError(message)
            attempt = used + 1
            run.attempt_counts[key] = attempt
        attempt_prompt = prompt
        attempt_parts = list(parts)
        if last_error:
            feedback = one_line(last_error)[:4000]
            block = (
                "## Correction required\n"
                f"The previous response was rejected: {feedback}\n"
                "Return a complete fresh response satisfying every field and rule; "
                "do not return a patch or omit fields."
            )
            if prompt:
                attempt_prompt = prompt + "\n\n" + block
            else:
                attempt_parts.append(block)
        result, last_error = logged_json_call(
            run,
            prompt=attempt_prompt,
            parts=attempt_parts,
            schema=schema,
            item=item,
            step=step,
            attempt=attempt,
            context=context,
            check=check,
            extra_files=extra_files,
            attached_paths=attached_paths,
            system_instruction=system_instruction,
            cache_task=cache_task,
            thinking=thinking,
            timeout_ms=timeout_ms,
            max_output_tokens=max_output_tokens,
        )
        if result is not None:
            with run.attempt_lock:
                run.attempt_errors.pop(key, None)
                run.blocked_items.discard(key)
            return result
        if rate_limited(last_error) and throttled < RATE_LIMIT_WAITS:
            with run.attempt_lock:
                run.attempt_counts[key] = max(
                    run.attempt_counts.get(key, 1) - 1, 0
                )
            throttled += 1
            pause = RATE_LIMIT_PAUSE * throttled + random.uniform(
                0, RATE_LIMIT_PAUSE
            )
            if report is not None:
                report(
                    f"rate limited ({throttled} of {RATE_LIMIT_WAITS}), "
                    f"waiting {pause:.0f}s"
                )
            time.sleep(pause)
            continue
        if transient_error(last_error) and interrupted < TRANSIENT_WAITS:
            with run.attempt_lock:
                run.attempt_counts[key] = max(
                    run.attempt_counts.get(key, 1) - 1, 0
                )
            interrupted += 1
            pause = TRANSIENT_PAUSE * interrupted + random.uniform(
                0, TRANSIENT_PAUSE
            )
            if report is not None:
                report(
                    f"service error ({interrupted} of {TRANSIENT_WAITS}), "
                    f"{one_line(last_error or '')[:80]}; waiting {pause:.0f}s"
                )
            time.sleep(pause)
            continue
        semantic = bool(last_error) and (
            last_error.startswith("ValueError:")
            or last_error.startswith("ValidationError:")
        )
        if semantic:
            normalized = one_line(last_error or "")
            with run.attempt_lock:
                history = run.attempt_errors.setdefault(key, [])
                history.append(normalized)
                repeated = 1
                for previous in reversed(history[:-1]):
                    if previous != normalized:
                        break
                    repeated += 1
                if repeated >= REPEATED_SEMANTIC_ERROR_LIMIT:
                    run.blocked_items.add(key)
            if repeated >= REPEATED_SEMANTIC_ERROR_LIMIT:
                raise RuntimeError(
                    f"same semantic validation error repeated {repeated} times; "
                    f"stopping calls for this item: {normalized}"
                )
        if attempt < MAX_ATTEMPTS:
            if report is not None:
                report(
                    f"attempt {attempt} of {MAX_ATTEMPTS} failed, "
                    f"{one_line(last_error or 'no reason given')}; "
                    f"asking again in {2**attempt}s"
                )
            time.sleep(2**attempt)
        else:
            message = (
                f"run-wide retry budget exhausted after {attempt} attempts: {last_error}"
            )
            if _transient_only(run, key, last_error):
                message += f" ({TRANSIENT_ONLY})"
            raise RuntimeError(message)


def _transient_only(run: Run, key: tuple[str, str], last_error: str | None) -> bool:
    history = run.attempt_errors.get(key) or []
    if history:
        return False
    return transient_error(last_error)


def rate_limited(error: str | None) -> bool:
    """Whether the service asked for fewer simultaneous calls."""
    return bool(error) and (
        "RESOURCE_EXHAUSTED" in error or " 429 " in error or "429:" in error
    )


TRANSIENT_ERROR = re.compile(
    r"^(?:ServerError:\s*(?:500|502|503|504)\b"
    r"|\w*(?:Timeout|TimeoutError)\b"
    r"|(?:ConnectError|RemoteProtocolError|ConnectionResetError|ConnectionError)\b)"
    r"|DEADLINE_EXCEEDED|\bUNAVAILABLE\b|Server disconnected|Connection reset"
)


def transient_error(error: str | None) -> bool:
    """Whether the call failed in the service or network, not in the reply.

    Such a failure says nothing about the prompt, so it does not use one of
    the item's attempts; it has its own, separately capped, backoff.
    """
    if not error or error.startswith(("ValueError:", "ValidationError:")):
        return False
    return bool(TRANSIENT_ERROR.search(error))


def _stored_fragment_for_validation(document: dict) -> dict:
    observations = []
    units = []
    for row in document.get("observations") or []:
        if row.get("kind") == "unit":
            units.append(
                {
                    "english": row.get("source_plain") or row.get("source_math") or "",
                    "arabic": row.get("math_text") or row.get("plain_lemma") or "",
                    "status": row.get("status") or "inferred",
                    "also": row.get("also") or [],
                    "abbreviation_only": bool(row.get("abbreviation_only")),
                    "basis": row.get("basis") or "",
                    "evidence": row.get("evidence") or [],
                }
            )
        else:
            observations.append(row)
    return {"section": document.get("section"), "observations": observations, "units": units}


def _validate_stored_fragment(section, inventory, document, sources, notation: str) -> dict:
    source_pages = {row["source_id"]: int(row["pages"]) for row in sources}
    return validate_terminology_fragment(
        str(section["number"]),
        inventory,
        _stored_fragment_for_validation(document),
        source_pages=source_pages,
        notation=notation,
    )


def extract_terminology(
    run: Run,
    section: dict,
    title: str,
    module_path: str,
    pdfs: list[Path],
    inventory: list[dict[str, Any]],
    snippets: list[str],
    fragment_path: Path,
    *,
    thinking: str | None = None,
    split_pdfs: bool = False,
    attempt_scope: str = "",
) -> dict:
    """One terminology call, or one call per PDF when ``split_pdfs`` is set."""
    system = system_prompt(run, "terminology.md")
    notation = str(run.ar_meta.get("notation") or "")
    sources = pdf_source_manifest(pdfs)
    level = thinking or run.thinking_by_step.get("terminology", run.thinking)

    def one_call(call_pdfs: list[Path], item: str, scope: str) -> TerminologyReply:
        call_sources = pdf_source_manifest(call_pdfs)
        source_pages = {row["source_id"]: int(row["pages"]) for row in call_sources}
        parts = [
            profile_heading(run),
            f"Arabic title: {title}\nArabic module_path: {module_path}\nSection: {section['number']}",
            "Attached Arabic source manifest JSON:\n"
            + json.dumps(call_sources, ensure_ascii=False),
            "Ordered terminology/symbol inventory JSON (return one observation per row, same order):\n"
            + json.dumps(inventory, ensure_ascii=False),
            "English unit evidence (snippets from this section's English exercises, spec and figures):\n"
            + json.dumps(snippets, ensure_ascii=False),
        ]
        uploaded_parts, uploaded = upload_pdfs(
            run.client, call_pdfs, section=section["number"], progress=run.progress
        )

        def check(result: TerminologyReply) -> None:
            _require_terminology(result)
            if result.status == "abort":
                return
            validate_terminology_fragment(
                str(section["number"]),
                inventory,
                result.map_fragment.model_dump(),
                source_pages=source_pages,
                notation=notation,
            )

        try:
            return request_reply(
                run,
                prompt="",
                parts=[*parts, *uploaded_parts],
                schema=TerminologyReply,
                item=item,
                step=TERMINOLOGY_STEP,
                context={
                    "book_id": run.ar_id,
                    "section": section["number"],
                    "profile": Profile.of(run.ar_meta).describe(),
                    "prompt_files": list(system.parts),
                    "scope": scope,
                },
                extra_files={
                    "source_manifest.json": json.dumps(call_sources, ensure_ascii=False, indent=2),
                    "map_inventory.json": json.dumps(inventory, ensure_ascii=False, indent=2),
                    "english_snippets.json": json.dumps(snippets, ensure_ascii=False, indent=2),
                },
                attached_paths=call_pdfs,
                check=check,
                report=lambda message: run.progress.say(f"  {section['number']}: {message}"),
                attempt_scope=scope,
                system_instruction=system.text,
                cache_task="terminology",
                thinking=level,
                timeout_ms=TERMINOLOGY_TIMEOUT_MS,
                max_output_tokens=TERMINOLOGY_MAX_OUTPUT_TOKENS,
            )
        finally:
            delete_uploads(run.client, uploaded)

    if split_pdfs:
        validated_parts = []
        for index, pdf in enumerate(pdfs, start=1):
            reply = one_call([pdf], f"{section['number']}_pdf{index}", f"{attempt_scope}split")
            if reply.status == "abort" or reply.map_fragment is None:
                raise RuntimeError(
                    f"{section['number']}: terminology aborted on {pdf.name}: "
                    f"{reply.abort_reason or 'no fragment'}"
                )
            fragment = validate_terminology_fragment(
                str(section["number"]),
                inventory,
                reply.map_fragment.model_dump(),
                source_pages={"source_1": len(PdfReader(pdf).pages)},
                notation=notation,
            )
            real_id = sources[index - 1]["source_id"]
            for row in fragment["observations"]:
                for citation in row.get("evidence") or []:
                    if citation.get("source_id") == "source_1":
                        citation["source_id"] = real_id
            validated_parts.append(fragment)
        fragment = {
            "schema_version": MAP_SCHEMA_VERSION,
            "section": str(section["number"]),
            "inventory": inventory,
            "observations": join_split_fragments(validated_parts),
        }
    else:
        reply = one_call(pdfs, str(section["number"]), attempt_scope)
        if reply.status == "abort" or reply.map_fragment is None:
            raise RuntimeError(
                f"{section['number']}: terminology aborted: "
                f"{reply.abort_reason or 'no fragment'}"
            )
        fragment = validate_terminology_fragment(
            str(section["number"]),
            inventory,
            reply.map_fragment.model_dump(),
            source_pages={row["source_id"]: int(row["pages"]) for row in sources},
            notation=notation,
        )
    document = {
        **fragment,
        "book_id": run.ar_id,
        "profile": run.ar_meta,
        "input_fingerprint": terminology_key(run, section, inventory, sources, snippets),
        "sources": sources,
        "model": run.model,
        "thinking_level": level,
        "prompt_files": list(system.parts),
        "english_snippets_fp": json_fingerprint(snippets),
    }
    write_json(fragment_path, document)
    return document


def ensure_terminology(
    run: Run,
    section: dict,
    drawings: dict,
    *,
    force: bool = False,
    allow_translate: bool = True,
) -> dict[str, Any]:
    """Return the section's terminology fragment, running the failure ladder when needed."""
    number = section["number"]
    stem = section["stem"]
    title, module_path = ar_identity(run.ar_id, number)
    pdfs = section_source_pdfs(run.ar_id, number, run.pad_width)
    missing = [path.name for path in pdfs if not path.is_file()]
    if missing:
        raise RuntimeError(
            f"{number}: Arabic section PDF(s) missing ({', '.join(missing)}); "
            f"run sources.py on {run.ar_id} first"
        )
    en_exercises_path = OUTPUT_DIR / run.en_id / ACCEPTED_STAGE / f"{stem}.json"
    if not en_exercises_path.is_file():
        raise RuntimeError(f"{number}: no English accepted exercises at {en_exercises_path}")
    from src.english_json import format_english_hits, read_english_json

    try:
        en_document, hits = read_english_json(en_exercises_path)
    except ValueError as error:
        raise RuntimeError(str(error)) from error
    for line in format_english_hits(run.en_id, en_exercises_path, hits):
        print(line)
    inventory = section_inventory(section, en_document, drawings)
    snippets = english_snippets(section, en_document, drawings, limit=ENGLISH_SNIPPET_CHARS)
    sources = pdf_source_manifest(pdfs)
    key = terminology_key(run, section, inventory, sources, snippets)
    fragment_path = map_fragment_path(run.ar_id, stem)
    notation = str(run.ar_meta.get("notation") or "")

    def prepared(document: dict, line: str) -> dict[str, Any]:
        return {
            "section": section,
            "title": title,
            "module_path": module_path,
            "en_document": en_document,
            "inventory": inventory,
            "fragment": document,
            "line": line,
        }

    if not force and fragment_path.is_file():
        try:
            document = json.loads(fragment_path.read_text(encoding="utf-8"))
            if document.get("input_fingerprint") != key:
                raise ValueError("terminology fragment is stale")
            _validate_stored_fragment(section, inventory, document, sources, notation)
            return prepared(document, f"  {number}: terminology already on disk")
        except (KeyError, OSError, TypeError, ValueError):
            pass
    if not allow_translate:
        raise RuntimeError(
            f"{number}: terminology fragment is missing or stale; run --map-only first"
        )
    try:
        document = extract_terminology(
            run, section, title, module_path, pdfs, inventory, snippets, fragment_path
        )
    except RuntimeError as first_error:
        try:
            document = extract_terminology(
                run,
                section,
                title,
                module_path,
                pdfs,
                inventory,
                snippets,
                fragment_path,
                split_pdfs=True,
                attempt_scope="split",
            )
        except RuntimeError:
            try:
                document = extract_terminology(
                    run,
                    section,
                    title,
                    module_path,
                    pdfs,
                    inventory,
                    snippets,
                    fragment_path,
                    thinking=run.escalation_thinking,
                    attempt_scope="high",
                )
            except RuntimeError as last_error:
                raise RuntimeError(
                    f"{number}: terminology: {ESCALATION_FAILED}: {last_error}"
                ) from first_error
    return prepared(document, f"  {number}: extracted terminology")


def spec_problem_field(problem: str) -> str:
    """The spec path a checker message names."""
    head = problem.split(":", 1)[0]
    if " field " in head:
        head = head.split(" field ", 1)[0]
    return head.strip()


def _drop_rows(rows: list[dict]) -> tuple[dict[str, list[str]], dict[str, int]]:
    by_field: dict[str, list[str]] = {}
    for row in rows:
        field_name = str(row.get("field") or "")
        by_field.setdefault(field_name, []).append(str(row.get("token") or ""))
    return by_field, {field_name: len(tokens) for field_name, tokens in by_field.items()}


def spec_translation_problems(
    source_spec: dict,
    reply_spec: dict,
    meta: dict,
    slots: dict,
    dropped_rows: list[dict],
) -> list[str]:
    """Token checks, then the same restored checks the exercises use."""
    by_field, counts = _drop_rows(dropped_rows)
    present = dict(iter_spec_strings(reply_spec))
    problems: list[str] = []
    for path, slot in slots.items():
        problems.extend(
            token_problems(slot, present.get(path, ""), path, dropped=by_field.get(path, []))
        )
    if problems:
        return problems
    restored = restore_spec(reply_spec, slots)
    return spec_problems(source_spec, restored, meta, dropped=counts)


def _spec_structure_problems(source: dict, target: dict) -> list[str]:
    problems: list[str] = []
    if "overview" not in target:
        problems.append("overview: translated field is missing")
    for name in SPEC_LIST_FIELDS:
        source_rows = list(source.get(name) or [])
        target_rows = target.get(name)
        if not isinstance(target_rows, list):
            problems.append(f"{name}: translated value is not a list")
            continue
        if len(source_rows) != len(target_rows):
            problems.append(
                f"{name}: translated {len(target_rows)} row(s), expected {len(source_rows)}"
            )
        for before, after in zip(source_rows, target_rows):
            if not isinstance(after, dict):
                problems.append(f"{before.get('id')}: translated row is not an object")
                continue
            if before.get("id") != after.get("id"):
                problems.append(
                    f"{before.get('id')}: id is {after.get('id')!r}, expected {before.get('id')!r}"
                )
    return problems


def translate_spec(
    run: Run,
    section: dict,
    title: str,
    module_path: str,
    out_path: Path,
) -> dict:
    """Translate one section spec. No PDFs. The glossary is in the system instruction."""
    started = time.perf_counter()
    system = system_prompt(run, "specs.md")
    key = spec_key(run, section)
    source_fp = spec_source_fp(run, section)
    if out_path.is_file() and not run.update:
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
            if spec_file_reusable(
                existing,
                key,
                source_fp,
                update=run.update,
                retranslate_on_prompt_change=run.retranslate_on_prompt_change,
            ):
                return existing
        except (OSError, ValueError):
            pass
    source_projection = translatable_spec(section["spec"])
    notation = str(run.ar_meta.get("notation") or "latin")
    protected, slots = protect_spec(source_projection, notation=notation)
    parts = [
        profile_heading(run),
        f"Arabic title: {title}\nArabic module_path: {module_path}",
        "English translatable spec JSON (placeholders protected):\n"
        + json.dumps(protected, ensure_ascii=False),
    ]
    run.progress.asking(section["number"], "spec, asking the model")

    def check(result: SpecOnlyReply) -> None:
        _require_spec_only(result)
        if result.status == "abort" or result.spec is None:
            return
        structure = _spec_structure_problems(source_projection, result.spec.model_dump())
        if structure:
            raise ValueError("; ".join(structure[:6]))
        for index, pair in enumerate(result.terminology):
            if not pair.english.strip() or not pair.arabic.strip():
                raise ValueError(f"terminology[{index}] has an empty side")
            problems = text_problems(
                pair.english,
                pair.arabic,
                f"terminology[{index}]",
                run.ar_meta,
                math_policy="none",
            )
            if problems:
                raise ValueError("; ".join(problems))

    reply = request_reply(
        run,
        prompt="",
        parts=parts,
        schema=SpecOnlyReply,
        item=section["number"],
        step=SPEC_STEP,
        context={
            "book_id": run.ar_id,
            "section": section["number"],
            "profile": Profile.of(run.ar_meta).describe(),
            "prompt_files": list(system.parts),
        },
        extra_files={
            "english_spec.json": json.dumps(section["spec"], ensure_ascii=False, indent=2),
            "english_spec_protected.json": json.dumps(protected, ensure_ascii=False, indent=2),
        },
        check=check,
        report=lambda message: run.progress.say(f"  {section['number']}: {message}"),
        system_instruction=system.text,
        cache_task="spec",
        thinking=run.thinking_by_step.get("spec", run.thinking),
        timeout_ms=SPEC_TIMEOUT_MS,
    )
    if reply.status == "abort" or reply.spec is None:
        raise RuntimeError(
            f"{section['number']}: spec: {reply.abort_reason or 'no reason given'}"
        )
    reply_spec = reply.spec.model_dump()
    dropped_rows = [row.model_dump() for row in reply.dropped_numbers]
    problems = spec_translation_problems(
        section["spec"], reply_spec, run.ar_meta, slots, dropped_rows
    )
    protected_text = dict(iter_spec_strings(protected))
    for round_index in range(1, MAX_SALVAGE_ROUNDS + 1):
        if not problems:
            break
        failing = []
        for problem in problems:
            field_name = spec_problem_field(problem)
            if field_name in protected_text and field_name not in failing:
                failing.append(field_name)
        if not failing:
            break
        run.progress.say(
            f"  {section['number']}: spec salvage {round_index} ({len(failing)} field(s))"
        )
        salvage_parts = [
            profile_heading(run),
            "These fields failed the placeholder check. Return only these fields, "
            "still carrying every placeholder, plus dropped_numbers for any number "
            "you omit from them.\n"
            + "\n".join(problems[:8]),
            "English fields JSON:\n"
            + json.dumps(
                {field_name: protected_text[field_name] for field_name in failing},
                ensure_ascii=False,
            ),
        ]

        def salvage_check(result: SpecSalvageReply) -> None:
            if result.status == "abort":
                return
            unknown = [row.field for row in result.fields if row.field not in failing]
            if unknown:
                raise ValueError("unknown spec field " + ", ".join(unknown[:4]))

        salvage = request_reply(
            run,
            prompt="",
            parts=salvage_parts,
            schema=SpecSalvageReply,
            item=section["number"],
            step=SPEC_STEP,
            context={
                "book_id": run.ar_id,
                "section": section["number"],
                "profile": Profile.of(run.ar_meta).describe(),
                "prompt_files": list(system.parts),
                "scope": f"salvage{round_index}",
            },
            check=salvage_check,
            report=lambda message: run.progress.say(f"  {section['number']}: {message}"),
            attempt_scope=f"salvage{round_index}",
            system_instruction=system.text,
            cache_task="spec",
            thinking=run.thinking_by_step.get("spec", run.thinking),
            timeout_ms=SPEC_TIMEOUT_MS,
        )
        if salvage.status == "abort":
            break
        set_spec_strings(reply_spec, {row.field: row.text for row in salvage.fields})
        replaced = {row.field for row in salvage.fields}
        dropped_rows = [
            row for row in dropped_rows if str(row.get("field") or "") not in replaced
        ]
        dropped_rows.extend(row.model_dump() for row in salvage.dropped_numbers)
        problems = spec_translation_problems(
            section["spec"], reply_spec, run.ar_meta, slots, dropped_rows
        )
    if problems:
        raise RuntimeError(
            f"{section['number']}: spec: " + "; ".join(problems[:6])
        )
    _by_field, drop_counts = _drop_rows(dropped_rows)
    restored = restore_spec(reply_spec, slots)
    vector_lesson = section_is_vector_lesson(section.get("spec") or section)
    translated = strict_merge_spec(
        section["spec"], restored, run.ar_meta, dropped=drop_counts
    )
    document = envelope(
        run.ar_id,
        run.ar_meta,
        section["number"],
        title,
        module_path,
        spec=translated,
        translation={
            "terminology": [row.model_dump() for row in reply.terminology],
            "glossary_fingerprint": run.book_map.get("semantic_fingerprint", ""),
            "spec_key": key,
            "spec_source_fp": source_fp,
        },
    )
    document = canonicalize_document(canonicalize_tree(document, vector_lesson=vector_lesson))
    write_json(out_path, document)
    run.progress.say(f"  {section['number']}: spec ({since(time.perf_counter() - started)})")
    return document


def ensure_spec(run: Run, section: dict, prepared: dict) -> dict:
    out_path = OUTPUT_DIR / run.ar_id / SPECS_STAGE / f"{section['stem']}.json"
    document = translate_spec(
        run,
        section,
        prepared["title"],
        prepared["module_path"],
        out_path,
    )
    prepared["spec_document"] = document
    prepared["terminology"] = list((document.get("translation") or {}).get("terminology") or [])
    return document



def translate_labels(
    run: Run,
    section: dict,
    title: str,
    module_path: str,
    ar_spec: dict,
    en_labels: dict,
    out_path: Path,
) -> str:
    started = time.perf_counter()
    ids = [row["id"] for row in en_labels["labels"]["objectives"]]
    source_labels = {
        row["id"]: str(row.get("label") or "")
        for row in en_labels["labels"]["objectives"]
    }
    system = system_prompt(run, "labels.md")
    payload = (
        f"{profile_heading(run)}\n\n"
        f"Arabic spec JSON:\n{json.dumps(ar_spec, ensure_ascii=False)}\n\n"
        f"English labels JSON:\n{json.dumps(en_labels['labels'], ensure_ascii=False)}"
    )
    run.progress.asking(section["number"], "labels, asking the model")

    def check(result: LabelTranslateReply) -> None:
        if result.status == "abort":
            return
        problems = label_problems(result.objectives, ids)
        if problems:
            raise ValueError("; ".join(problems))
        for row in result.objectives:
            problems = text_problems(
                source_labels[row.id],
                row.label,
                row.id,
                run.ar_meta,
                math_policy="none",
            )
            if problems:
                raise ValueError("; ".join(problems))

    reply = request_reply(
        run,
        prompt="",
        parts=[payload],
        schema=LabelTranslateReply,
        item=section["number"],
        step=LABEL_STEP,
        context={
            "book_id": run.ar_id,
            "section": section["number"],
            "profile": Profile.of(run.ar_meta).describe(),
            "prompt_files": list(system.parts),
        },
        extra_files={
            "arabic_spec.json": json.dumps(ar_spec, ensure_ascii=False, indent=2),
            "english_labels.json": json.dumps(
                en_labels["labels"], ensure_ascii=False, indent=2
            ),
        },
        check=check,
        report=lambda message: run.progress.say(f"  {section['number']}: {message}"),
        system_instruction=system.text,
        cache_task="label",
        thinking=run.thinking_by_step.get("label", run.thinking),
    )
    if reply.status == "abort":
        raise RuntimeError(
            f"{section['number']}: label translation aborted: "
            f"{reply.abort_reason or 'no reason given'}"
        )
    by_id = {
        row.id: clean_translated_text(row.label.strip(), row.id)
        for row in reply.objectives
    }
    labels = {"objectives": [{"id": name, "label": by_id[name]} for name in ids]}
    write_json(
        out_path,
        envelope(
            run.ar_id,
            run.ar_meta,
            section["number"],
            title,
            module_path,
            labels=labels,
        ),
    )
    return f"  {section['number']}: labels ({since(time.perf_counter() - started)})"


def copy_budget(
    run: Run,
    section: dict,
    title: str,
    module_path: str,
    en_path: Path,
    out_path: Path,
) -> str:
    document = json.loads(en_path.read_text(encoding="utf-8"))
    write_json(
        out_path,
        envelope(
            run.ar_id,
            run.ar_meta,
            section["number"],
            title,
            module_path,
            budget=document["budget"],
        ),
    )
    return f"  {section['number']}: budget copied"


def chunk_objective_groups(en_groups: list[dict], notation: str) -> list[list[dict]]:
    """Contiguous chunks whose protected JSON stays within OBJECTIVE_BATCH_CHARS."""
    chunks: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for group in en_groups:
        protected, _slots = protect_group(group, notation=notation)
        group_size = len(json.dumps(protected, ensure_ascii=False))
        if current and size + group_size > OBJECTIVE_BATCH_CHARS:
            chunks.append(current)
            current = []
            size = 0
        current.append(group)
        size += group_size
    if current:
        chunks.append(current)
    return chunks


def _meta_with_glossary(run: Run) -> dict:
    meta = dict(run.ar_meta)
    from src.book_map import glossary_entries

    words: set[str] = set()
    for entry in glossary_entries(run.book_map):
        for token in str(entry.get("arabic") or "").split():
            if token.isascii():
                words.add(token)
    meta["_latin_glossary_words"] = frozenset(words)
    return meta


def _drop_counts(drops: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in drops:
        field_name = str(row.get("field") or "")
        counts[field_name] = counts.get(field_name, 0) + 1
    return counts


def exercise_output_problems(group: dict, notation: str) -> list[str]:
    """Leftover Latin function commands, after mathematics is restored.

    Number–noun agreement is not in this list. A hint never fails a group.
    """
    problems: list[str] = []
    for text in group_print_strings(group):
        problems.extend(latin_function_output_problems(text, notation=notation))
    return problems


def exercise_agreement_hints(group: dict) -> list[str]:
    """Neutral pointers. They name no Arabic form and do not fail the group."""
    from review_probe import unit_agreement_messages

    hints: list[str] = []
    for text in group_print_strings(group):
        hints.extend(unit_agreement_messages(text))
    return hints


def _placeholder_table(slots_by_id: dict[str, Slots]) -> str:
    """``⟦M6⟧ = $13$`` for every placeholder in the protected English."""
    lines: list[str] = []
    for location, slots in slots_by_id.items():
        values = (
            list(slots.frozen.items())
            + list(slots.editable.items())
            + list(slots.numbers.items())
        )
        for key, value in values:
            lines.append(f"{location}: ⟦{key}⟧ = {value}")
    return "\n".join(lines)


def _arabic_for_correction(group: dict) -> dict:
    """The current Arabic the reviewer saw, with mathematics restored."""

    def figures(rows: list) -> list[dict]:
        return [
            {"description": str((figure or {}).get("description") or "")}
            for figure in rows
            if isinstance(figure, dict)
        ]

    exercises = []
    for exercise in group.get("exercises") or []:
        if not isinstance(exercise, dict):
            continue
        exercises.append(
            {
                "id": exercise.get("id"),
                "text": str(exercise.get("text") or ""),
                "options": [str(item) for item in (exercise.get("options") or [])],
                "answer": str(exercise.get("answer") or ""),
                "figures": figures(list(exercise.get("figures") or [])),
            }
        )
    return {
        "id": group.get("id"),
        "stem": str(group.get("stem") or ""),
        "figures": figures(list(group.get("figures") or [])),
        "exercises": exercises,
    }


def _correction_block(
    corrections: dict[str, str],
    slots_by_id: dict[str, dict[str, Slots]],
    current_arabic: dict[str, dict] | None,
) -> str:
    """A patch request: current Arabic, every placeholder's value, flagged spans only."""
    blocks: list[str] = []
    for gid, text in corrections.items():
        slots = slots_by_id.get(gid) or {}
        table = _placeholder_table(slots)
        arabic = (current_arabic or {}).get(gid)
        if arabic:
            shown = json.dumps(_arabic_for_correction(arabic), ensure_ascii=False, indent=2)
            blocks.append(
                f"### {gid}\n"
                "Current Arabic (mathematics restored):\n"
                f"{shown}\n\n"
                "Placeholder values:\n"
                f"{table}\n\n"
                "Flagged issues:\n"
                f"{text}\n\n"
                "Patch: change only the flagged fields and spans. "
                "Keep everything else word for word. "
                "Return the complete group, copying every placeholder unchanged."
            )
        else:
            blocks.append(f"### {gid}\n{text}")
    tail = (
        "\nReturn every named group complete."
        if not current_arabic
        else ""
    )
    return "Mandatory corrections for this replacement:\n" + "\n\n".join(blocks) + tail


def _remember_first_path(existing: dict, taken: str) -> str:
    """The path of the first translation. A later correction must not replace it."""
    stored = existing.get("first_path")
    if stored and stored != "correction":
        return str(stored)
    previous = existing.get("path")
    if previous and previous != "correction":
        return str(previous)
    return taken if taken != "correction" else "batch"


def translate_objective(
    run: Run,
    section: dict,
    en_spec: dict,
    ar_spec: dict,
    terminology: list[dict],
    objective: str,
    en_groups: list[dict],
    *,
    corrections: dict[str, str] | None = None,
    current_arabic: dict[str, dict] | None = None,
    attempt_scope: str = "",
    part: int | None = None,
    thinking: str | None = None,
) -> tuple[dict[str, dict], dict[str, str], dict[str, list[dict]]]:
    """Return merged groups, salvageable errors, and dropped_numbers by id."""
    item = f"{section['number']}_{objective or 'objective_none'}"
    if part:
        item += f"_part{part}"
    notation = str(run.ar_meta.get("notation") or "latin")
    remaining: list[dict] = []
    errors: dict[str, str] = {}
    for group in en_groups:
        message = english_source_untranslatable(group, f"{item}/{group['id']}")
        if message:
            errors[str(group["id"])] = message
        else:
            remaining.append(group)
    merged: dict[str, dict] = {}
    dropped_by_id: dict[str, list[dict]] = {}
    if not remaining:
        return merged, errors, dropped_by_id
    context = terminology_context(
        en_spec, ar_spec, terminology, objective
    )
    protected_rows = []
    slots_by_id: dict[str, dict] = {}
    for group in remaining:
        protected, slots = protect_group(group, notation=notation)
        protected_rows.append(protected)
        slots_by_id[str(group["id"])] = slots
    parts = [
        profile_heading(run),
        "Arabic objective context JSON:\n" + json.dumps(context, ensure_ascii=False),
        "English groups JSON (placeholders protected):\n"
        + json.dumps(protected_rows, ensure_ascii=False),
    ]
    if corrections:
        parts.append(_correction_block(corrections, slots_by_id, current_arabic))
    requested = {str(group["id"]): group for group in remaining}
    system = system_prompt(run, "exercises.md")
    meta = _meta_with_glossary(run)

    def check(result: ObjectiveTranslateReply) -> None:
        _require_objective(result)
        if result.status == "abort":
            return
        seen: set[str] = set()
        for row in result.groups:
            if row.id in seen or row.id not in requested:
                raise ValueError(f"duplicate or unknown group id {row.id}")
            seen.add(row.id)
        for row in result.aborted:
            if row.id not in requested:
                raise ValueError(f"aborted unknown group id {row.id}")

    run.progress.asking(section["number"], f"{objective or 'objective_none'} ({len(remaining)} groups), asking the model")
    try:
        reply = request_reply(
            run,
            prompt="",
            parts=parts,
            schema=ObjectiveTranslateReply,
            item=item,
            step=EXERCISE_STEP,
            context={
                "book_id": run.ar_id,
                "section": section["number"],
                "objective": objective,
                "groups": [str(group["id"]) for group in remaining],
                "scope": attempt_scope,
                "profile": Profile.of(run.ar_meta).describe(),
                "prompt_files": list(system.parts),
            },
            extra_files={
                "translation_context.json": json.dumps(context, ensure_ascii=False, indent=2),
                "english_groups.json": json.dumps(protected_rows, ensure_ascii=False, indent=2),
                "english_groups_source.json": json.dumps(
                    [translatable_group(group) for group in remaining],
                    ensure_ascii=False,
                    indent=2,
                ),
            },
            check=check,
            report=lambda message: run.progress.say(f"  {item}: {message}"),
            attempt_scope=attempt_scope,
            system_instruction=system.text,
            cache_task="exercise",
            thinking=thinking or run.thinking_by_step.get("exercise", run.thinking),
            timeout_ms=OBJECTIVE_CALL_TIMEOUT_MS,
            max_output_tokens=OBJECTIVE_MAX_OUTPUT_TOKENS,
        )
    except RuntimeError as error:
        message = str(error)
        for group in remaining:
            errors[str(group["id"])] = message
        return merged, errors, dropped_by_id
    if reply.status == "abort":
        reason = reply.abort_reason or "no reason given"
        terminal = source_corruption_abort(reason)
        for group in remaining:
            gid = str(group["id"])
            errors[gid] = (
                f"{item}/{gid}: {UNTRANSLATABLE_SOURCE}: {reason}"
                if terminal
                else f"{item}/{gid}: {reason}"
            )
        return merged, errors, dropped_by_id
    returned = {row.id: row for row in reply.groups}
    aborted = {row.id: row.reason for row in reply.aborted}
    for gid, en_group in requested.items():
        if gid in aborted:
            reason = aborted[gid]
            errors[gid] = (
                f"{item}/{gid}: {UNTRANSLATABLE_SOURCE}: {reason}"
                if source_corruption_abort(reason)
                else reason
            )
            continue
        row = returned.get(gid)
        if row is None:
            errors[gid] = f"{gid}: missing from reply"
            continue
        dumped = row.model_dump()
        drops = dumped.get("dropped_numbers") or []
        by_field: dict[str, list[str]] = {}
        for drop in drops:
            by_field.setdefault(str(drop.get("field") or ""), []).append(str(drop.get("token") or ""))
        problems: list[str] = []
        for location, slots in slots_by_id[gid].items():
            field_text = _group_location_text(dumped, location)
            problems.extend(
                token_problems(slots, field_text, location, dropped=by_field.get(location, []))
            )
        if problems:
            errors[gid] = "; ".join(problems[:3])
            continue
        restored = restore_group(dumped, slots_by_id[gid])
        output_problems = exercise_output_problems(restored, notation)
        if output_problems:
            errors[gid] = "; ".join(output_problems[:6])
            continue
        try:
            merged[gid] = merge_group(
                en_group,
                restored,
                meta,
                run.book_map,
                spec=en_spec,
                dropped=_drop_counts(drops),
            )
            dropped_by_id[gid] = drops
        except ValueError as error:
            errors[gid] = str(error)
    return merged, errors, dropped_by_id


def _group_location_text(group: dict, location: str) -> str:
    if location == "stem":
        return str(group.get("stem") or "")
    if location.startswith("figures["):
        index = int(location.split("[", 1)[1].split("]", 1)[0])
        figures = group.get("figures") or []
        figure = figures[index] if index < len(figures) else {}
        return str((figure or {}).get("description") or "")
    exercise_id, _, rest = location.partition(".")
    for exercise in group.get("exercises") or []:
        if str(exercise.get("id") or "") != exercise_id:
            continue
        if rest == "text":
            return str(exercise.get("text") or "")
        if rest == "answer":
            return str(exercise.get("answer") or "")
        if rest.startswith("options["):
            index = int(rest.split("[", 1)[1].split("]", 1)[0])
            options = exercise.get("options") or []
            return str(options[index] if index < len(options) else "")
        if rest.startswith("figures["):
            index = int(rest.split("[", 1)[1].split("]", 1)[0])
            figures = exercise.get("figures") or []
            figure = figures[index] if index < len(figures) else {}
            return str((figure or {}).get("description") or "")
    return ""


def translate_objective_batch(
    run: Run,
    section: dict,
    en_spec: dict,
    ar_spec: dict,
    terminology: list[dict],
    objective: str,
    en_groups: list[dict],
    *,
    corrections: dict[str, str] | None = None,
    current_arabic: dict[str, dict] | None = None,
    scope: str = "",
    part: int | None = None,
    escalate: bool = True,
) -> tuple[dict[str, dict], dict[str, str], dict[str, list[dict]], dict[str, str]]:
    """Return merged groups, terminal errors, dropped numbers, and the path each group took."""
    merged, errors, dropped = translate_objective(
        run,
        section,
        en_spec,
        ar_spec,
        terminology,
        objective,
        en_groups,
        corrections=corrections,
        current_arabic=current_arabic,
        attempt_scope=scope,
        part=part,
    )
    paths = {gid: ("correction" if corrections else "batch") for gid in merged}
    by_id = {str(group["id"]): group for group in en_groups}
    for round_index in range(1, MAX_SALVAGE_ROUNDS + 1):
        retry = [
            by_id[gid]
            for gid, message in errors.items()
            if gid in by_id and not terminal_section_error(message)
        ]
        if not retry:
            break
        truncated = any("truncated reply" in errors.get(str(group["id"]), "") for group in retry)
        slices = [retry[: len(retry) // 2], retry[len(retry) // 2 :]] if truncated and len(retry) > 1 else [retry]
        labels = [f"{scope}salvage{round_index}a", f"{scope}salvage{round_index}b"] if len(slices) == 2 else [f"{scope}salvage{round_index}"]
        for groups, label in zip(slices, labels):
            if not groups:
                continue
            subset_corrections = {
                str(group["id"]): errors.get(str(group["id"]), "")
                for group in groups
            }
            if corrections:
                for gid, text in corrections.items():
                    if gid in subset_corrections:
                        subset_corrections[gid] = text + "\n" + subset_corrections[gid]
            got, still, drops = translate_objective(
                run,
                section,
                en_spec,
                ar_spec,
                terminology,
                objective,
                groups,
                corrections=subset_corrections,
                current_arabic=current_arabic,
                attempt_scope=label,
            )
            merged.update(got)
            dropped.update(drops)
            for gid in got:
                errors.pop(gid, None)
                paths[gid] = f"salvage{round_index}"
            errors.update(still)
    if escalate:
        for gid, message in list(errors.items()):
            if gid not in by_id or terminal_section_error(message):
                continue
            solo_groups = [by_id[gid]]
            got, still, drops = translate_objective(
                run,
                section,
                en_spec,
                ar_spec,
                terminology,
                objective,
                solo_groups,
                corrections={gid: message[:4000]},
                current_arabic=current_arabic,
                attempt_scope=f"{scope}solo",
                thinking=run.thinking_by_step.get("exercise", run.thinking),
            )
            if gid in got:
                merged[gid] = got[gid]
                dropped[gid] = drops.get(gid, [])
                errors.pop(gid, None)
                paths[gid] = "solo"
                continue
            high_error = still.get(gid, message)
            got, still, drops = translate_objective(
                run,
                section,
                en_spec,
                ar_spec,
                terminology,
                objective,
                solo_groups,
                corrections={gid: high_error[:4000]},
                current_arabic=current_arabic,
                attempt_scope=f"{scope}solohigh",
                thinking=run.escalation_thinking,
            )
            if gid in got:
                merged[gid] = got[gid]
                dropped[gid] = drops.get(gid, [])
                errors.pop(gid, None)
                paths[gid] = "solo_high"
            else:
                errors[gid] = f"{gid}: {ESCALATION_FAILED}: {still.get(gid, high_error)}"
    return merged, errors, dropped, paths


def figure_labels_for_group(group: dict, drawings: dict | None) -> list[dict]:
    """English node bodies of this group's drawings. Empty when nothing is drawn."""
    drawings = drawings or {}
    labels: list[dict] = []

    def take(exercise_id: str | None, index: int, figure: Any) -> None:
        description = figure_description(figure)
        tikz = str((drawings.get(description) or {}).get("tikz") or "")
        if not tikz:
            return
        bodies = [
            match.group(2).strip()
            for match in TIKZ_NODE.finditer(tikz)
            if match.group(2).strip()
        ]
        if not bodies:
            return
        row: dict[str, Any] = {"index": index, "english_labels": bodies}
        if exercise_id:
            row["exercise"] = exercise_id
        labels.append(row)

    for index, figure in enumerate(group.get("figures") or []):
        take(None, index, figure)
    for exercise in group.get("exercises") or []:
        exercise_id = str(exercise.get("id") or "")
        for index, figure in enumerate(exercise.get("figures") or []):
            take(exercise_id, index, figure)
    return labels


def review_batches(
    en_groups: list[dict],
    ar_groups: list[dict],
    dropped_by_id: dict[str, list] | None = None,
    figure_labels_by_id: dict[str, list] | None = None,
) -> list[list[dict]]:
    """Pair groups into review calls below a stable character budget."""
    translated = {str(group.get("id") or ""): group for group in ar_groups}
    dropped_by_id = dropped_by_id or {}
    figure_labels_by_id = figure_labels_by_id or {}
    batches: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0
    for source in en_groups:
        group_id = str(source.get("id") or "")
        pair = {
            "group": group_id,
            "english": translatable_group(source),
            "arabic": translatable_group(translated[group_id]),
            "dropped_numbers": list(dropped_by_id.get(group_id) or []),
            "figure_labels": list(figure_labels_by_id.get(group_id) or []),
        }
        size = len(json.dumps(pair, ensure_ascii=False))
        if current and current_chars + size > REVIEW_BATCH_CHARS:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(pair)
        current_chars += size
    if current:
        batches.append(current)
    return batches


def review_translation(
    run: Run,
    section: dict,
    ar_spec: dict,
    terminology: list[dict],
    en_groups: list[dict],
    ar_groups: list[dict],
    review_round: int,
    objective: str = "",
    dropped_by_id: dict[str, list] | None = None,
    figure_labels_by_id: dict[str, list] | None = None,
) -> list[TranslationIssueV2]:
    """Run the mandatory issue-only bilingual review over one objective's groups."""
    prompt = system_prompt(run, "review.md")
    context = {
        "overview": ar_spec.get("overview") or "",
        "terminology": terminology,
        "equations": ar_spec.get("equations") or [],
        "notations": ar_spec.get("notations") or [],
    }
    issues: list[TranslationIssueV2] = []
    batches = review_batches(
        en_groups, ar_groups, dropped_by_id, figure_labels_by_id
    )
    for batch_number, batch in enumerate(batches, start=1):
        ids = {row["group"] for row in batch}
        exercise_ids = {
            row["group"]: {
                str(exercise.get("id") or "")
                for exercise in row["english"].get("exercises") or []
            }
            for row in batch
        }
        payload = (
            f"{profile_heading(run)}\n\n"
            f"Arabic terminology context JSON:\n"
            f"{json.dumps(context, ensure_ascii=False)}\n\n"
            f"English/Arabic group pairs JSON:\n"
            f"{json.dumps(batch, ensure_ascii=False)}"
        )

        def check(result: TranslationReviewReplyV2) -> None:
            if result.status == "abort":
                return
            for index, issue in enumerate(result.issues):
                if issue.group not in ids:
                    raise ValueError(
                        f"issue {index + 1} names unknown group {issue.group!r}"
                    )
                if issue.exercise and issue.exercise not in exercise_ids[issue.group]:
                    raise ValueError(
                        f"issue {index + 1} names unknown exercise "
                        f"{issue.exercise!r} in {issue.group}"
                    )
                if not issue.field.strip() or not issue.reason.strip():
                    raise ValueError(
                        f"issue {index + 1} has an empty field or reason"
                    )

        reply = request_reply(
            run,
            prompt="",
            parts=[payload],
            schema=TranslationReviewReplyV2,
            system_instruction=prompt.text,
            cache_task="review",
            thinking=run.thinking_by_step.get("review", run.thinking),
            timeout_ms=REVIEW_TIMEOUT_MS,
            item=(
                f"{section['number']}_review_{review_round}_"
                f"{objective or 'objective_none'}_batch_{batch_number}"
            ),
            step=REVIEW_STEP,
            context={
                "book_id": run.ar_id,
                "section": section["number"],
                "review_round": review_round,
                "review_batch": batch_number,
                "groups": sorted(ids),
                "profile": Profile.of(run.ar_meta).describe(),
                "prompt_files": prompt.files,
            },
            extra_files={
                "translation_context.json": json.dumps(
                    context, ensure_ascii=False, indent=2
                ),
                "review_pairs.json": json.dumps(
                    batch, ensure_ascii=False, indent=2
                ),
            },
            check=check,
            report=lambda message: run.progress.say(
                f"  {section['number']} review batch {batch_number}: {message}"
            ),
        )
        if reply.status == "abort":
            raise RuntimeError(
                f"{section['number']}: translation review aborted: "
                f"{reply.abort_reason or 'no reason given'}"
            )
        issues.extend(reply.issues)
    return issues


def issues_by_group(issues: list[TranslationIssue] | list[TranslationIssueV2]) -> dict[str, str]:
    grouped: dict[str, list[str]] = {}
    for issue in issues:
        location = (
            f"{issue.exercise}.{issue.field}"
            if issue.exercise
            else issue.field
        )
        grouped.setdefault(issue.group, []).append(
            f"- {location}: {issue.reason}"
        )
    return {group: "\n".join(rows) for group, rows in grouped.items()}


def write_exercise_documents(
    run: Run,
    section: dict,
    title: str,
    module_path: str,
    en_document: dict,
    groups: list[dict],
    out_path: Path,
) -> dict:
    body = copy.deepcopy(en_document["exercises"])
    body["groups"] = groups
    document = envelope(
        run.ar_id,
        run.ar_meta,
        section["number"],
        title,
        module_path,
        exercises=body,
    )
    recount(document)
    write_json(out_path, canonicalize_document(document))
    return document


def _manifest_drops(manifest_groups: dict) -> dict[str, dict[str, int]]:
    """Each group's declared dropped_numbers, kept for the section check."""
    drops: dict[str, dict[str, int]] = {}
    for gid, stored in manifest_groups.items():
        rows = stored.get("dropped_numbers") if isinstance(stored, dict) else None
        drops[str(gid)] = _drop_map(rows)
    return drops


def _drop_map(rows: list | None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows or []:
        if isinstance(row, dict):
            field_name = str(row.get("field") or "")
        else:
            field_name = str(getattr(row, "field", "") or "")
        if field_name:
            counts[field_name] = counts.get(field_name, 0) + 1
    return counts


def forget_cleared_failures(state: dict, groups: dict | None = None) -> None:
    """Drop failed_groups rows that later validated or reviewed."""
    failed = state.get("failed_groups")
    if not isinstance(failed, dict) or not failed:
        return
    rows = groups if groups is not None else state.get("groups")
    if not isinstance(rows, dict):
        return
    for gid in list(failed):
        row = rows.get(gid)
        if isinstance(row, dict) and row.get("status") in {"reviewed", "validated"}:
            failed.pop(gid, None)


def _select_group(run: Run, en_group: dict, stored: dict | None, kept: dict | None, en_spec: dict, ar_spec: dict) -> str:
    """Return translate, revalidate, review, or keep."""
    keys = group_keys(run, en_group, en_spec, ar_spec)
    meta = _meta_with_glossary(run)
    dropped = _drop_map((stored or {}).get("dropped_numbers"))
    complete = bool(kept) and group_row_complete(
        en_group, kept, meta, spec=en_spec, dropped=dropped or None
    )
    if run.update or not stored or stored.get("source_fp") != keys["source_fp"] or not complete:
        return "translate"
    glossary_changed = (
        stored.get("glossary_fp") != keys["glossary_fp"]
        or stored.get("profile_fp") != keys["profile_fp"]
    )
    if glossary_changed:
        return "revalidate" if complete else "translate"
    if stored.get("prompt_fp") != keys["prompt_fp"]:
        return "translate" if run.retranslate_on_prompt_change else "keep"
    current_review = review_key(run)
    if stored.get("status") == "reviewed" and stored.get("review_fp") == current_review and not run.rereview:
        return "keep"
    if stored.get("status") == "reviewed" and stored.get("review_fp") != current_review and not run.rereview:
        return "keep"
    return "review"


def translate_exercises(
    run: Run,
    section: dict,
    title: str,
    module_path: str,
    en_spec: dict,
    ar_spec: dict,
    terminology: list[dict],
    en_document: dict,
    out_09: Path,
    out_08: Path,
    state_path: Path,
    drawings: dict | None = None,
) -> str:
    started = time.perf_counter()
    en_groups = list(en_document["exercises"]["groups"])
    done = load_translated_groups(out_08)
    state = read_manifest(state_path)
    if state.get("version") != TRANSLATION_MANIFEST_VERSION:
        state = {
            "version": TRANSLATION_MANIFEST_VERSION,
            "book_id": run.ar_id,
            "section": section["number"],
            "stem": section["stem"],
            "groups": {},
            "failed_groups": {},
            "review": {"status": "pending", "rounds": []},
            "exercises_accepted": False,
            "accepted": False,
            "failure": "",
        }
    manifest_groups = dict(state.get("groups") or {})
    meta = _meta_with_glossary(run)
    pending: list[dict] = []
    to_review: list[dict] = []
    skipped = 0
    failed: list[str] = []
    for en_group in en_groups:
        gid = str(en_group["id"])
        stored = manifest_groups.get(gid) if isinstance(manifest_groups.get(gid), dict) else None
        action = _select_group(run, en_group, stored, done.get(gid), en_spec, ar_spec)
        if action == "translate":
            pending.append(en_group)
        elif action == "revalidate":
            keys = group_keys(run, en_group, en_spec, ar_spec)
            stored = dict(stored or {})
            stored.update(keys)
            stored["status"] = "validated"
            stored["review_fp"] = ""
            stored["output_fp"] = json_fingerprint(done[gid])
            manifest_groups[gid] = stored
            to_review.append(en_group)
            skipped += 1
        elif action == "review":
            to_review.append(en_group)
            skipped += 1
        else:
            skipped += 1
    state["groups"] = manifest_groups
    state["thinking_by_step"] = run.thinking_by_step
    forget_cleared_failures(state, manifest_groups)
    write_json(state_path, state)
    gate = threading.Lock()

    def persist() -> None:
        ordered_now = [
            done[group["id"]]
            for group in en_groups
            if group_row_complete(
                group,
                done.get(group["id"]),
                meta,
                spec=en_spec,
                dropped=_drop_map((manifest_groups.get(group["id"]) or {}).get("dropped_numbers")),
            )
        ]
        write_exercise_documents(run, section, title, module_path, en_document, ordered_now, out_08)
        state["groups"] = manifest_groups
        forget_cleared_failures(state, manifest_groups)
        write_json(state_path, state)

    def one_chunk(objective: str, groups: list[dict]) -> None:
        got, errors, drops, paths = translate_objective_batch(
            run, section, en_spec, ar_spec, terminology, objective, groups
        )
        with gate:
            done.update(got)
            for group in groups:
                gid = str(group["id"])
                keys = group_keys(run, group, en_spec, ar_spec)
                if gid in got:
                    taken = paths.get(gid, "batch")
                    manifest_groups[gid] = {
                        **keys,
                        "output_fp": json_fingerprint(got[gid]),
                        "status": "validated",
                        "review_fp": "",
                        "dropped_numbers": drops.get(gid) or [],
                        "path": taken,
                        "first_path": taken,
                    }
                    translated_ids.add(gid)
                elif gid in errors:
                    state.setdefault("failed_groups", {})[gid] = errors[gid]
                    failed.append(f"{gid}: {errors[gid]}")
            persist()

    translated_ids: set[str] = set()
    if pending:
        by_objective: dict[str, list[dict]] = {}
        order: list[str] = []
        for group in pending:
            objective = str(group.get("objective") or "")
            if objective not in by_objective:
                order.append(objective)
            by_objective.setdefault(objective, []).append(group)
        chunks: list[tuple[str, list[dict]]] = []
        notation = str(run.ar_meta.get("notation") or "latin")
        for objective in order:
            for chunk in chunk_objective_groups(by_objective[objective], notation):
                chunks.append((objective, chunk))
        worker_count = min(max(1, run.workers), MAX_WORKERS, len(chunks) or 1)
        run.progress.say(
            f"  {section['number']}: {len(pending)} group(s) to translate in "
            f"{len(chunks)} objective call(s), {worker_count} worker(s)"
        )
        futures = {
            run.call_executor.submit(one_chunk, objective, groups): (objective, groups)
            for objective, groups in chunks
        }
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as error:  # noqa: BLE001
                objective, groups = futures[future]
                failed.append(f"{objective}: {type(error).__name__}: {one_line(str(error))}")
    if run.rereview:
        to_review = list(en_groups)
    elif state.get("exercises_accepted") and not run.update:
        # Exercises already passed review. Re-review only groups this run translated.
        # Groups that stayed "validated" because of open issues must not be sent
        # back to the model on a whole-book run.
        to_review = [
            group for group in en_groups if str(group["id"]) in translated_ids
        ]
    else:
        already = {group["id"] for group in to_review}
        for group in en_groups:
            stored = manifest_groups.get(group["id"]) or {}
            if group["id"] not in already and stored.get("status") != "reviewed":
                to_review.append(group)
    ordered = [
        done[group["id"]]
        for group in en_groups
        if group["id"] in done
        and group_row_complete(
            group,
            done.get(group["id"]),
            meta,
            spec=en_spec,
            dropped=_drop_map((manifest_groups.get(group["id"]) or {}).get("dropped_numbers")),
        )
    ]
    if failed or len(ordered) != len(en_groups):
        state["failure"] = "; ".join(failed[:3])
        state["exercises_accepted"] = False
        forget_cleared_failures(state, manifest_groups)
        write_json(state_path, state)
        missing = [group["id"] for group in en_groups if group["id"] not in {row["id"] for row in ordered}]
        raise RuntimeError(
            f"{section['number']}: {len(ordered)}/{len(en_groups)} group(s) on disk"
            + (f"; failed {', '.join(missing)}" if missing else "")
            + (f"; reasons: {state['failure']}" if state["failure"] else "")
        )
    if (
        not pending
        and not to_review
        and state.get("exercises_accepted")
        and not run.update
        and not run.rereview
        and out_09.is_file()
    ):
        review = state.get("review") if isinstance(state.get("review"), dict) else {}
        if review.get("status") == "pending":
            # Ctrl+C during a re-review left this mid-round. 09_ was not rewritten.
            state["review"] = {"status": "passed", "rounds": []}
        state["groups"] = manifest_groups
        forget_cleared_failures(state, manifest_groups)
        write_json(state_path, state)
        return (
            f"  {section['number']}: {len(ordered)} group(s)"
            f" (0 translated, {skipped} kept, "
            f"{since(time.perf_counter() - started)})"
        )
    candidate = write_exercise_documents(
        run, section, title, module_path, en_document, ordered, out_08
    )
    validate_document(
        en_document, candidate, meta, dropped=_manifest_drops(manifest_groups)
    )
    review_rounds: list[dict] = []
    unresolved: list = []
    current_review = review_key(run)
    for review_round in range(1, MAX_REVIEW_ROUNDS + 1):
        if not to_review:
            unresolved = []
            break
        by_objective: dict[str, list[dict]] = {}
        for group in to_review:
            by_objective.setdefault(str(group.get("objective") or ""), []).append(group)
        unresolved = []
        for objective, subset in by_objective.items():
            arabic = [done[group["id"]] for group in subset]
            dropped_by_id = {
                group["id"]: list(
                    (manifest_groups.get(group["id"]) or {}).get("dropped_numbers") or []
                )
                for group in subset
            }
            labels_by_id = {
                group["id"]: figure_labels_for_group(group, drawings)
                for group in subset
            }
            unresolved.extend(
                review_translation(
                    run,
                    section,
                    ar_spec,
                    terminology,
                    subset,
                    arabic,
                    review_round,
                    objective,
                    dropped_by_id,
                    labels_by_id,
                )
            )
        reviewed_ids = {group["id"] for group in to_review}
        flagged = {issue.group for issue in unresolved}
        for gid in reviewed_ids - flagged:
            row = dict(manifest_groups.get(gid) or {})
            row["status"] = "reviewed"
            row["review_fp"] = current_review
            manifest_groups[gid] = row
        review_rounds.append(
            {
                "round": review_round,
                "reviewed": sorted(reviewed_ids),
                "issues": [issue.model_dump() for issue in unresolved],
            }
        )
        state["review"] = {"status": "pending", "rounds": review_rounds}
        state["groups"] = manifest_groups
        forget_cleared_failures(state, manifest_groups)
        write_json(state_path, state)
        if not unresolved:
            break
        # The last round is the verdict. A correction after it would replace the
        # reviewed text and then be thrown away when the section fails.
        if review_round >= MAX_REVIEW_ROUNDS:
            break
        feedback = issues_by_group(unresolved)
        changed: list[str] = []
        buckets: dict[str, list[dict]] = {}
        for group in en_groups:
            if group["id"] in feedback:
                buckets.setdefault(str(group.get("objective") or ""), []).append(group)
        for objective, groups in buckets.items():
            subset_feedback = {group["id"]: feedback[group["id"]] for group in groups}
            got, errors, drops, paths = translate_objective_batch(
                run,
                section,
                en_spec,
                ar_spec,
                terminology,
                objective,
                groups,
                corrections=subset_feedback,
                current_arabic={group["id"]: done[group["id"]] for group in groups},
                scope=f"review{review_round}",
                escalate=False,
            )
            for gid, row in got.items():
                done[gid] = row
                keys = group_keys(run, next(g for g in groups if g["id"] == gid), en_spec, ar_spec)
                taken = paths.get(gid, "correction")
                manifest_groups[gid] = {
                    **keys,
                    "output_fp": json_fingerprint(row),
                    "status": "validated",
                    "review_fp": "",
                    "dropped_numbers": drops.get(gid) or [],
                    "path": taken,
                    "first_path": _remember_first_path(manifest_groups.get(gid) or {}, taken),
                }
                changed.append(gid)
            for gid, message in errors.items():
                run.progress.say(
                    f"  {section['number']}: {gid} correction failed, {one_line(message)}; keeping the previous Arabic"
                )
        ordered = [done[group["id"]] for group in en_groups]
        candidate = write_exercise_documents(
            run, section, title, module_path, en_document, ordered, out_08
        )
        validate_document(
            en_document, candidate, meta, dropped=_manifest_drops(manifest_groups)
        )
        changed_ids = set(changed)
        # A failed correction leaves that group's text in place. The other
        # groups still take their remaining rounds.
        failed_ids = {issue.group for issue in unresolved if issue.group not in changed_ids}
        unresolved = [issue for issue in unresolved if issue.group not in changed_ids]
        to_review = [
            group for group in en_groups if group["id"] in changed_ids or group["id"] in failed_ids
        ]
        state["groups"] = manifest_groups
        forget_cleared_failures(state, manifest_groups)
        write_json(state_path, state)
    open_issues = [
        {
            "group": issue.group,
            "exercise": issue.exercise,
            "field": issue.field,
            "category": issue.category,
            "reason": issue.reason,
        }
        for issue in unresolved
    ]
    write_json(out_09, canonicalize_document(candidate))
    state["groups"] = manifest_groups
    if open_issues:
        state["review"] = {
            "status": "passed_with_issues",
            "rounds": review_rounds,
            "open_issues": open_issues,
        }
        for issue in unresolved:
            gid = str(issue.group)
            row = dict(manifest_groups.get(gid) or {})
            row["status"] = "reviewed"
            row["review_fp"] = current_review
            manifest_groups[gid] = row
        print(
            f"  {section['number']}: accepted with {len(open_issues)} open review issue(s)",
            flush=True,
        )
    else:
        state["review"] = {"status": "passed", "rounds": review_rounds}
    state["exercises_accepted"] = True
    state["objective_calls"] = len({str(group.get("objective") or "") for group in en_groups})
    state["failure"] = ""
    forget_cleared_failures(state, manifest_groups)
    write_json(state_path, state)
    return (
        f"  {section['number']}: {len(ordered)} group(s)"
        f" ({len(translated_ids)} translated, {skipped} kept, "
        f"{since(time.perf_counter() - started)})"
    )


def translate_tikz(
    run: Run,
    item: str,
    en_tikz: str,
    en_description: str,
    ar_description: str,
    correction_feedback: str = "",
) -> str:
    system = system_prompt(run, "tikz.md")
    payload = (
        f"{profile_heading(run)}\n\n"
        f"Arabic description:\n{ar_description}\n\n"
        f"English description:\n{en_description}\n\n"
        f"English TikZ:\n{en_tikz}"
    )
    if correction_feedback:
        payload += (
            "\n\nMandatory correction to the previous translated drawing:\n"
            f"{correction_feedback}\n"
            "Return the complete corrected tikzpicture."
        )

    def check(result: TikzTranslateReply) -> None:
        if result.status == "abort":
            return
        if not str(result.tikz).strip():
            raise ValueError("ok reply has no tikz")
        translated = picture_only(clean_translated_text(str(result.tikz), item))
        translated = normalize_tikz_print_digits(translated)
        validate_tikz(en_tikz, translated, run.ar_meta)

    reply = request_reply(
        run,
        prompt="",
        parts=[payload],
        schema=TikzTranslateReply,
        item=item,
        step=TIKZ_STEP,
        context={
            "book_id": run.ar_id,
            "figure": item,
            "profile": Profile.of(run.ar_meta).describe(),
            "prompt_files": list(system.parts),
        },
        extra_files={"tikz_input.txt": payload},
        check=check,
        report=lambda message: run.progress.say(f"  {item}: {message}"),
        system_instruction=system.text,
        cache_task="tikz",
        thinking=run.thinking_by_step.get("tikz", run.thinking),
    )
    if reply.status == "abort" or not str(reply.tikz).strip():
        raise RuntimeError(
            f"{item}: TikZ translation aborted: "
            f"{reply.abort_reason or 'no reason given'}"
        )
    translated = picture_only(clean_translated_text(str(reply.tikz), item))
    translated = normalize_tikz_print_digits(translated)
    validate_tikz(en_tikz, translated, run.ar_meta)
    return translated


def adopt_section_figures(
    run: Run,
    section: dict,
    en_document: dict,
    ar_document: dict,
    drawings: dict,
) -> str:
    started = time.perf_counter()
    en_places = figure_places(en_document)
    ar_places = figure_places(ar_document)
    if len(en_places) != len(ar_places):
        raise RuntimeError(
            f"{section['number']}: English has {len(en_places)} figure(s), "
            f"Arabic has {len(ar_places)}"
        )
    adopted = skipped = copied = 0
    for (en_place, ar_place) in zip(en_places, ar_places):
        en_group, en_exercise, en_index, en_figure = en_place
        ar_group, ar_exercise, ar_index, ar_figure = ar_place
        if (en_group, en_exercise, en_index) != (ar_group, ar_exercise, ar_index):
            raise RuntimeError(
                f"{section['number']}: figure order diverged at {en_group} "
                f"{en_exercise or '-'} #{en_index}"
            )
        en_description = figure_description(en_figure)
        ar_description = figure_description(ar_figure)
        if not en_description.strip():
            continue
        en_row = drawings.get(en_description) or {}
        en_tikz = str(en_row.get("tikz") or "").strip()
        if not en_tikz:
            raise RuntimeError(
                f"{section['number']}: no English drawing for {en_group} "
                f"{en_exercise or '-'} #{en_index}"
            )
        figure_type = (
            en_figure.get("figure_type", "") if isinstance(en_figure, dict) else ""
        )
        en_key = str(en_row.get("key") or figure_key(en_description))
        ar_key = translated_figure_key(
            en_key,
            en_tikz,
            ar_description,
            run.ar_meta,
            glossary_fp(run.book_map),
        )
        out_path = OUTPUT_DIR / run.ar_id / FIGURES_STAGE / f"{ar_key}.json"
        item = (
            f"{section['stem']}_{en_group}_"
            f"{en_exercise or 'stem'}_{en_index}"
        )
        layout = normalize_tikz_print_digits(en_tikz)
        needs_translation = tikz_needs_call(layout, run.ar_meta["notation"])
        with figure_write_lock(ar_key):
            existing: dict = {}
            stored_tikz = ""
            leftover: list = []
            if already_written(out_path, False):
                try:
                    existing = json.loads(out_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    existing = {}
                stored_tikz = str(existing.get("tikz") or "").strip()
                leftover = (
                    tikz_problems(
                        en_tikz, stored_tikz, run.ar_meta, run.book_map
                    )
                    if needs_translation and stored_tikz
                    else []
                )
                if (
                    existing.get("settled")
                    and existing.get("provenance")
                    and not leftover
                    and (
                        already_relabelled(existing)
                        or not extra_filed_overlaps(existing, en_row)
                    )
                ):
                    skipped += 1
                    continue
            record = {
                "description": ar_description,
                "figure_type": figure_type,
                "provenance": {
                    "english_key": en_key,
                    "english_tikz": json_fingerprint(en_tikz),
                    "arabic_description": json_fingerprint(ar_description),
                    "profile": {
                        key: run.ar_meta.get(key)
                        for key in ("language_code", "numerals", "notation")
                    },
                    "section": section["number"],
                    "group": en_group,
                    "exercise": en_exercise,
                    "index": en_index,
                },
            }
            if isinstance(ar_figure, dict):
                for name in ("grid", "graph", "strip"):
                    if isinstance(ar_figure.get(name), dict):
                        record[name] = ar_figure[name]
            layout_nudges = 0
            asked_model = False
            tried_stored = False
            while True:
                tikz = layout
                if stored_tikz and not leftover and not tried_stored:
                    tikz = stored_tikz
                    tried_stored = True
                elif needs_translation:
                    run.progress.asking(
                        section["number"], f"tikz {item}, asking the model"
                    )
                    tikz = translate_tikz(
                        run,
                        item,
                        en_tikz,
                        en_description,
                        ar_description,
                    )
                    asked_model = True
                try:
                    adopt_tikz(
                        run.ar_id,
                        ar_description,
                        figure_type,
                        tikz,
                        figure=record,
                        wide=bool(en_row.get("wide")),
                        key=ar_key,
                        allowed_overlaps=int(en_row.get("overlaps") or 0),
                        reference_tikz=layout,
                    )
                    break
                except RuntimeError as error:
                    said = str(error)
                    collision = (
                        "overlap" in said or "collision" in said
                    ) and "could not be checked" not in said
                    if not collision:
                        raise
                    current = tikz
                    filed = False
                    while layout_nudges < MAX_LENGTHMARK_NUDGES:
                        bumped = bump_lengthmark_offsets(current)
                        if bumped == current:
                            break
                        current = bumped
                        layout_nudges += 1
                        run.progress.say(
                            f"  {section['number']}: {item} has a translated-label "
                            "collision; increasing lengthmark offset"
                        )
                        try:
                            adopt_tikz(
                                run.ar_id,
                                ar_description,
                                figure_type,
                                current,
                                figure=record,
                                wide=bool(en_row.get("wide")),
                                key=ar_key,
                                allowed_overlaps=int(en_row.get("overlaps") or 0),
                                reference_tikz=layout,
                            )
                            filed = True
                            break
                        except RuntimeError as nested:
                            nested_said = str(nested)
                            if (
                                "overlap" not in nested_said
                                and "collision" not in nested_said
                            ) or "could not be checked" in nested_said:
                                raise
                    if filed:
                        break
                    run.progress.say(
                        f"  {section['number']}: {item} has a translated-label "
                        "collision; relabelling"
                    )
                    try:
                        with getattr(run, "call_slots", None) or nullcontext():
                            unclash_adopted(
                                run.ar_id,
                                ar_description,
                                figure_type,
                                current,
                                figure=record,
                                wide=bool(en_row.get("wide")),
                                key=ar_key,
                                client=run.client,
                                logger=run.logger,
                                model=run.model,
                                thinking=run.thinking,
                            )
                    except RuntimeError as relabel_error:
                        adopt_tikz(
                            run.ar_id,
                            ar_description,
                            figure_type,
                            current,
                            figure=record,
                            wide=bool(en_row.get("wide")),
                            key=ar_key,
                            allowed_overlaps=max(
                                int(en_row.get("overlaps") or 0), 32
                            ),
                            reference_tikz=layout,
                        )
                        run.progress.say(
                            f"  {section['number']}: {item} relabel failed "
                            f"({one_line(str(relabel_error))}); settling"
                        )
                    break
            if asked_model:
                adopted += 1
            else:
                copied += 1
    write_figure_data(run.ar_id, ar_document)
    return (
        f"  {section['number']}: figures {adopted} translated, {copied} copied, "
        f"{skipped} kept ({since(time.perf_counter() - started)})"
    )


def write_figure_data(ar_id: str, document: dict) -> None:
    """File adopted figures under `10_` so the tree stays inspectable."""
    root = OUTPUT_DIR / ar_id / FIGURE_DATA_STAGE
    for group in document.get("exercises", {}).get("groups") or []:
        for key, row in extract.records(group):
            path = root / extract.folder(row["figure_type"]) / f"{key}.json"
            write_json(
                path,
                canonicalize_tree(
                    row,
                    vector_lesson=section_is_vector_lesson(document),
                ),
            )


def write_applied_book(ar_id: str, ar_meta: dict[str, str], rows: list[dict]) -> Path:
    path = applied_book_path(ar_id)
    existing: dict[str, dict] = {}
    if path.is_file():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            existing = {row["number"]: row for row in previous.get("sections") or []}
        except (OSError, ValueError):
            existing = {}
    for row in rows:
        existing[row["number"]] = row
    order = list(section_spans(ar_id, None))
    sections = [existing[number] for number in order if number in existing]
    write_json(
        path,
        canonicalize_document(
            {"book_id": ar_id, **ar_meta, "sections": sections, "applied_edits": []}
        ),
    )
    return path


def translate_section(
    run: Run,
    section: dict,
    drawings: dict,
    prepared: dict[str, Any],
) -> list[str]:
    number = section["number"]
    stem = section["stem"]
    title = str(prepared["title"])
    module_path = str(prepared["module_path"])
    en_document = prepared["en_document"]
    from src.english_json import load_english_json

    en_labels_path = OUTPUT_DIR / run.en_id / LABELS_STAGE / f"{stem}.json"
    en_budget_path = OUTPUT_DIR / run.en_id / BUDGETS_STAGE / f"{stem}.json"
    try:
        en_labels = (
            load_english_json(en_labels_path) if en_labels_path.is_file() else None
        )
        if en_budget_path.is_file():
            load_english_json(en_budget_path)
    except ValueError as error:
        raise RuntimeError(str(error)) from error
    state_path = manifest_path(run.ar_id, stem)
    state = read_manifest(state_path)
    if state.get("version") != TRANSLATION_MANIFEST_VERSION:
        state = {}
    if accepted_section_reusable(run, section, state, prepared):
        review = state.get("review") if isinstance(state.get("review"), dict) else {}
        before_failed = dict(state.get("failed_groups") or {})
        forget_cleared_failures(state)
        heal = not state.get("accepted") or review.get("status") == "pending"
        if heal:
            state["accepted"] = True
            state["failure"] = ""
            if review.get("status") == "pending":
                state["review"] = {"status": "passed", "rounds": []}
        if heal or (state.get("failed_groups") or {}) != before_failed:
            write_json(state_path, state)
        return [f"  {number}: already accepted"]
    write_json(
        state_path,
        {
            **state,
            "version": TRANSLATION_MANIFEST_VERSION,
            "book_id": run.ar_id,
            "section": number,
            "stem": stem,
            "profile": run.ar_meta,
            "model": run.model,
            "thinking_by_step": run.thinking_by_step,
            "groups": state.get("groups") or {},
            "failed_groups": state.get("failed_groups") or {},
            "review": state.get("review") or {"status": "pending", "rounds": []},
            "exercises_accepted": bool(state.get("exercises_accepted")) and not run.update,
            "figures": state.get("figures") or {"status": "pending"},
            "accepted": False,
            "failure": "",
        },
    )
    try:
        ensure_spec(run, section, prepared)
        spec_document = prepared["spec_document"]
        lines: list[str] = [str(prepared.get("line") or f"  {number}: terminology ready")]
        lines.append(f"  {number}: spec ready")
        ar_spec = spec_document["spec"]
        terminology = list(prepared.get("terminology") or [])
        labels_path = OUTPUT_DIR / run.ar_id / LABELS_STAGE / f"{stem}.json"
        budget_path = OUTPUT_DIR / run.ar_id / BUDGETS_STAGE / f"{stem}.json"
        out_09 = OUTPUT_DIR / run.ar_id / ACCEPTED_STAGE / f"{stem}.json"
        out_08 = OUTPUT_DIR / run.ar_id / EXERCISES_STAGE / f"{stem}.json"
        for stored in (out_08, out_09):
            if repaired := repair_stored_escapes(stored):
                lines.append(
                    f"  {number}: restored {repaired} LaTeX backslash field(s) in "
                    f"{stored.parent.name}"
                )
        for path, needed in (
            (en_labels_path, "labels"),
            (en_budget_path, "budgets"),
        ):
            if not path.is_file():
                raise RuntimeError(f"{number}: no English {needed} at {path}")
        if en_labels is None:
            en_labels = load_english_json(en_labels_path)
        current_labels = labels_key(run, en_labels, ar_spec)
        current_labels_source = labels_source_fp(run, en_labels, ar_spec)
        if labels_reusable(
            file_exists=labels_path.is_file(),
            stored_key=state.get("labels_key"),
            current_key=current_labels,
            stored_source=state.get("labels_source_fp"),
            current_source=current_labels_source,
            update=run.update,
            retranslate_on_prompt_change=run.retranslate_on_prompt_change,
        ):
            lines.append(f"  {number}: labels already on disk")
        else:
            lines.append(
                translate_labels(
                    run, section, title, module_path, ar_spec, en_labels, labels_path
                )
            )
            state = read_manifest(state_path)
            state["labels_key"] = current_labels
            state["labels_source_fp"] = current_labels_source
            write_json(state_path, state)
        if budget_path.is_file() and not run.update:
            lines.append(f"  {number}: budget already on disk")
        else:
            lines.append(
                copy_budget(run, section, title, module_path, en_budget_path, budget_path)
            )
        lines.append(
            translate_exercises(
                run,
                section,
                title,
                module_path,
                section["spec"],
                ar_spec,
                terminology,
                en_document,
                out_09,
                out_08,
                state_path,
                drawings,
            )
        )
        ar_document = json.loads(out_09.read_text(encoding="utf-8"))
        incomplete = exercises_incomplete_reason(
            en_document,
            out_09,
            run.ar_meta,
            spec=section.get("spec") if isinstance(section.get("spec"), dict) else None,
            state_path=state_path,
        )
        if incomplete:
            print(f"  {number}: {incomplete}", flush=True)
            raise RuntimeError(
                f"{number}: accepted exercises are still incomplete: {incomplete}"
            )
        state = read_manifest(state_path)
        figures_passed = (state.get("figures") or {}).get("status") == "passed"
        if (
            figures_passed
            and not run.update
            and section_needs_relabel(run.ar_id, run.en_id, number)
        ):
            figures_passed = False
        if figures_passed and not run.update:
            lines.append(f"  {number}: figures already on disk")
        else:
            lines.append(
                adopt_section_figures(run, section, en_document, ar_document, drawings)
            )
            figures_passed = True
        completed = read_manifest(state_path)
        completed["figures"] = {"status": "passed" if figures_passed else "pending"}
        completed["spec_key"] = spec_key(run, section)
        completed["labels_key"] = current_labels
        completed["labels_source_fp"] = current_labels_source
        completed["accepted"] = True
        completed["failure"] = ""
        forget_cleared_failures(completed)
        write_json(state_path, completed)
        return lines
    except Exception as error:
        failed = read_manifest(state_path)
        failed["failure"] = str(error)
        failed["accepted"] = False
        write_json(state_path, failed)
        raise


def glossary_merge_attempts() -> list[tuple[str, int]]:
    """Two medium tries, then one high try."""
    return [("medium", 1), ("medium", 2), ("high", 3)]


GLOSSARY_FEEDBACK_LINES = 200
GLOSSARY_FEEDBACK_CHARS = 16_000
_GLOSSARY_TRUNCATION_LINE = (
    "the previous reply was cut off at the output limit (MAX_TOKENS); "
    "keep the reply shorter and think less"
)


def _glossary_is_truncation(text: str) -> bool:
    return text.startswith("truncated reply") or "EOF while parsing" in text


def _glossary_is_noise(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("For further information visit") or stripped.startswith(
        "input_value="
    )


def _glossary_feedback_lines(raw_lines: list[str]) -> list[str]:
    """Deduped problem lines: pydantic noise dropped, truncation collapsed to one line."""
    out: list[str] = []
    truncated = False
    for line in raw_lines:
        text = line.strip()
        if not text or _glossary_is_noise(text):
            continue
        if _glossary_is_truncation(text) or "MAX_TOKENS" in text:
            truncated = True
            continue
        if text not in out:
            out.append(text)
    if truncated and _GLOSSARY_TRUNCATION_LINE not in out:
        out.append(_GLOSSARY_TRUNCATION_LINE)
    return out


def _glossary_extend_history(history: list[str], raw_lines: list[str]) -> list[str]:
    """Append new problem lines; drop the oldest when over the cap (newest kept)."""
    for line in _glossary_feedback_lines(raw_lines):
        if line not in history:
            history.append(line)
    while history and (
        len(history) > GLOSSARY_FEEDBACK_LINES
        or sum(len(item) + 1 for item in history) > GLOSSARY_FEEDBACK_CHARS
    ):
        if (
            history[0] == _GLOSSARY_TRUNCATION_LINE
            and len(history) > 1
        ):
            history.pop(1)
        else:
            history.pop(0)
    return history


def _glossary_history_block(history: list[str]) -> str:
    if not history:
        return ""
    return (
        "\n\nThe earlier replies were rejected for these reasons (all attempts so far):\n"
        + "\n".join(f"- {item}" for item in history)
        + "\nReturn a corrected reply that fixes every item.\n"
    )


def _glossary_line_for_kind(line: str, kind: str, part_ids: set[str]) -> bool:
    """Whether a stored problem line belongs on this kind's retry prompt."""
    for other in ("unit", "term", "symbol"):
        if line.startswith(f"two {other} entries") or line.startswith(f"{other} "):
            return other == kind
    if line.startswith("row id ") or line.startswith("unknown source row"):
        return any(item_id and item_id in line for item_id in part_ids)
    return True


def _glossary_system(run: Run) -> tuple[str, str]:
    path = stage_dir(PROMPT_STAGE) / "glossary_merge.md"
    prompt = read_part(path).strip()
    system = prompt + "\n\nProfile: " + Profile.of(run.ar_meta).describe() + ".\n"
    return system, relative(path)


def ensure_book_glossary(
    run: Run,
    fragment_paths: list[Path],
    fragments: list[dict],
    *,
    force: bool = False,
    token_limit: int = GLOSSARY_MERGE_TOKEN_LIMIT,
    invoke: Callable[..., dict] | None = None,
) -> tuple[dict[str, Any], str]:
    """One glossary merge for the book, reused when the terminology files have not changed.

    The second value is ``reused`` or ``ran``.
    """
    notation = str(run.ar_meta.get("notation") or "")
    system, prompt_file = _glossary_system(run)
    schema = GlossaryMergeReply.model_json_schema()
    key = glossary_merge_key(fragment_paths, system, schema, run.model, notation)
    path = book_map_path(run.ar_id)
    if not force and path.is_file():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
        if stored.get("merge_key") == key and stored.get("status") == "ready":
            print("  glossary merge already on disk", flush=True)
            return stored, "reused"
    rows = glossary_candidate_rows(fragments)
    payload = glossary_input_payload(rows, notation)
    if glossary_input_tokens(payload) > token_limit:
        parts = split_glossary_rows(rows)
        print(
            f"  glossary merge input is over {token_limit} tokens; "
            f"splitting into {', '.join(kind for kind, _rows in parts)}",
            flush=True,
        )
    else:
        parts = [("book", rows)]

    def default_invoke(user: str, thinking: str, attempt: int, part: str, part_rows: list[dict]) -> dict:
        def check(reply: GlossaryMergeReply, current_rows: list[dict] = part_rows) -> None:
            problems = glossary_merge_problems(reply.model_dump(), current_rows, notation)
            if problems:
                raise ValueError("\n".join(problems))

        result, error = logged_json_call(
            run,
            prompt=user,
            parts=[],
            schema=GlossaryMergeReply,
            item=f"glossary_{part}",
            step=GLOSSARY_MERGE_STEP,
            attempt=attempt,
            context={
                "book_id": run.ar_id,
                "prompt_files": [prompt_file],
                "part": part,
            },
            check=check,
            system_instruction=system,
            cache_task=None,
            thinking=thinking,
            timeout_ms=GLOSSARY_MERGE_TIMEOUT_MS,
            max_output_tokens=GLOSSARY_MERGE_MAX_OUTPUT_TOKENS,
        )
        if result is None:
            raise ValueError(error or "empty glossary merge reply")
        return result.model_dump()

    caller = invoke or default_invoke
    merged_entries: list[dict] = []
    merged_dropped: list[dict] = []
    merged_conflicts: list[dict] = []
    ladder = glossary_merge_attempts()
    queue: list[tuple[str, list[dict], list[str], int]] = [
        (part, part_rows, [], 0) for part, part_rows in parts
    ]
    while queue:
        part, part_rows, history, start = queue.pop(0)
        problems: list[str] = []
        reply: dict | None = None
        last_error = "no reply"
        split_book = False
        rungs = ladder[start:]
        if not rungs:
            raise RuntimeError(
                "glossary merge failed"
                + (f" for {part}" if part != "book" else "")
                + f"; no glossary means no translation: {last_error}"
            )
        for thinking, attempt in rungs:
            user = json.dumps(
                glossary_input_payload(part_rows, notation),
                ensure_ascii=False,
                indent=2,
            )
            user += _glossary_history_block(history)
            print(
                f"  glossary merge ({part}), attempt {attempt} at {thinking}",
                flush=True,
            )
            try:
                reply = caller(user, thinking, attempt, part, part_rows)
                problems = glossary_merge_problems(reply, part_rows, notation)
            except ValueError as error:
                reply = None
                last_error = str(error)
                raw = [line for line in last_error.splitlines() if line.strip()]
                _glossary_extend_history(history, raw)
                last_rung = attempt == ladder[-1][1]
                if (
                    part == "book"
                    and _glossary_is_truncation(last_error)
                    and len({str(row.get("kind") or "") for row in part_rows}) > 1
                    and not last_rung
                ):
                    kinds = [kind for kind, _rows in split_glossary_rows(part_rows)]
                    print(
                        "  glossary merge (book) truncated; splitting into "
                        + ", ".join(kinds),
                        flush=True,
                    )
                    next_index = next(
                        index + 1
                        for index, (_level, number) in enumerate(ladder)
                        if number == attempt
                    )
                    for kind, kind_rows in split_glossary_rows(part_rows):
                        kind_ids = {str(row.get("id") or "") for row in kind_rows}
                        kind_history = [
                            line
                            for line in history
                            if _glossary_line_for_kind(line, kind, kind_ids)
                        ]
                        queue.append((kind, kind_rows, kind_history, next_index))
                    split_book = True
                    break
                problems = raw
                continue
            if not problems:
                break
            last_error = "\n".join(problems)
            _glossary_extend_history(history, problems)
            reply = None
        if split_book:
            continue
        if reply is None or problems:
            raise RuntimeError(
                "glossary merge failed"
                + (f" for {part}" if part != "book" else "")
                + f"; no glossary means no translation: {last_error}"
            )
        merged_entries.extend(reply.get("entries") or [])
        merged_dropped.extend(reply.get("dropped") or [])
        merged_conflicts.extend(reply.get("conflicts") or [])
    combined = {
        "entries": merged_entries,
        "dropped": merged_dropped,
        "conflicts": merged_conflicts,
    }
    warnings: list[dict] = []
    problems = glossary_merge_problems(combined, rows, notation, warnings)
    merged_conflicts.extend(warnings)
    if problems:
        raise RuntimeError(
            "glossary merge failed; no glossary means no translation: "
            + "\n".join(problems)
        )
    book_map = book_map_from_glossary_reply(
        run.ar_id, run.ar_meta, combined, rows, merge_key=key
    )
    state = OUTPUT_DIR / run.ar_id / TRANSLATION_STATE_STAGE
    state.mkdir(parents=True, exist_ok=True)
    write_json(state / GLOSSARY_CONFLICTS_FILE, merged_conflicts)
    write_json(state / GLOSSARY_DROPPED_FILE, merged_dropped)
    return book_map, "ran"


def translate_book(
    en_id: str,
    section: str | None,
    model: str,
    thinking: str,
    update: bool,
    workers: int,
    *,
    map_only: bool = False,
    downstream_only: bool = False,
    thinking_by_step: dict[str, str] | None = None,
    escalation_thinking: str = DEFAULT_ESCALATION_THINKING,
    sweeps: int = DEFAULT_SWEEPS,
    retranslate_on_prompt_change: bool = False,
    rereview: bool = False,
    use_cache: bool = True,
    remerge: bool = False,
) -> int:
    require_english_id(en_id)
    run_started = datetime.now().astimezone().isoformat()
    ar_id = pair_books(en_id)
    document = require_english_review(en_id)
    all_sections = matching_applied_sections(document, None)
    sections = matching_applied_sections(document, section)
    ar_meta = book_metadata(ar_id)
    print(f"splitting all Arabic sources for the per-book map of {ar_id}", flush=True)
    split_book(ar_id, None, update)
    drawings = library(en_id)
    missing_key = not any(os.environ.get(name) for name in API_KEY_VARS)
    print(f"translating {len(sections)} section(s) of {en_id} -> {ar_id}", flush=True)
    if missing_key:
        raise RuntimeError(f"set one of {' / '.join(API_KEY_VARS)} to call the model")

    print("  opening the model client", flush=True)
    client = genai.Client()
    print("  setting the log clock", flush=True)
    logger = ApiLogger(OUTPUT_DIR / ar_id)
    prompt = bind_profile(assemble(PROMPT_STAGE, ar_meta), ar_meta)
    pad_width = number_pad_width(book_spans(ar_id))
    by_number = {row["number"]: row for row in sections}
    applied_rows: list[dict] = []
    failures: list[str] = []
    prepared_by_stem: dict[str, dict[str, Any]] = {}
    terminology_failed: dict[str, str] = {}

    def record_applied(number: str) -> None:
        row = by_number[number]
        spec_path = OUTPUT_DIR / ar_id / SPECS_STAGE / f"{row['stem']}.json"
        spec_document = json.loads(spec_path.read_text(encoding="utf-8"))
        applied_rows.append(
            {
                "number": spec_document["number"],
                "stem": row["stem"],
                "title": spec_document["title"],
                "module_path": spec_document["module_path"],
                "spec": spec_document["spec"],
            }
        )

    def section_is_complete(row: dict) -> bool:
        en_path = OUTPUT_DIR / en_id / ACCEPTED_STAGE / f"{row['stem']}.json"
        ar_path = OUTPUT_DIR / ar_id / ACCEPTED_STAGE / f"{row['stem']}.json"
        if not en_path.is_file() or row["stem"] not in prepared_by_stem:
            return False
        en_document = json.loads(en_path.read_text(encoding="utf-8"))
        return exercises_complete(
            en_document,
            ar_path,
            ar_meta,
            state_path=manifest_path(ar_id, row["stem"]),
        )

    def run_rows(rows: list[dict], *, count_progress: bool) -> list[str]:
        local_failures: list[str] = []
        if not rows:
            return local_failures
        worker_count = min(
            max(1, workers), len(rows), MAX_SECTION_WORKERS
        )
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = {
                pool.submit(
                    translate_section,
                    run,
                    row,
                    drawings,
                    prepared_by_stem[row["stem"]],
                ): row["number"]
                for row in rows
            }
            for future in as_completed(futures):
                number = futures[future]
                try:
                    lines = future.result()
                except Exception as error:  # noqa: BLE001
                    local_failures.append(
                        f"{number}: {type(error).__name__}: {error}"
                    )
                    if count_progress:
                        progress.done(
                            number,
                            f"  {number}: failed, "
                            f"{one_line(f'{type(error).__name__}: {error}')}",
                        )
                    else:
                        progress.done_again(number)
                        print(
                            f"  {number}: failed, "
                            f"{one_line(f'{type(error).__name__}: {error}')}",
                            flush=True,
                        )
                    continue
                for line in lines:
                    print(line, flush=True)
                record_applied(number)
                write_applied_book(ar_id, ar_meta, applied_rows[-1:])
                if count_progress:
                    progress.done(number)
                else:
                    progress.done_again(number)
        return local_failures

    call_workers = min(max(1, workers), MAX_WORKERS)
    with (
        ThreadPoolExecutor(max_workers=call_workers) as call_executor,
        Progress(len(sections)) as progress,
    ):
        run = Run(
            client=client,
            logger=logger,
            en_id=en_id,
            ar_id=ar_id,
            ar_meta=ar_meta,
            model=model,
            thinking=thinking,
            prompt=prompt,
            pad_width=pad_width,
            sections_root=OUTPUT_DIR / ar_id / SECTIONS_STAGE,
            update=update,
            progress=progress,
            workers=min(max(1, workers), MAX_WORKERS),
            call_slots=threading.BoundedSemaphore(min(max(1, workers), MAX_WORKERS)),
            call_executor=call_executor,
            attempt_lock=threading.Lock(),
            attempt_counts={},
            attempt_errors={},
            blocked_items=set(),
            thinking_by_step=dict(thinking_by_step or DEFAULT_THINKING_BY_STEP),
            escalation_thinking=escalation_thinking,
            use_cache=use_cache,
            retranslate_on_prompt_change=retranslate_on_prompt_change,
            rereview=rereview,
        )
        object.__setattr__(
            run,
            "caches",
            PromptCaches(client, model, ar_id, enabled=use_cache, logger=logger),
        )
        run.caches.recover_orphans()
        try:
            print(
                f"building/reusing {len(all_sections)} section map fragment(s) before "
                "downstream translation",
                flush=True,
            )
            selected_numbers = {row["number"] for row in sections}
            map_workers = min(max(1, workers), len(all_sections) or 1, MAX_SECTION_WORKERS)
            with ThreadPoolExecutor(max_workers=map_workers) as pool:
                futures = {
                    pool.submit(
                        ensure_terminology,
                        run,
                        row,
                        drawings,
                        force=bool(update and row["number"] in selected_numbers),
                        allow_translate=not downstream_only,
                    ): row
                    for row in all_sections
                }
                for future in as_completed(futures):
                    row = futures[future]
                    try:
                        prepared = future.result()
                        prepared_by_stem[row["stem"]] = prepared
                        print(prepared["line"], flush=True)
                    except Exception as error:  # noqa: BLE001
                        terminology_failed[row["number"]] = (
                            f"{type(error).__name__}: {error}"
                        )
                        print(
                            f"  {row['number']}: terminology failed, "
                            f"{one_line(str(error))}",
                            flush=True,
                        )
            ordered_fragments = [
                prepared_by_stem[row["stem"]]["fragment"]
                for row in all_sections
                if row["stem"] in prepared_by_stem
            ]
            terminology_extracted = [
                row["number"]
                for row in all_sections
                if row["stem"] in prepared_by_stem
                and "extracted terminology" in prepared_by_stem[row["stem"]].get("line", "")
            ]
            terminology_reused = [
                row["number"]
                + " ("
                + _reuse_note(map_fragment_path(ar_id, row["stem"]), run_started)
                + ")"
                for row in all_sections
                if row["stem"] in prepared_by_stem
                and "already on disk" in prepared_by_stem[row["stem"]].get("line", "")
            ]
            merge_action = "not_run"
            glossary_failed = ""
            if terminology_failed:
                glossary_failed = (
                    "glossary merge not run; terminology is missing for "
                    + ", ".join(sorted(terminology_failed))
                )
            else:
                try:
                    book_map, merge_action = ensure_book_glossary(
                        run,
                        [map_fragment_path(ar_id, row["stem"]) for row in all_sections],
                        ordered_fragments,
                        force=remerge,
                    )
                    book_map["sections_failed"] = {}
                    book_map["sections_merged"] = [row["number"] for row in all_sections]
                    run.book_map.clear()
                    run.book_map.update(book_map)
                    write_json(
                        book_map_path(ar_id),
                        canonicalize_tree(book_map, vector_lesson=True),
                    )
                    print(
                        f"  book glossary: {len(book_map.get('entries') or [])} entries, "
                        f"{str(book_map.get('semantic_fingerprint') or '')[:12]}",
                        flush=True,
                    )
                except RuntimeError as error:
                    glossary_failed = str(error)
            if glossary_failed:
                failures.append(glossary_failed)
                print(f"  {glossary_failed}", flush=True)
            if map_only:
                code = 0 if not terminology_failed and not glossary_failed else 1
                if getattr(run, "caches", None) is not None:
                    run.caches.close()
                _write_translation_report(
                    ar_id,
                    selected=[row["number"] for row in sections],
                    terminology_failed=terminology_failed,
                    failures=failures,
                    exit_code=code,
                    run_started=run_started,
                    argv=sys.argv,
                    caches=getattr(run, "caches", None),
                    terminology_extracted=terminology_extracted,
                    terminology_reused=terminology_reused,
                    merge_action=merge_action,
                )
                print(f"OK: glossary for {ar_id}" if code == 0 else f"FAILED: glossary for {ar_id}")
                return code
            if glossary_failed:
                print("FAILED:", file=sys.stderr)
                print(f"  {glossary_failed}", file=sys.stderr)
                _write_translation_report(
                    ar_id,
                    selected=[row["number"] for row in sections],
                    terminology_failed=terminology_failed,
                    failures=failures,
                    exit_code=1,
                    run_started=run_started,
                    argv=sys.argv,
                    caches=getattr(run, "caches", None),
                    terminology_extracted=terminology_extracted,
                    terminology_reused=terminology_reused,
                    merge_action=merge_action,
                )
                return 1
            to_run = [row for row in sections if row["number"] not in terminology_failed]
            for number, reason in terminology_failed.items():
                if number in selected_numbers:
                    failures.append(f"{number}: {reason}")
            failures.extend(run_rows(to_run, count_progress=True))
            for sweep in range(1, sweeps + 1):
                retry_rows = []
                kept: list[str] = []
                for item in failures:
                    transient = TRANSIENT_ONLY in item or transient_error(item)
                    terminal = ESCALATION_FAILED in item or UNTRANSLATABLE_SOURCE in item
                    number = item.split(":", 1)[0].strip()
                    row = by_number.get(number)
                    if transient and not terminal and row is not None:
                        retry_rows.append(row)
                    else:
                        kept.append(item)
                if not retry_rows:
                    break
                print(
                    f"sweep {sweep} of {sweeps}: retrying "
                    f"{', '.join(row['number'] for row in retry_rows)}",
                    flush=True,
                )
                failures = [*kept, *run_rows(retry_rows, count_progress=False)]
        finally:
            if getattr(run, "caches", None) is not None:
                run.caches.close()

    if applied_rows:
        path = write_applied_book(ar_id, ar_meta, applied_rows)
        print(f"  applied Arabic book -> {path.relative_to(PROJECT_ROOT)}", flush=True)

    still_missing = [row["number"] for row in sections if not section_is_complete(row)]
    exit_code = 1 if failures or still_missing else 0
    _write_translation_report(
        ar_id,
        selected=[row["number"] for row in sections],
        terminology_failed=terminology_failed,
        failures=failures,
        exit_code=exit_code,
        run_started=run_started,
        argv=sys.argv,
        caches=getattr(run, "caches", None),
        terminology_extracted=terminology_extracted,
        terminology_reused=terminology_reused,
        merge_action=merge_action,
    )
    if exit_code:
        print("FAILED:", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        for number in still_missing:
            if not any(item.startswith(f"{number}:") for item in failures):
                print(f"  {number}: accepted exercises still incomplete", file=sys.stderr)
        return 1
    print(f"OK: translated {len(sections)} section(s) into {ar_id}")
    print(papers_not_rebuilt_line(en_id))
    return 0


def papers_not_rebuilt_line(en_id: str) -> str:
    """The typesetting command a successful translation does not run itself."""
    return (
        "papers not rebuilt; run: "
        f"python3 -B scripts/pipeline.py {en_id} --translate "
        "--from ar_frontmatter --to ar_groups"
    )


def _reuse_note(path: Path, run_started: str) -> str:
    """Where a stored file came from: this session, or an earlier run."""
    if not path.is_file():
        return "earlier run"
    written = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    try:
        started = datetime.fromisoformat(run_started)
    except ValueError:
        started = written
    if written >= started:
        return f"this session ({written:%H:%M})"
    return f"earlier run ({written:%Y-%m-%d %H:%M})"


def _write_translation_report(
    ar_id: str,
    *,
    selected: list[str],
    terminology_failed: dict[str, str],
    failures: list[str],
    exit_code: int,
    run_started: str,
    argv: list[str],
    caches: PromptCaches | None = None,
    terminology_extracted: list[str] | None = None,
    terminology_reused: list[str] | None = None,
    merge_action: str = "not_run",
) -> None:
    from translation_metrics import summarize

    finished = datetime.now().astimezone()
    try:
        started_dt = datetime.fromisoformat(run_started)
        wall = (finished - started_dt).total_seconds()
    except ValueError:
        wall = 0
    root = OUTPUT_DIR / ar_id
    summary = summarize(root, since=run_started) if (root / "00_api_call_logs").is_dir() else {
        "steps": {},
        "cost_usd": 0.0,
    }
    failed_numbers = {item.split(":", 1)[0].strip() for item in failures}
    sections = []
    state_dir = root / TRANSLATION_STATE_STAGE
    if state_dir.is_dir():
        for path in sorted(state_dir.glob("*.json")):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(manifest, dict) or manifest.get("version") != 4:
                continue
            number = str(manifest.get("section") or "")
            groups = manifest.get("groups") or {}
            paths = {}
            for entry in groups.values():
                if isinstance(entry, dict):
                    paths[str(entry.get("path") or "batch")] = paths.get(str(entry.get("path") or "batch"), 0) + 1
            issues = {}
            for round_row in (manifest.get("review") or {}).get("rounds") or []:
                if isinstance(round_row, dict) and round_row.get("round") == 1:
                    for issue in round_row.get("issues") or []:
                        if isinstance(issue, dict):
                            category = str(issue.get("category") or "other")
                            issues[category] = issues.get(category, 0) + 1
            if number in failed_numbers or number in terminology_failed:
                status = "failed"
            elif number in selected and manifest.get("accepted"):
                status = "accepted"
            elif number in selected:
                status = "failed"
            else:
                status = "not_selected"
            sections.append(
                {
                    "number": number,
                    "status": status,
                    "failure": manifest.get("failure") or terminology_failed.get(number, ""),
                    "groups": len(groups),
                    "objective_calls": manifest.get("objective_calls") or 0,
                    "paths": paths,
                    "failed_groups": manifest.get("failed_groups") or {},
                    "review_rounds": len((manifest.get("review") or {}).get("rounds") or []),
                    "issues_round1_by_category": issues,
                    "figures": (manifest.get("figures") or {}).get("status", ""),
                }
            )
    report = {
        "run_started": run_started,
        "run_finished": finished.isoformat(),
        "wall_seconds": wall,
        "argv": argv,
        "book_id": ar_id,
        "selected_sections": selected,
        "exit_code": exit_code,
        "terminology": {
            "ok": [row["number"] for row in sections if row["number"] not in terminology_failed],
            "extracted": list(terminology_extracted or []),
            "reused": list(terminology_reused or []),
            "failed": terminology_failed,
            "calls": (summary.get("steps") or {}).get("terminology_extract", {}).get("calls", 0),
            "cost_usd": (summary.get("steps") or {}).get("terminology_extract", {}).get("usd", 0.0),
        },
        "glossary": {"entries": 0, "conflicts": 0, "action": merge_action},
        "sections": sections,
        "cost_by_step": summary.get("steps") or {},
        "cost_total_usd": summary.get("cost_usd") or 0.0,
        "caches": caches.storage_rows() if caches is not None else [],
        "cache_failures": list(caches.failures) if caches is not None else [],
    }
    cache_usd = sum(row["usd"] for row in report["caches"])
    report["cache_storage_usd"] = cache_usd
    logged_storage = float(
        ((summary.get("steps") or {}).get("cache_storage") or {}).get("usd") or 0
    )
    if not logged_storage:
        report["cost_total_usd"] = float(report["cost_total_usd"]) + cache_usd
    conflicts = state_dir / GLOSSARY_CONFLICTS_FILE
    if conflicts.is_file():
        try:
            rows = json.loads(conflicts.read_text(encoding="utf-8"))
            report["glossary"]["conflicts"] = len(rows) if isinstance(rows, list) else 0
        except (OSError, ValueError):
            pass
    book_map_file = state_dir / BOOK_MAP_FILE
    if book_map_file.is_file():
        try:
            report["glossary"]["entries"] = len(json.loads(book_map_file.read_text(encoding="utf-8")).get("entries") or [])
        except (OSError, ValueError):
            pass
    term_step = (summary.get("steps") or {}).get("terminology_extract") or {}
    merge_step = (summary.get("steps") or {}).get(GLOSSARY_MERGE_STEP) or {}
    report["glossary"]["cost_usd"] = merge_step.get("usd", 0.0)
    not_accepted = [
        row for row in sections if row.get("status") not in {"accepted", "not_selected"}
    ]
    report["not_accepted"] = [
        {
            "number": row["number"],
            "status": row["status"],
            "figures": row.get("figures") or "",
            "failure": row.get("failure") or "",
        }
        for row in not_accepted
    ]
    state_dir.mkdir(parents=True, exist_ok=True)
    history_cost = 0.0
    for older in state_dir.glob("translation_report_*.json"):
        try:
            history_cost += float(json.loads(older.read_text(encoding="utf-8")).get("cost_total_usd") or 0)
        except (OSError, ValueError, TypeError):
            continue
    report["cost_all_runs_usd"] = history_cost + float(report["cost_total_usd"])
    write_json(state_dir / REPORT_JSON, report)
    stamp = re.sub(r"[^0-9T]", "", run_started)[:15]
    write_json(state_dir / f"translation_report_{stamp}.json", report)
    lines = [
        f"# Translation report {ar_id}",
        "",
        f"Exit {exit_code}. Wall {wall:.0f}s. Cost ${report['cost_total_usd']:.2f}.",
        "",
        "| Section | Status | Groups | Failure |",
        "|---|---|---|---|",
    ]
    for row in sections:
        lines.append(
            f"| {row['number']} | {row['status']} | {row['groups']} | {row['failure']} |"
        )
    lines.append("")
    lines.append("| Step | Calls | Errors | USD |")
    lines.append("|---|---|---|---|")
    for name, row in (summary.get("steps") or {}).items():
        lines.append(f"| {name} | {row.get('calls', 0)} | {row.get('errors', 0)} | {row.get('usd', 0):.2f} |")
    lines.append("")
    extracted = ", ".join(terminology_extracted or []) or "none"
    reused = ", ".join(terminology_reused or []) or "none"
    lines.append(
        f"Terminology: extracted {extracted}. Reused {reused}. "
        f"${term_step.get('usd', 0):.4f}."
    )
    if terminology_failed:
        lines.append(
            "Terminology failed: " + ", ".join(sorted(terminology_failed)) + "."
        )
    lines.append(
        f"Glossary merge: {merge_action}, {merge_step.get('calls', 0)} call(s), "
        f"${merge_step.get('usd', 0):.4f}."
    )
    if report["caches"]:
        lines.append("")
        lines.append(
            f"Cache storage ${report['cache_storage_usd']:.4f} "
            f"({len(report['caches'])} cache(s))."
        )
        lines.append("")
        lines.append("| Task | Tokens | Seconds | State | USD |")
        lines.append("|---|---|---|---|---|")
        for row in report["caches"]:
            lines.append(
                f"| {row['task']} | {row['tokens']} | {row['seconds']:.1f} | "
                f"{row.get('state') or row.get('deleted')} | {row['usd']:.4f} |"
            )
        if report.get("cache_failures"):
            lines.append("")
            lines.append("Cache creation failures:")
            for row in report["cache_failures"]:
                lines.append(
                    f"- {row['task']} at {row['time']}: {row['error']} "
                    f"({row.get('uncached_calls', 0)} uncached call(s))"
                )
    lines.append("")
    lines.append(f"Cost across all runs: ${report['cost_all_runs_usd']:.4f}.")
    if report["not_accepted"]:
        lines.append("")
        lines.append("Sections not accepted:")
        for row in report["not_accepted"]:
            lines.append(
                f"- {row['number']}: {row['status']}; figures {row['figures'] or 'unknown'}; "
                f"{row['failure']}"
            )
    text = "\n".join(lines) + "\n"
    (state_dir / REPORT_MD).write_text(text, encoding="utf-8")
    (state_dir / f"translation_report_{stamp}.md").write_text(text, encoding="utf-8")
    if report["not_accepted"]:
        print("Sections not accepted:", flush=True)
        for row in report["not_accepted"]:
            print(
                f"  {row['number']}: {row['status']}, figures {row['figures'] or 'unknown'}",
                flush=True,
            )
    print(f"REPORT: exit {exit_code}, cost ${report['cost_total_usd']:.2f}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_id", help="English book id, e.g. mathematics_en_eg08_t1")
    parser.add_argument("-s", "--section", help="translate this section, or every section under it")
    parser.add_argument("--update", action="store_true", help="redo translations already on disk")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--thinking-level", choices=THINKING_LEVELS, default=DEFAULT_THINKING)
    parser.add_argument("--spec-thinking", choices=THINKING_LEVELS)
    parser.add_argument("--label-thinking", choices=THINKING_LEVELS)
    parser.add_argument("--exercise-thinking", choices=THINKING_LEVELS)
    parser.add_argument("--review-thinking", choices=THINKING_LEVELS)
    parser.add_argument("--tikz-thinking", choices=THINKING_LEVELS)
    parser.add_argument("--terminology-thinking", choices=THINKING_LEVELS)
    parser.add_argument("--escalation-thinking", choices=THINKING_LEVELS, default=DEFAULT_ESCALATION_THINKING)
    parser.add_argument("--sweeps", type=int, default=DEFAULT_SWEEPS)
    parser.add_argument("--retranslate-on-prompt-change", action="store_true")
    parser.add_argument("--rereview", action="store_true")
    parser.add_argument(
        "--remerge",
        action="store_true",
        help="run the glossary merge again even when the stored key still matches",
    )
    parser.add_argument("--no-cache", action="store_true")
    phases = parser.add_mutually_exclusive_group()
    phases.add_argument(
        "--map-only",
        action="store_true",
        help="build/verify the complete PDF-backed book map, then stop",
    )
    phases.add_argument(
        "--downstream-only",
        action="store_true",
        help="require a current complete map and translate only downstream artifacts",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=(
            f"most calls in flight (default: {DEFAULT_WORKERS}; "
            f"explicit maximum: {MAX_WORKERS})"
        ),
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if args.sweeps < 0:
        parser.error("--sweeps must be at least 0")
    thinking_by_step = dict(DEFAULT_THINKING_BY_STEP)
    for key in STEP_KEYS:
        thinking_by_step[key] = args.thinking_level
    overrides = {
        "spec": args.spec_thinking,
        "label": args.label_thinking,
        "exercise": args.exercise_thinking,
        "review": args.review_thinking,
        "tikz": args.tikz_thinking,
        "terminology": args.terminology_thinking,
    }
    for key, value in overrides.items():
        if value:
            thinking_by_step[key] = value
    try:
        return translate_book(
            args.book_id,
            args.section,
            args.model,
            args.thinking_level,
            args.update,
            args.workers,
            map_only=args.map_only,
            downstream_only=args.downstream_only,
            thinking_by_step=thinking_by_step,
            escalation_thinking=args.escalation_thinking,
            sweeps=args.sweeps,
            retranslate_on_prompt_change=args.retranslate_on_prompt_change,
            rereview=args.rereview,
            use_cache=not args.no_cache,
            remerge=args.remerge,
        )
    except RuntimeError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
