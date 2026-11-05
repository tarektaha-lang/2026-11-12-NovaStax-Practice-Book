#!/usr/bin/env python3
"""Draw a count-and-shade lattice from its numbers, with no model in between.

A grid figure is a lattice of equal squares and a number of those squares to fill.
Those integers live in the figure's `grid` field — `cols`, `rows`, `shaded`, and
how many extra full copies stand beside it — and the TikZ is one template. Asking
a model for that is paying to parse integers out of a sentence. A figure written
before the field existed is still drawn from its `description`; everything new
carries the field, and that is what is plotted.

The stem, the question and the answer stay out: they are what the student is
working on, and putting them on the picture hands the exercise over.

    python3 scripts/figures/grid.py
    python3 scripts/figures/grid.py sa_mathematics_ar_g6_t2
"""

from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from sources import OUTPUT_DIR  # noqa: E402

EASTERN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

# A size written in brackets, "10×10" or "4×5", times sign or ASCII stand-in.
TIMES = re.compile(r"(\d+)\s*[×xX*]\s*(\d+)")

# How many cells the lattice is cut into. The noun after the number — مربع, بلاط —
# is left unmatched, so tanween and the like do not matter.
CELLS = re.compile(r"مقسم\S*\s+إلى\s+(\d+)|divided into\s+(\d+)", re.IGNORECASE)

# Shaded on the remainder grid. `مظلل منها N` is the multi-grid remainder;
# `تم تظليل N` is a single grid. `مربعات منها` ("squares of them") is not the
# first of those, because `مظلل` is not next to `منها`.
SHADED_OF = re.compile(r"مظلل(?:ة)?\s+منها\s+(\d+)")
SHADED_WERE = re.compile(r"تم تظليل\s+(\d+)|(\d+)\s+shaded", re.IGNORECASE)
SHADED_ONE = re.compile(r"تظليل\s+(?:مربع|بلاط\S*)\s+واحد")

# The complement, used only to check the count. Never taken as the fill.
UNSHADED = re.compile(r"ترك\s+(\d+)[^.\n]*غير مظلل|(?:and\s+)?(\d+)\s+unshaded")

# More than one lattice side by side, for a percentage past a hundred.
TWO_GRIDS = re.compile(r"شبكتان|two grids", re.IGNORECASE)
THREE_GRIDS = re.compile(r"ثلاث\s+شبكات|three grids", re.IGNORECASE)
FOUR_GRIDS = re.compile(r"أربع\s+شبكات|four grids", re.IGNORECASE)
N_GRIDS = re.compile(r"(\d+)\s+شبكات|(\d+)\s+grids", re.IGNORECASE)

# The description names which cells, not how many. A count still wins; these
# only abort when there is no number to fill by.
REGION = re.compile(
    r"الصفوف|الصف\b|الأعمدة|العمود|النصف|الربع\b|متناثر|"
    r"\b(top|bottom|left|right)\s+(row|rows|half|column)|scattered|every other",
    re.IGNORECASE,
)

FIGURE_DATA_STAGE = "10_book_figure_data"


class GridError(ValueError):
    """The description cannot be drawn as a count-and-shade lattice."""


@dataclass(frozen=True)
class Grid:
    """One lattice, the cells to shade on it, and how many extra full copies."""

    cols: int
    rows: int
    shaded: int
    extra_full: int = 0


@dataclass(frozen=True)
class Drawn:
    """The picture, or why there is not one."""

    tikz: str = ""
    reason: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.tikz)


