#!/usr/bin/env python3
"""Draw a function graph from its structured `graph` field, with no model.

The figure JSON carries a `graph` written with the exercise: `x_ticks`, `y_ticks`,
`domain`, `range`, and a `curves` list. Each curve has a `kind`, a `label`, and
the numbers that fix it — two points for a line; a vertex or one x-intercept for
a parabola; amplitude and frequency for sine or cosine when they are not 1. This
module plots from that field only — not from the prose description.

What the label still tells the plotter, when the curve fields do not: the
amplitude and frequency of a sine or cosine, which way a parabola opens, and
the leading coefficient of a quadratic written as \(Ax^2\) or \(At^2\). The
stretch of a parabola that neither the fields nor the label fix is taken as
leading coefficient ±1. A one-curve equation that already appears in the stem
or exercise wording is kept on the spec but not drawn.

Several curves on one pair of axes share the parent's ticks, domain and range.
Each curve may carry a `style` of `solid` or `dashed`. Curves are drawn in the
ordinary black of draw.md (no `curveblue`); dash is how two curves differ.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Literal

PI = math.pi

# How far past the last feature the axes run, as a fraction of the span they
# already cover, so the arrowheads have somewhere to go and a parabola's crossing
# is visible. A floor in data units, so a graph whose features sit on top of
# each other still gets a window.
PAD_FRAC = 0.18
PAD_MIN = 0.75

# Room around the plotting area for tick numbers and axis names, in millimetres.
# Taken off the column before the units are chosen, so the whole picture — names
# included — holds the measure rather than the axes alone.
ROOM_X = 18.0
ROOM_Y = 16.0

# A trigonometric graph is wider than it is tall; a parabola is not. Caps on the
# plotted height, as a fraction of the column, so a vertex at (1, −9) does not
# become a tower and a sine of amplitude 1 does not become a ripple.
TALL_FRAC = {"parabola": 0.85, "line": 0.75, "sine": 0.55, "cosine": 0.55}

# Sample a curve this many times so a sine does not come out faceted.
SAMPLES = 100

# Named points on the curve, in the same ordinary black as the stroke. Axis ticks
# are shorter: they only locate a number, they do not have to tell the point from
# its label.
DOT_PT = 1.6
TICK_PT = 2.0

# Tick numbers sit this far off the axis, in TeX points. Intercepts and waves
# need more than a plain tick: the stroke continues below the axis.
INTERCEPT_GAP_PT = 12
INTERCEPT_SHIFT_PT = 8
TRIG_GAP_PT = 12
TRIG_SHIFT_PT = 8
# A trough a unit below the axis is farther than any fixed below-gap, so the
# number sits above the axis (the empty half-plane) instead.
Y_ON_LINE_SHIFT_PT = 6
# Adjacent tick labels closer than this, in millimetres, crowd each other.
# Keep the marks; drop the denser odd π/4 numbers.
TICK_LABEL_MIN_MM = 11.0


class CannotDraw(ValueError):
    """The description does not determine a function graph."""


@dataclass
class FunctionGraph:
    """The numbers a function graph is plotted from.

    One record is one curve, and also the axes when it is the only curve. Several
    curves on the same axes live in `curves`; the parent then holds the axis names,
    ticks and grid, and each child is a curve with its own kind, label and style.
    """

    kind: Literal["parabola", "sine", "cosine", "line"]
    x_name: str = "x"
    y_name: str = "y"
    origin: str = "0"
    x_ticks: list[tuple[float, str]] = field(default_factory=list)
    y_ticks: list[tuple[float, str]] = field(default_factory=list)
    vertex: tuple[float, float] | None = None
    x_intercepts: list[float] = field(default_factory=list)
    y_intercept: float | None = None
    opens: Literal["up", "down"] | None = None
    a: float | None = None
    amplitude: float | None = None
    frequency: float = 1.0
    domain: tuple[float, float] | None = None
    y_range: tuple[float, float] | None = None
    points: list[tuple[float, float]] = field(default_factory=list)
    marked: list[tuple[float, float, str]] = field(default_factory=list)
    grid: bool = False
    curve: bool = True
    label: str = ""
    show_label: bool = True
    style: Literal["solid", "dashed"] = "solid"
    curves: list[FunctionGraph] = field(default_factory=list)


def all_curves(spec: FunctionGraph) -> list[FunctionGraph]:
    """The curves to plot: the children, or the spec itself when it is the curve."""
    return spec.curves or [spec]


def tikz_of(figure: dict, wide: bool = False) -> str:
    """The TikZ that plots one figure, at the narrow column or the wide measure."""
    spec = spec_of(figure)
    return render(spec, figure_width(figure, wide))


def png_of(figure: dict, path: str, wide: bool = False) -> None:
    """A matplotlib preview of the same numbers, written to `path`.

    Presentation is ordinary matplotlib (default axes, colour cycle, legend), not
    the book's TikZ layout. Not used on the paper today; kept simple enough that a
    later export path remains plausible.
    """
    spec = spec_of(figure)
    render_png(spec, figure_width(figure, wide), path)


def figure_width(figure: dict, wide: bool = False) -> float:
    """The column the figure is drawn to, in millimetres."""
    key = "max_wide_width" if wide else "max_narrow_width"
    return millimetres(str(figure.get(key) or ("162mm" if wide else "84mm")))


def spec_of(figure: dict) -> FunctionGraph:
    """A plottable spec from the figure's `graph` field alone."""
    raw = figure.get("graph")
    if not isinstance(raw, dict):
        raise CannotDraw("function graph needs a graph field")
    spec = spec_from_fields(raw)
    # The structured field is enough for most curves. When a parabola still has
    # no opening and the prose names one, take that — writers often put
    # "opening downwards" in the description and leave `opens` unset.
    apply_opens_from_description(spec, str(figure.get("description") or ""))
    hide_duplicate_label(spec, figure)
    return spec


MATH_SEGMENT = re.compile(r"\$\$.+?\$\$|\$.+?\$", re.DOTALL)


def equation_key(text: str) -> str:
    """Normalise an equation for comparing a curve label to the wording."""
    body = text.strip()
    if body.startswith("$") and body.endswith("$"):
        body = body[1:-1]
    body = body.replace(r"\,", "").replace(r"\;", "").replace(r"\!", "")
    return re.sub(r"\s+", "", body).lower()


def equation_in_wording(label: str, wording: str) -> bool:
    """True when the curve's equation already appears in the stem or exercise text."""
    key = equation_key(label)
    if not key:
        return False
    for match in MATH_SEGMENT.finditer(wording):
        if equation_key(match.group(0)) == key:
            return True
    return key in equation_key(wording)


def hide_duplicate_label(spec: FunctionGraph, figure: dict) -> None:
    """Do not draw a lone equation that the stem or exercise wording already names."""
    drawn = all_curves(spec)
    if len(drawn) != 1:
        return
    wording = f"{figure.get('stem') or ''} {figure.get('text') or ''}"
    if equation_in_wording(drawn[0].label, wording):
        drawn[0].show_label = False


def apply_opens_from_description(spec: FunctionGraph, text: str) -> None:
    """Set `opens` from prose when the graph field left it blank."""
    if not text.strip():
        return
    for one in all_curves(spec):
        if one.kind != "parabola" or one.opens is not None:
            continue
        if re.search(r"downward|opening downwards|opens?\s+down", text, re.I):
            one.opens = "down"
        elif re.search(r"upward|opening upwards|opens?\s+up", text, re.I):
            one.opens = "up"
        else:
            continue
        # Drop a stretch settled under the wrong opening (default up) and settle again.
        one.a = None
        if one.vertex is not None or one.x_intercepts:
            finish_parabola(one)


def millimetres(width: str) -> float:
    """A column written `84mm`, as millimetres."""
    found = re.match(r"\s*([\d.]+)\s*mm", str(width))
    return float(found.group(1)) if found else 84.0


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

COORD = re.compile(
    r"\(\s*"
    r"(?P<x>-?[\d.]+|\\frac\{[^}]+\}\{[^}]+\}|\d*\\pi|\\pi)"
    r"\s*,\s*"
    r"(?P<y>-?[\d.]+|\\frac\{[^}]+\}\{[^}]+\}|\d*\\pi|\\pi)"
    r"\s*\)"
)
X_EQUALS = re.compile(r"(?<![a-z])x\s*=\s*(-?[\d.]+)", re.I)
TRIG_EQ = re.compile(
    r"y\s*=\s*(?:(?P<amp>\d+(?:\.\d+)?)|(?P<unknown>a))?\s*"
    r"\\(?P<fn>sin|cos)\s*(?P<freq>\d+(?:\.\d+)?)?\\theta",
    re.I,
)
DOMAIN = re.compile(
    r"\[(?P<a>0|[-\d.]+|\\pi|\d*\\pi)\s*,\s*(?P<b>[-\d.]+|\\pi|\d*\\pi|2\\pi)\]"
)
TICKS_AFTER = re.compile(
    r"(?:ticks?(?:\s+marks?)?\s+at|labell?ed(?:\s+with)?)\s+"
    r"(?P<body>\$?[^.]{0,180})",
    re.I,
)
AXIS_LETTER = re.compile(
    r"(?P<which>horizontal|vertical)\s+axis\s+(?:is\s+)?labell?ed\s*\$([^$]+)\$",
    re.I,
)


def parse_description(text: str) -> FunctionGraph:
    """Read a function graph out of the prose a figure is described in."""
    if not text.strip():
        raise CannotDraw("no description to plot")
    if re.search(r"no curve is plotted|axes only|without any (plotted )?curve", text, re.I):
        spec = parse_description(re.sub(
            r"no curve is plotted|axes only|without any (plotted )?curve",
            "",
            text,
            flags=re.I,
        ))
        spec.curve = False
        return spec

    kind = kind_of(text)
    spec = FunctionGraph(kind=kind)
    spec.grid = bool(re.search(
        r"grid\s+lines|with a grid|on a grid|squared paper", text, re.I
    ))
    spec.origin = "O" if re.search(r"origin\s+O\b", text) else "0"
    apply_axis_names(spec, text)

    if kind == "line":
        return parse_line(spec, text)
    if kind in ("sine", "cosine"):
        return parse_trig(spec, text, kind)
    return parse_parabola(spec, text)


def kind_of(text: str) -> Literal["parabola", "sine", "cosine", "line"]:
    """Which sort of curve the wording is describing."""
    if re.search(r"\\cos\b|cosine", text, re.I):
        return "cosine"
    if re.search(r"\\sin\b|sine", text, re.I):
        return "sine"
    if re.search(r"straight line|linear function", text, re.I):
        return "line"
    if re.search(r"parabola|quadratic", text, re.I):
        return "parabola"
    raise CannotDraw("the description is not a parabola, a sine, a cosine or a line")


def parse_line(spec: FunctionGraph, text: str) -> FunctionGraph:
    """A line through the intercepts (or any two points) the wording names."""
    coords = coordinates(text)
    if len(coords) >= 2:
        spec.points = coords[:2]
    elif (xs := x_equals(text)) and spec.y_intercept is not None:
        spec.points = [(xs[0], 0.0), (0.0, spec.y_intercept)]
    else:
        xs = x_equals(text)
        if len(xs) >= 1 and coords:
            spec.points = [(xs[0], 0.0), coords[0] if abs(coords[0][1]) > 1e-9 else (0.0, coords[0][1])]
        elif len(coords) == 1 and xs:
            spec.points = [(xs[0], 0.0), coords[0]]
    if len(spec.points) < 2:
        # x-intercept then y-intercept, the usual pair.
        x_int = [c for c in coords if abs(c[1]) < 1e-9]
        y_int = [c for c in coords if abs(c[0]) < 1e-9]
        if x_int and y_int:
            spec.points = [x_int[0], y_int[0]]
    if len(spec.points) < 2:
        raise CannotDraw("a line needs two points")
    spec.x_intercepts = [x for x, y in spec.points if abs(y) < 1e-9]
    for x, y in spec.points:
        if abs(x) < 1e-9:
            spec.y_intercept = y
    spec.x_ticks = [(x, number_tex(x)) for x, y in spec.points if abs(y) < 1e-9]
    spec.y_ticks = [(y, number_tex(y)) for x, y in spec.points if abs(x) < 1e-9]
    for x, y in spec.points:
        mark(spec, x, y)
    return spec


