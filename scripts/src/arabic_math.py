#!/usr/bin/env python3
"""CLS Arabic math letters for Arabic-notation papers.

A formula in the JSON is still written with ordinary Arabic letters. This module
turns those letters into the macros the math face actually draws, and emits the
``\\Umathchardef`` lines the preamble needs so those macros exist.

The grid is the one in CLS ``arabic-math.dtx``: 32 names (the 28 letters plus
dotless beh, noon, feh and qaf) times six styles — isolated ``U+1EE00``, initial
``i`` ``U+1EE20``, tailed ``t`` ``U+1EE40``, stretched ``s`` ``U+1EE60``, looped
``l`` ``U+1EE80``, double-struck ``d`` ``U+1EEA0``. A macro is emitted only when
``fonts/NagwaArabicMath-12-25.otf`` has that codepoint. Isolated heh is empty
and isolated jeem is not the shape a formula uses, so ``ه`` / ``ج`` and
``\\heh`` / ``\\jeem`` are ``\\hehi`` / ``\\jeemi``. An initial form the font
does not have falls back to the isolated letter.
"""

from __future__ import annotations

import re

# CLS order, which is also Unicode's order in the Arabic Mathematical Alphabetic
# Symbols block. Index n of a style sits at that style's start + n.
LETTERS = (
    "alef", "beh", "jeem", "dal", "heh", "waw", "zain", "hah", "tah", "yeh",
    "kaf", "lam", "meem", "noon", "seen", "ain", "feh", "sad", "qaf", "reh",
    "sheen", "teh", "theh", "khah", "thal", "dad", "zah", "ghain",
    "dotlessbeh", "dotlessnoon", "dotlessfeh", "dotlessqaf",
)
_LETTER_NAMES = frozenset(LETTERS)

STYLE_STARTS = (
    ("", 0x1EE00),
    ("i", 0x1EE20),
    ("t", 0x1EE40),
    ("s", 0x1EE60),
    ("l", 0x1EE80),
    ("d", 0x1EEA0),
)

# Codepoints of the 32×6 grid that NagwaArabicMath-12-25.otf actually draws.
# tests/test_arabic_notation_rules.py checks this set against the font cmap.
FONT_MATH_CODEPOINTS = frozenset({
    0x1EE00, 0x1EE01, 0x1EE02, 0x1EE03, 0x1EE05, 0x1EE06, 0x1EE07, 0x1EE08,
    0x1EE09, 0x1EE0A, 0x1EE0B, 0x1EE0C, 0x1EE0D, 0x1EE0E, 0x1EE0F, 0x1EE10,
    0x1EE11, 0x1EE12, 0x1EE13, 0x1EE14, 0x1EE15, 0x1EE16, 0x1EE17, 0x1EE18,
    0x1EE19, 0x1EE1A, 0x1EE1B, 0x1EE1C, 0x1EE1D, 0x1EE1E, 0x1EE1F, 0x1EE21,
    0x1EE22, 0x1EE24, 0x1EE27, 0x1EE29, 0x1EE2A, 0x1EE2B, 0x1EE2C, 0x1EE2D,
    0x1EE2E, 0x1EE2F, 0x1EE30, 0x1EE31, 0x1EE32, 0x1EE34, 0x1EE35, 0x1EE36,
    0x1EE37, 0x1EE39, 0x1EE3B, 0x1EE42, 0x1EE47, 0x1EE49, 0x1EE4B, 0x1EE4D,
    0x1EE4E, 0x1EE4F, 0x1EE51, 0x1EE52, 0x1EE54, 0x1EE57, 0x1EE59, 0x1EE5B,
    0x1EE5D, 0x1EE5F, 0x1EE61, 0x1EE62, 0x1EE64, 0x1EE67, 0x1EE68, 0x1EE69,
    0x1EE6A, 0x1EE6C, 0x1EE6D, 0x1EE6E, 0x1EE6F, 0x1EE70, 0x1EE71, 0x1EE72,
    0x1EE74, 0x1EE75, 0x1EE76, 0x1EE77, 0x1EE79, 0x1EE7A, 0x1EE7B, 0x1EE7C,
    0x1EE7E, 0x1EE80, 0x1EE81, 0x1EE82, 0x1EE83, 0x1EE84, 0x1EE85, 0x1EE86,
    0x1EE87, 0x1EE88, 0x1EE89, 0x1EE8B, 0x1EE8C, 0x1EE8D, 0x1EE8E, 0x1EE8F,
    0x1EE90, 0x1EE91, 0x1EE92, 0x1EE93, 0x1EE94, 0x1EE95, 0x1EE96, 0x1EE97,
    0x1EE98, 0x1EE99, 0x1EE9A, 0x1EE9B, 0x1EEA1, 0x1EEA2, 0x1EEA3, 0x1EEA5,
    0x1EEA6, 0x1EEA7, 0x1EEA8, 0x1EEA9, 0x1EEAB, 0x1EEAC, 0x1EEAD, 0x1EEAE,
    0x1EEAF, 0x1EEB0, 0x1EEB1, 0x1EEB2, 0x1EEB3, 0x1EEB4, 0x1EEB5, 0x1EEB6,
    0x1EEB7, 0x1EEB8, 0x1EEB9, 0x1EEBA, 0x1EEBB,
})

