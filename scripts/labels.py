#!/usr/bin/env python3
"""Name each of a section's objectives in a few words, for the paper to head with.

An objective is written to be precise, not to be read across the top of a column
of exercises: they run to sixteen words in the middle and thirty-two at the long
end. One call per section turns each into a heading short enough to sit above the
exercises that practise it, and nothing else. No exercise, count or spec is
touched here.

The spec each call sees is the reviewed one, taken from the book review's applied
book, so an objective the review removed is never named. review.py must have run
first.

Sections are named concurrently, one worker per section, because no section needs
anything another section produces. Lines therefore appear as each section lands
rather than in the order of the book.

Nothing here uploads, so a section's whole wait is the one call: a line goes out
as each is asked, and where the book stands is reported whenever half a minute
passes with nothing else to say.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
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
        genai_answer_text,
        genai_response_files,
        genai_usage,
        safe_log,
    )
    from src.prompting import Prompt, assemble
    from review import book_attributes, reviewed_book
    from specs import Progress, in_kb, one_line, since
    from sources import PROJECT_ROOT, OUTPUT_DIR, book_metadata, matching_sections
except ModuleNotFoundError as error:  # pragma: no cover
    sys.exit(f"missing dependency ({error.name}): pip install -r requirements.txt")

# The prompt is assembled from prompts/labels/ by prompting.py, from the parts
# the book's language chooses.
PROMPT_STAGE = "labels"

LABELS_STAGE = "05_section_labels"
LABEL_STEP = "section_labels"

# What a heading may run to. Six words is what fits on one line above a column of
# exercises at the paper's own size, and a heading that needs a second line is
# the objective again rather than a name for it.
MAX_LABEL_WORDS = 6

# What a section's labels hold on disk. Kept here rather than read off a dumped
# reply, so that the skip check and the writer are held to one list.
LABEL_KEYS = ("objectives",)

DEFAULT_MODEL = "gemini-3.8-flash"
# Naming is a short task and a shallow one: the objective already says what it is
# about, and the model is only finding the few words of it that matter.
DEFAULT_THINKING = "medium"
THINKING_LEVELS = ("minimal", "low", "medium", "high")
API_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
MAX_ATTEMPTS = 4
# One worker per section still to be named, and the ceiling that keeps a book with
# hundreds of sections from putting hundreds of calls in flight at once.
MAX_WORKERS = 25


class ObjectiveLabel(BaseModel):
    """One objective's heading."""

    id: str = Field(description="The objective this names, exactly as the spec prints it")
    label: str = Field(
        description=(
            f"A heading for that objective, at most {MAX_LABEL_WORDS} words, in plain prose"
            " with no mathematical notation and no closing full stop"
        )
    )


class LabelReply(BaseModel):
    """The model's answer, which may refuse the section instead of naming it."""

    status: Literal["ok", "abort"]
    abort_reason: str = Field(
        default="", description="Why the objectives cannot be named; empty when status is ok"
    )
    objectives: list[ObjectiveLabel] = Field(
        default_factory=list, description="One entry per objective, in the spec's order"
    )


def build_prompt(meta: dict[str, str]) -> Prompt:
    """The prompt this stage sends for the book, assembled from its parts."""
    return assemble(PROMPT_STAGE, meta)