def parse_trig(spec: FunctionGraph, text: str, kind: str) -> FunctionGraph:
    """A sine or cosine, amplitude and period taken from the equation or the peaks."""
    spec.kind = kind  # type: ignore[assignment]
    matched = TRIG_EQ.search(text)
    if matched:
        if matched.group("amp"):
            spec.amplitude = float(matched.group("amp"))
        spec.frequency = float(matched.group("freq") or 1)
        spec.kind = "sine" if matched.group("fn").lower() == "sin" else "cosine"
    domain = DOMAIN.search(text)
    if domain:
        spec.domain = (latex_number(domain.group("a")), latex_number(domain.group("b")))
    else:
        spec.domain = (0.0, 2 * PI)

    coords = coordinates(text)
    ys = [y for _, y in coords]
    if spec.amplitude is None and ys:
        spec.amplitude = max(abs(y) for y in ys)
    if spec.amplitude is None:
        spec.amplitude = 1.0

    spec.x_ticks = ticks_in(text, horizontal=True) or default_trig_xticks(spec)
    spec.y_ticks = ticks_in(text, horizontal=False) or [
        (-spec.amplitude, number_tex(-spec.amplitude)),
        (spec.amplitude, number_tex(spec.amplitude)),
    ]
    spec.x_name = spec.x_name if spec.x_name != "x" else r"\theta"
    return spec


def parse_parabola(spec: FunctionGraph, text: str) -> FunctionGraph:
    """A parabola from vertex, intercepts and which way it opens."""
    if re.search(r"downward|opening downwards", text, re.I):
        spec.opens = "down"
    elif re.search(r"upward|opening upwards", text, re.I):
        spec.opens = "up"

    coords = coordinates(text)
    vertex = vertex_in(text, coords)
    if vertex:
        spec.vertex = vertex
        mark(spec, vertex[0], vertex[1])

    spec.x_intercepts = intercepts_x(text, coords)
    for x, y in coords:
        if abs(x) < 1e-9 and abs(y) > 1e-9:
            spec.y_intercept = y

    if spec.opens is None:
        if spec.vertex and spec.x_intercepts:
            h, k = spec.vertex
            spec.opens = "up" if k <= 0 else "down"
            # Vertex below the axis and two intercepts: it opened up to get there.
            if k < 0:
                spec.opens = "up"
            elif k > 0:
                spec.opens = "down"
        else:
            spec.opens = "up"

    finish_parabola(spec)
    spec.x_ticks = [(x, number_tex(x)) for x in spec.x_intercepts]
    if spec.vertex:
        h, k = spec.vertex
        if all(abs(h - x) > 1e-6 for x, _ in spec.x_ticks):
            spec.x_ticks.append((h, number_tex(h)))
        spec.y_ticks = [(k, number_tex(k))] if abs(k) > 1e-9 else []
    if spec.y_intercept is not None and all(
        abs(spec.y_intercept - y) > 1e-6 for y, _ in spec.y_ticks
    ):
        spec.y_ticks.append((spec.y_intercept, number_tex(spec.y_intercept)))
    for x in spec.x_intercepts:
        mark(spec, x, 0.0)
    if spec.y_intercept is not None:
        mark(spec, 0.0, spec.y_intercept)
    return spec


def mark(spec: FunctionGraph, x: float, y: float, label: str = "") -> None:
    """A named point on the curve, once."""
    if any(abs(x - px) < 1e-8 and abs(y - py) < 1e-8 for px, py, _ in spec.marked):
        return
    spec.marked.append((x, y, label))


def infer_parabola_opens(spec: FunctionGraph) -> None:
    """Which way the parabola opens when the writer left `opens` unset.

    Prefer numbers already on the curve: a y-intercept with a vertex off the
    y-axis fixes the sign of `a`, and a vertex above (below) the x-axis with
    roots opens down (up). Default to up only when nothing else decides.
    """
    if spec.opens is not None:
        return
    if spec.a is not None:
        spec.opens = "down" if spec.a < 0 else "up"
        return
    if spec.vertex is not None and spec.y_intercept is not None:
        h, k = spec.vertex
        if abs(h) > 1e-9:
            a = (spec.y_intercept - k) / (h * h)
            if abs(a) > 1e-12:
                spec.opens = "down" if a < 0 else "up"
                return
    if spec.vertex is not None and spec.x_intercepts:
        _h, k = spec.vertex
        if k > 1e-9:
            spec.opens = "down"
            return
        if k < -1e-9:
            spec.opens = "up"
            return
    # Leave `opens` unset: finish_parabola treats that as up, and
    # apply_opens_from_description can still read "opening downwards" from prose.


def finish_parabola(spec: FunctionGraph) -> None:
    """Settle a, h, k from whatever of vertex and intercepts the wording gave.

    Leading coefficient ±1 when the intercepts do not already determine it, unless
    the equation label already fixed `a`.
    """
    sign = 1.0 if spec.opens != "down" else -1.0
    if spec.a is None:
        preset_a = None
    else:
        preset_a = spec.a
    if spec.vertex and spec.y_intercept is not None:
        h, k = spec.vertex
        if abs(h) > 1e-9:
            a = (spec.y_intercept - k) / (h * h)
            if abs(a) > 1e-12:
                spec.a = a
                return
    if spec.vertex and len(spec.x_intercepts) >= 2:
        h, k = spec.vertex
        r1, r2 = spec.x_intercepts[0], spec.x_intercepts[1]
        denom = (h - r1) * (h - r2)
        spec.a = (k / denom) if abs(denom) > 1e-9 else (preset_a if preset_a is not None else sign)
        return
    if spec.vertex and len(spec.x_intercepts) == 1:
        spec.a = preset_a if preset_a is not None else sign
        return
    if spec.vertex:
        spec.a = preset_a if preset_a is not None else sign
        return
    if len(spec.x_intercepts) >= 2:
        r1, r2 = spec.x_intercepts[0], spec.x_intercepts[1]
        h = (r1 + r2) / 2
        spec.a = preset_a if preset_a is not None else sign
        k = spec.a * (h - r1) * (h - r2)
        spec.vertex = (h, k)
        mark(spec, h, k)
        return
    if len(spec.x_intercepts) == 1:
        r = spec.x_intercepts[0]
        spec.vertex = (r, 0.0)
        spec.a = preset_a if preset_a is not None else sign
        mark(spec, r, 0.0)
        return
    raise CannotDraw("a parabola needs a vertex or intercepts")


def vertex_in(text: str, coords: list[tuple[float, float]]) -> tuple[float, float] | None:
    """The vertex if the wording names one, including a peak of a projectile."""
    for label in (r"vertex", r"peak", r"trough"):
        found = re.search(label, text, re.I)
        if not found:
            continue
        match = COORD.search(text, found.end())
        if match and match.start() - found.end() < 80:
            return latex_number(match.group("x")), latex_number(match.group("y"))
    return None


def intercepts_x(text: str, coords: list[tuple[float, float]]) -> list[float]:
    """x-intercepts from `x = a and x = b` or from points on the axis."""
    if re.search(r"does not cross|never cross|entire(?:ly)? curve lies strictly above|"
                 r"lies strictly above the x-axis|does not sit on the x-axis", text, re.I):
        return []
    named = x_equals(text)
    if len(named) >= 2:
        return named[:2]
    if len(named) == 1:
        return named
    on_axis = [x for x, y in coords if abs(y) < 1e-9]
    # Drop a vertex that happens to sit on the axis if it is the only point —
    # finish_parabola treats a single intercept as a touch.
    return on_axis


def x_equals(text: str) -> list[float]:
    """Every `x = N` in the wording, in the order they are written."""
    return [float(found) for found in X_EQUALS.findall(text)]


def coordinates(text: str) -> list[tuple[float, float]]:
    """Coordinate pairs written in the description, dollars or none."""
    found: list[tuple[float, float]] = []
    for match in COORD.finditer(text):
        try:
            found.append((latex_number(match.group("x")), latex_number(match.group("y"))))
        except ValueError:
            continue
    return found


def apply_axis_names(spec: FunctionGraph, text: str) -> None:
    """Letters the wording gives the axes, falling back to x and y."""
    for match in AXIS_LETTER.finditer(text):
        letter = match.group(2).strip()
        if match.group("which").lower().startswith("horiz"):
            spec.x_name = letter
        else:
            spec.y_name = letter
    if re.search(r"\\theta", text) and spec.x_name == "x":
        spec.x_name = r"\theta"


def ticks_in(text: str, horizontal: bool) -> list[tuple[float, str]]:
    """Tick values listed after 'ticks at' or 'labelled with', if that clause is there."""
    # Prefer the clause that belongs to this axis.
    axis = "horizontal" if horizontal else "vertical"
    clause = re.search(
        rf"{axis}\s+axis[^.]{{0,200}}(?:ticks?(?:\s+marks?)?\s+at|labell?ed(?:\s+with)?)\s+"
        rf"(?P<body>\$?[^.]{{0,180}})",
        text,
        re.I,
    )
    body = clause.group("body") if clause else None
    if body is None and not horizontal:
        clause = re.search(
            r"vertical\s+axis[^.]{0,160}ticks?\s+at\s+(?P<body>\$?[^.]{0,120})",
            text,
            re.I,
        )
        body = clause.group("body") if clause else None
    if body is None and horizontal:
        clause = TICKS_AFTER.search(text)
        body = clause.group("body") if clause else None
    if not body:
        return []
    return labelled_values(body)


def labelled_values(body: str) -> list[tuple[float, str]]:
    """Numbers and π-fractions listed in one clause, with the TeX they were written in."""
    pieces = re.findall(
        r"\\frac\{[^}]+\}\{[^}]+\}|\d*\\pi|-?[\d.]+",
        body,
    )
    out: list[tuple[float, str]] = []
    for piece in pieces:
        try:
            value = latex_number(piece)
        except ValueError:
            continue
        tex = piece.strip()
        if re.fullmatch(r"-?[\d.]+", tex):
            tex = number_tex(value)
        out.append((value, tex))
    return out


def default_trig_xticks(spec: FunctionGraph) -> list[tuple[float, str]]:
    """The usual marks on [0, 2π] at every π/2 — readable, not crowded."""
    lo, hi = spec.domain or (0.0, 2 * PI)
    # Always π/2: a frequency-2 wave still reads with the quarter-turn marks, and
    # π/4 labels overlap each other at the book's 12pt.
    step = PI / 2
    ticks = []
    x = lo
    while x <= hi + 1e-9:
        ticks.append((x, pi_tex(x)))
        x += step
    return ticks


def spec_from_fields(raw: dict) -> FunctionGraph:
    """A spec written out as numbers, for figures that no longer need parsing.

    A single curve may still be the `graph` object itself (`kind`, `vertex`, …).
    Several curves are `graph.curves`: a list of those objects, sharing the parent's
    ticks, domain, range, axis names, origin and grid.
    """
    pieces = raw.get("curves")
    if isinstance(pieces, list) and pieces:
        first = curve_from_fields(pieces[0])
        spec = FunctionGraph(kind=first.kind)
        spec.curves = [curve_from_fields(one) for one in pieces]
        spec.x_ticks, spec.y_ticks = merge_ticks(spec.curves)
        # Writer ticks / window on the parent beat anything merged from the curves.
        apply_plot_fields(spec, raw)
        # A figure-level domain is also the interval a trig curve is sampled on.
        if spec.domain is not None:
            for one in spec.curves:
                if one.kind in ("sine", "cosine"):
                    one.domain = spec.domain
        spec.curve = any(one.curve for one in spec.curves)
        if spec.x_name == "x" and all(
            one.kind in ("sine", "cosine") for one in spec.curves
        ):
            spec.x_name = r"\theta"
        return spec
    return curve_from_fields(raw)


