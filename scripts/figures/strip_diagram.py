#!/usr/bin/env python3
"""Draw a strip diagram from its structured `strip` field, with no model.

The figure JSON carries a `strip` written with the exercise: `layout`
(`equal_unit` or `part_whole`), the rows or whole/parts, and any bracket. This
module draws from that field only — not from the prose description. Older files
without a `strip` field still fall back to parsing the description.

Widths that the wording does not fix — mixed units, an unknown beside a number
— are taken as a readable split, never as a measured scale. That is a convention
of this renderer, not something read off the page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

# Bar height in millimetres: enough for a 12pt label to sit clear of the edges,
# and no higher. Kept in millimetres so a wider column stretches the bars, not
# the type.
BAR_H = 8.5
ROW_GAP = 3.0

# Bracket: a short upright at each end and a rule between them, beyond the bar.
BRACKET_STEM = 2.0
BRACKET_OUT = 3.5

# The small empty rectangle a student writes a unit in, and the gap to the unit
# printed beside it. Sizes in millimetres, matching the prompt's example box.
BOX_W = 6.5
BOX_H = 4.9
BOX_GAP = 1.2

# Room to the left of the bars for a name set with `left=6pt`.
NAME_PAD = 2.2
NAME_CHAR = 1.85

# A writing line in a blank cell, the same length every time.
BLANK_RULE = r"\underline{\hspace{1.5cm}}"

# Percent first, and the escaped form before the bare sign, so `40\%` is one unit
# and not a backslash plus a comment. The Arabic percent glyph is the same unit.
# Arabic units are longest-first. Bare م is a metre after a number; after a
# letter it is another variable (م ح is a product, ل سم is a letter then a unit).
PERCENT = r"\\%|%|٪"
ARABIC_UNIT = (
    r"لترات|لترًا|لترا|لتر|كيلوجرام|كجم|سنتيمتر|سم|مليمتر|مم|"
    r"كيلومتر|كم|متر|جرام|جم|ملليلتر|مل"
)
UNIT = rf"{PERCENT}|L\.E\.|mL|km|cm|kg|LE|tons?|{ARABIC_UNIT}|m|L|g|م"
CELL_UNIT = rf"(?:{UNIT})"
NUMBER = r"[\d,]+(?:\.\d+)?"
LETTER = r"[A-Za-z\u0621-\u064A]"


class CannotDraw(ValueError):
    """The description does not determine a strip diagram."""


@dataclass
class Cell:
    """What is written in one rectangle, or the blank the student fills."""

    kind: Literal["text", "blank", "blank_box"]
    text: str = ""
    unit: str = ""


@dataclass
class UnitRow:
    """One row of identical unit blocks, optionally named and bracketed."""

    name: str = ""
    count: int = 1
    cell: Cell = field(default_factory=lambda: Cell(kind="text", text=""))
    bracket: str = ""
    bracket_side: Literal["above", "below"] | None = None


@dataclass
class Strip:
    """The cells a strip diagram is drawn from."""

    layout: Literal["equal_unit", "part_whole"]
    rows: list[UnitRow] = field(default_factory=list)
    whole: Cell | None = None
    parts: list[Cell] = field(default_factory=list)


def tikz_of(figure: dict, wide: bool = False) -> str:
    """The TikZ that draws one figure, at the narrow column or the wide measure."""
    spec = spec_of(figure)
    key = "max_wide_width" if wide else "max_narrow_width"
    width = millimetres(str(figure.get(key) or ("162mm" if wide else "84mm")))
    return render(spec, width)


def spec_of(figure: dict) -> Strip:
    """A drawable spec, from a structured field if the figure has one, else the prose."""
    raw = figure.get("strip")
    if isinstance(raw, dict):
        return spec_from_fields(raw)
    return parse_description(str(figure.get("description") or ""))


def millimetres(width: str) -> float:
    """A column written `84mm`, as millimetres."""
    found = re.match(r"\s*([\d.]+)\s*mm", str(width))
    return float(found.group(1)) if found else 84.0


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_description(text: str) -> Strip:
    """Read a strip diagram out of the prose a figure is described in."""
    if not text.strip():
        raise CannotDraw("no description to draw")
    layout = layout_of(text)
    if layout == "equal_unit":
        return parse_equal_unit(text)
    return parse_part_whole(text)


def layout_of(text: str) -> Literal["equal_unit", "part_whole"]:
    """Which of the two layouts the wording is describing."""
    if re.search(r"tape diagram", text, re.I):
        return "equal_unit"
    if re.search(
        r"part[- ]part[- ]whole|part[- ]whole|bar model|two-tier",
        text,
        re.I,
    ):
        return "part_whole"
    if re.search(
        r"identical blocks|equal rectangular blocks|equal rectangles",
        text,
        re.I,
    ):
        return "equal_unit"
    raise CannotDraw("the description is not an equal-unit tape or a part–whole bar")


def parse_equal_unit(text: str) -> Strip:
    """A row of identical blocks, or two rows comparing a unit to a multiple of it."""
    product = re.search(
        r"row of (?P<n>\d+) equal rectangular blocks, each containing the "
        r"(?:number|variable) (?P<label>\S+)\.\s*"
        r"A bracket over the entire diagram is labeled with the "
        r"(?:variable|total number) (?P<br>\S+)",
        text,
        re.I,
    )
    if product:
        label = product.group("label").strip(".,'\"")
        bracket = product.group("br").strip(".,'\"")
        return Strip(
            layout="equal_unit",
            rows=[
                UnitRow(
                    count=int(product.group("n")),
                    cell=cell_from(label),
                    bracket=bracket,
                    bracket_side="above",
                )
            ],
        )

    count = long_count(text)
    label = unit_label(text)
    if count is None or label is None:
        raise CannotDraw("an equal-unit diagram needs a unit label and a count")
    names = row_names(text)
    bracket, side = diagram_bracket(text)
    cell = cell_from(label)
    top = UnitRow(name=names[0] if names else "", count=1, cell=cell)
    bottom = UnitRow(
        name=names[1] if len(names) > 1 else "",
        count=count,
        cell=cell,
        bracket=bracket,
        bracket_side=side if bracket else None,
    )
    return Strip(layout="equal_unit", rows=[top, bottom])


def long_count(text: str) -> int | None:
    """How many unit blocks the longer row has."""
    patterns = (
        r"(?P<n>\d+) identical(?:\s+adjacent)?(?:\s+end-to-end)?\s+"
        r"(?:blocks|sections|rectangles)",
        r"(?P<n>\d+) equal rectangles",
        r"bottom bar (?P<n>\d+) blocks",
        r"represented by (?P<n>\d+) identical",
        r"shown as (?P<n>\d+) identical",
    )
    for pattern in patterns:
        found = re.search(pattern, text, re.I)
        if found:
            return int(found.group("n"))
    return None


def unit_label(text: str) -> str | None:
    """The amount written in every unit block."""
    patterns = (
        rf"blocks of\s+['\"]?({NUMBER}(?:\s+{CELL_UNIT})?)['\"]?",
        rf"each labelled\s+['\"]?({NUMBER}(?:\s+{CELL_UNIT})?)['\"]?",
        rf"each labeled\s+['\"]?({NUMBER}(?:\s+{CELL_UNIT})?)['\"]?",
        rf"each containing the number\s+['\"]?({NUMBER})['\"]?",
        r"each containing the variable\s+['\"]?([A-Za-z])['\"]?",
        rf"each containing\s+['\"]?({NUMBER}(?:\s+{CELL_UNIT})?)['\"]?",
        rf"1 (?:block|section) labelled\s+['\"]?({NUMBER}(?:\s+{CELL_UNIT})?)['\"]?",
        rf"1 (?:block|section) labeled\s+['\"]?({NUMBER}(?:\s+{CELL_UNIT})?)['\"]?",
        rf"1 (?:block|section) containing(?: the number)?\s+['\"]?({NUMBER})['\"]?",
        rf"1 (?:block|section) of\s+['\"]?({NUMBER})['\"]?",
        rf"containing the number\s+['\"]?({NUMBER})['\"]?",
        r"containing the variable\s+['\"]?([A-Za-z])['\"]?",
    )
    for pattern in patterns:
        found = re.search(pattern, text, re.I)
        if found:
            return found.group(1).strip(".,'\"")
    return None


def row_names(text: str) -> list[str]:
    """Names printed to the left of the rows, in top-then-bottom order."""
    if re.search(r"Quantity A", text):
        return ["Quantity A", "Quantity B"]
    pair = re.search(
        r"(?:bars?|tapes?|rows?) labelled '([^']+)' and '([^']+)'",
        text,
        re.I,
    )
    if pair:
        return [pair.group(1), pair.group(2)]
    labelled = re.findall(
        r"(?:tape|row|bar)s? labell?ed '([^']+)'",
        text,
        re.I,
    )
    if len(labelled) >= 2:
        return labelled[:2]
    owned = re.findall(r"\b([A-Z][a-z]+)'s tape\b", text)
    if len(owned) >= 2:
        return owned[:2]
    actors = re.findall(r"\b([A-Z][a-z]+) (?:walked|scored)\b", text)
    if len(actors) >= 2:
        return actors[:2]
    nouns = re.findall(r"\bA ([a-z]+) is\b", text)
    if len(nouns) >= 2:
        return [noun.capitalize() for noun in nouns]
    return []


def diagram_bracket(text: str) -> tuple[str, Literal["above", "below"] | None]:
    """A total marked off the bars, and which side of the row it sits on."""
    patterns: tuple[tuple[str, Literal["above", "below"]], ...] = (
        (
            r"bracket over the entire diagram is labeled with the "
            r"(?:variable |total number )?['\"]?([^'\".]+)['\"]?",
            "above",
        ),
        (r"total bracket showing ['\"]?([^'\".]+)['\"]?", "below"),
        (r"bracket below showing ['\"]?([^'\".]+)['\"]?", "below"),
        (r"brace underneath labeled ['\"]?([^'\".]+)['\"]?", "below"),
        (r"line below indicating a total of ['\"]?([^'\".]+)['\"]?", "below"),
        (r"brackets below \w+'s tape shows a total of ['\"]?([^'\".]+)['\"]?", "below"),
        (r"showing a total of ['\"]?([^'\".]+)['\"]?", "below"),
        (r"totalling ['\"]?([^'\".]+)['\"]?", "below"),
        (r"with a total of ['\"]?([^'\".]+)['\"]?", "below"),
        (r", total ['\"]?([^'\".]+)['\"]?", "below"),
    )
    for pattern, side in patterns:
        found = re.search(pattern, text, re.I)
        if found:
            return found.group(1).strip(".,'\""), side
    return "", None


# A cell as the descriptions write it: quoted, a number with a unit, a letter,
# a blank, or a question mark. Quotes are not optional — mixing an optional quote
# with a non-greedy run stops `'3,200'` at the comma.
LABEL = (
    r"(?:a blank box labeled \w+|a blank|a question mark '\?'|'[^']+'|"
    rf"{NUMBER}(?:\s+{CELL_UNIT})?|[A-Za-z]|\?)"
)


def parse_part_whole(text: str) -> Strip:
    """A whole on top and its parts underneath, sharing an edge."""
    three = re.search(
        rf"(?:total amount |top bar representing the total amount )({LABEL})"
        rf", and a bottom bar divided into three sections: "
        rf"two known parts labeled ({LABEL}) and ({LABEL}), "
        rf"and one unknown part labeled ({LABEL})",
        text,
        re.I,
    )
    if three:
        return Strip(
            layout="part_whole",
            whole=cell_from(three.group(1)),
            parts=[cell_from(g) for g in three.group(2, 3, 4)],
        )

    for pattern in (
        rf"top (?:box|rectangle) labeled ({LABEL})(?: representing the whole)?, "
        rf"(?:and two lower sub-boxes labeled |split below into two sub-rectangles labeled )"
        rf"({LABEL}) and ({LABEL})",
        rf"top row is a single box labeled ({LABEL})\.\s*"
        rf"The bottom row (?:is split into two boxes|consists of two adjacent boxes): "
        rf"the left box is labeled ({LABEL}) and the right box is labeled ({LABEL})",
        rf"top bar showing(?: total length)? ({LABEL}), divided below into two "
        rf"sub-bars: the left sub-bar shows ({LABEL}) and the right sub-bar shows ({LABEL})",
        rf"top rectangular box (?:containing |labeled )({LABEL})\.\s*"
        rf"Below the top box are two adjacent rectangular boxes: "
        rf"the left box (?:is labeled |contains )({LABEL}) and the right box "
        rf"(?:is labeled |contains )({LABEL})",
        rf"top row shows (?:a total mass of )?({LABEL})\.\s*"
        rf"The bottom row is split into two boxes: "
        rf"the left box contains ({LABEL}) and the right box contains ({LABEL})",
    ):
        found = re.search(pattern, text, re.I)
        if found:
            return Strip(
                layout="part_whole",
                whole=cell_from(found.group(1)),
                parts=[cell_from(found.group(2)), cell_from(found.group(3))],
            )

    raise CannotDraw("a part–whole diagram needs a whole and its parts")


def cell_from(raw: str) -> Cell:
    """One cell's contents, from a quoted label or a short phrase."""
    text = re.sub(r"\s+", " ", raw.strip().rstrip(","))
    box = re.search(r"blank box labeled\s+(\w+)", text, re.I)
    if box:
        return Cell(kind="blank_box", unit=box.group(1))
    if re.search(r"question mark|\?", text) and not re.search(NUMBER, text):
        return Cell(kind="text", text="?")
    inner = text.strip("'\"")
    if re.fullmatch(r"(?:a )?blank", inner, re.I):
        return Cell(kind="blank")
    return Cell(kind="text", text=inner)


