"""This year's academic year on every book's table of contents.

Every book must carry academic_year="26-27" this year. When a table of contents
does not, the tool writes that value onto the <book> element and records the
edit in a log at the repo root. A book already on 26-27 is left untouched.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_ACADEMIC_YEAR = "26-27"

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
INPUT_DIR = PROJECT_ROOT / "data" / "input"
LOG_PATH = PROJECT_ROOT / "academic_year_edits.txt"

BOOK_OPEN_TAG = re.compile(r"(<book\b)([^>]*?)(/?>)", re.DOTALL)
YEAR_ATTR = re.compile(r"""\bacademic_year\s*=\s*(["'])(.*?)(\1)""", re.DOTALL)

_lock = threading.Lock()


def toc_path(book_id: str) -> Path:
    """The table of contents the year is stored on."""
    return INPUT_DIR / book_id / f"{book_id}.xml"


def apply_required_year(
    xml_text: str, required: str = REQUIRED_ACADEMIC_YEAR
) -> tuple[str, str, bool]:
    """Rewrite the <book> academic_year if it is not this year's value.

    Returns the XML, the year that was on the tag (empty if it had none), and
    whether the text changed. Only the opening <book> tag is touched, so the
    rest of the table of contents keeps its own spacing and order.
    """
    match = BOOK_OPEN_TAG.search(xml_text)
    if match is None:
        raise RuntimeError("table of contents has no <book> element")

    prefix, attrs, closer = match.group(1), match.group(2), match.group(3)
    year_match = YEAR_ATTR.search(attrs)
    if year_match is not None:
        old = year_match.group(2).strip()
        if old == required:
            return xml_text, old, False
        quote = year_match.group(1)
        new_attrs = (
            attrs[: year_match.start()]
            + f"academic_year={quote}{required}{quote}"
            + attrs[year_match.end() :]
        )
    else:
        old = ""
        spacer = "" if (not attrs or attrs[-1].isspace()) else " "
        new_attrs = f'{attrs}{spacer}academic_year="{required}"'

    new_tag = f"{prefix}{new_attrs}{closer}"
    return xml_text[: match.start()] + new_tag + xml_text[match.end() :], old, True


def _append_log(book_id: str, path: Path, old_year: str, log_path: Path) -> None:
    """Append one edit to the repo log, creating the file on the first write."""
    relative = path
    try:
        relative = path.relative_to(PROJECT_ROOT)
    except ValueError:
        pass
    was = old_year if old_year else "(missing)"
    when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    block = (
        "The tool modified academic_year for this year.\n"
        f"when: {when}\n"
        f"book: {book_id}\n"
        f"file: {relative.as_posix()}\n"
        f"academic_year: {was} -> {REQUIRED_ACADEMIC_YEAR}\n"
        "\n"
    )
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(block)


def ensure_academic_year(
    book_id: str,
    *,
    toc: Path | None = None,
    log_path: Path | None = None,
) -> str | None:
    """Set this book's academic_year to 26-27 if it is not already.

    Returns the previous year when the XML was rewritten, or None when the
    book was already on this year. The edit is logged at academic_year_edits.txt
    in the tool repo.

    Args:
        book_id: Folder name of the book under data/input.
        toc: Table of contents to edit; omitted, the book's usual XML.
        log_path: Log to append; omitted, the repo-root log file.
    """
    path = toc if toc is not None else toc_path(book_id)
    if not path.is_file():
        raise RuntimeError(f"table of contents not found: {path}")
    destination = log_path if log_path is not None else LOG_PATH

    with _lock:
        text = path.read_text(encoding="utf-8")
        new_text, old_year, changed = apply_required_year(text)
        if not changed:
            return None
        path.write_text(new_text, encoding="utf-8")
        _append_log(book_id, path, old_year, destination)

    print(
        f"academic_year: {book_id} was {old_year or '(missing)'}; "
        f"the tool set it to {REQUIRED_ACADEMIC_YEAR} for this year "
        f"(logged in {destination.name})",
        flush=True,
    )
    return old_year
