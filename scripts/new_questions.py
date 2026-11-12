#!/usr/bin/env python3
"""Write a section's worth of new practice exercises.

One call per objective, asking for the number of exercises the budget gave that
objective and eight more. The model sees what the section teaches, the wording and
notation it uses, and the questions the book's own textbooks ask for that
objective — as examples of the level expected, never as material to copy.

The eight are written like the rest and marked in no way: the objective simply has
more exercises than the paper will print. They are the room the later stages need —
to leave out an exercise the proofreading could not put right, and to choose, of
the exercises there are, the ones that fill the paper's last page rather than
strand a line on it. The budget is what the paper is meant to print, and the file
records both: the budget the objective was given and the count it was written to.

Working an objective at a time is what makes the budget mean something. A call
writes for one objective and may name earlier ones on an exercise that needs
them, but never a later one, because a later objective has its own exercises
still to come. The objective being written for is therefore always the latest
one an exercise names, so the exercise counts against it and against nothing
else, and the section's hundred exercises fall exactly as the budget divided
them.

Three stages come first: review.py for the spec, budgets.py for the counts, and
source_questions.py for the examples. Every objective of every section runs
concurrently, and a section's file is written once all of its objectives have landed.
Objectives that land before a sibling fails are kept in a sidecar under the stage
folder so the next run calls only what is still missing, rather than spending them.

Nothing here is typeset. questions_review.py reads these files next: it corrects
what a rule can correct, works the proofread exercises and reports the ones that
are wrong, one call per exercise, then leaves the flagged ones out. extract.py writes out
the figures the exercises describe, draw.py draws them and papers.py sets the
paper, so the exercises can be rewritten without redrawing and the paper can be
set again without either.

By default objectives use the Gemini Batch API. ``--no-batch`` keeps the
previous unary thread pool. In-flight jobs are saved under the book output so a
stopped run resumes without submitting the same objective again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    from google import genai
    from google.genai import types
    from pydantic import BaseModel, Field, PrivateAttr, ValidationError, model_validator

    from src.api_logger import (
        ApiLogger,
        finish_reason,
        genai_answer_text,
        genai_response_files,
        genai_usage,
        safe_log,
    )
    from src.gemini_batch import (
        BATCH_FAILED,
        FINISH_DONE,
        FULL_GET_TIMEOUT_MS,
        BatchChunk,
        BatchLock,
        BatchState,
        BatchStatus,
        batch_display_name,
        batch_lock_path,
        batch_state_path,
        chunk_requests,
        delete_files,
        entries,
        find_jobs_by_display_names,
        finish_token,
        generate_content_response,
        get_batch_job,
        get_batch_status,
        inlined_responses,
        is_legacy,
        map_rows_by_key,
        new_run_id,
        pick_duplicate_match,
        response_and_error,
        result_file_name,
        run_batch_jobs,
        src_for_chunk,
    )
    from budgets import BUDGETS_STAGE
    from src.latex import DOUBLED_COMMAND, house_wording_square_problems, single_commands
    from src.arabic_verse import POEM_MARKER, Poem, poem_problems
    from src.geometry_arrows import canonicalize_document, canonicalize_tree, section_is_vector_lesson
    from src.prompting import Profile, Prompt, assemble
    from source_questions import QUESTIONS_STAGE
    from review import book_attributes, reviewed_book
    from specs import Progress, in_kb, one_line, since
    from sources import PROJECT_ROOT, OUTPUT_DIR, book_metadata, matching_sections
except ModuleNotFoundError as error:  # pragma: no cover
    sys.exit(f"missing dependency ({error.name}): pip install -r requirements.txt")

# The prompt is assembled from prompts/new_questions/ by prompting.py — mathematics
# uses the stage root; Arabic, English, and Science use subject/ overlays — from the
# parts the book's language, numerals and notation choose; the set sizes are filled in
# here, since they are this stage's own.
PROMPT_STAGE = "new_questions"

EXERCISES_STAGE = "08_section_new_questions"
# Finished objectives of a section still being written; the stage folder holds whole
# papers only, so what landed is kept here until every objective of the section lands.
PARTIAL_DIR = ".partial"
WRITE_STEP = "section_exercises"

# What a section's exercises hold on disk. Kept here rather than read off a
# dumped reply, so that the skip check and the writer are held to one list.
EXERCISE_KEYS = ("total", "budget", "word_problems", "objectives", "groups")

# How many exercises more than its budget every objective is written to. The paper
# prints the budget; these are what it chooses from, and what it can leave out. Any
# kind of exercise, word problems among them: the model is asked for the larger count
# and writes it as it would write the smaller, so the extras are not a kind of their
# own.
EXTRA_EXERCISES = 8

# The sizes a set may be, and nothing between them. A set is laid out as a block of
# cells, two or four across, so a count that divides by four fills its last row and
# any other count leaves a gap at the end of the set. Twelve is the most: past that a
# stem stops being one instruction and starts being a page, and a longer list is
# printed as consecutive sets sharing the stem, which is what the book itself does.
SET_SIZES = (4, 8, 12)

# What a set may be when its exercises carry something tall: a figure, or a table of
# its own. Four, because either is tall where a line of maths is not — four figures
# are two rows of two, and four frequency distributions are four blocks of ruled
# cells — and a set of eight of them is most of a page under a single stem. A table
# shared by the whole set is another matter: the stem prints it once, and the
# exercises under it are as short as any others.
TALL_SET_SIZES = (4,)

# How many choices a multiple-choice exercise offers. One number, because a paper
# whose questions offer four choices in one place and three in another is asking
# the student to read the page rather than the mathematics.
MCQ_OPTIONS = 4
# An answer that chooses by letter: the letter alone, or in brackets, or the letter and
# then the reason after a comma or a stop — "C", "(ج)", "ب، لأن …". The letters are the
# ones the papers print beside the choices, in either language. An opening bracket must
# have its closing one, so that "(أ، ب)" — a pair, in an answer that lists pairs — is
# not read as the choice أ.
CHOICE_LETTER_CLASS = "[A-Fأبجدهو]"
CHOICE_ANSWER = re.compile(
    rf"^\s*(?:[(\[]\s*({CHOICE_LETTER_CLASS})\s*[)\]]|({CHOICE_LETTER_CLASS}))\s*(?:$|[,،.:)\]])"
)
# A letter the model wrote onto a choice that the schema already asked to be
# unlabelled. "(B) Correct English (punctuation and spelling)" is the checklist
# point; the "(B)" is the paper's own numbering leaking into the answer column.
OPTION_LABEL = re.compile(
    rf"^\s*(?:[(\[]\s*{CHOICE_LETTER_CLASS}\s*[)\]]|{CHOICE_LETTER_CLASS}\s*[.)])\s*"
)


def chosen_letter(answer: str) -> str:
    """The choice letter an answer names, or "" where it names none."""
    found = CHOICE_ANSWER.match(answer)
    return (found.group(1) or found.group(2)) if found else ""

# The words a stem is built from when it says nothing its exercises do not already
# say. "Choose the correct answer from the given options:" tells a student what the
# four printed choices tell them, and "Complete each of the following:" what the
# blank tells them; a stem drawn wholly from these words earns no set. One that
# reaches past them keeps its set, either because it names a task the exercises
# could not imply — "Complete each of the following using Pythagorean triples:" —
# or because it names something they share. Maths in a stem counts as reaching
# past them: it is there to say what goes where.
EMPTY_STEM_WORDS = frozenset(
    """a all an and answer answers as blank blanks choice choices choose complete
    correct each fill following for from given in is it its make of one option
    options pick right select sentence sentences statement statements the them
    then these this to true with""".split()
)

# Maths standing outside the $...$ it belongs in. The typesetter can only take a
# field's prose for prose and escape it, so "\frac{1}{2}" written bare reaches the
# page as \frac{1}{2}, backslash and braces and all. It happens where a field is
# nothing but maths — an option reading "2\sqrt{3}", an exercise reading
# "(0.2)^{-2}" under a stem that says what to do with it — and there is no prose
# around it to remind the model of the delimiters. \blank is the one command the
# prose is meant to carry, so it is the one exception.
MATH_SEGMENT = re.compile(r"\$\$.+?\$\$|\$.+?\$", re.DOTALL)
NAKED_MATH = re.compile(r"\\(?!blank\b)[a-zA-Z]+|[\^_]\{|\^-?\w")
# A superscript or subscript written inside \text{...}, as "$\text{^\circ C}$" does:
# the braces of \text are words, not maths, and LaTeX stops on a ^ in them. The
# body is read up to the first closing brace, which is enough for the \text a unit
# is written in; one holding its own $...$ is judged by its maths instead.
SCRIPT_IN_TEXT = re.compile(r"\\text\{[^{}$]*[\^_]")
# A degree written as a filled bullet: "$48^\bullet$", "$(x+20)^\bullet$", "$3س^\bullet$".
# \bullet after a number, a close paren, or a letter is never a degree. $a \bullet b$
# (no caret) is left alone.
DEGREE_BULLET = re.compile(
    r"([0-9٠-٩A-Za-z\u0621-\u064A)])"
    r"(\s*)"
    r"\^\s*(?:\{\s*)?\\bullet(?:\s*\})?"
)
# One character written many times over, as "60%.1000000000000000…" was in an answer
# of the Saudi book: the model's output degenerating into a loop, which the reply's JSON
# survived because the loop ran inside a string. Nothing an exercise says repeats one
# character a dozen times — a row of dots is an ellipsis, a rule is \hline — so a run
# this long is a broken reply, and the whole reply is asked for again.
RUNAWAY = re.compile(r"(.)\1{11,}", re.DOTALL)
BLANK = r"\blank"
CLOSING_BLANK = re.compile(r"\\blank(?:\{\})?\s*$")
# Where the model asks for a figure to stand inside the wording; the paper puts the
# picture there and the marker itself is never read as words.
FIGURE_MARKER = re.compile(r"\[figure \d+\]")

# How a sentence may close. A question mark and an exclamation mark end one as surely
# as a full stop does, and an exercise is asked in whichever of the three suits it. The
# Arabic question mark is the one an Arabic book asks with, and the prompt for such a
# book says so; it is accepted for every book, since no English exercise will end in it.
# A closing guillemet or ASCII quote after the stop is not the end of the sentence.
SENTENCE_END = (".", "?", "!", "؟")

# A table written as Markdown rather than as LaTeX: a row of cells fenced by pipes,
# or the dashed rule a Markdown table puts under its heading. The typesetter knows
# nothing of Markdown, so a table written this way reaches the page as its own pipes
# and colons, a dozen exercises of one section of the sample book among them. The
# model writes them because the textbook questions it is shown as examples carry
# their tables that way, which is why the prompt says so and this checks it.
MARKDOWN_ROW = re.compile(r"^[ \t]*\|.*\|[ \t]*$", re.M)
MARKDOWN_RULE = re.compile(
    r"^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(\|[ \t]*:?-{3,}:?[ \t]*)*\|?[ \t]*$", re.M
)

# A table written as it should be, which is what tells a set that holds tables from
# one that holds lines of maths. There is one way to write a table and the checks
# above hold the model to it, so an array is a table and a table is an array.
LATEX_TABLE = re.compile(r"\\begin\{array\}")
UNICODE_TICK_CROSS = re.compile(r"[\u2713\u2714\u2715\u2717\u2718\u00d7]")
# A table set as display mathematics at the very end of an exercise.
CLOSING_TABLE = re.compile(r"\$\$\s*\\begin\{array\}.*?\\end\{array\}\s*\$\$\s*$", re.DOTALL)

# Words that point at something rather than say anything, and the small change of
# spelling between a stem and a member of its set: a stem speaks of "the following
# triangles" and its members of "the triangle". Both are set aside before a member is
# compared with its stem, so that pointing at the figure and naming it in the singular
# do not count as saying something the stem has not said.
POINTING_WORDS = frozenset(
    """a above adjacent an and are as at be below by diagram each figure figures
    following for from given here in is it its next of on one opposite or picture
    shown that the then there these this those to use using with""".split()
)

# Words that belong to the instruction and never to a choice. The model sometimes
# writes the paper's own scaffolding into the answer column — "Choose $20^7$." for
# a choice, "$5^4$ shall be correct." for another — and that second kind hands the
# student the answer.
OPTION_SCAFFOLD = re.compile(r"\b(choose|select|pick|correct|wrong|incorrect|answer)\b", re.I)
TEMPLATE_PLACEHOLDER = re.compile(r"\$\{[A-Za-z][A-Za-z0-9_]*\}")
GENERATOR_LEAKAGE = re.compile(
    r"preference indicator|purely mathematical value follows|"
    r"unresolved template|template variable|prompt leakage|"
    r"removed from options|placeholder|bypass",
    re.I,
)


def source_artifact_problems(text: str, where: str) -> list[str]:
    """Unfilled ``${ident}`` placeholders and generator prompt leakage."""
    problems: list[str] = []
    if found := TEMPLATE_PLACEHOLDER.search(text):
        problems.append(
            f"{where}: unfilled template placeholder {found.group()!r}"
        )
    if found := GENERATOR_LEAKAGE.search(text):
        problems.append(
            f"{where}: generator prompt leakage {found.group()!r}"
        )
    return problems

# The book's own questions for an objective, as examples. Enough to show the
# level and the range of task without spending the call on reading them.
EXAMPLE_LIMIT = 40

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_THINKING = "high"
DEFAULT_BATCH = True
DEFAULT_BATCH_PARALLEL = 0
DEFAULT_DOWNLOAD_WORKERS = 3
THINKING_LEVELS = ("minimal", "low", "medium", "high")
API_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
MAX_ATTEMPTS = 4
BATCH_STAGE = "new_questions"
# How many times a call is allowed to be told to slow down before it counts as
# failed, and the wait after the first such time, which grows with each. Long,
# because the limit met is the account's spending rate and a book with a hundred
# calls in flight is what met it: a minute or two of one worker waiting is nothing
# against a section that has to be written again. Jitter is added so that a
# hundred workers throttled together do not come back together.
RATE_LIMIT_WAITS = 12
RATE_LIMIT_PAUSE = 30.0
# One worker per objective still to be written, and the ceiling that keeps a
# book with hundreds of them from putting hundreds of calls in flight at once.
# Lower on the command line when the account's spending rate cannot carry it.
MAX_WORKERS = 25


# The types of drawing a book of this sort asks for. A figure says which it is here,
# at the point it is asked for, because this is the only place the subject of the
# drawing is known: by the time it is drawn there is a description and nothing else,
# and a type read back off finished TikZ is a guess — a triangle and a pie chart are
# both arcs and straight lines to anything reading the source.
#
# The type is what design rules are written against. Every rule about how a plane
# figure is drawn — the weight of a line, where a label sits, how an angle is marked —
# is a rule about all of them and none of the charts, and the rules a chart needs are
# about axes and ticks, which a triangle has none of. The type is what lets a rule say
# which drawings it governs instead of every drawing in the book.
#
# The list is closed, so the model chooses from it and cannot invent a name of its own:
# a type nobody has written rules for is a type that silently governs nothing. It is
# short on purpose and names subjects rather than shapes, so that it holds for the next
# book as well — circles and their chords are plane geometry, a cylinder is solid
# geometry.
#
# `other` is the way out of a closed list, and the list needs one. A book of a subject
# this list was not drawn from will ask for something absent from it — a map, a circuit,
# a Venn diagram — and without a way to say so the model must file that drawing under
# the nearest type that fits it badly. That is the worse of the two outcomes: a wrong
# type is drawn to rules made for something else and looks like an answer, while `other`
# is a count that can be looked at. What follows a figure landing there is a decision
# for whoever reads the count: a type of its own if the book has several, or the general
# rules if it is one drawing standing alone. It is a name for what is not yet known and
# never a place to put a figure that belongs to one of the six.
FIGURE_TYPES = (
    "plane geometry",
    "solid geometry",
    "function graph",
    "number line",
    "strip diagram",
    "histogram",
    "pie chart",
    "cumulative frequency curve",
    "grid",
    "other",
)

AxisValue = float | str


def parabola_fix(curve_dict: dict) -> tuple[list[float] | None, str | None]:
    """Vertex derived from an explicit quadratic label, and opens set from a.

    Returns (vertex or None, opens or None). None means that field does not change.
    """
    if curve_dict.get("kind") != "parabola":
        return None, None
    from figures.function_graph import quadratic_from_label

    coeffs = quadratic_from_label(str(curve_dict.get("label") or ""))
    if coeffs is None:
        return None, None
    a, b, c = coeffs
    vertex_out = None
    opens_out = None
    intercepts = curve_dict.get("x_intercepts") or []
    if curve_dict.get("vertex") is None and not intercepts:
        h = round(-b / (2 * a), 6)
        k = round(c - (b * b) / (4 * a), 6)
        vertex_out = [
            0.0 if abs(h) < 1e-12 else h,
            0.0 if abs(k) < 1e-12 else k,
        ]
    wanted = "down" if a < 0 else "up"
    if curve_dict.get("opens") != wanted:
        opens_out = wanted
    return vertex_out, opens_out


def count_parabola_fixes(payload: object) -> tuple[int, int]:
    """How many vertices and opens a raw reply would have parabola_fix change."""
    derived = corrected = 0
    for curve in _curve_dicts(payload):
        vertex, opens = parabola_fix(curve)
        if vertex is not None:
            derived += 1
        if opens is not None:
            corrected += 1
    return derived, corrected


def _curve_dicts(payload: object) -> list[dict]:
    found: list[dict] = []
    if isinstance(payload, dict):
        if payload.get("kind") in {"parabola", "sine", "cosine", "line"} and "label" in payload:
            found.append(payload)
        for value in payload.values():
            found.extend(_curve_dicts(value))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(_curve_dicts(item))
    return found


def _graph_curves(groups: list["ExerciseGroup"]) -> list["GraphCurve"]:
    curves: list[GraphCurve] = []
    for group in groups:
        figures = list(group.figures)
        for exercise in group.exercises:
            figures.extend(exercise.figures)
        for figure in figures:
            if figure.graph is not None:
                curves.extend(figure.graph.curves)
    return curves


class GraphCurve(BaseModel):
    """One curve on a function-graph figure."""

    kind: Literal["parabola", "sine", "cosine", "line"] = Field(
        description="What sort of curve this is"
    )
    label: str = Field(
        min_length=1,
        description=(
            "Equation as LaTeX without $...$. Always fill it, even when the stem or "
            "exercise wording already names this equation — the plotter reads the "
            "stretch from it. Use the same letters as the wording: ص = 16س, not "
            "y = 16x. When two or more curves share the axes, labels must be "
            "distinct so the strokes can be told apart"
        ),
    )
    style: Literal["solid", "dashed"] = Field(
        default="solid",
        description="Stroke style; dashed when a second curve must be told apart",
    )
    points: list[list[float]] = Field(
        default_factory=list,
        description="Two [x, y] points that determine a line; omit for other kinds",
    )
    vertex: list[float] | None = Field(
        default=None,
        description="Parabola vertex as [h, k]; omit for other kinds",
    )
    x_intercepts: list[AxisValue] = Field(
        default_factory=list,
        description=(
            "Where this curve meets the horizontal axis, in axis order, when those "
            "roots are shown. Include 0 when a shown root is the origin. Empty when "
            "the curve misses that axis, or when the student must read the roots off "
            "the graph (a parabola still needs a vertex). For a trigonometric axis "
            'prefer π-multiples as LaTeX without $...$ (e.g. "\\pi", "3\\pi"); plain '
            "floats near those values are also accepted"
        ),
    )
    y_intercepts: list[float] = Field(
        default_factory=list,
        description=(
            "Where this curve meets the vertical axis, when that point is shown "
            "(usually one entry). Include 0 when a shown intercept is the origin. "
            "Empty when the curve misses that axis, or when the student must read "
            "the intercept off the graph"
        ),
    )
    y_intercept: float | None = Field(
        default=None,
        description=(
            "Deprecated single y-intercept; prefer y_intercepts. Still accepted when the "
            "curve meets the y-axis and y_intercepts was left empty"
        ),
    )
    opens: Literal["up", "down"] | None = Field(
        default=None,
        description=(
            'Preferred for a parabola: "up" (opens upwards) or "down" (opens '
            "downwards). Optional — the plotter can infer it from the wording or "
            "intercepts. Omit for every other kind"
        ),
    )
    amplitude: float | None = Field(
        default=None, description="Amplitude of a sine or cosine; omit when 1 or unused"
    )
    frequency: float | None = Field(
        default=None, description="Frequency of a sine or cosine; omit when 1 or unused"
    )
    _parabola_vertex_derived: bool = PrivateAttr(default=False)
    _parabola_opens_corrected: bool = PrivateAttr(default=False)

    @model_validator(mode="after")
    def curve_fields_match_kind(self) -> "GraphCurve":
        if self.y_intercept is not None and not self.y_intercepts:
            self.y_intercepts = [self.y_intercept]
        elif self.y_intercepts and self.y_intercept is None:
            self.y_intercept = self.y_intercepts[0]
        if self.kind == "parabola":
            vertex_fix, opens_fix = parabola_fix(
                {
                    "kind": self.kind,
                    "label": self.label,
                    "vertex": self.vertex,
                    "x_intercepts": self.x_intercepts,
                    "opens": self.opens,
                }
            )
            if vertex_fix is not None:
                self.vertex = vertex_fix
                self._parabola_vertex_derived = True
            if opens_fix is not None:
                self.opens = opens_fix
                self._parabola_opens_corrected = True
            if self.vertex is None and not self.x_intercepts:
                raise ValueError("a parabola needs a vertex or at least one x-intercept")
        elif self.opens is not None:
            raise ValueError("only a parabola carries opens")
        return self


class StripCell(BaseModel):
    """What is written in one rectangle of a strip diagram."""

    kind: Literal["text", "blank", "blank_box"] = Field(
        default="text",
        description=(
            "How the cell is drawn: text (a number, variable, unit amount or ?), "
            "blank (a writing line), or blank_box (a small empty rectangle with its "
            "unit beside it)"
        ),
    )
    text: str = Field(
        default="",
        description=(
            "The label when kind is text: a number, a variable, a unit amount such as "
            "'8 L', '250 mL' or '40%', or '?' for an unknown the student must find"
        ),
    )
    unit: str = Field(
        default="",
        description="The unit beside a blank_box, e.g. 'cm' or 'm'; omit for other kinds",
    )

    @model_validator(mode="after")
    def cell_fields_match_kind(self) -> "StripCell":
        if self.kind == "blank_box" and not self.unit.strip():
            raise ValueError("a blank_box cell needs a unit")
        if self.kind == "blank" and (self.text.strip() or self.unit.strip()):
            raise ValueError("a blank cell carries no text or unit")
        return self


class StripRow(BaseModel):
    """One row of identical unit blocks in an equal-unit strip diagram."""

    name: str = Field(
        default="",
        description=(
            "Name printed to the left of the row, e.g. 'Mona', 'Quantity A'; omit "
            "when the row is unnamed"
        ),
    )
    count: int = Field(
        default=1,
        ge=1,
        description="How many identical blocks this row has",
    )
    cell: StripCell = Field(description="What every block in this row contains")
    bracket: str = Field(
        default="",
        description=(
            "A total marked off the row with a bracket, not written in a cell; omit "
            "when there is no bracket"
        ),
    )
    bracket_side: Literal["above", "below"] | None = Field(
        default=None,
        description='Which side of the row the bracket sits on; required when bracket is set',
    )

    @model_validator(mode="after")
    def bracket_has_side(self) -> "StripRow":
        if self.bracket.strip() and self.bracket_side not in ("above", "below"):
            raise ValueError('a bracket needs bracket_side set to "above" or "below"')
        return self


class Strip(BaseModel):
    """The cells a strip diagram is drawn from.

    Written here by the model because extract.py does not call one. The book draws
    from `strip`, not from the prose: put every row, cell, bracket and layout there.
    """

    layout: Literal["equal_unit", "part_whole"] = Field(
        description=(
            "equal_unit for a tape of identical blocks (one row, or two rows comparing "
            "a unit to a multiple); part_whole for a whole on top and its parts below"
        )
    )
    rows: list[StripRow] = Field(
        default_factory=list,
        description="The rows of an equal-unit diagram; omit for part_whole",
    )
    whole: StripCell | None = Field(
        default=None,
        description="The top cell of a part–whole diagram; omit for equal_unit",
    )
    parts: list[StripCell] = Field(
        default_factory=list,
        description="The bottom cells of a part–whole diagram, side by side; omit for equal_unit",
    )

    @model_validator(mode="after")
    def layout_fields_match(self) -> "Strip":
        if self.layout == "equal_unit":
            if not self.rows:
                raise ValueError("an equal-unit strip needs at least one row")
            if self.whole is not None or self.parts:
                raise ValueError("an equal-unit strip uses rows, not whole and parts")
        elif self.whole is None or len(self.parts) < 2:
            raise ValueError("a part–whole strip needs a whole and at least two parts")
        elif self.rows:
            raise ValueError("a part–whole strip uses whole and parts, not rows")
        return self


class Graph(BaseModel):
    """The numbers a function graph is plotted from: axes, window, and curves."""

    x_ticks: list[AxisValue] = Field(
        description=(
            "Tick values that must be drawn on the horizontal axis; include every "
            "intercept, vertex x-coordinate and other mark the student must read. "
            "Do not list 0 on the ticks — the origin is marked on its own. That rule "
            "is ticks only, not intercepts. For a trigonometric axis prefer LaTeX "
            'without $...$: "\\pi", "2\\pi", "\\frac{\\pi}{2}", '
            '"4\\pi". Floats near those multiples are also accepted and will be '
            "labelled as π on the figure"
        )
    )
    y_ticks: list[float] = Field(
        description=(
            "Tick values that must be drawn on the vertical axis; include every "
            "intercept, vertex y-coordinate, amplitude and other mark the student "
            "must read. Do not list 0 on the ticks — the origin is marked on its own. "
            "That rule is ticks only, not intercepts"
        )
    )
    domain: list[AxisValue] | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        description=(
            'Visible x-window of the figure as [xmin, xmax]. For sine and cosine this '
            'is the interval shown on the figure (e.g. [0, "2\\pi"] or '
            '[0, "4\\pi"]). Include whenever the exercise is not asking the '
            "student to find the domain; omit when it is, so the window is not the "
            "answer. Prefer π LaTeX for trigonometric endpoints; floats near those "
            "values are also accepted"
        ),
    )
    range: list[float] | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        description=(
            "Visible y-window of the figure as [ymin, ymax]. Include whenever the "
            "exercise is not asking the student to find the range; omit when it is, "
            "so the window is not the answer"
        ),
    )
    curves: list[GraphCurve] = Field(
        min_length=1, description="One entry per curve drawn on these axes"
    )


class Grid(BaseModel):
    """The integers a count-and-shade lattice is drawn from."""

    cols: int = Field(
        ge=1,
        description=(
            "How many cells wide one lattice is. A square number of cells is a square "
            "lattice (100 → 10, 25 → 5, 4 → 2). A count that 5 divides is five columns "
            "(20 → 5, 50 → 5). Anything else is the factorisation nearest a square, "
            "wider than it is tall"
        ),
    )
    rows: int = Field(
        ge=1,
        description="How many cells tall one lattice is",
    )
    shaded: int = Field(
        ge=0,
        description=(
            "How many cells are filled on the remainder lattice — the one that is not "
            "entirely filled. A count of zero is an empty lattice. This is the shaded "
            "count, even when the exercise asks about the unshaded cells"
        ),
    )
    extra_full: int = Field(
        default=0,
        ge=0,
        description=(
            "How many extra lattices stand beside the remainder, filled entirely. "
            "Omit when there is only one grid. For a percentage past a hundred: "
            "140% is extra_full 1 and shaded 40 on a 10×10; 215% is extra_full 2 "
            "and shaded 15"
        ),
    )


class Figure(BaseModel):
    """One figure an exercise needs: what type of drawing it is, and what it shows."""

    figure_type: Literal[*FIGURE_TYPES] = Field(  # type: ignore[valid-type]
        description="Which type of drawing this figure is; 'other' only when it is"
        " genuinely none of the named types"
    )
    description: str = Field(
        description="The figure described precisely enough to be redrawn from it alone"
    )
    graph: Graph | None = Field(
        default=None,
        description=(
            "Required for a function graph: curves, ticks, and domain/range when the "
            "exercise does not ask for them; omit for every other figure type"
        ),
    )
    strip: Strip | None = Field(
        default=None,
        description=(
            "Required for a strip diagram: layout, rows or whole/parts, and brackets; "
            "omit for every other figure type"
        ),
    )
    grid: Grid | None = Field(
        default=None,
        description=(
            "Required for a grid: cols, rows, shaded, and extra_full when needed; "
            "omit for every other figure type"
        ),
    )

    @model_validator(mode="after")
    def structured_fields_match_type(self) -> "Figure":
        if self.figure_type == "function graph":
            if self.graph is None:
                raise ValueError("a function graph needs a graph field")
        elif self.graph is not None:
            raise ValueError("only a function graph carries a graph field")
        if self.figure_type == "strip diagram":
            if self.strip is None:
                raise ValueError("a strip diagram needs a strip field")
        elif self.strip is not None:
            raise ValueError("only a strip diagram carries a strip field")
        if self.figure_type == "grid":
            if self.grid is None:
                raise ValueError("a grid needs a grid field")
        elif self.grid is not None:
            raise ValueError("only a grid carries a grid field")
        return self


class Exercise(BaseModel):
    """One new exercise, with the answer a teacher would check against."""

    text: str = Field(
        description="The exercise as the student reads it, maths as LaTeX, without its"
        " options; empty where a figure under a stem is the whole exercise"
    )
    options: list[str] = Field(
        default_factory=list,
        description="The four choices of a multiple-choice exercise, unlabelled; omit for others",
    )
    figures: list[Figure] = Field(
        default_factory=list,
        description="One entry per figure the exercise needs; omit when it needs none",
    )
    poems: list[Poem] = Field(
        default_factory=list,
        description="Verse blocks this exercise reprints; place with [poem N]; omit when none",
    )
    objectives: list[str] = Field(
        description="The objective written for, plus any earlier ones the exercise needs"
    )
    word_problem: bool = Field(
        default=False, description="True when the mathematics arises from a real situation"
    )
    answer: str = Field(
        description="The answer, with the one step that shows it is right where that helps"
    )


class ExerciseGroup(BaseModel):
    """One or more exercises under a single stem the paper prints once.

    A standalone exercise is a group of one with no `stem`.
    """

    stem: str = Field(
        default="",
        description="Instruction shared by every exercise below it; omit when it stands alone",
    )
    figures: list[Figure] = Field(
        default_factory=list,
        description="One entry per figure belonging to the shared stem; omit when none",
    )
    poems: list[Poem] = Field(
        default_factory=list,
        description="Verse blocks the stem reprints; place with [poem N] in stem; omit when none",
    )
    exercises: list[Exercise] = Field(min_length=1)


class WriteReply(BaseModel):
    """The model's answer, which may refuse the objective instead of writing for it."""

    status: Literal["ok", "abort"]
    abort_reason: str = Field(
        default="", description="Why the objective cannot be written for; empty when ok"
    )
    groups: list[ExerciseGroup] = Field(
        default_factory=list, description="The exercises, in the order they should be practised"
    )


