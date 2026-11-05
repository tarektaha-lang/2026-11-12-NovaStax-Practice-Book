#!/usr/bin/env python3
"""Proofread a book's new exercises, report the ones that are wrong, and leave those out.

The stage between writing the exercises and both extract and papers. new_questions.py
checks each objective's exercises as they land and sends back what fails. This reads
every section once it is whole, with the book in view, in three passes.

The first pass is a rule: it corrects what can be corrected and reports the rest.
Nothing here calls a model. Every check is a rule that can be written down, and every
correction follows from its finding deterministically, so the same exercises
proofread twice come out the same, and a correction can be read in the findings and
traced to the rule that made it. A finding with a correction names the field it
changed and what the field said before and after; a finding with none is a report —
something is wrong that no rule can put right. Odd $ signs and unbalanced braces
reject the exercise at accept; other reports stay in findings.json for a person.

The second pass puts those exercises in front of the model and asks which ones are
wrong and why, listing every defect on an exercise rather than stopping at the first
type. It does not send the section spec and does not check the curriculum. One call
per exercise. The prompt is cached so a book of calls does not resend it. A section's
report is written once every call for that section has landed, because a report
holding some of its exercises is not a report. The report is tied to the exercises
as they stand, by a hash, so a section that is rewritten is checked again rather
than carrying a report of the old paper.

The third pass reads that report and writes the same document again, without the
flagged exercises and without exercises whose $ signs or braces are still
unbalanced, so the stages that draw and set the paper never see a flagged one.
Every issue type rejects the exercise it names. A group left with no exercises
goes too. The counts are rebuilt so the file cannot disagree with itself; the budget
and the exercise ids stay as they were. An objective left under its budget prints
short and the run says so. A section with no usable report is written through
unrejected except for those delimiter faults, with a warning, so the rest of the
book still runs.

The fourth pass runs on question sets only. When review removes one or more members
from a multi-exercise set and what is left is no longer a size the paper prints
(4, 8 or 12 for a normal set, 4 when every member carries its own figure or table),
the model writes replacements using the same prompt as new_questions.py. Each
rejected member's review reasons go with the call so the new exercise does not
repeat the same fault. After this pass every set left on the paper must be a valid
size or a lone exercise with no stem.

The three passes write into one folder. The accepted exercises sit at the top of it,
as the same document the writing stage wrote, so extract.py and papers.py read them
through written_sections. The proofread exercises and the reports sit in folders of
their own beside them, and the originals are kept where they were, so a rule can be
added or changed and the whole book proofread again from what the model actually
wrote.

The checks in the first pass are a beginning: the two or three things seen wrong in
the first books, put here so that the shape of a check is settled. Each new thing
found wrong in a book should become a rule here rather than a repair at the point of
typesetting, because a repair there fixes the page and leaves the exercise wrong for
whoever reads it next.

    python scripts/questions_review.py eg_mathematics_en_g8_t1
    python scripts/questions_review.py eg_mathematics_en_g8_t1 -s 1.3
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
from collections import Counter
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    from google import genai
    from google.genai import types
    from pydantic import BaseModel, Field

    import src.latex as latex
    import new_questions
    from src.api_logger import (
        ApiLogger,
        genai_answer_text,
        genai_response_files,
        genai_usage,
        safe_log,
    )
    from new_questions import (
        Exercise,
        ExerciseGroup,
        SET_SIZES,
        TALL_SET_SIZES,
        balanced_braces,
        build_prompt as build_write_prompt,
        degree_bullets,
        listed,
        repaired,
        repair_groups_excessive_tashkeel,
        repair_groups_mark_placement,
        sizes_for,
        unescaped_dollars,
        write_problems,
        written_sections,
    )
    from src.geometry_arrows import canonicalize_document, section_is_vector_lesson
    from src.prompting import Profile, Prompt, assemble
    from review import book_attributes, reviewed_book
    from specs import Progress, one_line, since
    from sources import PROJECT_ROOT, OUTPUT_DIR, book_metadata, matching_sections
except ModuleNotFoundError as error:  # pragma: no cover
    sys.exit(f"missing dependency ({error.name}): pip install -r requirements.txt")

STAGE = "09_section_questions_review"
PROOFREAD_DIR = f"{STAGE}/proofread"
REVIEWS_DIR = f"{STAGE}/reviews"
FINDINGS_FILE = "findings.json"
REJECTED_FILE = "rejected.json"
PROMPT_STAGE = "questions_review"
REPLENISH_STEP = "set_replenish"
CHECK_STEP = "section_question_review"

# The fields of an exercise that hold words the reader sees, and so the fields the
# checks read; a group's shared stem is one more, on the group. Options are one field
# each, so a finding can name the one it means.
EXERCISE_FIELDS = ("text", "answer")
GROUP_FIELDS = ("stem",)

REVIEW_KEYS = ("checked", "wrong", "source", "objectives", "issues")
OBJECTIVE_KEYS = ("id", "checked", "wrong")
ISSUE_KEYS = ("id", "exercise", "type", "reason")
ISSUE_PREFIX = "issue"

ISSUE_TYPES = (
    "wrong_answer",
    "impossible",
    "options",
    "wording",
    "figure",
)

# The model sometimes nags that a key is `A, since ...` instead of `(A)`.
# That is not a mathematical fault; drop it even if the prompt is ignored.
ANSWER_FORMAT_NEEDLES = (
    "choice indicator",
    "option designation",
    "standard choice",
    "standard option",
    "formatted as",
    "rather than naming the choice",
    "naming the choice letter",
    "does not use the standard",
    "instead of using the standard",
    "must identify the choice by its letter",
    "must explicitly specify the correct choice letter",
    "must explicitly specify the correct option letter",
    "does not name the choice by its option letter",
    "does not state the correct option letter",
    "referencing a non-existent option",
    "references a non-existent option",
    "incorrectly includes the option letter",
    "incorrectly references an option",
    "omits the choice letter",
    "given as an explanation",
)

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_THINKING = "high"
THINKING_LEVELS = ("minimal", "low", "medium", "high")
API_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
MAX_ATTEMPTS = 4
# How many times a call is allowed to be told to slow down before it counts as
# failed, and the wait after the first such time, which grows with each. Long,
# because a book of this stage puts a call in flight for every exercise, and that
# is what meets the account's spending rate. Jitter is added so that a hundred
# workers throttled together do not come back together.
RATE_LIMIT_WAITS = 12
RATE_LIMIT_PAUSE = 30.0
# One worker per exercise still to be checked, and the ceiling that keeps a book
# of thousands of them from putting thousands of calls in flight at once.
# Lower on the command line when the account's spending rate cannot carry it.
MAX_WORKERS = 25
CACHE_TTL_SECONDS = 3600
CACHE_REFRESH_MARGIN_SECONDS = 300
CACHE_TTL = f"{CACHE_TTL_SECONDS}s"
# Gemini returns 403 when explicit cache TTL expires mid-run; refresh instead of failing.
CACHE_STALE_WAITS = 24


def is_answer_format_issue(kind: str, reason: str) -> bool:
    """True when the issue is only how the answer key is written."""
    if kind != "wrong_answer":
        return False
    text = reason.lower()
    return any(needle in text for needle in ANSWER_FORMAT_NEEDLES)


# --- Proofread: findings with corrections, and reports ----------------------------


@dataclass(frozen=True)
class Place:
    """Where in a section a finding is: which group, which exercise, which field.

    A finding about an exercise as a whole names no field.
    """

    group: int
    exercise: int | None
    field: str
    option: int | None = None

    def __str__(self) -> str:
        where = f"group {self.group}"
        if self.exercise is not None:
            where += f" exercise {self.exercise}"
        if self.field:
            where += f" {self.field}"
        if self.option is not None:
            where += f" {self.option + 1}"
        return where


@dataclass(frozen=True)
class Finding:
    """One thing a check found, and what was done about it.

    `after` is the correction, applied where the finding is; None means the finding
    is a report and the exercise was left as it was.
    """

    section: str
    check: str
    place: str
    what: str
    before: str | None = None
    after: str | None = None

    @property
    def corrected(self) -> bool:
        return self.after is not None


# A field check reads one field's words and says what they should be instead, with
# why, or None when they are as they should be. An exercise check reads a whole
# exercise and reports what no field check could see; it corrects nothing.
FieldCheck = Callable[[str, Profile], tuple[str, str] | None]
ExerciseCheck = Callable[[dict, Profile], list[str]]


# Letters that are not Arabic but look it: the Persian and Urdu forms of yeh, kaf and
# heh, which a model writing Arabic reaches for now and then (section 4.7 of the Saudi
# book came back with a Persian yeh), and which set in a different shape from the
# Arabic letters around them — the Persian yeh has no dots below at the end of a word,
# the Persian kaf no stroke in its isolated form. Each has one Arabic letter it stands
# for, so the correction is a substitution. Alef maksura (ى, U+0649) is not among them:
# it is a letter of Arabic in its own right, and a yeh written as one is a spelling
# question, not a letterform one.
FOREIGN_LETTERS = {
    "\u06cc": "\u064a",  # ی → ي
    "\u06a9": "\u0643",  # ک → ك
    "\u06c1": "\u0647",  # ہ → ه
    "\u06d5": "\u0647",  # ە → ه
}
FOREIGN_LETTER = re.compile("[" + "".join(map(re.escape, FOREIGN_LETTERS)) + "]")


def arabic_letters(text: str, profile: Profile) -> tuple[str, str] | None:
    """Persian and Urdu letterforms in an Arabic book, put back as the Arabic letters."""
    if profile.language_code != "ar":
        return None
    found = sorted(set(FOREIGN_LETTER.findall(text)))
    if not found:
        return None
    corrected = FOREIGN_LETTER.sub(lambda m: FOREIGN_LETTERS[m.group(0)], text)
    names = ", ".join(f"{letter} (U+{ord(letter):04X}) for {FOREIGN_LETTERS[letter]}" for letter in found)
    return corrected, f"non-Arabic letterform(s) {names}"


def degree_bullet(text: str, profile: Profile) -> tuple[str, str] | None:
    """A degree written as \\bullet, put back as \\circ."""
    after = degree_bullets(text)
    if after == text:
        return None
    return after, r"degree written as \bullet, put as \circ"


def labelled_choice(text: str, profile: Profile) -> tuple[str, str] | None:
    """A letter the model prefixed onto a choice, taken off; the paper letters them itself.

    Writing already strips these as the reply lands. This is the same repair for a
    section written before that, or one the strip missed: ``(أ) الحذف`` printed next
    to the paper's own ``(أ)`` is the doubled أ أ (or ب ب) on the page.
    """
    stripped = new_questions.strip_option_label(text)
    if stripped == text:
        return None
    return stripped, "prefixed choice letter taken off"


def multiple_choice(exercise: dict, profile: Profile) -> list[str]:
    """What is wrong with an exercise's choices that no correction can put right.

    An exercise offers as many choices as the paper sets, its choices differ from one
    another, and its answer, when it names one by letter, names one it has. Any of
    these wrong is a wrong exercise, and which of the choices is the wrong one is a
    judgement, so they are reported and not touched.

    An answer is read as naming a choice the way new_questions.py reads it — the letter
    alone, in brackets, or before the reason — so the two stages agree on what an answer
    of "ب، لأن …" means.
    """
    options = exercise.get("options")
    letters = latex.CHOICE_LETTERS[profile.language_code]
    letter = new_questions.chosen_letter(exercise.get("answer", ""))
    if letter not in letters:
        letter = ""
    if not options:
        # An answer that chooses by letter where there is nothing to choose from: the
        # choices were written and lost, as when the reply broke down before them.
        if letter:
            return [f"answer names option {letter} of an exercise with no options"]
        return []
    reports = []
    if len(options) != latex.MCQ_OPTIONS:
        reports.append(f"{len(options)} option(s) where the paper sets {latex.MCQ_OPTIONS}")
    seen: dict[str, int] = {}
    for index, option in enumerate(options, start=1):
        key = " ".join(option.split())
        if key in seen:
            reports.append(f"option {index} repeats option {seen[key]}: {option!r}")
        else:
            seen[key] = index
    if letter:
        chosen = letters.index(letter)
        if chosen >= len(options):
            reports.append(f"answer names option {letter}, the {chosen + 1}th, of {len(options)}")
    return reports


def maths_delimiters(exercise: dict, profile: Profile) -> list[str]:
    """A field with an odd number of $ signs or braces that do not pair.

    Accept rejects the exercise. Inserting a $ is not a correction: the hole is
    not always at the end of the field.
    """
    reports = []
    fields = [(field, exercise.get(field, "")) for field in EXERCISE_FIELDS]
    fields += [(f"option {index}", option) for index, option in enumerate(exercise.get("options", []), start=1)]
    for name, value in fields:
        if not isinstance(value, str):
            continue
        if unescaped_dollars(value) % 2:
            reports.append(f"{name} has an odd number of $ signs: {value!r}")
        if not balanced_braces(value):
            reports.append(f"{name} has unbalanced braces: {value!r}")
    return reports


def runaway(exercise: dict, profile: Profile) -> list[str]:
    """A field that repeats one character a dozen times over: the model's reply broke
    down into a loop while writing it, and whatever follows the loop is not to be
    trusted either. new_questions.py sends such a reply back now; this is for
    exercises written before it did."""
    reports = []
    fields = [(field, exercise.get(field, "")) for field in EXERCISE_FIELDS]
    fields += [(f"option {index}", option) for index, option in enumerate(exercise.get("options", []), start=1)]
    for name, value in fields:
        if isinstance(value, str) and (found := new_questions.RUNAWAY.search(value)):
            reports.append(f"{name} repeats {found.group(1)!r} {len(found.group(0))} times over")
    return reports


FIELD_CHECKS: dict[str, FieldCheck] = {
    "arabic_letters": arabic_letters,
    "degree_bullet": degree_bullet,
}
# Only the choices: an answer of "أ) الحياة" is naming the letter, not leaking a label.
OPTION_CHECKS: dict[str, FieldCheck] = {
    "labelled_choice": labelled_choice,
}
CORRECTING_CHECKS = {**FIELD_CHECKS, **OPTION_CHECKS}
EXERCISE_CHECKS: dict[str, ExerciseCheck] = {
    "multiple_choice": multiple_choice,
    "maths_delimiters": maths_delimiters,
    "runaway": runaway,
}


def fields_of(document: dict) -> Iterator[tuple[Place, dict | list, str | int]]:
    """Every field of words in a section, with where it is and how to write it back.

    Yields the place, the container the field sits in and the key into it, so a
    correction is one assignment; a group's stem, then each exercise's fields and each
    of its options, in the section's order.
    """
    for g, group in enumerate(document["exercises"]["groups"], start=1):
        for field in GROUP_FIELDS:
            if isinstance(group.get(field), str):
                yield Place(g, None, field), group, field
        for e, exercise in enumerate(group["exercises"], start=1):
            for field in EXERCISE_FIELDS:
                if isinstance(exercise.get(field), str):
                    yield Place(g, e, field), exercise, field
            for o, _ in enumerate(exercise.get("options", [])):
                yield Place(g, e, "option", o), exercise["options"], o


def proofread(document: dict, profile: Profile) -> list[Finding]:
    """Run every check over one section, correcting the document in place."""
    section = document["number"]
    findings: list[Finding] = []
    for place, holder, key in fields_of(document):
        checks = CORRECTING_CHECKS if place.field == "option" else FIELD_CHECKS
        for name, check in checks.items():
            before = holder[key]
            result = check(before, profile)
            if result is None:
                continue
            after, what = result
            holder[key] = after
            findings.append(Finding(section, name, str(place), what, before, after))
    for g, group in enumerate(document["exercises"]["groups"], start=1):
        for e, exercise in enumerate(group["exercises"], start=1):
            for name, check in EXERCISE_CHECKS.items():
                for what in check(exercise, profile):
                    findings.append(Finding(section, name, str(Place(g, e, "")), what))
    return findings


def proofread_book(book_id: str, section: str | None) -> tuple[list[tuple[str, dict]], list[Finding]]:
    """Proofread every section of a book, or one, writing the corrected sections and the findings."""
    print(f"reading the written exercises of {book_id}", flush=True)
    documents = written_sections(book_id, section)
    profile = Profile.of(book_metadata(book_id))
    stage_dir = OUTPUT_DIR / book_id / STAGE
    proof_dir = OUTPUT_DIR / book_id / PROOFREAD_DIR
    proof_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"proofreading {len(documents)} section(s) with {len(FIELD_CHECKS)} correcting "
        f"and {len(EXERCISE_CHECKS)} reporting check(s)",
        flush=True,
    )
    findings: list[Finding] = []
    for stem, document in documents:
        found = proofread(document, profile)
        document = canonicalize_document(document)
        (proof_dir / f"{stem}.json").write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        corrected = sum(1 for f in found if f.corrected)
        reported = len(found) - corrected
        if found:
            print(
                f"  {document['number']}: {corrected} correction(s), {reported} report(s)",
                flush=True,
            )
        findings += found

    # The findings of the whole book, or of the one section when that is all that was
    # read: what was found last time about a section not read this time is kept.
    findings_path = stage_dir / FINDINGS_FILE
    kept: list[dict] = []
    if section is not None and findings_path.is_file():
        read = {document["number"] for _, document in documents}
        kept = [
            f for f in json.loads(findings_path.read_text(encoding="utf-8"))
            if f.get("section") not in read
        ]
    findings_path.write_text(
        json.dumps(kept + [asdict(f) for f in findings], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    corrected = [f for f in findings if f.corrected]
    reported = [f for f in findings if not f.corrected]
    print(
        f"OK: {len(documents)} section(s) proofread under {proof_dir.relative_to(PROJECT_ROOT)}: "
        f"{len(corrected)} correction(s) made, {len(reported)} report(s) to read"
    )
    for check in sorted({f.check for f in findings}):
        count = sum(1 for f in findings if f.check == check)
        kind = "corrected" if check in FIELD_CHECKS else "reported"
        print(f"    {check}: {count} {kind}")
    if reported:
        print(f"    the reports, in {FINDINGS_FILE}:")
        for finding in reported:
            print(f"      {finding.section} {finding.place}: {finding.what}")
    return documents, findings


# --- Question review: one model call per exercise ---------------------------------


class Issue(BaseModel):
    """One exercise that is wrong, with the reason a teacher would act on."""

    exercise: str = Field(
        description="The exercise id, exactly as the JSON prints it, e.g. exercise_12"
    )
    type: Literal[*ISSUE_TYPES] = Field(  # type: ignore[valid-type]
        description=(
            "wrong_answer, impossible (including impossible givens or stories), "
            "options, wording, or figure (including a description that cannot be drawn)"
        )
    )
    reason: str = Field(
        description=(
            "Why this one defect is wrong, in English, in a sentence or three; "
            "one reason per issue, not a list of every fault"
        )
    )


class CheckReply(BaseModel):
    """The model's answer, which may refuse the payload instead of checking it."""

    status: Literal["ok", "abort"]
    abort_reason: str = Field(
        default="", description="Why the payload cannot be checked; empty when status is ok"
    )
    issues: list[Issue] = Field(
        default_factory=list,
        description=(
            "Every distinct defect on this exercise, of every type that applies; "
            "empty only when the exercise is sound"
        ),
    )


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def build_prompt(meta: dict[str, str]) -> Prompt:
    """The prompt this stage sends for the book, assembled from its parts."""
    return assemble(PROMPT_STAGE, meta)


