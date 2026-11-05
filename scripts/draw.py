#!/usr/bin/env python3
"""Draw every figure a book describes, one call each, and file the pictures.

Runs after extract.py, before papers.py. Each figure it is given is an exercise's worth
of words — "Isosceles triangle ABC with AB = AC = 13 cm and base BC = 10 cm" — written
by new_questions.py and listed by extract.py, and this stage asks for the TikZ that
draws it, compiles the picture on its own to see that it sets and to measure what it
came to, and files the picture for papers.py to print.

What it builds is a library, one file per figure keyed by a hash of the description, so
that two exercises asking for the same picture are one drawing and one call. A figure
already drawn is left alone: that is what keeps a second run of the stage from paying
for the book again, and --again is the only thing that overrides it, which is how a
change to the prompts reaches the figures drawn under the old one.

One call per figure, and then a compiled picture is reviewed for mathematical
fidelity — structure, lengths, angles, ratios, marks — not layout. That review
is the one judgement the stage makes about the drawing itself. A call that never
arrived is made again, which is the network being retried. A picture the engine
refuses outright is shown its own error and asked for again, up to --repair-rounds
times, since a figure that will not compile is not a figure at all. A picture that
prints one thing on top of another is shown where, and asked for again, up to
--clash-rounds times. And a picture the review finds mathematically wrong is drawn
again, up to --check-rounds times, with the problems named as JSON on the same
draw prompt. Contradictory givens, and a picture that is still mathematically
wrong after those redraws, are not a reason to drop the figure: they are filed
as they stand and listed in check.json.

The clash pass is measured rather than judged: each label is rendered
by itself on the same canvas as the drawing and the ink is intersected, so what
comes back is which marks actually fall on which, and where. It is the one fault
a compiler cannot see — a figure can set perfectly and still print a length across
the line it measures. A collision in a drawing that has not used its column is
grown to the column first, by scaling the geometry and leaving the type: the
column was never the constraint, and a wider measure would only add empty paper.
What still collides after that is asked about as a placement. A leftover word
on a marked point is asked once more as a pin — `\wordpin`, the type off the
ink and a pointer at the point, the same last resort as `\lengthmark[][arrows]`.
A leftover overlap is not a reason to take the figure out of the column.

Then the run looks at the shape of what it drew. A figure whose ink, cropped every
way, comes out --wide-ratio times wider than it is tall has been flattened by its
column, and one that comes out that many times taller than it is wide has been
stacked by it: the labels are set at the size of the wording whatever the picture
does, so the geometry is what gives way. That figure is drawn again to
max_wide_width. Only that figure. The other parts of its exercise stay at the
column they were drawn to, and the paper sets the set one picture to a line.
A few types are drawn to the wide measure from the
first call instead, because the answer is known before anything is drawn: a
number line is one long rule and everything on it is spaced along that single
dimension, so the measure is the whole of the room it has, whether the line
stands alone or is one of a set.

How a figure is set — the type, the two line weights, the one colour, and the styles a
drawing names its parts by — is not here at all. It is read from latex.py, which is what
sets the paper, because every style a drawing is told to use has to be defined both
where the figure is compiled to be looked at and where the paper compiles it for real.
A style defined in one place and not the other is a figure that draws perfectly here and
takes the whole section paper down when papers.py sets it.

Everything the model is sent lives under prompts/draw/, a file to the step
that sends it: core.md and its parts, repair.md, relabel.md, pin.md, widen.md,
check.md.
The ask itself is in three sorts of piece. core.md holds what is true of every
figure the paper prints — the width, the weights, the type, the marks it must not add.
language/, numerals/ and notation/ hold what is true of every figure in a book
written a certain way — the language its words are set in, the digits its numbers
are printed in, the letters its points are named with — and prompting.py drops
the book's three in. figure-types/<figure-type>.md holds what is true of one type
of figure, the conventions a histogram is drawn by or a triangle is. All are
folded into core.md before anything is called, so a convention can be written or
corrected for one type of figure, or for one kind of book, without touching the
rules that hold for all of them.

Reads data/output/<book_id>/10_book_figure_data/<figure-type>/*.json, a type at a time
because the prompt is the type's, and writes data/output/<book_id>/11_book_figures/:
<key>.json holding the picture and the size it came out at, <key>.png to look at it by,
clash/<key>.png for a figure that prints on top of itself, which is the figure as it
prints with what collides picked out in red, and check.json for figures whose stated
givens cannot coexist or whose drawing is still mathematically wrong after redraws.
No .tex and no .pdf: those are how a picture gets measured, not anything to keep, so they
are made somewhere temporary and go with it. A picture that has to be compiled again is
compiled from the source, which is the same two seconds of the engine and cannot be a
stale file pretending to be a fresh one.

Every call is logged under data/output/<book_id>/00_api_call_logs/, beside every other
stage's, so that what a book cost is one folder to add up. Each sort of ask is its own
step — figure_drawn, figure_checked, figure_repaired, figure_relabelled,
figure_pinned, figure_widened — so what a figure cost and how many tries of
which sort it took are read off the same place.

    python3 scripts/draw.py eg_mathematics_en_g8_t1
    python3 scripts/draw.py eg_mathematics_en_g8_t1 -t histogram -n 5
    python3 scripts/draw.py eg_mathematics_en_g8_t1 --key 04d5b2ec5149
    python3 scripts/draw.py eg_mathematics_en_g8_t1 --again      # under a changed prompt
    python3 scripts/draw.py eg_mathematics_en_g8_t1 --rebuild    # compile again, no calls
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    from google import genai
    from google.genai import types
    from pydantic import BaseModel, Field
except ModuleNotFoundError as error:  # pragma: no cover
    sys.exit(f"missing dependency ({error.name}): pip install -r requirements.txt")

# Beside this file rather than installed, so their absence is a broken checkout and
# not something pip is going to fix.
from src.api_logger import (  # noqa: E402
    ApiLogger,
    genai_answer_text,
    genai_response_files,
    genai_usage,
    safe_log,
)

# How a figure is set is the paper's business and not this stage's, so all of it is
# read from the module that sets the paper rather than written down again here. That is
# not tidiness: every style a drawing is told to use — `gridline`, `histobar`, the one
# blue — has to be defined both where the figure is compiled to be looked at and where
# the paper compiles it for real, and a style defined in one place and not the other is
# a figure that draws perfectly in review and takes the whole section paper down with
# `I do not know the key '/tikz/gridline'`. One definition, read twice, cannot drift.
import src.latex as latex  # noqa: E402
from src.latex import (  # noqa: E402
    BODY_SIZE,
    FIGURE_BORDER,
    LATEX_ENGINE,
    drawing_preamble,
    drawing_tex,
    standard_lines,
)

# Where the pipeline keeps a book's work, named in one place for every stage of it.
from src.prompting import Profile, assemble, stage_dir  # noqa: E402
from sources import PROJECT_ROOT, OUTPUT_DIR, book_metadata  # noqa: E402
from specs import since  # noqa: E402

from figures import function_graph  # noqa: E402
from figures import grid  # noqa: E402
from figures import strip_diagram  # noqa: E402

# The figures a book describes, filed by type, which is what extract.py leaves. This
# stage reads that folder and nothing else about the book: a figure's record carries the
# description, the wording around it, the answer, the two measures the page has for it
# and the figures it is printed beside, which is everything a drawing has to be made from.
FIGURE_DATA_STAGE = "10_book_figure_data"

# The drawings, which belong to the book rather than to any one section, since a
# description shared by two sections is one drawing.
FIGURES_STAGE = "11_book_figures"

# Where a figure's collisions are pictured, under the drawings rather than beside them,
# so that the stage folder itself holds one file per figure and nothing else. A figure
# with nothing wrong with it has no file here at all.
CLASH_FOLDER = "clash"
# Everything the model is sent lives under prompts/draw/ and nothing else
# does. The files are markdown and read like prose, which is exactly why they need
# a folder saying what they are: a .md beside a .py looks like documentation of
# the program, and these are input to it. Editing one to improve the writing
# changes what gets drawn, and the folder is the warning.
PROMPT_STAGE = "draw"
PROMPTS_DIR = stage_dir(PROMPT_STAGE)

# The drawing prompt is assembled by prompting.py from core.md and the parts the
# book's language, numerals and notation choose, like every other stage's. The
# three that follow it are one prompt to the step that sends it, so that a folder
# of logs and a folder of prompts can be read against each other: core.md and its
# parts are what figure_drawn was asked, check.md what figure_checked was asked,
# relabel.md what figure_relabelled was asked, and so on down.

# What is true of every figure the paper prints lives in core.md; what is true of
# one type of figure lives beside it, a file to a type, and is dropped into the
# {figure_type} slot in core.md when the run is set up. A pie chart and a triangle share
# the width, the weights and the type, and share nothing about how they are drawn,
# so the two sorts of rule are kept in two sorts of file: a convention for
# histograms can then be written, or corrected, without reading past the rules
# that hold for everything.
FIGURE_TYPES_DIR = PROMPTS_DIR / "figure-types"
FIGURE_TYPE_SLOT = "{figure_type}"

REPAIR_PROMPT_PATH = PROMPTS_DIR / "repair.md"
# Named for what it asks for and not for what went wrong, like the three beside
# it. The measuring that finds the collision is the clash; this is the ask that
# moves the labels, and the step it belongs to is figure_relabelled.
RELABEL_PROMPT_PATH = PROMPTS_DIR / "relabel.md"
PIN_PROMPT_PATH = PROMPTS_DIR / "pin.md"
WIDEN_PROMPT_PATH = PROMPTS_DIR / "widen.md"
# Mathematical fidelity after a picture compiles: structure, lengths, angles,
# ratios, marks. Layout is a later pass. A redraw after this review is not a
# new prompt: it is core.md again, with the problems named as JSON.
CHECK_PROMPT_PATH = PROMPTS_DIR / "check.md"

# Which stage of the pipeline made the call, named here rather than at the call
# site so that the logs and the code cannot drift apart.
DRAW_STEP = "figure_drawn"
CHECK_STEP = "figure_checked"
REPAIR_STEP = "figure_repaired"
CLASH_STEP = "figure_relabelled"
PIN_STEP = "figure_pinned"
WIDEN_STEP = "figure_widened"

# Figures the mathematical review would not accept, listed beside the drawings
# rather than mixed into them. A row in the library still holds the picture; this
# file is the report of which ones came back redraw or invalid_source.
CHECK_REPORT = "check.json"
# The name the report had when it listed only contradictory givens. Written so a
# later run can read it once, copy those rows into check.json, and take it away.
OLD_INVALID_SOURCE_REPORT = "invalid_source.json"

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_THINKING = "high"
THINKING_LEVELS = ("minimal", "low", "medium", "high")
API_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

# The calls wait on the network and the compiles do not, so every figure gets a
# thread and the engine is held to the cores that can actually run it.
MAX_WORKERS = 16
LATEX_LANES = max(2, (os.cpu_count() or 4) - 1)
_engine = threading.BoundedSemaphore(LATEX_LANES)

# A call that never arrived is made again; a call that arrived and drew the wrong
# picture is not. Only the first of those is a network to retry.
TRANSPORT_TRIES = 3

# A picture that will not compile is shown its own error and asked again. This is
# the one judgement the stage makes about a reply it received, and it is a narrow
# one: the engine has refused the figure outright, so there is nothing to look at
# and nothing to weigh up. Two rounds, because the first fixes almost everything
# it is going to fix and the third is paying for the model to repeat itself.
REPAIR_ROUNDS = 2
LOG_ERRORS = 3
LOG_CONTEXT = 8

# A figure that compiles can still print a length across the line it measures, and
# nothing in the compile notices. Only an actual overlap is asked about again: a
# label a point from what it names is how textbooks are set, and a run that argued
# about those would argue about nearly every figure in it.
#
# Three rounds, and more than the compile loop gets, because the two are answering
# different questions. A picture that will not compile has one fault to find, and a
# model that has not found it in two goes is repeating itself. A label with nowhere
# good to go is a search: the crowded points are exactly the ones where the first
# placement tried is unlikely to be the one that works, and each round is measured
# rather than guessed at, so a round that does not help is known to have not helped.
CLASH_ROUNDS = 3

# A compiled figure can still be the wrong figure: a side labelled 3x drawn
# shorter than one labelled 2x, a midpoint that is not a midpoint. That is
# judged after the picture compiles, and only then. Two rounds, like the
# compile loop: the first names what is wrong and the second is a second
# chance; a third is the model repeating itself.
CHECK_ROUNDS = 2

PNG_DPI = 200

# How much of a picture's bounding box is not the picture. Every label is a node,
# and a node reserves `inner sep` — a third of an em, which is 4pt at the paper's
# 12pt — outside the type on all four sides, over and above the ascent and descent
# the font itself claims above and below the letter. The topmost and bottommost
# things in a figure are almost always labels, so a picture asked for at 84mm
# arrives about 8pt taller than anything drawn in it, and that is the space that
# would otherwise be printed above and below every figure in the book.
#
# It cannot be taken off by setting `inner sep=0`, which is the room that keeps a
# label off the line it names. It is taken off the bounding box instead, once the
# picture has been compiled and its ink measured: see tight_box.
INK_BBOX = re.compile(
    r"%%HiResBoundingBox:\s*([\d.-]+)\s+([\d.-]+)\s+([\d.-]+)\s+([\d.-]+)"
)
PAGE_SIZE = re.compile(r"Page size:\s+([\d.]+) x ([\d.]+)")
# Under this a picture is already tight and is left as it came, rather than
# carrying a statement that takes nothing off it.
TRIM_LEAST = 0.1
# What this stage appends to a picture to bring its box down, marked so that it can
# be found and taken off again before the picture is measured a second time.
TRIM_MARK = "% bounding box brought down to the ink above and below"
# The crop itself, not only the comment. A relabel may copy this TeX back without
# TRIM_MARK; if untrimmed() then misses it, the next tight_box stacks a second crop
# and the official height collapses.
TRIM_BLOCK = re.compile(
    r"(?:" + re.escape(TRIM_MARK) + r"\s*)?"
    r"\\coordinate\s*\(inksouthwest\)\s*at\s*\(current bounding box\.south west\);\s*"
    r"\\coordinate\s*\(inknortheast\)\s*at\s*\(current bounding box\.north east\);\s*"
    r"\\pgfresetboundingbox\s*"
    r"\\useasboundingbox\s*\(\[shift=\{[^]]+\]inksouthwest\)\s*"
    r"rectangle\s*\(\[shift=\{[^]]+\]inknortheast\);\s*",
)

# What the model is told about the figure, in the order it reads them. The
# answer is there so a length or angle it states can place the figure to scale;
# the prompt forbids printing it. The width is left out and named in the prompt
# instead, so that the one number the drawing has to hold is stated once.
FIGURE_FIELDS = ("description", "stem", "text", "answer")

# Which of the input's two widths a figure is drawn to. Every figure is drawn to
# the narrow one first — the column beside the wording — and a figure that comes
# out too squat to read there is drawn again to the wide one, which is the whole
# measure of the page.
WIDTH_FIELD = "max_narrow_width"
WIDE_WIDTH_FIELD = "max_wide_width"
DEFAULT_WIDTH = "84mm"
DEFAULT_WIDE_WIDTH = "162mm"

# Which figures are set beside each other as the parts of one exercise, and so
# have to look like each other. Named in the input because it is an editorial
# decision and not something the wording gives away: figures can share a stem
# without being a set, and a set is only a set because the page prints it as one.
GROUP_FIELD = "drawn_with"

# A figure at least this many times wider than it is tall is drawn again to the
# wide measure. Squat is the problem the number is standing in for: the labels are
# set at the size of the wording whatever the picture does, so a figure the column
# has flattened has less and less room for the geometry between them, and the way
# out is not smaller type but a wider measure. Taken on the ink cropped every way,
# since what is being asked is the shape of the drawing and not of its page.
WIDE_RATIO = 2.5

# What that means for the types it does not mean the usual thing for. The 2.5 is
# a guess about a figure that has been squeezed, and it only holds where a figure
# drawn well would come out roughly as tall as it is wide. A cuboid drawn
# obliquely does not: the near face is wider than it is high before the depth is
# added, and the depth pushes it further, so a perfectly drawn box lands at about
# 2:1 with nothing wrong with it. Measured against the usual number, one of two
# cuboids crossed it and the other did not, and the pair were printed at two
# different measures for no reason a reader could see.
#
# `other` is here for the opposite reason: not because its figures have a shape
# in common but because they have none, so there is no shape the usual number is
# a guess about. A scissors diagram is four terms in a wide flat cross and a Venn
# diagram is a rectangle, and the only safe reading of 2:1 across a folder like
# that is that it means nothing. Held at 4:1, it catches a figure that is plainly
# wrong and leaves the rest alone.
WIDE_RATIO_BY_FIGURE_TYPE = {
    "other": 4.0,
    "solid_geometry": 4.0,
    "strip_diagram": 6.0,
}

# A count-and-shade lattice is drawn from integers, not asked of the model.
DETERMINISTIC_TYPES = frozenset({"grid"})

# Function graphs and strip diagrams are plotted from structured fields.
LOCAL_DRAWERS = {
    "function_graph": function_graph,
    "strip_diagram": strip_diagram,
}

# How much of its column a figure has to be filling before being flat counts as
# being flattened. Below this it is a small drawing with room to spare on both
# sides, and whatever is wrong with it, the column is not the cause.
FULL_MEASURE = 0.9

# The types drawn to the wide measure from the first call rather than the narrow
# one. Widening is normally a second thought — draw it in the column, see whether
# the column ruined it — but for some types the answer is known before anything is
# drawn. A number line is one long horizontal rule, and everything on it is spaced
# along that one dimension: the room between two ticks, and so the room for the
# numbers under them and for two points marked between them, is the measure
# divided by however many ticks there are. Twice the measure is twice the room for
# all of it, and there is no second dimension for the figure to use instead.
#
# Drawing them wide from the start rather than widening them afterwards saves the
# call that would draw each of them badly first, and keeps the shape test out of
# it: a number line is fifteen times wider than it is tall when it is perfect, so
# it trips that test at any measure and would ask to be widened for ever. One that
# belongs to a set is drawn to the same measure. The paper then gives that set one
# line each, which is the room the line was drawn for.
BORN_WIDE = {"number_line"}

# The picture, cut out of whatever the reply wrapped it in.
PICTURE = re.compile(r"\\begin\{tikzpicture\}.*\\end\{tikzpicture\}", re.DOTALL)

# A reply that escaped its newlines twice, so that they arrived as the two
# characters backslash and n rather than as newlines. Undone wherever it appears,
# which is safe: TeX reads the longest run of letters after a backslash, so \node
# is \node and never \n followed by "ode", and a backslash-n with a non-letter
# after it is a control sequence no TeX document has ever had.
ESCAPED_NEWLINE = re.compile(r"\\n(?![A-Za-z])")


class DrawReply(BaseModel):
    """The TikZ that draws one figure, or a refusal to draw it."""

    status: Literal["ok", "abort"]
    abort_reason: str = Field(
        default="", description="Why the figure cannot be drawn; empty when ok"
    )
    tikz: str = Field(default="", description="One tikzpicture environment and nothing else")


class ReviewReply(BaseModel):
    """Whether a compiled figure matches the givens, and what to do if it does not."""

    verdict: Literal["ok", "redraw", "invalid_source"]
    problems: list[str] = Field(
        default_factory=list,
        description="What must change, or why the givens cannot coexist; empty when ok",
    )


@dataclass(frozen=True)
class Run:
    """What every figure of a run is drawn against.

    Held together rather than passed one argument at a time, because everything
    in it is settled once at the top of a run and every figure wants all of it.
    """

    client: genai.Client | None
    logger: ApiLogger | None
    figure_type: str
    model: str
    thinking: str
    prompt: str
    # The files the drawing prompt was assembled from, for the logs: the core, the
    # book's language, numerals and notation parts, and the type's own rules.
    prompt_files: tuple[str, ...]
    repair_prompt: str
    relabel_prompt: str
    pin_prompt: str
    wide_prompt: str
    check_prompt: str
    rounds: int
    clash_rounds: int
    check_rounds: int
    wide_ratio: float


@dataclass(frozen=True)
class Folders:
    """Where a book's drawings go, and how the book they are for is set.

    The library is one file per figure and no folder of documents, because a .tex and
    a .pdf are how a picture gets measured rather than anything to keep. Both are made
    somewhere temporary and thrown away, and what is left behind is the picture as
    source, as an image, and as a row saying how big it came out.

    The profile rides along because every compile here has to match the paper the
    picture will stand in: a label in Arabic is set in the Arabic face and reordered
    right to left, or it is a row of missing glyphs measured at the wrong width.
    """

    root: Path
    clash: Path
    profile: Profile

    @classmethod
    def under(cls, book_id: str) -> Folders:
        root = OUTPUT_DIR / book_id / FIGURES_STAGE
        made = cls(root, root / CLASH_FOLDER, Profile.of(book_metadata(book_id)))
        made.root.mkdir(parents=True, exist_ok=True)
        made.clash.mkdir(parents=True, exist_ok=True)
        return made

    def row(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def png(self, key: str) -> Path:
        return self.root / f"{key}.png"

    def clash_png(self, key: str) -> Path:
        return self.clash / f"{key}.png"

    def check_report(self) -> Path:
        return self.root / CHECK_REPORT


@dataclass
class Result:
    """What became of one figure: what came back, and what compiling it did."""

    figure: dict
    tikz: str = ""
    status: str = ""
    detail: str = ""
    png: Path | None = None
    seconds: float = 0.0
    repairs: int = 0
    relabels: int = 0
    pins: int = 0
    checks: int = 0
    report: str = ""
    clash: Report | None = None
    marked: Path | None = None
    wide: bool = False
    invalid_source: bool = False
    check_problems: list[str] = field(default_factory=list)
    ink: tuple[float, float] | None = None
    # The page the picture compiled to, in the paper's own points. This is what the
    # paper holds room for, and it is not the ink: the box is brought down to the ink
    # above and below only, so a narrow drawing in a wide picture keeps the room its
    # own coordinates asked for either side. Measured while the PDF is still there to
    # measure, since nothing keeps it afterwards.
    size: tuple[float, float] | None = None
    compiled: bool = False
    # Whether this figure has been asked as far as it is going to be asked. A figure
    # that has been through the compile rounds, the layout rounds and the widening is
    # settled whatever state it ended in, and the next run leaves it alone: a collision
    # nobody could place is not a reason to pay for the same three rounds every time.
    settled: bool = False
    # Whether this run put the figure through the engine at all. A run skips the figures
    # it has already drawn, and reads them back only so that a set can be measured
    # whole; those are not results of this run and nothing is written for them. Without
    # this, a skipped figure looks exactly like a figure that failed to compile — no
    # picture, nothing measured — and filing it would delete the drawing it already has.
    touched: bool = False

    @property
    def width(self) -> str:
        """The measure this figure was actually drawn to."""
        return width_of(self.figure, self.wide)

    @property
    def ratio(self) -> float:
        """How many times wider than tall the drawing came out."""
        if not self.ink or not self.ink[1]:
            return 0.0
        return self.ink[0] / self.ink[1]

    @property
    def key(self) -> str:
        return self.figure.get("key", "?")

    @property
    def figure_type(self) -> str:
        return str(self.figure.get("figure_type", ""))

    @property
    def drawn(self) -> bool:
        return self.compiled


def figure_key(description: str) -> str:
    """The name a description is filed under, ignoring how its spaces fell.

    Here rather than with whoever writes the descriptions, because it is the identity of
    a drawing: the stage that lists the figures a book wants and the stage that draws
    them have to agree what one figure is, and a hash of the wording is what makes two
    exercises asking for the same picture one figure and one call.
    """
    collapsed = " ".join(description.split())
    return hashlib.sha256(collapsed.encode("utf-8")).hexdigest()[:12]


def figure_files(book_id: str, figure_type: str) -> list[Path]:
    """Every figure of one type a book describes, in a settled order.

    Sorted rather than left to the filesystem, so that what a run draws first does not
    depend on the machine it is run on.
    """
    folder = OUTPUT_DIR / book_id / FIGURE_DATA_STAGE / figure_type
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.json"))


def figure_types_of(book_id: str) -> list[str]:
    """Every type of figure this book actually asks for, as extract.py filed them.

    Read off the folders rather than from the list the writing stage chooses from, so
    that a book asking for no pie chart is not reported as a pie chart folder with
    nothing in it, and a type added to that list later needs nothing changed here.
    """
    root = OUTPUT_DIR / book_id / FIGURE_DATA_STAGE
    if not root.is_dir():
        return []
    return sorted(path.name for path in root.iterdir() if path.is_dir())


def figures_from(paths: list[Path], figure_type: str = "") -> list[dict]:
    """Read the figures, dropping any file that is not one.

    The type is taken from the folder the record was filed in rather than from the
    record itself, because the folder is what chose the prompt the figure is drawn
    against and the two must be the same answer.
    """
    figures = []
    for path in paths:
        try:
            figure = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(f"  skipping {path.name}: {error}", flush=True)
            continue
        if not str(figure.get("description", "")).strip():
            print(f"  skipping {path.name}: no description to draw", flush=True)
            continue
        figure.setdefault("key", path.stem)
        if figure_type:
            figure["figure_type"] = figure_type
        figures.append(figure)
    return figures


def born_wide(figure_type: str, figure: dict | None = None) -> bool:
    """Whether this figure is drawn to the wide measure from the first call.

    A number line is one of these wherever it is printed, including one that
    belongs to a set.
    """
    del figure
    return figure_type.replace(" ", "_") in BORN_WIDE


def is_local(figure_type: str) -> bool:
    """Whether this type is plotted here, not asked of the model."""
    return figure_type.replace(" ", "_") in LOCAL_DRAWERS


def local_drawer(figure_type: str):
    """The module that plots this type from its structured field."""
    name = figure_type.replace(" ", "_")
    drawer = LOCAL_DRAWERS.get(name)
    if drawer is None:
        raise RuntimeError(f"{figure_type} is not drawn locally")
    return drawer


def plotted_without_model(figure_type: str) -> bool:
    """Whether the picture is built from numbers, not from a model call."""
    return figure_type in DETERMINISTIC_TYPES or is_local(figure_type)


def wide_ratio_for(figure_type: str) -> float:
    """The shape a figure of this type has to reach before it is drawn again wider."""
    return WIDE_RATIO_BY_FIGURE_TYPE.get(figure_type, WIDE_RATIO)


def width_of(figure: dict, wide: bool = False) -> str:
    """The measure one figure is drawn to, as the input writes it."""
    if wide:
        return str(figure.get(WIDE_WIDTH_FIELD) or DEFAULT_WIDE_WIDTH).strip()
    return str(figure.get(WIDTH_FIELD) or DEFAULT_WIDTH).strip()


def millimetres(width: str) -> float:
    """A width as the input writes it, as a number to compare and to sort on."""
    found = re.match(r"\s*([\d.]+)\s*mm", str(width))
    return float(found.group(1)) if found else 0.0


def widths_in(figures: list[dict]) -> list[str]:
    """The columns a set of figures is drawn to, widest first."""
    return sorted({width_of(figure) for figure in figures}, reverse=True)


def group_of(figure: dict) -> list[str]:
    """The other figures this one is set beside, if the input names any."""
    named = figure.get(GROUP_FIELD) or []
    return [str(key) for key in named if str(key) != str(figure.get("key"))]


def grouped(figures: list[dict]) -> dict[str, set[str]]:
    """Every figure's set, itself included, keyed by figure.

    Read as a whole rather than a figure at a time because the sets have to close:
    named in one direction has to be the same set named in the other, or a set
    only half named would be half a page.
    """
    sets: dict[str, set[str]] = {}
    for figure in figures:
        key = str(figure["key"])
        sets.setdefault(key, {key}).update(group_of(figure))
    # Named in one direction is named in both, so that a set is the same set
    # whichever of its figures is asked about it.
    for key, others in list(sets.items()):
        for other in others:
            sets.setdefault(other, {other}).add(key)
    settled = {}
    for key in sets:
        whole: set[str] = set()
        pending = [key]
        while pending:
            at = pending.pop()
            if at in whole:
                continue
            whole.add(at)
            pending += list(sets.get(at, {at}))
        settled[key] = whole
    return settled


def chosen(
    figures: list[dict], count: int, keys: list[str] | None, widths: list[str] | None
) -> list[dict]:
    """The figures this run draws: the ones named, or `count` of them at random.

    Where widths are named, `count` is taken from each of them rather than from
    the input as a whole, because a run made to be looked at a column at a time
    wants the columns evenly filled and the input is not evenly split between
    them.
    """
    by_key = {figure["key"]: figure for figure in figures}
    if keys:
        if missing := sorted(set(keys) - set(by_key)):
            raise RuntimeError(f"no figure is keyed {', '.join(missing)}")
        return [by_key[key] for key in keys]
    if not widths:
        return whole_groups(random.sample(figures, min(count, len(figures))), by_key)

    picked: list[dict] = []
    for width in widths:
        pool = [figure for figure in figures if width_of(figure) == width]
        if not pool:
            raise RuntimeError(
                f"no figure is drawn to {width}; the input has "
                f"{', '.join(widths_in(figures))}"
            )
        if len(pool) < count:
            print(f"  only {len(pool)} figure(s) at {width}, not {count}", flush=True)
        picked += random.sample(pool, min(count, len(pool)))
    return whole_groups(picked, by_key)


def whole_groups(picked: list[dict], by_key: dict[str, dict]) -> list[dict]:
    """The figures picked, with the rest of anybody's set brought in beside them.

    A sampled run draws a set whole. The parts of one exercise are asked in one
    run, so a sheet made to be looked at is not half a set. A run that names
    keys draws those keys: widening one flat figure no longer redraws the others.
    """
    sets = grouped(list(by_key.values()))
    wanted: list[str] = []
    for figure in picked:
        for key in sorted(sets.get(str(figure["key"]), {str(figure["key"])})):
            if key in by_key and key not in wanted:
                wanted.append(key)
    if len(wanted) > len(picked):
        joined = len(wanted) - len(picked)
        print(f"  {joined} more figure(s) drawn to keep their sets whole", flush=True)
    return [by_key[key] for key in wanted]


def build_prompt(template: str, figure: dict, wide: bool = False) -> str:
    """The prompt as one figure is asked it, with the width it must hold.

    The width is the one thing about the prompt that changes after a figure has
    been drawn once, so every later ask about the same figure — the repair, the
    relabel — has to be told which measure that figure ended up at, or it would
    quietly hold a widened picture back to the column it outgrew.

    Substituted by hand rather than with str.format, because the prompt is full of
    braces that TeX wants and format would read every one of them as a field.
    """
    return template.replace("{max_width}", width_of(figure, wide))


def figure_part(figure: dict) -> str:
    """The figure as the model is given it: the description, the wording, and the answer."""
    given = {name: figure.get(name, "") for name in FIGURE_FIELDS if figure.get(name)}
    body = json.dumps(given, ensure_ascii=False, indent=2)
    return f"Figure JSON:\n{body}"


def problems_part(problems: list[str], tikz: str) -> str:
    """The review's problems and the picture they name, as JSON for a second draw."""
    body = json.dumps(
        {"problems": problems, "tikz": tikz.strip()},
        ensure_ascii=False,
        indent=2,
    )
    return f"Problems JSON:\n{body}"