class WriteReplyForced(BaseModel):
    """Exercises only — no abort path when the run must produce a reply."""

    groups: list[ExerciseGroup] = Field(
        min_length=1,
        description="The exercises, in the order they should be practised",
    )


FORCE_GENERATE_PROMPT = (
    "\n\n**Forced generation.** This run does not accept `abort`. Return the full "
    "exercise count with `status` ok (or return `groups` only). Use passage-based sets "
    "with a shared stem where the lesson requires it; for oral-performance objectives "
    "use written stand-ins (punctuation, tone, phrasing choices) that still fit the "
    "book's rules."
)


# A row on disk carries its id, and a group the objective it was written for,
# alongside the model's own fields. Read off the models so that renaming a field
# cannot leave the skip check behind.
GROUP_KEYS = {"id", "objective", *ExerciseGroup.model_fields}
GROUP_REQUIRED = {"id", "objective"} | {
    name for name, field in ExerciseGroup.model_fields.items() if field.is_required()
}
ROW_KEYS = {"id", *Exercise.model_fields}
ROW_REQUIRED = {"id"} | {
    name for name, field in Exercise.model_fields.items() if field.is_required()
}


def build_prompt(meta: dict[str, str], book_id: str = "") -> Prompt:
    """The prompt this stage sends for the book, with the set sizes filled in.

    Substituted by hand rather than with string.Template, because the prompt is
    full of LaTeX and Template would read every $ in it as a placeholder.
    """
    assembled = assemble(PROMPT_STAGE, meta, book_id)
    for slot in ("{set_sizes}", "{tall_set_sizes}", "{full_set}"):
        if slot not in assembled.text:
            raise RuntimeError(f"{assembled.parts[0]} no longer has a {slot} placeholder")
    text = (
        assembled.text.replace("{set_sizes}", listed(SET_SIZES))
        .replace("{tall_set_sizes}", listed(TALL_SET_SIZES))
        .replace("{full_set}", str(max(SET_SIZES)))
    )
    return Prompt(text, assembled.parts)


