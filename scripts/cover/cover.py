#!/usr/bin/env python3
"""Draw a book's cover from its table of contents and the illustration filed beside it.

The cover half of the first stage, frontmatter.py, which calls this and then sets the
pages inside the cover; this runs on its own for working on the cover's look. It reads
nothing any other stage wrote: everything on the cover is on the <book> element of the
table of contents — the subject, the country, the grade, the term, the academic year,
the language — or is the illustration the element names, which lives in the book's
input folder next to the table of contents and its source PDFs. Nothing here calls a
model, so a cover can be drawn again as often as the look of it is being worked on, for
the cost of one render.

The cover is one page: the waves of the house design across the top, the words
"Practice Book" in the book's language and the country's wording under them, then the
subject, the country, the grade and the year, then the illustration, and the imprint
in the band along the foot. The waves and the band take their colours from the
illustration, which carries a palette of four in its own file, so that whatever
picture a book is given the top and the bottom of the page are painted to match it.

Everything is vector. The design, the lettering and the illustration are SVG drawings
laid into one SVG page; the words are set by shaping them with HarfBuzz in the cover
face and writing each glyph out as an outline, so the page depends on no font being
installed wherever it is rendered and every renderer draws the same letters. The page
is then rendered to PDF once by rsvg-convert, which is the one thing beyond Python
this stage needs, as lualatex is the one thing the papers need.

Two more pages come out beside the cover: the same words in black on a white page,
which is the title page for the inside of the book, and the cover again with the
lines it was laid out on drawn over it, for looking at where things sit while the
layout is being worked on — the cover's equivalent of the papers' grid book. The title
page drawn here is the page without its foot: frontmatter.py lays the book's foot over
it, so a title page this was run alone for is not the finished one.

What this depends on lives in the repository: the design and the lettering under
frontmatter/cover/, the wording of grades, terms and type-lettering names in
frontmatter/wording.json, and the cover face, Alyamama, under fonts/. The lettering
is drawn rather than set because it is display type the designers cut by hand, named
after the text it contains and chosen per country from wording.json; a book whose
type lettering is missing is refused here, before anything is drawn.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

try:
    import uharfbuzz as hb

    from src.prompting import Profile
    from sources import INPUT_DIR, PROJECT_ROOT, OUTPUT_DIR, book_metadata
except ModuleNotFoundError as error:  # pragma: no cover
    sys.exit(f"missing dependency ({error.name}): pip install -r requirements.txt")

# The one folder the book's front matter goes into, the cover drawn here and the pages
# frontmatter.py sets alike. Named here rather than there because frontmatter.py
# imports this and not the other way round.
FRONTMATTER_STAGE = "01_frontmatter"

FRONTMATTER_DIR = PROJECT_ROOT / "frontmatter"
DESIGN_PATH = FRONTMATTER_DIR / "cover" / "base.svg"
LETTERING_DIRS = {
    "practice": FRONTMATTER_DIR / "cover" / "practice_book",
    "study": FRONTMATTER_DIR / "cover" / "study_book",
    "exam": FRONTMATTER_DIR / "cover" / "exam_book",
}
SHARED_ILLUSTRATIONS_DIR = FRONTMATTER_DIR / "cover" / "illustrations"
WORDING_PATH = FRONTMATTER_DIR / "wording.json"
FONT_DIR = PROJECT_ROOT / "fonts" / "Alyamama"
FONT_FAMILY = "Alyamama"

RENDERER = "rsvg-convert"

# The page is A4 in points, which is what the design is drawn on: one SVG unit is one
# point, and the PDF is rendered at 72 dots to the inch so that it stays so.
PAGE_WIDTH = 595.276
PAGE_HEIGHT = 841.89
RENDER_DPI = "72"

# The colours the design and the lettering are painted in, named C1 to C4 and declared
# as CSS variables. Each illustration declares its own four, and they are what the
# cover is painted in: the design's own declarations are the designers' sample and are
# overridden by the illustration's. rsvg-convert does not resolve CSS variables, so
# every var(--Cn) is written out as its colour before the page goes to it.
PALETTE = 4
PALETTE_DECLARATION = re.compile(r"--C([1-4])\s*:\s*(#[0-9a-fA-F]{6})")


# The lines of words on the cover, with the baseline each sits on measured from the
# top of the page, the size each is set at, and the weight of the face. These are the
# designers' measurements and are written down as they were approved; a line is
# centred on the page, and every one is black. The title is the one line that may be
# too long for its size — a subject like "Mathematics • Arts Section" — and is set at
# the largest whole-point size not above its own at which it fits between the text
# margins.
@dataclass(frozen=True)
class Line:
    role: str
    baseline: float
    size: float
    weight: str


TITLE = Line("title", 265, 52, "Regular")
COUNTRY = Line("country", 315, 18, "Light")
GRADE = Line("grade", 340, 18, "Light")
YEAR = Line("year", 365, 18, "Light")
IMPRINT = Line("imprint", 825, 18, "Light")
TEXT_MARGIN = 50

# The box the illustration is fitted into: scaled to fit, keeping its proportions,
# centred across the page and standing on the box's lower edge, so that pictures of
# every shape end at the same height above the band.
ILLUSTRATION_MARGIN = 60
ILLUSTRATION_TOP = 400
ILLUSTRATION_BOTTOM = 770

# Where the lettering goes. Every language's lettering spans the same width, the page
# less a margin each side, and is centred; how tall it stands and where its lower edge
# falls differ by language, because Arabic lettering has ascenders and a bowl below
# the line where Latin capitals have neither. The lettering is drawn to fill exactly
# the box these give it, as the approved covers had it: the cut lettering's own
# proportions are close to the box's for Arabic and a little wider than it for the
# Latin languages, so the Latin lettering stands a little taller on the page than it
# was cut, which is how the designers signed it off.
LETTERING_MARGIN = 40
LETTERING_WIDTH = PAGE_WIDTH - 2 * LETTERING_MARGIN
# height, lower edge — by language
LETTERING_BOX = {
    "ar": (70.6, 218.5),
    "de": (65.0, 200.0),
    "en": (51.0, 200.0),
    "es": (52.0, 200.0),
    "fr": (67.5, 200.0),
    "it": (51.0, 200.0),
}

# The marks between and around the years. The academic year is written as its two
# calendar years with an en dash between, and the term or part follows it after a
# bullet; a book with neither — a course that runs the year — has the years alone.
# A part is not an XML attribute: it is the `_pN` suffix on the book id, and it is
# only named when the book has no term.
YEAR_DASH = "\u2013"
YEAR_BULLET = " \u2022 "
PART_SUFFIX = re.compile(r"_p(\d+)$")
ARABIC_DIGITS = str.maketrans("0123456789", "\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669")

# The guides drawn over the cover for working on the layout: the text margins and each
# line's baseline, the illustration's box and the lettering's, each in its own colour
# and named where it is.
GUIDE_TEXT = "#f97316"
GUIDE_ILLUSTRATION = "#0891b2"
GUIDE_LETTERING = "#7c3aed"
GUIDE_LABEL_SIZE = 7


@dataclass(frozen=True)
class Run:
    """A stretch of one line's words that reads one way.

    A line is laid out as runs from left to right across the page, each shaped in its
    own direction. That is the whole of the bidirectional handling the cover needs,
    and it is done by saying the order rather than by working it out: the only line
    that mixes directions is the year, and which way round it goes is a design
    decision written where the line is built.
    """

    text: str
    rtl: bool


@dataclass(frozen=True)
class Words:
    """One line of the cover: the runs it is laid out in, and how it reads."""

    runs: list[Run]
    reading: str


@dataclass(frozen=True)
class Glyph:
    gid: int
    advance: float
    dx: float
    dy: float


@dataclass(frozen=True)
class Placed:
    """Where a drawing was put on the page: its left, top, width and height."""

    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class Cover:
    """What came out: the three PDFs, and what was written on the cover."""

    cover: Path
    title_page: Path
    guides: Path
    lines: dict[str, str]
    palette: list[str]


# --- Words -------------------------------------------------------------------


def reads_rtl(text: str) -> bool:
    """Whether a line of words is read from the right: it has a letter of a script that is."""
    return any(unicodedata.bidirectional(char) in ("R", "AL") for char in text)


def academic_years(value: str) -> tuple[str, str]:
    """The two calendar years an academic year like 25-26 spans, written in full."""
    parts = re.split(r"[-\u2013\u2014/]", value.strip())
    if len(parts) != 2 or not all(part.isdigit() and len(part) in (2, 4) for part in parts):
        raise RuntimeError(
            f"academic_year={value!r} is not two years like 25-26 or 2025-2026"
        )
    first, second = (part if len(part) == 4 else f"20{part}" for part in parts)
    if int(second) != int(first) + 1:
        raise RuntimeError(f"academic_year={value!r} does not span consecutive years")
    return first, second


def read_wording() -> dict:
    """The words the front matter says in each language, as frontmatter/wording.json has them."""
    if not WORDING_PATH.is_file():
        raise RuntimeError(f"{WORDING_PATH.relative_to(PROJECT_ROOT)} is not there")
    return json.loads(WORDING_PATH.read_text(encoding="utf-8"))


def part_from_id(book_id: str) -> str:
    """The part number encoded as a `_pN` suffix on the book id, or empty."""
    found = PART_SUFFIX.search(book_id.strip())
    return found.group(1) if found else ""


def part_label(wording: dict, country_code: str, part: str, lang: str) -> str:
    """Cover words for a part. India English is Roman; everything else is parts/n/lang."""
    if country_code == "in":
        named = wording.get("parts_in")
        by_lang = named.get(part) if isinstance(named, dict) else None
        text = by_lang.get(lang) if isinstance(by_lang, dict) else None
        if isinstance(text, str) and text.strip():
            return text.strip()
    return wording_of(
        wording, ("parts", part, lang), f"name for part {part} in {lang}"
    )


def wording_of(wording: dict, path: tuple[str, ...], what: str) -> str:
    """One string of the wording, or a RuntimeError naming what is missing."""
    node: object = wording
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise RuntimeError(
                f"frontmatter/wording.json has no {what}: no entry at {'/'.join(path)}"
            )
        node = node[key]
    if not isinstance(node, str) or not node.strip():
        raise RuntimeError(f"frontmatter/wording.json has no {what} at {'/'.join(path)}")
    return node


def cover_lines(
    meta: dict[str, str],
    profile: Profile,
    wording: dict,
    book_id: str = "",
) -> dict[str, Words]:
    """The words on the cover, line by line, each as the runs it is laid out in.

    The subject and the country are the table of contents' own words, in the book's
    language; the grade and the term are looked up by number, because a grade is
    named differently from one country to the next and the table of contents gives
    only the number. A part is read from the book id's `_pN` suffix, and only when
    there is no term.

    The year line reads year then term in an English book: "2025–2026 • Term 1". A
    part book with no term reads the same way with the part: "2026–2027 • Part I".
    In an Arabic book it reads the same way in its own direction, from the right:
    the years first, then the bullet, then the term or part, so that wording stands
    at the left of the line and the years at its right. The years themselves are
    read from the right too, 2025 first and 2026 after it, as the approved covers
    had them and as the papers print a range of exercise numbers; a Western-digit
    Arabic book prints its digits as they are and an Arabic-digit one in
    Arabic-Indic digits, as the rest of the book does.
    """
    lang = profile.language_code
    country_code = meta.get("country_code", "").strip().lower()
    title = (meta.get("book_title") or meta.get("subject") or "").strip()
    names = wording.get("country_names")
    by_lang = names.get(country_code) if isinstance(names, dict) else None
    named = by_lang.get(lang) if isinstance(by_lang, dict) else None
    country = (
        named.strip()
        if isinstance(named, str) and named.strip()
        else meta.get("country", "").strip()
    )
    grade = meta.get("grade", "").strip()
    term = meta.get("term", "").strip()
    if term == "0":
        term = ""
    part = "" if term else (meta.get("part", "").strip() or part_from_id(book_id))
    if not title:
        raise RuntimeError(
            "<book> has no book_title or subject attribute, which the cover prints"
        )
    for name, value in (("country", country), ("grade", grade),
                        ("country_code", country_code)):
        if not value:
            raise RuntimeError(f"<book> has no {name} attribute, which the cover prints")

    grade_words = wording_of(
        wording, ("grades", country_code, grade, lang), f"name for grade {grade} in {country_code} in {lang}"
    )
    imprint = wording_of(wording, ("nagwa_stax", lang), f"imprint in {lang}")
    first, second = academic_years(meta.get("academic_year", ""))
    if profile.indic_digits:
        first, second = first.translate(ARABIC_DIGITS), second.translate(ARABIC_DIGITS)

    extra = ""
    if term:
        extra = wording_of(wording, ("terms", term, lang), f"name for term {term} in {lang}")
    elif part:
        extra = part_label(wording, country_code, part, lang)

    rtl = profile.rtl
    years = f"{first}{YEAR_DASH}{second}"
    if extra:
        reading = f"{years}{YEAR_BULLET}{extra}"
        if rtl:
            year_runs = [Run(extra, True), Run(f"{YEAR_BULLET}{second}{YEAR_DASH}{first}", False)]
        else:
            year_runs = [Run(reading, False)]
    else:
        reading = years
        year_runs = [Run(f"{second}{YEAR_DASH}{first}" if rtl else years, False)]

    # A line of one run reads the way its own letters do, not the way the book does:
    # a country the table of contents names in Latin letters on an Arabic book is set
    # left to right, not spelt backwards.
    def one(text: str) -> Words:
        return Words([Run(text, reads_rtl(text))], text)

    return {
        TITLE.role: one(title),
        COUNTRY.role: one(country),
        GRADE.role: one(grade_words),
        YEAR.role: Words(year_runs, reading),
        IMPRINT.role: one(imprint),
    }


# --- Setting the words --------------------------------------------------------


class SvgPen:
    """A pen HarfBuzz draws a glyph's outline into, writing SVG path commands.

    Speaks the pen protocol fontTools defined and uharfbuzz drives: moveTo, lineTo,
    curveTo, qCurveTo, closePath and endPath, in font units with y upwards. The
    group the paths go into flips y, so nothing is flipped here.
    """

    def __init__(self) -> None:
        self.commands: list[str] = []

    @staticmethod
    def _point(point: tuple[float, float]) -> str:
        return f"{point[0]:g} {point[1]:g}"

    def moveTo(self, point: tuple[float, float]) -> None:  # noqa: N802 - pen protocol
        self.commands.append(f"M{self._point(point)}")

    def lineTo(self, point: tuple[float, float]) -> None:  # noqa: N802
        self.commands.append(f"L{self._point(point)}")

    def curveTo(self, *points: tuple[float, float]) -> None:  # noqa: N802
        if len(points) != 3:  # pragma: no cover - HarfBuzz sends cubics as three points
            raise RuntimeError(f"a cubic curve with {len(points)} points")
        self.commands.append("C" + " ".join(self._point(p) for p in points))

    def qCurveTo(self, *points: tuple[float, float]) -> None:  # noqa: N802
        # Any number of off-curve points ending on one on-curve point, with an
        # on-curve point implied midway between neighbouring off-curve ones.
        if points[-1] is None:  # pragma: no cover - closed all-off-curve contour
            raise RuntimeError("a quadratic contour with no on-curve point")
        controls, end = points[:-1], points[-1]
        for index, control in enumerate(controls):
            if index + 1 < len(controls):
                following = controls[index + 1]
                on = ((control[0] + following[0]) / 2, (control[1] + following[1]) / 2)
            else:
                on = end
            self.commands.append(f"Q{self._point(control)} {self._point(on)}")

    def closePath(self) -> None:  # noqa: N802
        self.commands.append("Z")

    def endPath(self) -> None:  # noqa: N802
        pass


class Face:
    """One weight of the cover face, loaded once and asked to shape and to draw."""

    def __init__(self, path: Path) -> None:
        self.path = path
        blob = path.read_bytes()
        self.face = hb.Face(blob)
        self.font = hb.Font(self.face)
        self.upem = self.face.upem
        self._outlines: dict[int, str] = {}

    def shape(self, run: Run) -> list[Glyph]:
        buffer = hb.Buffer()
        buffer.add_str(run.text)
        buffer.guess_segment_properties()
        buffer.direction = "rtl" if run.rtl else "ltr"
        hb.shape(self.font, buffer)
        return [
            Glyph(info.codepoint, pos.x_advance, pos.x_offset, pos.y_offset)
            for info, pos in zip(buffer.glyph_infos, buffer.glyph_positions)
        ]

    def outline(self, gid: int) -> str:
        if gid not in self._outlines:
            pen = SvgPen()
            self.font.draw_glyph_with_pen(gid, pen)
            self._outlines[gid] = "".join(pen.commands)
        return self._outlines[gid]

    def width(self, runs: list[Run], size: float) -> float:
        """How wide the line stands at this size, in points."""
        units = sum(glyph.advance for run in runs for glyph in self.shape(run))
        return units * size / self.upem

    def set(self, runs: list[Run], size: float, baseline: float, centre: float) -> str:
        """The line as outlines, centred on `centre` with its baseline at `baseline`."""
        glyphs = [glyph for run in runs for glyph in self.shape(run)]
        scale = size / self.upem
        total = sum(glyph.advance for glyph in glyphs)
        start = centre - total * scale / 2
        paths = []
        advance = 0.0
        for glyph in glyphs:
            commands = self.outline(glyph.gid)
            if commands:
                paths.append(
                    f'    <path transform="translate({advance + glyph.dx:g},{glyph.dy:g})" '
                    f'd="{commands}"/>'
                )
            advance += glyph.advance
        # The group flips y, because font units run upwards and the page runs down,
        # and scales font units to points.
        return (
            f'  <g transform="translate({start:.4f},{baseline:.4f}) scale({scale:.6f},{-scale:.6f})">\n'
            + "\n".join(paths)
            + "\n  </g>\n"
        )


def font_path(weight: str) -> Path:
    return FONT_DIR / f"{FONT_FAMILY}-{weight}.ttf"


def faces() -> dict[str, Face]:
    """The weights the lines are set in, each loaded once."""
    return {
        weight: Face(font_path(weight))
        for weight in sorted({line.weight for line in (TITLE, COUNTRY, GRADE, YEAR, IMPRINT)})
    }


def title_size(face: Face, runs: list[Run]) -> float:
    """The largest whole-point size, up to the title's own, at which it fits the measure."""
    measure = PAGE_WIDTH - 2 * TEXT_MARGIN
    for size in range(int(TITLE.size), 0, -1):
        if face.width(runs, size) <= measure:
            return float(size)
    return 1.0