def section_part(section: dict) -> str:
    """The section as the model sees it: what it is, and what it teaches.

    The objectives are what is being named, and the section's own title and
    overview are what tell the model which words are already spoken for: a
    heading that repeats the section's title names nothing.
    """
    spec = section["spec"]
    body = json.dumps(
        {
            "number": section["number"],
            "title": section["title"],
            "module_path": section["module_path"],
            "overview": spec.get("overview", ""),
            "objectives": spec["objectives"],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"Section JSON:\n{body}"


def objective_ids(section: dict) -> list[str]:
    """The ids of the objectives this section's reviewed spec holds, in order."""
    return [row["id"] for row in section["spec"]["objectives"]]


def label_problems(labels: list[ObjectiveLabel], ids: list[str]) -> list[str]:
    """Every reason a set of headings cannot be used, as lines for the report.

    Three rules carry the stage. The headings must name the section's own
    objectives, one each, or they cannot be attached to anything. Each must be
    short enough and plain enough to set as a heading, which means no notation:
    a heading is read at a glance and $\\sqrt{2}$ in it is a thing to decipher.
    And within a section they must differ, because two objectives that arrive
    under one heading are worse than no headings at all - the reader is told the
    same exercises are two different things.
    """
    problems: list[str] = []
    named = [row.id for row in labels]

    for duplicate in sorted({name for name in named if named.count(name) > 1}):
        problems.append(f"{duplicate} is named more than once")
    for unknown in [name for name in named if name not in ids]:
        problems.append(f"{unknown} is not an objective of this section")
    for missing in [name for name in ids if name not in named]:
        problems.append(f"{missing} is left unnamed")

    for row in labels:
        label = row.label.strip()
        if not label:
            problems.append(f"{row.id} has an empty heading")
            continue
        words = len(label.split())
        if words > MAX_LABEL_WORDS:
            problems.append(
                f"{row.id} is headed with {words} words, more than the {MAX_LABEL_WORDS} a"
                f" heading holds: {label!r}"
            )
        if "$" in label or "\\" in label:
            problems.append(f"{row.id} writes notation in a heading: {label!r}")
        if label.endswith("."):
            problems.append(f"{row.id} ends its heading with a full stop: {label!r}")

    wording = [row.label.strip().casefold() for row in labels]
    for repeated in sorted({text for text in wording if text and wording.count(text) > 1}):
        problems.append(f"{repeated!r} heads more than one objective")

    return problems


def check_labels(reply: LabelReply, ids: list[str]) -> None:
    """Reject headings that cannot be used, reporting every reason at once."""
    problems = label_problems(reply.objectives, ids)
    if problems:
        raise ValueError("; ".join(problems))


def logged_call(
    client,
    model: str,
    prompt: str,
    section: str,
    ids: list[str],
    thinking: str,
    logger: ApiLogger,
    item: str,
    attempt: int,
    context: dict,
) -> tuple[LabelReply | None, str | None]:
    """Ask for one section's headings once, logging the call however it turns out.

    Returns the model's reply, or None alongside the failure to report. A heading
    that is too long or repeats another fails here like a parse failure does, and
    is retried the same way.
    """
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=LabelReply,
        thinking_config=types.ThinkingConfig(thinking_level=thinking),
    )
    response = None
    result = None
    error_message = None
    started = time.perf_counter()
    try:
        response = client.models.generate_content(
            model=model, contents=[prompt, section], config=config
        )
        result = LabelReply.model_validate_json(genai_answer_text(response))
        if result.status == "ok":
            check_labels(result, ids)
    except Exception as error:  # noqa: BLE001 - retry transport, parse and rule failures alike
        error_message = f"{type(error).__name__}: {error}"
        result = None
    duration = time.perf_counter() - started

    safe_log(
        logger,
        files_builder=lambda captured=response: {
            "prompt.txt": prompt,
            "section.txt": section,
            **(genai_response_files(captured) if captured is not None else {}),
        },
        page_number=item,
        step_name=LABEL_STEP,
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
        },
    )
    return result, error_message


def request_labels(
    client,
    model: str,
    prompt: str,
    section: str,
    ids: list[str],
    thinking: str,
    logger: ApiLogger,
    item: str,
    context: dict,
    report: Callable[[str], None] | None = None,
) -> LabelReply:
    """Call the model until it returns usable headings, retrying every failure."""
    last_error: str | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        result, last_error = logged_call(
            client, model, prompt, section, ids, thinking, logger, item, attempt, context
        )
        if result is not None:
            return result
        if attempt < MAX_ATTEMPTS:
            # A heading a word too long is refused here and asked for again, so a
            # section can spend its four attempts without a word otherwise.
            if report is not None:
                report(
                    f"attempt {attempt} of {MAX_ATTEMPTS} failed, "
                    f"{one_line(last_error or 'no reason given')}; "
                    f"asking again in {2**attempt}s"
                )
            time.sleep(2**attempt)

    raise RuntimeError(f"failed after {MAX_ATTEMPTS} attempts: {last_error}")


def label_document(reply: LabelReply, ids: list[str]) -> dict:
    """The headings as they are written to disk, in the spec's own order.

    Ordered here rather than trusted from the reply, so a file always reads in the
    order the paper will set. The objective's full wording is not copied in: it
    lives in the reviewed spec, and a second copy here would be a second version
    to keep true.
    """
    by_id = {row.id: row.label.strip() for row in reply.objectives}
    return {"objectives": [{"id": name, "label": by_id[name]} for name in ids]}


def written_labels(path: Path, envelope_keys: set[str], ids: list[str]) -> bool:
    """True when a section already holds headings of the shape written today.

    The objectives are checked against the reviewed spec as it stands, not just
    against the schema, because this stage reads a file another stage can rewrite:
    a review that removes an objective leaves headings that still name it, and
    those have to be asked for again rather than carried forward.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        labels = document["labels"]
        if set(document) != envelope_keys or set(labels) != set(LABEL_KEYS):
            return False
        rows = [ObjectiveLabel.model_validate(row) for row in labels["objectives"]]
        if label_problems(rows, ids):
            return False
    except (OSError, LookupError, TypeError, ValueError):
        return False
    return True


def section_labels(book_id: str, stem: str) -> dict[str, str]:
    """One section's headings by objective id, or nothing where it has none.

    The reader every paper goes through. It answers with an empty mapping rather
    than raising, so a paper can be set before this stage has ever run: a section
    with no headings is set without them.
    """
    path = OUTPUT_DIR / book_id / LABELS_STAGE / f"{stem}.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        return {row["id"]: row["label"] for row in document["labels"]["objectives"]}
    except (OSError, LookupError, TypeError, ValueError):
        return {}


def describe(labels: dict) -> str:
    """The headings worth seeing on one line while a book runs."""
    rows = labels["objectives"]
    return f"{len(rows)} objective(s): " + "; ".join(row["label"] for row in rows)


@dataclass(frozen=True)
class Run:
    """Everything a section is named against that the whole book shares.

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


