#!/usr/bin/env python3
"""Collect the questions a book already prints, section by section.

Every source PDF a section is drawn from goes into a single call, and the model
returns the questions those pages ask, grouped as the book prints them and tagged
with the objectives they practise. Nothing here writes a question: this stage is
a faithful record of what the sources already ask, for whatever writes new
practice later to model itself on and to avoid repeating.

Two stages come first. sources.py builds the section PDFs this reads, and review.py
supplies the objectives a question is tagged with, taken from the reviewed book so
that an objective the review removed is not tagged against.

Sections are extracted concurrently, one worker per section, because no section
needs anything another section produces. Lines therefore appear as each section
lands rather than in the order of the book.

A book is a hundred-odd megabytes of PDFs and every worker pushes its own at
once, so the upload is reported as it goes and where the book stands is reported
whenever half a minute passes with nothing else to say.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    from google import genai
    from google.genai import types
    from pydantic import BaseModel, Field

    from src.api_logger import (
        ApiLogger,
        finish_reason,
        genai_answer_text,
        genai_finish_reason,
        genai_response_files,
        genai_usage,
        safe_log,
    )
    from src.arabic_verse import Poem
    from src.prompting import Prompt, assemble
    from src.geometry_arrows import canonicalize_document
    from review import book_attributes, reviewed_book
    from specs import (
        Progress,
        delete_uploads,
        in_mb,
        one_line,
        section_pdfs,
        since,
        upload_pdfs,
    )
    from sources import (
        PROJECT_ROOT,
        SECTIONS_STAGE,
        OUTPUT_DIR,
        book_metadata,
        book_spans,
        matching_sections,
        number_pad_width,
        section_spans,
    )
except ModuleNotFoundError as error:  # pragma: no cover
    sys.exit(f"missing dependency ({error.name}): pip install -r requirements.txt")

# The prompt is assembled from prompts/source_questions/ by prompting.py, from
# the parts the book's language, numerals and notation choose.
PROMPT_STAGE = "source_questions"

QUESTIONS_STAGE = "07_section_source_questions"
QUESTIONS_STEP = "section_questions"

# What an extraction on disk holds. Kept here rather than read off a dumped reply,
# so that the skip check and the writer are held to one list.
EXTRACT_KEYS = ("count", "objectives", "groups")

# A table transcribed as Markdown rather than as LaTeX: a row of cells fenced by
# pipes, or the dashed rule a Markdown table puts under its heading. Nothing
# downstream reads Markdown, so such a table prints as its own pipes and colons; and
# these questions are shown to the writing stage as examples, so one transcribed this
# way teaches the writing to do the same.
MARKDOWN_ROW = re.compile(r"^[ \t]*\|.*\|[ \t]*$", re.M)
MARKDOWN_RULE = re.compile(
    r"^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(\|[ \t]*:?-{3,}:?[ \t]*)*\|?[ \t]*$", re.M
)

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_THINKING = "high"
THINKING_LEVELS = ("minimal", "low", "medium", "high")
API_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
MAX_ATTEMPTS = 4

RECITATION_RECOVERY_SUFFIX = """

## Recitation recovery
The previous structured reply was blocked for quoting too much verbatim text from the PDF
(listening scripts, reading passages, poems). Return status "ok" with every question the
pages ask. Paraphrase stems and question wording in short school English; do not paste
consecutive sentences from the book. Keep maths, blanks, and multiple-choice options as
printed. Tag objectives as before."""

RECITATION_STRUCTURE_SUFFIX = """