def set_lines(
    lines: dict[str, Words], weights: dict[str, Face], which: tuple[Line, ...]
) -> tuple[str, dict[str, float]]:
    """The named lines as outlines, and the size each was set at."""
    pieces = []
    sizes: dict[str, float] = {}
    for line in which:
        face = weights[line.weight]
        runs = lines[line.role].runs
        size = title_size(face, runs) if line is TITLE else line.size
        sizes[line.role] = size
        pieces.append(face.set(runs, size, line.baseline, PAGE_WIDTH / 2))
    return "".join(pieces), sizes


# --- Laying in the drawings -----------------------------------------------------


def svg_parts(text: str, what: str) -> tuple[str, str]:
    """An SVG file as its root tag and what the root tag encloses."""
    opened = re.search(r"<svg\b[^>]*>", text, re.IGNORECASE | re.DOTALL)
    closed = text.rfind("</svg>")
    if not opened or closed == -1:
        raise RuntimeError(f"{what} is not a well-formed SVG file")
    return opened.group(0), text[opened.end() : closed]


def svg_size(root_tag: str, what: str) -> tuple[float, float]:
    """The width and height of an SVG's own coordinate space, from its viewBox."""
    found = re.search(r'\bviewBox\s*=\s*"([^"]*)"', root_tag)
    if not found:
        raise RuntimeError(f"{what} has no viewBox, so its size is not known")
    parts = re.split(r"[\s,]+", found.group(1).strip())
    if len(parts) != 4:
        raise RuntimeError(f"{what} has a viewBox that is not four numbers")
    width, height = float(parts[2]), float(parts[3])
    if width <= 0 or height <= 0:
        raise RuntimeError(f"{what} has a viewBox with no area")
    return width, height