def spec_part(section: dict, target: str) -> str:
    """The section as the model sees it, with the objective being written for flagged.

    Sent whole and sent compact. The whole spec is what keeps an exercise inside
    the section: the out_of_scope rows say what may not be asked, the glossary and
    notations say how to say it, and the other objectives say what this call must
    leave to the calls writing for them.
    """
    spec = dict(section["spec"])
    spec["objectives"] = [
        {**row, "target": True} if row["id"] == target else row for row in spec["objectives"]
    ]
    body = json.dumps(
        {
            "number": section["number"],
            "title": section["title"],
            "module_path": section["module_path"],
            **spec,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"Section JSON:\n{body}"


def assignment_part(objective: dict, count: int, earlier: list[str]) -> str:
    """What this one call is for: the objective, the count, and what it may lean on."""
    body = json.dumps(
        {
            "objective": objective,
            "exercises": count,
            "at_most_in_sets": count // 2,
            "may_also_name": earlier,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"Assignment JSON:\n{body}"


def examples_part(extraction: dict, objective: str) -> str:
    """The book's own questions for this objective, kept in their printed groups.

    A question of a set means nothing without the stem above it — "$x^2$" is not
    a question — so the examples travel as the book prints them, with only the
    questions tagged for this objective kept under each stem.
    """
    groups = []
    taken = 0
    for group in extraction["questions"]["groups"]:
        if taken >= EXAMPLE_LIMIT:
            break
        rows = [
            row["text"]
            for row in group["questions"]
            if objective in (row.get("objectives") or [])
        ][: EXAMPLE_LIMIT - taken]
        if not rows:
            continue
        taken += len(rows)
        item: dict = {"questions": rows}
        if group.get("stem"):
            item["stem"] = group["stem"]
        groups.append(item)

    body = json.dumps({"groups": groups}, ensure_ascii=False, separators=(",", ":"))
    return f"Textbook questions for this objective, as examples only:\n{body}"


def source_stems_part(
    extraction: dict,
    objective: str,
    form_stems: dict[str, str] | None = None,
) -> str:
    """Stems from source_questions the model must reuse when grouping — not invent."""
    from src.source_form_stems import (
        source_form_stems_for_objective,
        source_group_stems_for_objective,
    )

    shared = source_group_stems_for_objective(extraction, objective)
    form = (
        form_stems
        if form_stems is not None
        else source_form_stems_for_objective(extraction, objective)
    )
    body = json.dumps(
        {
            "objective": objective,
            "form_set_stems": form,
            "shared_group_stems": shared,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        "Textbook stems from source_questions for this objective (reuse verbatim when "
        "you group exercises; do not invent new header lines):\n"
        f"{body}"
    )


def exercises_in(groups: list[ExerciseGroup]) -> int:
    """How many exercises a list of groups holds."""
    return sum(len(group.exercises) for group in groups)


def exercises_in_sets(groups: list[ExerciseGroup]) -> int:
    """How many of those sit in a set, under a stem shared with others."""
    return sum(len(group.exercises) for group in groups if len(group.exercises) > 1)


def listed(sizes: tuple[int, ...]) -> str:
    """The sizes as a reader says them, as in "4, 8 or 12"."""
    if len(sizes) == 1:
        return str(sizes[0])
    return f"{', '.join(str(size) for size in sizes[:-1])} or {sizes[-1]}"


def carries_table(exercise: Exercise) -> bool:
    """Whether an exercise brings a table of its own to the page.

    Its wording or one of its choices, since either is printed under the stem and
    either is a block of ruled cells where a line of maths would do. A table in the
    stem is not counted: the stem is printed once however many exercises share it.
    """
    return any(LATEX_TABLE.search(field) for field in (exercise.text, *exercise.options))


def tall_with(group: ExerciseGroup) -> str:
    """What this set's own exercises carry that makes them tall, or nothing."""
    carried: list[str] = []
    if any(exercise.figures for exercise in group.exercises):
        carried.append("figures")
    if any(exercise.poems for exercise in group.exercises):
        carried.append("poems")
    if any(carries_table(exercise) for exercise in group.exercises):
        carried.append("tables")
    return " and ".join(carried)


def sizes_for(group: ExerciseGroup) -> tuple[int, ...]:
    """The sizes this set may be, which what its exercises carry decides."""
    return TALL_SET_SIZES if tall_with(group) else SET_SIZES


def split_into(count: int, sizes: tuple[int, ...]) -> list[int]:
    """Consecutive set sizes adding to `count`, or nothing where none do.

    Largest first, so that a set is filled before the next is opened, as a book
    prints a long list. A count that cannot be made of these sizes at all — five
    exercises where a set holds four or eight — returns nothing, and is a reply to
    send back rather than a shape to repair: the fifth exercise cannot be moved out
    on its own, since under a stem its wording is often no more than "$x^2 - 9$".
    """
    parts: list[int] = []
    left = count
    while left:
        fits = [size for size in sizes if size <= left]
        if not fits:
            return []
        parts.append(max(fits))
        left -= parts[-1]
    return parts


FORM_KINDS = frozenset({"mcq", "tf", "blank"})
FORM_KIND_ORDER = ("tf", "mcq", "blank")

FORM_SET_STEMS: dict[str, dict[str, str]] = {
    "en": {
        "mcq": "Choose the correct answer in each of the following:",
        "tf": "Put ($\\checkmark$) for true and ($\\times$) for false:",
        "blank": "Complete each of the following:",
    },
    "ar": {
        "mcq": "اختر الإجابة الصحيحة فيما يلي:",
        "tf": "ضع ($\\checkmark$) أمام العبارة الصحيحة و ($\\times$) أمام العبارة الخاطئة:",
        "blank": "أكمل ما يلي:",
    },
}
ALL_FORM_SET_STEMS = frozenset(
    stem for language in FORM_SET_STEMS.values() for stem in language.values()
)


def exercise_form_kind(exercise: Exercise) -> str:
    """Choose, true/false, complete, or another shape."""
    if exercise.options:
        return "mcq"
    text = exercise.text
    if UNICODE_TICK_CROSS.search(text) or r"\checkmark" in text or r"\times" in text:
        return "tf"
    if r"\blank" in text:
        return "blank"
    return "other"


def exercise_form_tall(exercise: Exercise) -> bool:
    """Whether one exercise forces tall set sizes when it is packed into a form set."""
    return bool(exercise.figures or exercise.poems or carries_table(exercise))


def exercise_instruction_header(exercise: Exercise, kind: str) -> str:
    """Printed instruction line repeated on loose exercises of one form."""
    if kind not in FORM_KINDS:
        return ""
    text = exercise.text.strip()
    if not text:
        return ""
    head = text.split("\n\n", 1)[0].strip()
    if kind == "mcq" and "\n" in head:
        head = head.split("\n", 1)[0].strip()
    if len(head) > 220 or LATEX_TABLE.search(head):
        return ""
    return head


def resolve_form_set_stem(
    kind: str,
    language_code: str,
    source_stems: dict[str, str] | None,
    header_line: str = "",
) -> str:
    """Header for a packed form set: textbook stem, shared instruction line, or default."""
    if source_stems and kind in source_stems:
        return source_stems[kind]
    header = header_line.strip()
    if header:
        return header
    language = language_code if language_code in FORM_SET_STEMS else "en"
    return FORM_SET_STEMS[language].get(kind, "")


def strip_leading_instruction(text: str, stem: str) -> str:
    """Drop a set stem duplicated at the start of an exercise's wording."""
    text = text.strip()
    stem = stem.strip()
    if not stem:
        return text
    norm_stem = " ".join(stem.split())
    if text.startswith(stem):
        rest = text[len(stem) :].lstrip("\n\r ")
        return rest or text
    head = text.split("\n\n", 1)[0].strip()
    if " ".join(head.split()) == norm_stem and "\n\n" in text:
        return text.split("\n\n", 1)[1].strip()
    return text


def form_set_stem(
    kind: str,
    language_code: str,
    source_stems: dict[str, str] | None = None,
) -> str:
    """Header for a packed form set (textbook ``source_stems`` or book default)."""
    return resolve_form_set_stem(kind, language_code, source_stems)


def form_set_stem_catalog(
    language_code: str,
    source_stems: dict[str, str] | None = None,
) -> frozenset[str]:
    """Every instruction line that counts as a form-set header for this book."""
    lines = set(ALL_FORM_SET_STEMS)
    if source_stems:
        lines.update(source_stems.values())
    language = language_code if language_code in FORM_SET_STEMS else "en"
    lines.update(FORM_SET_STEMS[language].values())
    return frozenset(" ".join(line.split()) for line in lines)


def is_mergeable_form_standalone(group: ExerciseGroup) -> bool:
    """One exercise with no shared stem or group-level stimulus."""
    if len(group.exercises) != 1:
        return False
    if group.stem.strip():
        return False
    if group.figures or group.poems:
        return False
    return True


def is_form_set_group(
    group: ExerciseGroup,
    *,
    form_stems: frozenset[str] | None = None,
) -> bool:
    """A multi-exercise set under a short choose / T–F / complete instruction."""
    if len(group.exercises) <= 1:
        return False
    stem = " ".join(group.stem.split())
    catalog = form_stems if form_stems is not None else ALL_FORM_SET_STEMS
    if stem in catalog:
        return True
    if LATEX_TABLE.search(group.stem):
        return False
    kinds = {exercise_form_kind(exercise) for exercise in group.exercises}
    return len(kinds) == 1 and kinds.issubset(FORM_KINDS) and len(stem) <= 220


def exercises_in_shared_stimulus_sets(groups: list[ExerciseGroup]) -> int:
    """Exercises in sets that count toward the half-in-sets cap (passage, table, figure, …)."""
    return sum(
        len(group.exercises)
        for group in groups
        if len(group.exercises) > 1 and not is_form_set_group(group)
    )


def ceiling_set_size(count: int, sizes: tuple[int, ...]) -> int | None:
    """Smallest allowed set size that fits ``count`` exercises (pad up, never down)."""
    if count <= 1:
        return None
    if count in sizes:
        return count
    for size in sizes:
        if size >= count:
            return size
    return None


def form_set_target_size(count: int, sizes: tuple[int, ...]) -> int | None:
    """Next printable set size (4, 8, 12, …) when at least three share a form."""
    if count < 3:
        return None
    return ceiling_set_size(count, sizes)


def form_set_padding_needed(count: int, sizes: tuple[int, ...]) -> int:
    """How many more exercises of one form are needed to fill the next set size."""
    target = form_set_target_size(count, sizes)
    if target is None:
        return 0
    if count in sizes:
        return 0
    return target - count


def pack_form_set_count(count: int, sizes: tuple[int, ...]) -> tuple[list[int], int]:
    """Largest-first set sizes and how many exercises they consume."""
    if count <= 1:
        return [], count
    if parts := split_into(count, sizes):
        return parts, count
    parts = []
    pending = count
    minimum = min(sizes)
    while pending >= minimum:
        size = max(s for s in sizes if s <= pending)
        parts.append(size)
        pending -= size
    used = count - pending
    return parts, used


def pack_form_bucket(
    exercises: list[Exercise],
    kind: str,
    language_code: str,
    tall: bool,
    source_stems: dict[str, str] | None,
    header_line: str = "",
) -> list[ExerciseGroup]:
    """Turn loose exercises of one form into one or more printable sets."""
    minimum = min(TALL_SET_SIZES if tall else SET_SIZES)
    if len(exercises) < minimum:
        return [ExerciseGroup(exercises=[exercise]) for exercise in exercises]
    sizes = TALL_SET_SIZES if tall else SET_SIZES
    layout, used = pack_form_set_count(len(exercises), sizes)
    if not layout:
        return [ExerciseGroup(exercises=[exercise]) for exercise in exercises]
    stem = resolve_form_set_stem(kind, language_code, source_stems, header_line)
    if not stem.strip():
        return [ExerciseGroup(exercises=[exercise]) for exercise in exercises]

    def with_stem_only(exercise: Exercise) -> Exercise:
        if not stem.strip():
            return exercise
        text = strip_leading_instruction(exercise.text, stem)
        if text == exercise.text:
            return exercise
        return exercise.model_copy(update={"text": text})

    trimmed = [with_stem_only(exercise) for exercise in exercises]
    groups: list[ExerciseGroup] = []
    start = 0
    for size in layout:
        groups.append(
            ExerciseGroup(stem=stem, exercises=trimmed[start : start + size])
        )
        start += size
    for exercise in trimmed[used:]:
        groups.append(ExerciseGroup(exercises=[exercise]))
    return groups


def _form_pool_key(exercise: Exercise, kind: str) -> tuple[str, bool, bool, str]:
    header = exercise_instruction_header(exercise, kind)
    return (kind, exercise.word_problem, exercise_form_tall(exercise), header)


def pool_form_standalone_counts(
    groups: list[ExerciseGroup],
) -> dict[tuple[str, bool, bool, str], int]:
    """How many loose choose / T–F / complete exercises sit in each pool."""
    pools: dict[tuple[str, bool, bool, str], int] = {}
    for group in groups:
        if not is_mergeable_form_standalone(group):
            continue
        exercise = group.exercises[0]
        kind = exercise_form_kind(exercise)
        if kind not in FORM_KINDS:
            continue
        key = _form_pool_key(exercise, kind)
        pools[key] = pools.get(key, 0) + 1
    return pools


def form_set_padding_problems(
    groups: list[ExerciseGroup],
    language_code: str = "en",
) -> list[str]:
    """Ask for more exercises when same-type singles almost fill a set but do not."""
    del language_code
    problems: list[str] = []
    for (kind, _word_problem, tall, _header), count in pool_form_standalone_counts(
        groups
    ).items():
        sizes = TALL_SET_SIZES if tall else SET_SIZES
        need = form_set_padding_needed(count, sizes)
        if need <= 0:
            continue
        target = form_set_target_size(count, sizes)
        label = {"mcq": "choose", "tf": "true/false", "blank": "complete"}[kind]
        problems.append(
            f"{count} standalone {label} exercise(s) of the same shape; "
            f"write {need} more to form one set of {target} grouped under the same "
            f"`stem` as the matching textbook example for this objective, or leave at most two loose"
        )
    return problems


def pack_form_sets(
    groups: list[ExerciseGroup],
    language_code: str = "en",
    source_stems: dict[str, str] | None = None,
) -> tuple[list[ExerciseGroup], int]:
    """Pool loose choose / T–F / complete items into form sets, then other groups."""
    preserved: list[ExerciseGroup] = []
    pools: dict[tuple[str, bool, bool, str], list[Exercise]] = {}

    for group in groups:
        if not is_mergeable_form_standalone(group):
            preserved.append(group)
            continue
        exercise = group.exercises[0]
        kind = exercise_form_kind(exercise)
        if kind not in FORM_KINDS:
            preserved.append(group)
            continue
        key = _form_pool_key(exercise, kind)
        pools.setdefault(key, []).append(exercise)

    packed: list[ExerciseGroup] = []
    packed_exercises = 0
    for kind in FORM_KIND_ORDER:
        keys = sorted(key for key in pools if key[0] == kind)
        for key in keys:
            for group in pack_form_bucket(
                pools[key],
                kind,
                language_code,
                key[2],
                source_stems,
                header_line=key[3],
            ):
                if len(group.exercises) > 1:
                    packed_exercises += len(group.exercises)
                packed.append(group)

    return packed + preserved, packed_exercises


def exercise_group_from_disk(group: dict) -> ExerciseGroup:
    """The model shape of one on-disk group, without its ids."""
    body = {key: group[key] for key in ExerciseGroup.model_fields if key in group}
    body["exercises"] = [
        {key: row[key] for key in Exercise.model_fields if key in row}
        for row in group.get("exercises") or []
    ]
    return ExerciseGroup.model_validate(body)


def pack_document_exercise_groups(
    groups: list[dict],
    language_code: str,
    *,
    source_by_objective: dict[str, dict[str, str]] | None = None,
) -> tuple[list[dict], dict[str, int]]:
    """Pack form sets per objective for section JSON already on disk."""
    order: list[str] = []
    by_objective: dict[str, list[dict]] = {}
    for group in groups:
        objective = group["objective"]
        if objective not in by_objective:
            order.append(objective)
            by_objective[objective] = []
        by_objective[objective].append(group)

    stats = {"standalone_in": 0, "sets_out": 0, "merged_exercises": 0, "left_standalone": 0}
    out: list[dict] = []
    stems_map = source_by_objective or {}
    for objective in order:
        chunk = by_objective[objective]
        models = [exercise_group_from_disk(row) for row in chunk]
        for model in models:
            if not is_mergeable_form_standalone(model):
                continue
            kind = exercise_form_kind(model.exercises[0])
            if kind in FORM_KINDS:
                stats["standalone_in"] += 1
            else:
                stats["left_standalone"] += 1
        source_stems = stems_map.get(objective)
        packed, merged = pack_form_sets(
            models, language_code, source_stems=source_stems
        )
        stats["merged_exercises"] += merged
        stats["sets_out"] += sum(1 for group in packed if len(group.exercises) > 1)
        for group in packed:
            if len(group.exercises) == 1 and is_mergeable_form_standalone(group):
                stats["left_standalone"] += 1
            body = group.model_dump(exclude_defaults=True)
            out.append({"objective": objective, **body})

    for index, group in enumerate(out, start=1):
        group["id"] = f"group_{index}"
    return out, stats


def says_nothing(stem: str) -> bool:
    """Whether a stem tells a student only what their exercises' own form tells them."""
    if "$" in stem:
        return False
    words = re.findall(r"[a-zA-Z]+", stem.lower())
    return bool(words) and all(word in EMPTY_STEM_WORDS for word in words)


# A control character JSON made of a LaTeX command's backslash. The reply is JSON,
# and in JSON `\f` is a form feed, `\t` a tab and `\b` a backspace: a model that
# writes `\frac` with one backslash where the format wanted two has written a form
# feed followed by "rac", and the exercise reaches the paper as a character LaTeX
# refuses. None of these four is ever wanted in an exercise, so one of them followed
# by a letter can only be a backslash that was lost, and is put back. A newline is
# left alone: it is wanted, and "\neq" was never seen lost this way.
LOST_BACKSLASH = re.compile(r"[\b\f\t\r](?=[A-Za-z])")
BACKSLASH_FOR = {"\b": "\\b", "\f": "\\f", "\t": "\\t", "\r": "\\r"}

# A table whose \begin lost the word "array" and kept only the column spec:
# "\begin{|c|c|c|}" is a table the model set out to write and LaTeX an environment
# it has never heard of, and a paper it stands in does not compile. The spec is
# unmistakable — pipes and column letters and nothing else — so the word is put back.
LOST_ARRAY = re.compile(r"\\begin\{(\s*\|?[lcr](?:\s*\|*\s*[lcr])*\s*\|*\s*)\}")

# The environments an exercise may open. Anything else is a name LaTeX will stop on,
# or one the paper never asked for, and is sent back rather than set.
ENVIRONMENTS = frozenset({
    "array", "tabular", "cases", "matrix", "pmatrix", "bmatrix", "vmatrix", "Bmatrix",
    "smallmatrix", "aligned", "gathered", "split",
})
ENVIRONMENT = re.compile(r"\\begin\{([^{}]*)\}")


def strings_in(value):
    """Every string in a value, however deep it is nested."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings_in(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings_in(item)


def with_backslashes(value):
    """The value with its backslashes put right, however deep it is nested.

    A backslash JSON swallowed is put back, a command written with two is brought
    back to one, and a table that lost the word "array" has it restored.
    """
    if isinstance(value, str):
        from src.english_json import repair_english_string

        value, hits = repair_english_string(value)
        refused = [before for before, _after, status in hits if status == "refused"]
        if refused:
            raise ValueError(
                "english json: "
                + "; ".join(f"{before} is not a known LaTeX command" for before in refused)
            )
        value = single_commands(value)
        return LOST_ARRAY.sub(r"\\begin{array}{\1}", value)
    if isinstance(value, list):
        return [with_backslashes(item) for item in value]
    if isinstance(value, dict):
        return {key: with_backslashes(item) for key, item in value.items()}
    return value


def lost_backslashes(groups: list[ExerciseGroup]) -> tuple[list[ExerciseGroup], int, int]:
    """The groups with their backslashes put right.

    The two counts are how many swallowed backslashes were restored, and how many
    commands written with two were brought back to one.
    """
    dumped = [group.model_dump() for group in groups]
    texts = list(strings_in(dumped))
    count = sum(
        len(LOST_BACKSLASH.findall(text)) + len(LOST_ARRAY.findall(text)) for text in texts
    )
    doubled = sum(len(DOUBLED_COMMAND.findall(text)) for text in texts)
    if not count and not doubled:
        return groups, 0, 0
    return (
        [ExerciseGroup.model_validate(with_backslashes(group)) for group in dumped],
        count,
        doubled,
    )


def repair_groups_excessive_tashkeel(groups: list[ExerciseGroup]) -> list[ExerciseGroup]:
    """Devocalise fields that would fail the full-sentence tashkeel density check.

    ﴿…﴾ and «…» stay vocalised; bare poem stems pasted from the PDF are stripped
    to contextual prose before validation. Runs before mark-placement repair.
    """
    from src.translation_quality import repair_excessive_tashkeel

    fixed: list[ExerciseGroup] = []
    for group in groups:
        figures = [
            figure.model_copy(
                update={"description": repair_excessive_tashkeel(figure.description)}
            )
            for figure in group.figures
        ]
        exercises: list[Exercise] = []
        for exercise in group.exercises:
            exercise_figures = [
                figure.model_copy(
                    update={"description": repair_excessive_tashkeel(figure.description)}
                )
                for figure in exercise.figures
            ]
            exercises.append(
                exercise.model_copy(
                    update={
                        "text": repair_excessive_tashkeel(exercise.text),
                        "figures": exercise_figures,
                    }
                )
            )
        fixed.append(
            group.model_copy(
                update={
                    "stem": repair_excessive_tashkeel(group.stem),
                    "figures": figures,
                    "exercises": exercises,
                }
            )
        )
    return fixed


def repair_groups_mark_placement(groups: list[ExerciseGroup]) -> list[ExerciseGroup]:
    """Strip unpaired shadda and move tanween off bare alif on printed fields.

    Quran ﴿…﴾ spans are left alone. Applied after structural repairs and before
    the mark-placement checker, so habitual بيّن / دائماً do not burn the whole
    objective retry budget.
    """
    from src.translation_quality import repair_mark_placement

    fixed: list[ExerciseGroup] = []
    for group in groups:
        figures = [
            figure.model_copy(
                update={"description": repair_mark_placement(figure.description)}
            )
            for figure in group.figures
        ]
        exercises: list[Exercise] = []
        for exercise in group.exercises:
            exercise_figures = [
                figure.model_copy(
                    update={"description": repair_mark_placement(figure.description)}
                )
                for figure in exercise.figures
            ]
            exercises.append(
                exercise.model_copy(
                    update={
                        "text": repair_mark_placement(exercise.text),
                        "answer": repair_mark_placement(exercise.answer),
                        "options": [
                            repair_mark_placement(option) for option in exercise.options
                        ],
                        "figures": exercise_figures,
                    }
                )
            )
        fixed.append(
            group.model_copy(
                update={
                    "stem": repair_mark_placement(group.stem),
                    "figures": figures,
                    "exercises": exercises,
                }
            )
        )
    return fixed


def _underline_fields(field: str) -> str:
    from src.latex import html_underlines, wrap_bare_underlines

    return wrap_bare_underlines(html_underlines(field))


def repair_groups_bare_underlines(
    groups: list[ExerciseGroup],
) -> tuple[list[ExerciseGroup], int]:
    """Put ``\\underline{...}`` and ``<u>…</u>`` into ``$...$`` on printed fields."""
    changed = 0
    fixed: list[ExerciseGroup] = []
    for group in groups:
        stem = _underline_fields(group.stem)
        if stem != group.stem:
            changed += 1
        figures = [
            figure.model_copy(
                update={"description": _underline_fields(figure.description)}
            )
            for figure in group.figures
        ]
        exercises: list[Exercise] = []
        for exercise in group.exercises:
            text = _underline_fields(exercise.text)
            answer = _underline_fields(exercise.answer)
            options = [_underline_fields(option) for option in exercise.options]
            exercise_figures = [
                figure.model_copy(
                    update={"description": _underline_fields(figure.description)}
                )
                for figure in exercise.figures
            ]
            if (
                text != exercise.text
                or answer != exercise.answer
                or options != exercise.options
            ):
                changed += 1
            exercises.append(
                exercise.model_copy(
                    update={
                        "text": text,
                        "answer": answer,
                        "options": options,
                        "figures": exercise_figures,
                    }
                )
            )
        fixed.append(
            group.model_copy(
                update={"stem": stem, "figures": figures, "exercises": exercises}
            )
        )
    return fixed, changed


def repair_groups_degree_bullets(groups: list[ExerciseGroup]) -> list[ExerciseGroup]:
    """Put a degree written as \\bullet back as \\circ on printed fields."""
    fixed: list[ExerciseGroup] = []
    for group in groups:
        figures = [
            figure.model_copy(
                update={"description": degree_bullets(figure.description)}
            )
            for figure in group.figures
        ]
        exercises: list[Exercise] = []
        for exercise in group.exercises:
            exercise_figures = [
                figure.model_copy(
                    update={"description": degree_bullets(figure.description)}
                )
                for figure in exercise.figures
            ]
            exercises.append(
                exercise.model_copy(
                    update={
                        "text": degree_bullets(exercise.text),
                        "answer": degree_bullets(exercise.answer),
                        "options": [degree_bullets(option) for option in exercise.options],
                        "figures": exercise_figures,
                    }
                )
            )
        fixed.append(
            group.model_copy(
                update={
                    "stem": degree_bullets(group.stem),
                    "figures": figures,
                    "exercises": exercises,
                }
            )
        )
    return fixed


def repair_groups_arabic_curve_labels(groups: list[ExerciseGroup]) -> list[ExerciseGroup]:
    """Map Latin x/y on function-graph curve labels to the letters the wording uses."""
    from src.arabic_labels import rewrite_arabic_curve_label

    def rewrite_figure(figure: Figure, wording: str) -> Figure:
        if figure.figure_type != "function graph" or figure.graph is None:
            return figure
        curves = [
            curve.model_copy(
                update={"label": rewrite_arabic_curve_label(curve.label, wording)}
            )
            for curve in figure.graph.curves
        ]
        graph = figure.graph.model_copy(update={"curves": curves})
        return figure.model_copy(update={"graph": graph})

    fixed: list[ExerciseGroup] = []
    for group in groups:
        group_wording = "\n".join(
            [group.stem]
            + [exercise.text for exercise in group.exercises]
            + [exercise.answer for exercise in group.exercises]
        )
        figures = [
            rewrite_figure(figure, f"{group_wording}\n{figure.description}")
            for figure in group.figures
        ]
        exercises: list[Exercise] = []
        for exercise in group.exercises:
            wording = "\n".join(
                [group.stem, exercise.text, exercise.answer]
            )
            exercise_figures = [
                rewrite_figure(figure, f"{wording}\n{figure.description}")
                for figure in exercise.figures
            ]
            exercises.append(exercise.model_copy(update={"figures": exercise_figures}))
        fixed.append(group.model_copy(update={"figures": figures, "exercises": exercises}))
    return fixed


def repaired(
    groups: list[ExerciseGroup],
    target: int,
    *,
    vector_lesson: bool = False,
    notation: str = "latin",
    language_code: str = "en",
    source_stems: dict[str, str] | None = None,
) -> tuple[list[ExerciseGroup], dict]:
    """Bring a reply inside the stage's limits wherever that can be done mechanically.

    Seven things are repaired rather than sent back, because each costs a whole
    call to ask again for a difference the exercises themselves do not notice. A
    backslash the JSON swallowed is put back, and a command written with two is
    brought back to one, as above. A letter the model prefixed onto a choice
    ("(B) …") is taken off, because the schema asked for the choices unlabelled
    and the paper numbers them itself. A degree written as \\bullet is put as
    \\circ. A group of several exercises with no stem is not a group, and neither is one
    whose stem only announces that the exercises are multiple choice or have a
    blank in them, so either way each exercise becomes its own. A set of a size the
    paper does not print becomes consecutive sets of sizes it does, wherever the
    count allows it, which is how a long list is printed anyway. And exercises past
    the budget are dropped from the end, since
    the count is the one thing this stage cannot negotiate. Coming up short is not
    repairable and is asked for again.
    """
    vertex_derived = opens_corrected = 0
    for curve in _graph_curves(groups):
        if curve._parabola_vertex_derived:
            vertex_derived += 1
        if curve._parabola_opens_corrected:
            opens_corrected += 1
    groups, backslashes, doubled = lost_backslashes(groups)
    groups, labelled = unlabelled_choices(groups)
    groups, paren_converted = repair_groups_paren_delimiters(groups)
    groups, math_wrapped = repair_groups_math_choices(groups)
    groups, underlines_wrapped = repair_groups_bare_underlines(groups)
    groups = repair_groups_degree_bullets(groups)
    kept: list[ExerciseGroup] = []
    stemless = boilerplate = oversized = 0

    for group in groups:
        if len(group.exercises) > 1 and (not group.stem.strip() or says_nothing(group.stem)):
            if group.stem.strip():
                boilerplate += 1
            else:
                stemless += 1
            kept += [
                ExerciseGroup(
                    figures=group.figures if index == 0 else [], exercises=[exercise]
                )
                for index, exercise in enumerate(group.exercises)
            ]
            continue
        sizes = sizes_for(group)
        if len(group.exercises) > min(sizes) and len(group.exercises) not in sizes:
            if parts := split_into(len(group.exercises), sizes):
                oversized += 1
                start = 0
                for part in parts:
                    kept.append(
                        ExerciseGroup(
                            stem=group.stem,
                            figures=group.figures if start == 0 else [],
                            exercises=group.exercises[start : start + part],
                        )
                    )
                    start += part
                continue
        kept.append(group)

    trimmed = 0
    if exercises_in(kept) > target:
        held: list[ExerciseGroup] = []
        total = 0
        for group in kept:
            room = target - total
            if room <= 0:
                break
            if len(group.exercises) > room:
                # A set is cut to a size it is still allowed to be, and passed over
                # where no such size fits the room left; cutting it to the room
                # exactly would only trade a reply that is too long for one whose
                # sets are the wrong shape.
                fits = [size for size in sizes_for(group) if size <= room]
                if not fits:
                    continue
                group = group.model_copy(update={"exercises": group.exercises[: max(fits)]})
            held.append(group)
            total += len(group.exercises)
        trimmed = exercises_in(kept) - total
        kept = held

    if notation == "arabic":
        kept = repair_groups_arabic_curve_labels(kept)

    dumped = canonicalize_tree(
        [group.model_dump() for group in kept], vector_lesson=vector_lesson
    )
    kept = [ExerciseGroup.model_validate(row) for row in dumped]

    padding = form_set_padding_problems(kept, language_code)
    form_packed = 0
    if not padding:
        kept, form_packed = pack_form_sets(
            kept, language_code, source_stems=source_stems
        )

    return kept, {
        "lost_backslashes": backslashes,
        "doubled_commands": doubled,
        "labelled_choices": labelled,
        "stemless_split": stemless,
        "empty_stem_split": boilerplate,
        "sets_split": oversized,
        "trimmed": trimmed,
        "parabola_vertex_derived": vertex_derived,
        "parabola_opens_corrected": opens_corrected,
        "paren_delimiters_converted": paren_converted,
        "math_choices_wrapped": math_wrapped,
        "underlines_wrapped": underlines_wrapped,
        "form_sets_packed": form_packed,
        "form_set_padding": padding,
    }


def naked_math(field: str) -> str:
    """The first maths this field leaves outside $...$, or "" where it leaves none."""
    found = NAKED_MATH.search(MATH_SEGMENT.sub(" ", field))
    return found.group(0) if found else ""


def unescaped_dollars(text: str) -> int:
    """How many $ signs open or close maths, ignoring \\$."""
    count = 0
    backslashes = 0
    for char in text:
        if char == "\\":
            backslashes += 1
            continue
        if char == "$" and backslashes % 2 == 0:
            count += 1
        backslashes = 0
    return count


# Short words that are prose even though they are fewer than three letters, when
# they stand as words of their own. Uppercase runs and two-letter symbols (mg, xy)
# stay mathematics.
ALL_MATH_PROSE_WORDS = frozenset("or is of to in at on as if by no be an".split())
TEXT_OR_MATHRM = re.compile(r"\\(?:text|mathrm)\{[^{}]*\}")
COMMAND_NAME = re.compile(r"\\[a-zA-Z]+")
LOWER_RUN = re.compile(r"[a-z]{3,}")
ARABIC_RUN = re.compile(r"[\u0600-\u06FF]{2,}")
LATIN_WORD = re.compile(r"[A-Za-z]+")
PAREN_DELIMS = re.compile(r"\\\((.+?)\\\)", re.DOTALL)


def strip_unescaped_dollars(text: str) -> str:
    """The field with unescaped $ taken out, leaving \\$ in place."""
    out: list[str] = []
    backslashes = 0
    for char in text:
        if char == "\\":
            backslashes += 1
            out.append(char)
            continue
        if char == "$" and backslashes % 2 == 0:
            backslashes = 0
            continue
        out.append(char)
        backslashes = 0
    return "".join(out)


def dollars_only_at_edges(text: str) -> bool:
    """True when every unescaped $ sits at the start or the end of the field."""
    body = text.strip()
    while body.startswith("$"):
        body = body[1:].lstrip()
    while body.endswith("$") and not body.endswith(r"\$"):
        body = body[:-1].rstrip()
    return unescaped_dollars(body) == 0


def choice_is_prose(option: str) -> bool:
    """Whether a choice still says something in words after maths is stripped."""
    body = strip_unescaped_dollars(option)
    body = TEXT_OR_MATHRM.sub(" ", body)
    body = COMMAND_NAME.sub(" ", body)
    if LOWER_RUN.search(body) or ARABIC_RUN.search(body):
        return True
    return any(word.lower() in ALL_MATH_PROSE_WORDS for word in LATIN_WORD.findall(body))


def all_math_choice(option: str) -> bool:
    """Whether a choice is mathematics that should sit inside $...$."""
    if r"\$" in option or not option.strip() or choice_is_prose(option):
        return False
    if not dollars_only_at_edges(option):
        return False
    dollars = unescaped_dollars(option)
    return bool(naked_math(option) or dollars % 2 or dollars == 0)


def convert_paren_delimiters(field: str) -> tuple[str, int]:
    """\\(...\\) written as $...$, skipped where the field uses \\$."""
    if r"\$" in field:
        return field, 0
    return PAREN_DELIMS.subn(r"$\1$", field)


def _interval_inner(seg: str) -> str:
    inner = seg.strip()
    if inner.startswith("$$") and inner.endswith("$$"):
        return inner[2:-2]
    if inner.startswith("$") and inner.endswith("$"):
        return inner[1:-1]
    if inner.startswith("$"):
        return inner[1:]
    return inner


def interval_problems(field: str, where: str) -> list[str]:
    """Broken intervals in a field's maths: a missing opener, or a doubled minus."""
    problems: list[str] = []
    segs = [found.group(0) for found in MATH_SEGMENT.finditer(field)]
    stripped = field.strip()
    if (
        stripped.startswith("$")
        and stripped.endswith(("[", "]"))
        and unescaped_dollars(stripped) % 2
        and stripped not in segs
    ):
        segs.append(stripped)
    for seg in segs:
        blanked = TEXT_OR_MATHRM.sub(" ", _interval_inner(seg))
        if "--" in blanked:
            problems.append(f"{where} writes a broken interval {seg!r}")
            continue
        if not blanked.endswith(("[", "]")) or "," not in blanked:
            continue
        last = blanked.rfind(",")
        previous = blanked.rfind(",", 0, last)
        region = blanked[:last] if previous == -1 else blanked[previous + 1 : last]
        if not any(opener in region for opener in "[]("):
            problems.append(f"{where} writes a broken interval {seg!r}")
    return problems


def repair_groups_paren_delimiters(
    groups: list[ExerciseGroup],
) -> tuple[list[ExerciseGroup], int]:
    """\\(...\\) in text, options and answers rewritten as $...$."""
    converted = 0
    rewritten: list[ExerciseGroup] = []
    for group in groups:
        exercises: list[Exercise] = []
        changed = False
        for exercise in group.exercises:
            text, n_text = convert_paren_delimiters(exercise.text)
            answer, n_answer = convert_paren_delimiters(exercise.answer)
            options: list[str] = []
            n_options = 0
            for option in exercise.options:
                wrapped, n = convert_paren_delimiters(option)
                options.append(wrapped)
                n_options += n
            count = n_text + n_answer + n_options
            if count:
                converted += count
                changed = True
                exercises.append(
                    exercise.model_copy(
                        update={"text": text, "answer": answer, "options": options}
                    )
                )
            else:
                exercises.append(exercise)
        rewritten.append(
            group.model_copy(update={"exercises": exercises}) if changed else group
        )
    return rewritten, converted


def repair_groups_math_choices(
    groups: list[ExerciseGroup],
) -> tuple[list[ExerciseGroup], int]:
    """All-math choices that leaked outside $...$, wrapped."""
    wrapped = 0
    rewritten: list[ExerciseGroup] = []
    for group in groups:
        exercises: list[Exercise] = []
        changed = False
        for exercise in group.exercises:
            if not exercise.options:
                exercises.append(exercise)
                continue
            options: list[str] = []
            option_changed = False
            for option in exercise.options:
                if all_math_choice(option):
                    body = strip_unescaped_dollars(option).strip()
                    rewritten_option = f"${body}$"
                    if rewritten_option != option:
                        wrapped += 1
                        option_changed = True
                        options.append(rewritten_option)
                        continue
                options.append(option)
            if option_changed:
                changed = True
                exercises.append(exercise.model_copy(update={"options": options}))
            else:
                exercises.append(exercise)
        rewritten.append(
            group.model_copy(update={"exercises": exercises}) if changed else group
        )
    return rewritten, wrapped


def balanced_braces(text: str) -> bool:
    """Whether every unescaped { has a matching }."""
    depth = 0
    escaped = False
    for char in text:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def field_problems(fields: dict[str, str], where: str) -> list[str]:
    """Every field of one exercise that writes maths without its delimiters, or that
    has run away into a repeated character."""
    return [
        f"{where} {name} repeats {found.group(1)!r} {len(found.group(0))} times over; "
        "the reply has broken down and is not read past this point"
        for name, field in fields.items()
        if (found := RUNAWAY.search(field))
    ] + [
        f"{where} {name} writes {loose} outside $...$"
        for name, field in fields.items()
        if (loose := naked_math(field))
    ] + [
        f"{where} {name} writes {found.group(0)!r}, a superscript or subscript inside "
        r"\text{...}; a script is maths and goes outside the \text, as $^\circ\text{C}$"
        for name, field in fields.items()
        if (found := SCRIPT_IN_TEXT.search(field))
    ] + [
        f"{where} {name} opens the environment {found.group(1)!r}, which LaTeX has not "
        r"got; a table is \begin{array}{...}...\end{array} and nothing else"
        for name, field in fields.items()
        for found in ENVIRONMENT.finditer(field)
        if found.group(1).strip() not in ENVIRONMENTS
    ] + [
        f"{where} {name} has an odd number of $ signs"
        for name, field in fields.items()
        if unescaped_dollars(field) % 2
    ] + [
        f"{where} {name} has unbalanced braces"
        for name, field in fields.items()
        if not balanced_braces(field)
    ]


def markdown_table(field: str) -> str:
    """The first row this field writes as a Markdown table, or "" where it writes none."""
    found = MARKDOWN_ROW.search(field) or MARKDOWN_RULE.search(field)
    return " ".join(found.group(0).split()) if found else ""


def table_problems(fields: dict[str, str], where: str) -> list[str]:
    """Every field of one exercise that writes a table as Markdown."""
    return [
        f"{where} {name} writes the table row {row!r} as Markdown; write a table as "
        r"LaTeX in $$\begin{array}...\end{array}$$"
        for name, field in fields.items()
        if (row := markdown_table(field))
    ]


def unclosed(text: str, alone: bool, chosen: bool = False) -> bool:
    """Whether an exercise that speaks in words is left without its full stop.

    Mathematics on its own closes nothing, so an exercise reading $(x + y)^2$ under
    a stem that says what to do with it ends where it ends. Once a word joins the
    mathematics the exercise is a sentence and wants its stop, and the stop goes
    missing most often where the sentence runs into mathematics or into a blank and
    the closing $ passes for an ending: "Simplify: $x^2 + 2xy + y^2$".

    A member of a set is let be unless it holds a blank, because a member is as
    often a phrase finishing its stem — "$11$ sides" under "...of a convex polygon
    having:" — and a phrase ends where the stem's sentence ended. A blank is what
    marks the member as a statement of its own, left open for the student.

    A figure marker is not wording and cannot be punctuated: an exercise reading
    "estimate the mode. [figure 1]" is closed, and the paper sets the picture where
    the marker stands. So the markers come out before the sentence is read, and
    they take their words with them, since "figure" is a word this rule must not
    count as prose.

    A table closing the exercise is read the same way. "Complete the following
    table: $$\\begin{array}...$$" is closed, because the colon promised the table and
    the table is there; what is judged is the wording before it, which may end with
    that colon or with a stop of its own. Only the table is let through: a colon
    before inline mathematics is still the "Simplify: $x^2$" this rule is for.

    A blank at the end of the wording is the answer slot, and closes the sentence
    the same way: "Investigating the sign of $f$ means \\blank" is closed. A blank
    that is not at the end still needs the sentence's own stop.

    The choices of a multiple-choice exercise are read the same way again. "The
    product in its simplest form is:" followed by four choices is closed for the same
    reason the table closes its sentence: the colon promised the choices, the paper
    prints them under it, and an Arabic stem in particular is written that way as a
    matter of course. So an exercise with choices may end with a colon and nothing
    else may.
    """
    body = POEM_MARKER.sub(" ", FIGURE_MARKER.sub(" ", text)).strip()
    if CLOSING_TABLE.search(body):
        body = CLOSING_TABLE.sub("", body).strip()
        if body.endswith(":"):
            return False
    if chosen and body.endswith(":"):
        return False
    body = body.rstrip("»\"'")
    if not body or body.endswith(SENTENCE_END):
        return False
    if CLOSING_BLANK.search(body):
        return False
    said = MATH_SEGMENT.sub(" ", body).replace(BLANK, " ")
    if not any(letter.isalpha() for letter in said):
        return False
    return alone or BLANK in body


def closing_problems(text: str, alone: bool, chosen: bool, where: str) -> list[str]:
    """The exercise's missing full stop, where its wording is left unclosed."""
    if not unclosed(text, alone, chosen):
        return []
    ended = text.strip()[-1]
    fault = (
        "ends with a colon, which promises wording that never comes"
        if ended == ":"
        else f"says its instruction in words and ends {ended!r}"
    )
    return [
        f"{where} text {fault}; close the sentence with a full stop, or with a "
        "question mark where it asks a question"
    ]


def math_key(segment: str) -> str:
    """A maths segment's body, comparable without delimiters or spacing.

    Case-sensitive, unlike equation_key: $X$ and $x$ are different keys. \\left,
    \\right, thin spaces and braces are stripped so $\\left(1,2\\right)$ matches
    $(1, 2)$.
    """
    body = segment.strip()
    if body.startswith("$$") and body.endswith("$$"):
        body = body[2:-2]
    elif body.startswith("$") and body.endswith("$"):
        body = body[1:-1]
    body = (
        body.replace(r"\left", "")
        .replace(r"\right", "")
        .replace(r"\,", "")
        .replace(r"\;", "")
        .replace(r"\!", "")
        .replace(r"\ ", "")
    )
    body = re.sub(r"\s+", "", body)
    body = body.replace(r"\dfrac", r"\frac").replace(r"\tfrac", r"\frac")
    return body.replace("{", "").replace("}", "")


def math_keys(field: str) -> set[str]:
    """Every maths segment in a field, as math_key values."""
    return {math_key(found.group(0)) for found in MATH_SEGMENT.finditer(field)}


def first_word(field: str) -> str:
    """The first Latin word, lower-cased, with maths taken out.

    Pointing words are left in, and the token is not rstripped, so "Find" and
    "a" stay distinguishable for the clause-start rule.
    """
    prose = MATH_SEGMENT.sub(" ", field)
    found = re.search(r"[A-Za-z]+", prose)
    return found.group(0).lower() if found else ""


def clause_starts(stem: str) -> set[str]:
    """The first word of the stem and of every clause after '.', ',', ':' or ';'.

    Maths is masked first so a decimal inside $...$ is not a clause break. The
    comma matters: in "In each of the following right-angled triangles, find the
    value of $x$:" the instruction "find" follows a comma.
    """
    masked = MATH_SEGMENT.sub(" ", stem)
    starts: set[str] = set()
    if word := first_word(stem):
        starts.add(word)
    for chunk in re.split(r"[.,:;]", masked):
        if word := first_word(chunk):
            starts.add(word)
    return starts


def sibling_key(text: str) -> tuple[frozenset[str], frozenset[str]]:
    """What makes one member of a set the same item as another.

    "to the $x$-axis" and "to the $x$-axis." share a key; "to the $x$-axis" and
    "to the $y$-axis" do not.
    """
    return (frozenset(content_words(text)), frozenset(math_keys(text)))


def content_words(field: str) -> set[str]:
    """What a field says in words: no maths, no pointing, singular and lower case.

    Pointing words come off before the trailing s, so "this" is dropped as "this"
    and does not survive as "thi".
    """
    prose = MATH_SEGMENT.sub(" ", field).lower()
    return {
        word.rstrip("s")
        for word in re.findall(r"[a-z]+", prose)
        if word not in POINTING_WORDS
    }


def restated(stem: str, text: str, siblings: list[str]) -> bool:
    """Whether a member of a set says only what its stem has already said.

    A stem reading "Find the perpendicular distance from the given point to the
    indicated coordinate axis:" followed by "$(0, -3, 4)$ to the $x$-axis" is
    listing distinct items, not restating the instruction. A stem reading
    "Factorize each of the following:" followed by "Factorize $x^2 - 9$." prints
    the instruction twice. The order below is load-bearing:

    1. No content words of its own is not a restatement — "$x^2 - 9$" under a
       stem that says what to do with it is exactly right.
    2. Words the stem does not use mean the member is saying something of its own.
    3. An instruction verb that opens a clause of the stem ("Find", "Factorize")
       is a restatement even when the member is unique in the set.
    4. A unique sibling (by content words and maths keys) is a distinct item of
       a list: "Point $A$", "due East", the 1.3.1 points.
    5. Identical siblings that add no maths the stem has not already named are
       restating it.
    """
    own = content_words(text)
    if not own:
        return False
    if not own <= content_words(stem):
        return False
    start = first_word(text)
    if start not in POINTING_WORDS and start in clause_starts(stem):
        return True
    if len(siblings) > 1:
        key = sibling_key(text)
        if sum(1 for sibling in siblings if sibling_key(sibling) == key) == 1:
            return False
    if not math_keys(text) - math_keys(stem):
        return True
    return False


def strip_option_label(option: str) -> str:
    """The choice without a letter the model prefixed, or the choice as it is."""
    stripped = OPTION_LABEL.sub("", option, count=1).strip()
    return stripped or option


def degree_bullets(text: str) -> str:
    """A degree written as \\bullet, put back as \\circ."""
    return DEGREE_BULLET.sub(r"\1\2^\\circ", text)


def unlabelled_choices(groups: list[ExerciseGroup]) -> tuple[list[ExerciseGroup], int]:
    """Choices with a prefixed (A)/(B) letter taken off, as the schema asked for them unlabelled."""
    labelled = 0
    rewritten: list[ExerciseGroup] = []
    for group in groups:
        exercises: list[Exercise] = []
        changed = False
        for exercise in group.exercises:
            if not exercise.options:
                exercises.append(exercise)
                continue
            options = [strip_option_label(option) for option in exercise.options]
            if options != list(exercise.options):
                labelled += sum(
                    new != old for new, old in zip(options, exercise.options)
                )
                changed = True
                exercises.append(exercise.model_copy(update={"options": options}))
            else:
                exercises.append(exercise)
        rewritten.append(
            group.model_copy(update={"exercises": exercises}) if changed else group
        )
    return rewritten, labelled


def option_prose(option: str) -> str:
    """What a choice says outside its maths, without a letter the model prefixed."""
    return MATH_SEGMENT.sub(" ", strip_option_label(option)).strip(" .,;:")


def stuck_token(option: str) -> str:
    """A lone capitalised word left trailing a choice's maths.

    The model occasionally fastens one stray word to choice after choice — "$-5$
    Lisboa", "$PK = 2 KM$ Monroe", "$6$ Baud" — a token it has caught on rather
    than anything a student could pick. A choice that is all prose is a real
    choice, "Scalene" or "Median", so it is the maths beside the word that marks
    the word as an intruder.
    """
    if not MATH_SEGMENT.search(option):
        return ""
    words = option_prose(option).split()
    if len(words) != 1 or words[0].isupper() or not words[0][:1].isupper():
        return ""
    return words[0]


def equation_key(text: str) -> str:
    """Normalise an equation for comparing a curve label to the wording."""
    body = text.strip()
    if body.startswith("$") and body.endswith("$"):
        body = body[1:-1]
    body = body.replace(r"\,", "").replace(r"\;", "").replace(r"\!", "")
    return re.sub(r"\s+", "", body).lower()


def asks_domain(wording: str) -> bool:
    """True when the exercise asks the student to find a domain."""
    return asks_extent(wording, "domain")


def asks_range(wording: str) -> bool:
    """True when the exercise asks the student to find a range."""
    return asks_extent(wording, "range")


def asks_extent(wording: str, which: Literal["domain", "range"]) -> bool:
    """True when the wording asks the student for a function's domain or range."""
    other = "range" if which == "domain" else "domain"
    return bool(
        re.search(
            rf"\b(?:find|determine|state|identify|give|write|what\s+is)\b"
            rf"[^.?]{{0,80}}\b{which}s?\b"
            rf"|\b{which}\s+(?:and\s+{other}\s+)?of\s+"
            rf"(?:the\s+)?(?:function|graph|relation|curve|\$)"
            rf"|\b{other}\s+and\s+{which}\b",
            wording,
            re.I,
        )
    )


def grid_problems(figure: Figure, where: str) -> list[str]:
    """Faults in a count-and-shade `grid` field the renderer cannot draw from."""
    if figure.figure_type != "grid" or figure.grid is None:
        return []
    try:
        from figures import grid as grid_figure

        spec = grid_figure.spec_from_fields(figure.grid.model_dump())
    except grid_figure.GridError as error:
        return [f"{where} grid field cannot be drawn: {error}"]
    except ValueError as error:
        return [f"{where} grid field is invalid: {error}"]
    want_cols, want_rows = grid_figure.lattice(spec.cols * spec.rows)
    if (spec.cols, spec.rows) != (want_cols, want_rows):
        return [
            f"{where} grid is {spec.cols}×{spec.rows}; a lattice of "
            f"{spec.cols * spec.rows} cells is drawn {want_cols}×{want_rows}"
        ]
    return []


def strip_problems(figure: Figure, where: str) -> list[str]:
    """Faults in a strip-diagram `strip` field the renderer cannot draw from."""
    if figure.figure_type != "strip diagram" or figure.strip is None:
        return []
    try:
        from figures import strip_diagram

        strip_diagram.spec_from_fields(figure.strip.model_dump())
    except strip_diagram.CannotDraw as error:
        return [f"{where} strip field cannot be drawn: {error}"]
    except ValueError as error:
        return [f"{where} strip field is invalid: {error}"]
    return []


def graph_problems(figure: Figure, wording: str, where: str) -> list[str]:
    """Faults in a function-graph `graph` field against the wording beside it."""
    if figure.figure_type != "function graph" or figure.graph is None:
        return []
    graph = figure.graph
    problems: list[str] = []
    curves = graph.curves

    for index, curve in enumerate(curves, start=1):
        if not curve.label.strip():
            problems.append(
                f"{where} curve {index} has no label; every curve must carry the"
                " equation so the plotter can read the stretch from it"
            )

    trig = any(curve.kind in ("sine", "cosine") for curve in curves)
    for index, curve in enumerate(curves, start=1):
        spot = f"{where} curve {index}"
        if curve.kind == "parabola" and curve.vertex is None and not curve.x_intercepts:
            problems.append(
                f"{spot} is a parabola with neither a vertex nor x_intercepts"
            )

    if graph.domain is not None and asks_domain(wording):
        problems.append(
            f"{where} sets domain {graph.domain!r}, but the exercise asks for the"
            " domain; omit domain so the drawn window is not the answer"
        )
    if graph.range is not None and asks_range(wording):
        problems.append(
            f"{where} sets range {graph.range!r}, but the exercise asks for the"
            " range; omit range so the drawn window is not the answer"
        )
    if graph.domain is None and not asks_domain(wording):
        what = (
            "the interval shown on the figure (e.g. [0, 2π])"
            if trig
            else "the visible x-window"
        )
        problems.append(
            f"{where} has no domain; name {what} unless the exercise asks for the domain"
        )
    if graph.range is None and not asks_range(wording):
        problems.append(
            f"{where} has no range; name the visible y-window unless the exercise"
            " asks for the range"
        )

    return problems


def foreign_problems(fields: dict[str, str], where: str, known: frozenset[str]) -> list[str]:
    """Every field that writes a letter the section's own material never uses.

    A word in another language reaches the page as nonsense at best, and as
    question marks at worst: the paper's type has nothing to set Bengali with, so
    "$7$ সর্বত্র" prints as a row of them. Beyond the ASCII that carries the
    mathematics, the letters allowed are the book's own alphabet and whatever the
    request itself uses. The alphabet is there because a section's material does
    not use every letter of its language — an Arabic spec with no alif madda in it
    was refusing every reply that wrote "الآتي" — and the request's letters are
    there for a name or a unit the book writes in a script not its own.
    """
    return [
        f"{where} {name} writes {letter!r}, a letter this section never uses"
        for name, field in fields.items()
        if (
            letter := next(
                (c for c in field if c.isalpha() and not c.isascii() and c not in known), ""
            )
        )
    ]


def write_problems(
    groups: list[ExerciseGroup],
    target: int,
    objective: str,
    allowed: list[str],
    known: frozenset[str],
) -> list[str]:
    """Every reason a reply cannot be used, as lines for the report.

    The count is the rule that carries the stage: the budget divided the section's
    hundred exercises between its objectives, and a call that writes a different
    number has not filled its share of that division. The objectives named carry
    the second rule, since an exercise that reaches forward to a later objective
    would be practising work the student has not met and would belong to a budget
    other than this one.
    """
    problems: list[str] = []

    total = exercises_in(groups)
    if total != target:
        problems.append(f"{total} exercises written, not the {target} asked for")

    in_sets = exercises_in_shared_stimulus_sets(groups)
    if in_sets > target // 2:
        problems.append(
            f"{in_sets} exercises sit in sets, more than the {target // 2} allowed here"
        )

    # Two sets under one instruction read as a single set interrupted for no reason:
    # the paper prints "Factorize each of the following:" twice down one column. The
    # exception is a stem with more exercises than a set may hold, which has to be
    # printed as consecutive sets sharing it, so an earlier set that is full has
    # earned the repeat.
    sharing: dict[str, list[ExerciseGroup]] = {}
    for group in groups:
        if len(group.exercises) > 1 and group.stem.strip() and not is_form_set_group(group):
            sharing.setdefault(" ".join(group.stem.split()), []).append(group)
    for stem, sets in sharing.items():
        if len(sets) > 1 and any(
            len(group.exercises) != max(sizes_for(group)) for group in sets[:-1]
        ):
            problems.append(
                f"{len(sets)} sets share the instruction {stem[:40]!r}; "
                "put the exercises that share an instruction in one set"
            )

    for index, group in enumerate(groups, start=1):
        if len(group.exercises) > 1:
            sizes = sizes_for(group)
            if len(group.exercises) not in sizes:
                carried = tall_with(group)
                carries = f" whose exercises carry {carried}" if carried else ""
                problems.append(
                    f"group {index} is a set of {len(group.exercises)}; a set{carries} "
                    f"holds {listed(sizes)} exercises and no other number"
                )
        problems += field_problems({"stem": group.stem}, f"group {index}")
        problems += source_artifact_problems(group.stem, f"group {index}")
        problems += foreign_problems({"stem": group.stem}, f"group {index}", known)
        problems += table_problems({"stem": group.stem}, f"group {index}")
        problems += poem_problems(group.stem, group.poems, f"group {index}")
        group_wording = " ".join(
            part
            for part in [group.stem, *(exercise.text for exercise in group.exercises)]
            if part.strip()
        )
        for figure_index, figure in enumerate(group.figures, start=1):
            spot = f"group {index} figure {figure_index}"
            problems += grid_problems(figure, spot)
            problems += graph_problems(figure, group_wording, spot)
            problems += strip_problems(figure, spot)
        sibling_texts = [member.text for member in group.exercises]
        for number, exercise in enumerate(group.exercises, start=1):
            where = f"group {index} exercise {number}"
            fields = {
                "text": exercise.text,
                "answer": exercise.answer,
                **{
                    f"choice ({letter})": option
                    for letter, option in zip("ABCD", exercise.options)
                },
            }
            problems += field_problems(fields, where)
            problems += interval_problems(exercise.answer, f"{where} answer")
            for letter, option in zip("ABCD", exercise.options):
                problems += interval_problems(
                    option, f"{where} choice ({letter})"
                )
            problems += house_wording_square_problems(
                " ".join(
                    part for part in [group.stem, exercise.text] if part.strip()
                ),
                where,
            )
            problems += source_artifact_problems(exercise.text, where)
            problems += source_artifact_problems(exercise.answer, f"{where} answer")
            for letter, option in zip("ABCD", exercise.options):
                problems += source_artifact_problems(
                    option, f"{where} choice ({letter})"
                )
            problems += foreign_problems(fields, where, known)
            problems += table_problems(fields, where)
            problems += closing_problems(
                exercise.text, len(group.exercises) == 1, bool(exercise.options), where
            )
            problems += poem_problems(
                exercise.text, exercise.poems, where, options=exercise.options
            )
            exercise_wording = " ".join(
                part for part in [group.stem, exercise.text] if part.strip()
            )
            for figure_index, figure in enumerate(exercise.figures, start=1):
                spot = f"{where} figure {figure_index}"
                problems += grid_problems(figure, spot)
                problems += graph_problems(figure, exercise_wording, spot)
                problems += strip_problems(figure, spot)
            for letter, option in zip("ABCD", exercise.options):
                if said := OPTION_SCAFFOLD.search(option_prose(option)):
                    problems.append(
                        f"{where} choice ({letter}) says {said.group(0)!r}, which belongs to"
                        " the instruction and not to a choice"
                    )
                if token := stuck_token(option):
                    problems.append(
                        f"{where} choice ({letter}) trails {token!r} after its maths, which is"
                        " not part of the choice"
                    )
            # A figure under a stem is a whole exercise on its own: the stem says what
            # to do, the figure says what to do it to, and wording between them would
            # only repeat one of the two. Without a stem there is no instruction at
            # all, so wording is still required.
            if not exercise.text.strip() and not (
                (exercise.figures or exercise.poems) and group.stem.strip()
            ):
                problems.append(f"{where} has no text")
            if len(group.exercises) > 1 and restated(
                group.stem, exercise.text, sibling_texts
            ):
                problems.append(
                    f"{where} says only what its stem says; leave the instruction to the"
                    " stem and let the exercise carry what is its own"
                )
            if not exercise.answer.strip():
                problems.append(f"{where} has no answer")
            if exercise.options and len(exercise.options) != MCQ_OPTIONS:
                problems.append(
                    f"{where} offers {len(exercise.options)} choices, not the {MCQ_OPTIONS}"
                    " a multiple-choice exercise carries"
                )
            # An answer that picks a choice by letter where no choices were written: the
            # exercise was meant as multiple choice and its options were lost on the way,
            # and what is left is a question the paper cannot ask.
            if not exercise.options and (letter := chosen_letter(exercise.answer)):
                problems.append(
                    f"{where} answers {letter!r}, choosing by letter, but offers no choices;"
                    " give the exercise its four choices or answer it in full"
                )
            # One complaint for the exercise, not one per choice: a table among the
            # choices is a fault in how the exercise was asked, and where one choice
            # is a table the others are the same table with a cell changed.
            if tabled := [
                letter
                for letter, option in zip("ABCD", exercise.options)
                if LATEX_TABLE.search(option)
            ]:
                choices = ", ".join(f"({letter})" for letter in tabled)
                problems.append(
                    f"{where} offers a table as choice {choices}; a table is never a choice — ask"
                    " for the one value those tables would differ in and offer values to pick"
                    " from, or ask for the table without making it multiple choice"
                )
            if objective not in exercise.objectives:
                problems.append(f"{where} does not name {objective}, which it is written for")
            for unknown in [name for name in exercise.objectives if name not in allowed]:
                problems.append(
                    f"{where} names {unknown}, which is not {objective} or earlier than it"
                )

    return problems


class ProblemsError(ValueError):
    """A reply that broke the write rules, with each reason kept as its own string."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


LOCATION_PREFIX = re.compile(
    r"^("
    r"group \d+"
    r"(?: exercise \d+)?"
    r"(?: choice \([^)]+\))?"
    r"(?: figure \d+)?"
    r"(?: curve \d+)?"
    r")(?:\s+|:\s*)(.*)$",
    re.S,
)


def _location_and_rest(problem: str) -> tuple[str, str]:
    found = LOCATION_PREFIX.match(problem)
    if found:
        return found.group(1), found.group(2)
    return "", problem


def retry_feedback(problems: list[str]) -> str | None:
    """The last failure, as a note the model sees on the next attempt.

    Messages that differ only by where they happened — group, exercise, choice,
    figure, curve — collapse to one bullet with the locations listed. Ten bullets
    and 2,000 characters is the cap; the rest become "… and N more".
    """
    if not problems:
        return None
    grouped: dict[str, list[str]] = {}
    order: list[str] = []
    for problem in problems:
        location, rest = _location_and_rest(problem)
        key = rest or problem
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        if location:
            grouped[key].append(location)
    bullets: list[str] = []
    for key in order:
        locations = grouped[key]
        if locations:
            bullets.append(f"{', '.join(locations)} {key}")
        else:
            bullets.append(key)
    omitted = 0
    if len(bullets) > 10:
        omitted = len(bullets) - 10
        bullets = bullets[:10]

    def render(items: list[str], extra: int) -> str:
        lines = ["Your previous reply was rejected for these reasons:"]
        lines.extend(f"- {item}" for item in items)
        if extra:
            lines.append(f"… and {extra} more")
        lines.append(
            "Write the whole reply again, fixing these and keeping to every rule above."
        )
        return "\n".join(lines)

    text = render(bullets, omitted)
    while len(text) > 2000 and bullets:
        bullets.pop()
        omitted += 1
        text = render(bullets, omitted)
    if len(text) > 2000:
        text = text[:1999] + "…"
    return text


def pydantic_problems(error: ValidationError) -> list[str]:
    """One problem per pydantic error, loc and msg, never split on '; '."""
    rows: list[str] = []
    for item in error.errors():
        loc = ".".join(str(part) for part in item.get("loc") or ())
        msg = str(item.get("msg") or "invalid")
        rows.append(f"{loc}: {msg}" if loc else msg)
    return rows or [str(error)]


def truncated_reply(finish: str | None, error_message: str | None) -> bool:
    """True when the model hit the cap or the JSON ran off the end of the reply."""
    if finish and "MAX_TOKENS" in finish:
        return True
    return bool(error_message) and "Invalid JSON: EOF" in error_message


def check_write(
    reply: WriteReply, target: int, objective: str, allowed: list[str], known: frozenset[str]
) -> None:
    """Reject a reply that cannot be used, reporting every reason at once."""
    problems = write_problems(reply.groups, target, objective, allowed, known)
    if problems:
        raise ProblemsError(problems)


def logged_call(
    client,
    model: str,
    prompt: str,
    parts: list[str],
    target: int,
    objective: str,
    allowed: list[str],
    thinking: str,
    logger: ApiLogger,
    item: str,
    attempt: int,
    context: dict,
    alphabet: frozenset[str] = frozenset(),
    vector_lesson: bool = False,
    notation: str = "latin",
    language_code: str = "en",
    source_stems: dict[str, str] | None = None,
    feedback: str | None = None,
    feedback_from_attempt: int | None = None,
    feedback_problems: list[str] | None = None,
    force_generate: bool = False,
) -> tuple[WriteReply | None, str | None, list[str] | None, str | None]:
    """Ask for one objective's exercises once, logging the call however it turns out.

    Returns the model's reply, or None alongside the failure to report, the
    structured problems when the reply broke a rule, and the SDK finish_reason.
    Feedback is last in contents so the prompt prefix stays cacheable, and is
    logged as feedback.txt rather than as a part. known letters stay those of
    the prompt and the parts.
    """
    effective_prompt = prompt + FORCE_GENERATE_PROMPT if force_generate else prompt
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=WriteReplyForced if force_generate else WriteReply,
        thinking_config=types.ThinkingConfig(thinking_level=thinking),
    )
    contents = [effective_prompt, *parts]
    if feedback:
        contents.append(feedback)
    response = None
    result = None
    error_message = None
    problems: list[str] | None = None
    repairs: dict = {}
    started = time.perf_counter()
    try:
        response = client.models.generate_content(
            model=model, contents=contents, config=config
        )
        raw = genai_answer_text(response)
        if force_generate:
            forced = WriteReplyForced.model_validate_json(raw)
            result = WriteReply(status="ok", groups=forced.groups)
        else:
            result = WriteReply.model_validate_json(raw)
        if result.status == "abort":
            if force_generate:
                raise ProblemsError(
                    [
                        "abort is not allowed with --force-generate; "
                        "return the full exercise count as status ok"
                    ]
                )
        if result.status == "ok":
            result.groups, repairs = repaired(
                result.groups,
                target,
                vector_lesson=vector_lesson,
                notation=notation,
                language_code=language_code,
                source_stems=source_stems,
            )
            result.groups = repair_groups_excessive_tashkeel(result.groups)
            result.groups = repair_groups_mark_placement(result.groups)
            # The book's alphabet and the letters the request itself uses are the
            # letters the reply may use. Feedback is not part of that request.
            known = alphabet | frozenset(
                character for character in effective_prompt + "".join(parts) if character.isalpha()
            )
            if padding := repairs.get("form_set_padding"):
                if not force_generate:
                    raise ProblemsError(padding)
            check_write(result, target, objective, allowed, known)
    except ProblemsError as error:
        error_message = f"{type(error).__name__}: {error}"
        problems = error.problems
        result = None
    except ValidationError as error:
        error_message = f"{type(error).__name__}: {error}"
        problems = pydantic_problems(error)
        result = None
    except Exception as error:  # noqa: BLE001 - retry transport, parse and rule failures alike
        error_message = f"{type(error).__name__}: {error}"
        result = None
    finish = finish_reason(response)
    duration = time.perf_counter() - started

    extra = {
        **context,
        "thinking_level": thinking,
        "returned": result.status if result else None,
        "finish_reason": finish,
        **repairs,
    }
    if feedback:
        extra["feedback_from_attempt"] = feedback_from_attempt
        extra["feedback_problems"] = feedback_problems
    safe_log(
        logger,
        files_builder=lambda captured=response: {
            "prompt.txt": effective_prompt,
            **{f"part_{number}.txt": part for number, part in enumerate(parts, start=1)},
            **({"feedback.txt": feedback} if feedback else {}),
            **(genai_response_files(captured) if captured is not None else {}),
        },
        page_number=item,
        step_name=WRITE_STEP,
        attempt=attempt,
        api="gemini",
        model=model,
        usage=genai_usage(response) if response is not None else {},
        duration_seconds=duration,
        status="success" if result is not None else "error",
        error_message=error_message,
        extra=extra,
    )
    return result, error_message, problems, finish


def request_exercises(
    client,
    model: str,
    prompt: str,
    parts: list[str],
    target: int,
    objective: str,
    allowed: list[str],
    thinking: str,
    logger: ApiLogger,
    item: str,
    context: dict,
    report: Callable[[str], None] | None = None,
    alphabet: frozenset[str] = frozenset(),
    vector_lesson: bool = False,
    notation: str = "latin",
    language_code: str = "en",
    source_stems: dict[str, str] | None = None,
    force_generate: bool = False,
) -> WriteReply:
    """Call the model until it returns usable exercises, retrying every failure.

    A refusal — a reply that broke a rule, a reply that would not parse — costs one
    of MAX_ATTEMPTS. Being told to slow down does not: a 429 says nothing about the
    reply and everything about how many calls a book's worth of workers has in
    flight, so it is waited out, for longer each time, and the attempt asked again.
    A run that is only ever throttled therefore finishes late rather than empty,
    which is what happened to 46 sections of a book the first time the limit was
    met with four two-second retries.

    The last rule-check failure is sent back as feedback on the next attempt.
    Truncation, a JSON reply that ran off the end, and transport errors clear it.
    """
    last_error: str | None = None
    attempt = 1
    throttled = 0
    feedback: str | None = None
    feedback_from: int | None = None
    pending_problems: list[str] | None = None

    while attempt <= MAX_ATTEMPTS:
        result, last_error, problems, finish = logged_call(
            client,
            model,
            prompt,
            parts,
            target,
            objective,
            allowed,
            thinking,
            logger,
            item,
            attempt,
            context,
            alphabet,
            vector_lesson,
            notation,
            language_code,
            source_stems,
            feedback=feedback,
            feedback_from_attempt=feedback_from,
            feedback_problems=pending_problems,
            force_generate=force_generate,
        )
        if result is not None:
            if result.status == "abort" and not force_generate:
                return result
            if result.status == "ok":
                return result
        if rate_limited(last_error) and throttled < RATE_LIMIT_WAITS:
            throttled += 1
            pause = RATE_LIMIT_PAUSE * throttled + random.uniform(0, RATE_LIMIT_PAUSE)
            if report is not None:
                report(f"rate limited ({throttled} of {RATE_LIMIT_WAITS}), waiting {pause:.0f}s")
            time.sleep(pause)
            continue
        if truncated_reply(finish, last_error):
            feedback = None
            feedback_from = None
            pending_problems = None
        elif problems:
            feedback = retry_feedback(problems)
            feedback_from = attempt
            pending_problems = problems
        else:
            feedback = None
            feedback_from = None
            pending_problems = None
        if attempt < MAX_ATTEMPTS:
            # This stage refuses more replies than any other — a count that is
            # not the budget's, a set of the wrong size, maths outside its
            # delimiters — so what was wrong is said rather than left to the log.
            if report is not None:
                report(
                    f"attempt {attempt} of {MAX_ATTEMPTS} failed, "
                    f"{one_line(last_error or 'no reason given')}; "
                    f"asking again in {2**attempt}s"
                )
            time.sleep(2**attempt)
        attempt += 1

    raise RuntimeError(f"failed after {MAX_ATTEMPTS} attempts: {last_error}")


def rate_limited(error: str | None) -> bool:
    """Whether a failure was the service asking for fewer calls, not refusing this one."""
    return bool(error) and ("RESOURCE_EXHAUSTED" in error or " 429 " in error)


def exercise_document(
    written: dict[str, list[ExerciseGroup]], budget_rows: list[dict]
) -> dict:
    """A section's exercises as they are written to disk, in the objectives' own order.

    Numbered straight through the section as written, so exercise_57 is the
    fifty-seventh exercise written; the paper numbers what it prints for itself. The
    totals are summed here rather than asked for, so the file cannot disagree with
    itself, and each group keeps the objective it was written for: that is the
    objective its exercises are budgeted against, whatever else they name. Each
    objective carries both its budget and the count written for it, the budget being
    what the paper is to print of them.
    """
    groups: list[dict] = []
    counts: list[dict] = []
    number = group_number = word_problems = 0

    for row in budget_rows:
        objective = row["id"]
        written_here = word_problems_here = 0
        for group in written[objective]:
            group_number += 1
            body = group.model_dump(exclude_defaults=True)
            rows = []
            for exercise in body["exercises"]:
                number += 1
                written_here += 1
                if exercise.get("word_problem"):
                    word_problems_here += 1
                rows.append({"id": f"exercise_{number}", **exercise})
            groups.append(
                {"id": f"group_{group_number}", "objective": objective, **body, "exercises": rows}
            )
        counts.append({
            "id": objective,
            "budget": row["exercises"],
            "exercises": written_here,
            "word_problems": word_problems_here,
        })
        word_problems += word_problems_here

    return {
        "total": number,
        "budget": sum(row["exercises"] for row in budget_rows),
        "word_problems": word_problems,
        "objectives": counts,
        "groups": groups,
    }


def written_exercises(path: Path, envelope_keys: set[str], budget_rows: list[dict]) -> bool:
    """True when a section already holds exercises of the shape written today.

    The per-objective counts are checked against the budget as it stands, not just
    against the schema, because this stage reads a file another stage can rewrite:
    a re-budgeted section leaves exercises divided the old way, and those have to
    be written again rather than carried forward.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        body = document["exercises"]
        if set(document) != envelope_keys or set(body) != set(EXERCISE_KEYS):
            return False
        if [(row["id"], row["budget"], row["exercises"]) for row in body["objectives"]] != [
            (row["id"], row["exercises"], row["exercises"] + EXTRA_EXERCISES)
            for row in budget_rows
        ]:
            return False

        ids = [row["id"] for row in budget_rows]
        total = word_problems = 0
        per_objective: dict[str, int] = {name: 0 for name in ids}
        for group in body["groups"]:
            if not GROUP_REQUIRED <= set(group) <= GROUP_KEYS:
                return False
            if group["objective"] not in per_objective:
                return False
            allowed = ids[: ids.index(group["objective"]) + 1]
            for row in group["exercises"]:
                if not ROW_REQUIRED <= set(row) <= ROW_KEYS:
                    return False
                total += 1
                if row["id"] != f"exercise_{total}":
                    return False
                if row.get("word_problem"):
                    word_problems += 1
                if group["objective"] not in row["objectives"]:
                    return False
                if any(name not in allowed for name in row["objectives"]):
                    return False
                per_objective[group["objective"]] += 1
            ExerciseGroup.model_validate(group)

        if body["total"] != total or body["word_problems"] != word_problems:
            return False
        if body["budget"] != sum(row["exercises"] for row in budget_rows):
            return False
        if any(row["exercises"] != per_objective[row["id"]] for row in body["objectives"]):
            return False
    except (OSError, LookupError, TypeError, ValueError):
        return False
    return True


def partial_path(stage_dir: Path, stem: str) -> Path:
    """Where a section's finished objectives wait until the rest land."""
    return stage_dir / PARTIAL_DIR / f"{stem}.json"


def kept_objectives(path: Path, budget_rows: list[dict]) -> dict[str, list[ExerciseGroup]]:
    """Objectives read from a sidecar that still match the budget and validate.

    A re-budgeted or re-specced section drops anything that no longer fits, rather
    than carrying it forward. A missing or unreadable file returns nothing.
    """
    budgets = {row["id"]: row["exercises"] for row in budget_rows}
    ids = [row["id"] for row in budget_rows]
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        rows = document["objectives"]
    except (OSError, LookupError, TypeError, ValueError):
        return {}

    kept: dict[str, list[ExerciseGroup]] = {}
    for entry in rows:
        objective = entry.get("id")
        if objective not in budgets:
            continue
        budget = budgets[objective]
        if entry.get("budget") != budget:
            continue
        if entry.get("exercises") != budget + EXTRA_EXERCISES:
            continue
        allowed = ids[: ids.index(objective) + 1]
        groups: list[ExerciseGroup] = []
        count = 0
        group_keys = set(ExerciseGroup.model_fields)
        group_required = {
            name
            for name, field in ExerciseGroup.model_fields.items()
            if field.is_required()
        }
        try:
            for raw in entry.get("groups") or []:
                if not group_required <= set(raw) <= group_keys:
                    raise ValueError("group shape")
                group = ExerciseGroup.model_validate(raw)
                groups.append(group)
                for exercise in group.exercises:
                    count += 1
                    if objective not in exercise.objectives:
                        raise ValueError("objective")
                    if any(name not in allowed for name in exercise.objectives):
                        raise ValueError("allowed")
        except ValueError:
            continue
        if count != budget + EXTRA_EXERCISES:
            continue
        kept[objective] = groups
    return kept


def write_partial(
    path: Path,
    book_id: str,
    number: str,
    kept: dict[str, list[ExerciseGroup]],
    budget_rows: list[dict],
) -> None:
    """Write the objectives that landed, without numbering them as a paper would."""
    budgets = {row["id"]: row["exercises"] for row in budget_rows}
    objectives = []
    for row in budget_rows:
        objective = row["id"]
        if objective not in kept:
            continue
        groups = kept[objective]
        objectives.append({
            "id": objective,
            "budget": budgets[objective],
            "exercises": sum(len(group.exercises) for group in groups),
            "groups": [group.model_dump(exclude_defaults=True) for group in groups],
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"book_id": book_id, "number": number, "objectives": objectives},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def describe(body: dict) -> str:
    """The counts worth seeing on one line while a book runs."""
    sets = sum(1 for group in body["groups"] if len(group["exercises"]) > 1)
    figures = sum(len(group.get("figures") or []) for group in body["groups"]) + sum(
        len(row.get("figures") or []) for group in body["groups"] for row in group["exercises"]
    )
    notes = [
        f"{body['total']} exercises for a budget of {body['budget']}",
        f"{body['word_problems']} word problems",
    ]
    if sets:
        notes.append(f"{sets} set(s)")
    if figures:
        notes.append(f"{figures} figure(s)")
    return ", ".join(notes)


def budget_shares(book_id: str, stem: str, objectives: list[dict]) -> list[dict]:
    """The counts budgets.py settled, in the order the spec lists the objectives.

    Put back into the spec's order rather than trusted as it comes, because that
    order is what "earlier" and "later" mean to this stage: it decides which
    objectives an exercise may lean on, and the order the paper is assembled in.
    """
    path = OUTPUT_DIR / book_id / BUDGETS_STAGE / f"{stem}.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))["budget"]["objectives"]
    except FileNotFoundError:
        raise RuntimeError(f"no budget at {path.relative_to(PROJECT_ROOT)}") from None
    except (OSError, LookupError, ValueError) as error:
        raise RuntimeError(f"cannot read {path.relative_to(PROJECT_ROOT)}: {error}") from error

    counts = {row["id"]: row["exercises"] for row in rows}
    unbudgeted = [row["id"] for row in objectives if row["id"] not in counts]
    if unbudgeted:
        raise RuntimeError(
            f"{path.name} does not budget {', '.join(unbudgeted)}; re-run budgets.py"
        )
    return [{"id": row["id"], "exercises": counts[row["id"]]} for row in objectives]


def written_sections(
    book_id: str,
    section: str | None,
    stage: str = EXERCISES_STAGE,
    written_by: str = "new_questions.py",
) -> list[tuple[str, dict]]:
    """A book's written sections as (stem, exercises document), or a RuntimeError.

    Lives here because this is the stage that writes them, and the stage that
    reviews them writes the same document again, corrected and then accepted, into a
    folder of its own: it reads through this and asks the stages downstream — drawing
    the figures, setting the paper — to read through it too, so there is one account
    of where a section's exercises are and one message when they are not. `stage` is
    the folder to read and `written_by` the script that fills it, for that message.

    The sections come from the reviewed book rather than from the folder. A section
    that was never written is named and skipped, and the rest of the book still
    runs; a run that finds none of the sections it was asked for is refused.
    """
    book = reviewed_book(book_id)
    rows = matching_sections(book["sections"], section)
    if section and not rows:
        raise RuntimeError(f"no section matching {section} in the reviewed {book_id}")

    found: list[tuple[str, dict]] = []
    missing: list[str] = []
    for row in rows:
        path = OUTPUT_DIR / book_id / stage / f"{row['stem']}.json"
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            document["exercises"]["groups"]
        except (OSError, LookupError, ValueError):
            missing.append(row["number"])
            continue
        found.append((row["stem"], document))

    if missing:
        print(
            f"skipping {len(missing)} section(s) with no exercises "
            f"({', '.join(missing)}); run {written_by} for those",
            flush=True,
        )
    if not found:
        raise RuntimeError(
            f"{len(missing)} section(s) have no exercises "
            f"({', '.join(missing) or 'none'}); run {written_by} first"
        )
    return found


def extraction(book_id: str, stem: str) -> dict:
    """The book's own questions for one section, or a RuntimeError naming the fix."""
    path = OUTPUT_DIR / book_id / QUESTIONS_STAGE / f"{stem}.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        document["questions"]["groups"]
    except FileNotFoundError:
        raise RuntimeError(f"no extracted questions at {path.relative_to(PROJECT_ROOT)}") from None
    except (OSError, LookupError, ValueError) as error:
        raise RuntimeError(f"cannot read {path.relative_to(PROJECT_ROOT)}: {error}") from error
    return document


@dataclass(frozen=True)
class Run:
    """Everything an objective is written against that the whole book shares.

    Gathered into one object because a worker needs all of it and none of it
    changes between objectives. The client and the logger are the two that matter
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
    # The letters a reply may write beyond what the request itself uses: the book's
    # own alphabet, read off its profile.
    alphabet: frozenset[str] = frozenset()
    force_generate: bool = False
    section: str | None = None
    update: bool = False


def task_name(section: dict, objective: dict) -> str:
    """What one call is called on screen: the section and the objective in it.

    An objective is this stage's unit of work, and the same name is what the
    progress object follows it by, so a line about it and the count of what is
    left cannot come apart.
    """
    return f"{section['number']} {objective['id']}"


def write_objective(
    run: Run,
    section: dict,
    objective: dict,
    count: int,
    examples: str,
    source_stems: dict[str, str] | None = None,
    extraction_doc: dict | None = None,
) -> tuple[str, list[ExerciseGroup], str]:
    """Write one objective's exercises, returning them and the line to print.

    Runs on a thread of its own and writes nothing to disk: a section's file is
    written by the caller once every one of its objectives has landed, because a
    paper that holds some of its objectives is not a paper. The wait itself is
    reported from here, since being handed a worker is what starts it.
    """
    ids = [row["id"] for row in section["spec"]["objectives"]]
    allowed = ids[: ids.index(objective["id"]) + 1]
    number_path = section["number"]
    name = task_name(section, objective)

    started = time.perf_counter()
    run.progress.asking(name, f"{count} exercises to write, asking the model")
    reply = request_exercises(
        run.client,
        run.model,
        run.prompt.text,
        [
            spec_part(section, objective["id"]),
            assignment_part(objective, count, allowed[:-1]),
            examples,
            *(
                [source_stems_part(extraction_doc, objective["id"], source_stems)]
                if extraction_doc
                else []
            ),
        ],
        count,
        objective["id"],
        allowed,
        run.thinking,
        run.logger,
        f"{number_path}-{objective['id']}",
        {
            "book_id": run.book_id,
            "section": number_path,
            "section_title": section["title"],
            "objective": objective["id"],
            "target_count": count,
            "prompt_files": run.prompt.files,
            "language_code": run.meta["language_code"],
        },
        report=lambda message: run.progress.say(f"  {name}: {message}"),
        alphabet=run.alphabet,
        vector_lesson=section_is_vector_lesson(section),
        notation=str(run.meta.get("notation") or "latin"),
        language_code=str(run.meta.get("language_code") or "en"),
        source_stems=source_stems,
        force_generate=run.force_generate,
    )

    if reply.status == "abort":
        raise RuntimeError(
            f"{objective['id']}: aborted, {reply.abort_reason or 'no reason given'}"
        )

    words = sum(
        1 for group in reply.groups for exercise in group.exercises if exercise.word_problem
    )
    sets = sum(1 for group in reply.groups if len(group.exercises) > 1)
    note = f", {sets} set(s)" if sets else ""
    elapsed = since(time.perf_counter() - started)
    return (
        objective["id"],
        reply.groups,
        f"  {name}: {count} exercises, {words} word problems{note} ({elapsed})",
    )


@dataclass
class ObjectiveJob:
    """One objective still to write."""

    section: dict
    objective: dict
    count: int
    parts: list[str]
    allowed: list[str]
    source_stems: dict[str, str] | None
    item: str
    name: str
    digest: str
    feedback: str | None = None
    feedback_from: int | None = None
    feedback_problems: list[str] | None = None


class WriteStore:
    """Write each landed objective to its section's resumable partial."""

    def __init__(
        self,
        book_id: str,
        stage_dir: Path,
        budgets: dict[str, list[dict]],
        written: dict[str, dict[str, list[ExerciseGroup]]],
        pending: list[dict],
    ) -> None:
        self._lock = threading.Lock()
        self.book_id = book_id
        self.stage_dir = stage_dir
        self.budgets = budgets
        self.written = written
        self.pending = pending

    def has(self, stem: str, objective: str) -> bool:
        with self._lock:
            return objective in self.written.get(stem, {})

    def item_applied(self, item: str) -> bool:
        with self._lock:
            return any(
                item == f"{row['number']}-{objective['id']}"
                and objective["id"] in self.written.get(row["stem"], {})
                for row in self.pending
                for objective in row["spec"]["objectives"]
            )

    def save(self, job: ObjectiveJob, groups: list[ExerciseGroup]) -> None:
        with self._lock:
            stem = job.section["stem"]
            self.written.setdefault(stem, {})[job.objective["id"]] = groups
            write_partial(
                partial_path(self.stage_dir, stem),
                self.book_id,
                job.section["number"],
                self.written[stem],
                self.budgets[stem],
            )


def objective_jobs(run: Run, tasks: list[tuple]) -> list[ObjectiveJob]:
    """Turn the existing unary task tuples into stable Batch items."""
    jobs: list[ObjectiveJob] = []
    for section, objective, count, examples, source_stems, extraction_doc in tasks:
        ids = [row["id"] for row in section["spec"]["objectives"]]
        allowed = ids[: ids.index(objective["id"]) + 1]
        parts = [
            spec_part(section, objective["id"]),
            assignment_part(objective, count, allowed[:-1]),
            examples,
            *(
                [source_stems_part(extraction_doc, objective["id"], source_stems)]
                if extraction_doc
                else []
            ),
        ]
        item = f"{section['number']}-{objective['id']}"
        digest = hashlib.sha256(
            "\n".join(
                [
                    run.prompt.text,
                    *parts,
                    run.thinking,
                    str(run.force_generate),
                ]
            ).encode("utf-8")
        ).hexdigest()
        jobs.append(
            ObjectiveJob(
                section=section,
                objective=objective,
                count=count,
                parts=parts,
                allowed=allowed,
                source_stems=source_stems,
                item=item,
                name=task_name(section, objective),
                digest=digest,
            )
        )
    return jobs


def objective_context(run: Run, job: ObjectiveJob) -> dict:
    return {
        "book_id": run.book_id,
        "section": job.section["number"],
        "section_title": job.section["title"],
        "objective": job.objective["id"],
        "target_count": job.count,
        "prompt_files": run.prompt.files,
        "language_code": run.meta["language_code"],
    }


def objective_request(
    run: Run, job: ObjectiveJob, attempt: int
) -> types.InlinedRequest:
    prompt = (
        run.prompt.text + FORCE_GENERATE_PROMPT
        if run.force_generate
        else run.prompt.text
    )
    contents = [prompt, *job.parts]
    if job.feedback:
        contents.append(job.feedback)
    return types.InlinedRequest(
        contents=contents,
        metadata={
            "item": job.item,
            "log_key": f"{run.book_id}|{job.item}|a{attempt}",
        },
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=WriteReplyForced if run.force_generate else WriteReply,
            thinking_config=types.ThinkingConfig(thinking_level=run.thinking),
        ),
    )


def parse_objective_response(
    run: Run, job: ObjectiveJob, response: Any
) -> tuple[WriteReply, dict]:
    """Apply the same parsing, repairs, and checks as the unary path."""
    raw = genai_answer_text(response)
    if run.force_generate:
        forced = WriteReplyForced.model_validate_json(raw)
        result = WriteReply(status="ok", groups=forced.groups)
    else:
        result = WriteReply.model_validate_json(raw)
    repairs: dict = {}
    if result.status != "ok":
        return result, repairs
    result.groups, repairs = repaired(
        result.groups,
        job.count,
        vector_lesson=section_is_vector_lesson(job.section),
        notation=str(run.meta.get("notation") or "latin"),
        language_code=str(run.meta.get("language_code") or "en"),
        source_stems=job.source_stems,
    )
    result.groups = repair_groups_excessive_tashkeel(result.groups)
    result.groups = repair_groups_mark_placement(result.groups)
    prompt = (
        run.prompt.text + FORCE_GENERATE_PROMPT
        if run.force_generate
        else run.prompt.text
    )
    known = run.alphabet | frozenset(
        character
        for character in prompt + "".join(job.parts)
        if character.isalpha()
    )
    if padding := repairs.get("form_set_padding"):
        if not run.force_generate:
            raise ProblemsError(padding)
    check_write(
        result,
        job.count,
        job.objective["id"],
        job.allowed,
        known,
    )
    return result, repairs


def log_objective_batch_call(
    run: Run,
    job: ObjectiveJob,
    attempt: int,
    response: Any,
    result: WriteReply | None,
    error_message: str | None,
    duration: float,
    batch_name: str,
    repairs: dict,
) -> None:
    prompt = (
        run.prompt.text + FORCE_GENERATE_PROMPT
        if run.force_generate
        else run.prompt.text
    )
    extra = {
        **objective_context(run, job),
        "thinking_level": run.thinking,
        "returned": result.status if result else None,
        "finish_reason": finish_reason(response),
        "batch": True,
        "batch_key": f"{run.book_id}|{job.item}|a{attempt}",
        "batch_name": batch_name,
        **repairs,
    }
    if job.feedback:
        extra["feedback_from_attempt"] = job.feedback_from
        extra["feedback_problems"] = job.feedback_problems
    safe_log(
        run.logger,
        files_builder=lambda captured=response: {
            "prompt.txt": prompt,
            **{
                f"part_{number}.txt": part
                for number, part in enumerate(job.parts, start=1)
            },
            **({"feedback.txt": job.feedback} if job.feedback else {}),
            **(
                genai_response_files(captured)
                if captured is not None
                else {}
            ),
        },
        page_number=job.item,
        step_name=WRITE_STEP,
        attempt=attempt,
        api="gemini",
        model=run.model,
        usage=genai_usage(response) if response is not None else {},
        duration_seconds=duration,
        status="success" if result is not None else "error",
        error_message=error_message,
        extra=extra,
    )


def apply_objective_row(
    run: Run,
    store: WriteStore,
    job: ObjectiveJob,
    row: Any,
    attempt: int,
    elapsed: float,
    batch_name: str,
) -> tuple[bool, str | None, list[str] | None, bool]:
    response, error_message = response_and_error(row)
    parsed = None
    result = None
    problems: list[str] | None = None
    repairs: dict = {}
    if error_message is None:
        try:
            if response is None:
                raise ValueError("no batch response for this request")
            parsed = generate_content_response(response)
            result, repairs = parse_objective_response(run, job, parsed)
        except ProblemsError as error:
            error_message = f"{type(error).__name__}: {error}"
            problems = error.problems
            result = None
        except ValidationError as error:
            error_message = f"{type(error).__name__}: {error}"
            problems = pydantic_problems(error)
            result = None
        except Exception as error:  # noqa: BLE001
            error_message = f"{type(error).__name__}: {error}"
            result = None
    log_objective_batch_call(
        run,
        job,
        attempt,
        parsed,
        result,
        error_message,
        elapsed,
        batch_name,
        repairs,
    )
    if result is None:
        return False, error_message, problems, False
    if result.status == "abort":
        return (
            False,
            f"aborted, {result.abort_reason or 'no reason given'}",
            None,
            True,
        )
    store.save(job, result.groups)
    return True, None, None, False


def objective_chunk(
    run: Run, jobs: list[ObjectiveJob], attempt: int, label: str
) -> BatchChunk:
    display = batch_display_name(
        run.book_id, BATCH_STAGE, label, run_id=new_run_id()
    )

    def build_src() -> tuple[Any, str | None]:
        return src_for_chunk(
            run.client,
            [(job.item, objective_request(run, job, attempt)) for job in jobs],
            display,
        )

    return BatchChunk(
        label=label,
        display_name=display,
        attempt=attempt,
        keys={job.item: job.digest for job in jobs},
        items=list(jobs),
        build_src=build_src,
    )


def objective_chunks(
    run: Run, jobs: list[ObjectiveJob], attempt: int
) -> list[BatchChunk]:
    by_item = {job.item: job for job in jobs}
    pairs = [
        (job.item, objective_request(run, job, attempt))
        for job in jobs
    ]
    return [
        objective_chunk(
            run,
            [by_item[item] for item, _request in chunk],
            attempt,
            f"a{attempt}-b{number}",
        )
        for number, chunk in enumerate(chunk_requests(pairs), start=1)
    ]


def adopted_objective_chunk(
    entry: dict[str, Any], by_item: dict[str, ObjectiveJob]
) -> BatchChunk:
    keys = {
        str(key): str(digest)
        for key, digest in (entry.get("keys") or {}).items()
    }
    return BatchChunk(
        label=str(entry.get("display_name") or "resume"),
        display_name=str(entry.get("display_name") or ""),
        attempt=int(entry.get("attempt") or 1),
        keys=keys,
        items=[by_item[key] for key in keys if key in by_item],
        build_src=None,
    )


def write_objectives_batch(
    run: Run,
    store: WriteStore,
    jobs: list[ObjectiveJob],
    state: BatchState,
    *,
    adopted: list[dict[str, Any]] | None = None,
    prefetched: dict[str, BatchStatus] | None = None,
    parallel: int = DEFAULT_BATCH_PARALLEL,
    download_workers: int = DEFAULT_DOWNLOAD_WORKERS,
    submit_leftovers: bool = True,
) -> tuple[list[str], set[str]]:
    """Run objective attempts through Batch and save every successful row."""
    failures: list[str] = []
    abandoned: set[str] = set()
    pending = list(jobs)
    adopted_entries = list(adopted or [])
    finalized: set[str] = set()

    def account(
        job: ObjectiveJob,
        ok: bool,
        error_message: str | None,
        problems: list[str] | None,
        terminal: bool,
        attempt: int,
        still: list[ObjectiveJob],
    ) -> None:
        if job.item in finalized or job in still:
            return
        if ok or store.has(job.section["stem"], job.objective["id"]):
            finalized.add(job.item)
            run.progress.done(job.name, f"  {job.name}")
            return
        if not terminal and attempt < MAX_ATTEMPTS:
            if problems:
                job.feedback = retry_feedback(problems)
                job.feedback_from = attempt
                job.feedback_problems = problems
            else:
                job.feedback = None
                job.feedback_from = None
                job.feedback_problems = None
            still.append(job)
            run.progress.say(
                f"  {job.name}: attempt {attempt} of {MAX_ATTEMPTS} failed, "
                f"{one_line(error_message or 'no reason given')}; asking again"
            )
            return
        finalized.add(job.item)
        failures.append(
            f"{job.name}: {error_message}"
            if terminal
            else f"{job.name}: failed after {MAX_ATTEMPTS} attempts: {error_message}"
        )
        run.progress.done(
            job.name,
            f"  {job.name}: FAILED {one_line(error_message or 'no reason given')}",
        )

    for attempt in range(1, MAX_ATTEMPTS + 1):
        if not pending and not (attempt == 1 and adopted_entries):
            break
        still: list[ObjectiveJob] = []
        by_item = {job.item: job for job in pending}
        adopted_keys: set[str] = set()
        adopted_pairs: list[tuple[dict[str, Any], BatchChunk]] = []
        if attempt == 1:
            for entry in adopted_entries:
                adopted_keys.update(str(key) for key in (entry.get("keys") or {}))
                adopted_pairs.append(
                    (entry, adopted_objective_chunk(entry, by_item))
                )
        leftover = [
            job
            for job in pending
            if job.item not in adopted_keys and submit_leftovers
        ]
        queue = objective_chunks(run, leftover, attempt)

        def on_submit(chunk: BatchChunk) -> None:
            for job in chunk.items:
                sent = sum(len(part.encode("utf-8")) for part in job.parts)
                run.progress.asking(
                    job.name,
                    f"{in_kb(sent)} sent, in batch {chunk.label}",
                )

        def on_finished(
            entry: dict[str, Any],
            chunk: BatchChunk,
            status: BatchStatus,
            elapsed: float,
        ) -> tuple[str, list[Any], str | None]:
            name = str(entry.get("name") or status.name or "")
            remote = status.job or get_batch_job(
                run.client, name, timeout_ms=FULL_GET_TIMEOUT_MS
            )
            token = finish_token(remote)
            result_file = result_file_name(remote) if remote is not None else None
            if token != FINISH_DONE:
                return token, [], result_file
            rows = map_rows_by_key(inlined_responses(run.client, remote))
            outcomes: list[Any] = []
            for job in chunk.items:
                if store.has(job.section["stem"], job.objective["id"]):
                    outcomes.append((job, True, None, None, False))
                    continue
                if chunk.keys.get(job.item) != job.digest:
                    outcomes.append(
                        (job, False, "stale batch row", None, False)
                    )
                    continue
                row = rows.get(job.item)
                if row is None:
                    log_objective_batch_call(
                        run,
                        job,
                        int(entry.get("attempt") or chunk.attempt),
                        None,
                        None,
                        "no batch response for this request",
                        elapsed,
                        name,
                        {},
                    )
                    outcomes.append(
                        (
                            job,
                            False,
                            "no batch response for this request",
                            None,
                            False,
                        )
                    )
                    continue
                ok, error, problems, terminal = apply_objective_row(
                    run,
                    store,
                    job,
                    row,
                    int(entry.get("attempt") or chunk.attempt),
                    elapsed,
                    name,
                )
                outcomes.append((job, ok, error, problems, terminal))
            return FINISH_DONE, outcomes, result_file

        def on_account(chunk: BatchChunk, outcomes: list[Any]) -> None:
            for job, ok, error, problems, terminal in outcomes:
                account(job, ok, error, problems, terminal, attempt, still)

        def on_failed(chunk: BatchChunk, message: str) -> None:
            for job in chunk.items:
                account(job, False, message, None, False, attempt, still)

        def on_abandoned(chunk: BatchChunk, message: str) -> None:
            for job in chunk.items:
                abandoned.add(job.item)
                account(job, False, message, None, True, attempt, still)

        def on_requeue(chunk: BatchChunk) -> list[BatchChunk]:
            retry = [
                job
                for job in chunk.items
                if not store.has(job.section["stem"], job.objective["id"])
            ]
            return objective_chunks(run, retry, attempt) if submit_leftovers else []

        run_batch_jobs(
            run.client,
            run.model,
            state,
            {
                "stage": BATCH_STAGE,
                "book_id": run.book_id,
                "model": run.model,
                "thinking": run.thinking,
                "section": getattr(run, "section", None),
                "update": getattr(run, "update", False),
                "force_generate": run.force_generate,
            },
            queue,
            adopted_pairs if attempt == 1 else [],
            parallel,
            attempt,
            on_finished,
            on_failed,
            on_submit=on_submit,
            prefetched=prefetched if attempt == 1 else {},
            download_workers=download_workers,
            on_account=on_account,
            on_abandoned=on_abandoned,
            on_requeue=on_requeue,
        )
        adopted_entries = []
        pending = still
        if pending and attempt < MAX_ATTEMPTS:
            time.sleep(2**attempt)
    return failures, abandoned


def objective_state_mismatch(
    data: dict[str, Any],
    book_id: str,
    model: str,
    thinking: str,
    section: str | None,
    force_generate: bool,
) -> str | None:
    expected = {
        "stage": BATCH_STAGE,
        "book_id": book_id,
        "model": model,
        "thinking": thinking,
        "section": section,
        "force_generate": force_generate,
    }
    for key, value in expected.items():
        if key in data and data.get(key) != value:
            return f"saved batch state has {key}={data.get(key)!r}, not {value!r}"
    return None


def resume_objective_jobs(
    run: Run, state: BatchState
) -> tuple[list[dict[str, Any]], dict[str, BatchStatus]]:
    data = state.load()
    current = entries(data)
    if not current:
        return [], {}
    if is_legacy(data):
        state.save_entries(
            {key: value for key, value in data.items() if key != "job"},
            current,
        )
    missing_names = [
        str(entry.get("display_name") or "")
        for entry in current
        if not entry.get("name") and entry.get("display_name")
    ]
    found = (
        find_jobs_by_display_names(run.client, missing_names)
        if missing_names
        else {}
    )
    adopted: list[dict[str, Any]] = []
    prefetched: dict[str, BatchStatus] = {}
    for entry in list(current):
        display = str(entry.get("display_name") or "")
        name = entry.get("name")
        if not name:
            remote, warning = pick_duplicate_match(found.get(display) or [])
            if warning:
                print(f"  {warning}", flush=True)
            if remote is None:
                state.remove_entry(display)
                continue
            name = remote.name
            state.set_entry_field(display, "name", name)
            entry["name"] = name
        status = get_batch_status(run.client, str(name))
        if status is None or status.state in BATCH_FAILED:
            delete_files(
                run.client,
                entry.get("src_file_name"),
                result_file_name(status.job)
                if status is not None and status.job is not None
                else None,
            )
            state.remove_entry(display)
            continue
        adopted.append(entry)
        if status.state != "UNKNOWN":
            prefetched[str(name)] = status
    return adopted, prefetched


def write_book(
    book_id: str,
    section: str | None,
    model: str,
    thinking: str,
    update: bool,
    max_workers: int = MAX_WORKERS,
    force_generate: bool = False,
    *,
    batch: bool = DEFAULT_BATCH,
    discard_batch_state: bool = False,
    batch_parallel: int = DEFAULT_BATCH_PARALLEL,
    download_workers: int = DEFAULT_DOWNLOAD_WORKERS,
) -> tuple[Path, int]:
    """Write exercises for every section of a budgeted book, or for just one."""
    # Said before the read rather than after, because the reviewed book is read
    # and validated whole before this stage knows how many sections it even has.
    print(f"reading the reviewed {book_id}", flush=True)
    book = reviewed_book(book_id)
    sections = matching_sections(book["sections"], section)
    if section and not sections:
        raise RuntimeError(f"no section matching {section} in the reviewed {book_id}")

    # The reviewed book's attributes are what this stage writes onto its own files. The
    # prompt is chosen by the table of contents instead: it is the one place a book says
    # what language, numerals and notation it is, and a book reviewed before those were
    # recorded still has them there.
    meta = book_attributes(book)
    prompt = build_prompt(book_metadata(book_id), book_id)
    stage_dir = OUTPUT_DIR / book_id / EXERCISES_STAGE
    envelope_keys = {"book_id", *meta, "number", "title", "module_path", "exercises"}

    # Every section's budget and examples are read before any call goes out, so a
    # book that has not been budgeted or extracted stops here rather than after a
    # book's worth of calls.
    print(
        f"reading the budget and the book's own questions for {len(sections)} section(s)",
        flush=True,
    )
    missing: list[str] = []
    budgets: dict[str, list[dict]] = {}
    examples: dict[str, dict] = {}
    for row in sections:
        try:
            budgets[row["stem"]] = budget_shares(
                book_id, row["stem"], row["spec"]["objectives"]
            )
            examples[row["stem"]] = extraction(book_id, row["stem"])
        except RuntimeError as error:
            missing.append(f"{row['number']}: {error}")
    if missing:
        raise RuntimeError(
            f"{len(missing)} section(s) are not ready: {'; '.join(missing)}; "
            f"run budgets.py and source_questions.py first"
        )

    pending: list[dict] = []
    skipped = 0
    for row in sections:
        out_path = stage_dir / f"{row['stem']}.json"
        partial = partial_path(stage_dir, row["stem"])
        if not update and written_exercises(out_path, envelope_keys, budgets[row["stem"]]):
            skipped += 1
            partial.unlink(missing_ok=True)
            continue
        pending.append(row)

    book_dir = OUTPUT_DIR / book_id
    state = BatchState(batch_state_path(book_dir, BATCH_STAGE))
    if discard_batch_state:
        if state.path.is_file():
            print(
                "  discarding local batch state (the remote job is not cancelled)",
                flush=True,
            )
        state.forget()
    resuming_batch = state.path.is_file()

    written: dict[str, dict[str, list[ExerciseGroup]]] = {}
    kept_from_run = 0
    for row in pending:
        partial = partial_path(stage_dir, row["stem"])
        if update and not resuming_batch:
            partial.unlink(missing_ok=True)
            written[row["stem"]] = {}
        else:
            seed = kept_objectives(partial, budgets[row["stem"]])
            written[row["stem"]] = seed
            kept_from_run += len(seed)

    failures: list[str] = []

    from src.source_form_stems import source_form_stems_for_objective

    tasks = [
        (
            row,
            objective,
            share["exercises"] + EXTRA_EXERCISES,
            examples_part(examples[row["stem"]], share["id"]),
            source_form_stems_for_objective(examples[row["stem"]], share["id"]),
            examples[row["stem"]],
        )
        for row in pending
        for objective, share in zip(row["spec"]["objectives"], budgets[row["stem"]])
        if objective["id"] not in written.get(row["stem"], {})
    ]

    store = WriteStore(book_id, stage_dir, budgets, written, pending)
    data = state.load()
    if data:
        pending_saved = [
            str(key)
            for entry in entries(data)
            for key in (entry.get("keys") or {})
            if not store.item_applied(str(key))
        ]
        if not pending_saved:
            state.forget()
            data = None
        else:
            mismatch = objective_state_mismatch(
                data,
                book_id,
                model,
                thinking,
                section,
                force_generate,
            )
            if mismatch:
                raise RuntimeError(
                    f"{mismatch}. Pass --discard-batch-state to drop the local "
                    "state only; the remote job is not cancelled."
                )

    if tasks:
        # Asked for here rather than at the top of the run, because a book whose
        # sections are all written has nothing to call for.
        if not any(os.environ.get(name) for name in API_KEY_VARS):
            raise RuntimeError(
                f"{len(tasks)} objective(s) still to write; "
                f"set one of {' / '.join(API_KEY_VARS)} to call the model"
            )
        # Every objective still missing at once. A section is only as done as its
        # slowest objective, so holding sections apart would leave workers idle
        # while one long objective finished.
        workers = min(len(tasks), max_workers)
        exercises = sum(task[2] for task in tasks)
        opening = (
            f"writing {exercises} exercises for {len(tasks)} objective(s) "
            f"across {len(pending)} section(s)"
        )
        opening += (
            " through the Batch API"
            if batch
            else f", with {workers} worker(s)"
        )
        if skipped:
            opening += f"; {skipped} already written"
        if kept_from_run:
            opening += f"; {kept_from_run} objective(s) kept from an earlier run"
        if force_generate:
            opening += "; abort refusals retried (--force-generate)"
        print(opening, flush=True)

        # Both the client and the logger reach the network to start up, so a run
        # that writes nothing needs neither. The logger's clock is set once here
        # rather than once per worker, and it is named on its own line because
        # setting it against NTP is the wait that can run into seconds.
        print("  opening the model client", flush=True)
        client = genai.Client()
        print("  setting the log clock", flush=True)
        logger = ApiLogger(OUTPUT_DIR / book_id)

        lock: BatchLock | None = None
        if batch or state.path.is_file():
            lock = BatchLock(batch_lock_path(book_dir, BATCH_STAGE))
            lock.acquire()
        try:
            with Progress(len(tasks)) as progress:
                run = Run(
                    client=client,
                    logger=logger,
                    book_id=book_id,
                    meta=meta,
                    model=model,
                    thinking=thinking,
                    prompt=prompt,
                    progress=progress,
                    alphabet=Profile.of(book_metadata(book_id)).alphabet,
                    force_generate=force_generate,
                    section=section,
                    update=update,
                )
                jobs = objective_jobs(run, tasks)
                adopted: list[dict[str, Any]] = []
                prefetched: dict[str, BatchStatus] = {}
                if entries(state.load()):
                    adopted, prefetched = resume_objective_jobs(run, state)
                leftover = [
                    job
                    for job in jobs
                    if not store.has(
                        job.section["stem"], job.objective["id"]
                    )
                ]
                abandoned: set[str] = set()
                if batch:
                    batch_failures, abandoned = write_objectives_batch(
                        run,
                        store,
                        leftover,
                        state,
                        adopted=adopted,
                        prefetched=prefetched,
                        parallel=batch_parallel,
                        download_workers=download_workers,
                    )
                    failures.extend(batch_failures)
                else:
                    if adopted:
                        batch_failures, abandoned = write_objectives_batch(
                            run,
                            store,
                            leftover,
                            state,
                            adopted=adopted,
                            prefetched=prefetched,
                            parallel=batch_parallel,
                            download_workers=download_workers,
                            submit_leftovers=False,
                        )
                        failures.extend(batch_failures)
                    task_by_item = {
                        f"{task[0]['number']}-{task[1]['id']}": task
                        for task in tasks
                    }
                    unary_tasks = [
                        task_by_item[job.item]
                        for job in jobs
                        if job.item not in abandoned
                        and not store.has(
                            job.section["stem"], job.objective["id"]
                        )
                    ]
                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        futures = {
                            pool.submit(write_objective, run, *task): (
                                task[0],
                                task[1],
                            )
                            for task in unary_tasks
                        }
                        for future in as_completed(futures):
                            row, target = futures[future]
                            name = task_name(row, target)
                            try:
                                objective, groups, line = future.result()
                            except Exception as error:  # noqa: BLE001
                                failures.append(
                                    f"{row['number']}: "
                                    f"{type(error).__name__}: {error}"
                                )
                                progress.done(
                                    name,
                                    f"  {name}: failed, "
                                    f"{one_line(f'{type(error).__name__}: {error}')}, "
                                    f"keeping the rest of section {row['number']}",
                                )
                                continue
                            store.save(
                                next(
                                    job
                                    for job in jobs
                                    if job.item
                                    == f"{row['number']}-{objective}"
                                ),
                                groups,
                            )
                            progress.done(name, line)
        except KeyboardInterrupt:
            if lock is not None:
                print(
                    "interrupted; batch state kept, rerun to resume",
                    flush=True,
                )
                sys.stderr.flush()
                os._exit(130)
            raise
        finally:
            if lock is not None:
                lock.release()

    # A section is written once every one of its objectives has landed; the rest
    # are kept in the sidecar for the next run.
    complete = [
        row
        for row in pending
        if len(written.get(row["stem"], {})) == len(budgets[row["stem"]])
    ]
    complete_stems = {row["stem"] for row in complete}
    for row in pending:
        if row["stem"] in complete_stems:
            continue
        kept = written.get(row["stem"], {})
        if kept:
            write_partial(
                partial_path(stage_dir, row["stem"]),
                book_id,
                row["number"],
                kept,
                budgets[row["stem"]],
            )

    if complete:
        print(f"writing {len(complete)} section file(s)", flush=True)

    partial_root = stage_dir / PARTIAL_DIR
    for row in complete:
        body = exercise_document(written[row["stem"]], budgets[row["stem"]])
        paper = canonicalize_document(
            {
                "book_id": book_id,
                **meta,
                "number": row["number"],
                "title": row["title"],
                "module_path": row["module_path"],
                "exercises": body,
            }
        )
        out_path = stage_dir / f"{row['stem']}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(paper, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        partial_path(stage_dir, row["stem"]).unlink(missing_ok=True)
        print(f"  {row['number']}: {describe(body)}", flush=True)

    if partial_root.is_dir() and not any(partial_root.iterdir()):
        partial_root.rmdir()

    part_written = [
        row
        for row in pending
        if row["stem"] not in complete_stems and written.get(row["stem"])
    ]
    summary = f"{len(complete)} section(s) written"
    if skipped:
        summary += f", {skipped} already written"
    if part_written:
        kept_objs = sum(len(written[row["stem"]]) for row in part_written)
        total_objs = sum(len(budgets[row["stem"]]) for row in part_written)
        summary += (
            f", {len(part_written)} section(s) part-written, "
            f"{kept_objs} of {total_objs} objective(s) kept for the next run"
        )
        if failures:
            summary += f" after {len(failures)} failed call(s)"
    elif failures:
        summary += (
            f", {len(pending) - len(complete)} given up on after "
            f"{len(failures)} failed call(s)"
        )
    print(f"OK: {summary} under {stage_dir.relative_to(PROJECT_ROOT)}")
    return stage_dir, len(failures)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_id", help="Book ID, e.g. eg_mathematics_en_g7_t1")
    parser.add_argument(
        "-s", "--section", help="write this section, or every section under it, e.g. -s 1 or -s 01"
    )
    parser.add_argument(
        "--update", action="store_true", help="rewrite sections that already have exercises"
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
        "--force-generate",
        action="store_true",
        help="do not accept model abort; retry refusals and allow form-set padding gaps",
    )
    parser.add_argument(
        "--batch",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_BATCH,
        help="write through the Gemini Batch API (default); "
        "--no-batch uses unary generateContent",
    )
    parser.add_argument(
        "--discard-batch-state",
        action="store_true",
        help="delete the local new_questions Batch state; "
        "does not cancel the remote job",
    )
    parser.add_argument(
        "--batch-parallel",
        type=int,
        default=DEFAULT_BATCH_PARALLEL,
        metavar="N",
        help="0 = no cap (default); N = at most N Batch jobs in flight",
    )
    parser.add_argument(
        "--download-workers",
        type=int,
        default=DEFAULT_DOWNLOAD_WORKERS,
        metavar="N",
        help="parallel downloads of finished Batch jobs; 1 = serial",
    )
    args = parser.parse_args()
    if args.batch_parallel < 0:
        parser.error("--batch-parallel must be >= 0")
    if args.download_workers < 1:
        parser.error("--download-workers must be at least 1")

    try:
        _, unwritten = write_book(
            args.book_id,
            args.section,
            args.model,
            args.thinking_level,
            args.update,
            max(1, args.workers),
            force_generate=args.force_generate,
            batch=args.batch,
            discard_batch_state=args.discard_batch_state,
            batch_parallel=args.batch_parallel,
            download_workers=args.download_workers,
        )
    except RuntimeError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    return 1 if unwritten else 0


if __name__ == "__main__":
    sys.exit(main())