def relabel_part(template: str, tikz: str, report: Report) -> str:
    """The picture and what it collides with, as the model is shown them."""
    return template.replace("{tikz}", tikz.strip()).replace("{clashes}", report.wording())


def repair_part(template: str, tikz: str, report: str) -> str:
    """The failed picture and the engine's complaint, as the model is shown them.

    Substituted by hand for the same reason the prompt is: both halves of this are
    TeX, and str.format would read every brace in them as a field of its own.
    """
    said = report.strip() or "The engine gave no error it could name."
    return template.replace("{tikz}", tikz.strip()).replace("{error}", said)


def picture_only(tikz: str) -> str:
    """Cut a reply back to the picture itself.

    The model occasionally wraps the picture in a document, trails it with a
    sentence of explanation, or escapes its newlines a second time on the way out
    so that every line of the picture arrives run onto the last. All three are
    mechanical to put right, and putting them right is not a redraw: it is reading
    the reply.
    """
    tikz = ESCAPED_NEWLINE.sub("\n", tikz)
    match = PICTURE.search(tikz)
    return match.group(0) if match else tikz.strip()


def pdf_size(pdf_path: Path) -> tuple[float, float]:
    """How big a compiled page is, in PDF points."""
    found = subprocess.run(
        ["pdfinfo", pdf_path.name], cwd=pdf_path.parent, capture_output=True, text=True
    )
    match = PAGE_SIZE.search(found.stdout)
    return (float(match.group(1)), float(match.group(2))) if match else (0.0, 0.0)


def page_size(pdf_path: Path) -> tuple[float, float]:
    """How big a compiled figure is, in the points the paper measures in.

    A PDF is measured in points of 1/72 of an inch and the paper in TeX's own of
    1/72.27, so the page is converted here rather than wherever it is read: this is the
    number the page fitter holds room by, and a figure reported 0.4% small would be
    reported small on every page it appears on.
    """
    width, height = pdf_size(pdf_path)
    return width * latex.POINTS_PER_BIG, height * latex.POINTS_PER_BIG


def ink_box(pdf_path: Path) -> tuple[float, float, float, float] | None:
    """Where the ink actually is on a compiled figure, in PDF points.

    Read off the drawing rather than reasoned about, because what stands above the
    topmost label is the font's business and not something the source can be asked.
    """
    if not shutil.which("gs"):
        return None
    found = subprocess.run(
        ["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=bbox", pdf_path.name],
        cwd=pdf_path.parent,
        capture_output=True,
        text=True,
    )
    match = INK_BBOX.search(found.stderr)
    return tuple(float(number) for number in match.groups()) if match else None


def untrimmed(tikz: str) -> str:
    """The picture as it was drawn, with any bounding box this stage set removed.

    So that measuring and trimming can be done again — on a run compiled a second
    time, above all — without each pass reading the box the pass before it set and
    trimming what has already been trimmed.
    """
    return TRIM_BLOCK.sub("", tikz)