def namespaced(inner: str, prefix: str) -> str:
    """The inside of an SVG with its ids and class names made its own.

    Two drawings laid into one page each brought their own ids — every Illustrator
    export has a Layer_1 and a clippath — and their own .st0 and .st1 classes, and
    whichever came second would have restyled the first. Prefixing them keeps each
    drawing's references pointing into itself.
    """
    inner = re.sub(r'\bid="([^"]+)"', lambda m: f'id="{prefix}{m.group(1)}"', inner)
    inner = re.sub(r"url\(#([^)]+)\)", lambda m: f"url(#{prefix}{m.group(1)})", inner)
    inner = re.sub(
        r'(xlink:href|href)="#([^"]+)"',
        lambda m: f'{m.group(1)}="#{prefix}{m.group(2)}"',
        inner,
    )
    classes = set()
    for found in re.finditer(r'\bclass="([^"]+)"', inner):
        classes.update(found.group(1).split())
    for name in sorted(classes, key=len, reverse=True):
        inner = re.sub(
            rf'\bclass="([^"]*)\b{re.escape(name)}\b([^"]*)"',
            lambda m: f'class="{m.group(1)}{prefix}{name}{m.group(2)}"',
            inner,
        )
        inner = re.sub(rf"\.{re.escape(name)}\b", f".{prefix}{name}", inner)
    return inner