def group_exercise_ids(groups: list[dict]) -> set[str]:
    """Every exercise id a list of groups holds."""
    return {row["id"] for group in groups for row in group["exercises"]}


def exercise_ids(document: dict) -> set[str]:
    """Every exercise id this section's file holds."""
    return group_exercise_ids(document["exercises"]["groups"])


def objective_order(document: dict) -> list[str]:
    """The objectives this section's exercises were written for, in paper order."""
    return [row["id"] for row in document["exercises"]["objectives"]]


def objective_groups(document: dict, objective: str) -> list[dict]:
    """The groups written for one objective, in the order the paper prints them."""
    return [
        group for group in document["exercises"]["groups"] if group["objective"] == objective
    ]


def paper_exercises(document: dict) -> list[tuple[dict, dict]]:
    """Each group and the exercise under it, in the order the paper prints them."""
    return [
        (group, exercise)
        for group in document["exercises"]["groups"]
        for exercise in group["exercises"]
    ]


def exercise_part(document: dict, group: dict, exercise: dict) -> str:
    """One exercise, with the stem and figures of the set it belongs to."""
    return "Exercise JSON:\n" + compact(
        {
            "number": document["number"],
            "title": document["title"],
            "objective": group["objective"],
            "group": group["id"],
            "stem": group.get("stem") or "",
            "figures": group.get("figures") or [],
            "exercise": exercise,
        }
    )


