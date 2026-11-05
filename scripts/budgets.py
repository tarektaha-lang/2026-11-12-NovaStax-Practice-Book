#!/usr/bin/env python3
"""Share a section's budget of practice exercises out among its objectives.

One call per section decides how the section's exercises are divided between the
objectives that section teaches. The budget is set by the section's size, 48 exercises
and 12 more for every objective it has, so a section that teaches more gets more
practice; the model decides only the division. It writes no exercises: this stage
settles how much practice each objective gets, and whatever writes the exercises later
reads these counts.

The spec each call sees is the reviewed one, taken from the book review's applied
book rather than from stage 2's files, so an objective the review removed is not
budgeted for. review.py must have run first.

Sections are budgeted concurrently, one worker per section, because no section
needs anything another section produces. Lines therefore appear as each section
lands rather than in the order of the book.

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

# The prompt is assembled from prompts/budgets/ by prompting.py, from the parts
# the book's language chooses.
PROMPT_STAGE = "budgets"

BUDGETS_STAGE = "06_section_budgets"
BUDGET_STEP = "section_budget"

# The whole point of the stage: a section's practice is this many exercises, a floor
# for the section and a share for every objective it teaches, and the model decides
# only how they are divided. A section of one objective has 60; one of six has 120.
BASE_EXERCISES = 48
EXERCISES_PER_OBJECTIVE = 12


def section_total(objectives: int) -> int:
    """How many exercises a section of this many objectives is budgeted."""
    return BASE_EXERCISES + EXERCISES_PER_OBJECTIVE * objectives


# What a budget on disk holds. Kept here rather than read off a dumped reply, so
# that the skip check and the writer are held to one list.
BUDGET_KEYS = ("total", "reasoning", "objectives")

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_THINKING = "high"
THINKING_LEVELS = ("minimal", "low", "medium", "high")
API_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
MAX_ATTEMPTS = 4
# One worker per section still to be budgeted, and the ceiling that keeps a book
# with hundreds of sections from putting hundreds of calls in flight at once.
MAX_WORKERS = 25


class ObjectiveShare(BaseModel):
    """One objective's share of the section's exercises."""

    id: str = Field(description="The objective this share is for, exactly as the spec prints it")
    exercises: int = Field(
        ge=1, description="How many of the section's exercises practise this objective"
    )


class BudgetReply(BaseModel):
    """The model's answer, which may refuse the section instead of budgeting it."""

    status: Literal["ok", "abort"]
    abort_reason: str = Field(
        default="", description="Why the section cannot be budgeted; empty when status is ok"
    )
    reasoning: str = Field(
        default="",
        description="Two or three sentences in English on how the split was decided",
    )
    objectives: list[ObjectiveShare] = Field(
        default_factory=list, description="One entry per objective, in the spec's order"
    )


def build_prompt(meta: dict[str, str]) -> Prompt:
    """The prompt this stage sends for the book, assembled from its parts."""
    return assemble(PROMPT_STAGE, meta)


def section_part(section: dict) -> str:
    """The section as the model sees it: its own attributes, then the whole spec.

    Sent compact and sent whole. A section spec is a few thousand tokens even at
    its longest, so nothing is trimmed the way the whole-book review has to trim,
    and the excerpts are part of what shows how much of the section each objective
    actually occupies.
    """
    body = json.dumps(
        {key: section[key] for key in ("number", "title", "module_path", "spec")},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    objectives = len(objective_ids(section))
    return (
        f"Budget for this section: exactly {section_total(objectives)} exercises, to be "
        f"divided between its {objectives} objective(s).\n\nSection spec JSON:\n{body}"
    )


def objective_ids(section: dict) -> list[str]:
    """The ids of the objectives this section's reviewed spec holds, in order."""
    return [row["id"] for row in section["spec"]["objectives"]]


def budget_problems(shares: list[ObjectiveShare], ids: list[str]) -> list[str]:
    """Every reason a budget cannot be used, as lines for the report.

    Two rules carry the stage. The shares must name the section's own objectives,
    one each, or the counts cannot be attached to anything. And they must sum to
    the budget exactly: a model that is a few either way has not divided the
    section's practice, it has guessed at it, and rounding its answer here would
    hide that. Both are checked before a reply is accepted, so a failure is
    retried like a parse failure.
    """
    problems: list[str] = []
    named = [share.id for share in shares]

    for duplicate in sorted({name for name in named if named.count(name) > 1}):
        problems.append(f"{duplicate} is budgeted more than once")
    for unknown in [name for name in named if name not in ids]:
        problems.append(f"{unknown} is not an objective of this section")
    for missing in [name for name in ids if name not in named]:
        problems.append(f"{missing} is left out of the budget")

    total = sum(share.exercises for share in shares)
    if total != section_total(len(ids)):
        problems.append(f"the shares add up to {total}, not {section_total(len(ids))}")

    return problems


def check_budget(reply: BudgetReply, ids: list[str]) -> None:
    """Reject a budget that cannot be used, reporting every reason at once."""
    problems = budget_problems(reply.objectives, ids)
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
) -> tuple[BudgetReply | None, str | None]:
    """Ask for one section's budget once, logging the call however it turns out.

    Returns the model's reply, or None alongside the failure to report. A budget
    that does not add up fails here like a parse failure does, and is retried the
    same way.
    """
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=BudgetReply,
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
        result = BudgetReply.model_validate_json(genai_answer_text(response))
        if result.status == "ok":
            check_budget(result, ids)
    except Exception as error:  # noqa: BLE001 - retry transport, parse and arithmetic alike
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
        step_name=BUDGET_STEP,
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