def nested(text: str, what: str, prefix: str, placed: Placed, stretch: bool) -> str:
    """A whole SVG drawing laid into the page as a nested <svg> at `placed`.

    Its viewBox is kept, so it scales as vector to the box it is given; its own width
    and height, if it had any, are dropped in favour of the box's. `stretch` fills the
    box exactly whatever the drawing's proportions, which is what the lettering asks
    for; the illustration keeps its own.
    """
    root_tag, inner = svg_parts(text, what)
    root_tag = re.sub(r'\s(width|height|x|y|id|preserveAspectRatio)="[^"]*"', "", root_tag)
    aspect = ' preserveAspectRatio="none"' if stretch else ""
    root_tag = re.sub(
        r"<svg\b",
        f'<svg x="{placed.x:.4f}" y="{placed.y:.4f}" '
        f'width="{placed.width:.4f}" height="{placed.height:.4f}"{aspect}',
        root_tag,
        count=1,
    )
    return f"  {root_tag}{namespaced(inner, prefix)}</svg>\n"


def lettering_box(profile: Profile) -> Placed:
    if profile.language_code not in LETTERING_BOX:
        raise RuntimeError(
            f"no lettering box is written down for language {profile.language_code!r} "
            f"in {Path(__file__).name}"
        )
    height, bottom = LETTERING_BOX[profile.language_code]
    return Placed(LETTERING_MARGIN, bottom - height, LETTERING_WIDTH, height)


