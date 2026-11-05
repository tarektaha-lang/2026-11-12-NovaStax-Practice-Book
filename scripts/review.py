#!/usr/bin/env python3
"""Assemble a practice book's section specs into one document and review it.

Stage 2 writes one spec per section and sees nothing but that section, so the
defects that live between sections survive it: a prerequisite that points
forward to a skill the book teaches later, one concept defined two ways in two
units, two sections introducing the same idea as new. This stage puts the whole
book in front of the model at once and asks only what a whole-book reading can
answer.

The model never writes content it cannot source. Every finding carries the edits
that resolve it, drawn from a vocabulary that keeps that promise: new text only
for the three fields inferred from the section rather than copied off a page, and
removal — which invents nothing — for any row at all.

A run leaves three files: the book as stage 2 left it, the reading of that book,
and the book with the reading applied. Reading it costs a whole-book call and
applying the result costs nothing, so the two halves skip separately: a book
already read is applied again without reaching the model.

specs.py must have written a spec for every leaf section first; an incomplete
book is refused before anything is assembled or called.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    from google import genai
    from google.genai import types
    from pydantic import BaseModel, Field, ValidationError

    from src.api_logger import (
        ApiLogger,
        genai_answer_text,
        genai_response_files,
        genai_usage,
        safe_log,
    )
    from src.prompting import Prompt, assemble
    from src.geometry_arrows import canonicalize_document
    from specs import (
        ID_PREFIXES,
        SPECS_STAGE,
        SectionSpec,
        Waiting,
        one_line,
        since,
        written_spec,
    )
    from sources import (
        PROJECT_ROOT,
        OUTPUT_DIR,
        Span,
        book_metadata,
        book_spans,
        number_pad_width,
        number_stem,
        section_spans,
    )
except ModuleNotFoundError as error:  # pragma: no cover
    sys.exit(f"missing dependency ({error.name}): pip install -r requirements.txt")

# The prompt is assembled from prompts/review/ by prompting.py, from the parts
# the book's language chooses.
PROMPT_STAGE = "review"

REVIEW_STAGE = "04_book_review"
# The stage holds the book as stage 2 left it, the reading of that book, and the
# book with the reading applied. Named for what they are rather than for the book
# they belong to, and for which side of the review they sit on.
BOOK_FILE = "book_before_review.json"
APPLIED_BOOK_FILE = "book_after_review.json"
FINDINGS_FILE = "findings.json"
REVIEW_STEP = "book_review"
REVIEW_ITEM = "book"

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_THINKING = "high"
THINKING_LEVELS = ("minimal", "low", "medium", "high")
API_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
MAX_ATTEMPTS = 4

SEVERITIES = ("high", "medium", "low")

# Findings are numbered on the way to disk rather than by the model, exactly as
# stage 2 numbers the rows of a spec.
FINDING_PREFIX = "finding"

# The three spec fields inferred from the section rather than transcribed from a
# page. A reviewer that cannot see the pages can still write these, so they are
# the only fields whose text it may write.
WRITABLE_FIELDS = ("overview", "prerequisites", "out_of_scope")
# Of those, the two that are lists and can therefore gain a row. The overview is
# one string: it can be rewritten but not added to.
INSERTABLE_FIELDS = ("prerequisites", "out_of_scope")
# The one writable field whose rows carry a second string. Replacing the topic
# without replacing the justification would leave the two contradicting.
REASON_FIELD = "out_of_scope"

# Stage 2 numbers spec rows on the way out, so `objective_2` names its field as
# well as its position; this reads that numbering back.
FIELD_BY_PREFIX = {prefix: field for field, prefix in ID_PREFIXES.items()}


class Edit(BaseModel):
    """One change that resolves part of a finding, written to be applied as it stands.

    Three operations, and the field decides which are open to a reviewer that
    cannot see the pages: replace and insert write text, so they are confined to
    the fields inferred from the section, while remove writes nothing at all and
    is therefore allowed on any row.
    """

    op: Literal["replace", "insert", "remove"] = Field(
        description="replace one row's text, insert a new row, or remove a row outright"
    )
    target: str = Field(
        description=(
            "For replace and remove, the row: 04_02.prerequisite_3, 04_02.term_5, "
            "04_02.overview. For insert, the field to append to: 04_02.prerequisites"
        )
    )
    text: str = Field(
        default="",
        description="The row's new wording, in the book's language; empty for remove",
    )
    reason: str = Field(
        default="",
        description="Why it is out of scope; required writing an out_of_scope row, empty otherwise",
    )


class Finding(BaseModel):
    """One defect that spans more than one section, with the edits that resolve it."""

    type: Literal[
        "forward_prerequisite",
        "missing_prerequisite",
        "terminology_drift",
        "notation_conflict",
        "redundant_teaching",
        "out_of_scope_contradiction",
        "objective_duplication",
    ]
    severity: Literal["high", "medium", "low"]
    sections: list[str] = Field(
        min_length=1, description="Hierarchy numbers the finding spans, earliest first, e.g. 4.2"
    )
    targets: list[str] = Field(
        min_length=1,
        description="The rows themselves, as <stem>.<row id>, e.g. 09_03.prerequisite_2",
    )
    evidence: str = Field(
        description="The conflicting strings, quoted from the specs in the book's language"
    )
    explanation: str = Field(description="Why those rows conflict, in English, in a sentence or three")
    edits: list[Edit] = Field(
        min_length=1,
        description="The changes that resolve this finding; every finding carries at least one",
    )


class ReviewReply(BaseModel):
    """The model's answer, which may refuse the book instead of reviewing it."""

    status: Literal["ok", "abort"]
    abort_reason: str = Field(
        default="", description="Why the book cannot be reviewed; empty when status is ok"
    )
    findings: list[Finding] = Field(
        default_factory=list, description="Empty when the book holds together"
    )


