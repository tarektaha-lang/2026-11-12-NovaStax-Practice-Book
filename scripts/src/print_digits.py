"""Canonical math islands for digits printed in Arabic exercise content."""

from __future__ import annotations

import re


MATH_SEGMENT = re.compile(r"\$\$.+?\$\$|\$.+?\$", re.DOTALL)
FIGURE_TOKEN = re.compile(r"\[figure [0-9٠-٩]+\]")
POEM_TOKEN = re.compile(r"\[poem [0-9٠-٩]+\]")
TECHNICAL_COMMAND = re.compile(
    r"\\(?:hspace\*?|vspace\*?|rule|raisebox|kern|mskip|hskip)"
    r"(?:\s*\[[^\]]*\])?(?:\s*\{[^{}]*\})+"
)
_PLAIN_NUMBER = r"[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
PRINT_NUMBER = re.compile(
    r"(?<![A-Za-z0-9\\])(?P<number>\d{1,2}:\d{2}(?::\d{2})?(?!\d)|"
    + _PLAIN_NUMBER
    + r"\s*:\s*"
    + _PLAIN_NUMBER
    + r"|"
    + _PLAIN_NUMBER
    + r")(?P<percent>\\%|%)?(?![A-Za-z0-9])"
)
# ``\node … {label}`` and path ``node [opt] {label}`` (not preceded by a letter).
# The body is the last top-level ``{...}`` before ``;``, with nested braces
# (``$\frac{3\pi}{2}$``) counted, so a first-``}`` regex cannot cut the label.
TIKZ_NODE_HEAD = re.compile(r"(?<![A-Za-z])node\b")
TIKZ_NODE_STATEMENT = re.compile(r"(?<![A-Za-z])node\b[^;]*;", re.DOTALL)


class TikzNodeMatch:
    """One ``node … {body}`` span, with the same groups a regex match would have."""

    __slots__ = ("_groups", "_start", "_end")

    def __init__(self, prefix: str, body: str, suffix: str, start: int, end: int) -> None:
        self._groups = (prefix + body + suffix, prefix, body, suffix)
        self._start = start
        self._end = end

    def group(self, number: int = 0) -> str:
        return self._groups[number]

    def __getitem__(self, number: int) -> str:
        return self._groups[number]

    def start(self, number: int = 0) -> int:
        if number == 0:
            return self._start
        if number == 1:
            return self._start
        if number == 2:
            return self._start + len(self._groups[1])
        return self._end - len(self._groups[3])

    def end(self, number: int = 0) -> int:
        if number == 0:
            return self._end
        if number == 1:
            return self._start + len(self._groups[1])
        if number == 2:
            return self._end - len(self._groups[3])
        return self._end


def iter_tikz_nodes(tikz: str):
    """``node`` commands whose printed body is a balanced ``{...}`` group."""
    for head in TIKZ_NODE_HEAD.finditer(tikz):
        index = head.end()
        depth_brace = depth_brack = depth_paren = 0
        group_start: int | None = None
        last_group: tuple[int, int] | None = None
        while index < len(tikz):
            char = tikz[index]
            if char == "{" and depth_brack == 0 and depth_paren == 0:
                if depth_brace == 0:
                    group_start = index
                depth_brace += 1
            elif char == "}" and depth_brack == 0 and depth_paren == 0:
                if depth_brace:
                    depth_brace -= 1
                    if depth_brace == 0 and group_start is not None:
                        last_group = (group_start, index + 1)
            elif char == "[" and depth_brace == 0 and depth_paren == 0:
                depth_brack += 1
            elif char == "]" and depth_brace == 0 and depth_paren == 0:
                depth_brack = max(0, depth_brack - 1)
            elif char == "(" and depth_brace == 0 and depth_brack == 0:
                depth_paren += 1
            elif char == ")" and depth_brace == 0 and depth_brack == 0:
                depth_paren = max(0, depth_paren - 1)
            elif (
                char == ";"
                and depth_brace == 0
                and depth_brack == 0
                and depth_paren == 0
            ):
                break
            index += 1
        if last_group is None:
            continue
        open_at, close_at = last_group
        yield TikzNodeMatch(
            tikz[head.start() : open_at + 1],
            tikz[open_at + 1 : close_at - 1],
            tikz[close_at - 1 : close_at],
            head.start(),
            close_at,
        )


