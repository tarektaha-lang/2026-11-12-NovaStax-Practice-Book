"""Legacy translation checks for replaying pre-2026-10-21 logs. Never import from the live translate path.

Fail-closed validation and deterministic merging for translated book data.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from new_questions import (
    balanced_braces,
    chosen_letter,
    field_problems,
    table_problems,
    unescaped_dollars,
)
from src.geometry_arrows import (
    canonicalize_geometry_arrows,
    canonicalize_tree,
    section_is_vector_lesson,
)
from src.print_digits import (
    TIKZ_NODE,
    TIKZ_NODE_STATEMENT,
    bare_print_digits,
    normalize_print_digits,
)
from src.translation_units import (
    canonicalize_units,
    normalize_translation_units,
)


BIDI_TABLE = dict.fromkeys(
    [0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)],
    None,
)
MATH_SEGMENT = re.compile(r"\$\$.+?\$\$|\$.+?\$", re.DOTALL)
LOST_ESCAPE_LETTERS = {"\t": "t", "\f": "f", "\b": "b", "\r": "r"}
LOST_COMMAND_ESCAPE = re.compile(r"([\t\f\b\r])(?=[A-Za-z])")
LOST_NEWLINE_COMMAND = re.compile(
    r"\n(?=(?:"
    r"eq|abla|eg|ot|otin|mid|parallel|subseteq|leq|geq|cong|sim"
    r"|e(?![A-Za-z.])"
    r"|i(?![A-Za-z.])(?!\s+[a-z])"
    r"|u(?![A-Za-z.])(?!\s+[a-z])"
    r")(?![A-Za-z]))"
)
CONTROL_CHARACTER = re.compile(r"[\x00-\x09\x0b\x0c\x0e-\x1f\x7f]")
VECTOR_COMMAND = re.compile(r"\\(?:vec|overrightarrow)\b")
RADIAN_MARK = re.compile(
    r"\^\s*\{\s*\\(?:text|mathrm)\s*\{\s*(?:rad|د)\s*\}\s*\}"
    r"|\^\s*\\(?:text|mathrm)\s*\{\s*(?:rad|د)\s*\}"
    r"|\\(?:text|mathrm)\s*\{\s*rad\s*\}"
    r"|\^\s*\{\s*د\s*\}"
    r"|\^\s*د(?![\u0621-\u064A])"
)
NAMED_ANGLE = re.compile(
    r"(?P<word>\b[Aa]ngles?|(?<![\u0621-\u064A])(?:لل|بال|وال|ال)?زاوية)"
    r"\s*\$\s*(?P<name>[A-Z]{1,3}|[A-Z]_\{?\w+\}?"
    r"|[\u0621-\u064A]ـ?(?:\s?[\u0621-\u064A]ـ?){0,2})\s*\$"
)
PREFIX_UNIT = re.compile(
    r"(\\text\{[A-Za-z/]+\})((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
)
TEXT_IN_MATH = re.compile(r"\\(?:text|mathrm|operatorname)\{([^{}]*)\}")
PROSE_FONT_COMMAND = re.compile(r"\\(?:textbf|textit|emph)\{([^{}]*)\}")
# Prose commands that may sit outside $...$ without being naked maths.
PROSE_NAKED_COMMANDS = frozenset({"blank", "textbf", "textit", "emph"})
ORDINAL_SUFFIX = frozenset({"st", "nd", "rd", "th"})
ORDINAL_SUPERSCRIPT = re.compile(
    r"\^\s*\{?\s*\\(?:text|mathrm|operatorname)\s*\{\s*(?:st|nd|rd|th)\s*\}\s*\}?",
    re.I,
)
_ORDINAL_SUP = (
    r"(?:\^\s*\{?\s*\\(?:text|mathrm|operatorname)\s*\{\s*(?:st|nd|rd|th)\s*\}\s*\}?"
    r"|\^\s*\{?\s*(?:st|nd|rd|th)\s*\}?)"
)
QUADRANT_NUMBER_ISLAND = re.compile(
    r"\$\s*[1-4]\s*" + _ORDINAL_SUP + r"?\s*\$", re.I
)
QUADRANT_WORD = re.compile(r"\bquadrants?\b|الربع|ربع", re.I)
_QUADRANT_NUMBER_WORDS = {
    "1": "1", "2": "2", "3": "3", "4": "4",
    "first": "1", "second": "2", "third": "3", "fourth": "4",
    "i": "1", "ii": "2", "iii": "3", "iv": "4",
    "الأول": "1", "الاول": "1", "الثاني": "2", "الثانى": "2",
    "الثالث": "3", "الرابع": "4",
}
_QUADRANT_FILLER = frozenset({"quadrant", "quadrants", "the", "الربع", "ربع", "st", "nd", "rd", "th"})
_QUADRANT_JOIN = r"(?:,|،|\band\b|\bor\b|و|أو)"
QUADRANT_BEFORE = re.compile(
    r"(?:\bquadrants?|الربع(?:ين|ان)?)\s*"
    r"(?:" + QUADRANT_NUMBER_ISLAND.pattern + r"\s*" + _QUADRANT_JOIN + r"\s*)*$",
    re.I,
)
QUADRANT_AFTER = re.compile(
    r"^\s*(?:" + _QUADRANT_JOIN + r"\s*" + QUADRANT_NUMBER_ISLAND.pattern + r"\s*)*"
    r"(?:\\text\{\s*)?(?:quadrants?\b|الربع)",
    re.I,
)
TEXT_BODY_DIGIT = re.compile(r"\d+(?:\.\d+)?")
TEXT_BODY_IDENTIFIER = re.compile(
    r"(?<![A-Za-z])[A-Z](?:_[A-Za-z0-9]+)?(?![A-Za-z])"
)
FIGURE_MARKER = re.compile(r"\[figure ([0-9٠-٩]+)\]")
POEM_MARKER = re.compile(r"\[poem ([0-9٠-٩]+)\]")
LOCALIZED_FIGURE_MARKER = re.compile(r"\[(?:الشكل|شكل)\s+[0-9٠-٩]+\]")
LOCALIZED_POEM_MARKER = re.compile(r"\[(?:القصيدة|قصيدة)\s+[0-9٠-٩]+\]")
ASCII_WORD = re.compile(r"[A-Za-z]{2,}")
LATEX_COMMAND = re.compile(r"\\[A-Za-z]+")
ENVIRONMENT = re.compile(r"\\(begin|end)\{([^{}]+)\}")
FOREIGN_PRINT = re.compile(
    r"[\u0900-\u0DFF\u0E00-\u0E7F\u1100-\u11FF\u3040-\u30FF"
    r"\u3400-\u4DBF\u4E00-\u9FFF\uAC00-\uD7AF\uF900-\uFAFF\uFF00-\uFFEF]"
)
MODEL_CHATTER = re.compile(
    r"Brevity check:|standard math notation staying untouched|"
    r"accurately translated|critical thinking to enable better analysis",
    re.I,
)
ARABIC_LETTER = re.compile(r"[\u0621-\u064A\u066E-\u06D3]")
TASHKEEL = frozenset(chr(code) for code in [*range(0x064B, 0x0660), 0x0670])

# These are symbols/units, not prose. They are allowed only after commands and
# mathematical segments have been removed from the target-language check.
ASCII_ALLOWLIST = frozenset(
    {
        "am",
        "pm",
        "cm",
        "dm",
        "mm",
        "km",
        "kg",
        "mg",
        "ml",
        "hz",
        "khz",
        "mhz",
        "kwh",
        "gb",
        "mb",
        "kb",
        "dna",
        "egp",
        "ft",
        "in",
        "yd",
        "mi",
        "le",
        "grad",
    }
)
CALCULATOR_KEYS = frozenset({"SHIFT", "MODE", "ALPHA", "ANS", "AC", "DEL"})
LATIN_MATH_UNIT = re.compile(
    r"(?i)^(?:mm|cm|dm|km|m|mg|kg|g|ml|l|hz|khz|mhz|kwh|km/h|m/s|"
    r"ft|in|yd|mi|egp|le)$"
)
LATIN_UNIT_ALIASES = {
    "meter": "m",
    "meters": "m",
    "metre": "m",
    "metres": "m",
    "centimeter": "cm",
    "centimeters": "cm",
    "centimetre": "cm",
    "centimetres": "cm",
    "millimeter": "mm",
    "millimeters": "mm",
    "kilometer": "km",
    "kilometers": "km",
    "kilometre": "km",
    "kilometres": "km",
    "kilogram": "kg",
    "kilograms": "kg",
    "gram": "g",
    "grams": "g",
    "liter": "l",
    "liters": "l",
    "litre": "l",
    "litres": "l",
    "foot": "ft",
    "feet": "ft",
    "inch": "in",
    "inches": "in",
    "yard": "yd",
    "yards": "yd",
    "mile": "mi",
    "miles": "mi",
    "degree": "degree",
    "degrees": "degree",
}
LATIN_UNIT_PHRASES = {
    "square meter": ("m", "2"),
    "square meters": ("m", "2"),
    "square metre": ("m", "2"),
    "square metres": ("m", "2"),
    "cubic meter": ("m", "3"),
    "cubic meters": ("m", "3"),
    "cubic metre": ("m", "3"),
    "cubic metres": ("m", "3"),
    "square foot": ("ft", "2"),
    "square feet": ("ft", "2"),
    "cubic foot": ("ft", "3"),
    "cubic feet": ("ft", "3"),
    "sq m": ("m", "2"),
    "sq. m": ("m", "2"),
    "sq cm": ("cm", "2"),
    "sq. cm": ("cm", "2"),
    "sq ft": ("ft", "2"),
    "sq. ft": ("ft", "2"),
}
PROSE_QUANTITY = re.compile(
    r"(?P<sign>[+\-−]?)\s*(?P<number>\d+(?:\.\d+)?)\s*"
    r"(?:"
    r"(?:[A-Za-z\u0621-\u064A\u066E-\u06D3]+\s+)?"
    r"(?:\\text\{\s*)?"
    r"(?P<degree>degrees?|°|\^?\\circ|درجات|درجتان|درجتين|درجة)"
    r"|"
    r"(?:\\text\{\s*)?"
    r"(?P<unit>km/h|m/s|kWh|mm|cm|dm|km|mg|kg|ml|Hz|kHz|MHz|"
    r"ft|yd|mi|L|سم|مم|كم|متر|كجم|جم|مل|لتر)"
    r")"
    r"(?![A-Za-z\u0621-\u064A\u066E-\u06D3])"
    r"(?:\s*\})?\s*(?P<power>\^\{?[23]\}?|[²³])?",
    re.I,
)
UNIT_CANONICAL = {
    "سم": "cm",
    "مم": "mm",
    "كم": "km",
    "متر": "m",
    "كجم": "kg",
    "جم": "g",
    "مل": "ml",
    "لتر": "l",
    "درجة": "degree",
    "درجات": "degree",
    "درجتان": "degree",
    "درجتين": "degree",
    "degree": "degree",
    "degrees": "degree",
    "°": "degree",
    r"\circ": "degree",
    r"^\circ": "degree",
}
SUPERSCRIPT_DIGITS = str.maketrans({"²": "2", "³": "3"})
CHOICE_MAP = dict(zip("ABCDEF", "أبجدهو"))
TIKZ_NUMBER = re.compile(r"(?<![A-Za-z])[-+]?(?:\d+(?:\.\d+)?|\.\d+)")
TIKZ_COMMANDS = ("draw", "node", "coordinate", "path", "addplot", "fill", "filldraw")
TIKZ_LABEL_SHIFT = re.compile(
    r"\b(?:[xy]shift|above|below|left|right)\s*=\s*"
    r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:pt|mm|cm|em|ex)\b"
)
TIKZ_LENGTHMARK_OFFSET = re.compile(
    r"(\\lengthmark\s*)\[\s*"
    r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:pt|mm|cm|em|ex)\s*\]"
)
TIKZ_LENGTHMARK_OPTIONS = re.compile(
    r"(\\lengthmark\s*)(?:\[[^\]]*\]){1,3}"
)
TIKZ_LENGTHMARK_HEAD = re.compile(r"\\lengthmark\s*(\[[^\]]*\])?")
DEFAULT_LENGTHMARK_GAP_CM = 0.22
LATIN_PROMOTION_IDENTIFIER = re.compile(
    r"^(?:\\(?:mathrm|mathit|mathbf)\{([A-Za-z](?:_[A-Za-z0-9]+)?)\}|"
    r"([A-Za-z](?:_[A-Za-z0-9]+)?))$"
)
LATIN_PROMOTION_NUMBER = re.compile(
    r"^[+\-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
    r"(?:\\(?:text|mathrm|operatorname)\{[^{}]*\})?"
    r"(?:\^[0-9]+)?$"
)
_NUMBER_WORDS = {
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "thirteen": "13",
    "fourteen": "14",
    "fifteen": "15",
    "sixteen": "16",
    "seventeen": "17",
    "eighteen": "18",
    "nineteen": "19",
    "twenty": "20",
    "thirty": "30",
    "forty": "40",
    "fifty": "50",
    "sixty": "60",
    "seventy": "70",
    "eighty": "80",
    "ninety": "90",
    "واحد": "1",
    "واحدة": "1",
    "اثنان": "2",
    "اثنين": "2",
    "اثنتان": "2",
    "اثنتين": "2",
    "ثلاثة": "3",
    "ثلاث": "3",
    "أربعة": "4",
    "أربع": "4",
    "خمسة": "5",
    "خمس": "5",
    "ستة": "6",
    "ست": "6",
    "سبعة": "7",
    "سبع": "7",
    "ثمانية": "8",
    "ثماني": "8",
    "ثمان": "8",
    "تسعة": "9",
    "تسع": "9",
    "عشرة": "10",
    "عشر": "10",
    "عشرون": "20",
    "عشرين": "20",
    "ثلاثون": "30",
    "ثلاثين": "30",
    "أربعون": "40",
    "أربعين": "40",
    "خمسون": "50",
    "خمسين": "50",
    "ستون": "60",
    "ستين": "60",
    "سبعون": "70",
    "سبعين": "70",
    "ثمانون": "80",
    "ثمانين": "80",
    "تسعون": "90",
    "تسعين": "90",
}
TRAILING_MATH_PUNCT = re.compile(r"([:.,;،؛!?]+)[ \t]*$")
REQUIRED_GROUP_FIELDS = ("id", "stem", "figures", "exercises")
REQUIRED_EXERCISE_FIELDS = ("id", "text", "options", "answer", "figures")
REQUIRED_FIGURE_FIELDS = ("description",)
SPEC_LIST_FIELDS = (
    "objectives",
    "prerequisites",
    "out_of_scope",
    "glossary",
    "teaching_points",
    "excerpts",
    "equations",
    "notations",
)
STRUCTURAL_SPEC_KEYS = {"id", "role", "reuse", "type"}


INTERVAL_NOTATION = re.compile(
    r"^[\(\[\]][^\[\](),]+,[^\[\](),]+[\)\[\]]$"
)


def symbolic_notation(value: str) -> bool:
    stripped = value.strip()
    return bool(
        stripped
        and not MATH_SEGMENT.search(stripped)
        and (
            "\\" in stripped
            or re.fullmatch(r"[A-Za-z]+", stripped)
            or re.search(r"[=<>+\-*/^]", stripped)
            or re.search(r"[A-Za-z]\s*:\s*[A-Za-z]", stripped)
            or INTERVAL_NOTATION.fullmatch(stripped)
        )
    )


def repair_json_escapes(value: str) -> str:
    """Restore LaTeX commands a single-backslash JSON escape turned into controls.

    ``"\\text"`` written with one backslash decodes as TAB + ``ext``; likewise
    ``\\frac`` (form feed), ``\\begin``/``\\blank`` (backspace) and ``\\right``
    (carriage return). Those controls never belong in a printed field. A
    newline is only a lost ``\\n`` when the letters after it spell a command.
    """
    return LOST_NEWLINE_COMMAND.sub(r"\\n", repair_control_escapes(value))


def repair_control_escapes(value: str) -> str:
    """Only the TAB/FF/BS/CR half of :func:`repair_json_escapes`.

    Safe on text already stored: none of those controls is ever content.
    """
    return LOST_COMMAND_ESCAPE.sub(
        lambda found: "\\" + LOST_ESCAPE_LETTERS[found.group(1)], value
    )


def repair_stored_controls(document: Any) -> tuple[Any, int]:
    """``document`` with every lost-escape control repaired, and how many fields."""
    if isinstance(document, str):
        fixed = repair_control_escapes(document)
        return fixed, int(fixed != document)
    if isinstance(document, dict):
        total = 0
        out = {}
        for key, value in document.items():
            out[key], count = repair_stored_controls(value)
            total += count
        return out, total
    if isinstance(document, list):
        total = 0
        rows = []
        for value in document:
            fixed, count = repair_stored_controls(value)
            rows.append(fixed)
            total += count
        return rows, total
    return document, 0


def normalize_text(value: str) -> str:
    """Remove unsupported direction controls without changing semantic content."""
    value = repair_json_escapes(value)
    value = value.translate(BIDI_TABLE)
    value = re.sub(r"(?i)(\+\s*)hold\b", r"\1", value)
    if value.count("$") % 2 and value.rstrip().endswith("$"):
        final = value.rfind("$")
        previous = value.rfind("$", 0, final)
        tail = value[previous + 1 : final]
        command = re.search(r"\\(?:left|frac|sqrt|overline|begin)\b", tail)
        if command and ARABIC_LETTER.search(tail[: command.start()]):
            insert_at = previous + 1 + command.start()
            value = value[:insert_at] + "$" + value[insert_at:]
    return value.strip()


def _first_naked_command(text: str) -> re.Match[str] | None:
    """The first math-mode LaTeX command that sits outside ``$...$``, if any.

    ``\\blank`` and prose font commands are valid in running text; wrapping
    them would invent a mathematics island the English field never had.
    """
    maths = [found.span() for found in MATH_SEGMENT.finditer(text)]
    for found in LATEX_COMMAND.finditer(text):
        if any(start <= found.start() < stop for start, stop in maths):
            continue
        if found.group(0)[1:] in PROSE_NAKED_COMMANDS:
            continue
        return found
    return None


def _span_has_bare_arabic(span: str) -> bool:
    stripped = TEXT_IN_MATH.sub(" ", span)
    stripped = LATEX_COMMAND.sub(" ", stripped)
    return bool(ARABIC_LETTER.search(stripped))


def _close_odd_math(source: str, target: str) -> str:
    """Insert a missing closer before trailing punctuation when source is balanced."""
    if unescaped_dollars(source) % 2 or unescaped_dollars(target) % 2 == 0:
        return target
    found = TRAILING_MATH_PUNCT.search(target)
    if found:
        return target[: found.start()] + "$" + found.group(0)
    return target + "$"


def _source_has_number_word(source: str, digit: str) -> bool:
    """True when ``source`` spells ``digit`` as an English or Arabic number word."""
    for word, value in _NUMBER_WORDS.items():
        if value != digit:
            continue
        if ARABIC_LETTER.search(word):
            pattern = (
                rf"(?<![\u0621-\u064A]){re.escape(word)}(?![\u0621-\u064A])"
            )
            if re.search(pattern, source):
                return True
        elif re.search(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", source, re.I):
            return True
    return False


def normalize_translation(source: str, target: str) -> str:
    """Apply safe repairs that are determined by the source field shape."""
    target = _close_odd_math(source, normalize_text(target))
    if (
        MATH_SEGMENT.fullmatch(source.strip())
        and not MATH_SEGMENT.search(target)
        and (LATEX_COMMAND.search(target) or symbolic_notation(target))
    ):
        return f"${target.strip()}$"
    source_command = _first_naked_command(source)
    target_command = _first_naked_command(target)
    if source_command is not None and target_command is not None:
        span = target[target_command.start() :]
        if not _span_has_bare_arabic(span):
            body = span.rstrip()
            target = (
                f"{target[: target_command.start()]}${body}${span[len(body) :]}"
            )
    return target


def normalize_arabic_notation(value: str) -> str:
    """Canonicalize common Latin notation that models leave in Arabic maths."""
    functions = {
        "sin": "جا",
        "cos": "جتا",
        "tan": "ظا",
        "cot": "ظتا",
        "sec": "قا",
        "csc": "قتا",
    }
    variables = {
        "x": "س",
        "y": "ص",
        "a": "أ",
        "b": "ب",
        "c": "ج",
        "d": "د",
        "k": "ك",
        "n": "ن",
        "i": "ت",
    }

    def normalize_math(found: re.Match[str]) -> str:
        math = found.group(0)
        math = re.sub(
            r"\\text\{m\}(?=\s*\(\s*\\angle)",
            r"\\text{ق}",
            math,
        )
        math = re.sub(r"(?<![A-Za-z\\])m(?=\s*\\angle)", "ق", math)
        math = re.sub(
            r"\\(sin|cos|tan|cot|sec|csc)\b",
            lambda command: rf"\text{{{functions[command.group(1)]} }}",
            math,
        )
        math = re.sub(
            r"(?<![A-Za-z\\])([xyabckndi])(?![A-Za-z])",
            lambda variable: variables[variable.group(1)],
            math,
        )
        math = re.sub(
            r"(\\begin\{array\}\{)([^{}]*)(\})",
            lambda array: array.group(1)
            + array.group(2).replace("ج", "c")
            + array.group(3),
            math,
        )
        return math

    return MATH_SEGMENT.sub(normalize_math, value)


def legacy_normalize_profile_translation(
    source: str,
    target: str,
    meta: dict,
    book_map: dict | None = None,
    *,
    where: str = "field",
) -> str:
    target = normalize_translation(source, target)
    if book_map:
        target = normalize_translation_units(source, target, book_map, where)
    if str(meta.get("notation") or "latin") == "arabic":
        target = normalize_arabic_notation(target)
    if str(meta.get("language_code") or meta.get("language") or "") == "ar":
        target = normalize_print_digits(target)
    # Ray-context only: keep true vectors. Section persist rewrites leftover
    # geometry ``\vec`` that never named itself a ray.
    target = canonicalize_geometry_arrows(target, vector_lesson=True)
    return target


def atomic_write_json(path: Path, document: Any) -> None:
    """Write JSON beside its destination, then atomically replace it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def json_fingerprint(value: Any) -> str:
    """A stable content hash for resumable translation manifests."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def figure_description(figure: Any) -> str:
    if isinstance(figure, dict):
        return str(figure.get("description") or "")
    return str(figure or "")


def _project_figure(figure: Any) -> dict[str, str]:
    return {"description": figure_description(figure)}


LEAKED_OPTION_LABEL = re.compile(r"^\s*(?:Option|Choice)\s+[A-Fa-f]\s*[:.)\-]\s*")


def option_text(option: Any) -> str:
    """An option without an ``Option D:`` label the extraction copied into it.

    The page prints the letter beside the option; the translation never
    repeats it, so neither the prompt nor the checker may count it as prose.
    """
    text = str(option)
    stripped = LEAKED_OPTION_LABEL.sub("", text, count=1)
    return stripped if stripped.strip() else text


def translatable_group(group: dict) -> dict:
    """Return exactly the fields the model is allowed to translate."""
    return {
        "id": str(group.get("id") or ""),
        "stem": str(group.get("stem") or ""),
        "figures": [_project_figure(row) for row in group.get("figures") or []],
        "poems": copy.deepcopy(list(group.get("poems") or [])),
        "exercises": [
            {
                "id": str(exercise.get("id") or ""),
                "text": str(exercise.get("text") or ""),
                "options": [option_text(option) for option in exercise.get("options") or []],
                "answer": str(exercise.get("answer") or ""),
                "figures": [
                    _project_figure(row) for row in exercise.get("figures") or []
                ],
                "poems": copy.deepcopy(list(exercise.get("poems") or [])),
            }
            for exercise in group.get("exercises") or []
        ],
    }




def _environment_problems(text: str, where: str) -> list[str]:
    stack: list[str] = []
    problems: list[str] = []
    for match in ENVIRONMENT.finditer(text):
        action, name = match.groups()
        if action == "begin":
            stack.append(name)
        elif not stack or stack[-1] != name:
            problems.append(f"{where}: unmatched \\end{{{name}}}")
        else:
            stack.pop()
    for name in reversed(stack):
        problems.append(f"{where}: unmatched \\begin{{{name}}}")
    return problems


def _fold_equivalent_notation(segment: str) -> str:
    """Spellings two books print for the same mathematics.

    ``\\cdot`` and ``\\times`` are both the product (except between vectors,
    where they are two different products); ``//`` is how Egyptian Arabic
    books print ``\\parallel``; ``\\text{ rad}`` and the Arabic superscript
    ``^د`` both mark radians.
    """
    if not VECTOR_COMMAND.search(segment):
        segment = re.sub(r"\\cdot(?![A-Za-z])", r"\\times ", segment)
    segment = segment.replace("//", r"\parallel ")
    segment = RADIAN_MARK.sub(" ", segment)
    return segment


def _math_skeleton(
    segment: str, notation: str, *, keep_text_digits: bool = False
) -> str:
    segment = _fold_equivalent_notation(segment.replace("∠", r"\angle"))
    if notation != "arabic":
        def text_slot(found: re.Match[str]) -> str:
            raw = found.group(1)
            punctuation = re.sub(r"\s+", "", raw)
            if punctuation in {",", "،", ";", "؛", ":", "."}:
                return punctuation
            body = " ".join(raw.split()).casefold()
            if body in ORDINAL_SUFFIX:
                return ""
            if body in LATIN_UNIT_PHRASES:
                unit, power = LATIN_UNIT_PHRASES[body]
                return rf"\text{{{unit}}}^{power}"
            unit = LATIN_UNIT_ALIASES.get(body, body)
            # Translated prose may move into or out of \text{...}; only
            # profile-controlled Latin unit content is immutable.
            if LATIN_MATH_UNIT.fullmatch(unit):
                return rf"\text{{{unit}}}"
            digits = "".join(TEXT_BODY_DIGIT.findall(raw))
            # ``\text{Model A}`` and ``\text{الطراز } A`` are the same label.
            labels = "".join(TEXT_BODY_IDENTIFIER.findall(raw))
            return digits + labels

        replaced = ORDINAL_SUPERSCRIPT.sub("", segment)
        replaced = TEXT_IN_MATH.sub(text_slot, replaced)
        replaced = replaced.translate(str.maketrans({"،": ",", "؛": ";", "−": "-"}))
        replaced = re.sub(r"\{:\}", ":", re.sub(r"\s+", "", replaced))
        # ``\text{LE } 450`` and ``450\text{ جنيه}`` are one quantity; the
        # unit's identity is compared separately, its side of the number is not.
        return PREFIX_UNIT.sub(r"\2\1", replaced)

    # Arabic notation deliberately changes variables and functions:
    # n -> ن, i -> ت, \sin -> جا. A number set stays \mathbb{X}; the typesetter
    # prints the Arabic set letter. Compare the immutable numerical/operator
    # structure while retaining structural LaTeX commands. Text runs may
    # legitimately appear or disappear around units (\text{cm} -> \text{سم}).
    # Their wording is checked separately; it is not immutable maths structure.
    def arabic_text_slot(found: re.Match[str]) -> str:
        body = found.group(1).strip()
        if body in {",", "،", ";", "؛"}:
            return ","
        if keep_text_digits:
            return " ".join(TEXT_BODY_DIGIT.findall(body))
        return ""

    replaced = TEXT_IN_MATH.sub(arabic_text_slot, segment)
    # A number set stays \\mathbb{X}. Replacing it with a bare letter drops
    # this marker, so the skeletons no longer match.
    structural = (
        "frac", "sqrt", "vec", "triangle", "angle",
        "begin", "end", "hline", "times", "div", "cdot", "pm", "mp",
        "neq", "leq", "geq", "in", "notin", "cup", "cap", "subset",
        "subseteq", "parallel", "perp", "circ", "mathbb",
    )
    markers = {name: f"¤{index + 1}¤" for index, name in enumerate(structural)}
    replaced = re.sub(
        r"\\([A-Za-z]+)\b",
        lambda found: markers.get(found.group(1), ""),
        replaced,
    )
    replaced = re.sub(r"\\[,;! ]", "", replaced)
    replaced = replaced.translate(str.maketrans({"،": ",", "؛": ";", "−": "-"}))
    replaced = "".join(char for char in replaced if not char.isalpha())
    replaced = re.sub(r"\{\}", "", replaced)
    return re.sub(r"\s+", "", replaced)


def _quadrant_label_number(body: str) -> str | None:
    """The quadrant a whole island names (``1^{st}\\text{ quadrant}``), if it is one."""
    if not QUADRANT_WORD.search(body):
        return None
    words = re.sub(r"\\(?:text|mathrm|operatorname)\s*\{([^{}]*)\}", r" \1 ", body)
    words = re.sub(r"[\^{}$]|\\[,;! ]", " ", words)
    number = None
    for word in words.split():
        folded = word.casefold()
        if folded in _QUADRANT_FILLER:
            continue
        if folded not in _QUADRANT_NUMBER_WORDS:
            return None
        number = _QUADRANT_NUMBER_WORDS[folded]
    return number


def _strip_quadrant_labels(text: str) -> tuple[str, list[str]]:
    """Remove islands that only name a quadrant; quadrant names are wording.

    English writes ``$1^{st}\\text{ quadrant}$`` or ``the $2^{nd}$ quadrant``;
    Arabic writes «الربع الأول» or ``الربع $2$``. Either side may be prose, so
    neither is turned into mathematics. The numbers are returned so that two
    sides which both kept an island can still be compared.
    """
    numbers: list[str] = []
    pieces: list[str] = []
    cursor = 0
    for found in MATH_SEGMENT.finditer(text):
        body = found.group(0).strip("$")
        number = _quadrant_label_number(body)
        if number is None and QUADRANT_NUMBER_ISLAND.fullmatch(found.group(0)):
            before = text[: found.start()]
            after = text[found.end() :]
            if QUADRANT_BEFORE.search(before) or QUADRANT_AFTER.match(after):
                number = re.search(r"[1-4]", body).group(0)
        if number is None:
            continue
        numbers.append(number)
        pieces.append(text[cursor : found.start()])
        pieces.append(" ")
        cursor = found.end()
    pieces.append(text[cursor:])
    return "".join(pieces), numbers


def _math_problems(source: str, target: str, where: str, notation: str) -> list[str]:
    source, source_quadrants = _strip_quadrant_labels(source)
    target, target_quadrants = _strip_quadrant_labels(target)
    if (
        source_quadrants
        and target_quadrants
        and Counter(source_quadrants) != Counter(target_quadrants)
    ):
        return [
            f"{where}: mathematics differs in quadrant labels: "
            f"{source_quadrants} -> {target_quadrants}"
        ]
    first: list[str] | None = None
    for variant in _math_variants(source, target, notation):
        problems = _math_variant_problems(*variant, where, notation)
        if not problems:
            return []
        if first is None:
            first = problems
    return first or []


def _promote_named_angles(text: str) -> str:
    """``common angle $C$`` / «الزاوية $ج$» read as ``$\\angle C$``.

    Either language may name an angle in words before a bare vertex island or
    print the ``\\angle`` sign inside it; both are the same angle.
    """
    return NAMED_ANGLE.sub(
        lambda found: f"{found.group('word')} $\\angle {found.group('name')}$",
        text,
    )


def _math_variants(
    source: str, target: str, notation: str
) -> list[tuple[str, str, bool, bool]]:
    """The plain comparison first, then readings that only ever loosen it.

    Each later reading accepts one more way an Arabic book writes the same
    mathematics: a digit moved out of ``\\text{Step 1}``, a named angle
    written ``\\angle C``, or (arabic notation, when the source has no
    subscripts) ``(نق_1)`` telling apart two radii English names R and r.
    """
    promoted_source = _promote_named_angles(source)
    promoted_target = _promote_named_angles(target)
    pairs = [(source, target)]
    if promoted_source != source or promoted_target != target:
        pairs.append((promoted_source, promoted_target))
    digit_readings = [False, True] if notation == "arabic" else [False]
    subscript_readings = (
        [False, True]
        if notation == "arabic" and "_" not in "".join(MATH_SEGMENT.findall(source))
        else [False]
    )
    return [
        (variant_source, variant_target, digits, subscripts)
        for variant_source, variant_target in pairs
        for digits in digit_readings
        for subscripts in subscript_readings
    ]


def _math_variant_problems(
    source: str,
    target: str,
    keep_text_digits: bool,
    drop_target_subscripts: bool,
    where: str,
    notation: str,
) -> list[str]:
    source_segments = MATH_SEGMENT.findall(source)
    target_segments = MATH_SEGMENT.findall(target)
    if notation == "arabic":
        token = re.compile(
            r"¤\d+¤|\d+(?:\.\d+)?|[+\-*/=^_<>()\[\],;]"
        )
        def normalized_tokens(segment: str, *, side: str = "source") -> list[str]:
            tokens = token.findall(
                _math_skeleton(segment, notation, keep_text_digits=keep_text_digits)
            )
            if drop_target_subscripts and side == "target":
                kept: list[str] = []
                skip = False
                for item in tokens:
                    if skip:
                        skip = False
                        if re.fullmatch(r"\d+", item):
                            continue
                    if item == "_":
                        skip = True
                        continue
                    kept.append(item)
                tokens = kept
            changed = True
            while changed:
                changed = False
                for start, item in enumerate(tokens):
                    if item != "(":
                        continue
                    try:
                        stop = tokens.index(")", start + 1)
                    except ValueError:
                        break
                    interior = tokens[start + 1 : stop]
                    if "(" in interior:
                        continue
                    cosmetic = (
                        not interior
                        or all(part in {",", ";"} for part in interior)
                        or (
                            interior[0].startswith("¤")
                            and not any(
                                part in {"+", "-", "*", "/", "=", "<", ">"}
                                for part in interior
                            )
                        )
                    )
                    if cosmetic:
                        tokens = tokens[:start] + interior + tokens[stop + 1 :]
                        changed = True
                        break
            return tokens

        source_flat = [
            item
            for segment in source_segments
            for item in normalized_tokens(segment)
            if item not in {",", ";"}
        ]
        target_flat = [
            item
            for segment in target_segments
            for item in normalized_tokens(segment, side="target")
            if item not in {",", ";"}
        ]
        if source_flat == target_flat:
            return []

        def formulas(segments: list[str], side: str) -> Counter[tuple[str, ...]]:
            found: Counter[tuple[str, ...]] = Counter()
            for segment in segments:
                tokens = normalized_tokens(segment, side=side)
                current: list[str] = []
                for item in tokens:
                    if item in {",", ";"}:
                        if current:
                            found[tuple(current)] += 1
                            current = []
                    else:
                        current.append(item)
                if current:
                    found[tuple(current)] += 1
            return found

        source_tokens = formulas(source_segments, "source")
        target_tokens = formulas(target_segments, "target")
        if source_tokens != target_tokens:
            return [
                f"{where}: immutable mathematics differs: "
                f"{dict(source_tokens)} -> {dict(target_tokens)}"
            ]
        return []
    return _latin_math_problems(source, target, source_segments, target_segments, where)


def _latin_core(skeleton: str) -> str:
    return re.sub(r"^\$+|\$+$", "", skeleton.strip())


def _latin_promotion(skeleton: str, source: str) -> bool:
    """True when an extra Latin island is a font/profile promotion, not a formula."""
    core = _latin_core(skeleton)
    if not core:
        return True
    if LATIN_PROMOTION_NUMBER.fullmatch(core):
        number = re.match(
            r"[+\-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", core
        )
        if not number:
            return False
        digit = number.group(0)
        return digit in source or _source_has_number_word(source, digit)
    found = LATIN_PROMOTION_IDENTIFIER.fullmatch(core)
    if not found:
        return False
    token = found.group(1) or found.group(2)
    return bool(
        re.search(rf"(?<![A-Za-z\\]){re.escape(token)}(?![A-Za-z])", source)
    )


def _island_join(gap: str) -> str | None:
    """The separator prose between two islands stands for, if it is only one."""
    compact = re.sub(r"\s+", "", gap).replace("،", ",").replace("؛", ";")
    if compact in {",", ";", ":"}:
        return compact
    if compact in {"و", ",و", "and", ",and", "or", ",or", "أو", ",أو"}:
        return ","
    return None


def _latin_math_problems(
    source: str,
    target: str,
    source_segments: list[str],
    target_segments: list[str],
    where: str,
) -> list[str]:
    """Keep formula islands aligned; allow extra $x$ / $A$ / digit islands.

    Arabic + latin notation must put reader-visible digits and Latin identifiers
    in math mode for the math face. Those promotions add `$...$` islands without
    changing the immutable formulas. One English list island
    (``$\\vec{MA}, \\vec{MB}$``) may also be printed as consecutive Arabic
    islands joined only by «،» / «و».
    """
    source_skeletons = [
        _math_skeleton(segment, "latin") for segment in source_segments
    ]
    target_skeletons = [
        _math_skeleton(segment, "latin") for segment in target_segments
    ]

    def joins_for(text: str, segments: list[str]) -> list[str | None]:
        spans = [found.span() for found in MATH_SEGMENT.finditer(text)]
        if len(spans) != len(segments):
            return [None] * max(len(segments) - 1, 0)
        return [
            _island_join(text[spans[item][1] : spans[item + 1][0]])
            for item in range(len(segments) - 1)
        ]

    target_joins = joins_for(target, target_segments)
    source_joins = joins_for(source, source_segments)

    def joined_run(
        skeletons: list[str],
        joins: list[str | None],
        start: int,
        wanted: str,
    ) -> int | None:
        if start >= len(skeletons):
            return None
        if skeletons[start] == wanted:
            return start + 1
        joined = _latin_core(skeletons[start])
        core = _latin_core(wanted)
        stop = start
        while stop < len(joins) and joins[stop] is not None:
            joined += joins[stop] + _latin_core(skeletons[stop + 1])
            stop += 1
            if joined == core:
                return stop + 1
            if not core.startswith(joined):
                return None
        return None

    cursor = 0
    source_i = 0
    while source_i < len(source_skeletons):
        index = source_i + 1
        before = source_segments[source_i]
        source_skeleton = source_skeletons[source_i]
        if not _latin_core(source_skeleton):
            # ``$\text{Mathematics}$`` has no immutable tokens; Arabic may
            # put that label in prose without a matching math island.
            source_i += 1
            continue
        found_target: int | None = None
        found_source: int | None = None
        for look in range(cursor, len(target_skeletons)):
            skipped = range(cursor, look)
            if not all(
                _latin_promotion(target_skeletons[item], source)
                for item in skipped
            ):
                return [
                    f"{where}: mathematics differs in segment {index}: "
                    f"{before!r} -> {target_segments[cursor]!r}"
                ]
            target_end = joined_run(
                target_skeletons, target_joins, look, source_skeleton
            )
            if target_end is not None:
                found_target = target_end
                found_source = source_i + 1
                break
            source_end = joined_run(
                source_skeletons, source_joins, source_i, target_skeletons[look]
            )
            if source_end is not None:
                found_target = look + 1
                found_source = source_end
                break
        if found_target is None or found_source is None:
            if cursor < len(target_segments):
                return [
                    f"{where}: mathematics differs in segment {index}: "
                    f"{before!r} -> {target_segments[cursor]!r}"
                ]
            return [
                f"{where}: translated field has {len(target_segments)} "
                f"mathematics segment(s), expected {len(source_segments)}"
            ]
        cursor = found_target
        source_i = found_source
    leftover = range(cursor, len(target_skeletons))
    if leftover and not all(
        _latin_promotion(target_skeletons[item], source) for item in leftover
    ):
        return [
            f"{where}: translated field has {len(target_segments)} mathematics "
            f"segment(s), expected {len(source_segments)}"
        ]
    return []


def bump_lengthmark_offsets(tikz: str, delta_cm: float = 0.15) -> str:
    """Move \\lengthmark labels farther from their segments after a collision."""

    def one(found: re.Match[str]) -> str:
        option = found.group(1)
        if option is None or option.strip() == "[]":
            return f"\\lengthmark[{DEFAULT_LENGTHMARK_GAP_CM + delta_cm:.2f}cm]"
        inner = option[1:-1].strip()
        dimension = re.fullmatch(
            r"([0-9]*\.?[0-9]+)(cm|mm|pt|em|ex)", inner
        )
        if dimension is None:
            return found.group(0)
        value = float(dimension.group(1))
        unit = dimension.group(2)
        if unit == "mm":
            centimetres = value / 10
        elif unit in {"pt", "em", "ex"}:
            centimetres = value / 28.452755906
        else:
            centimetres = value
        return f"\\lengthmark[{centimetres + delta_cm:.2f}cm]"

    return TIKZ_LENGTHMARK_HEAD.sub(one, tikz)


def _prose_math_signature(text: str) -> tuple[frozenset[tuple[str, str, str, str]], Counter[str]]:
    """Signed quantities/units, independent of delimiters and lexical number words."""
    quantities: Counter[tuple[str, str, str, str]] = Counter()
    # Wrapping every printable target digit in a math island is a font contract,
    # not a semantic change.  Ignore those delimiters when comparing quantities.
    scan = text.replace("$$", "").replace("$", "")
    # Egyptian terminology lexicalizes the named 30°-60°-90° triangle as
    # ``المثلث الثلاثيني الستيني``.  Restore that name's semantic angle
    # signature when the digits are not also printed parenthetically.
    if re.search(r"المثلث\s+الثلاثيني\s+الستيني", scan):
        for angle in ("30", "60", "90"):
            if not re.search(
                rf"(?<!\d){angle}\s*(?:°|\^\s*\\circ)", scan
            ):
                scan += f" {angle}°"
    # A hyphen in ``30°-60°-90°`` separates named angle measures; it is not a
    # negative sign on the following angle.
    scan = re.sub(r"(?<=°)[\-−](?=\d)", " ", scan)
    scan = re.sub(r"(?<=\\circ)[\-−](?=\d)", " ", scan)
    for found in PROSE_QUANTITY.finditer(scan):
        sign = "-" if found.group("sign") in {"-", "−"} else found.group("sign")
        unit_raw = found.group("degree") or found.group("unit")
        unit = UNIT_CANONICAL.get(unit_raw, unit_raw.casefold())
        power = (found.group("power") or "").translate(SUPERSCRIPT_DIGITS)
        power = re.sub(r"[\^{}]", "", power)
        quantities[(sign, found.group("number"), unit, power)] += 1

    # Other prose numbers can be translated lexically (4-sided -> رباعي,
    # Grade 8 -> الصف الثاني الإعدادي). Formula numbers remain protected by
    # strict math validation; prose quantities are protected above.
    return frozenset(quantities), Counter()


def _prose_for_language_check(text: str) -> str:
    pieces: list[str] = []
    last = 0
    for segment in MATH_SEGMENT.finditer(text):
        pieces.append(text[last : segment.start()])
        pieces.extend(
            body
            for body in TEXT_IN_MATH.findall(segment.group(0))
            if body.strip().casefold() not in ORDINAL_SUFFIX
        )
        last = segment.end()
    pieces.append(text[last:])
    prose = " ".join(pieces)
    prose = FIGURE_MARKER.sub(" ", prose)
    prose = LOCALIZED_FIGURE_MARKER.sub(" ", prose)
    prose = POEM_MARKER.sub(" ", prose)
    prose = LOCALIZED_POEM_MARKER.sub(" ", prose)
    prose = LATEX_COMMAND.sub(" ", prose)
    return prose


TITLE_CASE_WORD = re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)*\b")
TITLE_CASE_RUN = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b")


def _source_proper_names(source: str) -> set[str]:
    """Latin proper names the Arabic field may keep, from the English source.

    Multi-word title-case runs (Microsoft Excel) and mid-sentence title-case
    tokens (Insert, Pie) stay in Latin, as the language prompt asks. A glossary
    term that is the whole field, or a word that opens a sentence, is not
    protected — those are wording to translate.
    """
    names: set[str] = set()
    for found in TITLE_CASE_RUN.finditer(source):
        names.update(ASCII_WORD.findall(found.group(0)))
    for found in TITLE_CASE_WORD.finditer(source):
        if found.start() == 0:
            continue
        before = source[: found.start()].rstrip()
        if not before or before[-1] in ".!?؟:":
            continue
        names.add(found.group(0))
    return names


def _language_problems(source: str, target: str, where: str, meta: dict) -> list[str]:
    language = str(meta.get("language_code") or meta.get("language") or "")
    if language != "ar":
        return []
    prose = _prose_for_language_check(target)
    source_prose = _prose_for_language_check(source)
    source_for_words = source_prose
    for phrase in LATIN_UNIT_PHRASES:
        source_for_words = re.sub(
            rf"\b{re.escape(phrase)}\b", " ", source_for_words, flags=re.I
        )
    source_words = set(ASCII_WORD.findall(source_for_words))
    protected_latin = {
        word
        for segment in MATH_SEGMENT.findall(source)
        for word in ASCII_WORD.findall(TEXT_IN_MATH.sub("", segment))
    }
    protected_latin.update(
        word
        for quoted in re.findall(r"""['"]([^'"]+)['"]""", source)
        for word in ASCII_WORD.findall(quoted)
    )
    protected_latin.update(
        token
        for token in re.findall(r"\(([A-Za-z][A-Za-z0-9_-]{1,12})\)", source)
    )
    protected_latin.update(
        word
        for word in ASCII_WORD.findall(source)
        if word in CALCULATOR_KEYS
    )
    notation = str(meta.get("notation") or "latin")
    proper_names = _source_proper_names(source)

    def source_acronym(word: str) -> bool:
        return word.isupper() and word in source_words

    def allowed_target(word: str) -> bool:
        return word.casefold() in ASCII_ALLOWLIST or (
            word in CALCULATOR_KEYS and word in protected_latin
        ) or word in proper_names or source_acronym(word) or (
            notation == "latin" and word in protected_latin
        )

    words = [
        word
        for word in ASCII_WORD.findall(prose)
        if not allowed_target(word)
    ]
    problems = (
        [f"{where}: English prose remains in Arabic translation: {', '.join(words[:8])}"]
        if words
        else []
    )
    source_had_words = any(
        word.casefold() not in ASCII_ALLOWLIST
        and word.casefold() not in LATIN_UNIT_ALIASES
        and word not in CALCULATOR_KEYS
        and not word.isupper()
        and not (notation == "latin" and word in protected_latin)
        for word in source_words
    )
    target_has_arabic_math = bool(
        MATH_SEGMENT.fullmatch(target.strip())
        and ARABIC_LETTER.search(target)
    )
    if (
        source_had_words
        and not ARABIC_LETTER.search(prose)
        and not target_has_arabic_math
        and not words
    ):
        problems.append(f"{where}: source prose has no Arabic translation")
    return problems


def _mark_problems(text: str, where: str) -> list[str]:
    problems: list[str] = []
    for index, char in enumerate(text):
        if char not in TASHKEEL:
            continue
        look = index - 1
        while look >= 0 and text[look] in TASHKEEL:
            look -= 1
        if look < 0 or not ("\u0621" <= text[look] <= "\u064A"):
            problems.append(f"{where}: Arabic mark not after an Arabic letter")
            break
    return problems


# Printed practice Arabic is CONTEXTUAL: bare by default, sparse marks OK, full
# vocalisation not. ﴿…﴾ and «…» excerpts keep their marks and are masked before
# this check so quoted wording beside bare prose does not fail the field.
_FULL_VOCALIZED_WORD_FRACTION = 0.5
_FULL_MARK_DENSITY = 0.28
_FULL_MIN_VOCALIZED_WORDS = 3


def mask_quran_spans(text: str) -> str:
    """Replace ﴿...﴾ spans with spaces so their tashkeel is not scored as prose."""
    from src.arabic_math import QURAN_CLOSE, QURAN_OPEN

    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        start = text.find(QURAN_OPEN, index)
        if start < 0:
            out.append(text[index:])
            break
        end = text.find(QURAN_CLOSE, start + 1)
        if end < 0:
            out.append(text[index:])
            break
        out.append(text[index:start])
        out.append(" " * (end + 1 - start))
        index = end + 1
    return "".join(out)


GUILLEMET_OPEN = "\u00ab"
GUILLEMET_CLOSE = "\u00bb"
_PROTECT_TOKEN = "\ufff0{0}\ufff1"


def mask_guillemet_spans(text: str) -> str:
    """Replace «...» spans with spaces so their tashkeel is not scored as prose."""
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        start = text.find(GUILLEMET_OPEN, index)
        if start < 0:
            out.append(text[index:])
            break
        end = text.find(GUILLEMET_CLOSE, start + 1)
        if end < 0:
            out.append(text[index:])
            break
        out.append(text[index:start])
        out.append(" " * (end + 1 - start))
        index = end + 1
    return "".join(out)


def _arabic_words(text: str) -> list[str]:
    words: list[str] = []
    current: list[str] = []
    for char in text:
        if "\u0621" <= char <= "\u064A" or char in TASHKEEL or char == "\u0640":
            current.append(char)
            continue
        if current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return words


def excessive_tashkeel_problems(text: str, where: str) -> list[str]:
    """Fail when printed Arabic is fully vocalised outside ﴿…﴾ and «…» quotes."""
    if not text or not any(char in TASHKEEL for char in text):
        return []
    prose = mask_guillemet_spans(mask_quran_spans(text))
    if not any(char in TASHKEEL for char in prose):
        return []
    words = _arabic_words(prose)
    if not words:
        return []
    vocalized = sum(1 for word in words if any(char in TASHKEEL for char in word))
    letters = sum(1 for char in prose if "\u0621" <= char <= "\u064A")
    marks = sum(1 for char in prose if char in TASHKEEL)
    if vocalized < _FULL_MIN_VOCALIZED_WORDS:
        return []
    word_fraction = vocalized / len(words)
    density = marks / letters if letters else 0.0
    if word_fraction >= _FULL_VOCALIZED_WORD_FRACTION or density >= _FULL_MARK_DENSITY:
        return [
            f"{where}: Arabic is fully vocalised; use contextual tashkeel only "
            "(bare by default, marks on words that need them)"
        ]
    return []


def _next_quoted_span(text: str, index: int) -> tuple[int, int] | None:
    """Earliest ﴿…﴾ or «…» span at or after index, as (start, end_exclusive)."""
    from src.arabic_math import QURAN_CLOSE, QURAN_OPEN

    candidates: list[tuple[int, int]] = []
    quran_start = text.find(QURAN_OPEN, index)
    if quran_start >= 0:
        quran_end = text.find(QURAN_CLOSE, quran_start + 1)
        if quran_end >= 0:
            candidates.append((quran_start, quran_end + 1))
    guillemet_start = text.find(GUILLEMET_OPEN, index)
    if guillemet_start >= 0:
        guillemet_end = text.find(GUILLEMET_CLOSE, guillemet_start + 1)
        if guillemet_end >= 0:
            candidates.append((guillemet_start, guillemet_end + 1))
    if not candidates:
        return None
    return min(candidates, key=lambda span: span[0])


def _strip_tashkeel_from_prose(prose: str) -> str:
    return "".join(char for char in prose if char not in TASHKEEL)


def repair_excessive_tashkeel(text: str, as_is: Sequence[str] = ()) -> str:
    """Drop harakāt from prose when the field would fail the density check.

    ﴿…﴾, «…», and as_is excerpts are left untouched. ``repair_mark_placement``
    in new_questions runs afterward for whitelist sparse marks.
    """
    if not text or not excessive_tashkeel_problems(text, ""):
        return text

    protected: list[str] = []

    def hold(chunk: str) -> str:
        protected.append(chunk)
        return _PROTECT_TOKEN.format(len(protected) - 1)

    work = text
    for excerpt in sorted((item for item in as_is if item), key=len, reverse=True):
        if excerpt in work:
            work = work.replace(excerpt, hold(excerpt))

    pieces: list[str] = []
    index = 0
    length = len(work)
    while index < length:
        span = _next_quoted_span(work, index)
        if span is None:
            pieces.append(_strip_tashkeel_from_prose(work[index:]))
            break
        start, end = span
        pieces.append(_strip_tashkeel_from_prose(work[index:start]))
        pieces.append(hold(work[start:end]))
        index = end
    work = "".join(pieces)

    for offset, chunk in enumerate(protected):
        work = work.replace(_PROTECT_TOKEN.format(offset), chunk)
    return work


# Shadda must sit with a vowel/tanween in the same letter cluster. Sukun alone
# does not count. Fatḥatān (and the other tanweens) must not sit on bare alif
# U+0627; hamza-alif forms (أ إ آ) are allowed.
_SHADDA = "\u0651"
_CLUSTER_VOWEL = re.compile("[\u064B-\u0650]")
_LETTER_CLUSTER = re.compile("[\u0621-\u064A\u0671][\u064B-\u0652\u0670]*")
_TANWEEN_ON_ALIF = re.compile("\u0627[\u064B-\u064D]")
_TANWEEN_ON_ALIF_FIX = re.compile("([\u0621-\u064A\u0671])\u0627([\u064B-\u064D])")

# Whitelist only — do not invent tanween for every word ending in ا, and do not
# rewrite every يًا (خاليًا / كافيًا stay without shadda; nisba needs يًّا).
_MISSING_ADVERB_TANWEEN = (
    (re.compile(r"نطقا(?![ًٌٍَُِّْ])"), "نطقًا"),
)
_NISBA_WITHOUT_SHADDA = (
    (re.compile(r"شرعيًا"), "شرعيًّا"),
    (re.compile(r"إلهيًا"), "إلهيًّا"),
    (re.compile(r"عربيًا"), "عربيًّا"),
    (re.compile(r"علميًا"), "علميًّا"),
    (re.compile(r"دينيًا"), "دينيًّا"),
    (re.compile(r"رسميًا"), "رسميًّا"),
    (re.compile(r"لغويًا"), "لغويًّا"),
    (re.compile(r"فقهيًا"), "فقهيًّا"),
)
# Known bare-shadda imperatives: pair with the usual vowel instead of stripping.
_BARE_SHADDA_UPGRADES = (
    (re.compile(r"بيّن"), "بيِّن"),
    (re.compile(r"حدّد"), "حدِّد"),
)
# Field-initial imperative بين (preposition بين mid-sentence is left alone).
_IMPERATIVE_BAYYIN = (
    (re.compile(r"^بين(?=\s)"), "بيِّن"),
    (re.compile(r"(?<=[.\n؟!\u060c،؛:])\s*بين(?=\s)"), " بيِّن"),
)
_WHITELIST_MARK_REPAIRS = (
    _MISSING_ADVERB_TANWEEN
    + _NISBA_WITHOUT_SHADDA
    + _BARE_SHADDA_UPGRADES
    + _IMPERATIVE_BAYYIN
)


def _needs_mark_placement_work(text: str) -> bool:
    if any(char in TASHKEEL for char in text):
        return True
    return any(pattern.search(text) for pattern, _ in _WHITELIST_MARK_REPAIRS)


def _repair_whitelist_marks(prose: str) -> str:
    """Apply safe Mostafa-pattern word fixes on prose (Quran/as_is already held out)."""
    fixed = prose
    for pattern, replacement in _WHITELIST_MARK_REPAIRS:
        fixed = pattern.sub(replacement, fixed)
    return fixed


def _repair_prose_marks(prose: str) -> str:
    """Move tanween off bare alif; upgrade/drop unpaired shadda; apply whitelist fixes."""
    if not prose:
        return prose
    # Upgrade known bare-shadda imperatives before the generic strip so بيّن → بيِّن
    # (not بين). Then tanween-on-alif, then strip any remaining unpaired shadda.
    fixed = _repair_whitelist_marks(prose)
    if any(char in TASHKEEL for char in fixed):
        fixed = _TANWEEN_ON_ALIF_FIX.sub(r"\1\2" + "\u0627", fixed)

        def fix_cluster(match: re.Match[str]) -> str:
            cluster = match.group()
            if _SHADDA in cluster and not _CLUSTER_VOWEL.search(cluster):
                return cluster.replace(_SHADDA, "")
            return cluster

        fixed = _LETTER_CLUSTER.sub(fix_cluster, fixed)
    # Whitelist again for forms that do not need prior tashkeel (نطقا, leading بين).
    return _repair_whitelist_marks(fixed)


def repair_mark_placement(text: str, as_is: Sequence[str] = ()) -> str:
    """Repair bare shadda, tanween-on-alif, and whitelist mark forms outside Quran/as_is.

    Models habitually write بيّن / دائماً / نطقا / شرعيًا / bare بين as an
    imperative. Reject-and-retry cannot clear a whole objective of those.
    Pairing known bare-shadda imperatives (بيّن → بيِّن), moving fatḥatān before
    alif (دائماً → دائمًا), adding missing adverbial fatḥatān on a whitelist
    (نطقا → نطقًا), restoring nisba shadda on a whitelist (شرعيًا → شرعيًّا),
    and upgrading field-initial بين → بيِّن are safe mechanical fixes; ﴿…﴾ and
    as_is excerpts are left untouched. خاليًا / كافيًا and mid-sentence
    preposition بين are not rewritten.
    """
    if not text or not _needs_mark_placement_work(text):
        return text

    from src.arabic_math import QURAN_CLOSE, QURAN_OPEN

    protected: list[str] = []

    def hold(chunk: str) -> str:
        protected.append(chunk)
        return _PROTECT_TOKEN.format(len(protected) - 1)

    work = text
    for excerpt in sorted((item for item in as_is if item), key=len, reverse=True):
        if excerpt in work:
            work = work.replace(excerpt, hold(excerpt))

    pieces: list[str] = []
    index = 0
    length = len(work)
    while index < length:
        start = work.find(QURAN_OPEN, index)
        if start < 0:
            pieces.append(_repair_prose_marks(work[index:]))
            break
        end = work.find(QURAN_CLOSE, start + 1)
        if end < 0:
            pieces.append(_repair_prose_marks(work[index:]))
            break
        pieces.append(_repair_prose_marks(work[index:start]))
        pieces.append(hold(work[start : end + 1]))
        index = end + 1
    work = "".join(pieces)

    for offset, chunk in enumerate(protected):
        work = work.replace(_PROTECT_TOKEN.format(offset), chunk)
    return work


def mark_placement_problems(
    text: str,
    where: str,
    as_is: Sequence[str] = (),
) -> list[str]:
    """Fail bare shadda, tanween-on-alif, and whitelist mark forms outside Quran/as_is.

    Checks a copy only: as_is strings and ﴿…﴾ spans are blanked, then NFKC is
    applied so PDF presentation forms do not slip past. The original field is
    never rewritten here.
    """
    if not text or not _needs_mark_placement_work(text):
        return []
    checked = text
    for excerpt in as_is:
        if excerpt:
            checked = checked.replace(excerpt, " ")
    checked = mask_quran_spans(checked)
    if not _needs_mark_placement_work(checked):
        return []
    normalized = unicodedata.normalize("NFKC", checked)
    problems: list[str] = []
    for match in _LETTER_CLUSTER.finditer(normalized):
        cluster = match.group()
        if _SHADDA in cluster and not _CLUSTER_VOWEL.search(cluster):
            problems.append(
                f"{where}: bare shadda in «{cluster}»; "
                "pair shadda with a haraka or tanween"
            )
    for match in _TANWEEN_ON_ALIF.finditer(normalized):
        start = max(0, match.start() - 3)
        end = min(len(normalized), match.end() + 1)
        snippet = normalized[start:end]
        problems.append(
            f"{where}: tanween on alif in «{snippet}»; "
            "put the tanween on the letter before alif"
        )
    for pattern, replacement in _WHITELIST_MARK_REPAIRS:
        for match in pattern.finditer(normalized):
            problems.append(
                f"{where}: write «{replacement}» not «{match.group()}»"
            )
    return problems

def legacy_text_problems(
    source: str,
    target: str,
    where: str,
    meta: dict,
    *,
    math_policy: str = "strict",
    book_map: dict | None = None,
) -> list[str]:
    """All deterministic reasons a translated printable field cannot be accepted."""
    problems: list[str] = []
    try:
        target = legacy_normalize_profile_translation(
            source, target, meta, book_map, where=where
        )
    except ValueError as error:
        problems.append(str(error))
        target = legacy_normalize_profile_translation(source, target, meta)
    if source.strip() and not target.strip():
        problems.append(f"{where}: non-empty source has an empty translation")
        return problems
    if not source.strip() and target.strip():
        problems.append(f"{where}: empty source gained translated content")
    if MODEL_CHATTER.search(target):
        problems.append(f"{where}: model chatter or unrelated boilerplate")
    if match := FOREIGN_PRINT.search(target):
        problems.append(f"{where}: unsupported character {match.group()!r}")
    if match := CONTROL_CHARACTER.search(target):
        problems.append(
            f"{where}: control character {match.group()!r}; write every LaTeX "
            "backslash as \\\\ in JSON"
        )
    problems.extend(_mark_problems(target, where))
    if unescaped_dollars(target) % 2:
        problems.append(f"{where}: unbalanced mathematics delimiters")
    if not balanced_braces(target):
        problems.append(f"{where}: unbalanced braces")
    problems.extend(_environment_problems(target, where))
    problems.extend(
        field_problems({"field": PROSE_FONT_COMMAND.sub(r"\1", target)}, where)
    )
    problems.extend(table_problems({"field": target}, where))
    source_markers = FIGURE_MARKER.findall(source)
    target_markers = FIGURE_MARKER.findall(target)
    if source_markers != target_markers or LOCALIZED_FIGURE_MARKER.search(target):
        problems.append(
            f"{where}: figure marker(s) changed; keep canonical [figure N] tokens"
        )
    source_poems = POEM_MARKER.findall(source)
    target_poems = POEM_MARKER.findall(target)
    if source_poems != target_poems or LOCALIZED_POEM_MARKER.search(target):
        problems.append(
            f"{where}: poem marker(s) changed; keep canonical [poem N] tokens"
        )
    notation = str(meta.get("notation") or "latin")
    source_for_math = source
    target_for_math = target
    if str(meta.get("language_code") or meta.get("language") or "") == "ar":
        source_for_math = normalize_print_digits(source_for_math)
    if book_map:
        source_for_math = canonicalize_units(source_for_math, book_map)
        target_for_math = canonicalize_units(target_for_math, book_map)
    source_for_math = canonicalize_geometry_arrows(
        source_for_math, vector_lesson=True
    )
    target_for_math = canonicalize_geometry_arrows(
        target_for_math, vector_lesson=True
    )
    if math_policy == "strict":
        problems.extend(
            _math_problems(
                source_for_math, target_for_math, where, notation
            )
        )
    elif math_policy == "prose":
        source_signature = _prose_math_signature(source_for_math)
        target_signature = _prose_math_signature(target_for_math)
        if source_signature != target_signature:
            problems.append(
                f"{where}: quantities or numbers differ: "
                f"{source_signature} -> {target_signature}"
            )
    else:
        raise ValueError(f"unknown math policy {math_policy!r}")
    if str(meta.get("language_code") or meta.get("language") or "") == "ar":
        if bare := bare_print_digits(target):
            problems.append(
                f"{where}: printable digit(s) remain outside mathematics: "
                + ", ".join(bare[:8])
            )
    problems.extend(_language_problems(source, target, where, meta))
    return problems


def _missing_fields(row: dict, fields: tuple[str, ...], where: str) -> list[str]:
    return [f"{where}: {field} is missing" for field in fields if field not in row]


def _figure_problems(
    source: list[Any],
    target: Any,
    where: str,
    meta: dict,
    book_map: dict | None = None,
) -> list[str]:
    if not isinstance(target, list):
        return [f"{where}: figures is not a list"]
    problems: list[str] = []
    if len(source) != len(target):
        problems.append(
            f"{where}: translated {len(target)} figure(s), expected {len(source)}"
        )
    for index, (before, after) in enumerate(zip(source, target)):
        spot = f"{where}.figures[{index}]"
        if not isinstance(after, dict):
            problems.append(f"{spot}: figure is not an object")
            continue
        problems.extend(_missing_fields(after, REQUIRED_FIGURE_FIELDS, spot))
        if "description" in after:
            problems.extend(
                legacy_text_problems(
                    figure_description(before),
                    str(after["description"]),
                    f"{spot}.description",
                    meta,
                    math_policy="prose",
                    book_map=book_map,
                )
            )
    return problems


def map_mcq_answer(answer: str, options: list[str]) -> str:
    """Map a leading English choice letter only when the exercise is an MCQ."""
    if not options:
        return answer
    letter = chosen_letter(answer)
    mapped = CHOICE_MAP.get(letter)
    if not mapped:
        return answer
    stripped = answer.lstrip()
    padding = answer[: len(answer) - len(stripped)]
    if stripped.startswith(f"({letter})"):
        return padding + f"({mapped})" + stripped[len(f"({letter})") :]
    if stripped.startswith(f"[{letter}]"):
        return padding + f"[{mapped}]" + stripped[len(f"[{letter}]") :]
    return padding + mapped + stripped[len(letter) :]


def group_problems(
    source: dict,
    target: Any,
    meta: dict,
    book_map: dict | None = None,
) -> list[str]:
    """Every structural, language and maths defect in one translated group."""
    if not isinstance(target, dict):
        return [f"{source.get('id', 'group')}: translated group is not an object"]
    where = str(source.get("id") or "group")
    problems = _missing_fields(target, REQUIRED_GROUP_FIELDS, where)
    if target.get("id") != source.get("id"):
        problems.append(
            f"{where}: id is {target.get('id')!r}, expected {source.get('id')!r}"
        )
    if "stem" in target:
        problems.extend(
            legacy_text_problems(
                str(source.get("stem") or ""),
                str(target.get("stem") or ""),
                f"{where}.stem",
                meta,
                book_map=book_map,
            )
        )
    if "figures" in target:
        problems.extend(
            _figure_problems(
                list(source.get("figures") or []),
                target.get("figures"),
                where,
                meta,
                book_map,
            )
        )

    source_exercises = list(source.get("exercises") or [])
    target_exercises = target.get("exercises")
    if not isinstance(target_exercises, list):
        problems.append(f"{where}: exercises is not a list")
        return problems
    if len(source_exercises) != len(target_exercises):
        problems.append(
            f"{where}: translated {len(target_exercises)} exercise(s), "
            f"expected {len(source_exercises)}"
        )
    for index, (before, after) in enumerate(
        zip(source_exercises, target_exercises)
    ):
        exercise_where = str(before.get("id") or f"{where}.exercises[{index}]")
        if not isinstance(after, dict):
            problems.append(f"{exercise_where}: exercise is not an object")
            continue
        problems.extend(
            _missing_fields(after, REQUIRED_EXERCISE_FIELDS, exercise_where)
        )
        if after.get("id") != before.get("id"):
            problems.append(
                f"{exercise_where}: id is {after.get('id')!r}, "
                f"expected {before.get('id')!r}"
            )
        for field in ("text", "answer"):
            if field in after:
                target_value = str(after.get(field) or "")
                if field == "answer":
                    target_value = map_mcq_answer(
                        target_value, list(before.get("options") or [])
                    )
                problems.extend(
                    legacy_text_problems(
                        str(before.get(field) or ""),
                        target_value,
                        f"{exercise_where}.{field}",
                        meta,
                        book_map=book_map,
                    )
                )
        if "options" in after:
            target_options = after.get("options")
            source_options = list(before.get("options") or [])
            if not isinstance(target_options, list):
                problems.append(f"{exercise_where}: options is not a list")
            else:
                if len(source_options) != len(target_options):
                    problems.append(
                        f"{exercise_where}: translated {len(target_options)} option(s), "
                        f"expected {len(source_options)}"
                    )
                for option_index, (source_option, target_option) in enumerate(
                    zip(source_options, target_options)
                ):
                    problems.extend(
                        legacy_text_problems(
                            option_text(source_option),
                            str(target_option),
                            f"{exercise_where}.options[{option_index}]",
                            meta,
                            book_map=book_map,
                        )
                    )
        if "figures" in after:
            problems.extend(
                _figure_problems(
                    list(before.get("figures") or []),
                    after.get("figures"),
                    exercise_where,
                    meta,
                    book_map,
                )
            )
    return problems


def validate_group(
    source: dict,
    target: Any,
    meta: dict,
    book_map: dict | None = None,
) -> None:
    problems = group_problems(source, target, meta, book_map)
    if problems:
        raise ValueError("; ".join(problems))


def _merge_figures(
    source: list[Any],
    target: list[dict],
    meta: dict,
    book_map: dict | None = None,
) -> list[Any]:
    merged: list[Any] = []
    for before, after in zip(source, target):
        if isinstance(before, dict):
            row = copy.deepcopy(before)
            row["description"] = legacy_normalize_profile_translation(
                figure_description(before),
                str(after["description"]),
                meta,
                book_map,
                where="figure.description",
            )
            merged.append(row)
        else:
            merged.append(
                legacy_normalize_profile_translation(
                    str(before),
                    str(after["description"]),
                    meta,
                    book_map,
                    where="figure.description",
                )
            )
    return merged


def _merge_poems(
    source: list[Any],
    target: list[Any],
    meta: dict,
    book_map: dict | None = None,
    where: str = "poem",
) -> list[Any]:
    """Overlay translated shatrs onto English poem structure."""
    if not source:
        return []
    if len(source) != len(target):
        raise ValueError(
            f"{where}: translated {len(target)} poem(s), expected {len(source)}"
        )
    merged: list[Any] = []
    for index, (before, after) in enumerate(zip(source, target), start=1):
        row = copy.deepcopy(before) if isinstance(before, dict) else {"rows": []}
        source_rows = list(row.get("rows") or [])
        target_rows = list((after or {}).get("rows") or []) if isinstance(after, dict) else []
        if len(source_rows) != len(target_rows):
            raise ValueError(
                f"{where} {index}: translated {len(target_rows)} row(s), "
                f"expected {len(source_rows)}"
            )
        out_rows = []
        for line_index, (source_line, target_line) in enumerate(
            zip(source_rows, target_rows), start=1
        ):
            source_shatrs = list(
                (source_line or {}).get("shatrs") or []
                if isinstance(source_line, dict)
                else []
            )
            target_shatrs = list(
                (target_line or {}).get("shatrs") or []
                if isinstance(target_line, dict)
                else []
            )
            if len(source_shatrs) != len(target_shatrs):
                raise ValueError(
                    f"{where} {index} row {line_index}: translated "
                    f"{len(target_shatrs)} shatr(s), expected {len(source_shatrs)}"
                )
            out_rows.append(
                {
                    "shatrs": [
                        legacy_normalize_profile_translation(
                            str(before_shatr),
                            str(after_shatr),
                            meta,
                            book_map,
                            where=f"{where}[{index}].rows[{line_index}]",
                        )
                        for before_shatr, after_shatr in zip(source_shatrs, target_shatrs)
                    ]
                }
            )
        row["rows"] = out_rows
        merged.append(row)
    return merged


def merge_group(
    source: dict,
    target: dict,
    meta: dict,
    book_map: dict | None = None,
    *,
    vector_lesson: bool = True,
) -> dict:
    """Validate, then overlay only printable fields onto English structure."""
    source = canonicalize_tree(copy.deepcopy(source), vector_lesson=vector_lesson)
    target = canonicalize_tree(copy.deepcopy(target), vector_lesson=vector_lesson)
    validate_group(source, target, meta, book_map)
    out = copy.deepcopy(source)
    out["stem"] = legacy_normalize_profile_translation(
        str(source.get("stem") or ""),
        str(target["stem"]),
        meta,
        book_map,
        where=f"{source.get('id', 'group')}.stem",
    )
    out["figures"] = _merge_figures(
        list(source.get("figures") or []), list(target["figures"]), meta, book_map
    )
    out["poems"] = _merge_poems(
        list(source.get("poems") or []),
        list(target.get("poems") or []),
        meta,
        book_map,
        where=f"{source.get('id', 'group')}.poems",
    )
    source_exercises = list(source.get("exercises") or [])
    for source_row, before, after in zip(
        source_exercises, out.get("exercises") or [], target["exercises"]
    ):
        before["text"] = legacy_normalize_profile_translation(
            str(source_row.get("text") or ""),
            str(after["text"]),
            meta,
            book_map,
            where=f"{source_row.get('id', 'exercise')}.text",
        )
        before["options"] = [
            legacy_normalize_profile_translation(
                option_text(source_option),
                str(option),
                meta,
                book_map,
                where=f"{source_row.get('id', 'exercise')}.option",
            )
            for source_option, option in zip(
                source_row.get("options") or [], after["options"]
            )
        ]
        before["answer"] = legacy_normalize_profile_translation(
            str(source_row.get("answer") or ""),
            map_mcq_answer(str(after["answer"]), list(before["options"])),
            meta,
            book_map,
            where=f"{source_row.get('id', 'exercise')}.answer",
        )
        before["figures"] = _merge_figures(
            list(before.get("figures") or []),
            list(after["figures"]),
            meta,
            book_map,
        )
        before["poems"] = _merge_poems(
            list(before.get("poems") or []),
            list(after.get("poems") or []),
            meta,
            book_map,
            where=f"{source_row.get('id', 'exercise')}.poems",
        )
    return canonicalize_tree(out, vector_lesson=vector_lesson)


def document_problems(
    source: dict,
    target: dict,
    meta: dict,
    book_map: dict | None = None,
) -> list[str]:
    source_groups = list(source.get("exercises", {}).get("groups") or [])
    target_groups = list(target.get("exercises", {}).get("groups") or [])
    problems: list[str] = []
    if len(source_groups) != len(target_groups):
        problems.append(
            f"document has {len(target_groups)} translated group(s), "
            f"expected {len(source_groups)}"
        )
    for before, after in zip(source_groups, target_groups):
        problems.extend(group_problems(before, after, meta, book_map))
    return problems


def validate_document(
    source: dict,
    target: dict,
    meta: dict,
    book_map: dict | None = None,
) -> None:
    problems = document_problems(source, target, meta, book_map)
    if problems:
        raise ValueError("; ".join(problems))


def target_document_problems(document: dict, meta: dict) -> list[str]:
    """Validate target-language fields a paper/package reads as student content.

    Figure descriptions are drawing-library keys and prompts, not printed wording
    when papers.py has already confirmed every requested figure is drawn. They may
    legitimately contain bare TikZ/LaTeX notation and are validated by the
    generation/translation and drawing stages instead.
    """
    problems: list[str] = []
    for group in document.get("exercises", {}).get("groups") or []:
        group_id = str(group.get("id") or "group")
        if isinstance(group.get("stem"), str):
            stem = str(group.get("stem") or "")
            problems.extend(legacy_text_problems(stem, stem, f"{group_id}.stem", meta))
        for exercise in group.get("exercises") or []:
            exercise_id = str(exercise.get("id") or "exercise")
            for field in ("text", "answer"):
                value = str(exercise.get(field) or "")
                problems.extend(
                    legacy_text_problems(value, value, f"{exercise_id}.{field}", meta)
                )
            for index, option in enumerate(exercise.get("options") or []):
                value = str(option)
                problems.extend(
                    legacy_text_problems(
                        value, value, f"{exercise_id}.options[{index}]", meta
                    )
                )
    return problems


ENGLISH_IN_ARABIC_MARK = "English prose remains in Arabic translation:"


def split_english_gloss_problems(problems: list[str]) -> tuple[list[str], list[str]]:
    """Separate Arabic-paper audit rows that flag Latin/English words in prose.

    papers.py may treat the gloss rows as a report only when historical etymology
    exercises legitimately name Greek roots in Latin letters.
    """
    gloss: list[str] = []
    blocking: list[str] = []
    for row in problems:
        if ENGLISH_IN_ARABIC_MARK in row:
            gloss.append(row)
        else:
            blocking.append(row)
    return blocking, gloss


def _spec_value_problems(
    source: Any,
    target: Any,
    where: str,
    meta: dict,
    book_map: dict | None = None,
) -> list[str]:
    if isinstance(source, str):
        if not isinstance(target, str):
            return [f"{where}: translated value is not text"]
        strict = where.endswith(".latex") or where.endswith(".notation")
        compared_source = source
        if (
            strict
            and source.strip()
            and not MATH_SEGMENT.fullmatch(source.strip())
            and (
                where.endswith(".latex")
                or symbolic_notation(source)
            )
        ):
            compared_source = f"${source.strip()}$"
        return legacy_text_problems(
            compared_source,
            target,
            where,
            meta,
            math_policy="strict" if strict else "prose",
            book_map=book_map,
        )
    if isinstance(source, dict):
        if not isinstance(target, dict):
            return [f"{where}: translated row is not an object"]
        problems: list[str] = []
        for key, value in source.items():
            if key in STRUCTURAL_SPEC_KEYS:
                continue
            if key not in target:
                problems.append(f"{where}.{key}: translated field is missing")
            else:
                problems.extend(
                    _spec_value_problems(
                        value, target[key], f"{where}.{key}", meta, book_map
                    )
                )
        return problems
    if isinstance(source, list):
        if not isinstance(target, list):
            return [f"{where}: translated value is not a list"]
        problems = []
        if len(source) != len(target):
            problems.append(
                f"{where}: translated {len(target)} item(s), expected {len(source)}"
            )
        for index, (before, after) in enumerate(zip(source, target)):
            problems.extend(
                _spec_value_problems(
                    before, after, f"{where}[{index}]", meta, book_map
                )
            )
        return problems
    return []


def spec_problems(
    source: dict,
    target: dict,
    meta: dict,
    book_map: dict | None = None,
) -> list[str]:
    problems: list[str] = []
    if "overview" not in target:
        problems.append("overview: translated field is missing")
    else:
        problems.extend(
            legacy_text_problems(
                str(source.get("overview") or ""),
                str(target.get("overview") or ""),
                "overview",
                meta,
                math_policy="prose",
                book_map=book_map,
            )
        )
    for field in SPEC_LIST_FIELDS:
        source_rows = list(source.get(field) or [])
        target_rows = target.get(field)
        if not isinstance(target_rows, list):
            problems.append(f"{field}: translated value is not a list")
            continue
        if len(source_rows) != len(target_rows):
            problems.append(
                f"{field}: translated {len(target_rows)} row(s), "
                f"expected {len(source_rows)}"
            )
        for index, (before, after) in enumerate(zip(source_rows, target_rows)):
            where = str(before.get("id") or f"{field}[{index}]")
            if not isinstance(after, dict):
                problems.append(f"{where}: translated row is not an object")
                continue
            if before.get("id") != after.get("id"):
                problems.append(
                    f"{where}: id is {after.get('id')!r}, "
                    f"expected {before.get('id')!r}"
                )
            problems.extend(
                _spec_value_problems(before, after, where, meta, book_map)
            )
    return problems


def validate_spec(
    source: dict,
    target: dict,
    meta: dict,
    book_map: dict | None = None,
) -> None:
    problems = spec_problems(source, target, meta, book_map)
    if problems:
        raise ValueError("; ".join(problems))


def legacy_tikz_problems(
    source: str,
    target: str,
    meta: dict,
    book_map: dict | None = None,
) -> list[str]:
    """Conservative geometry, glyph-language and environment checks for TikZ."""
    problems: list[str] = []
    if book_map:
        try:
            target = normalize_translation_units(
                source, target, book_map, "TikZ"
            )
        except ValueError as error:
            problems.append(str(error))
    if target.count(r"\begin{tikzpicture}") != 1 or target.count(
        r"\end{tikzpicture}"
    ) != 1:
        problems.append("TikZ must contain exactly one tikzpicture environment")
    # Label correction is allowed to move node anchors/coordinates. Geometry
    # outside node statements must retain its exact numeric structure.
    def geometry_numbers(tikz: str) -> list[str]:
        without_nodes = TIKZ_NODE_STATEMENT.sub("", tikz)
        without_label_shifts = TIKZ_LABEL_SHIFT.sub("", without_nodes)
        without_mark_options = TIKZ_LENGTHMARK_OPTIONS.sub(
            r"\1", without_label_shifts
        )
        without_mark_offsets = TIKZ_LENGTHMARK_OFFSET.sub(
            r"\1[]", without_mark_options
        )
        return TIKZ_NUMBER.findall(without_mark_offsets)

    source_numbers = geometry_numbers(source)
    target_numbers = geometry_numbers(target)
    notation = str(meta.get("notation") or "latin")
    if notation == "latin":
        if source_numbers != target_numbers:
            problems.append("TikZ numeric/coordinate sequence changed under latin notation")
    elif sorted(source_numbers) != sorted(target_numbers):
        problems.append("TikZ numeric/coordinate values changed")
    for command in TIKZ_COMMANDS:
        before = len(re.findall(rf"\\{command}\b", source))
        after = len(re.findall(rf"\\{command}\b", target))
        if before != after:
            problems.append(
                f"TikZ \\{command} command count changed from {before} to {after}"
            )
    printed = [
        *TEXT_IN_MATH.findall(target),
        *(match.group(2) for match in TIKZ_NODE.finditer(target)),
    ]
    if str(meta.get("language_code") or meta.get("language") or "") == "ar":
        words = [
            word
            for text in printed
            for word in ASCII_WORD.findall(_prose_for_language_check(text))
            if word.casefold() not in ASCII_ALLOWLIST
        ]
        if words:
            problems.append(
                "English prose remains in translated TikZ: "
                + ", ".join(words[:8])
            )
    for index, text in enumerate(printed):
        problems.extend(_mark_problems(text, f"TikZ label {index + 1}"))
    return problems


def validate_tikz(
    source: str,
    target: str,
    meta: dict,
    book_map: dict | None = None,
) -> None:
    problems = legacy_tikz_problems(source, target, meta, book_map)
    if problems:
        raise ValueError("; ".join(problems))


def _translatable_spec_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _translatable_spec_value(item)
            for key, item in value.items()
            if key not in STRUCTURAL_SPEC_KEYS
        }
    if isinstance(value, list):
        return [_translatable_spec_value(item) for item in value]
    return value


def translatable_spec(spec: dict) -> dict:
    """The ID-addressed printable spec fields the model may translate."""
    projected = {
        "overview": str(spec.get("overview") or ""),
        **{
            field: [
                {
                    "id": str(row.get("id") or ""),
                    **{
                        key: _translatable_spec_value(value)
                        for key, value in row.items()
                        if key not in STRUCTURAL_SPEC_KEYS
                    },
                }
                for row in spec.get(field) or []
            ]
            for field in SPEC_LIST_FIELDS
        },
    }
    for row in projected["equations"]:
        latex = str(row.get("latex") or "").strip()
        if latex and not MATH_SEGMENT.fullmatch(latex):
            row["latex"] = f"${latex}$"
    for row in projected["notations"]:
        notation = str(row.get("notation") or "").strip()
        if symbolic_notation(notation):
            row["notation"] = f"${notation}$"
    return projected


def _overlay_spec_value(
    source: Any,
    target: Any,
    meta: dict,
    book_map: dict | None = None,
    where: str = "spec",
) -> Any:
    if isinstance(source, str):
        return legacy_normalize_profile_translation(
            source, str(target), meta, book_map, where=where
        )
    if isinstance(source, list):
        return [
            _overlay_spec_value(
                before, after, meta, book_map, f"{where}[{index}]"
            )
            for index, (before, after) in enumerate(zip(source, target))
        ]
    if isinstance(source, dict):
        out = copy.deepcopy(source)
        for key, value in source.items():
            if key in STRUCTURAL_SPEC_KEYS:
                continue
            out[key] = _overlay_spec_value(
                value, target[key], meta, book_map, f"{where}.{key}"
            )
        return out
    return copy.deepcopy(source)


def merge_spec(
    source: dict,
    target: dict,
    meta: dict,
    book_map: dict | None = None,
) -> dict:
    """Validate and overlay translated strings while preserving spec structure."""
    vector_lesson = section_is_vector_lesson(source)
    source = canonicalize_tree(copy.deepcopy(source), vector_lesson=vector_lesson)
    target = canonicalize_tree(copy.deepcopy(target), vector_lesson=vector_lesson)
    validate_spec(source, target, meta, book_map)
    out = copy.deepcopy(source)
    out["overview"] = legacy_normalize_profile_translation(
        str(source.get("overview") or ""),
        str(target["overview"]),
        meta,
        book_map,
        where="overview",
    )
    for field in SPEC_LIST_FIELDS:
        translated_by_id = {str(row["id"]): row for row in target[field]}
        out[field] = [
            _overlay_spec_value(
                row,
                translated_by_id[str(row["id"])],
                meta,
                book_map,
                str(row["id"]),
            )
            for row in source.get(field) or []
        ]
    return canonicalize_tree(out, vector_lesson=vector_lesson)


def terminology_context(
    source_spec: dict,
    target_spec: dict,
    terminology: list[dict],
    objective: str,
    book_map: dict | None = None,
) -> dict:
    """Compact section context repeated with one exercise-group call."""
    source_objectives = {
        str(row.get("id") or ""): row
        for row in source_spec.get("objectives") or []
    }
    target_objectives = {
        str(row.get("id") or ""): row
        for row in target_spec.get("objectives") or []
    }
    return {
        "overview": target_spec.get("overview") or "",
        "objective": {
            "id": objective,
            "english": (source_objectives.get(objective) or {}).get("text", ""),
            "arabic": (target_objectives.get(objective) or {}).get("text", ""),
        },
        "terminology": terminology,
        "book_map": book_map or {"entries": [], "semantic_fingerprint": ""},
        "equations": target_spec.get("equations") or [],
        "notations": target_spec.get("notations") or [],
    }