def apply_plot_fields(spec: FunctionGraph, raw: dict) -> None:
    """Axis names, window, ticks and grid from a graph object or multi-curve parent."""
    for name in ("x_name", "y_name", "origin"):
        if name in raw:
            setattr(spec, name, str(raw[name]))
    spec.grid = bool(raw.get("grid", False))
    if "domain" in raw and raw["domain"] is not None:
        lo, _ = axis_value(raw["domain"][0])
        hi, _ = axis_value(raw["domain"][1])
        spec.domain = (lo, hi)
    if "range" in raw and raw["range"] is not None:
        lo, _ = axis_value(raw["range"][0])
        hi, _ = axis_value(raw["range"][1])
        spec.y_range = (lo, hi)
    if "x_ticks" in raw and raw["x_ticks"] is not None:
        spec.x_ticks = ticks_from_raw(raw["x_ticks"])
    if "y_ticks" in raw and raw["y_ticks"] is not None:
        spec.y_ticks = ticks_from_raw(raw["y_ticks"])


def curve_from_fields(raw: dict) -> FunctionGraph:
    """One curve from a `graph` object or an entry of `graph.curves`."""
    if not isinstance(raw, dict):
        raise CannotDraw("a curve has to be an object of numbers")
    if raw.get("curves"):
        raise CannotDraw("a curve cannot itself contain curves")
    kind = raw.get("kind")
    if kind not in ("parabola", "sine", "cosine", "line"):
        raise CannotDraw(f"unknown graph kind {kind!r}")
    spec = FunctionGraph(kind=kind)
    apply_plot_fields(spec, raw)
    style = raw.get("style") or "solid"
    if style not in ("solid", "dashed"):
        raise CannotDraw(f"unknown curve style {style!r}")
    spec.style = style
    spec.label = str(raw.get("label") or "")
    if "opens" in raw:
        spec.opens = raw["opens"]
    if "vertex" in raw and raw["vertex"] is not None:
        spec.vertex = (float(raw["vertex"][0]), float(raw["vertex"][1]))
    if "x_intercepts" in raw and raw["x_intercepts"] is not None:
        spec.x_intercepts = [axis_value(x)[0] for x in raw["x_intercepts"]]
    if "y_intercepts" in raw and raw["y_intercepts"]:
        spec.y_intercept = float(raw["y_intercepts"][0])
    elif "y_intercept" in raw and raw["y_intercept"] is not None:
        spec.y_intercept = float(raw["y_intercept"])
    if "amplitude" in raw and raw["amplitude"] is not None:
        amp = float(raw["amplitude"])
        if math.isfinite(amp):
            spec.amplitude = amp
    if "frequency" in raw and raw["frequency"] is not None:
        freq = float(raw["frequency"])
        if math.isfinite(freq):
            spec.frequency = freq
    if "points" in raw:
        spec.points = [(float(x), float(y)) for x, y in raw["points"]]
    spec.curve = bool(raw.get("curve", True))
    enrich_from_label(spec)
    if not math.isfinite(spec.frequency):
        spec.frequency = 1.0
    if spec.amplitude is not None and not math.isfinite(spec.amplitude):
        spec.amplitude = None
    # Kind defaults fill ticks when the writer left them out; named ticks win after.
    named_x = "x_ticks" in raw and raw["x_ticks"] is not None
    named_y = "y_ticks" in raw and raw["y_ticks"] is not None
    if spec.kind == "parabola":
        infer_parabola_opens(spec)
        finish_parabola(spec)
        if not named_x:
            spec.x_ticks = [(x, number_tex(x)) for x in spec.x_intercepts]
            if spec.vertex:
                h, k = spec.vertex
                if all(abs(h - x) > 1e-6 for x, _ in spec.x_ticks):
                    spec.x_ticks.append((h, number_tex(h)))
        if not named_y:
            spec.y_ticks = []
            if spec.vertex:
                h, k = spec.vertex
                if abs(k) > 1e-9:
                    spec.y_ticks = [(k, number_tex(k))]
            if spec.y_intercept is not None and all(
                abs(spec.y_intercept - y) > 1e-6 for y, _ in spec.y_ticks
            ):
                spec.y_ticks.append((spec.y_intercept, number_tex(spec.y_intercept)))
        for x in spec.x_intercepts:
            mark(spec, x, 0.0)
        if spec.y_intercept is not None:
            mark(spec, 0.0, spec.y_intercept)
        if spec.vertex:
            mark(spec, spec.vertex[0], spec.vertex[1])
    elif spec.kind == "line":
        if len(spec.points) < 2:
            raise CannotDraw("a line needs two points")
        if not named_x:
            spec.x_ticks = [(x, number_tex(x)) for x, y in spec.points if abs(y) < 1e-9]
        if not named_y:
            spec.y_ticks = [(y, number_tex(y)) for x, y in spec.points if abs(x) < 1e-9]
        for x, y in spec.points:
            mark(spec, x, y)
    elif spec.kind in ("sine", "cosine"):
        if spec.amplitude is None:
            spec.amplitude = 1.0
        if spec.domain is None:
            spec.domain = (0.0, 2 * PI)
        if not named_x:
            spec.x_ticks = default_trig_xticks(spec)
        if not named_y:
            spec.y_ticks = [
                (-spec.amplitude, number_tex(-spec.amplitude)),
                (spec.amplitude, number_tex(spec.amplitude)),
            ]
        if spec.x_name == "x":
            spec.x_name = r"\theta"
    if named_x:
        spec.x_ticks = ticks_from_raw(raw["x_ticks"])
    if named_y:
        spec.y_ticks = ticks_from_raw(raw["y_ticks"])
    return spec


_FRAC_BRACED = re.compile(r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}")
_FRAC_BARE = re.compile(r"\\frac\s*(\d+)\s*(\d+)")
_SLASH_FRAC = re.compile(r"(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)")
_VERTEX_SQUARE = re.compile(
    r"(?P<sign>[+-])?(?P<coeff>\d*\.?\d*)\(\s*(?P<inner>[^()]*)\s*\)\^2"
)
_POLY_TERM = re.compile(r"[+-][^+-]+")
_ARABIC_LETTER = re.compile(r"[\u0600-\u06FF]")


def _frac_to_number(num: str, den: str) -> str | None:
    try:
        top = float(num.strip())
        bottom = float(den.strip())
    except ValueError:
        return None
    if bottom == 0:
        return None
    return f"({top / bottom:g})"


def _replace_fractions(text: str) -> str | None:
    """Turn \\frac{p}{q}, \\frac12 and p/q into parenthesised decimals."""

    def braced(match: re.Match[str]) -> str:
        return _frac_to_number(match.group(1), match.group(2)) or match.group(0)

    text = _FRAC_BRACED.sub(braced, text)
    if r"\frac" in text and _FRAC_BRACED.search(text):
        return None

    def bare(match: re.Match[str]) -> str:
        return _frac_to_number(match.group(1), match.group(2)) or match.group(0)

    text = _FRAC_BARE.sub(bare, text)
    if r"\frac" in text:
        return None

    def slash(match: re.Match[str]) -> str:
        return _frac_to_number(match.group(1), match.group(2)) or match.group(0)

    return _SLASH_FRAC.sub(slash, text)


def _parse_linear_inner(inner: str) -> tuple[float, float] | None:
    """Coefficients (p, q) of pV + q, or None when the inner is not linear in V."""
    body = inner.replace(" ", "").replace("*", "")
    if not body or "^" in body or body.count("V") > 1:
        return None
    if not body.startswith(("+", "-")):
        body = "+" + body
    terms = _POLY_TERM.findall(body)
    if not terms:
        return None
    p = q = 0.0
    for term in terms:
        sign = -1.0 if term[0] == "-" else 1.0
        piece = term[1:]
        if piece == "V":
            p += sign
        elif "V" in piece:
            coef = piece.replace("V", "")
            try:
                p += sign * (1.0 if coef == "" else float(coef))
            except ValueError:
                return None
        else:
            try:
                q += sign * float(piece)
            except ValueError:
                return None
    return p, q


def _fmt_poly_piece(a: float, b: float, c: float) -> str:
    parts: list[str] = []
    for coef, suffix in ((a, "*V^2"), (b, "*V"), (c, "")):
        if abs(coef) < 1e-15:
            continue
        parts.append(f"{coef:+g}{suffix}")
    return "".join(parts) if parts else "+0"


def _expand_vertex_squares(expr: str) -> str | None:
    """Replace coeff(linear)^2 with the expanded quadratic, including the sign."""
    current = expr
    for _ in range(8):
        match = _VERTEX_SQUARE.search(current)
        if not match:
            return current
        coeff_text = match.group("coeff")
        sign_text = match.group("sign") or ""
        if coeff_text in ("", ".", "-.", "+."):
            outer = 1.0
        else:
            try:
                outer = float(coeff_text)
            except ValueError:
                return None
        if sign_text == "-":
            outer = -outer
        parsed = _parse_linear_inner(match.group("inner"))
        if parsed is None:
            return None
        p, q = parsed
        a = outer * p * p
        b = outer * 2 * p * q
        c = outer * q * q
        current = (
            current[: match.start()]
            + _fmt_poly_piece(a, b, c)
            + current[match.end() :]
        )
    return None


def _parse_quadratic_poly(expr: str) -> tuple[float, float, float] | None:
    """Read aV^2 + bV + c from an expanded expression."""
    body = expr.replace(" ", "").replace("*", "")
    body = body.replace("++", "+").replace("--", "+").replace("+-", "-").replace("-+", "-")
    if not body:
        return None
    if re.search(r"[A-Za-z]", body.replace("V", "")):
        return None
    if not body.startswith(("+", "-")):
        body = "+" + body
    terms = _POLY_TERM.findall(body)
    if not terms or "".join(terms) != body:
        return None
    a = b = c = 0.0
    for term in terms:
        sign = -1.0 if term[0] == "-" else 1.0
        piece = term[1:]
        try:
            if "V^2" in piece:
                coef = piece.replace("V^2", "")
                a += sign * (1.0 if coef == "" else float(coef))
            elif "V^" in piece:
                return None
            elif "V" in piece:
                coef = piece.replace("V", "")
                b += sign * (1.0 if coef == "" else float(coef))
            else:
                c += sign * float(piece)
        except ValueError:
            return None
    if abs(a) < 1e-12:
        return None
    return (a, b, c)


def _lhs_variable(lhs: str) -> str | None | bool:
    """The variable named on the left, True when any of x/t is allowed, None if junk.

    True means the left-hand side is y or a single capital letter, so the right-hand
    side names the variable. None means the left-hand side is not a quadratic LHS.
    """
    body = lhs.strip()
    if not body or "'" in body or r"\prime" in body:
        return None
    if re.fullmatch(r"y", body, re.I):
        return True
    if re.fullmatch(r"[A-Z]", body):
        return True
    found = re.fullmatch(r"[A-Za-z]\(\s*([xt])\s*\)", body)
    if found:
        return found.group(1)
    return None