def tight_box(tikz: str, top: float, bottom: float) -> str:
    """The same picture, its bounding box brought down to the ink above and below.

    The corners are taken as coordinates before the box is reset, because resetting
    it is what makes `current bounding box` unavailable, and the trim is then a
    shift in points off those corners — absolute, so it does not matter what the
    picture scaled its own coordinates to.

    Only up and down. What stands to the left and right of a figure is mostly the
    figure being narrower than the column it was drawn for, which is the column's
    business rather than the picture's, and the labels at the ends of a shape are
    the ones whose room is worth keeping.
    """
    body = untrimmed(tikz).rstrip()
    closing = "\\end{tikzpicture}"
    if not body.endswith(closing):
        return tikz
    return (
        body[: -len(closing)].rstrip()
        + f"\n{TRIM_MARK}\n"
        "\\coordinate (inksouthwest) at (current bounding box.south west);\n"
        "\\coordinate (inknortheast) at (current bounding box.north east);\n"
        "\\pgfresetboundingbox\n"
        f"\\useasboundingbox ([shift={{(0pt,{bottom:.3f}pt)}}]inksouthwest) rectangle "
        f"([shift={{(0pt,{-top:.3f}pt)}}]inknortheast);\n"
        + closing
    )


class CompileError(RuntimeError):
    """A document the engine would not set, and what it said about it.

    Carries two readings of the same failure: a line for the run summary, and as
    much of the log as is worth showing the model that has to put it right.
    """

    def __init__(self, summary: str, report: str = "") -> None:
        super().__init__(summary)
        self.summary = summary
        self.report = report


def latex_errors(log_path: Path) -> tuple[str, str]:
    """The first line of a LaTeX failure, and the passage around every one of them.

    LaTeX writes an error as a `!` line, then the macro it was inside, then `l.<n>`
    and the source broken at the character it stopped on. That break is the whole
    diagnosis, so the lines after the `!` are worth as much as the `!` itself and
    are kept with it.
    """
    if not log_path.is_file():
        return "", ""
    lines = log_path.read_text(errors="replace").splitlines()
    first = ""
    passages = []
    for number, line in enumerate(lines):
        # `!  ==> Fatal error occurred` is the engine giving up, not a fault to
        # read; the faults themselves are above it and are what is wanted here.
        if not line.startswith("!") or line.lstrip("! ").startswith("==>"):
            continue
        if not first:
            first = line
        if len(passages) < LOG_ERRORS:
            passages.append("\n".join(lines[number : number + LOG_CONTEXT]))
    return first, "\n\n".join(passages)


def compiled(tex_path: Path, into: Path | None = None) -> Path:
    """Run the engine on one document, returning its PDF or saying why there is none.

    The PDF is cleared first, because LaTeX still writes one for a document it
    reported errors on and a stale file must never look like a fresh success. It
    is made beside the document either way and moved to `into` afterwards, since
    the engine writes where it is run and running it elsewhere would only put the
    log and the aux somewhere else too.
    """
    pdf_path = tex_path.with_suffix(".pdf")
    log_path = tex_path.with_suffix(".log")
    pdf_path.unlink(missing_ok=True)

    with _engine:
        result = subprocess.run(
            [LATEX_ENGINE, "-interaction=nonstopmode", tex_path.name],
            cwd=tex_path.parent,
            capture_output=True,
            text=True,
        )

    if result.returncode != 0 or not pdf_path.is_file():
        pdf_path.unlink(missing_ok=True)
        first, report = latex_errors(log_path)
        # The aux is noise; the log is the evidence, and it stays until the next
        # compile of this figure overwrites it or a later one succeeds.
        tex_path.with_suffix(".aux").unlink(missing_ok=True)
        raise CompileError(f"does not compile{f': {first}' if first else ''}", report)

    log_text = log_path.read_text(errors="replace") if log_path.is_file() else ""
    missing = next(
        (line.strip() for line in log_text.splitlines() if "Missing character:" in line),
        "",
    )
    if missing:
        pdf_path.unlink(missing_ok=True)
        tex_path.with_suffix(".aux").unlink(missing_ok=True)
        raise CompileError(f"has a missing glyph: {missing}", missing)

    for suffix in (".log", ".aux"):
        tex_path.with_suffix(suffix).unlink(missing_ok=True)
    if into is None:
        return pdf_path
    moved = into / pdf_path.name
    pdf_path.replace(moved)
    return moved


def rendered(pdf_path: Path, into: Path) -> Path | None:
    """A PNG of the compiled figure, for looking at without opening the PDF."""
    if not shutil.which("pdftoppm"):
        return None
    png_path = into / f"{pdf_path.stem}.png"
    subprocess.run(
        [
            "pdftoppm", "-png", "-r", str(PNG_DPI), "-singlefile",
            str(pdf_path), str(png_path.with_suffix("")),
        ],
        capture_output=True,
        check=False,
    )
    return png_path if png_path.is_file() else None


def logged_draw(
    run: Run, figure: dict, attempt: int, wide: bool = False
) -> tuple[DrawReply | None, str]:
    """Ask for one figure once, logging the call however it turns out.

    Everything the call involves is here — the timing, the parsing, the failure and
    the log — so that no path through the code can reach the model without leaving
    a folder behind. A timeout, a reply that is not JSON and a reply that is JSON of
    the wrong shape all come back the same way: nothing drawn, and a line saying
    which of them it was.
    """
    prompt = build_prompt(run.prompt, figure, wide)
    parts = [figure_part(figure)]
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=DrawReply,
        thinking_config=types.ThinkingConfig(thinking_level=run.thinking),
    )

    response = None
    reply = None
    error_message = None
    started = time.perf_counter()
    try:
        response = run.client.models.generate_content(
            model=run.model, contents=[prompt, *parts], config=config
        )
        reply = DrawReply.model_validate_json(genai_answer_text(response))
    except Exception as error:  # noqa: BLE001 - transport and parse failures alike
        error_message = f"{type(error).__name__}: {error}"
        reply = None
    duration = time.perf_counter() - started

    cut = False
    if reply is not None and reply.tikz:
        picture = picture_only(reply.tikz)
        cut = picture != reply.tikz
        reply.tikz = picture

    safe_log(
        run.logger,
        files_builder=lambda captured=response: {
            "prompt.txt": prompt,
            **{f"part_{number}.txt": part for number, part in enumerate(parts, start=1)},
            **(genai_response_files(captured) if captured is not None else {}),
        },
        page_number=figure.get("key", "unkeyed"),
        step_name=DRAW_STEP,
        attempt=attempt,
        api="gemini",
        model=run.model,
        usage=genai_usage(response) if response is not None else {},
        duration_seconds=duration,
        status="success" if reply is not None else "error",
        error_message=error_message,
        extra={
            "figure_type": run.figure_type,
            "max_width": width_of(figure, wide),
            "thinking_level": run.thinking,
            "returned": reply.status if reply is not None else None,
            "picture_cut_from_reply": cut,
            "prompt_files": list(run.prompt_files),
        },
    )
    return reply, error_message


def asked(run: Run, figure: dict, wide: bool = False) -> DrawReply:
    """Ask for one figure's TikZ, until one comes back.

    A reply that never arrived is asked for again, since a transport that dropped
    the call is not the model drawing the wrong picture; a reply that arrived
    stands whatever it holds. Every attempt is its own call and its own folder.
    """
    last = ""
    for attempt in range(1, TRANSPORT_TRIES + 1):
        reply, last = logged_draw(run, figure, attempt, wide)
        if reply is not None:
            return reply
        if attempt < TRANSPORT_TRIES:
            time.sleep(2**attempt)
    raise RuntimeError(f"the call did not come back: {last}")


def logged_repair(
    run: Run, figure: dict, tikz: str, report: str, round_number: int, attempt: int,
    wide: bool = False,
) -> tuple[DrawReply | None, str]:
    """Show the model its own picture and the engine's complaint, and ask again.

    The same standing prompt and the same figure as the first ask, with one part
    added: what came back and what LaTeX made of it. Sent as a fresh call rather
    than a turn in a conversation, so that a repair can be read months later
    without the call before it.
    """
    prompt = build_prompt(run.prompt, figure, wide)
    parts = [figure_part(figure), repair_part(run.repair_prompt, tikz, report)]
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=DrawReply,
        thinking_config=types.ThinkingConfig(thinking_level=run.thinking),
    )

    response = None
    reply = None
    error_message = None
    started = time.perf_counter()
    try:
        response = run.client.models.generate_content(
            model=run.model, contents=[prompt, *parts], config=config
        )
        reply = DrawReply.model_validate_json(genai_answer_text(response))
    except Exception as error:  # noqa: BLE001 - transport and parse failures alike
        error_message = f"{type(error).__name__}: {error}"
        reply = None
    duration = time.perf_counter() - started

    cut = False
    if reply is not None and reply.tikz:
        picture = picture_only(reply.tikz)
        cut = picture != reply.tikz
        reply.tikz = picture

    safe_log(
        run.logger,
        files_builder=lambda captured=response: {
            "prompt.txt": prompt,
            **{f"part_{number}.txt": part for number, part in enumerate(parts, start=1)},
            **(genai_response_files(captured) if captured is not None else {}),
        },
        page_number=figure.get("key", "unkeyed"),
        step_name=REPAIR_STEP,
        attempt=attempt,
        api="gemini",
        model=run.model,
        usage=genai_usage(response) if response is not None else {},
        duration_seconds=duration,
        status="success" if reply is not None else "error",
        error_message=error_message,
        extra={
            "figure_type": run.figure_type,
            "max_width": width_of(figure, wide),
            "thinking_level": run.thinking,
            "repair_round": round_number,
            "returned": reply.status if reply is not None else None,
            "picture_cut_from_reply": cut,
            "prompt_files": [*run.prompt_files, str(REPAIR_PROMPT_PATH.relative_to(PROJECT_ROOT))],
        },
    )
    return reply, error_message


def repaired(
    run: Run, figure: dict, tikz: str, report: str, round_number: int,
    wide: bool = False,
) -> DrawReply:
    """Ask for one figure's TikZ again, until a reply comes back."""
    last = ""
    for attempt in range(1, TRANSPORT_TRIES + 1):
        reply, last = logged_repair(run, figure, tikz, report, round_number, attempt, wide)
        if reply is not None:
            return reply
        if attempt < TRANSPORT_TRIES:
            time.sleep(2**attempt)
    raise RuntimeError(f"the call did not come back: {last}")


def logged_check(
    run: Run, result: Result, round_number: int, attempt: int,
) -> tuple[ReviewReply | None, str]:
    """Ask whether a compiled figure matches the givens, showing TikZ and the PNG."""
    prompt = run.check_prompt
    tikz_part = result.tikz.strip()
    parts: list = [figure_part(result.figure), tikz_part]
    png_path = result.png
    if png_path is not None and png_path.is_file():
        parts.append(
            types.Part.from_bytes(data=png_path.read_bytes(), mime_type="image/png")
        )
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=ReviewReply,
        thinking_config=types.ThinkingConfig(thinking_level=run.thinking),
    )

    response = None
    reply = None
    error_message = None
    started = time.perf_counter()
    try:
        response = run.client.models.generate_content(
            model=run.model, contents=[prompt, *parts], config=config
        )
        reply = ReviewReply.model_validate_json(genai_answer_text(response))
    except Exception as error:  # noqa: BLE001 - transport and parse failures alike
        error_message = f"{type(error).__name__}: {error}"
        reply = None
    duration = time.perf_counter() - started

    safe_log(
        run.logger,
        files_builder=lambda captured=response: {
            "prompt.txt": prompt,
            "part_1.txt": parts[0],
            "part_2.txt": tikz_part,
            "image_1.png": (
                png_path.read_bytes()
                if png_path is not None and png_path.is_file() else None
            ),
            **(genai_response_files(captured) if captured is not None else {}),
        },
        page_number=result.key,
        step_name=CHECK_STEP,
        attempt=attempt,
        api="gemini",
        model=run.model,
        usage=genai_usage(response) if response is not None else {},
        duration_seconds=duration,
        status="success" if reply is not None else "error",
        error_message=error_message,
        extra={
            "figure_type": run.figure_type,
            "max_width": result.width,
            "thinking_level": run.thinking,
            "check_round": round_number,
            "shown_the_figure": png_path is not None and png_path.is_file(),
            "returned": reply.verdict if reply is not None else None,
            "prompt_files": [str(CHECK_PROMPT_PATH.relative_to(PROJECT_ROOT))],
        },
    )
    return reply, error_message


def checked(run: Run, result: Result, round_number: int) -> ReviewReply:
    """Ask whether one compiled figure matches the givens, until a reply comes back."""
    last = ""
    for attempt in range(1, TRANSPORT_TRIES + 1):
        reply, last = logged_check(run, result, round_number, attempt)
        if reply is not None:
            return reply
        if attempt < TRANSPORT_TRIES:
            time.sleep(2**attempt)
    raise RuntimeError(f"the call did not come back: {last}")


def logged_redraw(
    run: Run, figure: dict, tikz: str, problems: list[str], round_number: int,
    attempt: int, wide: bool = False,
) -> tuple[DrawReply | None, str]:
    """Draw again from core.md, with the review's problems named as JSON."""
    prompt = build_prompt(run.prompt, figure, wide)
    written = problems_part(problems, tikz)
    parts = [figure_part(figure), written]
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=DrawReply,
        thinking_config=types.ThinkingConfig(thinking_level=run.thinking),
    )

    response = None
    reply = None
    error_message = None
    started = time.perf_counter()
    try:
        response = run.client.models.generate_content(
            model=run.model, contents=[prompt, *parts], config=config
        )
        reply = DrawReply.model_validate_json(genai_answer_text(response))
    except Exception as error:  # noqa: BLE001 - transport and parse failures alike
        error_message = f"{type(error).__name__}: {error}"
        reply = None
    duration = time.perf_counter() - started

    cut = False
    if reply is not None and reply.tikz:
        picture = picture_only(reply.tikz)
        cut = picture != reply.tikz
        reply.tikz = picture

    safe_log(
        run.logger,
        files_builder=lambda captured=response: {
            "prompt.txt": prompt,
            **{f"part_{number}.txt": part for number, part in enumerate(parts, start=1)},
            **(genai_response_files(captured) if captured is not None else {}),
        },
        page_number=figure.get("key", "unkeyed"),
        step_name=DRAW_STEP,
        attempt=attempt,
        api="gemini",
        model=run.model,
        usage=genai_usage(response) if response is not None else {},
        duration_seconds=duration,
        status="success" if reply is not None else "error",
        error_message=error_message,
        extra={
            "figure_type": run.figure_type,
            "max_width": width_of(figure, wide),
            "thinking_level": run.thinking,
            "check_round": round_number,
            "returned": reply.status if reply is not None else None,
            "picture_cut_from_reply": cut,
            "prompt_files": list(run.prompt_files),
        },
    )
    return reply, error_message


def redrawn(
    run: Run, figure: dict, tikz: str, problems: list[str], round_number: int,
    wide: bool = False,
) -> DrawReply:
    """Ask for one figure again from the review's problems, until a reply comes back."""
    last = ""
    for attempt in range(1, TRANSPORT_TRIES + 1):
        reply, last = logged_redraw(
            run, figure, tikz, problems, round_number, attempt, wide
        )
        if reply is not None:
            return reply
        if attempt < TRANSPORT_TRIES:
            time.sleep(2**attempt)
    raise RuntimeError(f"the call did not come back: {last}")


def faithful(run: Run, result: Result, out: Folders) -> Result:
    """Review a compiled figure for mathematical fidelity, and draw again if it fails.

    Layout is not this loop's question. Contradictory givens are not a reason to
    drop the picture: they are named and the figure stays filed.
    """
    if plotted_without_model(run.figure_type):
        return result
    if not result.drawn or run.check_rounds < 1:
        return result
    for spent in range(run.check_rounds + 1):
        review = checked(run, result, spent + 1)
        result.checks = spent + 1
        if review.verdict == "ok":
            result.invalid_source = False
            result.check_problems = []
            return result
        if review.verdict == "invalid_source":
            result.invalid_source = True
            result.check_problems = [item for item in review.problems if item.strip()]
            print(
                f"  {result.key}: invalid source"
                + (f" ({result.check_problems[0]})" if result.check_problems else ""),
                flush=True,
            )
            return result
        result.check_problems = [item for item in review.problems if item.strip()]
        if spent == run.check_rounds:
            print(
                f"  {result.key}: still mathematically wrong after "
                f"{run.check_rounds} redraw(s); keeping it",
                flush=True,
            )
            return result
        print(
            f"  {result.key}: mathematically wrong; drawing again ({spent + 1})",
            flush=True,
        )
        again = redrawn(
            run, result.figure, result.tikz, result.check_problems, spent + 1,
            result.wide,
        )
        if again.status == "abort" or not again.tikz:
            print(
                f"  {result.key}: redraw refused; keeping the compiled picture",
                flush=True,
            )
            return result
        result.tikz = again.tikz
        filed(result, out)
        mended(run, result, out)
        if not result.drawn:
            return result
    return result


# ==========================================================================
# Finding the labels of a figure that collide, by rendering them apart and
# intersecting the pixels.
# ==========================================================================
#
# Everything from here to the end of this block is a measuring instrument and
# nothing else: it calls no model, reads no prompt, and given the same picture
# returns the same answer every time. That is what makes it worth believing, so
# keep it that way — the pipeline below uses it, and it uses nothing of the
# pipeline.
#
# The question this answers is the one a reader asks first and a compiler never
# asks at all: does anything in this picture sit on top of anything else. A figure
# that compiles perfectly can still print a length across the line it measures.
#
# How it works, and why this way:
#
# **TeX counts its own nodes.** Nothing here reads the TikZ. Every node is counted
# as it is typeset, by a hook on `every node`, and a pass shows one of them and
# hides the rest. That catches a `node` written inside a path and the measure
# buried in `\anglemark`, neither of which a regular expression over the source
# would find, and it cannot drift out of step with what was actually drawn.
#
# **Everything is hidden by opacity, never removed.** A hidden path is still on the
# page taking up its room, so every pass crops to exactly the same box and the
# rasters line up pixel for pixel. Remove a path instead and the bounding box moves,
# and two rasters of different figures cannot be compared at all.
#
# **One compile, a page per element.** The passes are pages of a single document
# rather than documents of their own, which is the difference between two runs of
# the engine per figure and one run per label.
#
# **Masks are integers.** A raster becomes one enormous integer, one byte to the
# pixel, so intersecting two elements is `&` and counting the result is
# `bit_count()`. Every pair comes out of masks already in hand, so the work is
# linear in the labels rather than quadratic, and what comes back is where the
# collision is rather than only that there was one.


