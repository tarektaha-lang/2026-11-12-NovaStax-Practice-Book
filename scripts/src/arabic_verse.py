"""Arabic verse as logical shatrs, kashida, and CLS arabverse.

JSON stores one shatr per string and one printed row per ``PoemRow``. Papers
stretch the shatrs (except شعر حر) and emit ``\\begin{arabverse}{traditional}``.
The model never writes tatweel, ``::``, or the environment itself.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no fail - papers still set unstretched verse
    Image = ImageDraw = ImageFont = None  # type: ignore[misc, assignment]


TATWEEL = "\u0640"
HEMISTICH_MARK = "::"
ARABVERSE_BEGIN = r"\begin{arabverse}"
ARABVERSE_END = r"\end{arabverse}"

POEM_INDEX = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
POEM_TOKEN = re.compile(r"\[poem ([0-9٠-٩]+)\]")
POEM_MARKER = re.compile(r"[ \t]*\[poem ([0-9٠-٩]+)\][ \t]*")

LATEX_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

# Ligatures that must not be split by tatweel. Longer keys first, as in the
# Standalone_Json_Tex_Tool kashida engine this copies.
KASHIDA_EXCEPTIONS = {
    "بالله": "___${1}__/_",
    "فالله": "___${2}__/_",
    "تالله": "___${3}__/_",
    "والله": "___${4}__/_",
    "آلله": "___${5}__/_",
    "ألله": "___${6}__/_",
    "ولله": "___${7}__/_",
    "فلله": "___${8}__/_",
    "فبالله": "___${9}__/_",
    "وبالله": "___${10}__/_",
    "وتالله": "___${11}__/_",
    "فتالله": "___${12}__/_",
    "الله": "___${13}__/_",
    "لله": "___${14}__/_",
    "لأ": "___${15}__/_",
    "لا": "___${16}__/_",
    "لإ": "___${17}__/_",
    "لآ": "___${18}__/_",
}
KASHIDA_SLOT_RE = re.compile(
    r"(?=([جحخهعغفقثصضطكمنتلبيسشىئ]{1}[\u064b-\u0652]*[\u0622-\u064a]{1}))"
)
KASHIDA_DIACRITICS_RE = re.compile(r"[\u064b-\u0652]")

FONT_DIR = Path(__file__).resolve().parent.parent.parent / "fonts"
KASHIDA_FONT_CANDIDATES = (
    FONT_DIR / "Noto Naskh Arabic" / "NotoNaskhArabic-Regular.ttf",
    FONT_DIR / "Noto-Regular.ttf",
)

_KASHIDA_CACHE: dict[tuple[str, ...], list[str]] = {}
_KASHIDA_FONT_CACHE: dict[tuple[str, int], object] = {}
_KASHIDA_DRAW = None


class PoemRow(BaseModel):
    """One printed table row: a single shatr, or صدر then عجز."""

    shatrs: list[str] = Field(
        min_length=1,
        max_length=2,
        description="One shatr, or sadr then ajuz; never :: and never tatweel",
    )

    @field_validator("shatrs")
    @classmethod
    def shatrs_are_plain_verse(cls, value: list[str]) -> list[str]:
        cleaned = [part.strip() for part in value]
        if not all(cleaned):
            raise ValueError("a shatr cannot be empty")
        for part in cleaned:
            problem = forbidden_verse(part)
            if problem:
                raise ValueError(problem)
        return cleaned


class Poem(BaseModel):
    """A reprinted verse block, placed with ``[poem N]`` like a figure."""

    kashida: bool = Field(
        default=True,
        description="False only for free verse (شعر حر); omit otherwise",
    )
    rows: list[PoemRow] = Field(min_length=1)


def forbidden_verse(text: str) -> str:
    """Why this string cannot be stored as a shatr, or empty when it can."""
    if HEMISTICH_MARK in text:
        return "a shatr cannot contain ::"
    if TATWEEL in text:
        return "a shatr cannot contain tatweel"
    if ARABVERSE_BEGIN in text or ARABVERSE_END in text:
        return "a shatr cannot contain arabverse"
    return ""


def poem_linear_text(rows: list[PoemRow] | list[dict[str, Any]]) -> str:
    """Newline-joined shatrs for excerpt.text; a bayt is one space-joined row."""
    lines: list[str] = []
    for row in rows:
        shatrs = row.shatrs if isinstance(row, PoemRow) else list(row.get("shatrs") or [])
        line = " ".join(str(part).strip() for part in shatrs if str(part).strip())
        if line:
            lines.append(line)
    return "\n".join(lines)


def poem_number(digits: str) -> int:
    """The 1-based index in a ``[poem N]`` token, Western or Arabic-Indic."""
    return int(digits.translate(POEM_INDEX))


def marked_poem_indices(text: str) -> set[int]:
    return {poem_number(found.group(1)) for found in POEM_TOKEN.finditer(text)}


def asked_poems(row: dict[str, Any]) -> list[dict[str, Any]]:
    """The verse blocks a group or exercise carries, as dicts."""
    poems = row.get("poems") or []
    return [poem if isinstance(poem, dict) else poem.model_dump() for poem in poems]


def poem_shatrs(row: dict[str, Any] | Poem) -> list[str]:
    """Every shatr in a poem or a group/exercise that carries poems."""
    if isinstance(row, Poem):
        return [shatr for line in row.rows for shatr in line.shatrs]
    texts: list[str] = []
    if "rows" in row and "shatrs" not in row:
        for line in row.get("rows") or []:
            texts.extend(str(part) for part in (line.get("shatrs") or []))
        if texts:
            return texts
    for poem in asked_poems(row):
        for line in poem.get("rows") or []:
            texts.extend(str(part) for part in (line.get("shatrs") or []))
    return texts


def escape_poem_text(text: str) -> str:
    return "".join(LATEX_SPECIALS.get(char, char) for char in text)


def _replace_exceptions(text: str, mapping: dict[str, str]) -> str:
    out = text
    for key, placeholder in mapping.items():
        out = out.replace(key, placeholder)
    return out


def _strip_diacritics(text: str) -> str:
    return KASHIDA_DIACRITICS_RE.sub("", text)


def _kashida_font_path() -> Path | None:
    for path in KASHIDA_FONT_CANDIDATES:
        if path.is_file():
            return path
    naskh_dir = FONT_DIR / "Noto Naskh Arabic"
    if naskh_dir.is_dir():
        for path in sorted(naskh_dir.glob("*Regular*.ttf")):
            if path.is_file():
                return path
    return None


def _measure_draw():
    global _KASHIDA_DRAW
    if _KASHIDA_DRAW is None and Image is not None and ImageDraw is not None:
        _KASHIDA_DRAW = ImageDraw.Draw(Image.new("RGB", (4000, 400), "white"))
    return _KASHIDA_DRAW


def _poem_font(font_size: int = 16):
    if ImageFont is None:
        return None
    font_path = _kashida_font_path()
    if font_path is None:
        return None
    key = (str(font_path), font_size)
    if key not in _KASHIDA_FONT_CACHE:
        try:
            _KASHIDA_FONT_CACHE[key] = ImageFont.truetype(str(font_path), font_size)
        except OSError:
            return None
    return _KASHIDA_FONT_CACHE.get(key)


def text_width(text: str, font_size: int = 16) -> float | None:
    draw = _measure_draw()
    font = _poem_font(font_size)
    if draw is None or font is None:
        return None
    try:
        return float(draw.textlength(text, font=font))
    except Exception:
        return None


def insert_kashidas(text: str, total_kashidas: int) -> str:
    """Spread ``total_kashidas`` tatweels across the allowed slots of ``text``."""
    if total_kashidas <= 0 or not text:
        return text
    working = _replace_exceptions(text, KASHIDA_EXCEPTIONS)
    matches = [
        found
        for found in KASHIDA_SLOT_RE.finditer(working)
        if _strip_diacritics(found.group(1)) not in KASHIDA_EXCEPTIONS
    ]
    if not matches:
        return text
    shift = 0
    total_matches = len(matches)
    for index, found in enumerate(matches):
        add_count = total_kashidas // total_matches + (
            1 if index + 1 <= total_kashidas % total_matches else 0
        )
        if add_count <= 0:
            continue
        full = found.group(1)
        start = found.start(1) + shift
        end = start + len(full)
        replacement = full[:-1] + (TATWEEL * add_count) + full[-1]
        working = working[:start] + replacement + working[end:]
        shift += add_count
    return _replace_exceptions(working, {value: key for key, value in KASHIDA_EXCEPTIONS.items()})


def apply_kashida_to_lines(lines: list[str]) -> list[str]:
    """Stretch each line toward the longest, or return them unchanged if unmeasurable."""
    cleaned = [str(line or "").strip() for line in lines]
    if not cleaned:
        return cleaned
    cache_key = tuple(cleaned)
    cached = _KASHIDA_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)
    if _poem_font(16) is None or _measure_draw() is None:
        return cleaned
    try:
        widths = [text_width(line, 16) for line in cleaned]
        if any(width is None for width in widths):
            return cleaned
        max_width = max(float(width) for width in widths if width is not None)
        kashida_width = text_width(TATWEEL, 16)
        if not kashida_width or kashida_width <= 0:
            return cleaned
        fixed = []
        for line, width in zip(cleaned, widths):
            needed = int(round((max_width - float(width or 0)) / kashida_width))
            fixed.append(insert_kashidas(line, needed))
        _KASHIDA_CACHE[cache_key] = fixed
        return fixed
    except Exception:
        return cleaned


def stretch_rows(poem: Poem) -> list[list[str]]:
    """Shatr lists with kashida applied across the block (or unchanged if free)."""
    rows = [list(row.shatrs) for row in poem.rows]
    if not poem.kashida:
        return rows
    lines = [shatr for row in rows for shatr in row]
    stretched = apply_kashida_to_lines(lines)
    cursor = 0
    out: list[list[str]] = []
    for row in rows:
        count = len(row)
        out.append(stretched[cursor : cursor + count])
        cursor += count
    return out


def poem_from_value(value: Poem | dict[str, Any]) -> Poem:
    return value if isinstance(value, Poem) else Poem.model_validate(value)


def format_arabverse(poem: Poem | dict[str, Any]) -> str:
    """Kashida (unless free verse) and the CLS traditional arabverse body."""
    block = poem_from_value(poem)
    rows = stretch_rows(block)
    if not rows:
        return ""
    paired = any(len(row) == 2 for row in rows)
    formatted: list[str] = []
    for row in rows:
        cells = [escape_poem_text(shatr) for shatr in row]
        if len(cells) == 2:
            formatted.append(f"{cells[0]}\n&\n{cells[1]}")
        elif paired:
            formatted.append(rf"\multicolumn{{2}}{{c}}{{{cells[0]}}}")
        else:
            formatted.append(cells[0])
    body = "\n\\\\\n".join(formatted)
    return f"\\begin{{arabverse}}{{traditional}}\n{body}\n\\end{{arabverse}}"


def replace_poem_marker(text: str, index: int, replacement: str) -> str:
    return POEM_TOKEN.sub(
        lambda found: replacement if poem_number(found.group(1)) == index else found.group(0),
        text,
    )


def poem_problems(
    text: str,
    poems: list[Poem] | list[dict[str, Any]],
    where: str,
    *,
    options: list[str] | None = None,
) -> list[str]:
    """Reasons a field's ``[poem N]`` markers or verse objects cannot be used."""
    problems: list[str] = []
    blocks = [
        poem if isinstance(poem, Poem) else Poem.model_validate(poem) for poem in poems
    ]
    named = marked_poem_indices(text)
    for index in sorted(named):
        if index < 1 or index > len(blocks):
            problems.append(
                f"{where} names [poem {index}], which is not among its {len(blocks)} poem(s)"
            )
    for option_index, option in enumerate(options or [], start=1):
        if marked_poem_indices(option):
            problems.append(
                f"{where} choice {option_index} names a poem; a verse block is never a choice"
            )
    return problems