def quadratic_from_label(label: str) -> tuple[float, float, float] | None:
    """Coefficients (a, b, c) of a Latin quadratic, or None when the label is not one.

    Accepts optional $...$, a left-hand side y / F / f(x) / h(t), and a degree-2
    polynomial in x or t, including vertex form and \\frac{p}{q} coefficients.
    Arabic (ص = س^2), other letters, primes, a second '=', leftovers and a = 0
    return None. Latin only.
    """
    text = label.strip()
    if not text:
        return None
    if text.startswith("$") and text.endswith("$") and len(text) >= 2:
        text = text[1:-1].strip()
    if _ARABIC_LETTER.search(text) or text.count("=") != 1:
        return None
    text = (
        text.replace(r"\left", "")
        .replace(r"\right", "")
        .replace(r"\,", "")
        .replace(r"\;", "")
        .replace(r"\!", "")
        .replace(r"\ ", "")
        .replace("^{2}", "^2")
        .replace(" ", "")
    )
    replaced = _replace_fractions(text)
    if replaced is None:
        return None
    text = replaced.replace(" ", "")
    if "\\" in text:
        return None
    lhs, rhs = text.split("=", 1)
    named = _lhs_variable(lhs)
    if named is None or not rhs:
        return None
    xs = len(re.findall(r"(?<![A-Za-z])x(?![A-Za-z])", rhs))
    ts = len(re.findall(r"(?<![A-Za-z])t(?![A-Za-z])", rhs))
    if named is True:
        if bool(xs) == bool(ts):
            return None
        var = "x" if xs else "t"
    else:
        var = named
        other = "t" if var == "x" else "x"
        if re.search(rf"(?<![A-Za-z]){other}(?![A-Za-z])", rhs):
            return None
        if not re.search(rf"(?<![A-Za-z]){var}(?![A-Za-z])", rhs):
            return None
    expr = re.sub(rf"(?<![A-Za-z]){var}(?![A-Za-z])", "V", rhs)
    if re.search(r"[A-Za-z]", expr.replace("V", "")):
        return None
    expr = _expand_vertex_squares(expr)
    if expr is None:
        return None
    expr = re.sub(r"(?<=\d)V", "*V", expr)
    expr = re.sub(r"\)V", ")*V", expr)
    expr = re.sub(r"\((-?\d+(?:\.\d+)?)\)", r"\1", expr)
    if "(" in expr or ")" in expr:
        return None
    return _parse_quadratic_poly(expr)


def enrich_from_label(spec: FunctionGraph) -> None:
    """Fill amplitude, frequency, opens or stretch from the curve's equation label."""
    text = spec.label.strip()
    if not text:
        return
    if spec.kind in ("sine", "cosine"):
        matched = re.search(
            r"(?:(?P<amp>\d+(?:\.\d+)?)|(?P<unknown>a))?\s*"
            r"\\(?P<fn>sin|cos)\s*"
            r"(?:\(\s*(?P<freq_paren>\d+(?:\.\d+)?)\\theta\s*\)|"
            r"(?P<freq>\d+(?:\.\d+)?)?\\theta)",
            text,
            re.I,
        )
        if matched:
            if matched.group("amp") and (
                spec.amplitude is None or not math.isfinite(spec.amplitude)
            ):
                spec.amplitude = float(matched.group("amp"))
            freq_text = matched.group("freq_paren") or matched.group("freq")
            if freq_text and (
                not math.isfinite(spec.frequency) or abs(spec.frequency - 1.0) < 1e-12
            ):
                # Prefer a concrete factor from the equation over a missing or
                # default frequency (and over a non-finite value the writer left).
                if matched.group("freq_paren") or not math.isfinite(spec.frequency):
                    spec.frequency = float(freq_text)
                elif matched.group("freq"):
                    spec.frequency = float(freq_text)
            fn = matched.group("fn").lower()
            if fn in ("sin", "cos"):
                spec.kind = "sine" if fn == "sin" else "cosine"
        return
    if spec.kind != "parabola":
        return
    coeffs = quadratic_from_label(spec.label)
    if coeffs is not None:
        a, b, c = coeffs
        spec.a = a
        spec.opens = "down" if a < 0 else "up"
        if spec.vertex is None:
            h = round(-b / (2 * a), 6)
            k = round(c - (b * b) / (4 * a), 6)
            spec.vertex = (
                0.0 if abs(h) < 1e-12 else h,
                0.0 if abs(k) < 1e-12 else k,
            )
        return
    if spec.opens is None:
        if re.search(
            r"-\s*(?:\d+(?:\.\d+)?)?\s*(?:\\left\s*)?\(",
            text,
        ) or re.search(r"=\s*-\s*(?:x\^2|\\?x\^\{?2\}?)", text):
            spec.opens = "down"
        elif re.search(r"=\s*\+?\s*(?:x\^2|\\?x\^\{?2\}?)", text):
            spec.opens = "up"


EQ_LABEL = re.compile(
    r"\$([^$]*?(?:"
    r"y|h|"
    r"f\s*\(\s*x\s*\)|"
    r"P\s*\(\s*x\s*\)|"
    r"\\sin|\\cos"
    r")[^$]*?=[^$]+)\$",
    re.I,
)


def equation_labels(*parts: str) -> list[str]:
    """Equations written in $…$ in the exercise wording, in order."""
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        for match in EQ_LABEL.finditer(part or ""):
            label = match.group(1).strip()
            if label in seen:
                continue
            seen.add(label)
            out.append(label)
    return out


def synthesize_label(curve: FunctionGraph) -> str:
    """An equation label built from the numbers already known for `curve`."""
    if curve.kind == "line" and len(curve.points) >= 2:
        (x1, y1), (x2, y2) = curve.points[0], curve.points[1]
        if abs(x2 - x1) > 1e-9:
            m = (y2 - y1) / (x2 - x1)
            c = y1 - m * x1
            return f"y = {poly_term(m, 'x', first=True)}{const_term(c)}"
        return f"x = {number_plain(x1)}"
    if curve.kind == "sine":
        return trig_label("sin", curve.amplitude, curve.frequency)
    if curve.kind == "cosine":
        return trig_label("cos", curve.amplitude, curve.frequency)
    if curve.kind == "parabola":
        if curve.vertex is not None and curve.a is not None:
            h, k = curve.vertex
            a = curve.a
            return (
                f"y = {poly_term(a, '(x' + shift_tex(-h) + ')^2', first=True)}"
                f"{const_term(k)}"
            )
        if curve.vertex is not None:
            h, k = curve.vertex
            sign = "-" if curve.opens == "down" else ""
            return f"y = {sign}(x{shift_tex(-h)})^2{const_term(k)}"
        if curve.x_intercepts:
            return f"y = (x{shift_tex(-curve.x_intercepts[0])})^2"
    return ""


def trig_label(fn: str, amplitude: float | None, frequency: float) -> str:
    """`y = 3 \\sin 2\\theta`, omitting factors of 1."""
    amp = amplitude if amplitude is not None else 1.0
    parts = ["y = "]
    if abs(amp - 1.0) > 1e-9:
        parts.append(number_plain(amp))
        parts.append(r" ")
    parts.append(rf"\{fn}")
    if abs(frequency - 1.0) > 1e-9:
        parts.append(rf" {number_plain(frequency)}")
    parts.append(r"\theta")
    return "".join(parts)


def shift_tex(value: float) -> str:
    """`+ 3` or `- 2` for a translated factor, empty when the shift is 0."""
    if abs(value) < 1e-9:
        return ""
    return f" + {number_plain(value)}" if value > 0 else f" - {number_plain(-value)}"


def poly_term(coeff: float, symbol: str, *, first: bool) -> str:
    """One term of a linear or quadratic label."""
    if abs(coeff) < 1e-9:
        return ""
    if abs(coeff - 1.0) < 1e-9:
        return symbol if first else f" + {symbol}"
    if abs(coeff + 1.0) < 1e-9:
        return f"-{symbol}" if first else f" - {symbol}"
    body = f"{number_plain(abs(coeff))}{symbol}"
    if first:
        return body if coeff > 0 else f"-{body}"
    return f" + {body}" if coeff > 0 else f" - {body}"


def const_term(value: float) -> str:
    """A constant written after other terms, or empty when zero."""
    if abs(value) < 1e-9:
        return ""
    return f" + {number_plain(value)}" if value > 0 else f" - {number_plain(-value)}"


def weak_label(text: str) -> bool:
    """True when the equation names the function without determining the curve."""
    stripped = re.sub(r"\s+", "", text.lower())
    stripped = stripped.replace("\\", "")
    return bool(
        re.fullmatch(
            r"(?:y|f\(x\)|p\(x\)|h)=(?:f\(x\)|[a-z]|[a-z]\(?x\)?)",
            stripped,
        )
        or re.fullmatch(r"(?:y|f\(x\)|p\(x\))=0", stripped)
        or "bx+c" in stripped
        or "ax^2+bx+c" in stripped
        or re.search(r"=[a-z]sin", stripped)
        or re.search(r"=[a-z]cos", stripped)
    )


def pick_label(curve: FunctionGraph, candidates: list[str], index: int) -> str:
    """The label for one curve: a matching equation from the wording, else a built one."""
    pool = [c for c in candidates if not weak_label(c)]
    if curve.label.strip() and not weak_label(curve.label):
        chosen = bake_trig_amplitude(curve, curve.label.strip())
    elif index < len(pool):
        chosen = bake_trig_amplitude(curve, pool[index])
    else:
        chosen = ""
        for text in pool:
            if curve.kind == "sine" and re.search(r"\\sin", text):
                chosen = bake_trig_amplitude(curve, text)
                break
            if curve.kind == "cosine" and re.search(r"\\cos", text):
                chosen = bake_trig_amplitude(curve, text)
                break
            if curve.kind == "parabola" and re.search(r"\^2|x\^\{2\}", text):
                chosen = text
                break
            if curve.kind == "line" and re.search(r"=\s*.*x", text, re.I):
                chosen = text
                break
        if not chosen:
            chosen = synthesize_label(curve)
    # A symbolic quadratic that does not show the opening direction cannot carry
    # "opens down" once that field is dropped; prefer a concrete signed label.
    if (
        curve.kind == "parabola"
        and curve.opens == "down"
        and chosen
        and not re.search(r"-\s*(?:\d+(?:\.\d+)?)?\s*(?:\(|x\^|\\?x\^)", chosen)
    ):
        built = synthesize_label(curve)
        if built:
            return built
    return chosen or synthesize_label(curve)


def bake_trig_amplitude(curve: FunctionGraph, label: str) -> str:
    """Replace a lone `a` in `y = a \\sin …` with the amplitude extract already knows."""
    if curve.kind not in ("sine", "cosine"):
        return label
    amp = curve.amplitude
    if amp is None or abs(amp - 1.0) < 1e-9:
        return label
    if not re.search(r"(?<![a-z])a(?![a-z])\s*\\(?:sin|cos)", label, re.I):
        return label
    return re.sub(
        r"(?<![a-z])a(?![a-z])(\s*\\(?:sin|cos))",
        rf"{number_plain(amp)}\1",
        label,
        count=1,
        flags=re.I,
    )


def minimal_curve_dict(curve: FunctionGraph, label: str) -> dict:
    """The smallest `graph` object for one curve, plus its equation label."""
    text = (label or curve.label or "").strip()
    out: dict = {"kind": curve.kind}
    if text:
        out["label"] = text
    if curve.style == "dashed":
        out["style"] = "dashed"
    if curve.kind == "line":
        if len(curve.points) < 2:
            raise CannotDraw("a line needs two points")
        out["points"] = [[x, y] for x, y in curve.points[:2]]
        return out
    if curve.kind == "parabola":
        # Prefer a vertex; otherwise one x-intercept. Never both.
        if curve.vertex is not None:
            out["vertex"] = [curve.vertex[0], curve.vertex[1]]
        elif curve.x_intercepts:
            out["x_intercepts"] = [curve.x_intercepts[0]]
        else:
            raise CannotDraw("a parabola needs a vertex or an x-intercept")
        return out
    # sine / cosine: kind and label only.
    return out


def minimal_graph(spec: FunctionGraph, labels: list[str]) -> dict:
    """A `graph` field with curves, and ticks/domain/range when the spec has them."""
    curves = spec.curves or [spec]
    body = {
        "curves": [
            minimal_curve_dict(curve, pick_label(curve, labels, i))
            for i, curve in enumerate(curves)
        ]
    }
    if spec.x_ticks:
        body["x_ticks"] = [x for x, _ in spec.x_ticks]
    if spec.y_ticks:
        body["y_ticks"] = [y for y, _ in spec.y_ticks]
    if spec.domain is not None:
        body["domain"] = [spec.domain[0], spec.domain[1]]
    if spec.y_range is not None:
        body["range"] = [spec.y_range[0], spec.y_range[1]]
    if len(body["curves"]) == 1 and not (
        spec.x_ticks or spec.y_ticks or spec.domain or spec.y_range
    ):
        # Preserve the old single-curve shorthand when there is no axis window.
        return body["curves"][0]
    return body