def spec_from_fields(raw: dict) -> Strip:
    """A spec written out as cells, for figures that no longer need parsing."""
    layout = raw.get("layout")
    if layout not in ("equal_unit", "part_whole"):
        raise CannotDraw(f"unknown strip layout {layout!r}")
    if layout == "equal_unit":
        rows = []
        for row in raw.get("rows") or []:
            side = row.get("bracket_side")
            rows.append(
                UnitRow(
                    name=str(row.get("name") or ""),
                    count=int(row.get("count") or 1),
                    cell=_cell_field(row.get("cell") or row.get("label") or ""),
                    bracket=str(row.get("bracket") or ""),
                    bracket_side=side if side in ("above", "below") else None,
                )
            )
        if not rows:
            raise CannotDraw("an equal-unit diagram needs a row")
        return Strip(layout="equal_unit", rows=rows)
    whole = raw.get("whole")
    parts = raw.get("parts") or []
    if whole is None or len(parts) < 2:
        raise CannotDraw("a part–whole diagram needs a whole and at least two parts")
    return Strip(
        layout="part_whole",
        whole=_cell_field(whole),
        parts=[_cell_field(part) for part in parts],
    )


def _cell_field(raw: object) -> Cell:
    if isinstance(raw, dict):
        kind = raw.get("kind") or "text"
        if kind not in ("text", "blank", "blank_box"):
            raise CannotDraw(f"unknown cell kind {kind!r}")
        return Cell(kind=kind, text=str(raw.get("text") or ""), unit=str(raw.get("unit") or ""))
    return cell_from(str(raw))