# Number sets stay ``\mathbb{X}`` in the JSON. The typesetter prints the Arabic
# letter the Egyptian textbook uses. Tailed hah, sad and noon exist. Tailed tah
# (U+1EE48) and tailed kaf (U+1EE4A) do not: the book prints natural numbers as
# ط and the complex set as ك, so those two are the isolated letters.
NUMBER_SETS = {
    "R": "haht",
    "Z": "sadt",
    "Q": "noont",
    "N": "tah",
    "C": "kaf",
}
_MATHBB = re.compile(r"\\mathbb\{([A-Za-z])\}")
# Complex operators stay ``\operatorname{Re}`` / ``\operatorname{حقيقي}`` in
# JSON. The typesetter prints textbook abbreviations. ``ت`` is the imaginary
# unit, so the imaginary part is ``تخ``. ``Arg`` is ``سعة``.
_COMPLEX_OPERATORS = (
    (re.compile(r"\\(?:operatorname|mathrm)\s*\{\s*Re\s*\}|\\Re(?![A-Za-z])"), r"\\text{ح}"),
    (re.compile(r"\\(?:operatorname|mathrm)\s*\{\s*Im\s*\}|\\Im(?![A-Za-z])"), r"\\text{تخ}"),
    (re.compile(r"\\(?:operatorname|mathrm)\s*\{\s*Arg\s*\}|\\arg(?![A-Za-z])"), r"\\text{سعة}"),
    (re.compile(r"\\operatorname\s*\{\s*حقيقي\s*\}"), r"\\text{ح}"),
    (re.compile(r"\\operatorname\s*\{\s*تخيي?لي\s*\}"), r"\\text{تخ}"),
    (re.compile(r"\\operatorname\s*\{\s*سعة\s*\}"), r"\\text{سعة}"),
)
TATWEEL = "\u0640"
TAA_MARBUTA = "ة"
QURAN_OPEN = "\uFD3F"
QURAN_CLOSE = "\uFD3E"

# Ordinary Arabic letters as the model writes them, including the hamza-alefs a
# textbook uses for the first vertex, all sharing alef's slot.
LETTER_TO_NAME = {
    "ا": "alef", "أ": "alef", "إ": "alef", "آ": "alef",
    "ب": "beh",
    "ج": "jeem",
    "د": "dal",
    "ه": "heh",
    "و": "waw",
    "ز": "zain",
    "ح": "hah",
    "ط": "tah",
    "ي": "yeh",
    "ك": "kaf",
    "ل": "lam",
    "م": "meem",
    "ن": "noon",
    "س": "seen",
    "ع": "ain",
    "ف": "feh",
    "ص": "sad",
    "ق": "qaf",
    "ر": "reh",
    "ش": "sheen",
    "ت": "teh",
    "ث": "theh",
    "خ": "khah",
    "ذ": "thal",
    "ض": "dad",
    "ظ": "zah",
    "غ": "ghain",
    "ى": "yeh",
    "ئ": "yeh",
    "ء": "ain",
    "ؤ": "waw",
}