def graph_for_extract(figure: dict) -> dict | None:
    """Build a `graph` field from an existing one or from the description.

    Kept for one-off repair of older files. New exercises write `graph` in
    new_questions.py; extract.py only files that field through.
    """
    raw = figure.get("graph")
    try:
        if isinstance(raw, dict):
            # Re-minify an existing graph without re-parsing description.
            # Use fields as written; description parsing fills gaps only when absent.
            if raw.get("kind") or raw.get("curves"):
                spec = spec_from_fields(raw)
            else:
                spec = parse_description(str(figure.get("description") or ""))
        else:
            spec = parse_description(str(figure.get("description") or ""))
    except CannotDraw:
        return None
    labels = equation_labels(
        str(figure.get("stem") or ""),
        str(figure.get("text") or ""),
        str(figure.get("description") or ""),
    )
    return minimal_graph(spec, labels=labels)


def ticks_from_raw(values: list) -> list[tuple[float, str]]:
    """Tick positions from the graph field, labelled for print.

    Accepts plain numbers, or LaTeX multiples of π written as strings without
    `$...$` (e.g. ``\\pi``, ``2\\pi``, ``\\frac{\\pi}{2}``, ``4\\pi``).
    """
    out: list[tuple[float, str]] = []
    for item in values:
        if isinstance(item, (list, tuple)) and len(item) >= 1:
            value, tex = axis_value(item[0])
            if len(item) > 1 and item[1] not in (None, ""):
                tex = str(item[1])
        else:
            value, tex = axis_value(item)
        if abs(value) < 1e-9:
            continue
        out.append((value, tex))
    return out


def axis_value(item: object) -> tuple[float, str]:
    """One tick or endpoint: a number, or π LaTeX, as (value, print label)."""
    if isinstance(item, str):
        text = item.strip()
        if not text:
            raise CannotDraw("an empty tick is not a number")
        # Bare "pi" / "2pi" from a careless writer → TeX.
        text = re.sub(r"(?<![\\a-zA-Z])pi\b", r"\\pi", text)
        try:
            value = latex_number(text)
        except ValueError as error:
            raise CannotDraw(f"cannot read axis value {item!r}") from error
        # Keep the writer's TeX for π and for fractions, so -\frac{1}{3} prints
        # as a fraction rather than a rounded decimal.
        if re.search(r"\\pi|\\frac", text):
            return value, text
        return value, number_tex(value)
    try:
        value = float(item)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise CannotDraw(f"cannot read axis value {item!r}") from error
    return value, number_tex(value)


def merge_ticks(
    curves: list[FunctionGraph],
) -> tuple[list[tuple[float, str]], list[tuple[float, str]]]:
    """The union of the children's ticks, each value once."""

    def gather(attr: str) -> list[tuple[float, str]]:
        seen: set[float] = set()
        out: list[tuple[float, str]] = []
        for curve in curves:
            for value, tex in getattr(curve, attr):
                key = round(value, 8)
                if key in seen:
                    continue
                seen.add(key)
                out.append((value, tex))
        return out

    return gather("x_ticks"), gather("y_ticks")


# ---------------------------------------------------------------------------
# Numbers written in the description
# ---------------------------------------------------------------------------

def _sign_of(*marks: str) -> float:
    """-1 when an odd number of the marks is a minus, else 1."""
    return -1.0 if sum(1 for mark in marks if mark) % 2 else 1.0


def latex_number(text: str) -> float:
    """A number, a fraction, or a π-fraction as a float.

    A leading minus and a minus inside the numerator or denominator all count,
    so ``-\\frac{1}{3}`` and ``\\frac{-1}{3}`` are the same value.
    """
    raw = text.strip().replace("$", "").replace(" ", "")
    if not raw:
        raise ValueError("empty number")
    raw = re.sub(r"(?<![\\a-zA-Z])pi\b", r"\\pi", raw)
    # \frac{\pi}{2}, -\frac{2\pi}{3}, \frac{-\pi}{4}
    frac = re.fullmatch(r"(-?)\\frac\{(-?)(\d*)\\pi\}\{(-?)(\d+)\}", raw)
    if frac:
        den = int(frac.group(5))
        if den == 0:
            raise ValueError(f"zero denominator in {text!r}")
        num = int(frac.group(3) or "1")
        return _sign_of(frac.group(1), frac.group(2), frac.group(4)) * num * PI / den
    # \frac{1}{2}\pi, -\frac{3}{2}\pi
    frac = re.fullmatch(r"(-?)\\frac\{(-?)(\d+)\}\{(-?)(\d+)\}\\pi", raw)
    if frac:
        den = int(frac.group(5))
        if den == 0:
            raise ValueError(f"zero denominator in {text!r}")
        num = int(frac.group(3))
        return _sign_of(frac.group(1), frac.group(2), frac.group(4)) * num / den * PI
    # \frac{1}{3}, -\frac{1}{3}, \frac{-1}{3}
    frac = re.fullmatch(r"(-?)\\frac\{(-?)(\d+)\}\{(-?)(\d+)\}", raw)
    if frac:
        den = int(frac.group(5))
        if den == 0:
            raise ValueError(f"zero denominator in {text!r}")
        num = int(frac.group(3))
        return _sign_of(frac.group(1), frac.group(2), frac.group(4)) * num / den
    # 2\pi, -3\pi, \pi
    pi = re.fullmatch(r"(-?)(\d*)\\pi", raw)
    if pi:
        sign = -1.0 if pi.group(1) else 1.0
        return sign * float(pi.group(2) or "1") * PI
    if raw in (r"\pi", "pi"):
        return PI
    return float(raw)


def number_tex(value: float) -> str:
    """A tick number as TeX, integers unadorned, π-fractions as fractions."""
    labelled = pi_tex(value)
    if labelled != number_plain(value):
        return labelled
    if abs(value - round(value)) < 1e-8:
        return str(int(round(value)))
    return f"{value:g}"


def number_plain(value: float) -> str:
    if abs(value - round(value)) < 1e-8:
        return str(int(round(value)))
    return f"{value:g}"


def pi_tex(value: float) -> str:
    """Common multiples of π as the TeX a tick should print, else a plain number."""
    # Exact small fractions first, then integer multiples out to several turns.
    named = [
        (0, 1, "0"),
        (1, 4, r"\frac{\pi}{4}"),
        (1, 2, r"\frac{\pi}{2}"),
        (3, 4, r"\frac{3\pi}{4}"),
        (1, 1, r"\pi"),
        (5, 4, r"\frac{5\pi}{4}"),
        (3, 2, r"\frac{3\pi}{2}"),
        (7, 4, r"\frac{7\pi}{4}"),
        (2, 1, r"2\pi"),
        (5, 2, r"\frac{5\pi}{2}"),
        (3, 1, r"3\pi"),
        (7, 2, r"\frac{7\pi}{2}"),
        (4, 1, r"4\pi"),
        (5, 1, r"5\pi"),
        (6, 1, r"6\pi"),
    ]
    for numer, denom, tex in named:
        if abs(value - numer * PI / denom) < 1e-6:
            return tex
        if abs(value + numer * PI / denom) < 1e-6 and numer != 0:
            return f"-{tex}"
    # Any other near-integer multiple of π/2 (e.g. 8π).
    half = value / (PI / 2)
    if abs(half - round(half)) < 1e-6:
        units = int(round(half))
        if units % 2 == 0:
            n = units // 2
            if n == 0:
                return "0"
            if n == 1:
                return r"\pi"
            if n == -1:
                return r"-\pi"
            return rf"{n}\pi"
        numer = abs(units)
        sign = "-" if units < 0 else ""
        if numer == 1:
            return rf"{sign}\frac{{\pi}}{{2}}"
        return rf"{sign}\frac{{{numer}\pi}}{{2}}"
    return number_plain(value)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def scale_of(spec: FunctionGraph, width_mm: float) -> tuple[float, float, float, float, float, float]:
    """Axis window and millimetres per data unit, matching the TikZ layout."""
    xmin, xmax, ymin, ymax = window_of(spec)
    x_span = xmax - xmin
    y_span = ymax - ymin
    inner_w = max(width_mm - ROOM_X, 40.0)
    drawn = all_curves(spec)
    cap_frac = max(TALL_FRAC.get(one.kind, 0.8) for one in drawn)
    cap = cap_frac * width_mm
    x_mm = inner_w / x_span
    y_mm = x_mm
    if y_mm * y_span > cap:
        y_mm = cap / y_span
    waves = [one for one in drawn if one.kind in ("sine", "cosine")]
    if waves:
        amp = max(one.amplitude or 1.0 for one in waves)
        y_mm = max(y_mm, min(18.0 / amp, cap / y_span))
    return xmin, xmax, ymin, ymax, x_mm, y_mm


def render(spec: FunctionGraph, width_mm: float) -> str:
    """One tikzpicture plotting `spec` into a column `width_mm` millimetres across."""
    xmin, xmax, ymin, ymax, x_mm, y_mm = scale_of(spec, width_mm)
    drawn = all_curves(spec)

    lines = [rf"\begin{{tikzpicture}}[x={x_mm:.4f}mm, y={y_mm:.4f}mm]"]
    if spec.grid:
        step_x = tick_step([x for x, _ in spec.x_ticks] or [1.0])
        step_y = tick_step([y for y, _ in spec.y_ticks] or [1.0])
        lines.append(
            rf"  \draw [gridline] ({fmt(xmin)},{fmt(ymin)}) grid "
            rf"[xstep={fmt(step_x)}, ystep={fmt(step_y)}] ({fmt(xmax)},{fmt(ymax)});"
        )
    lines.append(rf"  \draw [axisline, ->] ({fmt(xmin)},0) -- ({fmt(xmax)},0);")
    lines.append(rf"  \node [right] at ({fmt(xmax)},0) {{${spec.x_name}$}};")
    lines.append(rf"  \draw [axisline, ->] (0,{fmt(ymin)}) -- (0,{fmt(ymax)});")
    lines.append(rf"  \node [above] at (0,{fmt(ymax)}) {{${spec.y_name}$}};")
    origin_opts = origin_node_opts(spec)
    lines.append(
        rf"  \node [{origin_opts}, inner sep=1pt]"
        rf" at (0,0) {{${spec.origin}$}};"
    )

    # Only relocate the y-value when the vertex sits on the y-axis; otherwise
    # the number belongs on the axis, even if the vertex is nearby.
    axis_vertex = next(
        (
            one.vertex
            for one in drawn
            if one.vertex is not None
            and abs(one.vertex[0]) <= 0.2
            and abs(one.vertex[1]) > 1e-8
        ),
        None,
    )

    is_trig = any(one.kind in ("sine", "cosine") for one in drawn)
    numbered = labelled_x_tick_values(spec.x_ticks, x_mm, xmin, xmax)
    for x, tex in spec.x_ticks:
        if abs(x) < 1e-8:
            continue
        if not (xmin < x < xmax):
            continue
        lines.append(rf"  \draw [axisline] ({fmt(x)},{TICK_PT}pt) -- ({fmt(x)},-{TICK_PT}pt);")
        if all(abs(x - kept) > 1e-9 for kept in numbered):
            continue
        intercept = intercept_node_opts(spec, x)
        if intercept:
            lines.append(
                rf"  \node [{intercept}, inner sep=1pt] at ({fmt(x)},0) {{${tex}$}};"
            )
            continue
        if vertex_x_away(spec, x):
            lines.append(
                rf"  \node [above=8pt, inner sep=1pt] at ({fmt(x)},0) {{${tex}$}};"
            )
            continue
        if is_trig:
            placed = trig_tick_place(spec, x)
            if placed is None:
                continue
            place, extra = placed
            lines.append(
                rf"  \node [{place}, inner sep=1pt{extra}] at ({fmt(x)},0) {{${tex}$}};"
            )
        else:
            lines.append(
                rf"  \node [below=8pt, inner sep=1pt] at ({fmt(x)},0) {{${tex}$}};"
            )
    for y, tex in spec.y_ticks:
        if abs(y) < 1e-8:
            continue
        if not (ymin < y < ymax):
            continue
        at_vertex = axis_vertex is not None and abs(y - axis_vertex[1]) < 1e-6
        if not at_vertex:
            lines.append(
                rf"  \draw [axisline] ({TICK_PT}pt,{fmt(y)}) -- (-{TICK_PT}pt,{fmt(y)});"
            )
        if at_vertex:
            continue
        if horizontal_line_at(drawn, y):
            side = f"left=8pt, yshift={Y_ON_LINE_SHIFT_PT}pt"
        else:
            side = "left=6pt"
            vertex_left = next(
                (
                    one.vertex
                    for one in drawn
                    if one.vertex is not None and abs(y - one.vertex[1]) < 1e-6
                ),
                None,
            )
            if vertex_left is not None and vertex_left[0] < -0.2:
                side = "right=6pt"
        lines.append(rf"  \node [{side}, inner sep=1pt] at (0,{fmt(y)}) {{${tex}$}};")

    if spec.curve:
        labeled = [
            one
            for one in drawn
            if one.curve and one.show_label and (one.label or "").strip()
        ]
        label_count = len(labeled)
        placed: list[tuple[float, float]] = []
        for one in drawn:
            if not one.curve:
                continue
            lines.extend(curve_draws(one, xmin, xmax, ymin, ymax))
            for x, y, _label in one.marked:
                if not (xmin <= x <= xmax and ymin <= y <= ymax):
                    continue
                lines.append(
                    rf"  \fill ({fmt(x)},{fmt(y)}) circle ({DOT_PT}pt);"
                )
            # Several equations share a top legend keyed by stroke style; a single
            # curve still parks its equation on the frame edge.
            if label_count == 1 and one in labeled:
                lines.extend(
                    curve_label(
                        one,
                        xmin,
                        xmax,
                        ymin,
                        ymax,
                        label_index=0,
                        label_count=1,
                        placed=placed,
                        tick_xs=[x for x, _ in spec.x_ticks],
                    )
                )
        if label_count > 1:
            lines.extend(curve_legend(labeled, xmin, xmax, ymax))
        if axis_vertex is not None:
            h, k = axis_vertex
            # Left of the vertex is empty (the curve is locally flat); below-left
            # lands on the arm.
            lines.append(
                rf"  \node [left=10pt, inner sep=1pt] at ({fmt(h)},{fmt(k)}) "
                rf"{{${number_tex(k)}$}};"
            )

    lines.append(r"\end{tikzpicture}")
    return "\n".join(lines) + "\n"