def minimal_cell_dict(cell: Cell) -> dict:
    """The smallest object for one cell in a `strip` field."""
    if cell.kind == "blank":
        return {"kind": "blank"}
    if cell.kind == "blank_box":
        return {"kind": "blank_box", "unit": cell.unit}
    return {"kind": "text", "text": cell.text}


def minimal_row_dict(row: UnitRow) -> dict:
    """One row of an equal-unit strip, with only the fields the renderer needs."""
    out: dict = {"count": row.count, "cell": minimal_cell_dict(row.cell)}
    if row.name:
        out["name"] = row.name
    if row.bracket:
        out["bracket"] = row.bracket
        if row.bracket_side:
            out["bracket_side"] = row.bracket_side
    return out


def minimal_strip(spec: Strip) -> dict:
    """A `strip` field with layout, rows or whole/parts, and nothing redundant."""
    if spec.layout == "part_whole":
        if spec.whole is None:
            raise CannotDraw("a part–whole diagram needs a whole")
        return {
            "layout": "part_whole",
            "whole": minimal_cell_dict(spec.whole),
            "parts": [minimal_cell_dict(part) for part in spec.parts],
        }
    if not spec.rows:
        raise CannotDraw("an equal-unit diagram needs a row")
    return {
        "layout": "equal_unit",
        "rows": [minimal_row_dict(row) for row in spec.rows],
    }


