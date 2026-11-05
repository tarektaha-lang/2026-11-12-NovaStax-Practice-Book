#!/usr/bin/env python3
"""Clean ``<math spoken="...">`` tags in a transcript sentence.

Collapse a doubled tag, drop a leftover ``class`` attribute, delete an orphan
close after the last real tag, and keep punctuation that already follows the
tag outside it. Inside each LaTeX body, undo a sized root index and wrap a
balanced ``( )``, ``[ ]``, or ``\\{ \\}`` pair with ``\\left`` and ``\\right``.

The ``spoken`` reading is left as written. A second pass on the result is a
no-op. Time and space are linear in the sentence length.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NESTED_OPEN = re.compile(
    r"(<math\b[^>]*>)\s*<math\b[^>]*>",
    re.IGNORECASE,
)
_NESTED_CLOSE = re.compile(r"</math>(?:\s*</math>)+", re.IGNORECASE)
_MATH_BLOCK = re.compile(
    r"<math\b([^>]*)>([\s\S]*?)</math>([.,;:!?،؛؟]*)",
    re.IGNORECASE,
)
_SPOKEN = re.compile(r'\bspoken="([^"]*)"', re.IGNORECASE)
_ORPHAN_CLOSE = re.compile(r"</math>", re.IGNORECASE)

_SQRT_SIZED = re.compile(r"\\sqrt\\left\[([^\]]*)\\right\]")
_SQRT_BEFORE_BRACKET = re.compile(r"\\sqrt\s*$")
_ALREADY_SIZED = re.compile(r"\\left$|\\right$")

_CLOSING_PAIRS = frozenset({
    ("(", ")"),
    ("[", "]"),
    ("]", "["),
    ("(", "]"),
    ("[", ")"),
})


@dataclass(frozen=True)
class _Delimiter:
    """One delimiter candidate inside a LaTeX body."""

    start: int
    length: int
    raw: str


def clean_math_tags(text: str) -> str:
    """Apply math-tag cleanup and delimiter sizing to one sentence.

    Args:
        text: A transcript sentence, possibly containing ``<math>`` tags.

    Returns:
        The same sentence with tags collapsed and balanced delimiters sized.
        Punctuation that followed a tag stays after ``</math>``.
    """
    if not text:
        return text
    collapsed = _collapse_nested_math(text)
    parts: list[str] = []
    last = 0
    for match in _MATH_BLOCK.finditer(collapsed):
        spoken = _SPOKEN.search(match.group(1))
        if spoken is None:
            parts.append(collapsed[last:match.end()])
            last = match.end()
            continue
        parts.append(collapsed[last:match.start()])
        latex = _size_delimiters(_repair_sqrt(match.group(2)))
        parts.append(f'<math spoken="{spoken.group(1)}">{latex}</math>{match.group(3) or ""}')
        last = match.end()
    parts.append(_ORPHAN_CLOSE.sub("", collapsed[last:]))
    return "".join(parts)


def _collapse_nested_math(text: str) -> str:
    """Collapse an immediate doubled ``<math>`` open and a run of closes.

    The outer opening tag is kept. Repeats until a further pass changes nothing.
    """
    previous = None
    while previous != text:
        previous = text
        text = _NESTED_OPEN.sub(r"\1", text)
        text = _NESTED_CLOSE.sub("</math>", text)
    return text


def _repair_sqrt(latex: str) -> str:
    """Turn ``\\sqrt\\left[n\\right]`` back into ``\\sqrt[n]``.

    KaTeX rejects a sized root index. The following delimiter pass then leaves
    that index alone.
    """
    return _SQRT_SIZED.sub(r"\\sqrt[\1]", latex)


def _skip_script_argument(latex: str, index: int) -> int:
    """Advance past a ``^{...}``, ``_{...}``, ``^x``, or ``_\\cmd`` argument."""
    pos = index + 1
    if pos >= len(latex):
        return pos
    if latex[pos] == "{":
        depth = 1
        pos += 1
        while pos < len(latex) and depth > 0:
            if latex[pos] == "{":
                depth += 1
            elif latex[pos] == "}":
                depth -= 1
            pos += 1
        return pos
    if latex[pos] == "\\":
        pos += 1
        while pos < len(latex) and latex[pos].isalpha():
            pos += 1
        return pos
    return pos + 1


def _delimiters(latex: str) -> list[_Delimiter]:
    """Delimiter candidates, skipping roots, scripts, and already-sized pairs."""
    found: list[_Delimiter] = []
    index = 0
    while index < len(latex):
        if latex[index] in "^_":
            index = _skip_script_argument(latex, index)
            continue
        if latex[index] == "[" and _SQRT_BEFORE_BRACKET.search(latex[:index]):
            close = latex.find("]", index + 1)
            if close != -1:
                index = close + 1
                continue
        if _ALREADY_SIZED.search(latex[max(0, index - 6):index]):
            index += 1
            continue
        if latex[index] == "\\" and index + 1 < len(latex) and latex[index + 1] in "{}":
            found.append(_Delimiter(index, 2, latex[index:index + 2]))
            index += 2
            continue
        if latex[index] in "()[]":
            found.append(_Delimiter(index, 1, latex[index]))
            index += 1
            continue
        index += 1
    return found


def _closes(open_delim: str, close_delim: str) -> bool:
    """Whether ``close_delim`` is the pair of ``open_delim``."""
    return (open_delim, close_delim) in _CLOSING_PAIRS


def _sides(delimiters: list[_Delimiter]) -> list[str] | None:
    """``left`` or ``right`` for each delimiter, or None when one is unmatched."""
    stack: list[int] = []
    sides: list[str | None] = [None] * len(delimiters)
    for index, item in enumerate(delimiters):
        if item.raw == "\\}":
            top = delimiters[stack[-1]].raw if stack else None
            if top == "\\{":
                sides[index] = "right"
                sides[stack.pop()] = "left"
            else:
                sides[index] = "left"
                stack.append(index)
            continue
        if item.raw == "\\{":
            sides[index] = "left"
            stack.append(index)
            continue
        top = delimiters[stack[-1]].raw if stack else None
        if top and len(top) == 1 and _closes(top, item.raw):
            sides[index] = "right"
            sides[stack.pop()] = "left"
        else:
            sides[index] = "left"
            stack.append(index)
    if stack or any(side is None for side in sides):
        return None
    return [side for side in sides if side is not None]


def _size_delimiters(latex: str) -> str:
    """Wrap each balanced delimiter pair. Leave the body unchanged if any is open.

    Plain ``{ }`` command arguments are never scanned. A root index and the
    argument of ``^`` or ``_`` are skipped, as is a delimiter already preceded
    by ``\\left`` or ``\\right``.
    """
    delimiters = _delimiters(latex)
    if not delimiters:
        return latex
    sides = _sides(delimiters)
    if sides is None:
        return latex
    parts: list[str] = []
    last = 0
    for item, side in zip(delimiters, sides, strict=True):
        parts.append(latex[last:item.start])
        prefix = "\\left" if side == "left" else "\\right"
        parts.append(prefix + item.raw)
        last = item.start + item.length
    parts.append(latex[last:])
    return "".join(parts)
