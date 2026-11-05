"""Form-set instruction lines taken from source_questions for one objective."""

from __future__ import annotations

import re
from collections import Counter

from src.source_exam_headers import _header_line_from_stem, _normalize_header_key

LATEX_TABLE = re.compile(r"\\begin\{(?:array|tabular)")


def _question_form_kind(text: str, options: list | None) -> str:
    if options:
        return "mcq"
    if re.search(r"\\checkmark|\\times|[✓✔✕×]", text or ""):
        return "tf"
    if r"\blank" in (text or ""):
        return "blank"
    return "other"


def _stem_for_catalog(stem: str) -> str:
    """Full stem when it is short; otherwise the first line only."""
    stem = stem.strip()
    if len(stem) <= 320 and "\n" not in stem[:80]:
        return stem
    line = _header_line_from_stem(stem)
    return line or stem.split("\n", 1)[0].strip()


def source_group_stems_for_objective(extraction: dict, objective: str) -> list[str]:
    """Distinct `stem` strings from source groups that touch this objective."""
    seen: set[str] = set()
    ordered: list[str] = []
    for group in extraction.get("questions", {}).get("groups") or []:
        rows = [
            row
            for row in group.get("questions") or []
            if objective in (row.get("objectives") or [])
        ]
        if not rows:
            continue
        stem = (group.get("stem") or "").strip()
        if not stem:
            continue
        key = stem
        if key in seen:
            continue
        seen.add(key)
        ordered.append(stem)
    return ordered


def source_form_stems_for_objective(extraction: dict, objective: str) -> dict[str, str]:
    """Textbook `stem` per form kind (mcq, tf, blank) for this objective — source only."""
    scores: Counter[tuple[str, str]] = Counter()
    display: dict[tuple[str, str], str] = {}

    for group in extraction.get("questions", {}).get("groups") or []:
        rows = [
            row
            for row in group.get("questions") or []
            if objective in (row.get("objectives") or [])
        ]
        if len(rows) < 2:
            continue
        stem = (group.get("stem") or "").strip()
        if not stem:
            continue
        kinds = {
            _question_form_kind(row.get("text") or "", row.get("options"))
            for row in rows
        }
        if len(kinds) != 1:
            continue
        kind = next(iter(kinds))
        if kind == "other":
            continue
        line = _header_line_from_stem(stem)
        if not line:
            continue
        key = _normalize_header_key(line)
        scores[(kind, key)] += len(rows)
        catalog = _stem_for_catalog(stem)
        prior = display.get((kind, key))
        if prior is None or len(catalog) < len(prior):
            display[(kind, key)] = catalog

    chosen: dict[str, str] = {}
    for (kind, key), _count in scores.most_common():
        if kind in chosen:
            continue
        chosen[kind] = display[(kind, key)]
    return chosen