## Recitation recovery (structure only)
The API blocked quoting the listening script or passage text. Return status "ok". Do not
copy the script, dialogue, or any consecutive printed sentences from the PDF. For each
exercise you can see, write a one-line stem such as "Answer using the listening passage
in the textbook (script not reproduced here)." Paraphrase each question in one short
sentence; keep multiple-choice options when the page shows them. Tag objectives. Extract
every numbered task or question you can identify from headings and layout even when the
script itself is omitted."""

# The documented generation cap. Calls already stop around 65,521; this records
# the limit rather than changing it. Truncation is handled by splitting per PDF.
MAX_OUTPUT_TOKENS = 65_536
SIZE_ABORT = re.compile(
    r"output capacity|too (long|large)|exceeds the single-response",
    re.I,
)
# One worker per section still to be extracted, and the ceiling that keeps a book
# with hundreds of sections from holding hundreds of PDFs in memory and hundreds
# of calls in flight at once.
MAX_WORKERS = 25


class Question(BaseModel):
    """One question as printed in the book, without the book's answer or working.

    Only `text` is required. The rest is left out of the reply altogether where it
    does not apply, which keeps both the answer and the file below down to what a
    question actually has.
    """

    text: str = Field(
        description="Faithful transcription of the question, maths as LaTeX, any choices included"
    )
    figures: list[str] = Field(
        default_factory=list,
        description="One description per figure the question needs; omit when it needs none",
    )
    poems: list[Poem] = Field(
        default_factory=list,
        description="Verse blocks this question reprints; place with [poem N]; omit when none",
    )
    objectives: list[str] = Field(
        default_factory=list,
        description="Ids of the section's objectives this question practises; omit when none",
    )


class QuestionGroup(BaseModel):
    """One or more questions sharing a single stem printed once in the book.

    A standalone question is a group of one with no `stem`.
    """

    stem: str = Field(
        default="",
        description="Statement shared by every question below it; omit when it stands alone",
    )
    figures: list[str] = Field(
        default_factory=list,
        description="One description per figure belonging to the shared stem; omit when none",
    )
    poems: list[Poem] = Field(
        default_factory=list,
        description="Verse blocks the stem reprints; place with [poem N] in stem; omit when none",
    )
    questions: list[Question] = Field(min_length=1)


class ExtractReply(BaseModel):
    """The model's answer, which may refuse the section instead of extracting it."""

    status: Literal["ok", "abort"]
    abort_reason: str = Field(
        default="", description="Why the pages cannot be extracted; empty when status is ok"
    )
    groups: list[QuestionGroup] = Field(
        default_factory=list,
        description="The section's questions in printed order; empty when it asks nothing",
    )


# A row on disk carries its id alongside the model's own fields. Read off the
# models so that renaming a field cannot leave the skip check behind, and the
# required ones are those the model must send.
GROUP_KEYS = {"id", *QuestionGroup.model_fields}
GROUP_REQUIRED = {"id"} | {
    name for name, field in QuestionGroup.model_fields.items() if field.is_required()
}
QUESTION_KEYS = {"id", *Question.model_fields}
QUESTION_REQUIRED = {"id"} | {
    name for name, field in Question.model_fields.items() if field.is_required()
}


def build_prompt(meta: dict[str, str]) -> Prompt:
    """The prompt this stage sends for the book, assembled from its parts."""
    return assemble(PROMPT_STAGE, meta)


def objectives(section: dict) -> list[dict]:
    """The objectives of this section's reviewed spec, in order, as the model sees them.

    The objectives and nothing else of the spec. This stage transcribes pages, so
    the teaching points, glossary and excerpts would be a second account of the
    same section for the model to drift towards; the objectives are here only
    because a question has to be labelled with one of them.
    """
    return [
        {"id": row["id"], "text": row["text"]} for row in section["spec"]["objectives"]
    ]