def lettering_kind(package_type: str) -> str:
    kind = (package_type or "practice").strip().lower()
    if kind not in LETTERING_DIRS:
        raise RuntimeError(
            f"package type {package_type!r} is not study, practice, or exam"
        )
    return kind


def lettering_path(
    profile: Profile,
    country_code: str,
    wording: dict,
    package_type: str = "practice",
) -> Path:
    kind = lettering_kind(package_type)
    lang = profile.language_code
    stem = wording_of(
        wording,
        ("types", country_code, kind, lang),
        f"type lettering for a {kind} book in {country_code} in {lang}",
    ).strip()
    if stem.lower().endswith(".svg"):
        stem = stem[:-4]
    if not stem:
        raise RuntimeError(
            f"empty type lettering name for a {kind} book in {country_code!r} in {lang}"
        )
    return LETTERING_DIRS[kind] / f"{stem}.svg"


def lettering(
    profile: Profile,
    country_code: str,
    wording: dict,
    package_type: str = "practice",
) -> str:
    kind = lettering_kind(package_type)
    path = lettering_path(profile, country_code, wording, kind)
    if not path.is_file():
        raise RuntimeError(
            f"no lettering has been cut for a {profile.language} {kind} book in "
            f"{country_code!r}: {path.relative_to(PROJECT_ROOT)} is not there"
        )
    return nested(
        path.read_text(encoding="utf-8"), path.name, "lettering-", lettering_box(profile), stretch=True
    )