def build_prompt(meta: dict[str, str]) -> Prompt:
    """The prompt this stage sends for the book, assembled from its parts."""
    return assemble(PROMPT_STAGE, meta)


def spec_file(book_id: str, number_path: str, pad_width: int) -> Path:
    """The spec stage 2 wrote for one section."""
    return OUTPUT_DIR / book_id / SPECS_STAGE / f"{number_stem(number_path, pad_width)}.json"


def incomplete_sections(
    book_id: str, sections: dict[str, list[Span]], pad_width: int, envelope_keys: set[str]
) -> list[str]:
    """Every leaf section whose spec cannot be read, as lines for the report.

    Missing and invalid are kept apart because the fix differs: a missing spec
    means specs.py has not reached that section, while an invalid one means what
    it left behind no longer matches the schema and has to be rewritten with
    --update. The check is stage 2's own, so the two stages cannot drift apart
    on what a usable spec is.
    """
    problems: list[str] = []
    for number_path, spans in sections.items():
        path = spec_file(book_id, number_path, pad_width)
        if not path.is_file():
            problems.append(f"[missing] {number_path} {spans[0].title}: no {path.name}")
        elif not written_spec(path, envelope_keys, book_id):
            problems.append(
                f"[invalid] {number_path} {spans[0].title}: {path.name} does not match "
                f"the current spec schema"
            )
    return problems


def assembled_book(
    book_id: str, sections: dict[str, list[Span]], meta: dict[str, str], pad_width: int
) -> dict:
    """Every section spec in one document, in table-of-contents order.

    A faithful union: the book's own attributes are written once at the top
    rather than once per section, and each spec body is carried across exactly
    as stage 2 wrote it. Nothing is renumbered, merged or summarised — this
    stage reads the book, it does not edit it.
    """
    document: dict = {"book_id": book_id, **meta, "sections": []}
    for number_path, spans in sections.items():
        spec = json.loads(
            spec_file(book_id, number_path, pad_width).read_text(encoding="utf-8")
        )
        document["sections"].append(
            {
                "number": number_path,
                # The stem names the section's spec file and prefixes every row
                # id a finding cites, so it travels with the section.
                "stem": number_stem(number_path, pad_width),
                "title": spec["title"],
                "module_path": spec["module_path"],
                "spec": spec["spec"],
            }
        )
    return document


def review_payload(document: dict) -> dict:
    """The assembled book as the model sees it, with excerpt wording withheld.

    An excerpt is an exact copy from a page this call cannot see and can never
    be changed, so nothing said about its text could be acted on — and it is the
    bulkiest field in the book. The rows stay, with their id, type and reuse, so
    a finding can still be about which excerpts a section carries. Poem
    `rows` are withheld with the text. Nothing else is trimmed.
    """
    sections = []
    for section in document["sections"]:
        spec = {
            field: (
                [{key: value for key, value in row.items() if key not in {"text", "rows"}} for row in body]
                if field == "excerpts"
                else body
            )
            for field, body in section["spec"].items()
        }
        sections.append({**section, "spec": spec})
    return {**document, "sections": sections}