# One byte to the pixel, so every shift moves in multiples of eight bits.
STRIDE = 8
DEFAULT_DPI = 300

# How far out to keep measuring. Beyond this a label is simply clear of its
# neighbour and the exact distance stops being interesting.
LOOK_PT = 4.0

# Closer than this and two things are worth mentioning, though only an actual
# overlap is worth redrawing for. A textbook label sits close to what it names by
# design — the letter beside a vertex is nearly touching it — so a threshold set
# where it flatters the eye condemns almost every figure ever printed.
TIGHT_PT = 1.5

# Ink shared by two things below this is a graze, not a collision: a letter set
# hard against a line it does not cross shares a pixel or two of antialiasing with
# it. Worth marking, since it prints as a touch; not worth a call to redraw, since
# it reads as a letter beside a line. Given at 300dpi and scaled from there.
GRAZE_PX = 3

# Ink is anything darker than halfway. The figures are black on white and the
# only greys are the antialiasing, so where the threshold sits between the two
# makes no practical difference.
INK = bytes(1 if value < 128 else 0 for value in range(256))

# Counts each node as it is typeset and shows only the chosen one: -1 shows them
# all, 0 shows none. `text opacity` has to be set on both branches, because a
# path drawn at zero opacity takes its node's text down with it unless the text
# is explicitly put back. `\lengthmark`'s measuring node is skipped: showing it
# would print the measure at the origin, on top of a vertex that is not labelled
# with that length.
ISOLATE = r"""
\newcount\clashnode
\tikzset{every node/.append style={/utils/exec={%
  \iflengthmarksizer
  \else
  \global\advance\clashnode by 1
  \ifnum\showonly=-1\relax
    \pgfkeysalso{text opacity=1}%
  \else\ifnum\clashnode=\showonly\relax
    \pgfkeysalso{text opacity=1}%
  \else
    \pgfkeysalso{text opacity=0}%
  \fi\fi
  \fi}}}
\newcommand{\resetnodes}{\global\clashnode=0\relax}
\tikzset{hideink/.style={draw opacity=0, fill opacity=0}}
\AtEndDocument{\immediate\write16{CLASHNODES=\the\clashnode}}
"""
HIDE_GEOMETRY = "every path/.append style=hideink"
NODE_COUNT = re.compile(r"CLASHNODES=(\d+)")
PICTURE_OPENS = re.compile(r"(\\begin\{tikzpicture\})(\s*)(\[?)")
# A scale on the picture itself, not xscale/yscale and not a scale= buried in a
# later style. Composed when a drawing is grown to fill its column.
PICTURE_SCALE = re.compile(
    r"(?<![A-Za-z@])scale\s*=\s*([0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)"
)


class Grid:
    """A fixed pixel canvas, and the mask arithmetic that goes with it.

    Every mask on one grid is the same length, so two of them can be intersected
    directly. Kept as a class only because the width and the total are needed by
    every operation and passing them everywhere reads worse.
    """

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.bits = width * height * STRIDE
        self.limit = (1 << self.bits) - 1
        self.row = width * STRIDE

    def mask(self, pixels: bytes) -> int:
        return int.from_bytes(pixels.translate(INK), "big")

    def spread(self, bits: int) -> int:
        """The mask grown by one pixel in each direction."""
        grown = (
            bits
            | (bits << STRIDE)
            | (bits >> STRIDE)
            | (bits << self.row)
            | (bits >> self.row)
        )
        return grown & self.limit

    def gap(self, one: int, other: int, look: int) -> int | None:
        """How many pixels apart two masks are; 0 if they touch, None if far apart."""
        if one & other:
            return 0
        grown = one
        for step in range(1, look + 1):
            grown = self.spread(grown)
            if grown & other:
                return step
        return None

    def box(self, bits: int) -> tuple[int, int, int, int]:
        """The pixel rectangle a mask occupies, as left, top, right, bottom."""
        left, top = self.width, self.height
        right = bottom = -1
        for pixel in self.pixels(bits):
            column, row = pixel % self.width, pixel // self.width
            left, right = min(left, column), max(right, column)
            top, bottom = min(top, row), max(bottom, row)
        return left, top, right, bottom

    def pixels(self, bits: int):
        """Every inked pixel of a mask, as an index into the canvas."""
        while bits:
            low = bits & -bits
            yield (self.bits - STRIDE - (low.bit_length() - 1)) // STRIDE
            bits ^= low


@dataclass
class Label:
    """One thing the figure prints, and where it landed."""

    number: int
    mask: int = field(repr=False)
    box: tuple[int, int, int, int]

    @property
    def centre(self) -> tuple[int, int]:
        left, top, right, bottom = self.box
        return (left + right) // 2, (top + bottom) // 2


@dataclass
class Clash:
    """Two things that are on top of each other, or nearly."""

    left: Label
    right: Label | None
    pixels: int
    gap: float
    hard: bool

    def where(self, report: Report) -> str:
        first = report.place(self.left)
        if self.right is None:
            return f"the label {first} and the drawing"
        return f"the label {first} and the label {report.place(self.right)}"


@dataclass
class Report:
    """What one figure's labels do to each other."""

    width: int
    height: int
    dpi: int
    labels: list[Label]
    clashes: list[Clash]
    full: int = field(default=0, repr=False)
    geometry: int = field(default=0, repr=False)

    @property
    def hard(self) -> list[Clash]:
        return [clash for clash in self.clashes if clash.hard]

    @property
    def tight(self) -> list[Clash]:
        return [
            clash for clash in self.clashes if not clash.hard and clash.gap < TIGHT_PT
        ]

    @property
    def clean(self) -> bool:
        return not self.hard

    @property
    def notable(self) -> list[Clash]:
        """The clashes worth marking: an overlap, or a gap tight enough to read as one."""
        return self.hard + self.tight

    def place(self, label: Label) -> str:
        """Where a label sits, in millimetres from the top left of the figure."""
        across, down = label.centre
        scale = 25.4 / self.dpi
        return f"{across * scale:.0f}mm across, {down * scale:.0f}mm down"

    def wording(self) -> str:
        """The clashes as a paragraph the model can act on."""
        lines = []
        for clash in sorted(self.hard, key=lambda one: -one.pixels):
            lines.append(f"- {clash.where(self)} overlap.")
        for clash in sorted(self.tight, key=lambda one: (one.gap, -one.pixels)):
            if clash.pixels:
                lines.append(f"- {clash.where(self)} just touch.")
            else:
                lines.append(f"- {clash.where(self)} are only {clash.gap:.1f}pt apart.")
        return "\n".join(lines)


def document(preamble: str, body: str, border: str) -> str:
    return (
        f"\\documentclass[{border},multi=tikzpicture]{{standalone}}\n"
        + preamble
        + ISOLATE
        + "\\begin{document}\n"
        + body
        + "\n\\end{document}\n"
    )


def _run(engine: str, tex: Path) -> str:
    done = subprocess.run(
        [engine, "-interaction=nonstopmode", tex.name],
        cwd=tex.parent, capture_output=True, text=True,
    )
    return done.stdout


def _pgm(path: Path) -> tuple[int, int, bytes]:
    """A binary PGM, read without an image library: P5, then three numbers."""
    data = path.read_bytes()
    at, fields = 2, []
    while len(fields) < 3:
        while data[at : at + 1].isspace():
            at += 1
        if data[at : at + 1] == b"#":
            at = data.index(b"\n", at) + 1
            continue
        start = at
        while not data[at : at + 1].isspace():
            at += 1
        fields.append(int(data[start:at]))
    return fields[0], fields[1], data[at + 1 :]


def without_ink(picture: str) -> str:
    """The same picture with its paths made invisible and its nodes left alone.

    The style has to go inside whatever options the picture already carries, not
    into a second bracket beside them. A tikzpicture takes one optional argument:
    write `\\begin{tikzpicture}[hide][x=0.11cm]` and TikZ reads the first group as
    the options and lets the second fall into the body, so the picture loses its
    own unit vectors and every coordinate reverts to the default centimetre. A
    chart that scales data to the page that way — 0 to 100 up the side at 0.06cm
    apiece — then renders a metre tall, the isolated pages come out a different
    size from the whole one, and the figure is quietly dropped from the check
    rather than reported as colliding.
    """
    opened = PICTURE_OPENS.search(picture)
    if opened is None:
        return picture
    head, space, bracket = opened.groups()
    if bracket:
        # Straight after the opening bracket, so the picture's own options still
        # follow and win where they say something this one also says.
        inserted = f"{head}{space}[{HIDE_GEOMETRY},"
    else:
        inserted = f"{head}[{HIDE_GEOMETRY}]{space}"
    return picture[: opened.start()] + inserted + picture[opened.end() :]


def _scale_num(value: float) -> str:
    """A TikZ scale factor written without trailing zeros."""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "1"


def picture_options(tikz: str) -> tuple[int, int, str] | None:
    """Where the tikzpicture's optional argument sits, and the options inside it."""
    opened = PICTURE_OPENS.search(tikz)
    if opened is None or not opened.group(3):
        return None
    start = opened.end() - 1
    depth = 0
    for index, char in enumerate(tikz[start:], start):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return start, index + 1, tikz[start + 1 : index]
    return None


def scale_picture(tikz: str, factor: float) -> str:
    """Multiply the picture's geometry by `factor`, leaving the type alone.

    `scale=` on the tikzpicture grows coordinates and not the labels, and does not
    grow \\anglemark radii either: those are real centimetres. Filling a column
    the drawing has not used is that — a larger shape, the same type.
    """
    if abs(factor - 1) < 0.02:
        return tikz
    opened = PICTURE_OPENS.search(tikz)
    if opened is None:
        return tikz
    span = picture_options(tikz)
    if span is None:
        head, space, _ = opened.groups()
        inserted = f"{head}[scale={_scale_num(factor)}]{space}"
        return tikz[: opened.start()] + inserted + tikz[opened.end() :]
    start, end, options = span
    found = PICTURE_SCALE.search(options)
    if found:
        grown = float(found.group(1)) * factor
        options = options[: found.start()] + f"scale={_scale_num(grown)}" + options[found.end() :]
    else:
        options = f"scale={_scale_num(factor)}, {options}"
    return tikz[:start] + "[" + options + "]" + tikz[end:]


def distinct_printed_labels(labels: list[Label]) -> list[Label]:
    """One entry per distinct ink, in the order the labels were numbered.

    Clash isolation counts every TikZ node, including nested pictures a
    printed mark creates while it is typeset. Those extra nodes rasterise
    as the same pixels as the mark they belong to. Pairwise they look like
    a stack of labels on one spot, and a longer Arabic unit word adds one
    more copy, which the translation then treats as a new overlap. One
    mask is one printed mark; two different words on top of each other
    still have two masks.
    """
    seen: set[int] = set()
    unique: list[Label] = []
    for label in labels:
        if label.mask in seen:
            continue
        seen.add(label.mask)
        unique.append(label)
    return unique


def looked_at(
    picture: str,
    preamble: str,
    border: str,
    folder: Path,
    engine: str = "lualatex",
    dpi: int = DEFAULT_DPI,
) -> Report | None:
    """Render one figure apart from itself and report what collides.

    `picture` is the tikzpicture as it stands, `preamble` everything the figure
    is normally compiled against. Returns None where the figure cannot be taken
    apart, which is not the same as a figure with nothing wrong with it.
    """
    folder.mkdir(parents=True, exist_ok=True)
    for stale in folder.glob("page-*.pgm"):
        stale.unlink()

    counting = folder / "count.tex"
    counting.write_text(
        document(preamble, "\\def\\showonly{0}\\resetnodes\n" + picture, border),
        encoding="utf-8",
    )
    found = NODE_COUNT.search(_run(engine, counting))
    if not found:
        return None
    count = int(found.group(1))
    if not count:
        return None

    hidden = without_ink(picture)
    pages = [
        "\\def\\showonly{-1}\\resetnodes\n" + picture,
        "\\def\\showonly{0}\\resetnodes\n" + picture,
    ]
    pages += [f"\\def\\showonly{{{index}}}\\resetnodes\n" + hidden for index in range(1, count + 1)]
    sheet = folder / "sheet.tex"
    sheet.write_text(document(preamble, "\n".join(pages), border), encoding="utf-8")
    _run(engine, sheet)
    pdf = sheet.with_suffix(".pdf")
    if not pdf.is_file():
        return None

    subprocess.run(
        ["pdftoppm", "-gray", "-r", str(dpi), str(pdf), str(folder / "page")],
        capture_output=True, check=False,
    )
    rasters = [_pgm(path) for path in sorted(folder.glob("page-*.pgm"))]
    if len(rasters) != count + 2:
        return None
    sizes = {(width, height) for width, height, _ in rasters}
    if len(sizes) != 1:
        return None

    width, height, _ = rasters[0]
    grid = Grid(width, height)
    full = grid.mask(rasters[0][2])
    geometry = grid.mask(rasters[1][2])

    labels = []
    for pixels in (page[2] for page in rasters[2:]):
        bits = grid.mask(pixels)
        if bits:
            labels.append(bits)
    # Numbered down the page and then across it, so that the numbering a person
    # reads matches the order they would find the labels in.
    boxed = sorted((grid.box(bits), bits) for bits in labels)
    ordered = distinct_printed_labels(
        [
            Label(number=number, mask=bits, box=box)
            for number, (box, bits) in enumerate(
                sorted(boxed, key=lambda pair: (pair[0][1], pair[0][0])), start=1
            )
        ]
    )

    look = round(LOOK_PT * dpi / 72)
    per_pt = 72 / dpi
    graze = max(1, round(GRAZE_PX * dpi / 300))

    def against(one: Label, other: Label | None, mask: int) -> Clash | None:
        distance = grid.gap(one.mask, mask, look)
        if distance is None:
            return None
        shared = (one.mask & mask).bit_count()
        return Clash(one, other, shared, distance * per_pt, hard=shared > graze)

    found_clashes = []
    for label in ordered:
        found = against(label, None, geometry)
        if found is not None:
            found_clashes.append(found)
    for first in range(len(ordered)):
        for second in range(first + 1, len(ordered)):
            other = ordered[second]
            found = against(ordered[first], other, other.mask)
            if found is not None:
                found_clashes.append(found)

    return Report(width, height, dpi, ordered, found_clashes,
                  full=full, geometry=geometry)


# --- the annotated picture --------------------------------------------------

def png(path: Path, width: int, height: int, rows: list[bytearray]) -> None:
    """Write RGB rows as a PNG, which needs zlib and nothing else."""
    raw = b"".join(b"\x00" + bytes(row) for row in rows)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            +             struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


RED = (200, 30, 30)
AMBER = (220, 150, 40)


def marked(report: Report, path: Path) -> Path:
    """The figure as it prints, with what collides picked out in colour.

    Overlapping ink is painted red and the box round each label in a clash is
    drawn in the same colour, so that a reader — or a model being shown its own
    work — can see at once which label is the problem rather than being told a
    coordinate and left to find it.
    """
    grid = Grid(report.width, report.height)
    rows = [bytearray(b"\xff" * (report.width * 3)) for _ in range(report.height)]

    for pixel in grid.pixels(report.full):
        column, row = pixel % report.width, pixel // report.width
        rows[row][column * 3 : column * 3 + 3] = b"\x20\x20\x20"

    hot = 0
    for clash in report.hard:
        hot |= clash.left.mask & (clash.right.mask if clash.right else report.geometry)
    for pixel in grid.pixels(hot):
        column, row = pixel % report.width, pixel // report.width
        rows[row][column * 3 : column * 3 + 3] = bytes(RED)

    for clash in report.notable:
        colour = RED if clash.hard else AMBER
        for label in (clash.left, clash.right):
            if label is not None:
                outline(rows, report, label.box, colour)

    png(path, report.width, report.height, rows)
    return path


def outline(rows, report: Report, box, colour) -> None:
    """A dotted rectangle round a label, clear of the label itself."""
    left, top, right, bottom = (box[0] - 3, box[1] - 3, box[2] + 3, box[3] + 3)
    left, top = max(0, left), max(0, top)
    right, bottom = min(report.width - 1, right), min(report.height - 1, bottom)
    paint = bytes(colour)
    for column in range(left, right + 1):
        if column % 4 < 2:
            rows[top][column * 3 : column * 3 + 3] = paint
            rows[bottom][column * 3 : column * 3 + 3] = paint
    for row in range(top, bottom + 1):
        if row % 4 < 2:
            rows[row][left * 3 : left * 3 + 3] = paint
            rows[row][right * 3 : right * 3 + 3] = paint


# ==========================================================================
# The end of the measuring, and back to the run that uses it.
# ==========================================================================