def section_part(section: dict, pdfs: list[Path], rows: list[dict]) -> str:
    """The section as the model sees it: what it is, its sources, its objectives.

    The sources are named in the order their PDFs follow, so the model can keep
    the questions in the order the book prints them across more than one source.
    """
    body = json.dumps(
        {
            "number": section["number"],
            "title": section["title"],
            "module_path": section["module_path"],
            "sources": [path.name for path in pdfs],
            "objectives": rows,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"Section JSON:\n{body}"


def repaired(groups: list[QuestionGroup]) -> tuple[list[QuestionGroup], int]:
    """Split every group that holds several questions but shares no stem.

    A group is one stem the book prints over several questions, so a group without
    one is not a group at all: nothing on the page ties those questions together,
    and a later stage numbering them as a set would head the set with nothing.
    Giving each its own group keeps every question and is exactly what the prompt
    asks for, so it is repaired here rather than sent back — the model does this
    on perhaps a third of sections, and each retry is another long call over a
    difference the questions themselves do not notice.

    The group's own figures follow its first question, which is where a figure
    printed above a run of questions sits.
    """
    kept: list[QuestionGroup] = []
    split = 0
    for group in groups:
        if len(group.questions) == 1 or group.stem.strip():
            kept.append(group)
            continue
        split += 1
        kept += [
            QuestionGroup(
                figures=group.figures if index == 0 else [], questions=[question]
            )
            for index, question in enumerate(group.questions)
        ]
    return kept, split


def markdown_table(field: str) -> str:
    """The first row this field writes as a Markdown table, or "" where it writes none."""
    found = MARKDOWN_ROW.search(field) or MARKDOWN_RULE.search(field)
    return " ".join(found.group(0).split()) if found else ""


def extract_problems(groups: list[QuestionGroup], ids: list[str]) -> list[str]:
    """Every reason an extraction cannot be used, as lines for the report.

    Four rules carry the stage. A question with no wording is nothing at all. A
    tag that names an objective the section does not have cannot be attached to
    anything. A table written as Markdown is a table lost, since nothing that reads
    this file reads Markdown. And a group of several questions with no stem is not a
    group, though that one is repaired before it is ever checked, so it stands here to
    keep such a group off disk rather than to fail a reply. The rest are checked
    before a reply is accepted, so a breach is retried like a parse failure.
    """
    problems: list[str] = []

    for index, group in enumerate(groups, start=1):
        if len(group.questions) > 1 and not group.stem.strip():
            problems.append(
                f"group {index} holds {len(group.questions)} questions but shares no stem"
            )
        if row := markdown_table(group.stem):
            problems.append(
                f"group {index} stem writes the table row {row!r} as Markdown; transcribe a "
                r"table as LaTeX in $$\begin{array}...\end{array}$$"
            )
        for number, question in enumerate(group.questions, start=1):
            if not question.text.strip():
                problems.append(f"group {index} question {number} has no text")
            if row := markdown_table(question.text):
                problems.append(
                    f"group {index} question {number} writes the table row {row!r} as "
                    r"Markdown; transcribe a table as LaTeX in $$\begin{array}...\end{array}$$"
                )
            for unknown in [name for name in question.objectives if name not in ids]:
                problems.append(
                    f"group {index} question {number} names {unknown}, "
                    f"which is not an objective of this section"
                )

    return problems


def check_extract(reply: ExtractReply, ids: list[str]) -> None:
    """Reject an extraction that cannot be used, reporting every reason at once."""
    problems = extract_problems(reply.groups, ids)
    if problems:
        raise ValueError("; ".join(problems))


def size_abort(reply: ExtractReply | None) -> bool:
    """True when the model refused because the reply would not fit in one call."""
    return bool(
        reply is not None
        and reply.status == "abort"
        and SIZE_ABORT.search(reply.abort_reason or "")
    )


def truncated_extract(
    reply: ExtractReply | None, error: str | None, finish: str | None
) -> bool:
    """True when the call hit the output cap, including a short valid JSON reply."""
    if finish and "MAX_TOKENS" in str(finish):
        return True
    return size_abort(reply)


def generate_extract_response(
    client,
    model: str,
    prompt: str,
    section: str,
    parts: list[types.Part],
    extra_contents: list[str] | None,
    config: types.GenerateContentConfig,
    context: dict,
) -> tuple[object, str, str]:
    """One generate_content call, with paraphrase then structure-only retries on RECITATION."""

    def call(prompt_text: str) -> tuple[object, str, str]:
        contents: list = [prompt_text, section, *(extra_contents or []), *parts]
        response = client.models.generate_content(
            model=model, contents=contents, config=config
        )
        return response, genai_answer_text(response), genai_finish_reason(response)

    response, answer, finish = call(prompt)
    if answer.strip() or finish != "RECITATION":
        return response, answer, finish
    if not context.get("recitation_recovery"):
        context["recitation_recovery"] = True
        response, answer, finish = call(prompt + RECITATION_RECOVERY_SUFFIX)
        if answer.strip() or finish != "RECITATION":
            return response, answer, finish
    if not context.get("recitation_structure"):
        context["recitation_structure"] = True
        response, answer, finish = call(prompt + RECITATION_STRUCTURE_SUFFIX)
    return response, answer, finish


def logged_call(
    client,
    model: str,
    prompt: str,
    section: str,
    parts: list[types.Part],
    ids: list[str],
    thinking: str,
    logger: ApiLogger,
    item: str,
    attempt: int,
    context: dict,
    extra_contents: list[str] | None = None,
) -> tuple[ExtractReply | None, str | None, str | None]:
    """Ask for one section's questions once, logging the call however it turns out.

    Returns the model's reply, or None alongside the failure to report, and the
    SDK finish_reason even when the JSON did not parse.
    """
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=ExtractReply,
        thinking_config=types.ThinkingConfig(thinking_level=thinking),
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    response = None
    result = None
    error_message = None
    split = 0
    started = time.perf_counter()
    try:
        response, answer, finish = generate_extract_response(
            client,
            model,
            prompt,
            section,
            parts,
            extra_contents,
            config,
            context,
        )
        if not answer.strip() and finish == "RECITATION":
            result = ExtractReply(
                status="abort",
                abort_reason=(
                    "Model blocked verbatim output (RECITATION). "
                    "Re-run with --update, try --thinking-level medium, "
                    "or split the section."
                ),
            )
        else:
            result = ExtractReply.model_validate_json(answer)
        if result.status == "ok":
            result.groups, split = repaired(result.groups)
            check_extract(result, ids)
    except Exception as error:  # noqa: BLE001 - retry transport, parse and rule failures alike
        error_message = f"{type(error).__name__}: {error}"
        result = None
    finish = finish_reason(response)
    duration = time.perf_counter() - started

    extra = {
        **context,
        "thinking_level": thinking,
        "returned": result.status if result else None,
        "groups_split": split,
        "finish_reason": finish,
    }
    if extra_contents:
        extra["pdf_label"] = extra_contents[0]
    safe_log(
        logger,
        files_builder=lambda captured=response: {
            "prompt.txt": prompt,
            "section.txt": section,
            **(genai_response_files(captured) if captured is not None else {}),
        },
        page_number=item,
        step_name=QUESTIONS_STEP,
        attempt=attempt,
        api="gemini",
        model=model,
        usage=genai_usage(response) if response is not None else {},
        duration_seconds=duration,
        status="success" if result is not None else "error",
        error_message=error_message,
        extra=extra,
    )
    return result, error_message, finish


def recitation_blocked(reply: ExtractReply | None, finish: str | None) -> bool:
    """True when the model refused verbatim output and returned an abort shell."""
    return bool(
        reply is not None
        and reply.status == "abort"
        and reply.abort_reason
        and "RECITATION" in reply.abort_reason
    )


def request_extract(
    client,
    model: str,
    prompt: str,
    section: str,
    parts: list[types.Part],
    ids: list[str],
    thinking: str,
    logger: ApiLogger,
    item: str,
    context: dict,
    report: Callable[[str], None] | None = None,
    extra_contents: list[str] | None = None,
) -> tuple[ExtractReply | None, str | None, str | None]:
    """Call the model until it returns a usable extraction.

    Schema and check_extract failures retry up to MAX_ATTEMPTS. Truncation
    (MAX_TOKENS, even with valid JSON, or a size-abort) stops at once so the
    caller can split per PDF rather than spend the remaining identical attempts.
    """
    last_error: str | None = None
    last_finish: str | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        result, last_error, last_finish = logged_call(
            client,
            model,
            prompt,
            section,
            parts,
            ids,
            thinking,
            logger,
            item,
            attempt,
            context,
            extra_contents=extra_contents,
        )
        if truncated_extract(result, last_error, last_finish):
            return result, last_error, last_finish
        if recitation_blocked(result, last_finish):
            return result, None, last_finish
        if result is not None:
            return result, None, last_finish
        if attempt < MAX_ATTEMPTS:
            # A section can spend four long calls here without a word otherwise,
            # since a tag that names no objective of the section is refused and
            # asked for again just as a transport failure is.
            if report is not None:
                report(
                    f"attempt {attempt} of {MAX_ATTEMPTS} failed, "
                    f"{one_line(last_error or 'no reason given')}; "
                    f"asking again in {2**attempt}s"
                )
            time.sleep(2**attempt)

    raise RuntimeError(f"failed after {MAX_ATTEMPTS} attempts: {last_error}")


def extract_with_fallback(
    request: Callable,
    parts: list,
    pdfs: list[Path],
    thinking: str,
    item: str,
    ids: list[str],
) -> ExtractReply:
    """Whole-section call, then per-PDF at the same thinking level, then one medium retry.

    `request(parts, extra_contents, thinking, item) -> (reply, error, finish)`.
    """
    reply, error, finish = request(parts, None, thinking, item)
    if recitation_blocked(reply, finish) and thinking == "high":
        reply, error, finish = request(
            parts,
            [
                "Recitation blocked. Return status ok. Do not copy listening scripts; "
                "paraphrase each task in one short sentence and tag objectives."
            ],
            "medium",
            f"{item}-recitation-medium",
        )
    if reply is not None and reply.status == "abort" and not size_abort(reply):
        return reply
    if (
        reply is not None
        and reply.status == "ok"
        and not truncated_extract(reply, error, finish)
    ):
        return reply
    if not truncated_extract(reply, error, finish):
        raise RuntimeError(error or "extract failed")

    if len(pdfs) > 1:
        groups: list[QuestionGroup] = []
        for index, (pdf, part) in enumerate(zip(pdfs, parts), start=1):
            extra = [f"source PDF {index} of {len(pdfs)}: {pdf.name}"]
            pdf_item = f"{item}-pdf{index}"
            pdf_reply, pdf_error, pdf_finish = request([part], extra, thinking, pdf_item)
            if (
                pdf_reply is not None
                and pdf_reply.status == "abort"
                and not size_abort(pdf_reply)
            ):
                raise RuntimeError(pdf_reply.abort_reason or "aborted")
            if truncated_extract(pdf_reply, pdf_error, pdf_finish):
                if thinking != "high":
                    raise RuntimeError(f"truncated extracting {pdf.name}")
                pdf_reply, pdf_error, pdf_finish = request(
                    [part], extra, "medium", f"{pdf_item}-medium"
                )
                if (
                    truncated_extract(pdf_reply, pdf_error, pdf_finish)
                    or pdf_reply is None
                    or pdf_reply.status != "ok"
                ):
                    raise RuntimeError(f"truncated extracting {pdf.name} at medium")
            elif pdf_reply is None or pdf_reply.status != "ok":
                raise RuntimeError(pdf_error or f"failed extracting {pdf.name}")
            groups.extend(pdf_reply.groups)
        merged = ExtractReply(status="ok", groups=groups)
        check_extract(merged, ids)
        return merged

    if thinking != "high":
        raise RuntimeError(error or "truncated extract")
    reply, error, finish = request(parts, None, "medium", f"{item}-medium")
    if reply is not None and reply.status == "abort" and not size_abort(reply):
        return reply
    if (
        reply is not None
        and reply.status == "ok"
        and not truncated_extract(reply, error, finish)
    ):
        return reply
    raise RuntimeError(error or reply.abort_reason if reply else "truncated extract")


def questions_per_page(groups: list[QuestionGroup], pdfs: list[Path]) -> float | None:
    """Questions extracted per source page, or None when the PDFs have no pages."""
    from sources import pdf_page_count

    pages = sum(pdf_page_count(path) or 0 for path in pdfs)
    if not pages:
        return None
    return sum(len(group.questions) for group in groups) / pages


def record_questions_per_page(
    logger: ApiLogger, item: str, rate: float, report: Callable[[str], None] | None
) -> None:
    """Write questions_per_page onto the latest call.json and warn when it is sparse."""
    if report is not None and rate < 2.0:
        report(
            f"sparse extraction: {rate:.1f} questions per page "
            "(under 2.0; a truncated high reply may have held more)"
        )
    folders = sorted(
        logger.logs_folder.glob(f"*_{item}_{QUESTIONS_STEP}*"),
        key=lambda path: path.name,
        reverse=True,
    )
    if not folders:
        return
    path = folders[0] / "call.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["questions_per_page"] = round(rate, 2)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except (OSError, ValueError):
        return


def objective_counts(tagged: list[list[str]], ids: list[str]) -> list[dict]:
    """How many questions practise each of the section's objectives, in spec order.

    Counted from the tags rather than asked for, so the file cannot disagree with
    itself. An objective nothing practises keeps its row and carries a zero, which
    is the number worth seeing: it says the book gives that objective no practice
    of its own. A question tagged with two objectives counts under each, so these
    need not add up to the section's total, and questions tagged with none are in
    the total but in no row.
    """
    tally = Counter(name for tags in tagged for name in tags)
    return [{"id": name, "questions": tally[name]} for name in ids]


def extract_document(groups: list[QuestionGroup], ids: list[str]) -> dict:
    """The questions as they are written to disk, with every row numbered.

    Questions are numbered straight through the section rather than within their
    group, so that question_14 is the fourteenth question the section asks however
    the book chose to group them, and a later stage can cite it. The count is
    summed here rather than asked for, so the file cannot disagree with itself.
    """
    numbered = []
    count = 0
    for index, group in enumerate(groups, start=1):
        body = group.model_dump(exclude_defaults=True)
        rows = []
        for question in body["questions"]:
            count += 1
            rows.append({"id": f"question_{count}", **question})
        numbered.append({"id": f"group_{index}", **body, "questions": rows})

    tagged = [question.objectives for group in groups for question in group.questions]
    return {"count": count, "objectives": objective_counts(tagged, ids), "groups": numbered}


def written_extract(path: Path, envelope_keys: set[str], ids: list[str]) -> bool:
    """True when a section already holds questions of the shape written today.

    Validation alone is too weak to decide this: unknown keys are ignored and
    absent arrays take their empty default, so a file written before a field was
    renamed would pass while quietly holding the old name. Comparing the keys of
    every row is what catches that. The objectives are checked against the reviewed
    spec as it stands too, because this stage reads a file another stage can
    rewrite: an extraction tagged with an objective the review has since removed
    has to be asked for again rather than carried forward.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        body = document["questions"]
        if set(document) != envelope_keys or set(body) != set(EXTRACT_KEYS):
            return False
        groups = []
        for group in body["groups"]:
            if not GROUP_REQUIRED <= set(group) <= GROUP_KEYS:
                return False
            for question in group["questions"]:
                if not QUESTION_REQUIRED <= set(question) <= QUESTION_KEYS:
                    return False
            groups.append(QuestionGroup.model_validate(group))
        if body["count"] != sum(len(group.questions) for group in groups):
            return False
        tagged = [question.objectives for group in groups for question in group.questions]
        if body["objectives"] != objective_counts(tagged, ids):
            return False
        if extract_problems(groups, ids):
            return False
    except (OSError, LookupError, TypeError, ValueError):
        return False
    return True


def describe(body: dict) -> str:
    """The counts worth seeing on one line while a book runs.

    How the questions fell across the objectives is the one to watch, so the
    per-objective counts are printed in full: a section that drills its first
    objective and leaves the last with nothing is either what the book does or a
    tagging that has gone flat, and the spread is what tells the two apart.
    """
    groups = body["groups"]
    rows = [question for group in groups for question in group["questions"]]
    counts = body["objectives"]
    covered = sum(1 for row in counts if row["questions"])
    spread = "/".join(str(row["questions"]) for row in counts)
    untagged = sum(1 for row in rows if not row.get("objectives"))
    sets = sum(1 for group in groups if len(group["questions"]) > 1)
    figures = sum(len(group.get("figures") or []) for group in groups) + sum(
        len(row.get("figures") or []) for row in rows
    )

    notes = [f"{body['count']} questions", f"{covered}/{len(counts)} objectives [{spread}]"]
    if untagged:
        notes.append(f"{untagged} untagged")
    if sets:
        notes.append(f"{sets} set(s)")
    if figures:
        notes.append(f"{figures} figure(s)")
    return ", ".join(notes)


@dataclass(frozen=True)
class Run:
    """Everything a section is extracted against that the whole book shares.

    Gathered into one object because a worker needs all of it and none of it
    changes between sections. The client and the logger are the two that matter
    for a concurrent run: both are made once here, and both are used from every
    worker.
    """

    client: genai.Client
    logger: ApiLogger
    book_id: str
    meta: dict[str, str]
    model: str
    thinking: str
    prompt: Prompt
    progress: Progress


def extract_section(
    run: Run, section: dict, pdfs: list[Path], out_path: Path
) -> tuple[str, str]:
    """Extract one section's questions, returning what became of it and the line to print.

    Runs on a thread of its own and shares nothing that is written to: its own
    PDFs go up, its own log folder is named for it, its own file is the only one
    it writes. What became of it is handed back for the caller to print, so that
    the count of finished sections is kept in one place, but the worker says as it
    goes what it is uploading and when it starts waiting on the model, since that
    is the minutes a book spends before its first section lands.
    """
    number_path = section["number"]
    rows = objectives(section)
    ids = [row["id"] for row in rows]

    started = time.perf_counter()
    parts, uploaded = upload_pdfs(
        run.client, pdfs, section=number_path, progress=run.progress
    )
    upload_seconds = time.perf_counter() - started
    run.progress.asking(
        number_path, f"up in {since(upload_seconds)}, asking the model"
    )

    section_text = section_part(section, pdfs, rows)
    context = {
        "book_id": run.book_id,
        "section": number_path,
        "section_title": section["title"],
        "module_path": section["module_path"],
        "objectives": len(ids),
        "pdfs": [path.name for path in pdfs],
        "upload_seconds": round(upload_seconds, 1),
        "prompt_files": run.prompt.files,
    }
    report = lambda message: run.progress.say(f"  {number_path}: {message}")

    def request(call_parts, extra_contents, thinking, item):
        return request_extract(
            run.client,
            run.model,
            run.prompt.text,
            section_text,
            call_parts,
            ids,
            thinking,
            run.logger,
            item,
            context,
            report=report,
            extra_contents=extra_contents,
        )

    try:
        reply = extract_with_fallback(
            request, parts, pdfs, run.thinking, number_path, ids
        )
    finally:
        delete_uploads(run.client, uploaded)

    elapsed = since(time.perf_counter() - started)
    if reply.status == "abort":
        if "RECITATION" in (reply.abort_reason or ""):
            from src.english_json import dump_english_json

            body = extract_document([], ids)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                dump_english_json(
                    canonicalize_document(
                        {
                            "book_id": run.book_id,
                            **run.meta,
                            "number": number_path,
                            "title": section["title"],
                            "module_path": section["module_path"],
                            "questions": body,
                        }
                    )
                ),
                encoding="utf-8",
            )
            return "written", (
                f"  {number_path}: 0 questions (RECITATION blocked; empty extract written) "
                f"({elapsed})"
            )
        return "aborted", (
            f"  {number_path}: aborted, "
            f"{reply.abort_reason or 'no reason given'} ({elapsed})"
        )
    rate = questions_per_page(reply.groups, pdfs)
    if rate is not None:
        record_questions_per_page(run.logger, number_path, rate, report)

    from src.english_json import dump_english_json

    body = extract_document(reply.groups, ids)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        dump_english_json(
            canonicalize_document(
                {
                    # The book's own attributes travel with every extraction, so a
                    # downstream reader needs nothing but this file and the spec it
                    # points at. How the questions were read belongs to the call log.
                    "book_id": run.book_id,
                    **run.meta,
                    "number": number_path,
                    "title": section["title"],
                    "module_path": section["module_path"],
                    "questions": body,
                }
            )
        ),
        encoding="utf-8",
    )
    return "written", (
        f"  {number_path}: {len(pdfs)} source(s) -> {describe(body)} ({elapsed})"
    )


def build_extracts(
    book_id: str, section: str | None, model: str, thinking: str, update: bool
) -> tuple[Path, int]:
    """Extract the questions of every section of a reviewed book, or just one."""
    if not any(os.environ.get(name) for name in API_KEY_VARS):
        raise RuntimeError(f"set one of {' / '.join(API_KEY_VARS)} to call the model")

    # Said before the read rather than after, because the reviewed book is read
    # and validated whole before this stage knows how many sections it even has.
    print(f"reading the reviewed {book_id}", flush=True)
    document = reviewed_book(book_id)
    sections = matching_sections(document["sections"], section)
    if section and not sections:
        raise RuntimeError(f"no section matching {section} in the reviewed {book_id}")

    # The objectives come from the reviewed book and the PDFs from the table of
    # contents, so the two are joined on the section number here.
    print("reading the table of contents", flush=True)
    spans = section_spans(book_id, None)
    orphans = [row["number"] for row in sections if row["number"] not in spans]
    if orphans:
        raise RuntimeError(
            f"{len(orphans)} reviewed section(s) are no longer in the table of contents: "
            f"{', '.join(orphans)}"
        )

    # The reviewed book carries the book's own attributes at the top, and those are
    # what this stage writes onto its own files. The prompt is chosen by the table of
    # contents instead: it is the one place a book says what language, numerals and
    # notation it is, and a book reviewed before those were recorded still has them
    # there.
    meta = book_attributes(document)
    prompt = build_prompt(book_metadata(book_id))
    pad_width = number_pad_width(book_spans(book_id))
    sections_root = OUTPUT_DIR / book_id / SECTIONS_STAGE
    stage_dir = OUTPUT_DIR / book_id / QUESTIONS_STAGE
    envelope_keys = {"book_id", *meta, "number", "title", "module_path", "questions"}

    pending: list[tuple[dict, list[Path], Path]] = []
    skipped = 0
    for row in sections:
        out_path = stage_dir / f"{row['stem']}.json"
        ids = [item["id"] for item in row["spec"]["objectives"]]
        if not update and written_extract(out_path, envelope_keys, ids):
            skipped += 1
            continue
        pdfs = section_pdfs(spans[row["number"]], row["number"], pad_width, sections_root)
        pending.append((row, pdfs, out_path))

    # Every section's PDFs are looked for before any call goes out. Sections run
    # together now, so a book that was never split would otherwise spend a whole
    # set of calls before the first missing file stopped it. Reported by section
    # rather than by file, because a section short of even one of its PDFs is
    # split again whole.
    unsplit = [
        row["number"]
        for row, pdfs, _ in pending
        if any(not path.is_file() for path in pdfs)
    ]
    if unsplit:
        raise RuntimeError(
            f"{len(unsplit)} section(s) have no PDF: {', '.join(unsplit)}; run sources.py first"
        )

    written = aborted = 0
    failures: list[str] = []

    if pending:
        files = [path for _, pdfs, _ in pending for path in pdfs]
        nbytes = sum(path.stat().st_size for path in files)
        workers = min(len(pending), MAX_WORKERS)
        opening = (
            f"extracting {len(pending)} section(s) with {workers} worker(s), "
            f"{len(files)} PDF(s), {in_mb(nbytes)} to upload"
        )
        if skipped:
            opening += f"; {skipped} already extracted"
        print(opening, flush=True)

        # Both the client and the logger reach the network to start up, so a run
        # that extracts nothing needs neither. The logger's clock is set once
        # here rather than once per worker, and it is named on its own line
        # because setting it against NTP is the wait that can run into seconds.
        print("  opening the model client", flush=True)
        client = genai.Client()
        print("  setting the log clock", flush=True)
        logger = ApiLogger(OUTPUT_DIR / book_id)

        with Progress(len(pending), len(files), nbytes) as progress:
            run = Run(
                client=client,
                logger=logger,
                book_id=book_id,
                meta=meta,
                model=model,
                thinking=thinking,
                prompt=prompt,
                progress=progress,
            )
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(extract_section, run, *item): item[0]["number"]
                    for item in pending
                }
                for future in as_completed(futures):
                    number_path = futures[future]
                    try:
                        outcome, line = future.result()
                    except Exception as error:  # noqa: BLE001 - the others still stand
                        failures.append(f"{number_path}: {type(error).__name__}: {error}")
                        progress.done(
                            number_path,
                            f"  {number_path}: failed, "
                            f"{one_line(f'{type(error).__name__}: {error}')}",
                        )
                        continue
                    if outcome == "written":
                        written += 1
                    else:
                        aborted += 1
                    # Printed through the progress object as it lands, so the line
                    # goes out flushed, the section stops counting as one the run
                    # waits on, and no heartbeat can cut across it.
                    progress.done(number_path, line)

    summary = f"{written} section(s) extracted"
    if skipped:
        summary += f", {skipped} already extracted"
    if aborted:
        summary += f", {aborted} aborted"
    if failures:
        summary += f", {len(failures)} failed"
    print(f"OK: {summary} under {stage_dir.relative_to(PROJECT_ROOT)}")
    return stage_dir, aborted + len(failures)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_id", help="Book ID, e.g. eg_mathematics_en_g7_t1")
    parser.add_argument(
        "-s", "--section", help="extract this section, or every section under it, e.g. -s 1 or -s 01"
    )
    parser.add_argument(
        "--update", action="store_true", help="re-extract sections that already have questions"
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
    args = parser.parse_args()

    try:
        _, unextracted = build_extracts(
            args.book_id, args.section, args.model, args.thinking_level, args.update
        )
    except RuntimeError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    return 1 if unextracted else 0


if __name__ == "__main__":
    sys.exit(main())