def book_part(payload: dict) -> str:
    """Label the assembled book so the model can tell it from the prompt.

    Sent compact: indentation is about a fifth of a whole book's payload and
    buys the model nothing. The copy written to disk keeps its indent.
    """
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"Assembled book JSON:\n{body}"


def section_rows(document: dict) -> dict[str, set[str]]:
    """Every addressable row of the assembled book, as stem -> row ids.

    The overview is one string rather than an array, so it has no row id of its
    own and answers to the field name instead.
    """
    rows: dict[str, set[str]] = {}
    for section in document["sections"]:
        ids = {"overview"}
        for body in section["spec"].values():
            if isinstance(body, list):
                ids.update(row["id"] for row in body)
        rows[section["stem"]] = ids
    return rows


def row_field(row_id: str) -> str | None:
    """The spec field a row id belongs to, e.g. `prerequisite_2` -> `prerequisites`."""
    if row_id == "overview":
        return "overview"
    prefix, _, number = row_id.rpartition("_")
    return FIELD_BY_PREFIX.get(prefix) if number.isdigit() else None


def target_problem(target: str, rows: dict[str, set[str]]) -> str | None:
    """Why a target cannot be resolved to a row of the book, or None when it can."""
    stem, separator, row_id = target.partition(".")
    if not separator or not row_id:
        return f"{target!r} is not written as <stem>.<row id>"
    if stem not in rows:
        return f"{target!r} names section {stem}, which is not in this book"
    if row_id not in rows[stem]:
        return f"{target!r} names a row section {stem} does not have"
    return None


def reason_problem(edit: Edit, field: str) -> str | None:
    """Why an edit's reason does not match the field it writes, or None when it does."""
    if field == REASON_FIELD and not edit.reason.strip():
        return f"{edit.target!r} writes an {REASON_FIELD} row with no reason"
    if field != REASON_FIELD and edit.reason.strip():
        return f"{edit.target!r} carries a reason, which only {REASON_FIELD} rows have"
    return None


def edit_problem(edit: Edit, rows: dict[str, set[str]]) -> str | None:
    """Why an edit cannot be applied as it stands, or None when it can.

    This is the rule that keeps the stage honest. Text may only be written into
    the fields inferred from the section, because the pages behind everything
    else are not in front of this call. Removal is open everywhere precisely
    because it writes nothing: dropping a definition that contradicts an earlier
    one needs no sight of the page it came from.
    """
    stem, separator, tail = edit.target.partition(".")
    if not separator or not tail:
        return f"{edit.target!r} is not written as <stem>.<row id> or <stem>.<field>"
    if stem not in rows:
        return f"{edit.target!r} names section {stem}, which is not in this book"

    if edit.op == "insert":
        if tail not in INSERTABLE_FIELDS:
            return (
                f"{edit.target!r} inserts a row into {tail}: only "
                f"{' and '.join(INSERTABLE_FIELDS)} may gain one"
            )
        if not edit.text.strip():
            return f"{edit.target!r} inserts a row with no text"
        return reason_problem(edit, tail)

    if tail not in rows[stem]:
        return f"{edit.target!r} names a row section {stem} does not have"
    field = row_field(tail)
    if field is None:
        return f"{edit.target!r} names a row whose field cannot be read from its id"

    if edit.op == "remove":
        if field == "overview":
            return f"{edit.target!r} removes the overview, which every section must have"
        if edit.text.strip() or edit.reason.strip():
            return f"{edit.target!r} removes a row and must carry no text of its own"
        return None

    if field not in WRITABLE_FIELDS:
        return (
            f"{edit.target!r} rewrites a row of {field}, which is transcribed from pages "
            f"this call cannot see: only {', '.join(WRITABLE_FIELDS)} may be written, "
            f"and any other row can only be removed"
        )
    if not edit.text.strip():
        return f"{edit.target!r} replaces a row with no text"
    return reason_problem(edit, field)