def review_fingerprint(document: dict, prompt: str) -> str:
    """A hash of the paper and the instructions that judged it."""
    return hashlib.sha256(
        (compact(document["exercises"]) + "\nquestion\n" + prompt).encode("utf-8")
    ).hexdigest()


def issue_key(issue: Issue) -> tuple[str, str, str]:
    """Identity of one defect: the exercise, the type, and the reason as written."""
    return (issue.exercise, *issue_body(issue))


def issue_body(issue: Issue) -> tuple[str, str]:
    """The defect itself, ignoring which exercise it is attached to."""
    return (issue.type, " ".join(issue.reason.split()).casefold())


def issue_problems(issues: list[Issue], ids: set[str]) -> list[str]:
    """Every reason a report cannot be kept, as lines for the retry."""
    problems: list[str] = []
    seen: set[tuple[str, str, str]] = set()

    for number, issue in enumerate(issues, start=1):
        label = f"issue {number}"
        if issue.exercise not in ids:
            problems.append(f"{label} names {issue.exercise}, which is not in this call")
        if not issue.reason.strip():
            problems.append(f"{label} ({issue.exercise}) gives no reason")
        key = issue_key(issue)
        if key in seen:
            problems.append(
                f"{label} repeats {issue.exercise} as {issue.type} with the same reason"
            )
        else:
            seen.add(key)

    return problems


def check_issues(reply: CheckReply, ids: set[str]) -> None:
    """Reject a report whose issues cannot be attached to this call's exercises."""
    problems = issue_problems(reply.issues, ids)
    if problems:
        raise ValueError("; ".join(problems))


def attach(ids: set[str], issues: list[Issue]) -> dict[str, list[Issue]]:
    """Every id this call covered, with the issues that landed on it."""
    found: dict[str, list[Issue]] = {eid: [] for eid in ids}
    for issue in issues:
        if is_answer_format_issue(issue.type, issue.reason):
            continue
        found[issue.exercise].append(issue)
    return found


CHECK_PAGE = re.compile(r"^(?P<section>\d+\.\d+)-(?P<exercise>exercise_\d+)$")


def load_logged_exercise_checks(
    book_id: str,
    number_to_stem: dict[str, str],
    papers: dict[str, dict],
    *,
    logs_dir: Path | None = None,
) -> tuple[dict[str, dict[str, list[Issue]]], int]:
    """Recover per-exercise check results from successful API logs on disk.

    When several logs exist for one exercise, the newest ``timestamp`` wins.
    """
    root = logs_dir or (OUTPUT_DIR / book_id / "00_api_call_logs")
    if not root.is_dir():
        return {}, 0
    best: dict[tuple[str, str], tuple[str, dict[str, list[Issue]]]] = {}
    scanned = 0
    for call_path in root.glob(f"*_{CHECK_STEP}/call.json"):
        scanned += 1
        try:
            call = json.loads(call_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if call.get("book_id") != book_id or call.get("step") != CHECK_STEP:
            continue
        if call.get("status") != "success" or call.get("returned") != "ok":
            continue
        parsed = CHECK_PAGE.match(str(call.get("page_number") or ""))
        if not parsed:
            continue
        stem = number_to_stem.get(parsed.group("section"))
        exercise_id = parsed.group("exercise")
        if not stem or stem not in papers:
            continue
        if exercise_id not in exercise_ids(papers[stem]):
            continue
        response_path = call_path.parent / "response_text.txt"
        if not response_path.is_file():
            continue
        try:
            reply = CheckReply.model_validate_json(response_path.read_text(encoding="utf-8"))
            check_issues(reply, {exercise_id})
        except (OSError, ValueError):
            continue
        stamped = str(call.get("timestamp") or call_path.parent.name)
        key = (stem, exercise_id)
        row = attach({exercise_id}, reply.issues)
        if key not in best or stamped > best[key][0]:
            best[key] = (stamped, row)
    merged: dict[str, dict[str, list[Issue]]] = {}
    for (stem, _exercise_id), (_stamped, row) in best.items():
        merged.setdefault(stem, {}).update(row)
    return merged, len(best)


def write_section_reviews(
    book_id: str,
    meta: dict[str, str],
    envelope_keys: set[str],
    rows: list[dict],
    papers: dict[str, dict],
    written: dict[str, dict[str, list[Issue]]],
    prompt_text: str,
    stage_dir: Path,
) -> list[dict]:
    """Write review JSON for sections whose exercises are all present in ``written``."""
    complete: list[dict] = []
    for row in rows:
        paper = papers[row["stem"]]
        ids = exercise_ids(paper)
        if len(written.get(row["stem"], {})) != len(ids):
            continue
        review = review_document(
            paper, written[row["stem"]], review_fingerprint(paper, prompt_text)
        )
        out_path = stage_dir / f"{row['stem']}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {
                    "book_id": book_id,
                    **meta,
                    "number": row["number"],
                    "title": row["title"],
                    "module_path": row["module_path"],
                    "review": review,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"  {row['number']}: {describe(review)}", flush=True)
        complete.append(row)
    return complete


def cache_display_name(book_id: str) -> str:
    """A cache name Gemini will accept: 128 characters, unique per book."""
    return f"{book_id}-question-review"[:128]


def create_prompt_cache(
    client: genai.Client,
    model: str,
    prompt: str,
    book_id: str,
) -> tuple[str, int]:
    """Cache the examiner prompt for the calls that follow."""
    cache = client.caches.create(
        model=model,
        config=types.CreateCachedContentConfig(
            display_name=cache_display_name(book_id),
            system_instruction=prompt,
            contents=["The next message is the exercise JSON to check."],
            ttl=CACHE_TTL,
        ),
    )
    if not cache.name:
        raise RuntimeError("cache created with no name")
    usage = cache.usage_metadata
    tokens = usage.total_token_count if usage is not None else 0
    return cache.name, tokens or 0


def cache_content_stale(error: str | None) -> bool:
    """Whether the API rejected an explicit cache that expired or was deleted."""
    if not error:
        return False
    if "CachedContent not found" in error:
        return True
    return (
        "403" in error
        and "PERMISSION_DENIED" in error
        and "Cached" in error
    )


class ReviewPromptCache:
    """Thread-safe Gemini prompt cache with proactive refresh before TTL."""

    def __init__(
        self,
        client: genai.Client,
        model: str,
        prompt: str,
        book_id: str,
    ) -> None:
        self._client = client
        self._model = model
        self._prompt = prompt
        self._book_id = book_id
        self._lock = threading.Lock()
        self._name: str | None = None
        self._created_at = 0.0
        self.names: list[str] = []
        self.leases: dict[str, tuple[int, float]] = {}

    def current(self) -> str | None:
        """The cache to attach to the next call, refreshing when near expiry."""
        with self._lock:
            return self._ensure_locked(time.perf_counter())

    def invalidate_and_replace(self) -> str | None:
        """Drop the stale handle and create a new cache (403 recovery)."""
        with self._lock:
            self._name = None
            return self._ensure_locked(time.perf_counter())

    def _ensure_locked(self, now: float) -> str | None:
        if (
            self._name
            and (now - self._created_at)
            < (CACHE_TTL_SECONDS - CACHE_REFRESH_MARGIN_SECONDS)
        ):
            return self._name
        try:
            name, tokens = create_prompt_cache(
                self._client, self._model, self._prompt, self._book_id
            )
        except Exception:  # noqa: BLE001
            return self._name
        self._name = name
        self._created_at = now
        if name not in self.leases:
            self.names.append(name)
            self.leases[name] = (tokens, now)
        return name


def drop_caches(client: genai.Client, names: list[str]) -> list[str]:
    """Delete the caches this run created, so they do not sit billing for the TTL."""
    failed: list[str] = []
    for name in names:
        try:
            client.caches.delete(name=name)
        except Exception as error:  # noqa: BLE001 - dropping is best-effort
            failed.append(f"{name}: {type(error).__name__}: {error}")
    return failed


