"""Exam instruction headers taken from source_questions (07_section_source_questions).

Group stems are what the MoE / external books print once above a block of questions.
Those strings feed curriculum-review prompts so reviewers compare practice books to the
actual command forms the sources use, not a hard-coded Egypt/Algeria list.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import sources

QUESTIONS_STAGE = "07_section_source_questions"

# Drop numbering clauses so "Choose … for questions (1)–(4):" merges with "Choose …:"
_QUESTION_RANGE = re.compile(
    r"\s+for\s+(?:the\s+)?questions?\s*.+$",
    re.I,
)
_TRAILING_COLON = re.compile(r"\s*:?\s*$")


def _normalize_header_key(line: str) -> str:
    """Key for counting variants of the same printed instruction."""
    key = " ".join(line.split()).strip()
    key = _QUESTION_RANGE.sub("", key)
    key = key.rstrip(":").strip().lower()
    return key


def _header_line_from_stem(stem: str) -> str | None:
    line = stem.split("\n", 1)[0].strip()
    if not line:
        return None
    if len(line) > 220:
        return None
    return line


def _groups_from_section(document: dict) -> list[dict]:
    block = document.get("questions")
    if isinstance(block, dict):
        return block.get("groups") or []
    return document.get("groups") or []


def collect_source_exam_headers(
    book_id: str,
    *,
    limit: int = 40,
) -> list[tuple[str, int]]:
    """Distinct exam headers from a book's source-question extracts, by frequency."""
    stage_dir = sources.OUTPUT_DIR / book_id / QUESTIONS_STAGE
    if not stage_dir.is_dir():
        return []

    key_to_display: dict[str, str] = {}
    counts: Counter[str] = Counter()

    for path in sorted(stage_dir.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for group in _groups_from_section(document):
            stem = (group.get("stem") or "").strip()
            if not stem:
                continue
            display = _header_line_from_stem(stem)
            if not display:
                continue
            key = _normalize_header_key(display)
            if not key:
                continue
            counts[key] += 1
            if key not in key_to_display or len(display) < len(key_to_display[key]):
                key_to_display[key] = display.rstrip(":").strip() + (
                    ":" if display.rstrip().endswith(":") else ""
                )

    ranked = counts.most_common(limit)
    return [(key_to_display[key], count) for key, count in ranked if key in key_to_display]


def format_exam_header_examples(
    book_id: str,
    *,
    limit: int = 40,
) -> str:
    """Comma-separated header list for prompts, or a fallback when sources are missing."""
    all_headers = collect_source_exam_headers(book_id, limit=10_000)
    if not all_headers:
        return (
            "(no headers yet — run source_questions.py so "
            f"{QUESTIONS_STAGE}/ has section JSON)"
        )
    shown = all_headers[:limit]
    lines = [display for display, _ in shown]
    if len(all_headers) > limit:
        lines.append("…")
    return ", ".join(lines)