def inspected(result: Result, out: Folders) -> Result:
    """Take a compiled figure apart and find out what it prints on top of what.

    Only for a figure that compiled: there is nothing to measure on a page the
    engine refused to make.
    """
    if not result.drawn:
        return result
    # Taking a figure apart makes a document, a PDF and a raster per label, which
    # is megabytes a figure and of no interest once it has been measured. Only the
    # marked picture is worth keeping, so the rest is made somewhere temporary.
    with tempfile.TemporaryDirectory(prefix=f"clash-{result.key}-") as workroom:
        report = looked_at(
            picture=standard_lines(
                latex.profile_tikz(result.tikz.strip(), out.profile)
            ),
            preamble=drawing_preamble(out.profile),
            border=f"{BODY_SIZE}pt,border={FIGURE_BORDER}",
            folder=Path(workroom),
            engine=LATEX_ENGINE,
        )
        result.clash = report
        if report is None:
            # A figure that cannot be taken apart is not a figure with nothing
            # wrong with it, and the two used to look alike in run.json: a null
            # where a clean report should be. Say so, or a picture drops out of
            # the check and nobody finds out until it is on the page.
            print(f"  ? {result.key}: cannot be taken apart, so not checked for"
                  " collisions", flush=True)
        marked_path = out.clash_png(result.key)
        if report is not None and report.notable:
            result.marked = marked(report, marked_path)
        else:
            # A figure laid out again and now clean must not be left with the
            # picture of the collision it used to have.
            marked_path.unlink(missing_ok=True)
            result.marked = None
    return result


def logged_relabel(
    run: Run, figure: dict, tikz: str, report: Report, marked_path: Path | None,
    round_number: int, attempt: int, wide: bool = False,
) -> tuple[DrawReply | None, str]:
    """Show the model where its picture collides with itself, and ask again.

    The marked-up figure goes with the words. Naming a label by where it sits in
    millimetres is exact but slow to act on; the same label ringed in red on the
    picture is neither ambiguous nor work to find, and the model is perfectly able
    to look at it.
    """
    prompt = build_prompt(run.prompt, figure, wide)
    written = relabel_part(run.relabel_prompt, tikz, report)
    parts: list = [figure_part(figure), written]
    if marked_path is not None and marked_path.is_file():
        parts.append(
            types.Part.from_bytes(data=marked_path.read_bytes(),
                                  mime_type="image/png")
        )
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=DrawReply,
        thinking_config=types.ThinkingConfig(thinking_level=run.thinking),
    )

    response = None
    reply = None
    error_message = None
    started = time.perf_counter()
    try:
        response = run.client.models.generate_content(
            model=run.model, contents=[prompt, *parts], config=config
        )
        reply = DrawReply.model_validate_json(genai_answer_text(response))
    except Exception as error:  # noqa: BLE001 - transport and parse failures alike
        error_message = f"{type(error).__name__}: {error}"
        reply = None
    duration = time.perf_counter() - started

    cut = False
    if reply is not None and reply.tikz:
        picture = picture_only(reply.tikz)
        cut = picture != reply.tikz
        reply.tikz = picture

    safe_log(
        run.logger,
        files_builder=lambda captured=response: {
            "prompt.txt": prompt,
            "part_1.txt": parts[0],
            "part_2.txt": written,
            "image_1.png": (marked_path.read_bytes()
                            if marked_path and marked_path.is_file() else None),
            **(genai_response_files(captured) if captured is not None else {}),
        },
        page_number=figure.get("key", "unkeyed"),
        step_name=CLASH_STEP,
        attempt=attempt,
        api="gemini",
        model=run.model,
        usage=genai_usage(response) if response is not None else {},
        duration_seconds=duration,
        status="success" if reply is not None else "error",
        error_message=error_message,
        extra={
            "figure_type": run.figure_type,
            "max_width": width_of(figure, wide),
            "thinking_level": run.thinking,
            "clash_round": round_number,
            "overlaps": len(report.hard),
            "tight": len(report.tight),
            "shown_the_figure": marked_path is not None,
            "returned": reply.status if reply is not None else None,
            "picture_cut_from_reply": cut,
            "prompt_files": [*run.prompt_files, str(RELABEL_PROMPT_PATH.relative_to(PROJECT_ROOT))],
        },
    )
    return reply, error_message


def relabelled(
    run: Run, figure: dict, tikz: str, report: Report, marked_path: Path | None,
    round_number: int, wide: bool = False,
) -> DrawReply:
    """Ask for one figure to be laid out again, until a reply comes back."""
    last = ""
    for attempt in range(1, TRANSPORT_TRIES + 1):
        reply, last = logged_relabel(
            run, figure, tikz, report, marked_path, round_number, attempt, wide
        )
        if reply is not None:
            return reply
        if attempt < TRANSPORT_TRIES:
            time.sleep(2**attempt)
    raise RuntimeError(f"the call did not come back: {last}")


def shape_of(pdf: Path | None) -> tuple[float, float] | None:
    """A compiled figure's ink, in millimetres across and down.

    Cropped every way, not only above and below. What the page is trimmed to is a
    typesetting decision — the run leaves the figure the width of its column — and
    the question here is the shape of the drawing itself.

    Ghostscript is what crops to the ink. When it is not on the machine the
    compiled page is the shape: roomed, too_squat, and the library row still
    need a width and a height, and a tall first draft must not be enlarged
    just because the tight box could not be read.
    """
    if pdf is None or not pdf.is_file():
        return None
    box = ink_box(pdf)
    per_mm = 72 / 25.4
    if box is None:
        width, height = pdf_size(pdf)
        if not width or not height:
            return None
        return width / per_mm, height / per_mm
    left, bottom, right, top = box
    return (right - left) / per_mm, (top - bottom) / per_mm


def occupying_mm(result: Result) -> float:
    """How much of the column the compiled picture actually takes, in millimetres.

    The larger of the cropped ink and the page, because a drawing can be narrower
    than the box its coordinates asked for, and either number over the measure is
    a picture the paper will shrink — type and all.
    """
    ink = (result.ink or (0.0, 0.0))[0]
    page = 0.0
    if result.size:
        page = result.size[0] / latex.POINTS_PER_INCH * latex.MM_PER_INCH
    return max(ink, page)


def fills_measure(result: Result) -> bool:
    """Whether the drawing has used the column it was drawn to."""
    measure = millimetres(result.width)
    occupied = occupying_mm(result)
    return bool(measure) and occupied >= FULL_MEASURE * measure


def roomed(result: Result, out: Folders, *, even_if_clean: bool = False) -> Result:
    """Grow a colliding drawing to fill its column, without asking.

    A figure standing well inside 84mm was never squeezed by it. Scaling the
    geometry there — type and \\anglemark radii left in centimetres — is what
    too_squat says that figure needed, and it is done here before any label is
    moved and before any wider measure is offered.

    A drawing already taller than it is wide is left: filling the column would
    stretch a skinny rhombus to the height of the page. Side margin is the
    right leftover, not a bigger diamond.
    """
    if not result.drawn:
        return result
    if result.clash is not None and result.clash.clean and not even_if_clean:
        return result
    if fills_measure(result):
        return result
    if result.ink and result.ink[1] > result.ink[0]:
        return result
    measure = millimetres(result.width)
    occupied = occupying_mm(result)
    if not occupied:
        return result
    factor = (0.97 * measure) / occupied
    if factor <= 1.02:
        return result
    print(
        f"  {result.key}: {occupied:.0f}mm in a {measure:.0f}mm column; "
        f"enlarging to fill it",
        flush=True,
    )
    before = result.tikz
    result.tikz = scale_picture(untrimmed(result.tikz), factor)
    filed(result, out)
    if not result.drawn:
        result.tikz = before
        filed(result, out)
        inspected(result, out)
        return result
    over = occupying_mm(result)
    if over > measure + 0.4:
        result.tikz = scale_picture(untrimmed(before), factor * (measure / over) * 0.98)
        filed(result, out)
        if not result.drawn:
            result.tikz = before
            filed(result, out)
    inspected(result, out)
    return result


def too_squat(result: Result, ratio: float = WIDE_RATIO) -> bool:
    """Whether a figure came out too flat to be read at the column it was drawn to.

    Two things have to be true, and the second is the one that is easy to forget.
    The figure has to be flat, and it has to be flat *against the column* — the
    ink reaching most of the way across the measure it was drawn to. A figure
    standing well inside its column was never squeezed by it, so there is nothing
    a wider column can give it, and the ask goes out anyway and comes back worse:
    a scissors diagram 50mm wide in an 84mm column was drawn again at 162mm and
    returned as the same four terms with a metre of empty X between them, 2:1
    become 3:1. What that figure needed was to be drawn larger where it was.
    """
    if not result.ink or result.ratio < ratio:
        return False
    measure = millimetres(result.width)
    return bool(measure) and result.ink[0] >= FULL_MEASURE * measure


def too_tall(result: Result, ratio: float = WIDE_RATIO) -> bool:
    """Whether a figure came out too tall for the column it was drawn to.

    Height at least `ratio` times the width. The drawing does not have to fill
    the column: a pair stacked in a narrow cell is tall without spanning that
    cell, and the fill test that guards a flat figure would leave it there.
    """
    if ratio <= 0 or not result.ink or not result.ink[0]:
        return False
    return result.ink[1] / result.ink[0] >= ratio


def widen_set(results: list[Result], ratio: float = WIDE_RATIO) -> set[str]:
    """Which figures are drawn again to the wide measure.

    A figure earns it by coming out too flat, or too tall. Only that figure is
    drawn again. The other members of its set stay at the column they were
    drawn to; the paper gives the set one line each once any member is wide.
    A figure already at the wide measure is not drawn again.
    """
    return {
        result.key
        for result in results
        if result.drawn
        and not result.wide
        and (too_squat(result, ratio) or too_tall(result, ratio))
    }


def collision_score(report: Report) -> tuple[int, int]:
    """How bad a figure's collisions are, worst first: how many, then how much ink.

    Two numbers rather than one because they answer different halves of the
    question. A figure with one label on a line is better placed than a figure with
    three, whatever the ink comes to; between two figures with one each, the one
    sharing less ink is the nearer miss.
    """
    return len(report.hard), sum(clash.pixels for clash in report.hard)


def unclashed(run: Run, result: Result, out: Folders) -> Result:
    """Ask again while the figure prints something on top of something else.

    The column is filled first if the drawing has not used it, because a small
    picture in an 84mm column is not a picture the column flattened. Labels are
    then moved, and the best picture is kept, not the last one. Over one round
    those are the same thing; over several they are not, and a label with nowhere
    good to go is exactly where a later round is liable to come back worse than an
    earlier one. Because every round is measured rather than judged, better and
    worse are known and not guessed at, so there is no reason to accept a reply
    that lost ground.
    """
    inspected(result, out)
    roomed(result, out)
    best: tuple[tuple[int, int], str] | None = None
    for spent in range(run.clash_rounds + 1):
        if result.clash is None:
            # The figure would not come apart, so there is nothing to measure and
            # nothing to say to the model about it.
            return result
        score = collision_score(result.clash)
        if best is None or score < best[0]:
            best = (score, result.tikz)
        if result.clash.clean:
            return result
        if spent == run.clash_rounds:
            # The last reply has been measured and there is no round left to spend.
            break
        print(
            f"  {result.key}: {len(result.clash.hard)} overlap(s); "
            f"asking for it to be laid out again ({spent + 1})",
            flush=True,
        )
        again = relabelled(
            run, result.figure, result.tikz, result.clash, result.marked, spent + 1,
            result.wide,
        )
        result.relabels = spent + 1
        if again.status == "abort" or not again.tikz:
            break
        result.tikz = again.tikz
        filed(result, out)
        if not result.drawn:
            # The new picture does not compile. The repair loop owns that for a
            # type the model drew. A local plot has no repair: keep the last
            # picture that compiled rather than discarding it.
            if is_local(run.figure_type) and best is not None:
                result.tikz = best[1]
                filed(result, out)
            return result
        inspected(result, out)

    if best is not None and best[1] != result.tikz:
        result.tikz = best[1]
        filed(result, out)
        inspected(result, out)
    return pin_clashed(run, result, out)


def logged_pin(
    run: Run, figure: dict, tikz: str, report: Report, marked_path: Path | None,
    attempt: int, wide: bool = False,
) -> tuple[DrawReply | None, str]:
    """Show leftover collisions and ask for the colliding words to be pinned."""
    prompt = build_prompt(run.prompt, figure, wide)
    written = relabel_part(run.pin_prompt, tikz, report)
    parts: list = [figure_part(figure), written]
    if marked_path is not None and marked_path.is_file():
        parts.append(
            types.Part.from_bytes(data=marked_path.read_bytes(),
                                  mime_type="image/png")
        )
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=DrawReply,
        thinking_config=types.ThinkingConfig(thinking_level=run.thinking),
    )

    response = None
    reply = None
    error_message = None
    started = time.perf_counter()
    try:
        assert run.client is not None
        response = run.client.models.generate_content(
            model=run.model, contents=[prompt, *parts], config=config
        )
        reply = DrawReply.model_validate_json(genai_answer_text(response))
    except Exception as error:  # noqa: BLE001 - transport and parse failures alike
        error_message = f"{type(error).__name__}: {error}"
        reply = None
    duration = time.perf_counter() - started

    cut = False
    if reply is not None and reply.tikz:
        picture = picture_only(reply.tikz)
        cut = picture != reply.tikz
        reply.tikz = picture

    safe_log(
        run.logger,
        files_builder=lambda captured=response: {
            "prompt.txt": prompt,
            "part_1.txt": parts[0],
            "part_2.txt": written,
            "image_1.png": (marked_path.read_bytes()
                            if marked_path and marked_path.is_file() else None),
            **(genai_response_files(captured) if captured is not None else {}),
        },
        page_number=figure.get("key", "unkeyed"),
        step_name=PIN_STEP,
        attempt=attempt,
        api="gemini",
        model=run.model,
        usage=genai_usage(response) if response is not None else {},
        duration_seconds=duration,
        status="success" if reply is not None else "error",
        error_message=error_message,
        extra={
            "figure_type": run.figure_type,
            "max_width": width_of(figure, wide),
            "thinking_level": run.thinking,
            "overlaps": len(report.hard),
            "tight": len(report.tight),
            "shown_the_figure": marked_path is not None,
            "returned": reply.status if reply is not None else None,
            "picture_cut_from_reply": cut,
            "prompt_files": [*run.prompt_files, str(PIN_PROMPT_PATH.relative_to(PROJECT_ROOT))],
        },
    )
    return reply, error_message


def asked_pin(
    run: Run, figure: dict, tikz: str, report: Report, marked_path: Path | None,
    wide: bool = False,
) -> DrawReply:
    """Ask once for leftover word labels to be pinned, until a reply comes back."""
    last = ""
    for attempt in range(1, TRANSPORT_TRIES + 1):
        reply, last = logged_pin(
            run, figure, tikz, report, marked_path, attempt, wide
        )
        if reply is not None:
            return reply
        if attempt < TRANSPORT_TRIES:
            time.sleep(2**attempt)
    raise RuntimeError(f"the call did not come back: {last}")


def pin_clashed(run: Run, result: Result, out: Folders) -> Result:
    """Ask the model to pin leftover word labels after clash rounds are spent.

    Geometry's last resort is ``\\lengthmark[][arrows]``; a name on a marked
    point is ``\\wordpin``. Ticks and vertex letters stay. A picture the engine
    then refuses is discarded; the least-colliding picture already in hand is
    kept.
    """
    if run.client is None or not run.pin_prompt:
        return result
    if not result.drawn or result.clash is None or result.clash.clean:
        return result
    best_score = collision_score(result.clash)
    best_tikz = result.tikz
    print(
        f"  {result.key}: {len(result.clash.hard)} overlap(s); "
        "asking for word labels to be pinned",
        flush=True,
    )
    again = asked_pin(
        run, result.figure, result.tikz, result.clash, result.marked, result.wide,
    )
    result.pins = 1
    if again.status == "abort" or not again.tikz:
        return result
    result.tikz = again.tikz
    filed(result, out)
    if not result.drawn:
        result.tikz = best_tikz
        filed(result, out)
        inspected(result, out)
        return result
    inspected(result, out)
    if result.clash is None:
        result.tikz = best_tikz
        filed(result, out)
        inspected(result, out)
        return result
    if collision_score(result.clash) > best_score:
        result.tikz = best_tikz
        filed(result, out)
        inspected(result, out)
    return result


def shape_words(result: Result, ratio: float = WIDE_RATIO) -> tuple[str, str]:
    """How the figure came out and why that is a reason to draw it again."""
    across, down = result.ink or (0.0, 0.0)
    shape = f"{across:.0f}mm across and {down:.0f}mm down"
    if too_squat(result, ratio):
        reason = (
            f"{result.ratio:.1f} times wider than it is tall, which is too flat to "
            f"read at that measure."
        )
    else:
        tall = (down / across) if across else 0.0
        reason = (
            f"{tall:.1f} times taller than it is wide, which is too tall to "
            f"read at that measure."
        )
    return shape, reason