def log_cache_storage(
    logger: ApiLogger,
    *,
    book_id: str,
    model: str,
    name: str,
    tokens: int,
    seconds: float,
    dropped: bool,
) -> None:
    """Record the explicit cache's measured token-hours as a priced lifecycle item."""
    item = "cache_" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    safe_log(
        logger,
        page_number=item,
        step_name="cache_storage",
        attempt=1,
        api="gemini",
        model=model,
        usage={
            "cache_storage_tokens": tokens,
            "cache_storage_seconds": round(seconds, 3),
        },
        duration_seconds=0,
        status="success" if dropped else "error",
        error_message=None if dropped else "cache deletion failed; TTL duration priced",
        extra={
            "book_id": book_id,
            "cache_name": name,
            "cache_deleted": dropped,
            "cache_ttl_seconds": 3600,
        },
        files={
            "cache.json": {
                "name": name,
                "tokens": tokens,
                "seconds": round(seconds, 3),
                "deleted": dropped,
            }
        },
    )


def logged_call(
    client,
    model: str,
    prompt: str,
    parts: list[str],
    ids: set[str],
    thinking: str,
    logger: ApiLogger,
    item: str,
    attempt: int,
    context: dict,
    cached_content: str | None = None,
) -> tuple[CheckReply | None, str | None]:
    """Ask once, logging the call however it turns out."""
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=CheckReply,
        thinking_config=types.ThinkingConfig(thinking_level=thinking),
        cached_content=cached_content,
    )
    contents = parts if cached_content else [prompt, *parts]
    response = None
    result = None
    error_message = None
    started = time.perf_counter()
    try:
        response = client.models.generate_content(
            model=model, contents=contents, config=config
        )
        result = CheckReply.model_validate_json(genai_answer_text(response))
        if result.status == "ok":
            check_issues(result, ids)
    except Exception as error:  # noqa: BLE001 - retry transport, parse and addressing alike
        error_message = f"{type(error).__name__}: {error}"
        result = None
    duration = time.perf_counter() - started

    safe_log(
        logger,
        files_builder=lambda captured=response: {
            "prompt.txt": prompt,
            **{f"part_{number}.txt": part for number, part in enumerate(parts, start=1)},
            **(genai_response_files(captured) if captured is not None else {}),
        },
        page_number=item,
        step_name=CHECK_STEP,
        attempt=attempt,
        api="gemini",
        model=model,
        usage=genai_usage(response) if response is not None else {},
        duration_seconds=duration,
        status="success" if result is not None else "error",
        error_message=error_message,
        extra={
            **context,
            "thinking_level": thinking,
            "cached_content": cached_content,
            "returned": result.status if result else None,
        },
    )
    return result, error_message


def rate_limited(error: str | None) -> bool:
    """Whether a failure was the service asking for fewer calls, not refusing this one."""
    return bool(error) and ("RESOURCE_EXHAUSTED" in error or " 429 " in error)


def request_check(
    client,
    model: str,
    prompt: str,
    parts: list[str],
    ids: set[str],
    thinking: str,
    logger: ApiLogger,
    item: str,
    context: dict,
    report: Callable[[str], None] | None = None,
    prompt_cache: ReviewPromptCache | None = None,
    cached_content: str | None = None,
) -> CheckReply:
    """Call the model until it returns a usable report, retrying every failure.

    A refusal — a reply that broke a rule, a reply that would not parse — costs one
    of MAX_ATTEMPTS. Being told to slow down does not: a 429 says nothing about the
    reply and everything about how many calls a book's worth of workers has in
    flight, so it is waited out, for longer each time, and the attempt asked again.
    """
    last_error: str | None = None
    attempt = 1
    throttled = 0
    cache_stale = 0

    while attempt <= MAX_ATTEMPTS:
        active_cache = (
            prompt_cache.current()
            if prompt_cache is not None
            else cached_content
        )
        result, last_error = logged_call(
            client,
            model,
            prompt,
            parts,
            ids,
            thinking,
            logger,
            item,
            attempt,
            context,
            cached_content=active_cache,
        )
        if result is not None:
            return result
        if (
            prompt_cache is not None
            and cache_content_stale(last_error)
            and cache_stale < CACHE_STALE_WAITS
        ):
            cache_stale += 1
            prompt_cache.invalidate_and_replace()
            if report is not None:
                report(
                    f"prompt cache expired ({cache_stale} of {CACHE_STALE_WAITS}), "
                    "refreshed; asking again"
                )
            continue
        if rate_limited(last_error) and throttled < RATE_LIMIT_WAITS:
            throttled += 1
            pause = RATE_LIMIT_PAUSE * throttled + random.uniform(0, RATE_LIMIT_PAUSE)
            if report is not None:
                report(f"rate limited ({throttled} of {RATE_LIMIT_WAITS}), waiting {pause:.0f}s")
            time.sleep(pause)
            continue
        if attempt < MAX_ATTEMPTS:
            if report is not None:
                report(
                    f"attempt {attempt} of {MAX_ATTEMPTS} failed, "
                    f"{one_line(last_error or 'no reason given')}; "
                    f"asking again in {2**attempt}s"
                )
            time.sleep(2**attempt)
        attempt += 1

    raise RuntimeError(f"failed after {MAX_ATTEMPTS} attempts: {last_error}")


def issues_document(issues: list[Issue]) -> list[dict]:
    """The issues as they are written to disk, each with its id."""
    return [
        {"id": f"{ISSUE_PREFIX}_{number}", **issue.model_dump()}
        for number, issue in enumerate(issues, start=1)
    ]


def is_stem_issue(reason: str) -> bool:
    """True when the reason is about the shared stem, not this member alone."""
    return "stem" in reason.lower()


def spread_stem_issues(
    document: dict, by_exercise: dict[str, list[Issue]]
) -> dict[str, list[Issue]]:
    """A bad shared stem makes every member of that set wrong."""
    for group in document["exercises"]["groups"]:
        members = [row["id"] for row in group["exercises"]]
        if len(members) < 2 or not (group.get("stem") or "").strip():
            continue
        shared: list[Issue] = []
        seen: set[tuple[str, str]] = set()
        for eid in members:
            for issue in by_exercise.get(eid) or []:
                if not is_stem_issue(issue.reason):
                    continue
                body = issue_body(issue)
                if body not in seen:
                    seen.add(body)
                    shared.append(issue)
        if not shared:
            continue
        for eid in members:
            have = {issue_body(issue) for issue in by_exercise.get(eid) or []}
            extra = [
                Issue(exercise=eid, type=issue.type, reason=issue.reason)
                for issue in shared
                if issue_body(issue) not in have
            ]
            if extra:
                by_exercise[eid] = list(by_exercise.get(eid) or []) + extra
    return by_exercise


def review_document(
    document: dict, by_exercise: dict[str, list[Issue]], fingerprint: str
) -> dict:
    """The section report as it is written to disk, assembled from its exercises."""
    by_exercise = spread_stem_issues(document, by_exercise)
    objective_of = {
        exercise["id"]: group["objective"]
        for group, exercise in paper_exercises(document)
    }
    by_objective: dict[str, list[Issue]] = {name: [] for name in objective_order(document)}
    for exercise_id, found in by_exercise.items():
        by_objective[objective_of[exercise_id]].extend(found)

    counts: list[dict] = []
    issues: list[Issue] = []
    for objective in objective_order(document):
        found = by_objective[objective]
        ids = group_exercise_ids(objective_groups(document, objective))
        counts.append(
            {
                "id": objective,
                "checked": len(ids),
                "wrong": len({issue.exercise for issue in found}),
            }
        )
        issues.extend(found)

    rows = issues_document(issues)
    return {
        "checked": sum(row["checked"] for row in counts),
        "wrong": len({row["exercise"] for row in rows}),
        "source": fingerprint,
        "objectives": counts,
        "issues": rows,
    }


def written_review(
    path: Path,
    envelope_keys: set[str],
    document: dict,
    fingerprint: str,
) -> bool:
    """True when a section already holds a report of this paper, of the shape written today."""
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        body = report["review"]
        if set(report) != envelope_keys or set(body) != set(REVIEW_KEYS):
            return False
        ids = exercise_ids(document)
        if body["checked"] != len(ids):
            return False
        if body["source"] != fingerprint:
            return False
        expected = [
            (row["id"], row["exercises"]) for row in document["exercises"]["objectives"]
        ]
        got = [(row["id"], row["checked"]) for row in body["objectives"]]
        if got != expected:
            return False
        named = set()
        for row in body["issues"]:
            if set(row) != set(ISSUE_KEYS):
                return False
            Issue.model_validate({key: row[key] for key in ("exercise", "type", "reason")})
            if row["exercise"] not in ids:
                return False
            named.add(row["exercise"])
        if body["wrong"] != len(named):
            return False
        if any(set(row) != set(OBJECTIVE_KEYS) for row in body["objectives"]):
            return False
    except (OSError, LookupError, TypeError, ValueError):
        return False
    return True


def describe(review: dict) -> str:
    """The counts worth seeing on one line while a book runs."""
    notes = f"{review['wrong']} wrong of {review['checked']}"
    if review["issues"]:
        counts = Counter(row["type"] for row in review["issues"])
        spread = ", ".join(f"{counts[name]} {name}" for name in ISSUE_TYPES if counts[name])
        notes += f" ({spread})"
    return notes


def describe_issues(issues: list[Issue], checked: int) -> str:
    """The counts for one call, while it is still landing."""
    wrong = {issue.exercise for issue in issues}
    notes = f"{len(wrong)} wrong of {checked}"
    if issues:
        counts = Counter(issue.type for issue in issues)
        spread = ", ".join(f"{counts[name]} {name}" for name in ISSUE_TYPES if counts[name])
        notes += f" ({spread})"
    return notes


@dataclass(frozen=True)
class Run:
    """Everything a call is checked against that the whole book shares."""

    client: genai.Client
    logger: ApiLogger
    book_id: str
    meta: dict[str, str]
    model: str
    thinking: str
    prompt: Prompt
    prompt_cache: ReviewPromptCache | None
    progress: Progress