class TikzNodePattern:
    """``finditer`` / ``sub`` over balanced TikZ node labels."""

    def finditer(self, tikz: str):
        return iter_tikz_nodes(tikz)

    def sub(self, repl, tikz: str) -> str:
        pieces: list[str] = []
        cursor = 0
        for found in iter_tikz_nodes(tikz):
            pieces.append(tikz[cursor : found.start()])
            pieces.append(repl(found) if callable(repl) else repl)
            cursor = found.end()
        pieces.append(tikz[cursor:])
        return "".join(pieces)


TIKZ_NODE = TikzNodePattern()
# Grade/year/class numbers are wording (Grade 7 → الصف السابع), not quantities.
GRADE_WORDING = re.compile(r"(?i)(?:grade|year|class|level)\s+\d+")


def _protected_spans(text: str) -> list[tuple[int, int]]:
    spans = [
        *(match.span() for match in MATH_SEGMENT.finditer(text)),
        *(match.span() for match in FIGURE_TOKEN.finditer(text)),
        *(match.span() for match in POEM_TOKEN.finditer(text)),
        *(match.span() for match in TECHNICAL_COMMAND.finditer(text)),
        *(match.span() for match in GRADE_WORDING.finditer(text)),
    ]
    if not spans:
        return []
    merged: list[tuple[int, int]] = []
    for start, stop in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(stop, merged[-1][1]))
        else:
            merged.append((start, stop))
    return merged


def _wrap(chunk: str) -> str:
    def one(found: re.Match[str]) -> str:
        number = found.group("number").replace("−", "-")
        percent = r"\%" if found.group("percent") else ""
        return f"${number}{percent}$"

    return PRINT_NUMBER.sub(one, chunk)


def normalize_print_digits(text: str) -> str:
    """Wrap every reader-visible Western digit run outside existing mathematics."""
    pieces: list[str] = []
    cursor = 0
    for start, stop in _protected_spans(text):
        pieces.append(_wrap(text[cursor:start]))
        pieces.append(text[start:stop])
        cursor = stop
    pieces.append(_wrap(text[cursor:]))
    return "".join(pieces)


def bare_print_digits(text: str) -> list[str]:
    """Reader-visible digit runs that still sit outside mathematics."""
    found: list[str] = []
    cursor = 0
    for start, stop in _protected_spans(text):
        found.extend(match.group(0) for match in PRINT_NUMBER.finditer(text[cursor:start]))
        cursor = stop
    found.extend(match.group(0) for match in PRINT_NUMBER.finditer(text[cursor:]))
    return found


# Arabic-Indic / Eastern Arabic digits in PGF math are not operators.
_PGF_DIGIT = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)


def _western_pgf_digits(chunk: str) -> str:
    """ASCII digits and ``.`` in TikZ geometry / PGF expressions, never labels."""
    return chunk.translate(_PGF_DIGIT).replace("٫", ".")


def normalize_tikz_print_digits(tikz: str) -> str:
    """Wrap digits in printed node bodies; keep PGF/coords in Western digits."""
    pieces: list[str] = []
    cursor = 0
    for found in TIKZ_NODE.finditer(tikz):
        pieces.append(_western_pgf_digits(tikz[cursor : found.start()]))
        pieces.append(
            _western_pgf_digits(found.group(1))
            + normalize_print_digits(found.group(2))
            + found.group(3)
        )
        cursor = found.end()
    pieces.append(_western_pgf_digits(tikz[cursor:]))
    return "".join(pieces)
