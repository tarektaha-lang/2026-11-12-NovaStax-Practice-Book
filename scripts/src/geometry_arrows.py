"""Canonical geometric-ray vs vector commands in stored mathematics.

Egyptian geometry writes a ray (including a secant or tangent ray) as
``\\overrightarrow``. A true vector, of one letter or two, is ``\\vec`` and is
printed as a harpoon. Models often store the ray as ``\\vec``; this module
rewrites that token when the object is a ray, and leaves vector lessons alone.
"""

from __future__ import annotations

import re
from typing import Any


VEC_OPEN = r"\vec{"
RAY_COMMAND = r"\overrightarrow{"

RAY_MEANING = re.compile(
    r"\brays?\b|شعاع|originating at|starting at endpoint|"
    r"secant ray|tangent ray|يمر بالنقطة|يبدأ من",
    re.IGNORECASE,
)
RAY_BEFORE = re.compile(
    r"(?:rays?|شعاع(?:ان|ات|ين)?|secant|tangent|قاطع|مماس)\s*(?:\$$)?\s*$",
    re.IGNORECASE,
)
MEMBERSHIP_BEFORE = re.compile(r"\\in\s*$")
VECTOR_LANGUAGE = re.compile(
    r"unit\s+vectors?|\bvectors?\b|متجه(?:ات|ان|ين)?(?:\s*الوحدة)?",
    re.IGNORECASE,
)

# Fingerprints, path stems, and call metadata are not printable mathematics.
SKIP_KEYS = frozenset(
    {
        "id",
        "book_id",
        "fingerprint",
        "input_fingerprint",
        "semantic_fingerprint",
        "book_map_fingerprint",
        "map_projection_fingerprint",
        "model",
        "thinking_level",
        "prompt_files",
        "schema_version",
    }
)


def section_is_vector_lesson(document: dict[str, Any] | None) -> bool:
    """Whether title, overview, or glossary names vectors as the topic."""
    if not isinstance(document, dict):
        return False
    spec = document.get("spec") if isinstance(document.get("spec"), dict) else document
    parts = [
        str(document.get("title") or ""),
        str(spec.get("overview") or "") if isinstance(spec, dict) else "",
    ]
    if isinstance(spec, dict):
        for row in spec.get("glossary") or []:
            if isinstance(row, dict):
                parts.append(str(row.get("term") or ""))
                parts.append(str(row.get("definition") or ""))
    return bool(VECTOR_LANGUAGE.search("\n".join(parts)))


def is_ray_meaning(text: str) -> bool:
    """A spec meaning (or equivalent) that names a geometric ray."""
    return bool(RAY_MEANING.search(text or ""))


def _vec_spans(text: str) -> list[tuple[int, int, str]]:
    """Start, end, and brace body of each ``\\vec{...}`` with balanced braces."""
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    while True:
        start = text.find(VEC_OPEN, cursor)
        if start < 0:
            return spans
        brace = start + len(VEC_OPEN) - 1
        depth = 0
        index = brace
        while index < len(text):
            char = text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    spans.append((start, index + 1, text[brace + 1 : index]))
                    cursor = index + 1
                    break
            index += 1
        else:
            return spans


def _is_ray_occurrence(text: str, start: int) -> bool:
    prefix = text[max(0, start - 80) : start]
    if MEMBERSHIP_BEFORE.search(prefix):
        return True
    return bool(RAY_BEFORE.search(prefix))


def canonicalize_geometry_arrows(text: str, *, vector_lesson: bool = False) -> str:
    """Rewrite ray ``\\vec{...}`` to ``\\overrightarrow{...}``.

    In a vector lesson only occurrences that are named as rays (or a point on a
    ray) are rewritten, so ``\\vec{a}`` and ``\\vec{AB}`` stay vectors. Elsewhere
    every ``\\vec`` is treated as a mis-tagged ray.
    """
    if not text or VEC_OPEN not in text:
        return text
    pieces: list[str] = []
    cursor = 0
    changed = False
    for start, stop, body in _vec_spans(text):
        rewrite = (not vector_lesson) or _is_ray_occurrence(text, start)
        pieces.append(text[cursor:start])
        if rewrite:
            pieces.append(f"{RAY_COMMAND}{body}}}")
            changed = True
        else:
            pieces.append(text[start:stop])
        cursor = stop
    if not changed:
        return text
    pieces.append(text[cursor:])
    return "".join(pieces)


def _contexts_are_ray(value: dict[str, Any]) -> bool:
    meaning = value.get("meaning")
    if isinstance(meaning, str) and is_ray_meaning(meaning):
        return True
    contexts = value.get("contexts")
    if isinstance(contexts, list):
        return any(is_ray_meaning(str(item)) for item in contexts)
    return False


def canonicalize_tree(value: Any, *, vector_lesson: bool = False) -> Any:
    """Rewrite printable strings in a JSON-shaped value."""
    if isinstance(value, str):
        return canonicalize_geometry_arrows(value, vector_lesson=vector_lesson)
    if isinstance(value, list):
        return [canonicalize_tree(item, vector_lesson=vector_lesson) for item in value]
    if isinstance(value, dict):
        notation = value.get("notation")
        if isinstance(notation, str) and _contexts_are_ray(value):
            return {
                key: (
                    canonicalize_geometry_arrows(item, vector_lesson=False)
                    if key == "notation" and isinstance(item, str)
                    else canonicalize_tree(item, vector_lesson=vector_lesson)
                )
                for key, item in value.items()
            }
        if value.get("kind") == "symbol" and _contexts_are_ray(value):
            math_keys = {"source_math", "math_text"}
            return {
                key: (
                    item
                    if key in SKIP_KEYS
                    else canonicalize_geometry_arrows(item, vector_lesson=False)
                    if key in math_keys and isinstance(item, str)
                    else canonicalize_tree(item, vector_lesson=False)
                )
                for key, item in value.items()
            }
        return {
            key: (
                item
                if key in SKIP_KEYS
                else canonicalize_tree(item, vector_lesson=vector_lesson)
            )
            for key, item in value.items()
        }
    return value


def canonicalize_document(document: Any) -> Any:
    """Rewrite a section (or book) document using its own vector-lesson test."""
    if not isinstance(document, dict):
        return canonicalize_tree(document, vector_lesson=False)
    sections = document.get("sections")
    if isinstance(sections, list):
        out = dict(document)
        out["sections"] = [canonicalize_document(section) for section in sections]
        return out
    return canonicalize_tree(
        document, vector_lesson=section_is_vector_lesson(document)
    )