def ask(
    run: Run,
    section: dict,
    parts: list[str],
    ids: set[str],
    item: str,
    name: str,
    extra: dict,
) -> tuple[CheckReply, str]:
    """One model call, using the prompt cache when it exists."""
    started = time.perf_counter()
    run.progress.asking(name, "")
    reply = request_check(
        run.client,
        run.model,
        run.prompt.text,
        parts,
        ids,
        run.thinking,
        run.logger,
        item,
        {
            "book_id": run.book_id,
            "grain": "question",
            "section": section["number"],
            "section_title": section["title"],
            "module_path": section["module_path"],
            "prompt_files": run.prompt.files,
            **extra,
        },
        report=lambda message: run.progress.say(f"  {name}: {message}"),
        prompt_cache=run.prompt_cache,
    )
    if reply.status == "abort":
        raise RuntimeError(f"aborted, {reply.abort_reason or 'no reason given'}")
    elapsed = since(time.perf_counter() - started)
    line = ""
    if reply.issues:
        line = f"  {name}: {describe_issues(reply.issues, len(ids))} ({elapsed})"
    return reply, line


def check_question(
    run: Run, section: dict, document: dict, group: dict, exercise: dict
) -> tuple[str, dict[str, list[Issue]], str]:
    """Check one exercise in one call."""
    exercise_id = exercise["id"]
    ids = {exercise_id}
    name = f"{section['number']} {exercise_id}"
    reply, line = ask(
        run,
        section,
        [exercise_part(document, group, exercise)],
        ids,
        f"{section['number']}-{exercise_id}",
        name,
        {"objective": group["objective"], "exercise": exercise_id},
    )
    return name, attach(ids, reply.issues), line


def check_book(
    book_id: str,
    section: str | None,
    papers: dict[str, dict],
    model: str,
    thinking: str,
    update: bool,
    max_workers: int = MAX_WORKERS,
    *,
    import_logs_only: bool = False,
) -> tuple[Path, int]:
    """Check every proofread section of a book, or just one, one exercise at a time."""
    print(f"reading the reviewed {book_id}", flush=True)
    book = reviewed_book(book_id)
    sections = matching_sections(book["sections"], section)
    if section and not sections:
        raise RuntimeError(f"no section matching {section} in the reviewed {book_id}")

    sections = [row for row in sections if row["stem"] in papers]
    if not sections:
        raise RuntimeError(
            f"no proofread exercises for {book_id}"
            + (f" matching {section}" if section else "")
        )

    # The reviewed book's attributes are what this stage writes onto its own files. The
    # prompt is chosen by the table of contents instead: it is the one place a book says
    # what language, numerals and notation it is, and a book reviewed before those were
    # recorded still has them there.
    meta = book_attributes(book)
    prompt = build_prompt(book_metadata(book_id))
    stage_dir = OUTPUT_DIR / book_id / REVIEWS_DIR
    envelope_keys = {"book_id", *meta, "number", "title", "module_path", "review"}

    pending: list[dict] = []
    skipped = 0
    for row in sections:
        paper = papers[row["stem"]]
        out_path = stage_dir / f"{row['stem']}.json"
        fingerprint = review_fingerprint(paper, prompt.text)
        if not update and written_review(out_path, envelope_keys, paper, fingerprint):
            skipped += 1
            continue
        pending.append(row)

    work_rows = list(pending)
    number_to_stem = {row["number"]: row["stem"] for row in sections}
    written: dict[str, dict[str, list[Issue]]] = {row["stem"]: {} for row in work_rows}
    failures: list[str] = []

    logged, log_hits = load_logged_exercise_checks(
        book_id, number_to_stem, papers
    )
    if log_hits:
        for stem, found in logged.items():
            if stem in written:
                written[stem].update(found)
        print(
            f"recovered {log_hits} exercise check(s) from API logs",
            flush=True,
        )
        finished = write_section_reviews(
            book_id,
            meta,
            envelope_keys,
            work_rows,
            papers,
            written,
            prompt.text,
            stage_dir,
        )
        finished_stems = {row["stem"] for row in finished}
        if finished_stems:
            print(
                f"wrote {len(finished)} section review(s) from logs alone",
                flush=True,
            )
        pending = [row for row in pending if row["stem"] not in finished_stems]
        skipped += len(finished_stems)

    if import_logs_only:
        remaining = sum(
            len(exercise_ids(papers[row["stem"]]))
            - len(written.get(row["stem"], {}))
            for row in work_rows
        )
        if remaining:
            print(
                f"{remaining} exercise(s) still unchecked; "
                "run again without --import-logs to call the model",
                flush=True,
            )
        else:
            print("every pending exercise is covered by logs", flush=True)
        summary = f"{len(work_rows) - len(pending)} section review(s) written from logs"
        if skipped:
            summary += f", {skipped} already checked"
        print(f"OK: {summary} under {stage_dir.relative_to(PROJECT_ROOT)}")
        return stage_dir, 0

    if pending:
        tasks = [
            (row, papers[row["stem"]], group, exercise)
            for row in pending
            for group, exercise in paper_exercises(papers[row["stem"]])
            if exercise["id"] not in written.get(row["stem"], {})
        ]
        if not tasks:
            print("every pending exercise is already in API logs; nothing to call", flush=True)
        elif not any(os.environ.get(name) for name in API_KEY_VARS):
            raise RuntimeError(
                f"{len(tasks)} exercise(s) still to check; "
                f"set one of {' / '.join(API_KEY_VARS)} to call the model"
            )
        if tasks:
            workers = min(len(tasks), max_workers)
            opening = (
                f"checking {len(tasks)} exercise(s) across {len(pending)} section(s), "
                f"with {workers} worker(s)"
            )
            if skipped:
                opening += f"; {skipped} already checked"
            if log_hits:
                opening += f"; {log_hits} from logs"
            print(opening, flush=True)

        if not tasks:
            pass
        else:
            print("  opening the model client", flush=True)
            client = genai.Client()
            print("  setting the log clock", flush=True)
            logger = ApiLogger(OUTPUT_DIR / book_id)

            prompt_cache: ReviewPromptCache | None = None
            try:
                print("  caching the examiner prompt", flush=True)
                holder = ReviewPromptCache(client, model, prompt.text, book_id)
                name = holder.current()
                if name is None:
                    print(
                        "    cache failed; sending the prompt with each call",
                        flush=True,
                    )
                else:
                    prompt_cache = holder
                    tokens = prompt_cache.leases[name][0]
                    print(f"    cached, {tokens} token(s)", flush=True)

                with Progress(len(tasks)) as progress:
                    run = Run(
                        client=client,
                        logger=logger,
                        book_id=book_id,
                        meta=meta,
                        model=model,
                        thinking=thinking,
                        prompt=prompt,
                        prompt_cache=prompt_cache,
                        progress=progress,
                    )
                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        futures = {
                            pool.submit(check_question, run, *task): task
                            for task in tasks
                        }
                        for future in as_completed(futures):
                            task = futures[future]
                            row = task[0]
                            call_name = f"{row['number']} {task[3]['id']}"
                            try:
                                name, found, line = future.result()
                            except Exception as error:  # noqa: BLE001
                                failures.append(
                                    f"{row['number']} {task[3]['id']}: "
                                    f"{type(error).__name__}: {error}"
                                )
                                progress.done(
                                    call_name,
                                    f"  {call_name}: failed, "
                                    f"{one_line(f'{type(error).__name__}: {error}')}",
                                )
                                continue
                            if row["stem"] in written:
                                written[row["stem"]].update(found)
                            progress.done(name, line)
                            for issues in found.values():
                                for issue in issues:
                                    progress.say(
                                        f"    {issue.exercise}  {issue.type}  "
                                        f"{one_line(issue.reason)}"
                                    )
            finally:
                cache_names = prompt_cache.names if prompt_cache else []
                if cache_names:
                    dropped = drop_caches(client, cache_names)
                    failed_names = {
                        row.split(":", 1)[0] for row in dropped
                    }
                    now = time.perf_counter()
                    leases = prompt_cache.leases if prompt_cache else {}
                    for name in cache_names:
                        tokens, created = leases[name]
                        was_dropped = name not in failed_names
                        elapsed = now - created if was_dropped else 3600.0
                        log_cache_storage(
                            logger,
                            book_id=book_id,
                            model=model,
                            name=name,
                            tokens=tokens,
                            seconds=elapsed,
                            dropped=was_dropped,
                        )
                    if dropped:
                        print(
                            f"  could not drop {len(dropped)} cache(s): "
                            f"{one_line('; '.join(dropped))}",
                            flush=True,
                        )
                    else:
                        print(f"  dropped {len(cache_names)} prompt cache(s)", flush=True)

    complete = write_section_reviews(
        book_id,
        meta,
        envelope_keys,
        work_rows,
        papers,
        written,
        prompt.text,
        stage_dir,
    )
    summary = f"{len(complete)} section review(s) written"
    if skipped:
        summary += f", {skipped} already checked"
    if failures:
        summary += (
            f", {len(pending) - len(complete)} given up on after "
            f"{len(failures)} failed call(s)"
        )
    print(f"OK: {summary} under {stage_dir.relative_to(PROJECT_ROOT)}")
    return stage_dir, len(failures)


# --- Set replenish: put rejected set members back at a valid size -----------------


class ReplenishReply(BaseModel):
    """New exercises to replace members a set lost in review."""

    status: Literal["ok", "abort"]
    abort_reason: str = Field(
        default="", description="Why the set cannot be written for; empty when ok"
    )
    exercises: list[Exercise] = Field(
        default_factory=list,
        description="Only the new members; not the exercises already kept",
    )


@dataclass(frozen=True)
class SetReplenishJob:
    """One multi-exercise set that needs replacements before the paper may use it."""

    section: str
    stem: str
    group: dict
    objective: str
    target: int
    count: int
    kept: list[dict]
    rejected: list[dict]