def strip_for_extract(figure: dict) -> dict | None:
    """Build a `strip` field from an existing one or from the description.

    Kept for one-off repair of older files. New exercises write `strip` in
    new_questions.py; extract.py only files that field through.
    """
    raw = figure.get("strip")
    try:
        if isinstance(raw, dict):
            spec = spec_from_fields(raw)
        else:
            spec = parse_description(str(figure.get("description") or ""))
    except CannotDraw:
        return None
    return minimal_strip(spec)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render(spec: Strip, width_mm: float) -> str:
    """One tikzpicture drawing `spec` into a column `width_mm` millimetres across."""
    if spec.layout == "equal_unit":
        return render_equal_unit(spec, width_mm)
    return render_part_whole(spec, width_mm)


def render_equal_unit(spec: Strip, width_mm: float) -> str:
    rows = spec.rows
    if not rows:
        raise CannotDraw("an equal-unit diagram has no rows")
    names = [row.name for row in rows]
    left = name_room(names)
    n = max(row.count for row in rows)
    bar_w = max(width_mm - left - 1.0, 1.0)
    unit = bar_w / n
    # Stack from the bottom row up, so a one-row figure sits on y = 0.
    lines = [r"\begin{tikzpicture}[x=1mm, y=1mm]"]
    y = 0.0
    drawn: list[tuple[UnitRow, float]] = []
    for row in reversed(rows):
        drawn.append((row, y))
        y += BAR_H + ROW_GAP
    for row, y0 in reversed(drawn):
        y1 = y0 + BAR_H
        mid = (y0 + y1) / 2
        if row.name:
            lines.append(
                rf"  \node [left=6pt] at (0,{fmt(mid)}) {{{row.name}}};"
            )
        for i in range(row.count):
            x0 = i * unit
            x1 = (i + 1) * unit
            lines.append(
                rf"  \draw ({fmt(x0)},{fmt(y0)}) rectangle ({fmt(x1)},{fmt(y1)});"
            )
            lines.extend(cell_draws(row.cell, x0, x1, y0, y1))
        if row.bracket:
            x1 = row.count * unit
            lines.extend(bracket_draws(row.bracket, 0.0, x1, y0, y1, row.bracket_side or "below"))
    lines.append(r"\end{tikzpicture}")
    return "\n".join(lines) + "\n"