def request_budget(
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
) -> BudgetReply:
    """Call the model until it returns a usable budget, retrying every failure."""
    last_error: str | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        result, last_error = logged_call(
            client, model, prompt, section, ids, thinking, logger, item, attempt, context
        )
        if result is not None:
            return result
        if attempt < MAX_ATTEMPTS:
            # A budget that does not add up is refused here and asked for again,
            # so a section can spend its four attempts without a word otherwise.
            if report is not None:
                report(
                    f"attempt {attempt} of {MAX_ATTEMPTS} failed, "
                    f"{one_line(last_error or 'no reason given')}; "
                    f"asking again in {2**attempt}s"
                )
            time.sleep(2**attempt)

    raise RuntimeError(f"failed after {MAX_ATTEMPTS} attempts: {last_error}")


def budget_document(reply: BudgetReply) -> dict:
    """The budget as it is written to disk.

    The section's totals are summed here rather than asked for, so the file
    cannot disagree with itself. The objective rows carry ids and counts only:
    the objective's wording lives in the reviewed spec, and copying it here would
    give a reader two versions of it to choose between.
    """
    shares = [share.model_dump() for share in reply.objectives]
    return {
        "total": sum(share["exercises"] for share in shares),
        "reasoning": reply.reasoning,
        "objectives": shares,
    }


def written_budget(path: Path, envelope_keys: set[str], ids: list[str]) -> bool:
    """True when a section already holds a budget of the shape written today.

    The objectives are checked against the reviewed spec as it stands, not just
    against the schema, because this stage reads a file another stage can rewrite:
    a review that removes an objective leaves a budget that still names it, and
    that budget has to be asked for again rather than carried forward.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        budget = document["budget"]
        if set(document) != envelope_keys or set(budget) != set(BUDGET_KEYS):
            return False
        shares = [ObjectiveShare.model_validate(row) for row in budget["objectives"]]
        if budget_problems(shares, ids):
            return False
    except (OSError, LookupError, TypeError, ValueError):
        return False
    return True


def describe(budget: dict) -> str:
    """The counts worth seeing on one line while a book runs."""
    shares = budget["objectives"]
    spread = "/".join(str(share["exercises"]) for share in shares)
    return f"{budget['total']} across {len(shares)} objective(s) [{spread}]"


@dataclass(frozen=True)
class Run:
    """Everything a section is budgeted against that the whole book shares.

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


def budget_section(
    run: Run, section: dict, part: str, out_path: Path
) -> tuple[str, str]:
    """Budget one section, returning what became of it and the line to print.

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
    reply = request_budget(
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
            "total_exercises": section_total(len(ids)),
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

    budget = budget_document(reply)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                # The book's own attributes travel with every budget, so a
                # downstream reader needs nothing but this file and the spec it
                # points at. How the budget was produced belongs to the call log.
                "book_id": run.book_id,
                **run.meta,
                "number": number_path,
                "title": section["title"],
                "module_path": section["module_path"],
                "budget": budget,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return "written", f"  {number_path}: {describe(budget)} ({elapsed})"


def build_budgets(
    book_id: str, section: str | None, model: str, thinking: str, update: bool
) -> tuple[Path, int]:
    """Budget every section of a reviewed book, or just one."""
    if not any(os.environ.get(name) for name in API_KEY_VARS):
        raise RuntimeError(f"set one of {' / '.join(API_KEY_VARS)} to call the model")

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
    stage_dir = OUTPUT_DIR / book_id / BUDGETS_STAGE
    envelope_keys = {"book_id", *meta, "number", "title", "module_path", "budget"}

    pending: list[tuple[dict, str, Path]] = []
    skipped = 0
    for row in sections:
        out_path = stage_dir / f"{row['stem']}.json"
        if not update and written_budget(out_path, envelope_keys, objective_ids(row)):
            skipped += 1
            continue
        pending.append((row, section_part(row), out_path))

    written = aborted = 0
    failures: list[str] = []

    if pending:
        workers = min(len(pending), MAX_WORKERS)
        objectives = sum(len(objective_ids(row)) for row, _, _ in pending)
        exercises = sum(section_total(len(objective_ids(row))) for row, _, _ in pending)
        to_send = sum(len(part.encode("utf-8")) for _, part, _ in pending)
        opening = (
            f"budgeting {len(pending)} section(s) with {workers} worker(s), "
            f"{exercises} exercises across {objectives} objective(s), "
            f"{in_kb(to_send)} of specs to send"
        )
        if skipped:
            opening += f"; {skipped} already budgeted"
        print(opening, flush=True)

        # Both the client and the logger reach the network to start up, so a run
        # that budgets nothing needs neither. The logger's clock is set once here
        # rather than once per worker, and it is named on its own line because
        # setting it against NTP is the wait that can run into seconds.
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
                    pool.submit(budget_section, run, *item): item[0]["number"]
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

    summary = f"{written} section budget(s) written"
    if skipped:
        summary += f", {skipped} already budgeted"
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
        "-s", "--section", help="budget this section, or every section under it, e.g. -s 1 or -s 01"
    )
    parser.add_argument(
        "--update", action="store_true", help="rewrite section budgets that already exist"
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
        _, unbudgeted = build_budgets(
            args.book_id, args.section, args.model, args.thinking_level, args.update
        )
    except RuntimeError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    return 1 if unbudgeted else 0


if __name__ == "__main__":
    sys.exit(main())