def exercise_group_from_dict(group: dict) -> ExerciseGroup:
    """The model shape of one on-disk group, without its ids."""
    body = {key: group[key] for key in ExerciseGroup.model_fields if key in group}
    body["exercises"] = [
        {key: row[key] for key in Exercise.model_fields if key in row}
        for row in group.get("exercises") or []
    ]
    return ExerciseGroup.model_validate(body)


def allowed_set_sizes(group: dict) -> tuple[int, ...]:
    """The counts a multi-exercise set of this shape may hold."""
    return sizes_for(exercise_group_from_dict(group))


def valid_set_size(group: dict) -> bool:
    """True when a group's exercise count is one the paper may print."""
    exercises = group.get("exercises") or []
    if len(exercises) <= 1:
        return True
    return len(exercises) in allowed_set_sizes(group)


def replenish_target(original: dict) -> int | None:
    """The size this set should return to, or None when review did not shrink a set."""
    if len(original.get("exercises") or []) <= 1:
        return None
    sizes = allowed_set_sizes(original)
    original_size = len(original["exercises"])
    if original_size not in sizes:
        return None
    return original_size


def rejected_set_members(
    group_id: str, original: dict, rejected_rows: list[dict]
) -> list[dict]:
    """Rejected exercises of one set, with the reasons review recorded."""
    by_id = {row["id"]: row for row in original.get("exercises") or []}
    found: list[dict] = []
    for row in rejected_rows:
        if row.get("group") != group_id:
            continue
        exercise_id = row.get("exercise")
        if not isinstance(exercise_id, str) or exercise_id not in by_id:
            continue
        found.append(
            {
                "id": exercise_id,
                "exercise": by_id[exercise_id],
                "types": row.get("types") or [],
                "reasons": row.get("reasons") or [],
            }
        )
    return found


def plan_set_replenish(
    document: dict, accepted: dict, rejected_rows: list[dict]
) -> list[SetReplenishJob]:
    """Every multi-exercise set left at an invalid size after review."""
    originals = {group["id"]: group for group in document["exercises"]["groups"]}
    jobs: list[SetReplenishJob] = []
    for group in accepted["exercises"]["groups"]:
        original = originals.get(group["id"])
        if original is None:
            continue
        target = replenish_target(original)
        if target is None:
            continue
        current = len(group.get("exercises") or [])
        if current == target:
            continue
        if current in allowed_set_sizes(group):
            continue
        need = target - current
        if need <= 0:
            continue
        jobs.append(
            SetReplenishJob(
                section=document["number"],
                stem=group["stem"],
                group=group,
                objective=group["objective"],
                target=target,
                count=need,
                kept=list(group["exercises"]),
                rejected=rejected_set_members(group["id"], original, rejected_rows),
            )
        )
    return jobs


def replenish_part(job: SetReplenishJob) -> str:
    """The set, what was kept, what was rejected, and how many to write."""
    payload = {
        "task": "replace rejected set members after question review",
        "section": job.section,
        "objective": job.objective,
        "group": job.group["id"],
        "stem": job.group.get("stem") or "",
        "figures": job.group.get("figures") or [],
        "target_size": job.target,
        "set_sizes": list(SET_SIZES),
        "tall_set_sizes": list(TALL_SET_SIZES),
        "exercises_to_write": job.count,
        "kept_exercises": job.kept,
        "rejected_exercises": job.rejected,
    }
    return "Set replenish JSON:\n" + compact(payload)


def allowed_objectives_for(document: dict, objective: str) -> list[str]:
    """Objective ids a replenish reply may name: this one and every earlier one."""
    rows = (document.get("spec") or {}).get("objectives") or document["exercises"]["objectives"]
    ids = [str(row["id"]) for row in rows]
    if objective not in ids:
        return [objective]
    return ids[: ids.index(objective) + 1]


def replenish_known(prompt: str, part: str, meta: dict[str, str]) -> frozenset[str]:
    """Letters a replenish reply may use: the book's alphabet and the call's own text."""
    from_request = frozenset(
        character for character in prompt + part if character.isalpha()
    )
    return Profile.of(meta).alphabet | from_request


def replenish_write_problems(
    group: ExerciseGroup,
    objective: str,
    allowed: list[str],
    known: frozenset[str],
) -> list[str]:
    """Like new_questions.write_problems for one merged set, without the objective budget rules."""
    problems = write_problems(
        [group],
        len(group.exercises),
        objective,
        allowed,
        known,
    )
    return [
        problem
        for problem in problems
        if "sit in sets" not in problem
        and not problem.endswith(f", not the {len(group.exercises)} asked for")
    ]


def check_replenish(
    reply: ReplenishReply,
    job: SetReplenishJob,
    document: dict,
    book_id: str,
    known: frozenset[str],
) -> None:
    """Reject a reply whose exercises cannot be merged into the set."""
    if reply.status != "ok":
        raise ValueError(reply.abort_reason or "model aborted set replenish")
    if len(reply.exercises) != job.count:
        raise ValueError(
            f"{len(reply.exercises)} exercise(s) written, not the {job.count} asked for"
        )
    trial = exercise_group_from_dict(job.group)
    merged = trial.model_copy(
        update={"exercises": [*trial.exercises, *reply.exercises]}
    )
    kept, _ = repaired(
        [merged], target=len(merged.exercises),
        vector_lesson=section_is_vector_lesson(document),
    )
    kept = repair_groups_mark_placement(repair_groups_excessive_tashkeel(kept))
    if len(kept) != 1:
        raise ValueError("replacements split the set into more than one group")
    group = kept[0]
    sizes = sizes_for(group)
    if len(group.exercises) not in sizes:
        raise ValueError(
            f"set would hold {len(group.exercises)} exercises; "
            f"a set here holds {listed(sizes)} and no other number"
        )
    allowed = allowed_objectives_for(document, job.objective)
    problems = replenish_write_problems(group, job.objective, allowed, known)
    if problems:
        raise ValueError("; ".join(problems))


def logged_replenish_call(
    client,
    model: str,
    prompt: str,
    part: str,
    job: SetReplenishJob,
    document: dict,
    meta: dict[str, str],
    thinking: str,
    logger: ApiLogger,
    item: str,
    attempt: int,
    context: dict,
) -> tuple[ReplenishReply | None, str | None]:
    """Ask once for replacement set members, logging the call."""
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=ReplenishReply,
        thinking_config=types.ThinkingConfig(thinking_level=thinking),
    )
    response = None
    result = None
    error_message = None
    started = time.perf_counter()
    try:
        response = client.models.generate_content(
            model=model,
            contents=[prompt, part],
            config=config,
        )
        result = ReplenishReply.model_validate_json(genai_answer_text(response))
        if result.status == "ok":
            check_replenish(
                result,
                job,
                document,
                context["book_id"],
                replenish_known(prompt, part, meta),
            )
    except Exception as error:  # noqa: BLE001
        error_message = f"{type(error).__name__}: {error}"
        result = None
    duration = time.perf_counter() - started
    safe_log(
        logger,
        files_builder=lambda captured=response: {
            "prompt.txt": prompt,
            "part.txt": part,
            **(genai_response_files(captured) if captured is not None else {}),
        },
        page_number=item,
        step_name=REPLENISH_STEP,
        attempt=attempt,
        api="gemini",
        model=model,
        usage=genai_usage(response) if response is not None else {},
        duration_seconds=duration,
        status="success" if result is not None else "error",
        error_message=error_message,
        extra={
            **context,
            "thinking_level": thinking,
            "returned": result.status if result else None,
            "target_size": job.target,
            "exercises_to_write": job.count,
        },
    )
    return result, error_message


def request_replenish(
    client,
    model: str,
    prompt: str,
    job: SetReplenishJob,
    document: dict,
    meta: dict[str, str],
    thinking: str,
    logger: ApiLogger,
    item: str,
    context: dict,
    report: Callable[[str], None] | None = None,
) -> ReplenishReply:
    """Call the model until it returns usable replacement exercises."""
    part = replenish_part(job)
    last_error: str | None = None
    attempt = 1
    throttled = 0
    while attempt <= MAX_ATTEMPTS:
        result, last_error = logged_replenish_call(
            client,
            model,
            prompt,
            part,
            job,
            document,
            meta,
            thinking,
            logger,
            item,
            attempt,
            context,
        )
        if result is not None:
            return result
        if rate_limited(last_error) and throttled < RATE_LIMIT_WAITS:
            throttled += 1
            pause = RATE_LIMIT_PAUSE * throttled + random.uniform(0, RATE_LIMIT_PAUSE)
            if report is not None:
                report(f"rate limited ({throttled} of {RATE_LIMIT_WAITS}), waiting {pause:.0f}s")
            time.sleep(pause)
            continue
        if attempt < MAX_ATTEMPTS:
            if report is not None:
                report(
                    f"attempt {attempt} of {MAX_ATTEMPTS} failed, "
                    f"{one_line(last_error or 'no reason given')}; "
                    f"asking again in {2**attempt}s"
                )
            time.sleep(2**attempt)
        attempt += 1
    raise RuntimeError(f"failed after {MAX_ATTEMPTS} attempts: {last_error}")


def merge_replenish(job: SetReplenishJob, reply: ReplenishReply) -> None:
    """Append validated replacements to the live group."""
    merged = exercise_group_from_dict(job.group)
    merged = merged.model_copy(
        update={"exercises": [*merged.exercises, *reply.exercises]}
    )
    kept, _ = repaired([merged], target=len(merged.exercises))
    kept = repair_groups_mark_placement(repair_groups_excessive_tashkeel(kept))
    rows = [exercise.model_dump(exclude_defaults=True) for exercise in kept[0].exercises]
    job.group["exercises"] = rows


