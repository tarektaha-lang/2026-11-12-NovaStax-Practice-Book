"""Repair LaTeX that a single JSON backslash turned into a control character.

The repair is in memory. It never writes a file and never changes an Arabic word.
A control is restored only when the letters after it are a known command, or, for
a NUL, when it is the stray delimiter left in front of ``$``.
"""

from __future__ import annotations

import json
import re
from typing import Any


# First letter is the JSON escape that swallowed the backslash: t f b r n.
# A NUL keeps the command name whole (``\u0000cos``), so those names are here too.
LATEX_COMMANDS = frozenset(
    {
        "bar",
        "because",
        "begin",
        "beta",
        "binom",
        "blank",
        "boldsymbol",
        "boxed",
        "cos",
        "cot",
        "csc",
        "dfrac",
        "frac",
        "nabla",
        "ne",
        "neg",
        "neq",
        "ncong",
        "ngeq",
        "ni",
        "nleq",
        "nmid",
        "not",
        "notin",
        "nparallel",
        "nsim",
        "nsubseteq",
        "nu",
        "rightarrow",
        "Rightarrow",
        "right",
        "sec",
        "sin",
        "tan",
        "text",
        "textbf",
        "textit",
        "textrm",
        "tfrac",
        "therefore",
        "theta",
        "times",
        "triangle",
    }
)
_ESCAPE_LETTER = {"\t": "t", "\f": "f", "\b": "b", "\r": "r", "\n": "n"}
_LETTERS = re.compile(r"[A-Za-z]+")
# Tab is included: ``\times`` / ``\theta`` / ``\text`` / ``\triangle`` decode as it.
# Newline and carriage return are real line breaks unless they spell a command.
DETECT_CONTROL = re.compile(r"[\x00-\x09\x0b\x0c\x0e-\x1f]")
_VISIBLE = {
    "\t": "<TAB>",
    "\n": "<NL>",
    "\r": "<CR>",
    "\f": "<FF>",
    "\b": "<BS>",
    "\x00": "<NUL>",
    "\v": "<VT>",
}


def _show(text: str) -> str:
    return "".join(_VISIBLE.get(char, char) for char in text)


def _window(text: str, start: int, stop: int) -> str:
    return _show(text[max(0, start - 18) : stop + 18])


def repair_english_string(value: str) -> tuple[str, list[tuple[str, str, str]]]:
    """Return the repaired text and one ``(before, after, status)`` hit per control.

    ``repaired`` means a known command or a NUL sitting on ``$``. ``refused`` means
    a control that is still there because restoring it would be a guess.
    """
    hits: list[tuple[str, str, str]] = []
    pieces: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\x00" and index + 1 < len(value) and value[index + 1] == "$":
            before = _window(value, index, index + 1)
            after = _window(value[:index] + value[index + 1 :], index, index)
            hits.append((before, after, "repaired"))
            index += 1
            continue
        prefix = _ESCAPE_LETTER.get(char)
        nul = char == "\x00"
        if prefix is not None or nul:
            letters = _LETTERS.match(value, index + 1)
            name = ((prefix or "") + letters.group(0)) if letters else ""
            if name in LATEX_COMMANDS:
                consumed = 1 + len(name) - len(prefix or "")
                # A newline before ``e.`` / ``i.`` / ``u.`` is a list item, not
                # ``\ne``, ``\ni``, or ``\nu``.
                if char == "\n" and name in {"ne", "ni", "nu"} and letters:
                    rest = value[index + consumed :]
                    if (
                        len(letters.group(0)) == 1
                        and rest.startswith(".")
                    ) or (
                        name == "ni"
                        and len(letters.group(0)) == 1
                        and re.match(r"\s+[a-z]", rest)
                    ):
                        pieces.append(char)
                        index += 1
                        continue
                replacement = "\\" + name
                # ``^\times`` is a lost ``^\circ``: the same files write angle
                # labels as ``120^\circ`` beside ``120^<TAB>imes``.
                if name == "times" and index > 0 and value[index - 1] == "^":
                    replacement = r"\circ"
                before = _window(value, index, index + consumed)
                after = _window(
                    value[:index] + replacement + value[index + consumed :],
                    index,
                    index + len(replacement),
                )
                hits.append((before, after, "repaired"))
                pieces.append(replacement)
                index += consumed
                continue
        pieces.append(char)
        index += 1
    text = "".join(pieces)
    for found in DETECT_CONTROL.finditer(text):
        window = _window(text, found.start(), found.end())
        hits.append((window, window, "refused"))
    return text, hits


def repair_english_tree(document: Any) -> tuple[Any, list[dict[str, str]]]:
    """Repair every string. Hits name the field. The document on disk is untouched."""
    hits: list[dict[str, str]] = []

    def walk(value: Any, field: str) -> Any:
        if isinstance(value, str):
            fixed, local = repair_english_string(value)
            for before, after, status in local:
                hits.append(
                    {
                        "field": field or "<root>",
                        "before": before,
                        "after": after,
                        "status": status,
                    }
                )
            return fixed
        if isinstance(value, dict):
            return {
                key: walk(item, f"{field}.{key}" if field else str(key))
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [
                walk(item, f"{field}[{index}]")
                for index, item in enumerate(value)
            ]
        return value

    return walk(document, ""), hits


def english_control_error(source: str, hits: list[dict[str, str]]) -> str:
    """The message for controls that were not restored. Empty when all were."""
    refused = [hit for hit in hits if hit["status"] == "refused"]
    if not refused:
        return ""
    lines = [
        f"{source}: {hit['field']}: {hit['before']} is not a known LaTeX command"
        for hit in refused
    ]
    return "; ".join(lines)


def read_english_json(path) -> tuple[Any, list[dict[str, str]]]:
    """Read English JSON and repair lost LaTeX backslashes in memory only.

    Raises ``ValueError`` before any caller can send the text to a model when a
    control character is not a known command or a NUL delimiter. The file is
    not rewritten.
    """
    from pathlib import Path

    file = Path(path)
    document = json.loads(file.read_text(encoding="utf-8"))
    repaired, hits = repair_english_tree(document)
    message = english_control_error(str(file), hits)
    if message:
        raise ValueError(message)
    return repaired, hits


def load_english_json(path) -> Any:
    """The repaired document from :func:`read_english_json`."""
    document, _hits = read_english_json(path)
    return document


def format_english_hits(book: str, path, hits: list[dict[str, str]]) -> list[str]:
    """One ``book | file | field | before | after | status`` line per control."""
    from pathlib import Path

    name = Path(path).name
    return [
        f"{book} | {name} | {hit['field']} | {hit['before']} | {hit['after']} | {hit['status']}"
        for hit in hits
    ]


def dump_english_json(document: Any) -> str:
    """JSON text for a future English build, with known commands restored.

    Refusing an unsafe control raises, so the caller does not write the file.
    """
    repaired, hits = repair_english_tree(document)
    message = english_control_error("english json", hits)
    if message:
        raise ValueError(message)
    return json.dumps(repaired, ensure_ascii=False, indent=2) + "\n"