def check_findings(reply: ReviewReply, rows: dict[str, set[str]]) -> None:
    """Reject a reply whose findings cannot be applied, reporting every reason.

    Every finding carries the edits that resolve it and nothing is left for a
    reader to act on later, so a finding whose edits cannot be applied is a reply
    to throw away rather than one to accept and half-apply. Two edits on one row
    are refused as well: whichever won would depend on the order they happened to
    be applied in.
    """
    problems: list[str] = []
    claimed: dict[str, str] = {}

    for number, finding in enumerate(reply.findings, start=1):
        label = f"finding {number} ({finding.type})"
        for target in finding.targets:
            problem = target_problem(target, rows)
            if problem:
                problems.append(f"{label} targets a row that does not exist: {problem}")

        for edit in finding.edits:
            problem = edit_problem(edit, rows)
            if problem:
                problems.append(f"{label} cannot be applied: {problem}")
                continue
            if edit.op == "insert":
                continue
            owner = claimed.get(edit.target)
            if owner:
                problems.append(
                    f"{label} edits {edit.target}, which {owner} edits too: one row, one edit"
                )
            else:
                claimed[edit.target] = label

    if problems:
        raise ValueError("; ".join(problems))


def logged_call(
    client,
    model: str,
    prompt: str,
    book: str,
    rows: dict[str, set[str]],
    thinking: str,
    logger: ApiLogger,
    attempt: int,
    context: dict,
) -> tuple[ReviewReply | None, str | None]:
    """Ask for one reading of the book, logging the call however it turns out.

    Returns the model's reply, or None alongside the failure to report. A reply
    whose findings do not address real rows fails here like a parse failure
    does, and is retried the same way.
    """
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=ReviewReply,
        thinking_config=types.ThinkingConfig(thinking_level=thinking),
    )
    response = None
    result = None
    error_message = None
    started = time.perf_counter()
    try:
        response = client.models.generate_content(
            model=model, contents=[prompt, book], config=config
        )
        result = ReviewReply.model_validate_json(genai_answer_text(response))
        check_findings(result, rows)
    except Exception as error:  # noqa: BLE001 - retry transport, parse and addressing failures alike
        result = None
        error_message = f"{type(error).__name__}: {error}"
    duration = time.perf_counter() - started

    safe_log(
        logger,
        files_builder=lambda captured=response: {
            "prompt.txt": prompt,
            "book.txt": book,
            **(genai_response_files(captured) if captured is not None else {}),
        },
        page_number=REVIEW_ITEM,
        step_name=REVIEW_STEP,
        attempt=attempt,
        api="gemini",
        model=model,
        usage=genai_usage(response) if response is not None else {},
        duration_seconds=duration,
        status="success" if result is not None else "error",
        error_message=error_message,
        extra={**context, "thinking_level": thinking, "returned": result.status if result else None},
    )
    return result, error_message


def request_review(
    client,
    model: str,
    prompt: str,
    book: str,
    rows: dict[str, set[str]],
    thinking: str,
    logger: ApiLogger,
    context: dict,
    report: Callable[[str], None] | None = None,
) -> ReviewReply:
    """Call the model until it returns a usable report, retrying every failure."""
    last_error: str | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        result, last_error = logged_call(
            client, model, prompt, book, rows, thinking, logger, attempt, context
        )
        if result is not None:
            return result
        if attempt < MAX_ATTEMPTS:
            # Every attempt is another reading of the whole book, so a run that
            # is quietly spending them looks exactly like one that is thinking.
            if report is not None:
                report(
                    f"  attempt {attempt} of {MAX_ATTEMPTS} failed, "
                    f"{one_line(last_error or 'no reason given')}; "
                    f"asking again in {2**attempt}s"
                )
            time.sleep(2**attempt)

    raise RuntimeError(f"failed after {MAX_ATTEMPTS} attempts: {last_error}")


def findings_document(findings: list[Finding]) -> list[dict]:
    """The findings as they are written to disk, each with its id.

    Numbered here rather than by the model, exactly as stage 2 numbers spec
    rows, so that "finding_3" means the third finding of this report. Dumping
    without defaults leaves text and reason out of the edits that carry neither,
    so a removal reads as the two fields it actually has.
    """
    return [
        {"id": f"{FINDING_PREFIX}_{number}", **finding.model_dump(exclude_defaults=True)}
        for number, finding in enumerate(findings, start=1)
    ]


def severity_counts(findings: list[Finding]) -> dict[str, int]:
    """How many findings of each severity, always all three keys."""
    return {
        severity: sum(1 for finding in findings if finding.severity == severity)
        for severity in SEVERITIES
    }