def renumber_exercises(document: dict) -> None:
    """Number every exercise in paper order after replacements were added."""
    number = 0
    for group in document["exercises"]["groups"]:
        for exercise in group["exercises"]:
            number += 1
            exercise["id"] = f"exercise_{number}"


def assert_sets_valid(document: dict) -> None:
    """Refuse a section that still carries a set the paper cannot print."""
    for group in document["exercises"]["groups"]:
        if not valid_set_size(group):
            sizes = allowed_set_sizes(group)
            raise RuntimeError(
                f"section {document['number']} {group['id']} is a set of "
                f"{len(group['exercises'])}; after review a set holds "
                f"{listed(sizes)} exercises and no other number"
            )


def replenish_sets(
    client,
    model: str,
    thinking: str,
    logger: ApiLogger,
    book_id: str,
    meta: dict[str, str],
    document: dict,
    accepted: dict,
    rejected_rows: list[dict],
    max_workers: int,
) -> int:
    """Write replacements for every set review left at an invalid size. Returns how many."""
    jobs = plan_set_replenish(document, accepted, rejected_rows)
    if not jobs:
        return 0
    prompt = build_write_prompt(meta, book_id)
    workers = min(len(jobs), max_workers)
    print(
        f"  {document['number']}: replenishing {len(jobs)} set(s), "
        f"{sum(job.count for job in jobs)} exercise(s), {workers} worker(s)",
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                request_replenish,
                client,
                model,
                prompt.text,
                job,
                document,
                meta,
                thinking,
                logger,
                f"{document['number']}-{job.group['id']}",
                {"book_id": book_id, "section": document["number"], "group": job.group["id"]},
            ): job
            for job in jobs
        }
        for future in as_completed(futures):
            job = futures[future]
            reply = future.result()
            merge_replenish(job, reply)
    renumber_exercises(accepted)
    recount(accepted)
    assert_sets_valid(accepted)
    return sum(job.count for job in jobs)


# --- Accept: leave the flagged exercises out of the paper -------------------------


def accepted_sections(book_id: str, section: str | None) -> list[tuple[str, dict]]:
    """A book's accepted sections, for drawing the figures and setting the paper; unread sections are skipped."""
    return written_sections(book_id, section, STAGE, "questions_review.py")


def review_issues(path: Path, fingerprint: str) -> list[dict] | None:
    """The issues of a report of this paper, or None when the report cannot be used."""
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        body = report["review"]
        if body["source"] != fingerprint:
            return None
        issues = body["issues"]
        if not isinstance(issues, list):
            return None
    except (OSError, LookupError, TypeError, ValueError):
        return None
    return issues


def exercise_needs_drawn_figure(exercise: dict) -> bool:
    """True when an exercise carries a figure description or names one in its wording."""
    if latex.asked_figures(exercise):
        return True
    for field in (exercise.get("text") or "", exercise.get("answer") or ""):
        if latex.FIGURE_MARKER.search(field):
            return True
    for option in exercise.get("options") or []:
        if latex.FIGURE_MARKER.search(option):
            return True
    return False


def figure_is_drawn(description: str, drawings: dict) -> bool:
    """True when the drawing library has TikZ for this description."""
    return bool(str((drawings.get(description) or {}).get("tikz", "")).strip())


def exercise_uses_undrawn_figure(
    exercise: dict, group: dict, drawings: dict
) -> bool:
    """True when this exercise needs a figure the library has not drawn yet."""
    group_figures = latex.asked_figures(group)
    for description in group_figures:
        if not figure_is_drawn(description, drawings):
            return True
    exercise_figures = latex.asked_figures(exercise)
    for description in exercise_figures:
        if not figure_is_drawn(description, drawings):
            return True
    for field in (
        exercise.get("text") or "",
        exercise.get("answer") or "",
        *(exercise.get("options") or []),
    ):
        for index in latex.marked_indices(field):
            if 1 <= index <= len(exercise_figures) and not figure_is_drawn(
                exercise_figures[index - 1], drawings
            ):
                return True
    return False


def drop_undrawn_figure_exercises(
    document: dict, drawings: dict
) -> tuple[dict, list[dict], int]:
    """A copy of the section without exercises whose figures are not in the library."""
    accepted = copy.deepcopy(document)
    removed: list[dict] = []
    kept: list[dict] = []
    dropped_groups = 0
    for group in accepted["exercises"]["groups"]:
        if any(not figure_is_drawn(d, drawings) for d in latex.asked_figures(group)):
            for exercise in group["exercises"]:
                removed.append(
                    {
                        "section": accepted["number"],
                        "exercise": exercise["id"],
                        "group": group["id"],
                        "objective": group["objective"],
                        "types": ["figure"],
                        "reasons": ["group stem figure is not drawn"],
                    }
                )
            dropped_groups += 1
            continue
        remaining: list[dict] = []
        for exercise in group["exercises"]:
            if exercise_uses_undrawn_figure(exercise, group, drawings):
                removed.append(
                    {
                        "section": accepted["number"],
                        "exercise": exercise["id"],
                        "group": group["id"],
                        "objective": group["objective"],
                        "types": ["figure"],
                        "reasons": ["figure is not drawn"],
                    }
                )
                continue
            remaining.append(exercise)
        if not remaining:
            dropped_groups += 1
            continue
        group["exercises"] = remaining
        kept.append(group)
    accepted["exercises"]["groups"] = kept
    recount(accepted)
    return accepted, removed, dropped_groups


def drop_figure_exercises(document: dict) -> tuple[dict, list[dict], int]:
    """A copy of the section without exercises that need a drawn figure."""
    accepted = copy.deepcopy(document)
    removed: list[dict] = []
    kept: list[dict] = []
    dropped_groups = 0
    for group in accepted["exercises"]["groups"]:
        if latex.asked_figures(group):
            for exercise in group["exercises"]:
                removed.append(
                    {
                        "section": accepted["number"],
                        "exercise": exercise["id"],
                        "group": group["id"],
                        "objective": group["objective"],
                        "types": ["figure"],
                        "reasons": ["exercise set shares a figure on the stem"],
                    }
                )
            dropped_groups += 1
            continue
        remaining: list[dict] = []
        for exercise in group["exercises"]:
            if exercise_needs_drawn_figure(exercise):
                removed.append(
                    {
                        "section": accepted["number"],
                        "exercise": exercise["id"],
                        "group": group["id"],
                        "objective": group["objective"],
                        "types": ["figure"],
                        "reasons": ["exercise needs a drawn figure"],
                    }
                )
                continue
            remaining.append(exercise)
        if not remaining:
            dropped_groups += 1
            continue
        group["exercises"] = remaining
        kept.append(group)
    accepted["exercises"]["groups"] = kept
    recount(accepted)
    return accepted, removed, dropped_groups


