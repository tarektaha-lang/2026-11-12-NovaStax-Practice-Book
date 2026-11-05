#!/usr/bin/env python3
"""Run a practice book's stages in the order each one needs the last.

Each stage is its own script. This one does not import them or call a model: it
builds the same command line a person would type, runs it, and stops at the first
non-zero exit. Stages that already have their output skip the work themselves, so
a stopped book is resumed by naming the stage to start from rather than by this
script deciding what is done.

    python3 scripts/pipeline.py mathematics_ar_sa06_t2
    python3 scripts/pipeline.py mathematics_ar_sa06_t2 --from papers
    python3 scripts/pipeline.py mathematics_ar_sa06_t2 --from groups --to audios
    python3 scripts/pipeline.py mathematics_ar_sa06_t2 -s 1.1
    python3 scripts/pipeline.py --list
    python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --from sources --to groups --translate
    python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --from transcripts --to cost --translate
    python3 -B scripts/pipeline.py mathematics_en_eg08_t1 --from sources --to groups --translate --updateboth

-s is forwarded only to stages that accept a section. review, extract, draw,
frontmatter and cost are whole-book stages and still run: a one-section
pass of a book that has not been reviewed yet will stop at review, which is the
same refusal those scripts give on their own. --update is forwarded to the stages
that skip existing work; draw.py takes --again for that, so that is what it gets.

--model and --thinking-level are forwarded only when given, and only to stages
that take them, so each stage keeps its own default. audios.py speaks with
gemini-3.8-flash-tts through the Batch API; --model does not reach it.
--tts-model does. --tts-batch is the default and forwards --batch; --no-tts-batch
forwards --no-batch (unary generateContent). transcripts.py also defaults to
Batch; --transcripts-batch/--no-transcripts-batch forward --batch/--no-batch
to transcripts and ar_transcripts only (omitted, those scripts keep their
own default). --batch-parallel N (0 = no cap, default) is forwarded to
transcripts, audios and their ar_ copies. --download-workers N (default 3
on those scripts) is forwarded the same way. --no-output-backup is
forwarded to package and ar_package. --workers is forwarded to
the stages that cap how many unary calls run at once.

--translate pairs an `_en_` book with its `_ar_` sibling. Every selected stage
is generated for both books: English sources through groups, then Arabic
sources / translate / typeset, then English transcripts through cost, then
Arabic transcripts through cost. `--from`/`--to` slice that list; `--to groups`
means both books through groups, `--to cost` both books through cost. A stage
whose output is already on disk is skipped on that book. --updateboth rebuilds
the selected range on both books, with no skips. --no-output-backup is
forwarded to package and ar_package: the served zip is still written, the
dated book-output archive is not.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

THINKING_LEVELS = ("minimal", "low", "medium", "high")
TAIL_NAMES = ("transcripts", "audios", "align", "package", "cost")
PAPERS_STAGE = "12_section_papers"


@dataclass(frozen=True, slots=True)
class Stage:
    """One script in the pipeline, and the flags it actually accepts."""

    name: str
    summary: str
    section: bool = False
    update: bool = False
    model: bool = False
    thinking: bool = False
    workers: bool = False
    tts: bool = False
    transcripts_batch: bool = False
    update_flag: str = "--update"
    script_name: str | None = None
    book: str = "self"

    @property
    def script(self) -> Path:
        """The script this stage runs, beside this file."""
        return SCRIPT_DIR / f"{self.script_name or self.name}.py"

    @property
    def canonical(self) -> str:
        """The underlying script name, without an `ar_` prefix."""
        return self.script_name or self.name.removeprefix("ar_")


# The order is the dependency graph flattened. sources before specs, specs before
# review, review before anything that reads the applied book. labels, budgets and
# source_questions could run together after review; they are kept in sequence so a
# failure is one stage's, not three intertwined. frontmatter reads only the table
# of contents and could run first; it sits before papers because that is the first
# stage that binds it. cost is last: it prices logs, it does not produce the book.
STAGES: tuple[Stage, ...] = (
    Stage(
        "sources",
        "validate the table of contents and split the source PDFs",
        section=True,
        update=True,
    ),
    Stage(
        "specs",
        "write one lesson spec per section",
        section=True,
        update=True,
        model=True,
        thinking=True,
    ),
    Stage(
        "review",
        "review the assembled specs as one book",
        update=True,
        model=True,
        thinking=True,
    ),
    Stage(
        "labels",
        "name each objective for the paper's headings",
        section=True,
        update=True,
        model=True,
        thinking=True,
    ),
    Stage(
        "budgets",
        "share each section's exercise budget among its objectives",
        section=True,
        update=True,
        model=True,
        thinking=True,
    ),
    Stage(
        "source_questions",
        "transcribe the questions the source PDFs already print",
        section=True,
        update=True,
        model=True,
        thinking=True,
    ),
    Stage(
        "new_questions",
        "write new practice exercises to the budgeted counts",
        section=True,
        update=True,
        model=True,
        thinking=True,
        workers=True,
    ),
    Stage(
        "questions_review",
        "review the new exercises and leave out the ones that are wrong",
        section=True,
        update=True,
        model=True,
        thinking=True,
        workers=True,
    ),
    Stage("extract", "file every figure description for drawing"),
    Stage(
        "draw",
        "draw each figure as TikZ and compile it",
        update=True,
        model=True,
        thinking=True,
        update_flag="--again",
    ),
    Stage("frontmatter", "set the cover, title, copyright and contents pages"),
    Stage(
        "papers",
        "typeset the section papers and bind the book",
        section=True,
    ),
    Stage(
        "groups",
        "write the working JSON and the overlaid bboxes PDF",
        section=True,
    ),
    Stage(
        "transcripts",
        "write a spoken transcript for each printed group",
        section=True,
        update=True,
        model=True,
        thinking=True,
        transcripts_batch=True,
    ),
    Stage(
        "audios",
        "synthesise speech for each transcript segment",
        section=True,
        update=True,
        tts=True,
    ),
    Stage(
        "align",
        "align each WAV with its spoken transcript",
        section=True,
        update=True,
    ),
    Stage(
        "package",
        "assemble the Foliostax practice package",
        section=True,
    ),
    Stage("cost", "price the book's API call logs"),
)

STAGE_NAMES: tuple[str, ...] = tuple(stage.name for stage in STAGES)
STAGES_BY_NAME: dict[str, Stage] = {stage.name: stage for stage in STAGES}
EN_PRE: tuple[Stage, ...] = tuple(stage for stage in STAGES if stage.name not in TAIL_NAMES)
EN_TAIL: tuple[Stage, ...] = tuple(stage for stage in STAGES if stage.name in TAIL_NAMES)


def ar_copy(stage: Stage) -> Stage:
    """The same script run against the Arabic sibling, named `ar_<stage>`."""
    return Stage(
        name=f"ar_{stage.name}",
        summary=f"Arabic twin: {stage.summary}",
        section=stage.section,
        update=stage.update,
        model=stage.model,
        thinking=stage.thinking,
        workers=stage.workers,
        tts=stage.tts,
        transcripts_batch=stage.transcripts_batch,
        update_flag=stage.update_flag,
        script_name=stage.name,
        book="twin",
    )


TRANSLATE_STAGE = Stage(
    "translate",
    "translate EN specs, labels and exercises into the Arabic sibling",
    section=True,
    update=True,
    model=True,
    thinking=True,
    workers=True,
)
AR_TYPESET: tuple[Stage, ...] = (
    ar_copy(STAGES_BY_NAME["sources"]),
    TRANSLATE_STAGE,
    ar_copy(STAGES_BY_NAME["frontmatter"]),
    ar_copy(STAGES_BY_NAME["papers"]),
    ar_copy(STAGES_BY_NAME["groups"]),
)
AR_TAIL: tuple[Stage, ...] = tuple(ar_copy(stage) for stage in EN_TAIL)
TRANSLATE_STAGES: tuple[Stage, ...] = EN_PRE + AR_TYPESET + EN_TAIL + AR_TAIL


def catalog_for(translate: bool) -> tuple[Stage, ...]:
    return TRANSLATE_STAGES if translate else STAGES


def since(seconds: float) -> str:
    """A duration short enough to sit at the end of a status line."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    return f"{int(seconds) // 60}m{int(seconds) % 60:02d}s"


def stage_name(raw: str, catalog: Sequence[Stage] = STAGES) -> str:
    """The stage a CLI token names, with or without a .py suffix.

    Args:
        raw: A stage name as typed, e.g. ``specs`` or ``specs.py``.
        catalog: The stages this run knows, native or --translate.

    Returns:
        The canonical stage name.

    Raises:
        ValueError: The token names no stage in the catalog.
    """
    name = Path(raw).stem
    known = tuple(stage.name for stage in catalog)
    if name not in known:
        raise ValueError(f"unknown stage {raw!r}; known: {', '.join(known)}")
    return name


def effective_stop(
    stop: str | None, translate: bool, only: Sequence[str]
) -> str | None:
    """Rewrite `--to groups` / `--to cost` so translation mode covers the sibling."""
    if not translate or not stop or only:
        return stop
    name = Path(stop).stem
    if name == "groups":
        return "ar_groups"
    if name in TAIL_NAMES:
        return f"ar_{name}"
    return stop


def select_stages(
    start: str | None,
    stop: str | None,
    only: Sequence[str],
    skip: Sequence[str],
    catalog: Sequence[Stage] = STAGES,
) -> list[Stage]:
    """The stages to run, in pipeline order.

    ``only`` names a subset and is applied first, still in catalog order rather
    than the order the names were given, so a book cannot be run backwards.
    ``start``/``stop`` then slice that list, and ``skip`` removes names after.

    Args:
        start: Inclusive first stage, or None to start at the beginning.
        stop: Inclusive last stage, or None to run through to the end.
        only: Stage names to keep; empty means keep all.
        skip: Stage names to drop after the slice.
        catalog: Native STAGES, or TRANSLATE_STAGES when --translate is set.

    Returns:
        The chosen stages, never empty.

    Raises:
        ValueError: A name is unknown, the slice is empty, or start is after stop.
    """
    chosen = list(catalog)
    if only:
        wanted = {stage_name(name, catalog) for name in only}
        chosen = [stage for stage in chosen if stage.name in wanted]
    if start or stop:
        names = [stage.name for stage in chosen]
        begin = 0 if start is None else names.index(stage_name(start, catalog))
        end = len(names) if stop is None else names.index(stage_name(stop, catalog)) + 1
        if begin >= end:
            raise ValueError(
                f"stage {start or names[0]!r} is not before {stop or names[-1]!r} "
                "in the pipeline"
            )
        chosen = chosen[begin:end]
    if skip:
        dropped = {stage_name(name, catalog) for name in skip}
        chosen = [stage for stage in chosen if stage.name not in dropped]
    if not chosen:
        raise ValueError("no stages left to run")
    return chosen


def stage_command(
    stage: Stage,
    book_id: str,
    *,
    section: str | None,
    update: bool,
    model: str | None,
    thinking_level: str | None,
    workers: int | None,
    tts_model: str | None,
    tts_batch: bool | None = None,
    transcripts_batch: bool | None = None,
    batch_parallel: int | None = None,
        download_workers: int | None = None,
        selection_from: Path | None = None,
        no_output_backup: bool = False,
        force_generate: bool = False,
    ) -> list[str]:
    """The argv this orchestrator would type for one stage.

    Flags the stage does not declare are not added, even if the run asked for
    them: review.py has no -s, audios.py has no --thinking-level, and passing
    either would be an argparse error rather than a skipped option.

    Args:
        stage: The stage to invoke.
        book_id: The book every script takes as its positional argument.
        section: Optional -s value, forwarded only when the stage accepts it.
        update: Whether to redo work already on disk.
        model: Explicit Gemini model, or None to leave the stage's default.
        thinking_level: Explicit thinking level, or None to leave the default.
        workers: Concurrent-call cap for stages that take --workers, or None.
        tts_model: Explicit TTS model for audios.py, or None.
        tts_batch: True to forward --batch (the default), False for --no-batch,
            None to omit and leave audios.py's default.
        transcripts_batch: True to forward --batch to transcripts.py, False for
            --no-batch, None to omit and leave the script default.
        batch_parallel: Forward --batch-parallel N to transcripts/audios and
            their ar_ copies; None omits the flag.
        download_workers: Forward --download-workers N to transcripts/audios
            and their ar_ copies; None omits the flag.
        selection_from: Folder of English `*.selection.json` membership for Arabic papers.
        no_output_backup: Forward --no-output-backup to package.py and ar_package.

    Returns:
        An argv headed by the current interpreter.
    """
    command = [sys.executable, str(stage.script), book_id]
    if stage.section and section:
        command.extend(["-s", section])
    if stage.update and update:
        command.append(stage.update_flag)
    if stage.model and model:
        command.extend(["--model", model])
    if stage.thinking and thinking_level:
        command.extend(["--thinking-level", thinking_level])
    if stage.workers and workers is not None:
        command.extend(["--workers", str(workers)])
    if stage.tts and tts_model:
        command.extend(["--model", tts_model])
    if stage.tts and tts_batch is not None:
        command.append("--batch" if tts_batch else "--no-batch")
    if stage.transcripts_batch and transcripts_batch is not None:
        command.append("--batch" if transcripts_batch else "--no-batch")
    if batch_parallel is not None and (stage.tts or stage.transcripts_batch):
        command.extend(["--batch-parallel", str(batch_parallel)])
    if download_workers is not None and (stage.tts or stage.transcripts_batch):
        command.extend(["--download-workers", str(download_workers)])
    if stage.name == "ar_papers" and selection_from is not None:
        command.extend(["--membership-from", str(selection_from)])
    if stage.canonical == "papers":
        command.append("--report-english-glosses")
    if no_output_backup and stage.canonical == "package":
        command.append("--no-output-backup")
    if force_generate and stage.name == "new_questions":
        command.append("--force-generate")
    return command


def target_book(stage: Stage, en_id: str, ar_id: str | None) -> str:
    """The book id this stage writes to."""
    if stage.book == "twin":
        if not ar_id:
            raise RuntimeError(f"{stage.name} needs an Arabic sibling")
        return ar_id
    return en_id


def update_for(stage: Stage, update: bool, updateboth: bool) -> bool:
    """Whether this stage receives --update / --again.

    ``--updateboth`` forwards it to every selected stage on both books.
    ``--update`` alone still only reaches English stages.
    """
    if updateboth:
        return True
    if stage.book == "twin" or stage.name == "translate":
        return False
    return update


def should_skip(
    stage: Stage,
    book_id: str,
    section: str | None,
    *,
    translate: bool,
    update: bool,
    updateboth: bool,
    output_dir: Path | None = None,
) -> bool:
    """Whether --translate should leave this stage uninvoked because it is done.

    Same rule for every selected stage (sources through cost, English and Arabic):
    skip when that book's artefacts exist. ``translate`` is checked against the
    Arabic tree. ``--updateboth`` never skips. ``--update`` without it still
    re-invokes English stages that skip internally, but not always-rerun stages
    and not the Arabic twin.
    """
    if not translate or updateboth:
        return False
    from src.twin import ALWAYS_RERUN, EN_MARK, sibling_id, stage_output_exists

    check_id = book_id
    if stage.name == "translate" and EN_MARK in book_id:
        check_id = sibling_id(book_id)
    if not stage_output_exists(stage.name, check_id, section, output_dir=output_dir):
        return False
    if update and stage.book != "twin" and stage.name != "translate":
        return stage.canonical in ALWAYS_RERUN
    return True


def print_plan(book_id: str, stages: Sequence[Stage], section: str | None) -> None:
    """Say which book is being run and which stages, before the first one starts."""
    names = " → ".join(stage.name for stage in stages)
    scope = f", section {section}" if section else ""
    print(f"pipeline: {book_id}{scope}, {len(stages)} stage(s)", flush=True)
    print(f"  {names}", flush=True)


def run_stage(command: Sequence[str]) -> int:
    """Run one stage, streaming its output, and return its exit code.

    Args:
        command: The argv built by stage_command.

    Returns:
        The child process exit code.
    """
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    completed = subprocess.run(command, cwd=PROJECT_ROOT, env=env, check=False)
    return completed.returncode


def list_stages(catalog: Sequence[Stage] = STAGES) -> None:
    """Print every stage in order, with the one-line summary --list is for."""
    width = max(len(stage.name) for stage in catalog)
    for index, stage in enumerate(catalog, start=1):
        print(f"  {index:>2}. {stage.name:<{width}}  {stage.summary}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "book_id",
        nargs="?",
        help="Book ID, e.g. mathematics_ar_sa06_t2",
    )
    parser.add_argument("-s", "--section", help="hold section-scoped stages to this section")
    parser.add_argument(
        "--update",
        action="store_true",
        help="redo work already on disk (draw.py receives --again)",
    )
    parser.add_argument(
        "--translate",
        action="store_true",
        help="after the English book, translate it into its _ar_ sibling and typeset that too",
    )
    parser.add_argument(
        "--updateboth",
        action="store_true",
        help="with --translate, rebuild the selected range on both books",
    )
    parser.add_argument(
        "--model",
        help="Gemini model for stages that call one; omitted, each stage keeps its default",
    )
    parser.add_argument(
        "--thinking-level",
        choices=THINKING_LEVELS,
        help="thinking level for stages that take one; omitted, each stage keeps its default",
    )
    parser.add_argument(
        "--tts-model",
        help="Gemini TTS model for audios.py; omitted, audios.py keeps its default",
    )
    parser.add_argument(
        "--tts-batch",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="use gemini-3.8-flash-tts Batch API for audios.py (default); "
        "--no-tts-batch calls unary generateContent",
    )
    parser.add_argument(
        "--transcripts-batch",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="forward --batch/--no-batch to transcripts.py and ar_transcripts.py; "
        "omitted, those scripts keep their own default (Batch)",
    )
    parser.add_argument(
        "--batch-parallel",
        type=int,
        default=None,
        metavar="N",
        help="0 = no cap (default); N = at most N jobs in flight; "
        "forwarded to transcripts, audios and their ar_ copies",
    )
    parser.add_argument(
        "--download-workers",
        type=int,
        default=None,
        metavar="N",
        help="parallel downloads of finished batch jobs; 1 = serial; "
        "each worker can peak at ~1-1.5 GB for an audio job; "
        "use 2 or 1 when several books run; forwarded to transcripts, "
        "audios and their ar_ copies",
    )
    parser.add_argument(
        "--workers",
        type=int,
        help="most calls in flight for stages that take --workers",
    )
    parser.add_argument(
        "--force-generate",
        action="store_true",
        help="forward to new_questions.py: retry model abort refusals",
    )
    parser.add_argument(
        "--from",
        dest="start",
        metavar="STAGE",
        help="first stage to run, inclusive, e.g. --from papers",
    )
    parser.add_argument(
        "--to",
        dest="stop",
        metavar="STAGE",
        help="last stage to run, inclusive, e.g. --to package",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="STAGE",
        help="run only these stages, still in pipeline order",
    )
    parser.add_argument(
        "--skip",
        nargs="+",
        metavar="STAGE",
        help="drop these stages from the run",
    )
    parser.add_argument(
        "--no-output-backup",
        action="store_true",
        help="forward --no-output-backup to package and ar_package "
        "(served zip only; skip the dated book-output archive)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="print the stages in order and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the commands without running them",
    )
    args = parser.parse_args()

    if args.updateboth and not args.translate:
        parser.error("--updateboth requires --translate")

    catalog = catalog_for(args.translate)
    if args.list:
        list_stages(catalog)
        return 0
    if not args.book_id:
        parser.error("book_id is required")
    if args.workers is not None and args.workers < 1:
        parser.error("--workers must be at least 1")
    if args.batch_parallel is not None and args.batch_parallel < 0:
        parser.error("--batch-parallel must be >= 0")
    if args.download_workers is not None and args.download_workers < 1:
        parser.error("--download-workers must be at least 1")

    ar_id: str | None = None
    if args.translate:
        try:
            from src.twin import pair_books, require_english_id

            require_english_id(args.book_id)
            ar_id = pair_books(args.book_id)
        except (RuntimeError, ValueError) as error:
            print(f"FAILED: {error}", file=sys.stderr)
            return 1

    try:
        stages = select_stages(
            args.start,
            effective_stop(args.stop, args.translate, args.only or ()),
            args.only or (),
            args.skip or (),
            catalog,
        )
    except ValueError as error:
        parser.error(str(error))

    missing = [stage.name for stage in stages if not stage.script.is_file()]
    if missing:
        print(
            f"FAILED: no script for {', '.join(missing)} under {SCRIPT_DIR}",
            file=sys.stderr,
        )
        return 1

    print_plan(args.book_id, stages, args.section)
    if args.translate and ar_id:
        print(f"  Arabic sibling: {ar_id}", flush=True)
    if not args.dry_run:
        from src.academic_year import ensure_academic_year

        ensure_academic_year(args.book_id)
        if ar_id:
            ensure_academic_year(ar_id)
    started = time.perf_counter()
    elapsed: list[tuple[str, float]] = []
    selection_from = (
        PROJECT_ROOT / "data" / "output" / args.book_id / PAPERS_STAGE
        if args.translate
        else None
    )
    from src.twin import tap_progress_line, tap_stage_counts

    for index, stage in enumerate(stages, start=1):
        book_id = target_book(stage, args.book_id, ar_id)
        label = "AR" if stage.book == "twin" or stage.name == "translate" else "EN"
        counts = tap_stage_counts(stage.canonical, book_id, args.section)
        if should_skip(
            stage,
            book_id,
            args.section,
            translate=args.translate,
            update=args.update,
            updateboth=args.updateboth,
        ):
            print(
                f"\n=== {index}/{len(stages)} {stage.name} ===",
                flush=True,
            )
            if counts is not None:
                print(
                    f"skip {tap_progress_line(label, stage.canonical, *counts)}",
                    flush=True,
                )
            else:
                print(f"skip {label} {stage.canonical}: already on disk", flush=True)
            continue
        command = stage_command(
            stage,
            book_id,
            section=args.section,
            update=update_for(stage, args.update, args.updateboth),
            model=args.model,
            thinking_level=args.thinking_level,
            workers=args.workers,
            tts_model=args.tts_model,
            tts_batch=args.tts_batch,
            transcripts_batch=args.transcripts_batch,
            batch_parallel=args.batch_parallel,
            download_workers=args.download_workers,
            selection_from=selection_from,
            no_output_backup=args.no_output_backup,
            force_generate=args.force_generate,
        )
        relative = Path(command[1]).relative_to(PROJECT_ROOT)
        shown = " ".join([str(relative), *command[2:]])
        print(f"\n=== {index}/{len(stages)} {stage.name} ===", flush=True)
        if counts is not None:
            print(tap_progress_line(label, stage.canonical, *counts), flush=True)
        print(f"$ python3 {shown}", flush=True)
        if args.dry_run:
            continue
        stage_started = time.perf_counter()
        try:
            code = run_stage(command)
        except KeyboardInterrupt:
            took = time.perf_counter() - stage_started
            print(
                f"FAILED: interrupted during {stage.name} after {since(took)}",
                file=sys.stderr,
            )
            return 130
        took = time.perf_counter() - stage_started
        elapsed.append((stage.name, took))
        if code:
            print(
                f"FAILED: {stage.name} exited {code} after {since(took)}",
                file=sys.stderr,
            )
            resume = f"python3 scripts/pipeline.py {args.book_id} --from {stage.name}"
            if args.section:
                resume += f" -s {args.section}"
            if args.translate:
                resume += " --translate"
            if args.updateboth:
                resume += " --updateboth"
            print(f"resume with: {resume}", file=sys.stderr)
            return code
        print(f"OK: {stage.name} in {since(took)}", flush=True)

    total = time.perf_counter() - started
    if args.dry_run:
        print(f"\nOK: {len(stages)} command(s) not run")
        return 0
    print(f"\nOK: {len(stages)} stage(s) in {since(total)}")
    for name, took in elapsed:
        print(f"  {name}: {since(took)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