def review_document(book_id: str, meta: dict[str, str], sections: int, findings: list[Finding]) -> dict:
    """The report as it is written to disk."""
    return {
        # The book's own attributes travel with the report, so a downstream
        # reader needs nothing but this file. How it was produced belongs to the
        # call log, not here.
        "book_id": book_id,
        **meta,
        "section_count": sections,
        "counts": severity_counts(findings),
        "findings": findings_document(findings),
    }


def written_findings(path: Path, envelope_keys: set[str]) -> bool:
    """True when a book already holds a report of the shape written today.

    Shallow on purpose, as stage 2's check is: the envelope's keys and the
    severities counted must be the ones written today, and the findings must be
    a list, but the rows themselves are not walked. Rereading the book is one
    whole-book call, and it is not spent on a row whose keys have drifted.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if set(document) != envelope_keys or set(document["counts"]) != set(SEVERITIES):
            return False
        if not isinstance(document["findings"], list):
            return False
        # A report written before findings carried their edits parses and counts
        # correctly, and the applying half would then find nothing to apply. One
        # level down is as deep as this goes, for the same reason stage 2's check
        # is shallow.
        if any(not finding.get("edits") for finding in document["findings"]):
            return False
    except (OSError, LookupError, TypeError, ValueError):
        return False
    return True


def written_book(path: Path, envelope_keys: set[str]) -> bool:
    """True when a book already holds an applied book of the shape written today."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if set(document) != envelope_keys or not isinstance(document["sections"], list):
            return False
    except (OSError, LookupError, TypeError, ValueError):
        return False
    return True


def next_row_id(body: list[dict], field: str) -> str:
    """The id an inserted row takes: one past the highest the field already holds.

    Counted from the ids rather than from the length, because a removal earlier in
    the same run leaves gaps, and reusing a number would give one section two rows
    with one id.
    """
    numbers = [
        int(number)
        for row in body
        if (number := str(row.get("id", "")).rpartition("_")[2]).isdigit()
    ]
    return f"{ID_PREFIXES[field]}_{max(numbers, default=0) + 1}"


def apply_edit(spec: dict, edit: dict) -> str:
    """Carry out one edit on one section's spec, returning what it did.

    Raises when the edit does not fit the spec in front of it, which the checks
    above have already ruled out for a reply this run received. It can still
    happen to a report edited by hand between runs, and then it stops the run.
    """
    op = edit["op"]
    _, _, tail = edit["target"].partition(".")

    if op == "insert":
        body = spec[tail]
        row = {"id": next_row_id(body, tail), "text": edit["text"]}
        if "reason" in edit:
            row["reason"] = edit["reason"]
        body.append(row)
        return f"inserted {row['id']}"

    if tail == "overview":
        if op != "replace":
            raise RuntimeError(f"cannot {op} the overview")
        spec["overview"] = edit["text"]
        return "replaced overview"

    field = next(
        (
            name
            for name, body in spec.items()
            if isinstance(body, list) and any(row.get("id") == tail for row in body)
        ),
        None,
    )
    if field is None:
        raise RuntimeError(f"no row {tail} in this section")

    body = spec[field]
    if op == "remove":
        spec[field] = [row for row in body if row.get("id") != tail]
        return f"removed {tail} from {field}"

    row = next(row for row in body if row.get("id") == tail)
    row["text"] = edit["text"]
    if "reason" in edit:
        row["reason"] = edit["reason"]
    return f"replaced {tail} in {field}"


def applied_book(document: dict, findings: list[dict]) -> tuple[dict, list[dict]]:
    """The assembled book with every edit applied, and the log of what was applied.

    The document is copied rather than edited in place so that a failure part way
    through leaves nothing written and nothing half-changed. Every edit is checked
    against the book again here: a reply this run received was checked before it
    was written, but a report read back off disk was not.
    """
    book = copy.deepcopy(document)
    sections = {section["stem"]: section for section in book["sections"]}
    rows = section_rows(document)
    log: list[dict] = []

    for finding in findings:
        for edit in finding["edits"]:
            try:
                problem = edit_problem(Edit(**edit), rows)
            except ValidationError as error:
                raise RuntimeError(f"{finding['id']} holds an unreadable edit: {error}") from None
            if problem:
                raise RuntimeError(f"{finding['id']} cannot be applied: {problem}")

            section = sections[edit["target"].partition(".")[0]]
            try:
                did = apply_edit(section["spec"], edit)
            except RuntimeError as error:
                raise RuntimeError(f"{finding['id']} cannot be applied: {error}") from None
            log.append(
                {
                    "finding": finding["id"],
                    "op": edit["op"],
                    "target": edit["target"],
                    "result": did,
                }
            )

    return book, log