def strip_figures_book(book_id: str, section: str | None) -> Path:
    """Drop every accepted exercise that needs a figure, rewriting the section files."""
    documents = accepted_sections(book_id, section)
    if not documents:
        raise RuntimeError(f"no accepted exercises for {book_id}" + (f" matching {section}" if section else ""))
    stage_dir = OUTPUT_DIR / book_id / STAGE
    rejected_path = stage_dir / REJECTED_FILE
    existing_rejected: list[dict] = []
    if rejected_path.is_file():
        existing_rejected = json.loads(rejected_path.read_text(encoding="utf-8"))
    removed_total = 0
    all_removed: list[dict] = []
    for stem, document in documents:
        before = document["exercises"]["total"]
        accepted, removed, dropped = drop_figure_exercises(document)
        removed_total += len(removed)
        all_removed += removed
        (stage_dir / f"{stem}.json").write_text(
            json.dumps(accepted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        after = accepted["exercises"]["total"]
        line = f"  {document['number']}: {len(removed)} figure exercise(s) removed of {before}"
        if dropped:
            line += f", {dropped} group(s) dropped"
        if after != before:
            short = shortfalls(accepted)
            if short:
                line += f"; {', '.join(short)} short"
        print(line, flush=True)
    stripped_ids = {row["exercise"] for row in all_removed}
    kept_rejected = [
        row for row in existing_rejected if row.get("exercise") not in stripped_ids
    ]
    rejected_path.write_text(
        json.dumps(kept_rejected + all_removed, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"OK: {len(documents)} section(s) written under {stage_dir.relative_to(PROJECT_ROOT)}: "
        f"{removed_total} figure exercise(s) removed"
    )
    return stage_dir


def strip_undrawn_figures_book(book_id: str, section: str | None) -> Path:
    """Drop accepted exercises whose figures are missing from the drawing library."""
    from draw import library

    documents = accepted_sections(book_id, section)
    if not documents:
        raise RuntimeError(
            f"no accepted exercises for {book_id}"
            + (f" matching {section}" if section else "")
        )
    drawings = library(book_id)
    stage_dir = OUTPUT_DIR / book_id / STAGE
    rejected_path = stage_dir / REJECTED_FILE
    existing_rejected: list[dict] = []
    if rejected_path.is_file():
        existing_rejected = json.loads(rejected_path.read_text(encoding="utf-8"))
    removed_total = 0
    all_removed: list[dict] = []
    for stem, document in documents:
        before = document["exercises"]["total"]
        accepted, removed, dropped = drop_undrawn_figure_exercises(document, drawings)
        removed_total += len(removed)
        all_removed += removed
        (stage_dir / f"{stem}.json").write_text(
            json.dumps(accepted, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        after = accepted["exercises"]["total"]
        line = (
            f"  {document['number']}: {len(removed)} undrawn figure exercise(s) "
            f"removed of {before}"
        )
        if dropped:
            line += f", {dropped} group(s) dropped"
        if after != before:
            short = shortfalls(accepted)
            if short:
                line += f"; {', '.join(short)} short"
        print(line, flush=True)
    stripped_ids = {row["exercise"] for row in all_removed}
    kept_rejected = [
        row for row in existing_rejected if row.get("exercise") not in stripped_ids
    ]
    rejected_path.write_text(
        json.dumps(kept_rejected + all_removed, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"OK: {len(documents)} section(s) written under "
        f"{stage_dir.relative_to(PROJECT_ROOT)}: "
        f"{removed_total} undrawn figure exercise(s) removed",
        flush=True,
    )
    return stage_dir


def recount(document: dict) -> None:
    """Put the section's totals back from the groups that remain."""
    body = document["exercises"]
    total = word_problems = 0
    per_objective = {
        row["id"]: {"exercises": 0, "word_problems": 0} for row in body["objectives"]
    }
    for group in body["groups"]:
        for exercise in group["exercises"]:
            total += 1
            per_objective[group["objective"]]["exercises"] += 1
            if exercise.get("word_problem"):
                word_problems += 1
                per_objective[group["objective"]]["word_problems"] += 1
    body["total"] = total
    body["word_problems"] = word_problems
    for row in body["objectives"]:
        counts = per_objective[row["id"]]
        row["exercises"] = counts["exercises"]
        row["word_problems"] = counts["word_problems"]


def shortfalls(document: dict) -> list[str]:
    """Objectives left with fewer exercises than they were budgeted, as notes for the log."""
    notes: list[str] = []
    for row in document["exercises"]["objectives"]:
        budget = row.get("budget")
        if budget is None:
            continue
        if row["exercises"] < budget:
            notes.append(f"{row['id']} {row['exercises']} of budget {budget}")
    return notes


def rule_issues(document: dict, profile: Profile) -> list[dict]:
    """Issues accept will reject without waiting for the model: odd $ and braces."""
    issues: list[dict] = []
    for group in document.get("exercises", {}).get("groups") or []:
        for exercise in group.get("exercises") or []:
            exercise_id = str(exercise.get("id") or "")
            if not exercise_id:
                continue
            for what in maths_delimiters(exercise, profile):
                issues.append(
                    {
                        "exercise": exercise_id,
                        "type": "maths_delimiters",
                        "reason": what,
                    }
                )
    return issues


def reject(document: dict, issues: list[dict]) -> tuple[dict, list[dict], int]:
    """A copy of the section without the flagged exercises, the records of them, and how many groups went."""
    accepted = copy.deepcopy(document)
    by_exercise: dict[str, list[dict]] = {}
    for issue in issues:
        exercise_id = issue.get("exercise")
        if isinstance(exercise_id, str) and exercise_id:
            by_exercise.setdefault(exercise_id, []).append(issue)

    rejected: list[dict] = []
    kept: list[dict] = []
    dropped = 0
    for group in accepted["exercises"]["groups"]:
        remaining: list[dict] = []
        for exercise in group["exercises"]:
            found = by_exercise.get(exercise["id"])
            if found:
                rejected.append(
                    {
                        "section": accepted["number"],
                        "exercise": exercise["id"],
                        "group": group["id"],
                        "objective": group["objective"],
                        "types": [row.get("type") or "" for row in found],
                        "reasons": [row.get("reason") or "" for row in found],
                    }
                )
                continue
            remaining.append(exercise)
        if not remaining:
            dropped += 1
            continue
        group["exercises"] = remaining
        kept.append(group)
    accepted["exercises"]["groups"] = kept
    recount(accepted)
    return accepted, rejected, dropped


def describe_accept(
    number: str,
    written: int,
    rejected: int,
    dropped: int,
    short: list[str],
    unchecked: bool,
) -> str | None:
    """The line worth printing for one section, or None when nothing happened to it."""
    if unchecked:
        return (
            f"  {number}: no usable report; {written} exercise(s) passed through unrejected"
        )
    if not rejected and not dropped and not short:
        return None
    notes = f"  {number}: {rejected} exercise(s) rejected of {written}"
    if dropped:
        notes += f", {dropped} group(s) dropped"
    if short:
        notes += f"; {', '.join(short)} short"
    return notes


def accept_book(
    book_id: str,
    section: str | None,
    documents: list[tuple[str, dict]],
    model: str,
    thinking: str,
    max_workers: int,
) -> tuple[Path, int, int, int]:
    """Accept every proofread section of a book, or one, writing the surviving exercises."""
    print(f"accepting {len(documents)} section(s)", flush=True)
    meta = book_attributes(reviewed_book(book_id))
    book_meta = book_metadata(book_id)
    prompt = build_prompt(book_meta)
    profile = Profile.of(book_meta)
    stage_dir = OUTPUT_DIR / book_id / STAGE
    review_dir = OUTPUT_DIR / book_id / REVIEWS_DIR
    stage_dir.mkdir(parents=True, exist_ok=True)

    rejected: list[dict] = []
    unchecked = 0
    replenish_jobs = 0
    client = None
    logger = None
    for stem, document in documents:
        written = document["exercises"]["total"]
        fingerprint = review_fingerprint(document, prompt.text)
        model_issues = review_issues(review_dir / f"{stem}.json", fingerprint)
        rules = rule_issues(document, profile)
        if model_issues is None and not rules:
            accepted = copy.deepcopy(document)
            dropped = 0
            found: list[dict] = []
            unchecked += 1
            line = describe_accept(document["number"], written, 0, 0, [], True)
        else:
            accepted, found, dropped = reject(document, (model_issues or []) + rules)
            line = describe_accept(
                document["number"], written, len(found), dropped, shortfalls(accepted), False
            )
        pending = plan_set_replenish(document, accepted, found)
        if pending:
            if not any(os.environ.get(name) for name in API_KEY_VARS):
                raise RuntimeError(
                    f"section {document['number']} has {len(pending)} set(s) at an invalid "
                    f"size after review; set one of {' / '.join(API_KEY_VARS)} to replenish them"
                )
            if client is None:
                client = genai.Client()
                logger = ApiLogger(OUTPUT_DIR / book_id)
            replenish_jobs += replenish_sets(
                client,
                model,
                thinking,
                logger,
                book_id,
                meta,
                document,
                accepted,
                found,
                max_workers,
            )
        else:
            assert_sets_valid(accepted)
        accepted = canonicalize_document(accepted)
        (stage_dir / f"{stem}.json").write_text(
            json.dumps(accepted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if line:
            print(line, flush=True)
        rejected += found

    # The rejections of the whole book, or of the one section when that is all that
    # was read: what was recorded last time about a section not read this time is kept.
    rejected_path = stage_dir / REJECTED_FILE
    kept: list[dict] = []
    if section is not None and rejected_path.is_file():
        read = {document["number"] for _, document in documents}
        kept = [
            row
            for row in json.loads(rejected_path.read_text(encoding="utf-8"))
            if row.get("section") not in read
        ]
    rejected_path.write_text(
        json.dumps(kept + rejected, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    summary = (
        f"OK: {len(documents)} section(s) accepted under {stage_dir.relative_to(PROJECT_ROOT)}: "
        f"{len(rejected)} rejected"
    )
    if replenish_jobs:
        summary += f", {replenish_jobs} set exercise(s) regenerated"
    if unchecked:
        summary += f", {unchecked} unchecked"
    print(summary)
    return stage_dir, len(documents), len(rejected), unchecked


# --- The three passes as one stage ------------------------------------------------


def review_book(
    book_id: str,
    section: str | None,
    model: str,
    thinking: str,
    update: bool,
    max_workers: int,
) -> int:
    """Proofread, check and accept a book, or one section of it. Returns the stage's exit code."""
    documents, _ = proofread_book(book_id, section)
    papers = {stem: paper for stem, paper in documents}
    _, failed = check_book(
        book_id, section, papers, model, thinking, update, max_workers
    )
    accept_book(book_id, section, documents, model, thinking, max_workers)
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_id", help="Book ID, e.g. eg_mathematics_en_g7_t1")
    parser.add_argument(
        "-s",
        "--section",
        help="review this section, or every section under it, e.g. -s 1 or -s 01",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="recheck sections that already have a report",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"model to call (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--thinking-level",
        default=DEFAULT_THINKING,
        choices=THINKING_LEVELS,
        help=f"how hard the model thinks (default: {DEFAULT_THINKING})",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=MAX_WORKERS,
        help=f"most calls in flight at once (default: {MAX_WORKERS}); lower it when the "
        "account's spending rate is exceeded",
    )
    parser.add_argument(
        "--strip-figures",
        action="store_true",
        help="drop accepted exercises that need a drawn figure, then exit",
    )
    parser.add_argument(
        "--strip-undrawn-figures",
        action="store_true",
        help="drop accepted exercises whose figures are not in the drawing library, then exit",
    )
    parser.add_argument(
        "--import-logs",
        action="store_true",
        help="write section reviews from successful 00_api_call_logs only; no model calls",
    )
    args = parser.parse_args()

    try:
        if args.strip_figures and args.strip_undrawn_figures:
            print(
                "FAILED: use only one of --strip-figures and --strip-undrawn-figures",
                file=sys.stderr,
            )
            return 1
        if args.strip_figures:
            strip_figures_book(args.book_id, args.section)
            return 0
        if args.strip_undrawn_figures:
            strip_undrawn_figures_book(args.book_id, args.section)
            return 0
        if args.import_logs:
            documents, _ = proofread_book(args.book_id, args.section)
            papers = {stem: paper for stem, paper in documents}
            check_book(
                args.book_id,
                args.section,
                papers,
                args.model,
                args.thinking_level,
                args.update,
                max(1, args.workers),
                import_logs_only=True,
            )
            return 0
        return review_book(
            args.book_id,
            args.section,
            args.model,
            args.thinking_level,
            args.update,
            max(1, args.workers),
        )
    except RuntimeError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