def siblings_part(result: Result, others: list[Result]) -> str:
    """What to tell a figure about the other parts of its exercise.

    Named and shown, because the set has to match and matching is not something
    the description can ask for: two figures drawn from two descriptions, each
    perfectly good, will happily disagree about how big a tick mark is.
    """
    if not others:
        return ""
    lines = [
        "### The other parts of this exercise",
        "",
        f"This figure is one of {len(others) + 1} printed together as the parts of a",
        "single exercise. Every one of them is being drawn again to the same measure,",
        "and they have to look like a set: the same scale, the same size of lettering,",
        "the same way of marking equal sides, equal angles and right angles. A reader",
        "meets all of them at once, and one part drawn larger or marked differently",
        "than the others reads as a mistake even when it is correct.",
        "",
        "Their pictures are attached, as they stand at the narrow measure. They are",
        "what the set currently looks like, not what it should look like — they are",
        "all about to grow, exactly as yours is. Take the conventions from them and",
        "the size from the measure you have been given.",
        "",
    ]
    for other in others:
        described = one_line(str(other.figure.get("description", "")), 200)
        lines.append(f"- **{other.key}** — {described}")
    lines.append("")
    return "\n".join(lines)


def widen_part(
    template: str, result: Result, others: list[Result], ratio: float = WIDE_RATIO
) -> str:
    """The picture, its shape and its set, as the model is shown them."""
    shape, reason = shape_words(result, ratio)
    return (
        template
        .replace("{max_width}", width_of(result.figure, wide=True))
        .replace("{old_width}", width_of(result.figure))
        .replace("{old_shape}", shape)
        .replace("{reason}", reason)
        .replace("{siblings}", siblings_part(result, others))
        .replace("{tikz}", result.tikz.strip())
    )


def logged_widen(
    run: Run, result: Result, others: list[Result], attempt: int
) -> tuple[DrawReply | None, str]:
    """Ask for one figure again at the wide measure, showing it the set it belongs to."""
    figure = result.figure
    prompt = build_prompt(run.prompt, figure, wide=True)
    written = widen_part(run.wide_prompt, result, others, run.wide_ratio)
    parts: list = [figure_part(figure), written]
    shown = [other for other in others if other.png and other.png.is_file()]
    for other in shown:
        parts.append(
            types.Part.from_bytes(data=other.png.read_bytes(), mime_type="image/png")
        )
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=DrawReply,
        thinking_config=types.ThinkingConfig(thinking_level=run.thinking),
    )

    response = None
    reply = None
    error_message = None
    started = time.perf_counter()
    try:
        response = run.client.models.generate_content(
            model=run.model, contents=[prompt, *parts], config=config
        )
        reply = DrawReply.model_validate_json(genai_answer_text(response))
    except Exception as error:  # noqa: BLE001 - transport and parse failures alike
        error_message = f"{type(error).__name__}: {error}"
        reply = None
    duration = time.perf_counter() - started

    cut = False
    if reply is not None and reply.tikz:
        picture = picture_only(reply.tikz)
        cut = picture != reply.tikz
        reply.tikz = picture

    safe_log(
        run.logger,
        files_builder=lambda captured=response: {
            "prompt.txt": prompt,
            "part_1.txt": parts[0],
            "part_2.txt": written,
            **{
                f"image_{number}.png": other.png.read_bytes()
                for number, other in enumerate(shown, start=1)
            },
            **(genai_response_files(captured) if captured is not None else {}),
        },
        page_number=figure.get("key", "unkeyed"),
        step_name=WIDEN_STEP,
        attempt=attempt,
        api="gemini",
        model=run.model,
        usage=genai_usage(response) if response is not None else {},
        duration_seconds=duration,
        status="success" if reply is not None else "error",
        error_message=error_message,
        extra={
            "figure_type": run.figure_type,
            "max_width": width_of(figure, wide=True),
            "narrow_width": width_of(figure),
            "thinking_level": run.thinking,
            "was_mm": [round(number, 1) for number in (result.ink or (0, 0))],
            "was_ratio": round(result.ratio, 2),
            "triggered_itself": too_squat(result),
            "set_shown": [other.key for other in shown],
            "returned": reply.status if reply is not None else None,
            "picture_cut_from_reply": cut,
            "prompt_files": [*run.prompt_files, str(WIDEN_PROMPT_PATH.relative_to(PROJECT_ROOT))],
        },
    )
    return reply, error_message


def rewidened(run: Run, result: Result, others: list[Result]) -> DrawReply:
    """Ask for one figure at the wide measure, until a reply comes back."""
    last = ""
    for attempt in range(1, TRANSPORT_TRIES + 1):
        reply, last = logged_widen(run, result, others, attempt)
        if reply is not None:
            return reply
        if attempt < TRANSPORT_TRIES:
            time.sleep(2**attempt)
    raise RuntimeError(f"the call did not come back: {last}")


def widen(run: Run, results: list[Result], out: Folders) -> list[Result]:
    """Draw again, to the wide measure, the figures the column has flattened.

    A whole phase of the run rather than something a figure decides for itself.
    Every figure has to be drawn and measured before it is known which of them
    are flat or tall. Only that figure is drawn again.
    """
    if run.wide_ratio <= 0:
        return []
    by_key = {result.key: result for result in results}
    sets = grouped([result.figure for result in results])
    wanted = widen_set(results, run.wide_ratio)
    if not wanted:
        return []

    flat = [key for key in wanted if too_squat(by_key[key], run.wide_ratio)]
    tall = [key for key in wanted if too_tall(by_key[key], run.wide_ratio)]
    print(
        f"widening {len(wanted)} figure(s)"
        + (f", {len(flat)} too flat" if flat else "")
        + (f", {len(tall)} too tall" if tall else ""),
        flush=True,
    )

    def bigger(key: str) -> Result:
        result = by_key[key]
        others = [
            by_key[other]
            for other in sorted(sets.get(key, {key}) - {key})
            if other in by_key and other in wanted
        ]
        started = time.perf_counter()
        was = result.ratio
        try:
            if is_local(run.figure_type):
                drawer = local_drawer(run.figure_type)
                try:
                    tikz = drawer.tikz_of(result.figure, wide=True)
                except drawer.CannotDraw:
                    print(
                        f"  {key}: kept at {result.width}; the wider draw was refused",
                        flush=True,
                    )
                    return result
                result.wide = True
                result.tikz = tikz
                filed(result, out)
                if result.drawn:
                    unclashed(run, result, out)
            else:
                reply = rewidened(run, result, others)
                if reply.status == "abort" or not reply.tikz:
                    print(f"  {key}: kept at {result.width}; the wider draw was refused", flush=True)
                    return result
                # Only now is the figure a wide one: the measure it is held to changes
                # with the picture, so that everything asked about it afterwards — the
                # repair, the relabel — is asked at the measure it is actually drawn to.
                result.wide = True
                result.tikz = reply.tikz
                filed(result, out)
                mended(run, result, out)
                if result.drawn:
                    faithful(run, result, out)
                if result.drawn:
                    unclashed(run, result, out)
                    mended(run, result, out)
        except RuntimeError as error:
            result.status, result.detail = "failed", str(error)
        result.seconds = round(result.seconds + time.perf_counter() - started, 1)
        across = f"{result.ink[0]:.0f}mm" if result.ink else "nothing"
        print(
            f"  {key}: {result.width} now, {across} of it "
            f"({was:.1f} to {result.ratio:.1f} wide-to-tall)",
            flush=True,
        )
        return result

    with ThreadPoolExecutor(max_workers=min(len(wanted), MAX_WORKERS)) as pool:
        return list(pool.map(bigger, sorted(wanted)))


def mended(run: Run, result: Result, out: Folders) -> Result:
    """Ask again while the engine refuses the picture.

    Apart from `draw` because two callers want it: a figure being drawn now, and a
    figure of a finished run being mended after the fact.
    """
    for round_number in range(result.repairs + 1, result.repairs + run.rounds + 1):
        if result.drawn:
            break
        print(f"  {result.key}: {result.detail}; asking again ({round_number})", flush=True)
        again = repaired(
            run, result.figure, result.tikz, result.report, round_number, result.wide
        )
        result.repairs = round_number
        if again.status == "abort" or not again.tikz:
            result.status = "aborted"
            result.detail = again.abort_reason or "no reason given"
            break
        result.tikz = again.tikz
        filed(result, out)
    return result


def plotted(run: Run, figure: dict, wide: bool, rtl: bool) -> DrawReply:
    """TikZ from structured numbers, for types that are not asked of the model."""
    if run.figure_type in DETERMINISTIC_TYPES:
        built = grid.drawn(figure, rtl=rtl)
        if not built.ok:
            return DrawReply(status="abort", abort_reason=built.reason or "no reason given")
        return DrawReply(status="ok", tikz=built.tikz)
    drawer = local_drawer(run.figure_type)
    try:
        return DrawReply(status="ok", tikz=drawer.tikz_of(figure, wide=wide))
    except drawer.CannotDraw as error:
        return DrawReply(status="abort", abort_reason=str(error))


def draw(run: Run, figure: dict, out: Folders) -> Result:
    """Draw one figure, compile it, look at what came out, and file all of it.

    Two things are asked again, and they are different questions. The engine
    refusing the picture is a fault in the TikZ, and the model is shown the error.
    The picture printing one thing on top of another is a fault in the layout, and
    the model is shown the picture. Whether the figure is the right figure is a
    third question, asked after it compiles, against the description and the answer.
    """
    started = time.perf_counter()
    result = Result(figure=figure, wide=born_wide(run.figure_type, figure))
    try:
        reply = (
            plotted(run, figure, result.wide, out.profile.rtl)
            if plotted_without_model(run.figure_type)
            else asked(run, figure, result.wide)
        )
        if reply.status == "abort":
            result.status = "aborted"
            result.detail = reply.abort_reason or "no reason given"
            result.seconds = time.perf_counter() - started
            return result

        result.tikz = reply.tikz
        filed(result, out)
        if plotted_without_model(run.figure_type):
            if result.drawn and is_local(run.figure_type):
                unclashed(run, result, out)
        else:
            mended(run, result, out)
            if result.drawn:
                faithful(run, result, out)
            if result.drawn:
                unclashed(run, result, out)
                # A picture laid out again may not compile; that is the other loop's
                # question, so it is handed back to it.
                mended(run, result, out)
        # Every round this stage has to spend on the figure has been spent, whatever
        # state it ended in. The widening may still ask for it again, and that is part
        # of the same run; what is settled is that the next run will not.
        result.settled = True
    except RuntimeError as error:
        result.status, result.detail = "failed", str(error)

    result.seconds = time.perf_counter() - started
    return result


def filed(result: Result, out: Folders) -> Result:
    """Compile one picture, measure it, and keep what is worth keeping.

    Apart from the call, so that a picture already in hand can be set again without
    being asked for again.

    Compiled twice where it has room above or below it to lose: once to find out where
    the ink is, and once with the box brought down to it. The second compile is a second
    of the engine and buys a figure that carries no space of its own into whatever sets
    it.

    The document and the PDF are made somewhere temporary and go with it. They are how a
    picture is measured, not anything to keep: what the library holds is the picture as
    source, an image of it to look at, and the size it came to. A picture that has to be
    compiled again is compiled from the source, which is the same two seconds of the
    engine and cannot be a stale file pretending to be a fresh one.
    """
    result.tikz = untrimmed(result.tikz)
    result.touched = True
    try:
        with tempfile.TemporaryDirectory(prefix=f"draw-{result.key}-") as workroom:
            tex_path = Path(workroom) / f"{result.key}.tex"
            tex_path.write_text(
                drawing_tex(result.tikz, profile=out.profile), encoding="utf-8"
            )
            pdf = compiled(tex_path)
            if box := ink_box(pdf):
                page = pdf_size(pdf)
                bottom, top = box[1], page[1] - box[3]
                if max(top, bottom) > TRIM_LEAST:
                    result.tikz = tight_box(result.tikz, top, bottom)
                    tex_path.write_text(
                        drawing_tex(result.tikz, profile=out.profile), encoding="utf-8"
                    )
                    pdf = compiled(tex_path)
            result.png = rendered(pdf, into=out.root)
            result.ink = shape_of(pdf)
            result.size = page_size(pdf)
            result.compiled = True
        result.status, result.detail = "drawn", ""
        result.report = ""
    except CompileError as error:
        result.png = None
        result.ink = result.size = None
        result.compiled = False
        result.status, result.detail = "failed", f"the picture {error.summary}"
        result.report = error.report
    return result


def figure_type_prompt_path(figure_type: str) -> Path:
    """Where the rules for one type of figure are kept."""
    return FIGURE_TYPES_DIR / f"{figure_type}.md"


def read_figure_type_prompt(figure_type: str) -> str:
    """The rules particular to this type of figure.

    A type nobody has written rules for yet is drawn on the general rules alone,
    which is a real gap rather than an error — the figures come out, and they come
    out without the conventions of their sort — so it says so loudly and goes on.
    """
    path = figure_type_prompt_path(figure_type)
    if not path.exists():
        written = (
            sorted(p.stem for p in FIGURE_TYPES_DIR.glob("*.md"))
            if FIGURE_TYPES_DIR.exists() else []
        )
        print(
            f"  ! no rules for {figure_type} at {path.relative_to(PROJECT_ROOT)}; drawing on the"
            f" general rules alone. There are rules for {', '.join(written) or 'no type yet'}.",
            flush=True,
        )
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise RuntimeError(f"cannot read the rules at {path}: {error}") from error