def check_sections(book: dict) -> None:
    """Refuse a book whose edits left a section outside stage 2's schema.

    The rule that bites is stage 2's own: a section must keep at least one
    objective, one prerequisite and one out_of_scope row. Removing the last of any
    of those satisfies every check the edits themselves are put through and only
    shows up here, so this is the backstop that keeps an unusable book off disk.
    """
    problems: list[str] = []
    for section in book["sections"]:
        try:
            SectionSpec.model_validate(section["spec"])
        except ValidationError as error:
            for detail in error.errors():
                field = ".".join(str(part) for part in detail["loc"]) or "spec"
                problems.append(f"{section['number']}: {field}: {detail['msg']}")
    if problems:
        raise RuntimeError(
            "the review would leave these sections outside the spec schema: "
            + "; ".join(problems)
        )


def reviewed_book(book_id: str) -> dict:
    """The applied book this stage wrote, for the stages that work from it.

    Lives here because the file is this stage's own output: a later stage asking
    for a reviewed book should not have to know where it is kept or what it is
    called. A book that was never reviewed is a RuntimeError naming the fix.
    """
    path = OUTPUT_DIR / book_id / REVIEW_STAGE / APPLIED_BOOK_FILE
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RuntimeError(
            f"no reviewed book at {path.relative_to(PROJECT_ROOT)}; run review.py first"
        ) from None
    except (OSError, ValueError) as error:
        raise RuntimeError(
            f"cannot read the reviewed book at {path.relative_to(PROJECT_ROOT)}: {error}"
        ) from error
    if not document.get("sections"):
        raise RuntimeError(f"no sections in {path.relative_to(PROJECT_ROOT)}")
    return document


def book_attributes(document: dict) -> dict[str, str]:
    """The book's own attributes, as carried at the top of a reviewed book.

    Everything but the sections and the record of what the review changed, so a
    later stage can write the same attributes onto its own files without reading
    the XML again.
    """
    return {
        key: value
        for key, value in document.items()
        if key not in ("book_id", "sections", "applied_edits")
    }


def describe(counts: dict[str, int]) -> str:
    """The severities worth seeing on the summary line.

    Read off the report's own counts rather than the findings, so a report just
    written and one read back off disk are summarised by the same line.
    """
    return ", ".join(f"{number} {severity}" for severity, number in counts.items() if number)


def describe_edits(log: list[dict]) -> str:
    """How many edits of each kind were applied, in the order they are documented."""
    counts = [
        (sum(1 for row in log if row["op"] == op), op)
        for op in ("replace", "insert", "remove")
    ]
    return ", ".join(f"{number} {op}" for number, op in counts if number) or "no edits"