def illustration_name(meta: dict[str, str]) -> str:
    name = meta.get("cover_illustration", "").strip()
    if not name:
        raise RuntimeError("<book> has no cover_illustration attribute naming the picture")
    return name


def illustration_path(book_id: str, meta: dict[str, str]) -> Path:
    name = illustration_name(meta)
    path = INPUT_DIR / book_id / f"{name}.svg"
    if not path.is_file():
        raise RuntimeError(
            f"cover_illustration={name!r} but {path.relative_to(PROJECT_ROOT)} is not there"
        )
    return path


def shared_illustration_path(meta: dict[str, str]) -> Path:
    name = illustration_name(meta)
    path = SHARED_ILLUSTRATIONS_DIR / f"{name}.svg"
    if not path.is_file():
        raise RuntimeError(
            f"cover_illustration={name!r} but {path.relative_to(PROJECT_ROOT)} is not there"
        )
    return path


def palette_of(illustration: str, what: str) -> list[str]:
    """The four colours an illustration declares for the cover to be painted in."""
    colours: dict[int, str] = {}
    for found in PALETTE_DECLARATION.finditer(illustration):
        colours.setdefault(int(found.group(1)), found.group(2).lower())
    missing = [f"C{index}" for index in range(1, PALETTE + 1) if index not in colours]
    if missing:
        raise RuntimeError(f"{what} declares no colour for {', '.join(missing)}")
    return [colours[index] for index in range(1, PALETTE + 1)]


def illustration_box(text: str, what: str) -> Placed:
    root_tag, _ = svg_parts(text, what)
    width, height = svg_size(root_tag, what)
    box_width = PAGE_WIDTH - 2 * ILLUSTRATION_MARGIN
    box_height = ILLUSTRATION_BOTTOM - ILLUSTRATION_TOP
    scale = min(box_width / width, box_height / height)
    drawn_width, drawn_height = width * scale, height * scale
    return Placed(
        ILLUSTRATION_MARGIN + (box_width - drawn_width) / 2,
        ILLUSTRATION_BOTTOM - drawn_height,
        drawn_width,
        drawn_height,
    )


def painted(svg: str, palette: list[str]) -> str:
    """The page with every var(--Cn) written out as the illustration's colour."""
    for index, colour in enumerate(palette, start=1):
        svg = svg.replace(f"var(--C{index})", colour)
    return svg