# The twelve grids of sa_mathematics_ar_g6_t2, as (cols, rows, shaded, extra_full).
# Shape follows the drawings already on disk: a square stays square, and a count
# that 5 divides is five columns — 20 cells 5×4, 50 cells 5×10, an explicit 4×5
# the same 20-cell 5×4.
EXPECTED = {
    "0348710c4fae": Grid(10, 10, 82),
    "1399a0ce0f8e": Grid(5, 4, 9),
    "258ea9f53f75": Grid(10, 10, 40, 1),
    "5d1b41f69f88": Grid(5, 5, 13),
    "769310e63472": Grid(10, 10, 50, 1),
    "777d4e7c22f6": Grid(5, 5, 9),
    "797bf52fd998": Grid(10, 10, 49),
    "b971ee391d2c": Grid(2, 2, 1),
    "c870b0da71df": Grid(5, 10, 21),
    "d1c01ed96041": Grid(10, 10, 37),
    "eb644732ddb1": Grid(5, 4, 9),
    "f22c43ccc89a": Grid(10, 10, 15, 2),
}


def western(text: str) -> str:
    """Eastern Arabic digits as the ASCII ones the rest of the parser reads."""
    return text.translate(EASTERN_DIGITS)


def lattice(cells: int) -> tuple[int, int]:
    """Columns and rows for a lattice of this many cells.

    A square stays square. Anything 5 divides is five columns, which is how the
    drawings already on disk factored 20 and 50. Anything else is the factorisation
    nearest a square, wider than it is tall.
    """
    if cells < 1:
        raise GridError(f"a grid cannot have {cells} cell(s)")
    root = math.isqrt(cells)
    if root * root == cells:
        return root, root
    if cells % 5 == 0:
        return 5, cells // 5
    best_cols, best_rows = cells, 1
    best_gap = cells - 1
    for rows in range(1, root + 1):
        if cells % rows:
            continue
        cols = cells // rows
        if cols < rows:
            cols, rows = rows, cols
        gap = cols - rows
        if gap < best_gap:
            best_cols, best_rows, best_gap = cols, rows, gap
    return best_cols, best_rows


def grid_count(text: str) -> int:
    """How many lattices stand side by side."""
    if TWO_GRIDS.search(text):
        return 2
    if THREE_GRIDS.search(text):
        return 3
    if FOUR_GRIDS.search(text):
        return 4
    if found := N_GRIDS.search(text):
        digits = found.group(1) or found.group(2)
        return int(digits)
    return 1


def cell_total(text: str) -> int | None:
    """How many cells one lattice is cut into, if the wording says."""
    if found := CELLS.search(text):
        digits = found.group(1) or found.group(2)
        return int(digits)
    return None


def explicit_size(text: str) -> tuple[int, int] | None:
    """A (width × height) written in the description, if there is one."""
    if found := TIMES.search(text):
        return int(found.group(1)), int(found.group(2))
    return None


def shaded_count(text: str) -> int | None:
    """How many cells of the remainder lattice are filled."""
    if found := SHADED_OF.search(text):
        return int(found.group(1))
    if found := SHADED_WERE.search(text):
        digits = found.group(1) or found.group(2)
        return int(digits)
    if SHADED_ONE.search(text):
        return 1
    return None


def unshaded_count(text: str) -> int | None:
    """How many cells are left white, when the wording names the complement."""
    if found := UNSHADED.search(text):
        digits = found.group(1) or found.group(2)
        return int(digits)
    return None


def parse(description: str) -> Grid:
    """The integers a grid description names, or why it is not this type."""
    text = western(description)
    if not text.strip():
        raise GridError("the description is empty")

    n_grids = grid_count(text)
    extra_full = max(n_grids - 1, 0)
    total = cell_total(text)
    size = explicit_size(text)
    if total is None and size is not None:
        total = size[0] * size[1]
    if total is None:
        raise GridError("the description does not say how many cells the grid has")
    if size is not None and size[0] * size[1] != total:
        raise GridError(
            f"the size {size[0]}×{size[1]} is not the {total} cells named"
        )

    shaded = shaded_count(text)
    if shaded is None:
        if REGION.search(text):
            raise GridError(
                "the description names which cells are shaded, not how many"
            )
        raise GridError("the description does not say how many cells are shaded")

    unshaded = unshaded_count(text)
    if unshaded is not None and shaded + unshaded != total:
        raise GridError(
            f"shaded {shaded} and unshaded {unshaded} do not add to {total}"
        )
    if shaded < 0 or shaded > total:
        raise GridError(f"{shaded} shaded cells cannot sit on a lattice of {total}")

    cols, rows = lattice(total)
    return Grid(cols, rows, shaded, extra_full)