def window_of(spec: FunctionGraph) -> tuple[float, float, float, float]:
    """The axis ranges, padded past the features and always containing the origin.

    When the writer set both `domain` and `range` on the graph, those are the
    named window, then padded slightly so arrowheads sit past the last tick and a
    curve that ends on an endpoint (e.g. 2π) does not land on the arrow tip.
    """
    if spec.domain is not None and spec.y_range is not None:
        xmin, xmax = padded(list(spec.domain))
        ymin, ymax = padded(list(spec.y_range))
        return xmin, xmax, ymin, ymax
    xs: list[float] = [0.0]
    ys: list[float] = [0.0]
    xs += [x for x, _ in spec.x_ticks]
    ys += [y for y, _ in spec.y_ticks]
    for one in all_curves(spec):
        xs += one.x_intercepts
        if one.y_intercept is not None:
            ys.append(one.y_intercept)
        if one.vertex:
            xs.append(one.vertex[0])
            ys.append(one.vertex[1])
        xs += [x for x, _ in one.points]
        ys += [y for _, y in one.points]
        xs += [x for x, _, _ in one.marked]
        ys += [y for _, y, _ in one.marked]
        if one.domain:
            xs += list(one.domain)
        if one.amplitude is not None:
            ys += [one.amplitude, -one.amplitude]
        if one.kind == "parabola" and one.vertex and one.a is not None:
            h, k = one.vertex
            a = one.a
            # A little of the arms past the last feature, so a crossing still reads
            # as a curve going on, not as the end of the drawing.
            if len(one.x_intercepts) >= 2:
                left, right = min(one.x_intercepts), max(one.x_intercepts)
                extra = max(0.8, 0.1 * (right - left))
                for x in (left - extra, right + extra):
                    xs.append(x)
                    ys.append(a * (x - h) ** 2 + k)
            else:
                xs += [h - 2.2, h + 2.2]
                ys.append(a * (2.2 ** 2) + k)
    xmin, xmax = padded(xs)
    ymin, ymax = padded(ys)
    return xmin, xmax, ymin, ymax


def padded(values: list[float]) -> tuple[float, float]:
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        lo, hi = lo - 1.0, hi + 1.0
    pad = max(PAD_MIN, PAD_FRAC * (hi - lo))
    return lo - pad, hi + pad


def stroke_of(spec: FunctionGraph) -> str:
    """Path options for a curve: ordinary black (draw.md default), dashed when asked."""
    return "dashed" if spec.style == "dashed" else ""


def path_options(spec: FunctionGraph, *options: str) -> str:
    """Comma-joined TikZ path options, skipping empties."""
    return ", ".join(part for part in (stroke_of(spec), *options) if part)


def curve_draws(
    spec: FunctionGraph, xmin: float, xmax: float, ymin: float, ymax: float
) -> list[str]:
    """The draw command that plots the function inside the window."""
    if spec.kind == "line":
        return line_draw(spec, xmin, xmax, ymin, ymax)
    if spec.kind in ("sine", "cosine"):
        return trig_draw(spec, xmin, xmax)
    return parabola_draw(spec, xmin, xmax, ymin, ymax)