# Letters that join into a word, so a run ending in taa marbuta is prose
# (``المساحة``, ``ساعة``) rather than a string of variables.
WORD_LETTERS = frozenset(LETTER_TO_NAME) | {
    TAA_MARBUTA, "ى", "ء", "ؤ", "ئ", TATWEEL,
}

# Vocalisation marks that belong in prose, not as maths atoms (fatha through sukun).
TASHKEEL = frozenset(chr(code) for code in range(0x064B, 0x0660)) | {"\u0670"}
FATHA = "\u064e"

# Letters whose isolated math glyph is wrong or missing, so a default variable
# is the initial form. Jeem's isolated shape is not the textbook letter; heh's
# isolated slot is empty.
INITIAL_DEFAULT = frozenset({"heh", "jeem"})

# Two-letter abbreviations that print as one textbook math glyph, not as a
# product of isolated letters. ``نق`` is Arabic Ray (U+0608), the radius.
SYMBOLS = (
    ("نق", "arrad", 0x0608),
)

# Unicode letter → the mathematical glyph it prints as. Used as a safety net in
# the preamble for a letter the walker did not rewrite.
MATH_LETTER_MAP = {
    "ا": 0x1EE00, "أ": 0x1EE00, "إ": 0x1EE00, "آ": 0x1EE00,
    "ب": 0x1EE01,
    "ج": 0x1EE22,
    "د": 0x1EE03,
    "ه": 0x1EE24,
    "و": 0x1EE05,
    "ز": 0x1EE06,
    "ح": 0x1EE07,
    "ط": 0x1EE08,
    "ي": 0x1EE09,
    "ك": 0x1EE0A,
    "ل": 0x1EE0B,
    "م": 0x1EE0C,
    "ن": 0x1EE0D,
    "س": 0x1EE0E,
    "ع": 0x1EE0F,
    "ف": 0x1EE10,
    "ص": 0x1EE11,
    "ق": 0x1EE12,
    "ر": 0x1EE13,
    "ش": 0x1EE14,
    "ت": 0x1EE15,
    "ث": 0x1EE16,
    "خ": 0x1EE17,
    "ذ": 0x1EE18,
    "ض": 0x1EE19,
    "ظ": 0x1EE1A,
    "غ": 0x1EE1B,
    "ى": 0x1EE09,
    "ئ": 0x1EE09,
    "ء": 0x1EE0F,
    "ؤ": 0x1EE05,
}


def _is_tex_letter(char: str) -> bool:
    """TeX's letters: A–Z a–z, not Unicode isalpha (which includes Arabic)."""
    return "A" <= char <= "Z" or "a" <= char <= "z"


def letter_macros() -> list[tuple[str, int]]:
    """(command, codepoint) for every grid glyph the math font actually has."""
    out: list[tuple[str, int]] = []
    for suffix, start in STYLE_STARTS:
        for index, name in enumerate(LETTERS):
            code = start + index
            if code not in FONT_MATH_CODEPOINTS:
                continue
            out.append((f"{name}{suffix}", code))
    return out


def _style_of(macro: str) -> tuple[str, str]:
    """``hehi`` → ``(heh, i)``. A name with no style suffix stays isolated."""
    for suffix in ("i", "t", "s", "l", "d"):
        if macro.endswith(suffix) and macro[: -len(suffix)] in _LETTER_NAMES:
            return macro[: -len(suffix)], suffix
    return macro, ""


_INITIAL_READY = frozenset(
    base for macro, _code in letter_macros() for base, style in (_style_of(macro),) if style == "i"
)


def macros_tex() -> str:
    """\\Umathchardef lines for every isolated and initial letter the paper may write."""
    lines = [
        f'\\Umathchardef\\{name}="7 "0 "{code:04X}'
        for name, code in letter_macros()
    ]
    lines.extend(
        f'\\Umathchardef\\{name}="7 "0 "{code:04X}'
        for _, name, code in SYMBOLS
    )
    lines.append(r"\let\heh=\hehi")
    lines.append(r"\let\jeem=\jeemi")
    return "\n".join(lines) + "\n"