# --- The pages ---------------------------------------------------------------


def guide_label(x: float, y: float, words: str, colour: str) -> str:
    return (
        f'    <text x="{x:.2f}" y="{y:.2f}" fill="{colour}" font-family="Helvetica, Arial, sans-serif" '
        f'font-size="{GUIDE_LABEL_SIZE}" font-weight="700">{words}</text>\n'
    )


def guides(sizes: dict[str, float], illustration: Placed, letters: Placed) -> str:
    """The lines the cover was laid out on, drawn over it and named."""
    left, right = TEXT_MARGIN, PAGE_WIDTH - TEXT_MARGIN
    out = ['  <g id="guides">\n']
    for x, label_x in ((left, left + 3), (right, right - 90)):
        out.append(
            f'    <line x1="{x:.2f}" y1="0" x2="{x:.2f}" y2="{PAGE_HEIGHT:.2f}" '
            f'stroke="{GUIDE_TEXT}" stroke-width="0.5" stroke-dasharray="3 3"/>\n'
        )
        out.append(guide_label(label_x, 24, f"text margin {TEXT_MARGIN:g}pt", GUIDE_TEXT))
    for line in (TITLE, COUNTRY, GRADE, YEAR, IMPRINT):
        out.append(
            f'    <line x1="{left:.2f}" y1="{line.baseline:.2f}" x2="{right:.2f}" '
            f'y2="{line.baseline:.2f}" stroke="{GUIDE_TEXT}" stroke-width="0.5"/>\n'
        )
        out.append(
            guide_label(
                left + 3, line.baseline + 9,
                f"{line.role}: baseline {line.baseline:g}pt, {sizes[line.role]:g}pt {line.weight}",
                GUIDE_TEXT,
            )
        )
    box_left, box_right = ILLUSTRATION_MARGIN, PAGE_WIDTH - ILLUSTRATION_MARGIN
    out.append(
        f'    <rect x="{box_left:.2f}" y="{ILLUSTRATION_TOP:.2f}" width="{box_right - box_left:.2f}" '
        f'height="{ILLUSTRATION_BOTTOM - ILLUSTRATION_TOP:.2f}" fill="none" '
        f'stroke="{GUIDE_ILLUSTRATION}" stroke-width="0.8" stroke-dasharray="5 3"/>\n'
    )
    out.append(
        f'    <rect x="{illustration.x:.2f}" y="{illustration.y:.2f}" width="{illustration.width:.2f}" '
        f'height="{illustration.height:.2f}" fill="none" stroke="{GUIDE_ILLUSTRATION}" stroke-width="0.5"/>\n'
    )
    out.append(
        guide_label(
            box_left + 4, ILLUSTRATION_TOP + 11,
            f"illustration box: {ILLUSTRATION_MARGIN:g}pt in from each side, "
            f"{ILLUSTRATION_TOP:g}-{ILLUSTRATION_BOTTOM:g}pt down; drawn {illustration.width:.0f}x{illustration.height:.0f}pt",
            GUIDE_ILLUSTRATION,
        )
    )
    out.append(
        f'    <rect x="{letters.x:.2f}" y="{letters.y:.2f}" width="{letters.width:.2f}" '
        f'height="{letters.height:.2f}" fill="none" stroke="{GUIDE_LETTERING}" stroke-width="0.8" '
        f'stroke-dasharray="5 3"/>\n'
    )
    out.append(
        guide_label(
            letters.x + 4, letters.y - 4,
            f"lettering: {letters.width:.0f}x{letters.height:g}pt, lower edge {letters.y + letters.height:g}pt",
            GUIDE_LETTERING,
        )
    )
    out.append("  </g>\n")
    return "".join(out)


def cover_svg(
    design: str,
    letters: str,
    words: str,
    picture: str,
    palette: list[str],
    overlay: str = "",
) -> str:
    """The cover: the design with the lettering, the words and the picture laid over it."""
    closed = design.rfind("</svg>")
    if closed == -1:
        raise RuntimeError("frontmatter/cover/base.svg has no closing </svg>")
    return painted(design[:closed] + letters + words + picture + overlay + design[closed:], palette)