def spec_from_fields(raw: dict) -> Grid:
    """A spec written out as integers, for figures that no longer need parsing."""
    try:
        cols = int(raw["cols"])
        rows = int(raw["rows"])
        shaded = int(raw["shaded"])
    except (KeyError, TypeError, ValueError) as error:
        raise GridError(f"the grid field is missing {error}") from error
    extra_full = int(raw["extra_full"]) if raw.get("extra_full") not in (None, "") else 0
    if cols < 1 or rows < 1:
        raise GridError(f"a grid cannot be {cols}×{rows}")
    if extra_full < 0:
        raise GridError(f"extra_full cannot be {extra_full}")
    if shaded < 0 or shaded > cols * rows:
        raise GridError(f"{shaded} shaded cells cannot sit on a lattice of {cols * rows}")
    return Grid(cols, rows, shaded, extra_full)


def spec_of(figure: dict) -> Grid:
    """A drawable spec, from a structured field if the figure has one, else the prose."""
    raw = figure.get("grid")
    if isinstance(raw, dict):
        return spec_from_fields(raw)
    description = str(figure.get("description") or "").strip()
    if not description:
        raise GridError("the figure has no grid field and no description")
    return parse(description)


def as_dict(spec: Grid) -> dict:
    """A `grid` field with cols, rows, shaded, and extra_full only when it is not zero."""
    out: dict = {"cols": spec.cols, "rows": spec.rows, "shaded": spec.shaded}
    if spec.extra_full:
        out["extra_full"] = spec.extra_full
    return out


def _macros(spec: Grid, a: int, b: int) -> list[str]:
    """The \\pgfmathsetmacro lines the picture names its sizes with."""
    lines = []
    if spec.cols == spec.rows:
        lines.append(rf"  \pgfmathsetmacro{{\g}}{{{spec.cols}}}")
    else:
        lines.append(rf"  \pgfmathsetmacro{{\gw}}{{{spec.cols}}}")
        lines.append(rf"  \pgfmathsetmacro{{\gh}}{{{spec.rows}}}")
    if a:
        lines.append(rf"  \pgfmathsetmacro{{\a}}{{{a}}}")
    if b:
        lines.append(rf"  \pgfmathsetmacro{{\b}}{{{b}}}")
    if spec.extra_full:
        lines.append(rf"  \pgfmathsetmacro{{\s}}{{{spec.extra_full}}}")
    return lines


def _fills(a: int, b: int, height: str) -> list[str]:
    """Shaded paths for the remainder lattice. Empty fills are left out."""
    lines = []
    if a:
        lines.append(
            rf"  \fill [shaded] (0,0)--(\a,0)--(\a,{height})--(0,{height})--cycle;"
        )
    if b and a:
        lines.append(
            r"  \fill [shaded] (\a,0)--(\a+1,0)--(\a+1,\b)--(\a,\b)--cycle;"
        )
    elif b:
        lines.append(
            r"  \fill [shaded] (0,0)--(1,0)--(1,\b)--(0,\b)--cycle;"
        )
    return lines


def _full_copy(width: str, height: str, xshift: str) -> list[str]:
    """One extra lattice, filled entirely, shifted by the language's gap formula."""
    return [
        rf"    \begin{{scope}}[{xshift}]",
        rf"      \fill [shaded] (0,0)--({width},0)--({width},{height})--(0,{height})--cycle;",
        rf"      \draw [gridline] (0,0) grid ({width},{height});",
        r"    \end{scope}",
    ]