def mathcode_lua() -> str:
    """A Lua table mapping each ordinary Arabic letter to its mathematical glyph."""
    pairs = ",".join(
        f"[{ord(letter)}]=0x{code:X}" for letter, code in MATH_LETTER_MAP.items()
    )
    return f"local isolated = {{{pairs}}}"


def wrap_quran_quotes(body: str) -> str:
    """Wrap a Quranic quotation ``﴿...﴾`` in a formula body as ``\\text``.

    A verse in a table cell is words, not variables, and the maths face has no
    glyph for the ornate parentheses. Braces inside would stop latex.py's
    TEXT_IN_MATHS from seeing the ``\\text``, so ``\\blank{}`` loses its ``{}``.
    """
    out: list[str] = []
    index = 0
    while True:
        start = body.find(QURAN_OPEN, index)
        end = body.find(QURAN_CLOSE, start + 1) if start >= 0 else -1
        if end < 0:
            out.append(body[index:])
            return "".join(out)
        before = body[index:start]
        if before.endswith("\\text{"):
            out.append(body[index : end + 1])
        else:
            quote = body[start : end + 1].replace("\\blank{}", "\\blank ")
            out.append(f"{before}\\text{{{quote}}}")
        index = end + 1


def _balanced_brace_end(text: str, open_at: int) -> int | None:
    """Index just past the ``}`` that closes the group at ``open_at``, or None."""
    if open_at >= len(text) or text[open_at] != "{":
        return None
    depth = 0
    index = open_at
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def wrap_taa_marbuta_words(body: str) -> str:
    """Wrap a run of Arabic letters that ends in taa marbuta as ``\\text``.

    ``المساحة`` is a word, not a product of variables, and taa marbuta has no
    mathematical glyph. Existing ``\\text{...}`` is left as it is; a slash or
    equals breaks a run, so ``كلم/ساعة`` wraps only ``ساعة``. Nested braces
    inside ``\\text`` (``\\text{… ($^\\circ\\text{س}$)}``) are skipped whole.
    """
    out: list[str] = []
    index = 0
    length = len(body)
    while index < length:
        if body.startswith("\\text{", index):
            close_at = _balanced_brace_end(body, index + 5)
            if close_at is None:
                out.append(body[index:])
                break
            out.append(body[index:close_at])
            index = close_at
            continue
        char = body[index]
        if char == "\\":
            next_at = index + 1
            if next_at < length and not _is_tex_letter(body[next_at]):
                out.append(body[index : next_at + 1])
                index = next_at + 1
                continue
            while next_at < length and _is_tex_letter(body[next_at]):
                next_at += 1
            out.append(body[index:next_at])
            index = next_at
            continue
        if char in WORD_LETTERS:
            start = index
            index += 1
            while index < length and body[index] in WORD_LETTERS:
                index += 1
            run = body[start:index]
            if run.endswith(TAA_MARBUTA):
                out.append(f"\\text{{{run}}}")
            else:
                out.append(run)
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _symbol_at(body: str, index: int) -> tuple[str, int] | None:
    """A two-letter textbook symbol at ``index``, or None.

    ``نق`` is one radius glyph when the next character is not another Arabic
    letter, so ``نق_1`` and ``2نق`` match and a longer run is left to the
    letter walker.
    """
    for letters, name, _code in SYMBOLS:
        end = index + len(letters)
        if not body.startswith(letters, index):
            continue
        if end < len(body) and body[end] in LETTER_TO_NAME:
            continue
        return name, end
    return None


def fold_math_alef(body: str) -> str:
    """``أ`` / ``إ`` / ``آ`` outside ``\\text`` become ``ا``. The caller has already split ``\\text``."""
    return body.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")


def replace_number_sets(body: str) -> str:
    """``\\mathbb{R}^+`` becomes ``\\haht^+``. An unknown set letter is left as written."""

    def repl(found: re.Match[str]) -> str:
        name = NUMBER_SETS.get(found.group(1))
        if name is None:
            return found.group(0)
        return f"\\{name}"

    return _MATHBB.sub(repl, body)