def line_ends(
    spec: FunctionGraph, xmin: float, xmax: float, ymin: float, ymax: float
) -> tuple[tuple[float, float], tuple[float, float]]:
    """The two ends of the line inside the window."""
    (x1, y1), (x2, y2) = spec.points[0], spec.points[1]
    if abs(x2 - x1) < 1e-9:
        return (x1, ymin), (x1, ymax)
    m = (y2 - y1) / (x2 - x1)
    c = y1 - m * x1
    # Clip to the window by intersecting with the four sides, then taking the
    # two hits that lie on the segment of the window.
    hits: list[tuple[float, float]] = []
    for x in (xmin, xmax):
        y = m * x + c
        if ymin - 1e-9 <= y <= ymax + 1e-9:
            hits.append((x, y))
    if abs(m) > 1e-9:
        for y in (ymin, ymax):
            x = (y - c) / m
            if xmin - 1e-9 <= x <= xmax + 1e-9:
                hits.append((x, y))
    unique: list[tuple[float, float]] = []
    for point in hits:
        if not any(abs(point[0] - u[0]) < 1e-6 and abs(point[1] - u[1]) < 1e-6 for u in unique):
            unique.append(point)
    if len(unique) < 2:
        return (x1, y1), (x2, y2)
    a, b = unique[0], unique[1]
    for i, p in enumerate(unique):
        for q in unique[i + 1 :]:
            if (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 > (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2:
                a, b = p, q
    return a, b


def line_draw(
    spec: FunctionGraph, xmin: float, xmax: float, ymin: float, ymax: float
) -> list[str]:
    a, b = line_ends(spec, xmin, xmax, ymin, ymax)
    opts = path_options(spec)
    prefix = f"[{opts}] " if opts else ""
    return [
        rf"  \draw {prefix}({fmt(a[0])},{fmt(a[1])}) -- ({fmt(b[0])},{fmt(b[1])});"
    ]


def trig_draw(spec: FunctionGraph, xmin: float, xmax: float) -> list[str]:
    lo, hi = spec.domain or (0.0, 2 * PI)
    lo = max(lo, xmin)
    hi = min(hi, xmax)
    amp = spec.amplitude or 1.0
    freq = spec.frequency
    fn = "sin" if spec.kind == "sine" else "cos"
    expr = rf"{fmt(amp)}*{fn}({fmt(freq)}*\x r)"
    return [
        rf"  \draw [{path_options(spec, f'domain={fmt(lo)}:{fmt(hi)}', f'samples={SAMPLES}')}] "
        rf"plot (\x, {{{expr}}});"
    ]


def parabola_draw(
    spec: FunctionGraph, xmin: float, xmax: float, ymin: float, ymax: float
) -> list[str]:
    if spec.vertex is None or spec.a is None:
        raise CannotDraw("a parabola has no vertex to plot")
    h, k = spec.vertex
    a = spec.a
    lo, hi = parabola_domain(a, h, k, xmin, xmax, ymin, ymax)
    shift = r"(\x)" if abs(h) < 1e-12 else (
        rf"(\x+{fmt(-h)})" if h < 0 else rf"(\x-{fmt(h)})"
    )
    expr = rf"{fmt(a)}*{shift}*{shift}+({fmt(k)})"
    return [
        rf"  \draw [{path_options(spec, f'domain={fmt(lo)}:{fmt(hi)}', f'samples={SAMPLES}')}] "
        rf"plot (\x, {{{expr}}});"
    ]


def parabola_domain(
    a: float, h: float, k: float,
    xmin: float, xmax: float, ymin: float, ymax: float,
) -> tuple[float, float]:
    """The x-range on which the parabola stays inside the y-window."""

    def height(x: float) -> float:
        return a * (x - h) ** 2 + k

    def inside(x: float) -> bool:
        return ymin - 1e-6 <= height(x) <= ymax + 1e-6

    xs: list[float] = []
    if inside(xmin):
        xs.append(xmin)
    if inside(xmax):
        xs.append(xmax)
    for y_lim in (ymin, ymax):
        if abs(a) < 1e-12:
            continue
        disc = (y_lim - k) / a
        if disc < 0:
            continue
        root = math.sqrt(disc)
        for x in (h - root, h + root):
            if xmin - 1e-9 <= x <= xmax + 1e-9:
                xs.append(x)
    if len(xs) < 2:
        return xmin, xmax
    lo, hi = min(xs), max(xs)
    inset = 0.04 * (xmax - xmin)
    return max(lo, xmin + inset), min(hi, xmax - inset)


# How far a curve's equation sits off the stroke or the top of the frame, in TeX
# points.
LABEL_GAP_PT = 8

# Multi-curve legend: sample stroke length and row pitch, in millimetres.
LEGEND_SAMPLE_MM = 6.0
LEGEND_ROW_MM = 3.5


def curve_legend(
    labeled: list[FunctionGraph],
    xmin: float,
    xmax: float,
    ymax: float,
) -> list[str]:
    """A top-of-figure key: solid/dashed sample beside each equation."""
    if len(labeled) < 2:
        return []
    x = (xmin + xmax) / 2.0
    rows: list[str] = []
    for i, one in enumerate(labeled):
        text = one.label.strip()
        math = text if (text.startswith("$") and text.endswith("$")) else f"${text}$"
        y_mm = -i * LEGEND_ROW_MM
        stroke = "dashed" if one.style == "dashed" else ""
        opts = f"[{stroke}] " if stroke else ""
        rows.append(
            rf"    \draw {opts}(0,{fmt(y_mm)}mm) -- ({fmt(LEGEND_SAMPLE_MM)}mm,{fmt(y_mm)}mm) "
            rf"node [right=4pt, inner sep=1pt] {{{math}}};"
        )
    body = "\n".join(rows)
    return [
        rf"  \begin{{scope}}[shift={{({fmt(x)},{fmt(ymax)})}}]",
        rf"    \begin{{scope}}[yshift={LABEL_GAP_PT}pt]",
        body,
        r"    \end{scope}",
        r"  \end{scope}",
    ]


def curve_label(
    spec: FunctionGraph,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    label_index: int = 0,
    label_count: int = 1,
    placed: list[tuple[float, float]] | None = None,
    tick_xs: list[float] | None = None,
) -> list[str]:
    """The equation written next to the curve, if this curve has one to show."""
    if not spec.show_label:
        return []
    text = spec.label.strip()
    if not text:
        return []
    math = text if (text.startswith("$") and text.endswith("$")) else f"${text}$"
    seats = placed if placed is not None else []
    x, y, anchor = label_anchor(
        spec,
        xmin,
        xmax,
        ymin,
        ymax,
        label_index=label_index,
        label_count=label_count,
        placed=seats,
        tick_xs=tick_xs or [],
    )
    seats.append((x, y))
    opts = f"{anchor}, inner sep=1pt" if anchor else "inner sep=1pt"
    return [
        rf"  \node [{opts}] at ({fmt(x)},{fmt(y)}) {{{math}}};"
    ]


def label_anchor(
    spec: FunctionGraph,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    label_index: int = 0,
    label_count: int = 1,
    placed: list[tuple[float, float]] | None = None,
    tick_xs: list[float] | None = None,
) -> tuple[float, float, str]:
    """A seat for the equation, clear of the stroke and the axes.

    One curve: park on the top edge of the window (in the padding past every
    feature), kept off x = 0 so it does not land on the axis name $y$.

    Several curves: seat each label on a distinctive point of its own stroke —
    a peak or trough for sine/cosine, the right end of a horizontal line, the
    vertex or an arm of a parabola — choosing the candidate farthest from seats
    already taken.
    """
    gap = f"={LABEL_GAP_PT}pt"
    if label_count > 1:
        seat = multi_label_anchor(
            spec,
            xmin,
            xmax,
            ymin,
            ymax,
            label_index,
            placed or [],
            gap,
            tick_xs=tick_xs or [],
        )
        if seat is not None:
            return seat
    dx = max(xmax - xmin, 1e-9)
    if spec.style == "dashed":
        frac = 0.42
    elif spec.kind == "line":
        frac = 0.62
    elif spec.kind in ("sine", "cosine"):
        frac = 0.70
    else:
        frac = 0.78
    x = xmin + frac * dx
    # Keep clear of the axis name at (0, ymax) and of the top-right corner
    # where the two arrowheads meet the frame.
    if abs(x) < 0.35 * dx:
        x = xmax - 0.22 * dx if xmax > abs(xmin) else xmin + 0.22 * dx
    # Sit above the frame and a little left of the right edge so the node grows
    # into empty space rather than onto $y$ or the rising arm.
    return min(x, xmax - 0.12 * dx), ymax, f"above{gap}"


def multi_label_anchor(
    spec: FunctionGraph,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    label_index: int,
    placed: list[tuple[float, float]],
    gap: str,
    tick_xs: list[float] | None = None,
) -> tuple[float, float, str] | None:
    """Pick an on-curve seat for one of several shared-axis equations."""
    candidates = label_candidates(
        spec, xmin, xmax, ymin, ymax, label_index, gap, tick_xs=tick_xs or []
    )
    if not candidates:
        return None
    # Alternate peak/trough so two waves do not both claim the top of the frame.
    prefer_above = label_index % 2 == 0
    preferred = [c for c in candidates if ("above" in c[2]) == prefer_above]
    pool = preferred or candidates
    # If every preferred seat shares an x with one already taken, open the pool so
    # a second wave can sit elsewhere (e.g. not both at θ = π).
    if placed and preferred:
        dx = max(xmax - xmin, 1e-9)
        dy = max(ymax - ymin, 1e-9)

        def far_enough(c: tuple[float, float, str]) -> bool:
            x, y, _ = c
            return all(
                ((x - px) / dx) ** 2 + ((y - py) / dy) ** 2 > 0.12
                for px, py in placed
            )

        free = [c for c in preferred if far_enough(c)]
        if free:
            pool = free
        elif not any(far_enough(c) for c in preferred):
            # Preferred column is taken; allow the other side of the same wave.
            pool = [c for c in candidates if far_enough(c)] or candidates
    x, y, anchor = farthest_label_seat(pool, placed, xmin, xmax, ymin, ymax)
    # Top/bottom frame seats sit next to the axis name; on-curve seats keep their x.
    if abs(y - ymax) < 1e-9 or abs(y - ymin) < 1e-9:
        x = nudge_off_axis_name(x, xmin, xmax)
    return x, y, anchor


def nudge_off_axis_name(x: float, xmin: float, xmax: float) -> float:
    """Keep a top/bottom equation clear of the axis name at x = 0."""
    dx = max(xmax - xmin, 1e-9)
    clear = 0.35 * dx
    if abs(x) >= clear:
        return x
    if xmax > abs(xmin):
        return min(clear, xmax - 0.12 * dx)
    return max(-clear, xmin + 0.12 * dx)


def label_candidates(
    spec: FunctionGraph,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    label_index: int,
    gap: str,
    tick_xs: list[float] | None = None,
) -> list[tuple[float, float, str]]:
    """Possible (x, y, anchor) seats for one curve among several."""
    if spec.kind in ("sine", "cosine"):
        return trig_label_candidates(
            spec, xmin, xmax, ymin, ymax, gap, tick_xs=tick_xs or []
        )
    if spec.kind == "line":
        return line_label_candidates(spec, xmin, xmax, ymin, ymax, label_index, gap)
    if spec.kind == "parabola":
        return parabola_label_candidates(spec, xmin, xmax, ymin, ymax, gap)
    return []


def trig_label_candidates(
    spec: FunctionGraph,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    gap: str,
    tick_xs: list[float] | None = None,
) -> list[tuple[float, float, str]]:
    """Peaks and troughs on the stroke, clear of the y-axis name.

    Trough seats that land on an x-tick column are nudged sideways so the
    equation stays under the trough without covering the tick number.
    """
    amp = spec.amplitude if spec.amplitude is not None else 1.0
    freq = spec.frequency if math.isfinite(spec.frequency) and abs(spec.frequency) > 1e-12 else 1.0
    lo, hi = spec.domain or (xmin, xmax)
    lo, hi = max(lo, xmin), min(hi, xmax)
    dx = max(xmax - xmin, 1e-9)
    axis_clear = 0.12 * dx
    ticks = tick_xs or []
    out: list[tuple[float, float, str]] = []
    span = max(hi - lo, 0.0)
    turns = int(math.ceil(freq * span / PI)) + 3
    for base, y, is_max in (
        [(0.5 * PI, amp, True), (1.5 * PI, -amp, False)]
        if spec.kind == "sine"
        else [(0.0, amp, True), (PI, -amp, False)]
    ):
        for k in range(-2, turns + 2):
            x = (base + k * 2 * PI) / freq
            if x < lo - 1e-9 or x > hi + 1e-9:
                continue
            if abs(x) < axis_clear:
                continue
            if not (ymin - 1e-6 <= y <= ymax + 1e-6):
                continue
            if is_max:
                out.append((x, y, f"above{gap}"))
            else:
                out.append(
                    (nudge_off_tick_column(x, xmin, xmax, ticks), y, f"below{gap}")
                )
    return out


def nudge_off_tick_column(
    x: float, xmin: float, xmax: float, tick_xs: list[float]
) -> float:
    """Shift a trough label off an x-tick column when it would cover the number."""
    if not tick_xs:
        return x
    dx = max(xmax - xmin, 1e-9)
    near = 0.04 * dx
    if all(abs(x - tx) > near for tx in tick_xs):
        return x
    # Small shift: stay under the same trough, only clear the tick number.
    shift = 0.05 * dx
    right = x + shift
    left = x - shift
    if right <= xmax and (left < xmin or (xmax - x) >= (x - xmin)):
        return right
    if left >= xmin:
        return left
    return x


def line_label_candidates(
    spec: FunctionGraph,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    label_index: int,
    gap: str,
) -> list[tuple[float, float, str]]:
    """Right end of a horizontal line, or mid-segment above/below otherwise."""
    a, b = line_ends(spec, xmin, xmax, ymin, ymax)
    (x1, y1), (x2, y2) = a, b
    if abs(y2 - y1) < 1e-9:
        right = a if a[0] >= b[0] else b
        return [(right[0], right[1], f"right{gap}")]
    mid = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
    side = f"above{gap}" if label_index % 2 == 0 else f"below{gap}"
    return [(mid[0], mid[1], side)]


def parabola_label_candidates(
    spec: FunctionGraph,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    gap: str,
) -> list[tuple[float, float, str]]:
    """Vertex and mid-arm x-seats, parked on the frame so they clear the stroke."""
    if spec.vertex is None or spec.a is None:
        return []
    h, k = spec.vertex
    a = spec.a
    lo, hi = parabola_domain(a, h, k, xmin, xmax, ymin, ymax)
    out: list[tuple[float, float, str]] = []
    xs: list[float] = []
    if xmin <= h <= xmax:
        xs.append(h)
    for frac in (0.35, 0.65):
        xs.append(lo + frac * (hi - lo))
    for x in xs:
        if not (xmin <= x <= xmax):
            continue
        y = a * (x - h) ** 2 + k
        if not (ymin - 1e-6 <= y <= ymax + 1e-6):
            continue
        out.append((x, ymax, f"above{gap}"))
        out.append((x, ymin, f"below{gap}"))
    return out


def farthest_label_seat(
    candidates: list[tuple[float, float, str]],
    placed: list[tuple[float, float]],
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
) -> tuple[float, float, str]:
    """The candidate farthest from already-placed seats in normalized coords."""
    dx = max(xmax - xmin, 1e-9)
    dy = max(ymax - ymin, 1e-9)

    def score(seat: tuple[float, float, str]) -> float:
        x, y, _ = seat
        if not placed:
            # First equation: earliest seat in reading order (leftmost).
            return -x
        return min(
            ((x - px) / dx) ** 2 + ((y - py) / dy) ** 2 for px, py in placed
        )

    return max(candidates, key=score)


def label_nudge(
    anchor: str, xmin: float, xmax: float, ymin: float, ymax: float
) -> tuple[float, float]:
    """A small extra data-space push for matplotlib (anchor point already cleared)."""
    base = (anchor or "").split("=")[0].strip()
    gap_x = 0.02 * (xmax - xmin)
    gap_y = 0.04 * (ymax - ymin)
    dx = dy = 0.0
    if base == "left" or base.endswith(" left"):
        dx = -gap_x
    elif base == "right" or base.endswith(" right"):
        dx = gap_x
    if "above" in base:
        dy = gap_y
    elif "below" in base:
        dy = -gap_y
    return dx, dy


def vertex_x_away(spec: FunctionGraph, x: float) -> bool:
    """True when x is a vertex whose y is far from the axis — empty space is above."""
    for one in all_curves(spec):
        if one.vertex is None:
            continue
        h, k = one.vertex
        if abs(x - h) < 1e-6 and abs(k) > 1.0:
            return True
    return False


def horizontal_line_at(drawn: list[FunctionGraph], y: float) -> bool:
    """Whether any curve is a horizontal line through this y-value."""
    for one in drawn:
        if one.kind != "line" or len(one.points) < 2:
            continue
        y1, y2 = one.points[0][1], one.points[1][1]
        if abs(y1 - y2) < 1e-9 and abs(y1 - y) < 1e-6:
            return True
    return False


def labelled_x_tick_values(
    ticks: list[tuple[float, str]], x_mm: float, xmin: float, xmax: float
) -> list[float]:
    """x-values whose tick numbers are printed; marks may still be drawn for the rest.

    When neighbouring labels would overlap in millimetres (π/4 on a narrow column),
    keep every mark and drop the odd π/4 numbers, leaving π/2, π, 3π/2, 2π.
    """
    visible = [x for x, _ in ticks if abs(x) > 1e-8 and xmin < x < xmax]
    if len(visible) < 2:
        return visible
    ordered = sorted(visible)
    min_mm = min((ordered[i + 1] - ordered[i]) * x_mm for i in range(len(ordered) - 1))
    if min_mm >= TICK_LABEL_MIN_MM:
        return visible
    kept = [x for x in visible if not odd_pi_quarter(x)]
    return kept or visible


def odd_pi_quarter(x: float) -> bool:
    """True for ±π/4, ±3π/4, ±5π/4, … — the denser odd multiples of π/4."""
    q = x / (PI / 4)
    rounded = round(q)
    return abs(q - rounded) < 1e-6 and abs(rounded) % 2 == 1


def trig_wave_at(spec: FunctionGraph, x: float) -> tuple[float, float]:
    """Value and slope of a sine or cosine at x."""
    amp = spec.amplitude or 1.0
    freq = spec.frequency if math.isfinite(spec.frequency) else 1.0
    if not math.isfinite(amp):
        amp = 1.0
    if spec.kind == "sine":
        return amp * math.sin(freq * x), amp * freq * math.cos(freq * x)
    return amp * math.cos(freq * x), -amp * freq * math.sin(freq * x)


def trig_tick_place(spec: FunctionGraph, x: float) -> tuple[str, str] | None:
    """Vertical placement and extra options for a trigonometric tick number.

    Returns None to keep the tick mark but skip the number: two waves leave no
    empty half-plane at a trough that is also a zero of the other curve.
    """
    values: list[float] = []
    slope = 0.0
    for one in all_curves(spec):
        if one.kind not in ("sine", "cosine"):
            continue
        y, dy = trig_wave_at(one, x)
        values.append(y)
        if abs(dy) > abs(slope):
            slope = dy
    if not values:
        return f"below={TRIG_GAP_PT}pt", ""
    extra = ""
    if abs(slope) >= 0.15:
        extra = f", xshift=-{TRIG_SHIFT_PT}pt" if slope < 0 else f", xshift={TRIG_SHIFT_PT}pt"
    near_axis = any(abs(y) < 0.25 for y in values)
    deep_below = min(values) < -0.4
    if deep_below and near_axis:
        return None
    if deep_below or all(y < -0.15 for y in values):
        return "above=8pt", extra if abs(slope) >= 0.15 else ""
    return f"below={TRIG_GAP_PT}pt", extra


def trig_tick_shift(spec: FunctionGraph, x: float) -> str:
    """Nudge a trigonometric tick off the curve where the wave crosses the axis."""
    placed = trig_tick_place(spec, x)
    if not placed:
        return ""
    _, extra = placed
    return extra


def origin_node_opts(spec: FunctionGraph) -> str:
    """Where to sit the origin 0, off the axes and off the curve.

    Default is below-left. When a parabola occupies that corner (vertex below
    the axis and intercepts on both sides of 0), sit above-left instead. When
    only a left intercept crowds Q3, sit below-right.
    """
    q3 = q4 = False
    left_intercept_near = False
    for one in all_curves(spec):
        intercepts = list(one.x_intercepts)
        if one.kind == "parabola" and one.vertex is not None:
            _h, k = one.vertex
            if intercepts and min(intercepts) < -1e-6 and max(intercepts) > 1e-6 and k < -1e-8:
                q3 = q4 = True
            elif k > 1e-8 and any(-1.5 < xi < -1e-6 for xi in intercepts):
                left_intercept_near = True
        if any(-1.5 < xi < -1e-6 for xi in intercepts):
            left_intercept_near = True
    if q3 and q4:
        return "above left=6pt and 6pt"
    if left_intercept_near:
        return "below right=6pt and 6pt"
    nearby_y_neg = any(-1.5 < y < -1e-8 for y, _ in spec.y_ticks)
    if nearby_y_neg:
        return "below left=6pt and 8pt"
    return "below left=6pt and 6pt"


def intercept_node_opts(spec: FunctionGraph, x: float) -> str:
    """TikZ options for a parabola intercept number, or empty if x is not one.

    The number sits further below the axis and grows into the empty strip so a
    leading minus does not sit on the exterior arm. Tick marks stay on the intercept.
    """
    for one in all_curves(spec):
        if one.kind != "parabola" or one.vertex is None:
            continue
        if all(abs(x - xi) > 1e-6 for xi in one.x_intercepts):
            continue
        h, k = one.vertex
        toward_vertex = k > 0
        gap, shift = INTERCEPT_GAP_PT, INTERCEPT_SHIFT_PT
        # A negative intercept next to the origin: interior shift lands the
        # number on the y-axis. Above-left is empty (the exterior arm is below).
        if -1.4 < x < -1e-6:
            return "above left=6pt and 4pt"
        if x < h - 1e-6:
            if toward_vertex:
                return f"anchor=north west, xshift={shift}pt, yshift=-{gap}pt"
            return f"anchor=north east, xshift=-{shift}pt, yshift=-{gap}pt"
        if x > h + 1e-6:
            if toward_vertex:
                return f"anchor=north east, xshift=-{shift}pt, yshift=-{gap}pt"
            return f"anchor=north west, xshift={shift}pt, yshift=-{gap}pt"
        return f"anchor=north, yshift=-{gap}pt"
    return ""


def intercept_shift(spec: FunctionGraph, x: float) -> str:
    """Nudge an intercept's tick number into the empty side of the axis."""
    opts = intercept_node_opts(spec, x)
    if not opts:
        return ""
    if f"xshift=-{INTERCEPT_SHIFT_PT}pt" in opts:
        return f", xshift=-{INTERCEPT_SHIFT_PT}pt"
    if f"xshift={INTERCEPT_SHIFT_PT}pt" in opts:
        return f", xshift={INTERCEPT_SHIFT_PT}pt"
    return ""


def tick_step(values: list[float]) -> float:
    """A grid step from the ticks, defaulting to 1."""
    nums = sorted({round(v, 8) for v in values if abs(v) > 1e-9})
    if len(nums) >= 2:
        return min(nums[i + 1] - nums[i] for i in range(len(nums) - 1))
    return 1.0


def fmt(value: float) -> str:
    """A coordinate for TikZ, short and stable."""
    if abs(value) < 1e-10:
        return "0"
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text


# ---------------------------------------------------------------------------
# Matplotlib preview — same spec, not used on the paper
# ---------------------------------------------------------------------------

MM_PER_INCH = 25.4
PT_PER_INCH = 72.27
CURVE_BLUE_HEX = "#1A5FB4"
GRID_GREY_HEX = "#CCCCCC"
LABEL_PT = 12.0
RULE_PT = 1.0
LIGHT_PT = 0.5


def render_png(spec: FunctionGraph, width_mm: float, path: str) -> None:
    """Plot `spec` with ordinary matplotlib defaults into `path`.

    Data come from the same `graph` numbers as TikZ; the look does not — default
    spines, ticks, colour cycle, and a legend for curve equations.
    """
    import matplotlib.pyplot as plt

    xmin, xmax, ymin, ymax, x_mm, y_mm = scale_of(spec, width_mm)
    drawn = all_curves(spec)
    height_mm = y_mm * (ymax - ymin) + ROOM_Y
    fig, ax = plt.subplots(
        figsize=(width_mm / MM_PER_INCH, height_mm / MM_PER_INCH), dpi=150
    )
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_aspect(y_mm / x_mm)
    ax.set_xlabel(f"${spec.x_name}$")
    ax.set_ylabel(f"${spec.y_name}$")
    ax.axhline(0.0, color="0.5", lw=0.8, zorder=1)
    ax.axvline(0.0, color="0.5", lw=0.8, zorder=1)
    if spec.grid:
        ax.grid(True, which="major", alpha=0.35)

    x_tick_vals = [x for x, _ in spec.x_ticks if abs(x) > 1e-8 and xmin < x < xmax]
    y_tick_vals = [y for y, _ in spec.y_ticks if abs(y) > 1e-8 and ymin < y < ymax]
    if x_tick_vals:
        ax.set_xticks(x_tick_vals)
        ax.set_xticklabels(
            [f"${tex}$" for x, tex in spec.x_ticks if abs(x) > 1e-8 and xmin < x < xmax]
        )
    if y_tick_vals:
        ax.set_yticks(y_tick_vals)
        ax.set_yticklabels(
            [f"${tex}$" for y, tex in spec.y_ticks if abs(y) > 1e-8 and ymin < y < ymax]
        )

    if spec.curve:
        for one in drawn:
            if not one.curve:
                continue
            xs, ys = curve_xy(one, xmin, xmax, ymin, ymax)
            ls = "--" if one.style == "dashed" else "-"
            label = one.label.strip() if one.show_label else ""
            if label and label.startswith("$") and label.endswith("$"):
                legend = label
            elif label:
                legend = f"${label}$"
            else:
                legend = one.kind if len(drawn) > 1 else None
            line, = ax.plot(xs, ys, ls=ls, label=legend, zorder=3)
            for x, y, _lab in one.marked:
                if xmin <= x <= xmax and ymin <= y <= ymax:
                    ax.plot(
                        x, y, "o", color=line.get_color(), ms=4,
                        zorder=4, markeredgewidth=0,
                    )
        if any(one.show_label and (one.label or one.kind) for one in drawn if one.curve):
            ax.legend(loc="best", frameon=True)

    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor="white")
    plt.close(fig)


def curve_xy(
    spec: FunctionGraph, xmin: float, xmax: float, ymin: float, ymax: float
) -> tuple[list[float], list[float]]:
    """Sampled points of one curve inside the window."""
    if spec.kind == "line":
        a, b = line_ends(spec, xmin, xmax, ymin, ymax)
        return [a[0], b[0]], [a[1], b[1]]
    if spec.kind in ("sine", "cosine"):
        lo, hi = spec.domain or (0.0, 2 * PI)
        lo, hi = max(lo, xmin), min(hi, xmax)
        amp = spec.amplitude or 1.0
        freq = spec.frequency
        fn = math.sin if spec.kind == "sine" else math.cos
        xs = [lo + i * (hi - lo) / (SAMPLES - 1) for i in range(SAMPLES)]
        return xs, [amp * fn(freq * x) for x in xs]
    if spec.vertex is None or spec.a is None:
        raise CannotDraw("a parabola has no vertex to plot")
    h, k = spec.vertex
    a = spec.a
    lo, hi = parabola_domain(a, h, k, xmin, xmax, ymin, ymax)
    xs = [lo + i * (hi - lo) / (SAMPLES - 1) for i in range(SAMPLES)]
    return xs, [a * (x - h) ** 2 + k for x in xs]


def mpl_xshift(spec: FunctionGraph, x: float, drawn: list[FunctionGraph]) -> float:
    """Horizontal nudge of a tick number, in millimetres."""
    extra = intercept_shift(spec, x)
    if any(one.kind in ("sine", "cosine") for one in drawn):
        extra = trig_tick_shift(spec, x)
    found = re.search(r"xshift=(-?\d+)pt", extra)
    if not found:
        return 0.0
    return float(found.group(1)) / PT_PER_INCH * MM_PER_INCH


def mpl_anchor(anchor: str) -> tuple[str, str]:
    """TikZ node anchor as matplotlib ha, va (ignores a `=12pt` gap)."""
    base = (anchor or "").split("=")[0].strip()
    if not base:
        return "center", "center"
    mapping = {
        "above": ("center", "bottom"),
        "below": ("center", "top"),
        "left": ("right", "center"),
        "right": ("left", "center"),
        "above left": ("right", "bottom"),
        "below left": ("right", "top"),
        "above right": ("left", "bottom"),
        "below right": ("left", "top"),
    }
    return mapping.get(base, ("center", "bottom"))


def mpl_math(text: str) -> str:
    """A TeX fragment as a matplotlib mathtext string."""
    raw = text.strip()
    if raw.startswith("$") and raw.endswith("$"):
        raw = raw[1:-1]
    return f"${raw}$"


def mpl_font() -> str:
    """STIX Two if the machine has it, else a serif matplotlib already knows."""
    from matplotlib import font_manager

    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("STIX Two Text", "STIX Two Math", "STIX", "DejaVu Serif"):
        if name in available:
            return name
    return "DejaVu Serif"


def book_blue() -> str:
    """The book's one colour, read from latex.py when that module is importable."""
    try:
        from src.latex import CURVE_BLUE
    except Exception:
        return CURVE_BLUE_HEX
    return f"#{CURVE_BLUE}"
