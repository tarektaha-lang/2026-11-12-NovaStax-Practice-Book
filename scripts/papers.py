#!/usr/bin/env python3
"""Set a book's practice papers from its exercises and its figures.

The last stage, and the only one that makes a PDF. It reads what questions_review.py
wrote, what draw.py drew and what labels.py named, and hands them to latex.py,
which owns the LaTeX itself. Nothing here calls a model, so a paper can be set again
as often as the look of it is being worked on, for the cost of a compile.

Every section in scope is set every time, with no skipping of one already on
disk: when the question is how the page looks, a stage that decided the paper
was already fine would be the opposite of useful. A section whose LaTeX will
not compile, or whose target-language audit fails, is reported with that
complaint and leaves its .tex behind to read when the engine ran, while the
rest of the sections are still set. A stale PDF for that section is removed so
it cannot sneak into a later bind.

Runs after questions_review.py, draw.py and labels.py, and it sets the accepted
exercises in 09_section_questions_review. By default every figure the section
uses must already be in the drawing library; otherwise that section is not set,
and the run says how many are missing, so this run does not put `[figure: …]`
placeholders on the page. Pass `--allow-missing-figures` to set anyway (the
description printed in italics where no drawing exists).
By default, Arabic books report Latin/English etymology words in exercise fields
(Arkhaios, logos, Moseion, …) in `english_gloss_report.json` under the papers
folder and still typeset those sections. Pass `--no-report-english-glosses` to
block the section again when the target-language audit finds them. A section that
has not been named is still set without headings.

Each section says when it goes to the engine, so a book of them reads as work in
progress rather than as a wait; how each came out is reported afterwards, in the
book's order, because that is the report worth keeping.

The pages are then numbered through the book and the papers bound into one PDF of the
whole book, bookmarked section by section, which is the file to read a book in and to
hand to anyone else. Numbering and binding run only when every accepted section has a
paper on disk; a holey book is not numbered, not bound, and any previous bound PDF is
removed. A paper is set before it can know where in the book it falls, so the
numbering is a pass of its own after every paper is in. The book begins with its
front matter — the cover, the title page, the copyright page and the contents that
frontmatter.py set — so the first paper is numbered from the page after those, the
total every foot says counts them, and the front matter's own feet are stamped with
that total here, the one place it is known. A book whose front matter has not been set
is numbered and bound without it, and says so.

The book is bound twice: once as it is to be read, and once with the grid the paper is
set on drawn under every page, for looking at how the layout is coming along. The grid
is one page, compiled once and laid under all of them, so the second book is the first
book with lines behind it rather than a second setting of it, and it costs one compile
however long the book runs to.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    import frontmatter
    import src.latex as latex
    from draw import library, measure_library
    from labels import section_labels
    from pypdf import PdfReader, PdfWriter
    from specs import Progress, since
    from src.prompting import Profile
    from sources import PROJECT_ROOT, OUTPUT_DIR, book_metadata
    from questions_review import accepted_sections
    from src.translation_quality import (
        split_english_gloss_problems,
        target_document_problems,
    )
except ModuleNotFoundError as error:  # pragma: no cover
    sys.exit(f"missing dependency ({error.name}): pip install -r requirements.txt")

PAPERS_STAGE = "12_section_papers"

# The engine is a subprocess doing arithmetic, so the useful number of them at
# once is the number of cores, not the number of sections.
LATEX_LANES = max(2, (os.cpu_count() or 4) - 1)
UNRESOLVED_FIGURE = re.compile(r"\[(?:figure|الشكل|شكل)\s+[0-9٠-٩]+")
UNRESOLVED_POEM = re.compile(r"\[(?:poem|القصيدة|قصيدة)\s+[0-9٠-٩]+")
ESCAPED_MATH = re.compile(r"\\textbackslash\{\}(?:frac|sqrt|begin|end)")
MAX_X_SP = 45_000_000
MAX_Y_SP = 60_000_000


def display_path(path: Path) -> Path:
    """``path`` relative to the project when it is inside it, otherwise as given."""
    try:
        return path.relative_to(PROJECT_ROOT)
    except ValueError:
        return path


def tex_audit_problems(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        return [f"cannot read generated TeX: {error}"]
    problems: list[str] = []
    if found := UNRESOLVED_FIGURE.search(text):
        problems.append(f"unresolved figure marker {found.group()!r}")
    if found := UNRESOLVED_POEM.search(text):
        problems.append(f"unresolved poem marker {found.group()!r}")
    if found := ESCAPED_MATH.search(text):
        problems.append(f"escaped naked mathematics {found.group()!r}")
    if "Missing character:" in text:
        problems.append("generated TeX contains a missing-character diagnostic")
    return problems


def bbox_audit_problems(
    path: Path, printed_ids: list[str], *, rtl: bool
) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        return [f"cannot read bbox data: {error}"]
    wanted = set(printed_ids)
    marks: dict[str, list[str]] = {name: [] for name in printed_ids}
    problems: list[str] = []
    expected_direction = "R" if rtl else "L"
    for line_number, line in enumerate(lines, start=1):
        parts = line.split()
        if len(parts) != 7:
            problems.append(f"bbox line {line_number} has {len(parts)} fields")
            continue
        mark, page, name, x, y, width, direction = parts
        if name not in wanted:
            continue
        marks[name].append(mark)
        try:
            page_number, x_sp, y_sp, width_sp = map(int, (page, x, y, width))
        except ValueError:
            problems.append(f"bbox line {line_number} has a non-integer coordinate")
            continue
        if page_number < 1 or not (0 < x_sp <= MAX_X_SP) or not (
            0 < y_sp <= MAX_Y_SP
        ) or width_sp <= 0:
            problems.append(f"bbox line {line_number} is outside the page")
        if direction != expected_direction:
            problems.append(
                f"{name} has bbox direction {direction}, expected {expected_direction}"
            )
    for name, found in marks.items():
        if found.count("B") != 1 or found.count("E") != 1 or len(found) != 2:
            problems.append(
                f"{name} has bbox marks {found!r}, expected one B and one E"
            )
    return problems


def paper(
    book_id: str,
    stem: str,
    document: dict,
    papers_dir: Path,
    drawings: latex.Drawings,
    profile: Profile,
    progress: Progress,
    *,
    allow_missing_figures: bool,
    report_english_glosses: bool = True,
    locked_left_out: dict[str, list[str]] | None = None,
    locked_printed_ids: list[str] | None = None,
    expected_printed_ids: list[str] | tuple[str, ...] | None = None,
) -> tuple[Literal["set", "undrawn", "uncompiled"], str, list[dict[str, str]]]:
    """Set one section's paper, saying how it came out and what to report.

    Never raises: a section that will not compile, or whose figures are not all
    drawn, is one line of the report, and the sections after it are still worth
    setting. `allow_missing_figures` sets it anyway, printing each missing
    figure as the description it was to be drawn from.

    The first of what it returns is `set` when the paper came out, `undrawn`
    when a figure was missing and the section was not set, and `uncompiled`
    when the target-language audit or the engine refused it.

    Says when it goes to the engine but not how it came out: that is the caller's
    to print, once every section is in and they can be read in the book's order.

    `profile` is the book's: which language it is in and so which face and which
    direction its papers are set in. Read once off the table of contents, because
    it is the book's to say and every paper of the book is set the same way.
    """
    number = document["number"]
    figures = latex.figures_in(document)

    def drawn_figure(description: str) -> bool:
        row = drawings.get(description) or {}
        return bool(str(row.get("tikz", "")).strip())

    drawn = sum(1 for description in figures if drawn_figure(description))
    labels = section_labels(book_id, stem)
    note = f", {len(figures)} figure(s), {drawn} drawn" if figures else ""
    groups = len(latex.section_groups(document))
    progress.step(number, "measuring", f"measuring {groups} question(s){note}")

    if figures and drawn < len(figures) and not allow_missing_figures:
        (papers_dir / f"{stem}.pdf").unlink(missing_ok=True)
        progress.done(number)
        missing = len(figures) - drawn
        return (
            "undrawn",
            f"  {number}: {missing} of {len(figures)} figure(s) not drawn "
            f"(run extract.py and draw.py before papers.py)",
            [],
        )

    gloss_report: list[dict[str, str]] = []
    try:
        target_problems = target_document_problems(
            document,
            {
                "language_code": profile.language_code,
                "numerals": profile.numerals,
                "notation": profile.notation,
            },
        )
        blocking, gloss = split_english_gloss_problems(target_problems)
        if gloss and report_english_glosses:
            gloss_report = [
                {
                    "section": number,
                    "stem": stem,
                    "field": row.split(":", 1)[0],
                    "detail": row,
                }
                for row in gloss
            ]
        elif gloss:
            blocking.extend(gloss)
        if blocking:
            raise RuntimeError(
                "target-language audit failed: " + "; ".join(blocking)
            )
        paper = latex.build_section_pdf(
            document,
            papers_dir / f"{stem}.tex",
            drawings,
            labels,
            profile,
            locked_left_out=locked_left_out,
            locked_printed_ids=locked_printed_ids,
            expected_printed_ids=expected_printed_ids,
        )
    except RuntimeError as error:
        (papers_dir / f"{stem}.pdf").unlink(missing_ok=True)
        progress.done(number)
        return "uncompiled", f"  {number}: {error}", []

    audit_problems: list[str] = []
    if paper.selection:
        chosen = paper.selection
        if (
            locked_printed_ids is not None
            and chosen.printed_ids != locked_printed_ids
        ):
            audit_problems.append(
                "printed exercise order differs from the requested selection lock"
            )
        if (
            expected_printed_ids is not None
            and set(chosen.printed_ids) != set(expected_printed_ids)
        ):
            audit_problems.append(
                "printed exercise membership differs from the requested lock"
            )
        audit_problems.extend(
            bbox_audit_problems(
                papers_dir / f"{stem}.bbox",
                chosen.printed_ids,
                rtl=profile.rtl,
            )
        )
    audit_problems.extend(tex_audit_problems(papers_dir / f"{stem}.tex"))
    if audit_problems:
        (papers_dir / f"{stem}.pdf").unlink(missing_ok=True)
        progress.done(number)
        return (
            "uncompiled",
            f"  {number}: post-typesetting audit failed: "
            + "; ".join(audit_problems),
            [],
        )

    progress.done(number)
    notes = [f"{paper.pages} page paper"]
    if paper.selection:
        chosen = paper.selection
        notes.append(
            f"{chosen.printed} of {chosen.written} exercises printed for a budget of "
            f"{chosen.budget}, interior sparsest {chosen.interior_fill:.0%} full, "
            f"last page {chosen.fill:.0%} full"
        )
        if chosen.added:
            notes.append(f"{chosen.added} over budget to fill pages from the pool")
        (papers_dir / f"{stem}.selection.json").write_text(
            json.dumps(
                {
                    "budget": chosen.budget,
                    "written": chosen.written,
                    "printed": chosen.printed,
                    "printed_ids": chosen.printed_ids,
                    "worst_interior_fill": round(chosen.interior_fill, 3),
                    "last_page_fill": round(chosen.fill, 3),
                    "left_out": chosen.left_out,
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
    if paper.planned:
        # The last page of a section is the only one entitled to be part empty, so
        # it is left out of what the pages are reported as having wasted.
        foot = paper.slack[:-1]
        notes.append(
            f"{sum(foot) / max(len(foot), 1):.0f}pt spare at the foot of a page"
            f", {max(foot, default=0):.0f}pt at worst"
        )
        if paper.divided:
            notes.append(
                f"{paper.divided} set(s) divided at member boundaries "
                "to preserve order and fill pages"
            )
        if paper.loose:
            notes.append(f"{paper.loose} block(s) taller than the paper, left to TeX to break")
        if paper.tries > 1:
            notes.append(f"planned {paper.tries} times")
    else:
        notes.append("could not be measured; page breaks left to TeX")
    if labels:
        notes.append(f"{len(labels)} objective label(s)")
    else:
        notes.append("no objective labels; run labels.py")
    if figures:
        notes.append(f"{drawn}/{len(figures)} figure(s) drawn")
    repairs = latex.count_repairs(document)
    if repairs:
        notes.append(f"{repairs} missing $ repaired")
    if gloss_report:
        notes.append(f"{len(gloss_report)} English gloss(es) reported")
    return "set", f"  {number}: {', '.join(notes)}", gloss_report


def stamp(tex_path: Path, number: str, first: int, total: int, progress: Progress) -> str:
    """Put the book's page numbers in one paper's foot, saying what went wrong if any.

    Never raises, for the same reason setting a paper does not: a paper that will not
    come back from the engine is a line of the report.
    """
    progress.step(number, "numbering", f"numbering from page {first} of {total}")
    try:
        latex.stamp_pages(tex_path, first, total)
    except RuntimeError as error:
        progress.done(number)
        return f"  {number}: {error}"
    progress.done(number)
    return ""


def missing_papers(
    order: list[tuple[str, dict]], papers_dir: Path
) -> list[str]:
    """Section numbers in book order whose paper PDF is not on disk."""
    missing: list[str] = []
    for stem, document in order:
        if not (papers_dir / f"{stem}.pdf").is_file():
            missing.append(str(document["number"]))
    return missing


def bound_book_paths(
    book_id: str, output_dir: Path | None = None
) -> tuple[Path, Path]:
    """The readable book PDF and the grid copy beside it."""
    root = (output_dir or OUTPUT_DIR) / book_id
    return root / f"{book_id}.pdf", root / f"{book_id}_grid.pdf"


def unbound_book_note(book_pdf: Path, section_pdfs: list[Path]) -> str:
    """What a section-only run says about the book PDF it left alone."""
    older = book_pdf.is_file() and any(
        path.is_file() and path.stat().st_mtime > book_pdf.stat().st_mtime
        for path in section_pdfs
    )
    if older:
        return "book PDF was NOT rebound and is now older than the section paper"
    return "book PDF was NOT rebound"


def discard_bound_book(book_id: str, output_dir: Path | None = None) -> None:
    """Remove a previous bound book so a holey run cannot leave a hand-out file."""
    for path in bound_book_paths(book_id, output_dir):
        path.unlink(missing_ok=True)


def numbering(
    order: list[tuple[str, dict]], papers_dir: Path, before: int
) -> tuple[list[tuple[Path, str, int]], int]:
    """Where every paper falls in the book, and how many pages the book runs to.

    Counted from the papers on disk, in the book's order, so that the numbers are the
    bound book's. Called only when every accepted section has a paper. `before` is how
    many pages stand in front of the first paper — the front matter's — and the total
    counts them.
    """
    runs: list[tuple[Path, str, int]] = []
    first = before + 1
    for stem, document in order:
        pdf = papers_dir / f"{stem}.pdf"
        if not pdf.is_file():
            continue
        pages = len(PdfReader(pdf).pages)
        runs.append((papers_dir / f"{stem}.tex", document["number"], first))
        first += pages
    return runs, first - 1


def number_pages(
    order: list[tuple[str, dict]], papers_dir: Path, front: frontmatter.FrontMatter | None
) -> list[str]:
    """Number the pages of every paper the book wants numbered differently.

    A paper is set before the book is bound and so cannot know where in the book it
    falls; this is the pass that tells it, once every paper is in and the pages can be
    counted through. Only the papers whose numbers have moved go back to the engine, so
    setting one section of a long book costs that section and whatever follows it.

    The front matter is numbered here too: its pages were set before any paper was and
    say a total of their own, and this is the one place the book's total is known.

    The foot sits in the margin and nothing else about the paper is touched, so no page
    can come out differently for having been numbered.
    """
    runs, total = numbering(order, papers_dir, front.pages if front else 0)
    wanted = [run for run in runs if latex.page_stamp(run[0]) != (run[2], total)]
    failures: list[str] = []
    if front:
        failures += frontmatter.stamp(front, total)
    if not wanted:
        return failures

    print(
        f"numbering {len(wanted)} paper(s) of the {total} page(s) the book runs to",
        flush=True,
    )
    with Progress(len(wanted), phases=("numbering",)) as progress:
        with ThreadPoolExecutor(max_workers=LATEX_LANES) as pool:
            failures += list(
                pool.map(
                    lambda run: stamp(run[0], run[1], run[2], total, progress), wanted
                )
            )
    return [line for line in failures if line]


def bind(
    book_id: str,
    order: list[tuple[str, dict]],
    papers_dir: Path,
    front: frontmatter.FrontMatter | None,
) -> tuple[Path, int, int]:
    """Bind the front matter and the section papers into one PDF, in the book's order.

    Bound from the papers on disk rather than from the ones this run set, so that
    working on a single section over and over can refresh an already-complete book.
    The caller must not invoke this when any accepted section is missing a paper;
    a holey book is not a book.

    Each section is bookmarked by its number and title, because a book of a hundred
    pages that can only be reached by scrolling is a book nobody opens twice; of the
    front matter, the contents page is bookmarked, by its own heading.
    """
    book_pdf, _ = bound_book_paths(book_id)
    if missing_papers(order, papers_dir):
        return book_pdf, 0, 0
    writer = PdfWriter()
    bound = 0
    for pdf, bookmark in front.bound if front else []:
        writer.append(pdf, bookmark or None)
    for stem, document in order:
        section_pdf = papers_dir / f"{stem}.pdf"
        if section_pdf.exists():
            writer.append(section_pdf, f"{document['number']}. {document['title']}")
            bound += 1
    pages = len(writer.pages)
    if bound:
        writer.write(book_pdf)
    writer.close()
    return book_pdf, bound, pages


def rule_grid(
    book_pdf: Path,
    papers_dir: Path,
    front: frontmatter.FrontMatter | None,
    profile: Profile,
) -> Path:
    """Lay the grid under every page of a bound book, as a second book beside it.

    The grid is drawn once and the same page is put under all of them, so the two
    books are the same pages twice over: whatever the grid shows about where a word
    sits is true of the book that is handed out, because it is that book with lines
    under it and not a second setting of it.

    Under rather than over, so that a rule falling where a word does is the one that
    gives way; the lines are for reading the page by, not for reading instead of it.
    The cover and the title page are left as they are: they are drawn to the cover's
    own layout, not set on the grid, so the grid would say nothing true about them.
    """
    grid_pdf = latex.build_grid_pdf(
        papers_dir / "fitting" / "grid.tex", profile
    )
    grid = PdfReader(grid_pdf).pages[0]
    # Cloned rather than appended so that the section bookmarks come across: the
    # grid book is the one being read closely, and it is the one that most wants
    # them.
    writer = PdfWriter(clone_from=book_pdf)
    drawn = 2 if front else 0
    for index, page in enumerate(writer.pages):
        if index < drawn:
            continue
        page.merge_page(grid, over=False)
    ruled_pdf = book_pdf.with_name(f"{book_pdf.stem}_grid.pdf")
    writer.write(ruled_pdf)
    writer.close()
    return ruled_pdf


SELECTION_LOCK_SUFFIX = ".selection.json"


def lock_section_stems(lock_root: Path) -> list[str]:
    """Section stems named by ``01_01.selection.json``, not Path.stem."""
    return sorted(
        path.name.removesuffix(SELECTION_LOCK_SUFFIX)
        for path in lock_root.glob(f"*{SELECTION_LOCK_SUFFIX}")
        if path.name.endswith(SELECTION_LOCK_SUFFIX)
    )


def unaccepted_lock_stems(lock_root: Path, accepted_stems: set[str]) -> list[str]:
    """Lock files for sections this book has not accepted yet."""
    return [stem for stem in lock_section_stems(lock_root) if stem not in accepted_stems]


def set_papers(
    book_id: str,
    section: str | None,
    *,
    allow_missing_figures: bool = False,
    report_english_glosses: bool = True,
    selection_from: Path | None = None,
    membership_from: Path | None = None,
    section_only: bool = False,
    output_dir: Path | None = None,
) -> tuple[Path, int]:
    """Set every paper of a book, or one section's, and say how each came out.

    A section whose figures are not all drawn is not set, unless
    `allow_missing_figures` is set, in which case the description is printed
    where the drawing would have been.     Either way the count this returns is
    the sections that did not come out, audit blocks, compile failures and
    missing figures together, plus any paper left with the wrong page numbers,
    or the number of accepted sections still without a PDF when the book cannot
    be bound.
    """
    print("looking for the LaTeX engine", flush=True)
    latex.require_latex()
    print(f"reading the written exercises of {book_id}", flush=True)
    documents = accepted_sections(book_id, section)
    papers_dir = output_dir or OUTPUT_DIR / book_id / PAPERS_STAGE
    papers_dir.mkdir(parents=True, exist_ok=True)

    # A drawing filed without its size would have no room held for it on the
    # page, and measuring is a compile rather than a call, so it is done here
    # rather than asked of whoever drew it.
    measure_library(book_id)
    drawings = library(book_id)
    profile = Profile.of(book_metadata(book_id))

    if selection_from is not None and membership_from is not None:
        raise RuntimeError("choose selection_from or membership_from, not both")
    lock_root = selection_from or membership_from
    if lock_root is not None:
        shown = display_path(lock_root.resolve())
        mode = "membership" if membership_from is not None else "membership and order"
        print(f"locking printed exercise {mode} from {shown}", flush=True)
        if section is None:
            have = {stem for stem, _ in documents}
            absent = unaccepted_lock_stems(lock_root, have)
            if absent:
                print(
                    "membership lock has no accepted exercises for "
                    f"{', '.join(absent)}; those sections are not in this book",
                    flush=True,
                )

    started = time.perf_counter()
    print(
        f"setting {len(documents)} paper(s) in {LATEX_LANES} lane(s), "
        f"from a library of {len(drawings)} drawing(s)",
        flush=True,
    )

    # The sections are reported in the order the book has them, however the pool
    # finishes them, so the report reads like the book. Each says when it starts
    # so that the wait for a book's worth of compiles is not a silent one.
    def set_one(pair: tuple[str, dict], progress: Progress):
        lock = latex.membership_lock_from(
            None if lock_root is None else lock_root / f"{pair[0]}.selection.json",
            require_printed=selection_from is not None,
        )
        return paper(
            book_id,
            *pair,
            papers_dir,
            drawings,
            profile,
            progress,
            allow_missing_figures=allow_missing_figures,
            report_english_glosses=report_english_glosses,
            locked_left_out=None if lock is None else lock.left_out,
            locked_printed_ids=(
                list(lock.printed_ids)
                if lock is not None and selection_from is not None
                else None
            ),
            expected_printed_ids=(
                None if lock is None or not lock.printed_ids else lock.printed_ids
            ),
        )

    with Progress(len(documents), phases=("measuring",)) as progress:
        with ThreadPoolExecutor(max_workers=LATEX_LANES) as pool:
            results = list(
                pool.map(lambda pair: set_one(pair, progress), documents)
            )

    print(
        f"how each came out, in the book's order "
        f"({since(time.perf_counter() - started)}):",
        flush=True,
    )
    gloss_findings: list[dict[str, str]] = []
    for outcome, line, reported in results:
        print(line, flush=True)
        if reported:
            gloss_findings.extend(reported)
    if report_english_glosses and profile.language_code == "ar":
        report_path = papers_dir / "english_gloss_report.json"
        report_path.write_text(
            json.dumps(
                {"book_id": book_id, "findings": gloss_findings},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(
            f"English gloss report: {len(gloss_findings)} field(s) flagged → "
            f"{display_path(report_path)}",
            flush=True,
        )
        for row in gloss_findings:
            print(f"  {row['section']}: {row['detail']}", flush=True)
    set_count = sum(outcome == "set" for outcome, _, _ in results)
    undrawn = sum(outcome == "undrawn" for outcome, _, _ in results)
    uncompiled = sum(outcome == "uncompiled" for outcome, _, _ in results)

    if section_only:
        failures = undrawn + uncompiled
        print(
            f"OK: isolated section output left under "
            f"{display_path(papers_dir)}"
        )
        book_pdf, _grid = bound_book_paths(book_id)
        print(
            unbound_book_note(
                book_pdf,
                [path for path in papers_dir.glob("*.pdf") if path.is_file()],
            ),
            flush=True,
        )
        return papers_dir, failures

    # One section set is still a whole book bound, so the book's order is read again
    # when this run was only asked for part of it.
    order = documents if section is None else accepted_sections(book_id, None)
    failures = undrawn + uncompiled
    summary = f"{set_count} paper(s) set"
    if undrawn:
        summary += f", {undrawn} not set for a figure not drawn"
    if uncompiled:
        summary += f", {uncompiled} not set"
    missing = missing_papers(order, papers_dir)
    if missing:
        discard_bound_book(book_id)
        print(f"{summary} under {display_path(papers_dir)}", flush=True)
        print(
            "FAILED: "
            f"{len(missing)} paper(s) missing; not numbering or binding: "
            + ", ".join(missing),
            flush=True,
        )
        return papers_dir, failures if failures else len(missing)

    front = frontmatter.front_matter(book_id)
    if front is None:
        print(
            "no front matter has been set for this book (run frontmatter.py); "
            "numbering and binding the papers alone",
            flush=True,
        )
    unnumbered = number_pages(order, papers_dir, front)
    for line in unnumbered:
        print(line, flush=True)

    print("binding the papers into one book", flush=True)
    book_pdf, bound, pages = bind(book_id, order, papers_dir, front)
    ruled_pdf = rule_grid(book_pdf, papers_dir, front, profile) if bound else None

    if unnumbered:
        summary += f", {len(unnumbered)} left with the wrong page numbers"
    print(f"OK: {summary} under {display_path(papers_dir)}")
    if bound:
        matter = f" after {front.pages} page(s) of front matter" if front else ""
        print(
            f"    {bound} paper(s){matter}, {pages} page(s) in all, bound as "
            f"{display_path(book_pdf)}"
        )
    if ruled_pdf:
        print(f"    the same pages on the grid, as {display_path(ruled_pdf)}")
    return papers_dir, failures + len(unnumbered)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_id", help="Book ID, e.g. eg_mathematics_en_g7_t1")
    parser.add_argument(
        "-s", "--section", help="set this section, or every section under it, e.g. -s 1 or -s 01"
    )
    parser.add_argument(
        "--allow-missing-figures",
        action="store_true",
        help="set papers even when some figures are not drawn yet (prints descriptions)",
    )
    parser.add_argument(
        "--report-english-glosses",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "for Arabic books (default: on), set papers even when Latin/English "
            "etymology words remain in exercise text; write english_gloss_report.json"
        ),
    )
    parser.add_argument(
        "--section-only",
        action="store_true",
        help="set only -s and do not renumber or bind the production book",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="write section artifacts here instead of 12_section_papers",
    )
    locks = parser.add_mutually_exclusive_group()
    locks.add_argument(
        "--selection-from",
        type=Path,
        help="lock printed exercise membership and order to another book",
    )
    locks.add_argument(
        "--membership-from",
        type=Path,
        help="lock printed exercise membership but fit/order this book natively",
    )
    args = parser.parse_args()
    if args.section_only and not args.section:
        parser.error("--section-only requires --section")

    try:
        _, failures = set_papers(
            args.book_id,
            args.section,
            allow_missing_figures=args.allow_missing_figures,
            report_english_glosses=args.report_english_glosses,
            selection_from=args.selection_from,
            membership_from=args.membership_from,
            section_only=args.section_only,
            output_dir=args.output_dir,
        )
    except RuntimeError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
