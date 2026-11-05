#!/usr/bin/env python3
"""Write out every figure a book describes, one file each, for drawing from.

The stage between accepting the exercises and drawing them. Its job is to settle what
the book asks to be drawn: every description the accepted exercises hold, filed once
under the key a drawing is matched back by, with everything the page asks of that
drawing beside it. A figure can then be drawn from its own file and nothing else —
here or in another project, by another hand or another model — and the picture filed
back under the key it was drawn for.

Each figure is one file of nine fields, in a folder named for the type of drawing it is:

    10_book_figure_data/plane_geometry/d91eb08673c5.json

The nine are the same whatever the figure belongs to, so nothing reading them has to
know how a book is arranged. An empty `stem` is a question standing on its own; an
empty `text` is a figure that is the whole exercise, with the stem above it the only
wording there is; a figure shared by a set carries every question it serves, laid out
as the page prints them. The `key` is what a drawing is matched back by, so a picture
returned under that name can be filed against the figure it was drawn for.

The last three are what the book's arrangement asks of a drawing, and they are all it
asks. Two are the room the page has for it: draw to the narrow width, and where the
figure is too flat to be read that narrow — anything around two to one or flatter —
draw to the wide one instead and it will be set across the measure on its own. The
third names the figures printed beside it in one block, and is empty for a figure
printed on its own. A block is set at one width throughout, so a figure that goes wide
takes the rest with it; and the reader meets them side by side, so they are worth
drawing side by side, at one scale and by one set of conventions.

What is left out is as deliberate as what is kept. There is still no section number
and no set named, because a set is this project's bookkeeping: what matters to whoever
draws is the width it leaves and the company it keeps, and both are in the record
already.

Runs after questions_review.py, before draw.py.

    python scripts/extract.py eg_mathematics_en_g8_t1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    import src.latex as latex
    from draw import figure_key
    from questions_review import accepted_sections
    from sources import PROJECT_ROOT, OUTPUT_DIR
    from src.arabic_labels import rewrite_graph
except ModuleNotFoundError as error:  # pragma: no cover
    sys.exit(f"missing dependency ({error.name}): pip install -r requirements.txt")

# The figures belong to the book rather than to any one section, since a description
# shared by two sections is one drawing, filed once.
FIGURE_DATA_STAGE = "10_book_figure_data"

# Where a figure goes that was described before its type was asked for. Such a figure
# is a plain sentence with nothing saying what it shows, and guessing would file it
# under rules it was never judged by; it is written out all the same, since a figure
# left out of an account of every figure is the one thing this must not do.
UNSORTED = "unsorted"


def millimetres(points: float) -> str:
    """A width the page keeps in points, as the millimetres a figure is drawn to.

    Whole millimetres, because these are a limit to draw up to rather than a
    measurement of anything: the page's own widths come out on the millimetre as it is,
    and a figure a fifth of a point over its budget is a figure nobody can see is over.
    """
    return f"{round(points / latex.POINTS_PER_INCH * latex.MM_PER_INCH)}mm"


# The room the page has for a figure, which is the paper's own two widths said in the
# units a drawing is made in. Taken from latex.py rather than written down, so that a
# change to the margin, the gutter or the room the numbers stand in reaches whoever is
# drawing instead of quietly making their figures the wrong size.
#
# The narrow width is two widths, because how much of a line a figure gets depends on
# what it shares the line with. A figure of a standalone exercise has a whole grid
# column to itself. A figure of a set is set inside a cell of a grid laid two across,
# and that cell spends the number's step on its own number first, so it is that much
# narrower — and it has to be drawn to the narrower of the two, since a set is laid out
# by its widest member and one figure over the budget puts every member of the set on a
# line of its own.
#
# The pipeline itself keeps only the narrower, so that any drawing can be set either
# way. That is the right rule for a library of figures shared across a book and the
# wrong one for drawing a figure that is only ever printed in one place, which is what
# these files are for: a standalone figure given 74mm is a figure drawn 10mm smaller
# than the page was holding for it.
MAX_NARROW_ALONE = millimetres(latex.grid_column(2))
MAX_NARROW_IN_SET = millimetres(latex.grid_column(latex.SET_FIGURE_SPAN) - latex.NUMBER_ROOM)

# What a figure too flat for any column is drawn to: the measure less the column the
# numbers stand in, which is all the width there is under an exercise's wording.
MAX_WIDE = millimetres(latex.NUMBERED_WIDTH)


def joined(parts: list[str]) -> str:
    """Several pieces of wording as one, laid out as the page prints them."""
    return "\n\n".join(part for part in (piece.strip() for piece in parts) if part)


def records(group: dict, *, notation: str = "latin") -> list[tuple[str, dict]]:
    """Every figure this group asks for, each as the key it is filed under and its record.

    A figure hanging off a question carries that question. A figure on the group is
    shared by all of them — it is drawn to serve the set, so the set is the wording
    around it — and carries the lot. Where the group holds one question those are the
    same thing said twice, and the one question is what it carries either way.

    A figure described before its type was asked for is a bare sentence rather than an
    entry, and is taken as a description with no type.

    Where a figure hangs decides how wide it may be drawn, and who it is drawn beside.
    A figure on a question of a set is set in a cell beside its fellows and gets a
    cell's width, and it is those fellows it is drawn with; a figure on the stem stands
    across the measure above them all, so it has the room a standalone figure has and
    no company to keep, as does anything in a group holding one question.
    """
    questions = group.get("exercises") or []
    stem = (group.get("stem") or "").strip()
    in_a_set = len(questions) > 1

    # The block the page prints as one, in the order it prints it. A figure of it is
    # drawn with the others; a figure that is the only one asked of its set is drawn
    # with nobody, which falls out of taking itself back out below.
    block: list[str] = []
    if in_a_set:
        for question in questions:
            for described in latex.asked_figures(question):
                if (key := figure_key(described)) not in block:
                    block.append(key)

    places: list[tuple[list, list[dict], str, list[str]]] = []
    if shared := list(group.get("figures") or []):
        places.append((shared, questions, MAX_NARROW_ALONE, []))
    for question in questions:
        if figures := question.get("figures") or []:
            narrow = MAX_NARROW_IN_SET if in_a_set else MAX_NARROW_ALONE
            places.append((figures, [question], narrow, block))

    out: list[tuple[str, dict]] = []
    for figures, serves, narrow, beside in places:
        for figure in figures:
            described = figure["description"] if isinstance(figure, dict) else figure
            key = figure_key(described)
            row = {
                "key": key,
                "figure_type": (
                    figure.get("figure_type", "") if isinstance(figure, dict) else ""
                ),
                "stem": stem,
                "text": joined([question.get("text") or "" for question in serves]),
                "answer": joined([question.get("answer") or "" for question in serves]),
                "description": described,
                "max_narrow_width": narrow,
                "max_wide_width": MAX_WIDE,
                "drawn_with": [other for other in beside if other != key],
            }
            if isinstance(figure, dict):
                if row["figure_type"] == "grid" and isinstance(figure.get("grid"), dict):
                    row["grid"] = figure["grid"]
                if row["figure_type"] == "function graph" and isinstance(
                    figure.get("graph"), dict
                ):
                    graph = figure["graph"]
                    if notation == "arabic":
                        wording = joined(
                            [stem, row["text"], row["answer"], described]
                        )
                        graph = rewrite_graph(graph, wording)
                    row["graph"] = graph
                if row["figure_type"] == "strip diagram" and isinstance(
                    figure.get("strip"), dict
                ):
                    row["strip"] = figure["strip"]
            out.append((key, row))
    return out


def folder(figure_type: str) -> str:
    """The directory a type of drawing is filed in, as a name a shell can type."""
    return figure_type.replace(" ", "_") if figure_type else UNSORTED


def extract(book_id: str) -> Path:
    """Write out the book's figures, and say where they went and what they were.

    Files this wrote before and would not write now are removed, so that a run made
    after the questions were rewritten holds the figures the book asks for and not the
    ones it used to. Anything else in the folder is left alone: it is a folder meant to
    be worked in, and this owns only what it put there.

    A description asked for in two different sets is one drawing standing in two
    blocks, and it is drawn once, so what it is drawn with is every figure of both.
    That gives it a fellow or two it is never actually printed beside, which costs a
    drawing nothing — it is drawn to suit the company either way — where drawing it
    twice, once per block, would leave the library with two pictures under one name.
    """
    documents = accepted_sections(book_id, None)
    out_dir = OUTPUT_DIR / book_id / FIGURE_DATA_STAGE

    written: dict[Path, dict] = {}
    repeated = 0
    for _, document in documents:
        notation = str(document.get("notation") or "latin")
        for group in document["exercises"]["groups"]:
            for key, row in records(group, notation=notation):
                path = out_dir / folder(row["figure_type"]) / f"{key}.json"
                # A description written twice is one drawing, filed once, carrying the
                # first place the book asks it — which is the wording the drawing stage
                # keeps too. Either would do: the picture is the same. What it is drawn
                # with is the exception, and gathers rather than settles on the first,
                # since the drawing has to sit among all of them.
                if kept := written.get(path):
                    repeated += 1
                    kept["drawn_with"] += [
                        other
                        for other in row["drawn_with"]
                        if other != kept["key"] and other not in kept["drawn_with"]
                    ]
                    continue
                written[path] = row

    for path, row in written.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    stale = 0
    for path in sorted(out_dir.rglob("*.json")):
        if path not in written:
            path.unlink()
            stale += 1
    if out_dir.is_dir():
        for directory in sorted(out_dir.iterdir()):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()

    counts: dict[str, int] = {}
    for row in written.values():
        counts[folder(row["figure_type"])] = counts.get(folder(row["figure_type"]), 0) + 1

    print(
        f"OK: {len(written)} figure(s) of {len(documents)} section(s) "
        f"under {out_dir.relative_to(PROJECT_ROOT)}"
    )
    for figure_type in sorted(counts):
        print(f"  {figure_type:<28}{counts[figure_type]:>4}")
    if repeated:
        print(f"  ({repeated} description(s) asked more than once, filed once)")
    if stale:
        print(f"  ({stale} file(s) from an earlier run removed)")
    if UNSORTED in counts:
        print(
            f"  ({counts[UNSORTED]} figure(s) were described before the figure type was "
            "asked for, and have none)"
        )
    return out_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_id", help="the book whose figures to write out")
    args = parser.parse_args()
    try:
        extract(args.book_id)
    except (RuntimeError, OSError) as error:
        print(f"failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