def _remainder(a: int, b: int, width: str, height: str, indent: str) -> list[str]:
    """Fills and lattice of the remainder grid, indented for the scope they sit in."""
    lines = [indent + line.strip() for line in _fills(a, b, height)]
    lines.append(rf"{indent}\draw [gridline] (0,0) grid ({width},{height});")
    return lines


def picture(spec: Grid, rtl: bool) -> str:
    """One tikzpicture for this lattice, in the language's fill order."""
    a, b = divmod(spec.shaded, spec.rows)
    square = spec.cols == spec.rows
    width = r"\g" if square else r"\gw"
    height = r"\g" if square else r"\gh"
    y = rf"((\x-1)*{width})+(2*\x)"

    lines = [r"\begin{tikzpicture}"]
    lines.extend(_macros(spec, a, b))

    if rtl:
        lines.append(r"  \begin{scope}[scale=-1]")
        lines.extend(_remainder(a, b, width, height, "    "))
        if spec.extra_full:
            lines.append(r"    \foreach \x in {1,...,\s}{")
            lines.append(rf"      \pgfmathsetmacro{{\y}}{{{y}}}")
            lines.extend(
                "  " + line
                for line in _full_copy(width, height, rf"xshift=-{width} cm -\y cm")
            )
            lines.append(r"    }")
        lines.append(r"  \end{scope}")
    elif spec.extra_full:
        remainder_shift = rf"(\s*{width})+(2*\s)+2"
        lines.append(r"  \foreach \x in {1,...,\s}{")
        lines.append(rf"    \pgfmathsetmacro{{\y}}{{{y}}}")
        lines.extend(_full_copy(width, height, r"xshift=\y cm"))
        lines.append(r"  }")
        lines.append(rf"  \pgfmathsetmacro{{\y}}{{{remainder_shift}}}")
        lines.append(r"  \begin{scope}[xshift=\y cm]")
        lines.extend(_remainder(a, b, width, height, "    "))
        lines.append(r"  \end{scope}")
    else:
        lines.extend(_remainder(a, b, width, height, "  "))

    lines.append(r"\end{tikzpicture}")
    return "\n".join(lines) + "\n"


def drawn(figure: dict, rtl: bool) -> Drawn:
    """TikZ for one figure, or the reason it cannot be drawn this way."""
    try:
        spec = spec_of(figure)
    except GridError as error:
        return Drawn(reason=str(error))
    return Drawn(tikz=picture(spec, rtl=rtl))


def check_book(book_id: str) -> int:
    """Parse every grid this book describes and report any that disagree."""
    folder = OUTPUT_DIR / book_id / FIGURE_DATA_STAGE / "grid"
    if not folder.is_dir():
        print(f"FAILED: no grids under {folder}", file=sys.stderr)
        return 1

    paths = sorted(folder.glob("*.json"))
    if not paths:
        print(f"FAILED: no grid files in {folder}", file=sys.stderr)
        return 1

    failed = 0
    for path in paths:
        try:
            figure = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(f"FAIL {path.stem}: {error}")
            failed += 1
            continue
        try:
            got = spec_of(figure)
        except GridError as error:
            print(f"FAIL {path.stem}: {error}")
            failed += 1
            continue
        want = EXPECTED.get(path.stem)
        if want is not None and got != want:
            print(f"FAIL {path.stem}: {got} != {want}")
            failed += 1
            continue
        extra = f", {got.extra_full} extra full" if got.extra_full else ""
        print(
            f"OK {path.stem}: {got.cols}×{got.rows}, {got.shaded} shaded{extra}"
        )
    if failed:
        print(f"FAILED: {failed} of {len(paths)} grid(s)", file=sys.stderr)
        return 1
    print(f"OK: {len(paths)} grid(s)")
    return 0


def main() -> int:
    book_id = sys.argv[1] if len(sys.argv) > 1 else "sa_mathematics_ar_g6_t2"
    return check_book(book_id)


if __name__ == "__main__":
    sys.exit(main())