def read_prompts(figure_type: str, meta: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Every prompt a run is drawn against, read once and up front.

    Together, so that a missing or unreadable prompt stops the run before it has
    called anything rather than partway down the list.

    The drawing prompt is assembled from the parts the book's language, numerals and
    notation choose, and the type's own rules are folded into it here and not at the
    call, because the drawing prompt heads every later call about the same figure
    too — the repair, the relabel, the pin, the widening all say "everything above
    still holds" and mean it — so folding them in once is what keeps a figure being
    mended under the same conventions it was drawn under.

    Returns the prompts by step, and the files the drawing prompt was made from, for
    the logs to record.
    """
    assembled = assemble(PROMPT_STAGE, meta)
    prompts = {"draw": assembled.text}
    files = assembled.files
    for name, path in (
        ("repair", REPAIR_PROMPT_PATH),
        ("relabel", RELABEL_PROMPT_PATH),
        ("pin", PIN_PROMPT_PATH),
        ("widen", WIDEN_PROMPT_PATH),
        ("check", CHECK_PROMPT_PATH),
    ):
        try:
            prompts[name] = path.read_text(encoding="utf-8")
        except OSError as error:
            raise RuntimeError(f"cannot read the prompt at {path}: {error}") from error

    if FIGURE_TYPE_SLOT not in prompts["draw"]:
        raise RuntimeError(
            f"{assembled.parts[0]} has no {FIGURE_TYPE_SLOT} in it, so the rules for"
            f" {figure_type} would be dropped silently"
        )
    prompts["draw"] = prompts["draw"].replace(
        FIGURE_TYPE_SLOT, read_figure_type_prompt(figure_type)
    )
    if figure_type_prompt_path(figure_type).exists():
        files.append(str(figure_type_prompt_path(figure_type).relative_to(PROJECT_ROOT)))
    return prompts, files


# What a drawn figure holds on disk, and the least of it that makes a row worth
# believing. `width` and `height` are the page the picture compiled to, in the paper's
# own points, which is what the page fitter holds room by; `settled` says the figure has
# been asked as far as it is going to be asked, so a run that finds a collision left in
# it leaves it alone rather than paying for the same three rounds every time.
FIGURE_KEYS = (
    "key", "description", "figure_type", "tikz", "width", "height",
    "wide", "ink_mm", "ratio", "overlaps", "settled",
    "invalid_source", "check_problems", "grid", "graph", "strip", "provenance",
)
FIGURE_REQUIRED = ("key", "description", "tikz")


def library_row(result: Result) -> dict:
    """One drawn figure as the library keeps it.

    Everything here is either what sets the figure or what decides whether it is worth
    drawing again. What became of the run that drew it — how long it took, how many
    rounds it cost — is in the call logs, which is where a question about a run belongs;
    a library row is read by the stage that sets the paper and by the next run of this
    one, and neither has any use for the timings.
    """
    width, height = result.size or (0.0, 0.0)
    row = {
        "key": result.key,
        "description": result.figure.get("description", ""),
        "figure_type": result.figure_type,
        "tikz": result.tikz,
        "width": round(width, 2),
        "height": round(height, 2),
        "wide": result.wide,
        "ink_mm": [round(number, 1) for number in result.ink] if result.ink else None,
        "ratio": round(result.ratio, 2) if result.ink else None,
        "overlaps": len(result.clash.hard) if result.clash is not None else None,
        "settled": result.settled,
        "invalid_source": result.invalid_source,
        "check_problems": result.check_problems or None,
    }
    for name in ("grid", "graph", "strip"):
        if isinstance(result.figure.get(name), dict):
            row[name] = result.figure[name]
    if isinstance(result.figure.get("provenance"), dict):
        row["provenance"] = result.figure["provenance"]
    return row


def file_row(result: Result, out: Folders) -> None:
    """Write one figure's row, or take it away where the figure could not be drawn.

    A figure that would not compile leaves nothing behind: the paper sets a figure it
    has no drawing for as the description it was to be drawn from, which is the right
    thing to print and better than a row holding TikZ that does not build. Its image
    goes too, so that the folder cannot show yesterday's picture of a figure that no
    longer has one.

    A figure this run never touched is left exactly as it is, which is not the same
    thing: it has a drawing already, and the run only read it back to measure a set by.
    """
    if not result.touched:
        return
    if not result.drawn:
        out.row(result.key).unlink(missing_ok=True)
        out.png(result.key).unlink(missing_ok=True)
        return
    destination = out.row(result.key)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(library_row(result), ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
        delete=False,
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(destination)


def new_label_overlaps(found: int, source: int) -> int:
    """How many hard label overlaps the translation added on top of the source."""
    return max(0, int(found) - max(0, int(source)))


def source_hard_overlaps(tikz: str, profile) -> int | None:
    """Hard overlaps in a source picture, compiled under the same profile as the translation.

    ``None`` when the picture cannot be taken apart, so the caller keeps its
    stored allowance rather than treating a failed measurement as zero.
    """
    if not str(tikz or "").strip():
        return 0
    if not shutil.which(LATEX_ENGINE):
        return None
    with tempfile.TemporaryDirectory(prefix="clash-src-") as workroom:
        report = looked_at(
            picture=standard_lines(latex.profile_tikz(tikz.strip(), profile)),
            preamble=drawing_preamble(profile),
            border=f"{BODY_SIZE}pt,border={FIGURE_BORDER}",
            folder=Path(workroom),
            engine=LATEX_ENGINE,
        )
    if report is None:
        return None
    return len(report.hard)


def same_picture(first: str, second: str) -> bool:
    """The same picture, apart from spacing and the box brought down to its ink."""

    def plain(tikz: str) -> str:
        return re.sub(r"\s+", "", untrimmed(picture_only(tikz)))

    return bool(first.strip()) and plain(first) == plain(second)


def adopt_tikz(
    book_id: str,
    description: str,
    figure_type: str,
    tikz: str,
    *,
    figure: dict | None = None,
    wide: bool = False,
    key: str | None = None,
    allowed_overlaps: int = 0,
    reference_tikz: str = "",
) -> dict:
    """Compile translated TikZ under this book's profile, with no model calls.

    The geometry is already decided; this only sets it in the Arabic face and
    measures the box the paper will hold. A picture that will not compile is a
    RuntimeError, because there is no repair loop here.

    ``reference_tikz`` is the source layout the picture was copied from. A
    picture that compiles but cannot be taken apart is still adopted, with a
    warning, when it prints exactly the reference's labels: nothing it prints
    was laid out anew, so there is nothing new to collide.
    """
    if not shutil.which(LATEX_ENGINE):
        raise RuntimeError(f"{LATEX_ENGINE} is not on the path; install TeX Live")
    out = Folders.under(book_id)
    record = dict(figure or {})
    record["description"] = description
    record["figure_type"] = figure_type
    record["key"] = key or figure_key(description)
    result = Result(
        figure=record,
        tikz=picture_only(tikz) if tikz.strip() else tikz,
        wide=wide,
    )
    filed(result, out)
    inspected(result, out)
    if result.clash is None and r"\node" in result.tikz:
        # Under many parallel engines a compile or a measurement can fail once
        # for reasons that have nothing to do with the picture.
        filed(result, out)
        inspected(result, out)
    if result.drawn and result.clash is None and r"\node" in result.tikz:
        if not (reference_tikz and same_picture(reference_tikz, result.tikz)):
            raise RuntimeError(
                f"{result.key}: translated TikZ could not be checked for label collisions"
            )
        print(
            f"  ? {result.key}: adopted unchecked; it prints the same labels as "
            "the source layout",
            flush=True,
        )
    allowed = max(0, int(allowed_overlaps))
    if result.clash is not None:
        if reference_tikz.strip():
            source = source_hard_overlaps(reference_tikz, out.profile)
            if source is not None:
                allowed = max(allowed, source)
            elif same_picture(reference_tikz, result.tikz):
                allowed = max(allowed, len(result.clash.hard))
        extra = new_label_overlaps(len(result.clash.hard), allowed)
        if extra:
            detail = result.clash.wording()
            raise RuntimeError(
                f"{result.key}: translated TikZ has {extra} new label overlap(s) "
                f"({len(result.clash.hard)} total, {allowed} already in the source)"
                f"{f': {detail}' if detail else ''}"
            )
    result.settled = True
    file_row(result, out)
    if not result.drawn:
        raise RuntimeError(
            f"{result.key}: translated TikZ {result.detail or 'does not compile'}"
        )
    return library_row(result)


def unclash_adopted(
    book_id: str,
    description: str,
    figure_type: str,
    tikz: str,
    *,
    figure: dict | None = None,
    wide: bool = False,
    key: str | None = None,
    client: genai.Client | None = None,
    logger: ApiLogger | None = None,
    model: str = DEFAULT_MODEL,
    thinking: str = DEFAULT_THINKING,
) -> dict:
    """Relabel a translated picture that picked up overlaps the English source did not have.

    Translation copies English anchors; Arabic unit words are longer, so two
    labels that sat clear as ``N`` can meet as ``نيوتن``. The lengthmark nudge
    cannot move a ``\\node``. This is the same measured relabel/pin loop a
    figure gets when it is drawn, then the row is filed even if a collision
    remains — leftover ink is not a reason to drop the picture.
    """
    if client is None:
        raise RuntimeError("cannot relabel translated TikZ without a model client")
    type_name = str(figure_type or "other").replace(" ", "_")
    prompts, prompt_files = read_prompts(type_name, book_metadata(book_id))
    run = Run(
        client=client,
        logger=logger,
        figure_type=type_name,
        model=model,
        thinking=thinking,
        prompt=prompts["draw"],
        prompt_files=tuple(prompt_files),
        repair_prompt="",
        relabel_prompt=prompts["relabel"],
        pin_prompt=prompts["pin"],
        wide_prompt="",
        check_prompt="",
        rounds=0,
        clash_rounds=CLASH_ROUNDS,
        check_rounds=0,
        wide_ratio=0,
    )
    out = Folders.under(book_id)
    record = dict(figure or {})
    record["description"] = description
    record["figure_type"] = figure_type
    record["key"] = key or figure_key(description)
    provenance = record.get("provenance")
    if isinstance(provenance, dict):
        record["provenance"] = {**provenance, "relabelled": True}
    else:
        record["provenance"] = {"relabelled": True}
    result = Result(
        figure=record,
        tikz=picture_only(tikz) if tikz.strip() else tikz,
        wide=wide,
    )
    filed(result, out)
    if not result.drawn:
        raise RuntimeError(
            f"{result.key}: translated TikZ {result.detail or 'does not compile'}"
        )
    unclashed(run, result, out)
    result.settled = True
    file_row(result, out)
    return library_row(result)


def figure_rows(book_id: str) -> list[tuple[Path, dict]]:
    """Every figure drawn for a book, with the path it is filed at."""
    rows = []
    for path in sorted((OUTPUT_DIR / book_id / FIGURES_STAGE).glob("*.json")):
        if path.name in (CHECK_REPORT, OLD_INVALID_SOURCE_REPORT):
            continue
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            if str(row.get("tikz", "")).strip():
                rows.append((path, row))
        except (OSError, LookupError, TypeError, ValueError):
            continue
    return rows


def library(book_id: str) -> latex.Drawings:
    """Every figure drawn for a book so far, keyed by the description it came from.

    Keyed by description because that is the only thing the drawing was made from, so
    the paper can be set with whatever drawings exist and nothing has to be threaded
    here from the stage that made them.
    """
    return {row["description"]: row for _, row in figure_rows(book_id)}


def measure_library(book_id: str) -> int:
    """Fill in the size of any drawing filed without one, by compiling it again.

    A drawing keeps the size it came out at so that the paper can hold room for it.
    Measuring is a compile and no call, so a row that lost its measurements — or was
    written by a stage that did not record them — is compiled rather than drawn again.
    """
    todo = [
        (path, row) for path, row in figure_rows(book_id)
        if not (row.get("width") and row.get("height"))
    ]
    if not todo:
        return 0
    if not shutil.which(LATEX_ENGINE):
        raise RuntimeError(f"{LATEX_ENGINE} is not on the path; install TeX Live")
    profile = Profile.of(book_metadata(book_id))

    def measure(item: tuple[Path, dict]) -> bool:
        path, row = item
        try:
            with tempfile.TemporaryDirectory(prefix="measure-") as workroom:
                tex_path = Path(workroom) / f"{row['key']}.tex"
                tex_path.write_text(
                    drawing_tex(row["tikz"], profile=profile), encoding="utf-8"
                )
                width, height = page_size(compiled(tex_path))
        except (CompileError, OSError):
            return False
        row["width"], row["height"] = round(width, 2), round(height, 2)
        path.write_text(
            json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return True

    with ThreadPoolExecutor(max_workers=LATEX_LANES) as pool:
        return sum(pool.map(measure, todo))


def drawn_well(out: Folders, figure: dict) -> bool:
    """Whether the figure filed for this description is one to leave alone.

    The description is checked against the row and not only the key, because the key is
    a hash of the description and a row whose two disagree is a row written for
    something else. What the picture is then judged on is the shape of the row rather
    than the picture: a drawing this stage would refuse today is a drawing to ask for
    again, which is how a change to the rules reaches the figures already drawn without
    every one of them being paid for a second time to find out.
    """
    try:
        row = json.loads(out.row(str(figure["key"])).read_text(encoding="utf-8"))
        if not set(FIGURE_REQUIRED) <= set(row) <= set(FIGURE_KEYS):
            return False
        if row["description"] != figure.get("description"):
            return False
        if not str(row["tikz"]).strip():
            return False
        return bool(row.get("settled"))
    except (OSError, LookupError, TypeError, ValueError):
        return False


def one_line(text: str, room: int = 70) -> str:
    """A description as much of it as fits on the end of a line."""
    collapsed = " ".join(str(text).split())
    return collapsed if len(collapsed) <= room else collapsed[: room - 1] + "…"


def type_slug(name: str) -> str:
    """Folder form of a figure type: spaces and underscores are the same type."""
    return str(name or "").replace(" ", "_")


def results_from_library(
    book_id: str,
    figure_types: list[str] | None = None,
    keys: list[str] | None = None,
) -> list[Result]:
    """Drawings already filed under 11_, as results that can be compiled again.

    Rebuild and mend read the pictures themselves. A translated book may have
    no 10_ catalog — extract never ran — and still have a library of TikZ.
    """
    allowed = {type_slug(one) for one in figure_types} if figure_types else None
    want = set(keys) if keys else None
    results: list[Result] = []
    for _, row in figure_rows(book_id):
        tikz = str(row.get("tikz") or "").strip()
        if not tikz:
            continue
        if allowed is not None and type_slug(row.get("figure_type")) not in allowed:
            continue
        key = str(row.get("key") or "")
        if want is not None and key not in want:
            continue
        results.append(
            Result(
                figure=row,
                tikz=tikz,
                status="drawn",
                wide=bool(row.get("wide")),
                settled=bool(row.get("settled")),
                invalid_source=bool(row.get("invalid_source")),
                check_problems=[
                    str(item) for item in (row.get("check_problems") or [])
                ],
            )
        )
    return results


def from_library(book_id: str, figures: list[dict], out: Folders) -> list[Result]:
    """The drawings already filed for these figures, read back as results.

    So that a picture can be compiled again, measured again or asked about again without
    being paid for again. What each was drawn from is taken from the figure record rather
    than from the row, because the later asks need the wording and the measures around
    the description and a row keeps only the description itself.
    """
    filed_rows = {row["key"]: row for _, row in figure_rows(book_id)}
    results = []
    for figure in figures:
        row = filed_rows.get(str(figure["key"]))
        if row is None:
            continue
        results.append(
            Result(
                figure=figure,
                tikz=row.get("tikz", ""),
                status="drawn",
                wide=bool(row.get("wide")),
                settled=bool(row.get("settled")),
                invalid_source=bool(row.get("invalid_source")),
                check_problems=[
                    str(item) for item in (row.get("check_problems") or [])
                ],
            )
        )
    return results


def recall_measure(book_id: str, results: list[Result]) -> None:
    """Mark library drawings as measured, without compiling them again.

    The row already holds the ink and whether the picture is the wide one.
    Compiling an already-wide partner again would only rewrite the file it
    already has.
    """
    if not results:
        return
    rows = {row["key"]: row for _, row in figure_rows(book_id)}
    for result in results:
        row = rows.get(result.key)
        if not row or not str(row.get("tikz", "")).strip():
            continue
        ink = row.get("ink_mm") or ()
        if len(ink) == 2:
            result.ink = (float(ink[0]), float(ink[1]))
        result.compiled = True


def leftover_check(result: Result) -> str | None:
    """The review verdict still standing on a compiled figure, or None when it is fine.

    Contradictory givens are invalid_source. A picture that is still mathematically
    wrong after the redraw rounds, or whose redraw was refused, is redraw. An ok
    figure, or one that never compiled, is neither.
    """
    if not result.drawn:
        return None
    if result.invalid_source:
        return "invalid_source"
    if result.check_problems:
        return "redraw"
    return None


def load_check_rows(path: Path) -> dict[str, dict]:
    """The figures already listed in a check report, keyed, or empty if it cannot be read."""
    if not path.is_file():
        return {}
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    by_key: dict[str, dict] = {}
    for row in existing.get("figures") or []:
        key = str(row.get("key") or "")
        if key:
            by_key[key] = row
    return by_key


def write_check_report(out: Folders, everything: list[Result]) -> list[Result]:
    """Merge this run's leftover review findings into the book's report file.

    Both verdicts sit in one file: invalid_source for contradictory givens, redraw
    for a picture still mathematically wrong after the redraw rounds. Touched
    figures update or leave the report; untouched ones already listed stay, so a
    run of one type does not wipe another type's findings. A leftover
    invalid_source.json from before this file held both verdicts is read once and
    taken away.
    """
    path = out.check_report()
    old_path = out.root / OLD_INVALID_SOURCE_REPORT
    by_key = load_check_rows(path)
    if not by_key:
        by_key = load_check_rows(old_path)
        for row in by_key.values():
            row.setdefault("verdict", "invalid_source")

    found: list[Result] = []
    for result in everything:
        if not result.touched:
            continue
        verdict = leftover_check(result)
        if verdict is not None:
            by_key[result.key] = {
                "key": result.key,
                "description": result.figure.get("description", ""),
                "figure_type": result.figure_type,
                "verdict": verdict,
                "problems": result.check_problems,
            }
            found.append(result)
        else:
            by_key.pop(result.key, None)

    figures = [by_key[key] for key in sorted(by_key)]
    if figures:
        path.write_text(
            json.dumps({"count": len(figures), "figures": figures},
                       ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
    else:
        path.unlink(missing_ok=True)
    old_path.unlink(missing_ok=True)
    return found


def announce(out: Folders, everything: list[Result]) -> tuple[Path, int]:
    """File every figure the run touched, say what became of them, and where to look.

    Only what it touched, in the counting as well as the filing: a run that drew four
    figures of a book that already had four hundred drew four, and reporting it as four
    of four hundred and four would bury the thing that just happened.
    """
    everything.sort(key=lambda result: result.key)
    for result in everything:
        file_row(result, out)

    results = [result for result in everything if result.touched]
    failed = [result for result in results if not result.drawn]
    repaired_after = [result for result in results if result.repairs and result.drawn]
    laid_again = [result for result in results if result.relabels]
    checked_again = [result for result in results if result.checks]
    widened = [result for result in results if result.wide]
    colliding = [
        result for result in results if result.clash is not None and not result.clash.clean
    ]
    checked = write_check_report(out, everything)
    invalid = [result for result in checked if leftover_check(result) == "invalid_source"]
    redrawn = [result for result in checked if leftover_check(result) == "redraw"]
    summary = f"{len(results) - len(failed)} of {len(results)} figure(s) drawn"
    if repaired_after:
        summary += f", {len(repaired_after)} after being asked again"
    if laid_again:
        summary += f", {len(laid_again)} laid out again"
    pinned = [result for result in results if result.pins]
    if pinned:
        summary += f", {len(pinned)} pinned"
    if checked_again:
        summary += f", {len(checked_again)} checked"
    if widened:
        summary += f", {len(widened)} widened"
    if invalid:
        summary += f", {len(invalid)} invalid source"
    if redrawn:
        summary += f", {len(redrawn)} still mathematically wrong"
    if failed:
        summary += f", {len(failed)} not"
    print(f"  {summary}", flush=True)
    if colliding:
        print(
            f"  {len(colliding)} figure(s) still print on top of themselves; "
            f"see {out.clash.relative_to(PROJECT_ROOT)}",
            flush=True,
        )
        for result in colliding:
            for line in result.clash.wording().splitlines():
                print(f"    {result.key} {line.lstrip('- ')}", flush=True)
    if checked:
        parts = []
        if invalid:
            parts.append(f"{len(invalid)} figure(s) have contradictory givens")
        if redrawn:
            parts.append(
                f"{len(redrawn)} still mathematically wrong after redraws"
            )
        print(
            f"  {'; '.join(parts)}; "
            f"see {out.check_report().relative_to(PROJECT_ROOT)}",
            flush=True,
        )
        for result in checked:
            verdict = leftover_check(result) or "redraw"
            first = result.check_problems[0] if result.check_problems else "no reason given"
            print(f"    {result.key} {verdict}: {first}", flush=True)
    return out.root, len(failed)


def asking_for(
    book_id: str, figure_type: str, model: str, thinking: str, rounds: int,
    clash_rounds: int, check_rounds: int, wide_ratio: float,
) -> Run:
    """Everything one type of figure is drawn against, settled once.

    The logger is built here rather than at the first call, because setting the clock
    asks the network and the answer is wanted once a run and not once a figure. It logs
    under the book, beside every other stage's calls, so that what a book cost is one
    folder to add up rather than one per type of figure.

    The book's language, numerals and notation come off its table of contents, the one
    place a book says what it is; a figure's labels are set in them.
    """
    # Grid is drawn from integers alone. Function graphs and strip diagrams are plotted
    # here, but their labels are still relaid through the model when they collide, so
    # they need the same client, logger and prompts as a type the model draws.
    if figure_type in DETERMINISTIC_TYPES:
        return Run(
            client=None,
            logger=None,
            figure_type=figure_type,
            model=model,
            thinking=thinking,
            prompt="",
            prompt_files=(),
            repair_prompt="",
            relabel_prompt="",
            pin_prompt="",
            wide_prompt="",
            check_prompt="",
            rounds=rounds,
            clash_rounds=clash_rounds,
            check_rounds=0,
            wide_ratio=wide_ratio,
        )
    prompts, prompt_files = read_prompts(figure_type, book_metadata(book_id))
    return Run(
        client=genai.Client(),
        logger=ApiLogger(OUTPUT_DIR / book_id),
        figure_type=figure_type,
        model=model,
        thinking=thinking,
        prompt=prompts["draw"],
        prompt_files=tuple(prompt_files),
        repair_prompt=prompts["repair"],
        relabel_prompt=prompts["relabel"],
        pin_prompt=prompts["pin"],
        wide_prompt=prompts["widen"],
        check_prompt=prompts["check"],
        rounds=rounds,
        clash_rounds=clash_rounds,
        check_rounds=check_rounds,
        wide_ratio=wide_ratio,
    )


def draw_type(
    book_id: str, figure_type: str, out: Folders, model: str, thinking: str,
    rounds: int, clash_rounds: int, check_rounds: int, wide_ratio: float | None,
    count: int = 0, keys: list[str] | None = None, widths: list[str] | None = None,
    seed: int | None = None, again: bool = False,
) -> list[Result]:
    """Draw every figure of one type this book asks for and has no drawing of.

    A figure already drawn and settled is left exactly as it is: the library is what the
    stage is building, and a figure in it has been through the compile rounds, the layout
    rounds and the widening already. `again` is how a change to the prompts reaches the
    figures drawn under the old one, and it is the only thing that will.

    The widening runs once the type is drawn rather than as each figure comes back,
    because a figure is widened from the shape it compiled to. Figures skipped as
    already drawn are read back from the library first, so a flat or tall one
    among them is still drawn to the wide measure. A wide one does not pull the
    rest of its set.
    """
    described = figures_from(figure_files(book_id, figure_type), figure_type)
    if not described:
        return []
    if wide_ratio is None:
        wide_ratio = wide_ratio_for(figure_type)

    if seed is not None:
        random.seed(seed)
    # Every figure in the order it was filed unless a run has been narrowed on purpose.
    # `chosen` samples, which is what a run made to look at a few figures wants and not
    # what a run drawing a book wants: sampling all of them would only shuffle them.
    wanted = (
        described if not (count or keys or widths)
        else chosen(described, count or len(described), keys, widths)
    )
    todo = wanted if again else [
        figure for figure in wanted if not drawn_well(out, figure)
    ]
    held = {str(figure["key"]) for figure in todo}
    standing = from_library(
        book_id, [figure for figure in wanted if str(figure["key"]) not in held], out
    )
    if not todo:
        print(
            f"{figure_type.replace('_', ' ')}: {len(wanted)} figure(s), all drawn already",
            flush=True,
        )
        return standing

    measures = [width_of(figure, born_wide(figure_type, figure)) for figure in todo]
    columns = ", ".join(
        f"{measures.count(width)} at {width}"
        for width in sorted(set(measures), key=millimetres, reverse=True)
    )
    already = len(wanted) - len(todo)
    how = (
        "deterministically"
        if figure_type in DETERMINISTIC_TYPES
        else "plotted locally"
        if plotted_without_model(figure_type)
        else f"with {model} thinking {thinking}"
    )
    print(
        f"{figure_type.replace('_', ' ')}: drawing {len(todo)} of {len(wanted)} figure(s)"
        + (f", {already} drawn already" if already else "")
        + f" — {columns} — {how}",
        flush=True,
    )

    asking = asking_for(
        book_id, figure_type, model, thinking, rounds, clash_rounds, check_rounds,
        wide_ratio
    )
    results: list[Result] = []
    with ThreadPoolExecutor(max_workers=min(len(todo), MAX_WORKERS)) as pool:
        futures = {pool.submit(draw, asking, figure, out): figure for figure in todo}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            note = result.status if result.drawn else f"{result.status}, {result.detail}"
            print(
                f"  {result.key}: {note} ({result.seconds:.0f}s), "
                f"{one_line(result.figure.get('description', ''))}",
                flush=True,
            )

    if figure_type in DETERMINISTIC_TYPES:
        return results + standing

    # A figure that came out too flat, or too tall, is drawn again to the wide
    # measure. Only that figure. Members this run did not draw are read from the
    # library so a flat or tall one among them is still caught. An already-wide
    # one is not compiled again.
    sets = grouped([one.figure for one in results + standing])
    touched: set[str] = set()
    for result in results:
        touched |= sets.get(result.key, {result.key})
    mates = [one for one in standing if one.tikz and one.key in touched]
    to_measure = [one for one in mates if not one.wide]
    if to_measure:
        print(
            f"  measuring {len(to_measure)} figure(s) drawn before, in case one is flat or tall",
            flush=True,
        )
        with ThreadPoolExecutor(max_workers=LATEX_LANES) as pool:
            list(pool.map(lambda one: filed(one, out), to_measure))
    recall_measure(book_id, [one for one in mates if one.wide])
    present = {one.key for one in results + standing}
    described_by_key = {str(figure["key"]): figure for figure in described}
    missing = [
        described_by_key[key]
        for key in sorted(touched - present)
        if key in described_by_key
    ]
    library = from_library(book_id, missing, out)
    recall_measure(book_id, library)

    widen(asking, results + mates + library, out)
    return results + standing


def draw_book(
    book_id: str, figure_types: list[str] | None, model: str, thinking: str,
    rounds: int, clash_rounds: int, check_rounds: int, wide_ratio: float | None,
    count: int = 0, keys: list[str] | None = None, widths: list[str] | None = None,
    seed: int | None = None, again: bool = False,
) -> tuple[Path, int]:
    """Draw every figure a book describes, a type at a time.

    A type at a time and not all at once, because the prompt a figure is drawn against
    is the type's, and a run that mixed them would have to read all seven before it
    called anything. Taken in order so that a run reads the same way twice.
    """
    described = figure_types_of(book_id)
    types = figure_types or described
    if not types:
        print(f"OK: {book_id} has no figures to draw", flush=True)
        return Folders.under(book_id).root, 0

    if not shutil.which(LATEX_ENGINE):
        raise RuntimeError(f"{LATEX_ENGINE} is not on the path; install TeX Live")

    if any(one not in DETERMINISTIC_TYPES for one in types):
        if not any(os.environ.get(name) for name in API_KEY_VARS):
            raise RuntimeError(f"set one of {' / '.join(API_KEY_VARS)} to call the model")
    if unknown := sorted(set(types) - set(described)):
        raise RuntimeError(
            f"{book_id} describes no {', '.join(unknown)} figure; it describes "
            f"{', '.join(described)}"
        )

    out = Folders.under(book_id)
    started = time.perf_counter()
    results: list[Result] = []
    for figure_type in types:
        results += draw_type(
            book_id, figure_type, out, model, thinking, rounds, clash_rounds,
            check_rounds, wide_ratio, count, keys, widths, seed, again,
        )

    if not results:
        print(
            f"OK: {book_id} has no figures to draw "
            f"({since(time.perf_counter() - started)})",
            flush=True,
        )
        return out.root, 0
    print(
        f"OK: {book_id} in {since(time.perf_counter() - started)} under "
        f"{out.root.relative_to(PROJECT_ROOT)}",
        flush=True,
    )
    return announce(out, results)


def mend_book(
    book_id: str, figure_types: list[str] | None, model: str, thinking: str,
    rounds: int, clash_rounds: int, check_rounds: int, wide_ratio: float | None,
    keys: list[str] | None = None,
) -> tuple[Path, int]:
    """Ask again for whatever is wrong with the drawings on disk, and nothing else.

    The asking-again half of a run, pointed at a library that is already built. A book
    that came back with two bad figures out of four hundred does not need the other
    three hundred and ninety-eight drawn a second time to be worth fixing.

    Four faults are looked for: a picture the engine will not set, a picture that
    sets but prints one thing on top of another, a picture whose givens cannot
    coexist, and a picture the mathematical review still finds wrong after redraws.
    Leftover word clashes after placement trials are spent are still pinned.
    Which of the first two a figure has cannot be read off its row — the clash is
    only known by taking the figure apart — so every picture is compiled and
    measured first, all of it locally and none of it costing a call.
    """
    if not shutil.which(LATEX_ENGINE):
        raise RuntimeError(f"{LATEX_ENGINE} is not on the path; install TeX Live")
    out = Folders.under(book_id)
    standing_all = results_from_library(book_id, figure_types, keys)
    types = figure_types or sorted({
        type_slug(one.figure_type) for one in standing_all
    })
    if any(one not in DETERMINISTIC_TYPES for one in types):
        if not any(os.environ.get(name) for name in API_KEY_VARS):
            raise RuntimeError(f"set one of {' / '.join(API_KEY_VARS)} to call the model")
    mended_any: list[Result] = []
    by_type: dict[str, list[Result]] = {}
    for one in standing_all:
        by_type.setdefault(type_slug(one.figure_type), []).append(one)
    for figure_type in types:
        standing = by_type.get(type_slug(figure_type), [])
        if not standing:
            continue

        print(
            f"{figure_type.replace('_', ' ')}: looking over {len(standing)} drawing(s)",
            flush=True,
        )
        for result in standing:
            result.tikz = picture_only(result.tikz)
            result.repairs = 0
        with ThreadPoolExecutor(max_workers=min(len(standing), MAX_WORKERS)) as pool:
            list(pool.map(lambda one: inspected(filed(one, out), out), standing))

        broken = [
            result for result in standing
            if (not result.drawn and rounds)
            or (result.drawn and result.clash and not result.clash.clean)
            or (result.drawn and check_rounds and leftover_check(result) is not None)
        ]
        if not broken:
            # Nothing to ask, which is the good outcome and not an error. The rows are
            # still written, because the pass above measured every figure afresh:
            # throwing that away would leave a row saying a figure had never been
            # checked when it had, and been found clean.
            print(
                "  nothing to mend: every picture compiles, prints clear, "
                "and matches its givens",
                flush=True,
            )
            mended_any += standing
            continue

        if plotted_without_model(figure_type):
            print(
                f"  {len(broken)} figure(s) still wrong; "
                f"{figure_type.replace('_', ' ')} is drawn by rule, "
                "so there is no call that would mend it",
                flush=True,
            )
            mended_any += standing
            continue

        asking = asking_for(
            book_id, figure_type, model, thinking, rounds, clash_rounds, check_rounds,
            wide_ratio_for(figure_type) if wide_ratio is None else wide_ratio,
        )
        print(f"  mending {len(broken)} of {len(standing)}", flush=True)

        def fix(result: Result, asking: Run = asking) -> Result:
            started = time.perf_counter()
            try:
                mended(asking, result, out)
                if result.drawn:
                    faithful(asking, result, out)
                if result.drawn:
                    unclashed(asking, result, out)
                    mended(asking, result, out)
            except RuntimeError as error:
                result.status, result.detail = "failed", str(error)
            result.seconds = round(result.seconds + time.perf_counter() - started, 1)
            print(
                f"  {result.key}: "
                f"{'drawn' if result.drawn else f'{result.status}, {result.detail}'}",
                flush=True,
            )
            return result

        with ThreadPoolExecutor(max_workers=min(len(broken), MAX_WORKERS)) as pool:
            list(pool.map(fix, broken))
        widen(asking, [one for one in standing if one.drawn], out)
        mended_any += standing

    if not mended_any:
        raise RuntimeError(f"no drawing filed for {book_id} to mend")
    return announce(out, mended_any)


def rebuild_book(
    book_id: str, figure_types: list[str] | None, keys: list[str] | None = None,
) -> tuple[Path, int]:
    """Compile every drawing on disk again, from the pictures themselves, calling nothing.

    For when the fault was in the reading or the setting rather than in the drawing: a
    picture that arrived sound and was compiled or measured wrongly is put right here,
    and the figures around it are only doing the same work twice, which is a second or
    two of the engine rather than another call. This is also what carries a change to
    how a figure is set — a weight, a style, the room its box is brought down to —
    through to every figure already drawn.
    """
    if not shutil.which(LATEX_ENGINE):
        raise RuntimeError(f"{LATEX_ENGINE} is not on the path; install TeX Live")
    out = Folders.under(book_id)
    results = results_from_library(book_id, figure_types, keys)
    if not results:
        raise RuntimeError(f"no drawing filed for {book_id} to compile again")

    print(f"compiling {len(results)} picture(s) again in {LATEX_LANES} lane(s)", flush=True)
    for result in results:
        # Read again as well as set again, since a reply read wrongly is the sort of
        # fault this exists to put right.
        result.tikz = picture_only(result.tikz)
    with ThreadPoolExecutor(max_workers=LATEX_LANES) as pool:
        for result in pool.map(lambda one: inspected(filed(one, out), out), results):
            if not result.drawn:
                print(f"  {result.key}: {result.status}, {result.detail}", flush=True)
    return announce(out, results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_id", help="which book's figures to draw")
    parser.add_argument(
        "-t", "--figure-type", action="append", metavar="TYPE",
        help="draw only this type of figure; may be repeated (default: every type the "
             "book describes)",
    )
    parser.add_argument(
        "-n", "--count", type=int, default=0, metavar="N",
        help="draw at most this many figures of each type, per width where widths are "
             "named, chosen at random (default: every figure the book describes)",
    )
    parser.add_argument(
        "-w", "--width", action="append", metavar="MM",
        help="draw only figures of this column, e.g. -w 84mm -w 74mm; may be repeated",
    )
    parser.add_argument(
        "--key", action="append",
        help="draw this figure by key rather than every figure; may be repeated",
    )
    parser.add_argument(
        "--seed", type=int, help="choose the same figures on every run with this seed"
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"model to call (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--thinking-level", default=DEFAULT_THINKING, choices=THINKING_LEVELS,
        help=f"how hard the model thinks while drawing (default: {DEFAULT_THINKING})",
    )
    parser.add_argument(
        "--repair-rounds", type=int, default=REPAIR_ROUNDS, metavar="N",
        help=f"how many times to show a picture its own LaTeX error and ask again; "
             f"0 to take the first reply whatever it is (default: {REPAIR_ROUNDS})",
    )
    parser.add_argument(
        "--clash-rounds", type=int, default=CLASH_ROUNDS, metavar="N",
        help=f"how many times to show a picture where it prints on top of itself and "
             f"ask again; 0 skips those placement trials, then leftover word labels "
             f"are still pinned (default: {CLASH_ROUNDS})",
    )
    parser.add_argument(
        "--check-rounds", type=int, default=CHECK_ROUNDS, metavar="N",
        help=f"how many times to redraw a picture the mathematical review finds "
             f"wrong; 0 to skip the review (default: {CHECK_ROUNDS})",
    )
    parser.add_argument(
        "--wide-ratio", type=float, default=None, metavar="R",
        help="draw again to the wide measure any figure at least this many times "
             "wider than it is tall, or this many times taller than it is wide; "
             "0 to leave every figure at its column "
             "(default: " + str(WIDE_RATIO) + ", and "
             + ", ".join(f"{ratio} for {figure_type}" for figure_type, ratio
                         in sorted(WIDE_RATIO_BY_FIGURE_TYPE.items()))
             + "; no effect on the types drawn wide to begin with: "
             + ", ".join(sorted(BORN_WIDE)) + ")",
    )
    parser.add_argument(
        "--again", action="store_true",
        help="draw every figure again, including the ones already drawn, which is how a "
             "change to the prompts reaches the figures drawn under the old one",
    )
    parser.add_argument(
        "--mend", action="store_true",
        help="ask again only for the drawings on disk that will not compile, that "
             "print on top of themselves, or that have contradictory givens, leaving "
             "the rest as they are",
    )
    parser.add_argument(
        "--rebuild", action="store_true",
        help="compile and measure every drawing on disk again, calling nothing",
    )
    args = parser.parse_args()

    if args.repair_rounds < 0:
        print("FAILED: --repair-rounds cannot be negative", file=sys.stderr)
        return 1
    if args.clash_rounds < 0:
        print("FAILED: --clash-rounds cannot be negative", file=sys.stderr)
        return 1
    if args.check_rounds < 0:
        print("FAILED: --check-rounds cannot be negative", file=sys.stderr)
        return 1
    if args.wide_ratio is not None and args.wide_ratio < 0:
        print("FAILED: --wide-ratio cannot be negative", file=sys.stderr)
        return 1
    if args.mend and args.rebuild:
        print("FAILED: --mend calls the model and --rebuild does not; pick one", file=sys.stderr)
        return 1

    try:
        if args.rebuild:
            _, failed = rebuild_book(args.book_id, args.figure_type, args.key)
        elif args.mend:
            _, failed = mend_book(
                args.book_id, args.figure_type, args.model, args.thinking_level,
                args.repair_rounds, args.clash_rounds, args.check_rounds,
                args.wide_ratio,
                args.key,
            )
        else:
            _, failed = draw_book(
                args.book_id, args.figure_type, args.model, args.thinking_level,
                args.repair_rounds, args.clash_rounds, args.check_rounds,
                args.wide_ratio,
                args.count, args.key, args.width, args.seed, args.again,
            )
    except RuntimeError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