def title_page_svg(words: str) -> str:
    """The title page: the cover's words in black on the bare page, and nothing else.

    No white is painted behind them: a PDF page is white already, and a painted one
    would hide the grid the papers' grid book lays under every page.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {PAGE_WIDTH} {PAGE_HEIGHT}">\n'
        f"{words}"
        "</svg>\n"
    )


def render(svg_path: Path) -> Path:
    """One SVG page rendered to the PDF beside it, at a point to the unit."""
    pdf_path = svg_path.with_suffix(".pdf")
    result = subprocess.run(
        [RENDERER, "-f", "pdf", "--dpi-x", RENDER_DPI, "--dpi-y", RENDER_DPI,
         "-o", str(pdf_path), str(svg_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        raise RuntimeError(f"{RENDERER} would not render {svg_path.name}: {detail}")
    return pdf_path


def require_renderer() -> None:
    if shutil.which(RENDERER) is None:
        raise RuntimeError(f"{RENDERER} not found; install librsvg (brew install librsvg)")
    for path in (DESIGN_PATH, WORDING_PATH):
        if not path.is_file():
            raise RuntimeError(f"{path.relative_to(PROJECT_ROOT)} is not there")
    for line in (TITLE, COUNTRY, GRADE, YEAR, IMPRINT):
        if not font_path(line.weight).is_file():
            raise RuntimeError(
                f"the cover face is not at {font_path(line.weight).relative_to(PROJECT_ROOT)}"
            )


def write_cover_pages(
    meta: dict[str, str],
    *,
    package_type: str,
    picture_path: Path,
    stage_dir: Path,
    stem: str,
) -> Cover:
    """Draw the cover pages from metadata and an illustration already on disk."""
    require_renderer()
    profile = Profile.of(meta)
    wording = read_wording()
    country_code = meta.get("country_code", "").strip().lower()
    kind = lettering_kind(package_type)

    lines = cover_lines(meta, profile, wording, stem)
    picture_text = picture_path.read_text(encoding="utf-8")
    palette = palette_of(picture_text, picture_path.name)
    placed = illustration_box(picture_text, picture_path.name)
    picture = nested(picture_text, picture_path.name, "illustration-", placed, stretch=False)
    letters = lettering(profile, country_code, wording, kind)
    design = DESIGN_PATH.read_text(encoding="utf-8")

    weights = faces()
    cover_words, sizes = set_lines(lines, weights, (TITLE, COUNTRY, GRADE, YEAR, IMPRINT))
    title_words, _ = set_lines(lines, weights, (TITLE, COUNTRY, GRADE, YEAR))

    stage_dir.mkdir(parents=True, exist_ok=True)
    pages = {
        "cover": cover_svg(design, letters, cover_words, picture, palette),
        "title_page": title_page_svg(title_words),
        "cover_guides": cover_svg(
            design, letters, cover_words, picture, palette,
            guides(sizes, placed, lettering_box(profile)),
        ),
    }
    outputs: dict[str, Path] = {}
    for name, svg in pages.items():
        svg_path = stage_dir / f"{stem}_{name}.svg"
        svg_path.write_text(svg, encoding="utf-8")
        outputs[name] = render(svg_path)

    written = {role: words.reading for role, words in lines.items()}
    if sizes[TITLE.role] != TITLE.size:
        written[TITLE.role] += f"  (set at {sizes[TITLE.role]:g}pt to fit)"
    return Cover(outputs["cover"], outputs["title_page"], outputs["cover_guides"], written, palette)


def draw_cover(book_id: str) -> Cover:
    """Draw a book's cover, its title page and its guides, and say what was written."""
    meta = book_metadata(book_id)
    return write_cover_pages(
        meta,
        package_type="practice",
        picture_path=illustration_path(book_id, meta),
        stage_dir=OUTPUT_DIR / book_id / FRONTMATTER_STAGE,
        stem=book_id,
    )


def draw_cover_from_meta(
    meta: dict[str, str],
    *,
    package_type: str,
    stage_dir: Path,
    stem: str,
) -> Cover:
    """Draw a cover from catalog metadata and the shared illustration library."""
    return write_cover_pages(
        meta,
        package_type=package_type,
        picture_path=shared_illustration_path(meta),
        stage_dir=stage_dir,
        stem=stem,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_id", help="Book ID, e.g. eg_mathematics_en_g7_t1")
    args = parser.parse_args()

    try:
        cover = draw_cover(args.book_id)
    except RuntimeError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1

    print(f"OK: cover drawn as {cover.cover.relative_to(PROJECT_ROOT)}")
    print(f"    title page as {cover.title_page.relative_to(PROJECT_ROOT)}")
    print(f"    the cover on its guides as {cover.guides.relative_to(PROJECT_ROOT)}")
    print(f"    painted in {', '.join(cover.palette)}, and reading:")
    for role, words in cover.lines.items():
        print(f"      {role:<8} {words}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