def render_part_whole(spec: Strip, width_mm: float) -> str:
    if spec.whole is None or len(spec.parts) < 2:
        raise CannotDraw("a part–whole diagram needs a whole and its parts")
    width = max(width_mm - 1.0, 28.0 * len(spec.parts) / 2)
    fracs = part_shares(spec.parts)
    y_mid = BAR_H
    y_top = 2 * BAR_H
    lines = [r"\begin{tikzpicture}[x=1mm, y=1mm]"]
    lines.append(rf"  \draw (0,0) rectangle ({fmt(width)},{fmt(y_top)});")
    lines.append(rf"  \draw (0,{fmt(y_mid)}) -- ({fmt(width)},{fmt(y_mid)});")
    x = 0.0
    splits: list[float] = []
    for frac, part in zip(fracs[:-1], spec.parts[:-1]):
        x += frac * width
        splits.append(x)
        lines.append(rf"  \draw ({fmt(x)},0) -- ({fmt(x)},{fmt(y_mid)});")
    edges = [0.0, *splits, width]
    lines.extend(cell_draws(spec.whole, 0.0, width, y_mid, y_top))
    for part, x0, x1 in zip(spec.parts, edges, edges[1:]):
        lines.extend(cell_draws(part, x0, x1, 0.0, y_mid))
    lines.append(r"\end{tikzpicture}")
    return "\n".join(lines) + "\n"


def part_shares(parts: list[Cell]) -> list[float]:
    """How the bottom row is split. Proportional only when every part is known and of one kind."""
    values = [number_of(part) for part in parts]
    units = [unit_of(part) for part in parts]
    if (
        all(value is not None for value in values)
        and len(set(units)) == 1
        and sum(values) > 0
    ):
        total = sum(values)
        raw = [value / total for value in values]
        floor = 0.22 if len(parts) == 2 else 0.18
        return clamp_floor(raw, floor)
    n = len(parts)
    return [1.0 / n] * n


def clamp_floor(shares: list[float], floor: float) -> list[float]:
    """Keep every cell readable, then put the leftover on the largest share."""
    out = [max(share, floor) for share in shares]
    extra = sum(out) - 1.0
    if extra <= 0:
        return out
    richest = max(range(len(out)), key=out.__getitem__)
    out[richest] = max(floor, out[richest] - extra)
    total = sum(out)
    return [share / total for share in out]


def number_of(cell: Cell) -> float | None:
    if cell.kind != "text":
        return None
    found = re.match(rf"({NUMBER})\b", cell.text)
    if not found:
        return None
    return float(found.group(1).replace(",", ""))


def unit_of(cell: Cell) -> str:
    if cell.kind != "text":
        return ""
    found = re.match(rf"{NUMBER}\s*({CELL_UNIT})?$", cell.text)
    if not found:
        return ""
    unit = found.group(1) or ""
    if unit and _percent_unit(unit):
        return "%"
    return unit


def name_room(names: list[str]) -> float:
    """Millimetres the left-side names take, so the bars still fit the column."""
    longest = max((name for name in names if name), key=len, default="")
    if not longest:
        return 0.0
    return NAME_PAD + NAME_CHAR * len(longest)