def replace_real_imag_parts(body: str) -> str:
    """``\\operatorname{Re}`` / ``\\operatorname{حقيقي}`` / ``Arg`` become ``\\text`` names."""
    for pattern, replacement in _COMPLEX_OPERATORS:
        body = pattern.sub(replacement, body)
    return body


def _letter_command(name: str, *, initial: bool) -> str:
    """The macro for one letter. A missing initial glyph falls back to isolated."""
    if (initial or name in INITIAL_DEFAULT) and name in _INITIAL_READY:
        return f"\\{name}i "
    return f"\\{name} "


def _qaf_measures_angle(body: str, index: int) -> bool:
    """True when ``ق`` at this point names an angle: ``ق\\angle`` or ``ق(\\angle…``."""
    pos = index
    length = len(body)
    while pos < length and body[pos] in " \t":
        pos += 1
    if body.startswith("\\angle", pos):
        end = pos + 6
        return end == length or not _is_tex_letter(body[end])
    if body.startswith("\\left", pos):
        pos += 5
        while pos < length and body[pos] in " \t":
            pos += 1
    if pos < length and body[pos] in "([":
        inner = pos + 1
        while inner < length and body[inner] in " \t":
            inner += 1
        if body.startswith("\\angle", inner):
            end = inner + 6
            return end == length or not _is_tex_letter(body[end])
    return False


def letters_for_tex(body: str) -> str:
    """Rewrite Arabic letters in a formula body as CLS macros.

    ``اب`` becomes ``\\alef \\beh``. A letter followed by tatweel that joins
    the next letter takes the initial form, and the tatweel is dropped:
    ``هـو`` → ``\\hehi \\waw``. A dangling tatweel (nothing joins after it)
    prints the isolated letter (``عـ`` → ``\\ain``), except letters in
    ``INITIAL_DEFAULT`` (``جـ``, ``هـ``), which stay initial. A fatha on a
    single maths letter outside ``\\text`` is a prime: ``سَ`` → ``\\seen '``.
    Bare ``ه`` is ``\\hehi`` and bare ``ج`` is ``\\jeemi``. ``نق`` is
    ``\\arrad``. Letters inside ``\\text{...}`` are copied unchanged. Each
    macro is closed with a space so a following Arabic letter is not read as
    part of the command name. TeX commands are copied so a second pass leaves
    ``\\alef`` and ``\\arrad`` alone. Stray tatweel is dropped.
    """
    out: list[str] = []
    index = 0
    length = len(body)
    while index < length:
        char = body[index]
        if char == "\\":
            next_at = index + 1
            if next_at < length and not _is_tex_letter(body[next_at]):
                out.append(body[index : next_at + 1])
                index = next_at + 1
                continue
            while next_at < length and _is_tex_letter(body[next_at]):
                next_at += 1
            command = body[index + 1 : next_at]
            if command in {"text", "operatorname"} and next_at < length and body[next_at] == "{":
                close_at = _balanced_brace_end(body, next_at)
                if close_at is not None:
                    out.append(body[index:close_at])
                    index = close_at
                    continue
            if command in INITIAL_DEFAULT and command in _INITIAL_READY:
                out.append(f"\\{command}i ")
            else:
                out.append(body[index:next_at])
            index = next_at
            continue
        symbol = _symbol_at(body, index)
        if symbol is not None:
            name, end = symbol
            out.append(f"\\{name} ")
            index = end
            continue
        name = LETTER_TO_NAME.get(char)
        if name is not None:
            index += 1
            had_tatweel = False
            while index < length and body[index] == TATWEEL:
                had_tatweel = True
                index += 1
            primed = False
            while index < length and body[index] in TASHKEEL:
                if body[index] == FATHA:
                    primed = True
                index += 1
            joins_next = index < length and body[index] in LETTER_TO_NAME
            initial = had_tatweel and joins_next
            # A space ends the control word. Babel gives Arabic letters catcode
            # 11, so ``\hahة`` would otherwise be one undefined command.
            if name == "qaf" and _qaf_measures_angle(body, index):
                out.append("\\dotlessqaft ")
            else:
                out.append(_letter_command(name, initial=initial))
            if primed:
                out.append("'")
            continue
        if char == TATWEEL:
            index += 1
            continue
        if char in TASHKEEL:
            index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out)