def label_section(
    run: Run, section: dict, part: str, out_path: Path
) -> tuple[str, str]:
    """Name one section's objectives, returning what became of it and the line to print.

    Runs on a thread of its own and shares nothing that is written to: its own log
    folder is named for it, its own file is the only one it writes. What became of
    it is handed back for the caller to print, so that the count of finished
    sections is kept in one place, but the worker says when it starts waiting,
    because that wait is all a section of this stage does.
    """
    number_path = section["number"]
    ids = objective_ids(section)
    started = time.perf_counter()
    run.progress.asking(
        number_path,
        f"{len(ids)} objective(s), {in_kb(len(part.encode('utf-8')))} sent, "
        f"asking the model",
    )
    reply = request_labels(
        run.client,
        run.model,
        run.prompt.text,
        part,
        ids,
        run.thinking,
        run.logger,
        number_path,
        {
            "book_id": run.book_id,
            "section": number_path,
            "section_title": section["title"],
            "module_path": section["module_path"],
            "objectives": len(ids),
            "prompt_files": run.prompt.files,
        },
        report=lambda message: run.progress.say(f"  {number_path}: {message}"),
    )

    elapsed = since(time.perf_counter() - started)
    if reply.status == "abort":
        return "aborted", (
            f"  {number_path}: aborted, "
            f"{reply.abort_reason or 'no reason given'} ({elapsed})"
        )

    labels = label_document(reply, ids)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                # The book's own attributes travel with every file, so a
                # downstream reader needs nothing but this one and the spec it
                # points at. How the headings were produced belongs to the log.
                "book_id": run.book_id,
                **run.meta,
                "number": number_path,
                "title": section["title"],
                "module_path": section["module_path"],
                "labels": labels,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return "written", f"  {number_path}: {describe(labels)} ({elapsed})"


def build_labels(
    book_id: str, section: str | None, model: str, thinking: str, update: bool
) -> tuple[Path, int]:
    """Name the objectives of every section of a reviewed book, or of just one."""
    # Said before the read rather than after, because the reviewed book is read
    # and validated whole before this stage knows how many sections it even has.
    print(f"reading the reviewed {book_id}", flush=True)
    document = reviewed_book(book_id)
    sections = matching_sections(document["sections"], section)
    if section and not sections:
        raise RuntimeError(f"no section matching {section} in the reviewed {book_id}")

    # The reviewed book carries the book's own attributes at the top, and those are
    # what this stage writes onto its own files. The prompt is chosen by the table of
    # contents instead: it is the one place a book says what language, numerals and
    # notation it is, and a book reviewed before those were recorded still has them
    # there.
    meta = book_attributes(document)
    prompt = build_prompt(book_metadata(book_id))
    stage_dir = OUTPUT_DIR / book_id / LABELS_STAGE
    envelope_keys = {"book_id", *meta, "number", "title", "module_path", "labels"}

    pending: list[tuple[dict, str, Path]] = []
    skipped = 0
    for row in sections:
        out_path = stage_dir / f"{row['stem']}.json"
        if not update and written_labels(out_path, envelope_keys, objective_ids(row)):
            skipped += 1
            continue
        pending.append((row, section_part(row), out_path))

    written = aborted = 0
    failures: list[str] = []

    if pending:
        # Asked for only where there is something to name, so a run that names
        # nothing needs no key.
        if not any(os.environ.get(name) for name in API_KEY_VARS):
            raise RuntimeError(f"set one of {' / '.join(API_KEY_VARS)} to call the model")

        workers = min(len(pending), MAX_WORKERS)
        objectives = sum(len(objective_ids(row)) for row, _, _ in pending)
        to_send = sum(len(part.encode("utf-8")) for _, part, _ in pending)
        opening = (
            f"naming {objectives} objective(s) across {len(pending)} section(s) "
            f"with {workers} worker(s), {in_kb(to_send)} of specs to send"
        )
        if skipped:
            opening += f"; {skipped} already named"
        print(opening, flush=True)

        # Both the client and the logger reach the network to start up. The
        # logger's clock is set once here rather than once per worker, and it is
        # named on its own line because setting it against NTP is the wait that
        # can run into seconds.
        print("  opening the model client", flush=True)
        client = genai.Client()
        print("  setting the log clock", flush=True)
        logger = ApiLogger(OUTPUT_DIR / book_id)

        with Progress(len(pending)) as progress:
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
                    pool.submit(label_section, run, *item): item[0]["number"]
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

    summary = f"{written} section(s) named"
    if skipped:
        summary += f", {skipped} already named"
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
        "-s", "--section", help="name this section, or every section under it, e.g. -s 1 or -s 01"
    )
    parser.add_argument(
        "--update", action="store_true", help="rewrite headings that already exist"
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
        _, unnamed = build_labels(
            args.book_id, args.section, args.model, args.thinking_level, args.update
        )
    except RuntimeError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    return 1 if unnamed else 0


if __name__ == "__main__":
    sys.exit(main())