def cell_draws(cell: Cell, x0: float, x1: float, y0: float, y1: float) -> list[str]:
    """The nodes (and inner box) that sit in one cell."""
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    if cell.kind == "blank":
        return [rf"  \node at ({fmt(cx)},{fmt(cy)}) {{{BLANK_RULE}}};"]
    if cell.kind == "blank_box":
        unit_w = NAME_CHAR * len(cell.unit)
        total = BOX_W + BOX_GAP + unit_w
        left = cx - total / 2
        bottom = cy - BOX_H / 2
        right = left + BOX_W
        top = bottom + BOX_H
        unit_x = right + BOX_GAP
        return [
            rf"  \draw ({fmt(left)},{fmt(bottom)}) rectangle ({fmt(right)},{fmt(top)});",
            rf"  \node [anchor=west] at ({fmt(unit_x)},{fmt(cy)}) {{{cell.unit}}};",
        ]
    return [rf"  \node at ({fmt(cx)},{fmt(cy)}) {{{math_of(cell.text)}}};"]


def bracket_draws(
    label: str,
    x0: float,
    x1: float,
    y0: float,
    y1: float,
    side: Literal["above", "below"],
) -> list[str]:
    """Three strokes marking a total off the bar, with the label beyond the rule."""
    mid = (x0 + x1) / 2
    if side == "above":
        rule = y1 + BRACKET_OUT
        return [
            rf"  \draw ({fmt(x0)},{fmt(y1 + BRACKET_STEM)}) -- ({fmt(x0)},{fmt(rule)})"
            rf" -- ({fmt(x1)},{fmt(rule)}) -- ({fmt(x1)},{fmt(y1 + BRACKET_STEM)});",
            rf"  \node [above=1pt] at ({fmt(mid)},{fmt(rule)}) {{{math_of(label)}}};",
        ]
    rule = y0 - BRACKET_OUT
    return [
        rf"  \draw ({fmt(x0)},{fmt(y0 - BRACKET_STEM)}) -- ({fmt(x0)},{fmt(rule)})"
        rf" -- ({fmt(x1)},{fmt(rule)}) -- ({fmt(x1)},{fmt(y0 - BRACKET_STEM)});",
        rf"  \node [below=1pt] at ({fmt(mid)},{fmt(rule)}) {{{math_of(label)}}};",
    ]


def math_of(text: str) -> str:
    """A cell or bracket label as maths in `$...$`.

    A space splits two letters as a product (``م ح``). A number or ``?`` then
    leftover is a quantity: the leftover goes in ``\\text`` (``19 لترًا``,
    ``? كجم``). A letter then a known multi-letter unit is the same
    (``ل سم``). Arabic is not wrapped wholesale.
    """
    raw = text.strip().strip("'\"")
    if raw in {"?", "؟"}:
        return "$?$"
    if re.fullmatch(LETTER, raw):
        return f"${raw}$"
    found = re.fullmatch(rf"({NUMBER}|\?|؟|{LETTER})(\s*)(.*)", raw)
    if found is None:
        return f"${_escape_percent(raw)}$"
    lead, space, rest = found.group(1), found.group(2), found.group(3)
    if not rest:
        if re.fullmatch(NUMBER, lead):
            return f"${lead.replace(',', '{,}')}$"
        return f"${lead}$"
    math_lead = "?" if lead in {"?", "؟"} else lead.replace(",", "{,}")
    unit = rest.strip()
    if _percent_unit(unit):
        return f"${math_lead}\\%$"
    if re.fullmatch(NUMBER, lead) or lead in {"?", "؟"}:
        gap = space or " "
        return f"${math_lead}\\text{{{gap}{rest}}}$"
    if re.fullmatch(CELL_UNIT, unit) and len(unit) > 1:
        gap = space or " "
        return f"${math_lead}\\text{{{gap}{unit}}}$"
    if re.fullmatch(rf"{LETTER}(?:\s+{LETTER})*", rest):
        return f"${math_lead}{space}{rest}$"
    return f"${_escape_percent(raw)}$"


def _percent_unit(unit: str) -> bool:
    """Whether this captured unit is a percent sign in any written form."""
    return bool(re.fullmatch(PERCENT, unit))


def _escape_percent(text: str) -> str:
    """A bare % in a label is a TeX comment; a \\% already there is left alone."""
    return re.sub(r"(?<!\\)%", r"\\%", text)


def fmt(value: float) -> str:
    """A coordinate for TikZ, short and stable."""
    if abs(value) < 1e-10:
        return "0"
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text