def build_review(book_id: str, model: str, thinking: str, update: bool) -> tuple[Path, int]:
    """Assemble a book's specs, have the model read it whole, and apply what it says."""
    sections = section_spans(book_id, None)
    if not sections:
        raise RuntimeError(f"no sections in {book_id}")

    meta = book_metadata(book_id)
    pad_width = number_pad_width(book_spans(book_id))
    stage_dir = OUTPUT_DIR / book_id / REVIEW_STAGE
    spec_keys = {"book_id", *meta, "number", "title", "module_path", "spec"}

    print(f"reviewing {book_id}, {len(sections)} section(s)", flush=True)

    # A book missing even one spec is not a book to review: this pass is about
    # what the sections say to each other, and a section that is not there says
    # nothing. Checked before the assembly, the client and the logger, so it
    # writes nothing and reaches no network. Said aloud because it reads and
    # validates every spec in the book before anything else happens.
    print("  checking that every section has a usable spec", flush=True)
    problems = incomplete_sections(book_id, sections, pad_width, spec_keys)
    if problems:
        raise RuntimeError(
            "\n".join(
                [
                    f"{book_id} has {len(problems)} of {len(sections)} section(s) without a "
                    f"usable spec; run specs.py first",
                    *(f"  {problem}" for problem in problems),
                ]
            )
        )

    findings_path = stage_dir / FINDINGS_FILE
    applied_path = stage_dir / APPLIED_BOOK_FILE
    reviewed = not update and written_findings(
        findings_path, {"book_id", *meta, "section_count", "counts", "findings"}
    )
    # The two halves are skipped separately because they cost different things.
    # Only a book that has been read and had that reading applied has nothing
    # left to do; a book read but not applied is finished off without a call.
    if reviewed and written_book(applied_path, {"book_id", *meta, "sections", "applied_edits"}):
        print(f"OK: {book_id} already reviewed under {stage_dir.relative_to(PROJECT_ROOT)}")
        return stage_dir, 0

    print("  assembling the specs into one book", flush=True)
    document = assembled_book(book_id, sections, meta, pad_width)
    book_path = stage_dir / BOOK_FILE
    book_path.parent.mkdir(parents=True, exist_ok=True)
    book_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"  {len(sections)} section(s) -> {book_path.name} "
        f"({book_path.stat().st_size / 1024:.0f} KB)",
        flush=True,
    )

    if reviewed:
        report = json.loads(findings_path.read_text(encoding="utf-8"))
        print(
            f"  {len(report['findings'])} finding(s) already read, applying without a call",
            flush=True,
        )
    else:
        if not any(os.environ.get(name) for name in API_KEY_VARS):
            raise RuntimeError(f"set one of {' / '.join(API_KEY_VARS)} to call the model")

        # Built here rather than beside the assembly, so that a run which applies
        # a reading it already has neither builds a payload nor reports a size for
        # a call it is not going to make.
        book = book_part(review_payload(document))
        print(
            f"  {len(book.encode('utf-8')) / 1024:.0f} KB sent without excerpt text",
            flush=True,
        )

        # Both the client and the logger reach the network to start up, and a run
        # with nothing to read — because the book is read already, or because it
        # stops on a missing spec — has returned or branched long before here.
        # Named apart because setting the logger's clock against NTP is the one
        # that can take seconds.
        print("  opening the model client", flush=True)
        client = genai.Client()
        print("  setting the log clock", flush=True)
        logger = ApiLogger(OUTPUT_DIR / book_id)

        # One call carries the whole book, so there is nothing to interleave with
        # the wait: how long it has been out is reported from a thread instead.
        prompt = build_prompt(meta)
        started = time.perf_counter()
        with Waiting("still with the model") as waiting:
            reply = request_review(
                client,
                model,
                prompt.text,
                book,
                section_rows(document),
                thinking,
                logger,
                {
                    "book_id": book_id,
                    "sections": len(sections),
                    "excerpt_text_withheld": True,
                    "prompt_files": prompt.files,
                },
                report=waiting.say,
            )
        read_in = since(time.perf_counter() - started)

        if reply.status == "abort":
            print(
                f"  aborted after {read_in}, {reply.abort_reason or 'no reason given'}",
                flush=True,
            )
            return stage_dir, 1

        print(f"  read in {read_in}, {len(reply.findings)} finding(s)", flush=True)

        # Written before anything is applied, and read from for the applying, so
        # the report on disk and the book beside it can never disagree.
        report = review_document(book_id, meta, len(sections), reply.findings)
        findings_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    applied, log = applied_book(document, report["findings"])
    applied = canonicalize_document(applied)
    check_sections(applied)

    # Every change to the book named on one line, because nobody is watching this
    # run: the console output is the only account of it outside the file itself.
    for row in log:
        print(f"  {row['finding']}  {row['op']:<7}  {row['target']}", flush=True)

    applied["applied_edits"] = log
    applied_path.write_text(
        json.dumps(applied, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    summary = f"{len(report['findings'])} finding(s) across {len(sections)} section(s)"
    if report["findings"]:
        summary += f" ({describe(report['counts'])})"
    print(
        f"OK: {summary}, applied as {len(log)} edit(s) ({describe_edits(log)}) "
        f"under {stage_dir.relative_to(PROJECT_ROOT)}"
    )
    return stage_dir, 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_id", help="Book ID, e.g. eg_mathematics_en_g7_t1")
    parser.add_argument(
        "--update", action="store_true", help="review a book that has already been reviewed"
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
        _, aborted = build_review(
            args.book_id, args.model, args.thinking_level, args.update
        )
    except RuntimeError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    return 1 if aborted else 0


if __name__ == "__main__":
    sys.exit(main())
