#!/usr/bin/env python3
"""Write the book's working JSON and overlaid bboxes PDF.

Generates `data/output/<book_id>/<book_id>.json` (the working JSON with printed
groups keyed by bound PDF page, coordinates in top_left, and question tap MD5s)
and stamps question boxes onto `data/output/<book_id>/<book_id>_bboxes.pdf`.
Individual question JSONs are cut and named by their MD5 in `13_question_jsons/`.

    python3 scripts/groups.py mathematics_ar_sa06_t2
    python3 scripts/groups.py mathematics_ar_sa06_t2 -s 1.1
    python3 scripts/groups.py mathematics_ar_sa06_t2 --no-pdf
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    import frontmatter
    import src.latex as latex
    import papers
    from cover import read_wording
    from src.prompting import Profile
    from pypdf import PageObject, PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject
    from questions_review import accepted_sections
    from sources import OUTPUT_DIR, PROJECT_ROOT, working_json
except ModuleNotFoundError as error:  # pragma: no cover
    sys.exit(f"missing dependency ({error.name}): pip install -r requirements.txt")

# Visual bounding box colors: Group union in red; stem and parts in blue.
GROUP_COLOR = (0.80, 0.16, 0.16)
PART_COLOR = (0.16, 0.40, 0.80)
STROKE_PT = 0.7

SP_PER_PT = 65536.0
BOX_PAD = 3.0
# How far the box rises above the first baseline. The wash is included.
# A stem is Noto Naskh Arabic: vocalised lines and an inline fraction reach
# about 13.5pt, and the pad sits above that. A set cell is maths. Rising one
# body size plus the pad clears the parentheses and still leaves a gap above
# the next row; giving the cells the Arabic rise lays the stem's box on them.
STEM_RISE = 13.5 + BOX_PAD
CELL_RISE = 12.0 + BOX_PAD
MARK_LINE = re.compile(
    r"^(B|E)\s+(\d+)\s+(\S+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+([LR])\s*$"
)


@dataclass(frozen=True)
class Box:
    """One rectangle on one page of a section paper, in TeX points, origin lower-left."""

    page: int
    x: float
    y: float
    w: float
    h: float
    group_id: str = ""


@dataclass(frozen=True)
class Mark:
    """One savepos TeX wrote as a question began or ended."""

    kind: str
    page: int
    qid: str
    x: float
    y: float
    width: float
    direction: str


def profile_of(document: dict) -> Profile:
    """The book's profile as the exercises document records it."""
    return Profile.of(
        {
            "language_code": document["language_code"],
            "numerals": document["numerals"],
            "notation": document["notation"],
            "country_code": document.get("country_code"),
        }
    )


def sp_to_pt(value: int) -> float:
    """Scaled TeX points from a \\savepos integer."""
    return value / SP_PER_PT


def parse_bbox(path: Path) -> list[Mark]:
    """The begin/end marks of one paper, ignoring lines the engine wrapped or dropped."""
    marks: list[Mark] = []
    if not path.is_file():
        return marks
    for line in path.read_text(errors="replace").splitlines():
        found = MARK_LINE.match(line.strip())
        if found is None:
            continue
        marks.append(
            Mark(
                kind=found.group(1),
                page=int(found.group(2)),
                qid=found.group(3),
                x=sp_to_pt(int(found.group(4))),
                y=sp_to_pt(int(found.group(5))),
                width=sp_to_pt(int(found.group(6))),
                direction=found.group(7),
            )
        )
    return marks


def pair_marks(marks: list[Mark]) -> list[tuple[Mark, Mark]]:
    """Each question's begin mark with the end mark that follows it."""
    pending: dict[str, Mark] = {}
    pairs: list[tuple[Mark, Mark]] = []
    for mark in marks:
        if mark.kind == "B":
            pending[mark.qid] = mark
            continue
        start = pending.pop(mark.qid, None)
        if start is None:
            continue
        pairs.append((start, mark))
    return pairs


def horizontal_box(start: Mark) -> tuple[float, float]:
    """Left and right of the hover box, number included, clamped to the text block.

    The mark sits on the outer edge of the cell (left in LTR, right in RTL) and the
    recorded width is already the cell, number and wording together. The start-edge
    pad follows the writing direction. A further BOX_PAD is then taken on the
    visual left, Arabic and English the same, without moving the right.
    """
    width = max(start.width, 1.0)
    if start.direction == "R":
        right = min(latex.PAGE_WIDTH - latex.PAGE_MARGIN + BOX_PAD, start.x + BOX_PAD)
        left = max(latex.PAGE_MARGIN - BOX_PAD, right - width)
    else:
        left = max(latex.PAGE_MARGIN - BOX_PAD, start.x - BOX_PAD)
        right = min(latex.PAGE_WIDTH - latex.PAGE_MARGIN + BOX_PAD, left + width)
    left = max(0.0, left - BOX_PAD)
    if right <= left:
        right = left + width
    return left, right


def anchored(start: Mark, end: Mark) -> bool:
    """Whether the start mark is the first baseline.

    A stem's id is the group's, and \\qidline puts that mark on the baseline.
    A set cell's end mark is inside the parbox, so the two marks do not share
    an x, and the start mark is on the row baseline. A list's marks are both
    in vertical mode and share an x; that start is the top of the block.
    """
    if start.qid.startswith("group_"):
        return True
    return abs(start.x - end.x) > 0.5


def one_line(start: Mark, end: Mark) -> bool:
    """A drop shorter than the leading is one line's depth, not another line."""
    return start.page == end.page and (start.y - end.y) < latex.BODY_LEADING


def row_floors(pairs: list[tuple[Mark, Mark]]) -> dict[tuple[int, float], float]:
    """The lowest end mark of each one-line row, so the row shares one bottom.

    Cells on one row share a start baseline and differ only by how far their
    own glyphs hang. Hanging each box on its own end mark shifts it.
    """
    floors: dict[tuple[int, float], float] = {}
    for start, end in pairs:
        if not anchored(start, end) or not one_line(start, end):
            continue
        key = (start.page, round(start.y, 2))
        floors[key] = min(floors.get(key, end.y), end.y)
    return floors


def vertical_span(
    start: Mark, end: Mark, page: int, floor: float | None = None
) -> tuple[float, float]:
    """Bottom and top of the box on one PDF page, in TeX's bottom-left points."""
    text_bottom = latex.PAGE_MARGIN - BOX_PAD
    text_top = latex.PAGE_HEIGHT - latex.PAGE_MARGIN + BOX_PAD
    if anchored(start, end):
        # The end mark is under the last line's depth. The top is the first
        # baseline plus a fixed rise, so cells on one row share it.
        rise = STEM_RISE if start.qid.startswith("group_") else CELL_RISE
        top_of_line = start.y + rise
        if start.page == end.page:
            lowest = end.y if floor is None or not one_line(start, end) else floor
            top, bottom = top_of_line, lowest - BOX_PAD
        elif page == start.page:
            top, bottom = top_of_line, text_bottom
        elif page == end.page:
            top, bottom = text_top, end.y - BOX_PAD
        else:
            top, bottom = text_top, text_bottom
    else:
        # A list's start mark is already the top of the block.
        start_top = start.y + BOX_PAD
        end_bottom = end.y - BOX_PAD
        if start.page == end.page:
            top = max(start_top, end.y + latex.BODY_SIZE + BOX_PAD)
            bottom = min(end_bottom, start.y - BOX_PAD)
        elif page == start.page:
            top, bottom = start_top, text_bottom
        elif page == end.page:
            top, bottom = text_top, end_bottom
        else:
            top, bottom = text_top, text_bottom
    if top - bottom < latex.BODY_SIZE:
        top = bottom + latex.BODY_SIZE + 2 * BOX_PAD
    top = min(text_top, max(top, text_bottom + 1))
    bottom = max(text_bottom, min(bottom, top - 1))
    return bottom, top


def boxes_from_marks(path: Path) -> dict[str, list[Box]]:
    """TeX-point boxes keyed by the id written into the .bbox file."""
    marks = parse_bbox(path)
    if not marks:
        return {}

    base_page = 1
    tex_path = path.with_suffix(".tex")
    stamp = latex.page_stamp(tex_path)
    min_mark_page = min(m.page for m in marks)
    if stamp and stamp[0] <= min_mark_page:
        base_page = stamp[0]
    else:
        base_page = min_mark_page

    raw_boxes: list[Box] = []
    pairs = pair_marks(marks)
    floors = row_floors(pairs)
    for start, end in pairs:
        floor = floors.get((start.page, round(start.y, 2)))
        for page in range(start.page, end.page + 1):
            x0, x1 = horizontal_box(start)
            bottom, top = vertical_span(start, end, page, floor)
            raw_boxes.append(
                Box(
                    page=page - base_page,
                    x=x0,
                    y=bottom,
                    w=x1 - x0,
                    h=top - bottom,
                    group_id=start.qid,
                )
            )
    by_qid: dict[str, list[Box]] = defaultdict(list)
    for b in raw_boxes:
        by_qid[b.group_id].append(b)
    return dict(by_qid)


def pdf_quad(box: Box, page: Any) -> list[float]:
    """`box` as `[x0, y0, x1, y1]` in the PDF page's own points, origin top-left."""
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)
    sx = width / latex.PAGE_WIDTH
    sy = height / latex.PAGE_HEIGHT
    x0 = box.x * sx
    y_lower = box.y * sy
    x1 = (box.x + box.w) * sx
    y_upper = (box.y + box.h) * sy
    y0 = height - y_upper
    y1 = height - y_lower
    return [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)]


def pdf_quads(
    boxes: list[Box], reader: PdfReader, page: int | None = None
) -> list[list[float]]:
    """PDF-point quads for boxes on `page` of `reader` (or every in-range page)."""
    quads: list[list[float]] = []
    for box in boxes:
        if page is not None and box.page != page:
            continue
        if box.page < 0 or box.page >= len(reader.pages):
            continue
        quads.append(pdf_quad(box, reader.pages[box.page]))
    return quads


def union_boxes(boxes: list[Box]) -> list[Box]:
    """One box per page covering every box that page holds."""
    by_page: dict[int, list[Box]] = defaultdict(list)
    for box in boxes:
        by_page[box.page].append(box)
    united: list[Box] = []
    for page in sorted(by_page):
        items = by_page[page]
        x0 = min(item.x for item in items)
        y0 = min(item.y for item in items)
        x1 = max(item.x + item.w for item in items)
        y1 = max(item.y + item.h for item in items)
        united.append(Box(page=page, x=x0, y=y0, w=x1 - x0, h=y1 - y0))
    return united


def group_boxes(group: dict, by_qid: dict[str, list[Box]]) -> list[Box]:
    """Every TeX-point box belonging to a printed group."""
    exercises = list(group.get("exercises") or [])
    if len(exercises) <= 1:
        eid = latex.tex_id((exercises[0] or {}).get("id") if exercises else "")
        return list(by_qid.get(eid) or [])
    boxes = list(by_qid.get(latex.tex_id(group.get("id"))) or [])
    for exercise in exercises:
        boxes.extend(by_qid.get(latex.tex_id(exercise.get("id"))) or [])
    return boxes


def pages_of(boxes: list[Box]) -> list[int]:
    """0-based section pages that hold a piece of this group, from its marks."""
    return sorted({box.page for box in boxes})


def nested_bboxes(
    group: dict,
    by_qid: dict[str, list[Box]],
    reader: PdfReader,
    page: int | None = None,
) -> list[dict[str, Any]]:
    """The working-JSON `bboxes` payload for one printed group on one PDF page."""
    exercises = list(group.get("exercises") or [])
    if len(exercises) <= 1:
        eid = latex.tex_id((exercises[0] or {}).get("id") if exercises else "")
        boxes = by_qid.get(eid) or []
        quads = pdf_quads(boxes, reader, page)
        parts = [{eid: quads}] if eid else []
        return [{"group": quads, "stem": [], "exercises": parts}]

    gid = latex.tex_id(group.get("id"))
    stem_boxes = by_qid.get(gid) or []
    all_boxes = list(stem_boxes)
    parts: list[dict[str, list[list[float]]]] = []
    for exercise in exercises:
        eid = latex.tex_id(exercise.get("id"))
        part_boxes = by_qid.get(eid) or []
        all_boxes.extend(part_boxes)
        parts.append({eid: pdf_quads(part_boxes, reader, page)})
    return [
        {
            "group": pdf_quads(union_boxes(all_boxes), reader, page),
            "stem": pdf_quads(stem_boxes, reader, page),
            "exercises": parts,
        }
    ]


def missing_mark_ids(group: dict, by_qid: dict[str, list[Box]]) -> list[str]:
    """Ids this group needed in the .bbox file and did not have."""
    exercises = list(group.get("exercises") or [])
    wanted: list[str] = []
    if len(exercises) <= 1:
        if exercises:
            wanted.append(latex.tex_id(exercises[0].get("id")))
    else:
        wanted.append(latex.tex_id(group.get("id")))
        wanted.extend(latex.tex_id(exercise.get("id")) for exercise in exercises)
    return [qid for qid in wanted if qid and not by_qid.get(qid)]


def bboxes_payload(raw: object) -> dict[str, Any]:
    """The nested bboxes object, from the new shape or a legacy list of quads."""
    if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "group" in raw[0]:
        row = raw[0]
        return {
            "group": list(row.get("group") or []),
            "stem": list(row.get("stem") or []),
            "exercises": list(row.get("exercises") or []),
        }
    quads: list[list[float]] = []
    for item in raw or []:
        if isinstance(item, dict) and "box" in item:
            quads.append(list(item["box"]))
        elif isinstance(item, (list, tuple)) and len(item) == 4:
            quads.append([float(item[0]), float(item[1]), float(item[2]), float(item[3])])
    return {"group": quads, "stem": [], "exercises": []}


def overlay_page(
    width: float,
    height: float,
    boxes: list[tuple[float, float, float, float]],
    color: tuple[float, float, float],
) -> PageObject:
    """A blank page that strokes `boxes` in PDF points, origin lower-left."""
    r, g, b = color
    commands = [
        "q",
        f"{r:.3f} {g:.3f} {b:.3f} RG",
        f"{STROKE_PT:.2f} w",
    ]
    for x, y, w, h in boxes:
        commands.append(f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re")
        commands.append("S")
    commands.append("Q")
    stream = DecodedStreamObject()
    stream.set_data(("\n".join(commands) + "\n").encode("latin-1"))
    page = PageObject.create_blank_page(width=width, height=height)
    page[NameObject("/Contents")] = stream
    return page


def stamp_quads(
    page: Any, quads: list[list[float]], color: tuple[float, float, float]
) -> None:
    """Paint top-left `[x0, y0, x1, y1]` quads onto one PDF page (lower-left canvas)."""
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)
    rects: list[tuple[float, float, float, float]] = []
    for quad in quads:
        if len(quad) != 4:
            continue
        x0, y0, x1, y1 = (float(value) for value in quad)
        left = min(x0, x1)
        right = max(x0, x1)
        canvas_bottom = height - max(y0, y1)
        canvas_top = height - min(y0, y1)
        rects.append((left, canvas_bottom, right - left, canvas_top - canvas_bottom))
    if not rects:
        return
    page.merge_page(overlay_page(width, height, rects, color), over=True)


def paint_group(page: Any, group: dict) -> int:
    """Stroke one group's stored quads. Returns how many rectangles were painted."""
    payload = bboxes_payload(group.get("bboxes"))
    painted = 0
    group_quads = payload["group"]
    stamp_quads(page, group_quads, GROUP_COLOR)
    painted += len(group_quads)
    if group.get("kind") == "exercise":
        return painted
    stamp_quads(page, payload["stem"], PART_COLOR)
    painted += len(payload["stem"])
    for row in payload["exercises"]:
        if not isinstance(row, dict):
            continue
        for quads in row.values():
            stamp_quads(page, list(quads or []), PART_COLOR)
            painted += len(quads or [])
    return painted


def overlay_section(
    pdf_path: Path,
    document: dict,
    book_pages: dict[str, list[dict]],
    offset: int,
) -> tuple[PdfReader, int]:
    """A section PDF with the working-JSON boxes drawn on it."""
    reader = PdfReader(pdf_path)
    painted = 0
    by_page: dict[int, list[dict]] = defaultdict(list)
    for key, groups in book_pages.items():
        try:
            book_page = int(key)
        except (TypeError, ValueError):
            continue
        local = book_page - offset - 1
        if local < 0 or local >= len(reader.pages):
            continue
        for group in groups:
            if group.get("section") != document["number"]:
                continue
            by_page[local].append(group)
    for index, page in enumerate(reader.pages):
        for group in by_page.get(index, []):
            painted += paint_group(page, group)
    print(
        f"  {document['number']}: {painted} box(es) on {len(reader.pages)} page(s)",
        flush=True,
    )
    return reader, painted


def front_pdfs(book_id: str, profile: Profile) -> list[tuple[Path, str]]:
    """Cover, title, copyright and contents as they stand on disk."""
    heading = frontmatter.contents_heading(profile, read_wording())
    bound = [
        (frontmatter.page_path(book_id, frontmatter.COVER), ""),
        (frontmatter.page_path(book_id, frontmatter.TITLE_PAGE), ""),
        (frontmatter.page_path(book_id, frontmatter.COPYRIGHT), ""),
        (frontmatter.page_path(book_id, frontmatter.CONTENTS), heading),
    ]
    if not all(path.is_file() for path, _ in bound):
        return []
    return bound


def write_bboxes_pdf(
    book_id: str,
    section: str | None,
    book: dict[str, Any],
    *,
    papers_dir: Path | None = None,
    out_dir: Path | None = None,
) -> Path:
    """Overlay every section in scope and bind the book as <book_id>_bboxes.pdf."""
    book_pages = book.get("pages") or {}
    documents = accepted_sections(book_id, None)
    if not documents:
        raise RuntimeError(f"no sections to box for {book_id}")
    if section and not any(document["number"] == section for _, document in documents):
        raise RuntimeError(f"no section {section} in {book_id}")
    papers_dir = papers_dir or OUTPUT_DIR / book_id / papers.PAPERS_STAGE
    out_dir = out_dir or OUTPUT_DIR / book_id
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = profile_of(documents[0][1])
    scoped = [
        (stem, document)
        for stem, document in documents
        if section is None or document["number"] == section
    ]
    print(
        f"boxing {len(scoped)} section(s) of {book_id}",
        flush=True,
    )

    writer = PdfWriter()
    front = front_pdfs(book_id, profile) if section is None else []
    if section is None:
        if front:
            for pdf, bookmark in front:
                writer.append(pdf, bookmark or None)
        else:
            print("no front matter; boxing the papers alone", flush=True)

    boxed = 0
    last_stem = scoped[-1][0]
    # JSON keys are bound-book pages: front.pages + section-local page.
    # Offset here must be that same page count, not the number of front-matter
    # files — contents can run to more than one page.
    offset = len(writer.pages)
    if offset == 0:
        fm = frontmatter.front_matter(book_id)
        if fm is not None:
            offset = fm.pages
    for stem, document in documents:
        pdf_path = papers_dir / f"{stem}.pdf"
        pages = len(PdfReader(pdf_path).pages) if pdf_path.is_file() else 0
        if section is None or document["number"] == section:
            if not pdf_path.is_file():
                print(f"  {document['number']}: no paper PDF; skipped", flush=True)
            else:
                overlaid, _ = overlay_section(pdf_path, document, book_pages, offset)
                writer.append(overlaid, f"{document['number']}. {document['title']}")
                boxed += 1
                last_stem = stem
        offset += pages

    if not boxed:
        raise RuntimeError(f"no section PDFs under {papers_dir}")
    name = f"{last_stem}_bboxes.pdf" if section else f"{book_id}_bboxes.pdf"
    out_path = out_dir / name
    writer.write(out_path)
    writer.close()
    print(f"wrote {out_path.relative_to(PROJECT_ROOT)} ({boxed} paper(s))", flush=True)
    return out_path

# Book-level fields copied off the first exercises document. Digits and notation
# stay here because later stages assemble prompts from them; they are not restated
# on every group.
BOOK_FIELDS = (
    "book_id",
    "subject",
    "grade",
    "term",
    "language_code",
    "language",
    "academic_year",
    "numerals",
    "notation",
    "country_code",
    "country",
)

# Keys transcripts.py and audios.py write onto a group. Rebuilding the JSON
# from the papers must not wipe a transcript or the audio already stored.
KEEP_ON_GROUP = ("transcript", "transcript_generation", "audio")

COORDINATE_SYSTEM = {
    "type": "absolute",
    "origin": "top_left",
    "format": "x0,y0,x1,y1",
}


def load_json(path: Path) -> dict[str, Any]:
    """Read an object from `path`, or raise with the file named."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise RuntimeError(f"cannot read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid JSON in {path}: {error}") from error
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} is not a JSON object")
    return data


def iter_stored_groups(book: dict[str, Any]) -> list[dict[str, Any]]:
    """Every group stored in a working JSON, whatever shape an older run used."""
    pages = book.get("pages")
    if isinstance(pages, dict):
        return [group for key in sorted(pages) for group in pages[key]]
    if isinstance(pages, list):
        found: list[dict[str, Any]] = []
        for item in pages:
            if not isinstance(item, dict):
                continue
            for groups in item.values():
                found.extend(groups)
        return found
    return list(book.get("groups") or [])


def existing_transcripts(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Transcripts already stored, keyed by (section number, printed group id)."""
    if not path.is_file():
        return {}
    kept: dict[tuple[str, str], dict[str, Any]] = {}
    for group in iter_stored_groups(load_json(path)):
        section = group.get("section")
        group_id = group.get("id")
        if not section or not group_id:
            continue
        payload = {key: group[key] for key in KEEP_ON_GROUP if key in group}
        if payload:
            kept[(section, group_id)] = payload
    return kept


def previous_by_section(path: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """Stored groups keyed by section number, then printed group id."""
    if not path.is_file():
        return {}
    by_section: dict[str, dict[str, dict[str, Any]]] = {}
    for group in iter_stored_groups(load_json(path)):
        section = group.get("section")
        group_id = group.get("id")
        if section and group_id:
            by_section.setdefault(section, {})[group_id] = group
    return by_section


def page_count(pdf_path: Path) -> int:
    """How many pages a section PDF runs to."""
    if not pdf_path.is_file():
        raise RuntimeError(f"no paper PDF at {pdf_path}")
    return len(PdfReader(pdf_path).pages)


def page_key(number: int) -> str:
    """A 4-digit page label, matching the 1-based page number in the bound PDF."""
    return f"{number:04d}"


def exercise_ids(group: dict) -> list[str]:
    """The exercise ids of one group, in the order the section stores them."""
    return [str(exercise.get("id") or "") for exercise in group.get("exercises") or []]


def groups_in_selection(document: dict, printed_ids: list[str]) -> list[dict]:
    """The section's groups whose exercises the paper printed, in reading order.

    The selection is the list papers.py wrote. A group is printed when every
    exercise it holds is in that list, and left out when none of them are. A
    list that divides a group, names an exercise the section does not have, or
    repeats an id does not describe the paper.
    """
    number = str(document.get("number") or "")
    rank = {exercise_id: index for index, exercise_id in enumerate(printed_ids)}
    if len(rank) != len(printed_ids):
        raise RuntimeError(f"{number}: selection repeats a printed id")
    wanted = set(rank)
    kept: list[dict] = []
    seen: set[str] = set()
    for group in latex.section_groups(document):
        ids = exercise_ids(group)
        if not ids:
            continue
        if any(not exercise_id for exercise_id in ids):
            raise RuntimeError(f"{number}: a group has an exercise with no id")
        chosen = [exercise_id for exercise_id in ids if exercise_id in wanted]
        if not chosen:
            continue
        if len(chosen) != len(ids):
            raise RuntimeError(
                f"{number}: selection splits group {group.get('id')}"
            )
        for exercise_id in ids:
            if exercise_id in seen:
                raise RuntimeError(
                    f"{number}: exercise {exercise_id} is in two groups"
                )
            seen.add(exercise_id)
        kept.append(group)
    missing = [exercise_id for exercise_id in printed_ids if exercise_id not in seen]
    if missing:
        raise RuntimeError(
            f"{number}: selection prints {missing[0]}, which is not in the section"
        )
    kept.sort(key=lambda group: min(rank[exercise_id] for exercise_id in exercise_ids(group)))
    return kept


def marks_agree(
    number: str, groups: list[dict], by_qid: dict[str, list[Box]], pdf_pages: int
) -> None:
    """Every printed piece has a mark on a page of the PDF, and no mark is spare.

    The .bbox file is the page and the box TeX recorded when it shipped the
    paper. A printed exercise with no mark, a mark for an exercise the
    selection did not print, or a mark off the end of the PDF is a paper that
    does not match its record.
    """
    missing = [
        qid for group in groups for qid in missing_mark_ids(group, by_qid)
    ]
    if missing:
        raise RuntimeError(
            f"{number}: no .bbox mark for {', '.join(missing)}; re-run papers.py"
        )
    allowed: set[str] = set()
    for group in groups:
        allowed.add(latex.tex_id(group.get("id")))
        for exercise in group.get("exercises") or []:
            allowed.add(latex.tex_id(exercise.get("id")))
    allowed.discard("")
    unexpected = sorted(qid for qid in by_qid if qid not in allowed)
    if unexpected:
        raise RuntimeError(
            f"{number}: .bbox marks {unexpected[0]}, which the selection did not print"
        )
    for group in groups:
        for box in group_boxes(group, by_qid):
            if box.page < 0 or box.page >= pdf_pages:
                raise RuntimeError(
                    f"{number}: a mark for {box.group_id} falls outside the PDF"
                )


def printed_from_paper(
    document: dict, papers_dir: Path, stem: str
) -> tuple[list[dict], dict[str, list[Box]], PdfReader]:
    """The groups the paper printed, and the boxes TeX shipped for them.

    Membership is the selection papers.py wrote. The page of each piece, and
    its box, are the marks in the .bbox file, including a member of a set that
    continues on the next page.
    """
    number = str(document.get("number") or "")
    pdf_path = papers_dir / f"{stem}.pdf"
    if not pdf_path.is_file():
        raise RuntimeError(f"{number}: no paper PDF; run papers.py first")
    selection_path = papers_dir / f"{stem}.selection.json"
    if not selection_path.is_file():
        raise RuntimeError(f"{number}: no selection; run papers.py first")
    bbox_path = papers_dir / f"{stem}.bbox"
    if not bbox_path.is_file():
        raise RuntimeError(f"{number}: no {bbox_path.name}; run papers.py first")
    lock = latex.membership_lock_from(selection_path)
    if lock is None:
        raise RuntimeError(f"{number}: no selection; run papers.py first")
    groups = groups_in_selection(document, list(lock.printed_ids))
    by_qid = boxes_from_marks(bbox_path)
    reader = PdfReader(pdf_path)
    marks_agree(number, groups, by_qid, len(reader.pages))
    return groups, by_qid, reader


QUESTION_JSONS_STAGE = "13_question_jsons"


def question_payload(exercise: dict[str, Any]) -> dict[str, Any]:
    """The self-contained question JSON for an exercise member or standalone question.

    Args:
        exercise: One printed exercise. Book keys such as id, number, and
            objectives are left out so the hash follows the question content.

    Returns:
        The exercise fields that identify the question: text, and figures,
        options, and answer when the exercise has them.
    """
    payload: dict[str, Any] = {"text": exercise["text"]}
    if exercise.get("figures"):
        payload["figures"] = deepcopy(exercise["figures"])
    if exercise.get("options"):
        payload["options"] = deepcopy(exercise["options"])
    if "answer" in exercise:
        payload["answer"] = exercise["answer"]
    return payload


def stem_payload(group: dict[str, Any]) -> dict[str, Any]:
    """The self-contained question JSON for a question set's stem.

    The stem sentence is often shared by unrelated sets. The hashed document
    therefore carries that set's exercises, in printed order and in the same
    shape as each exercise tap, so identical wording with different exercises
    does not share a stem MD5.

    Args:
        group: A question set, with its stem and the exercises under it.

    Returns:
        The stem sentence, the stem figures when present, and one entry per
        exercise from question_payload.
    """
    payload: dict[str, Any] = {"stem": group.get("stem") or ""}
    if group.get("figures"):
        payload["figures"] = deepcopy(group["figures"])
    payload["exercises"] = [
        question_payload(exercise) for exercise in group.get("exercises") or []
    ]
    return payload


def save_question_json_and_get_md5(
    payload: dict[str, Any],
    original_id: str,
    output_dir: Path,
) -> str:
    """Construct <original_id>.json, calculate its MD5, rename to <md5>.json, and return md5."""
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_path = output_dir / f"{original_id}.json"
    content_bytes = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    temp_path.write_bytes(content_bytes)
    digest = hashlib.md5(content_bytes).hexdigest()
    final_path = output_dir / f"{digest}.json"
    temp_path.replace(final_path)
    return digest


def compute_taps(
    group: dict[str, Any],
    question_jsons_dir: Path,
) -> list[dict[str, str]]:
    """Compute stable tap identifiers (id, md5) by cutting question JSONs.

    - Standalone exercise: one tap with id equal to group id, hashing the question JSON.
    - Set with stem: one tap for "stem" (stem plus that set's exercises) and one
      tap per exercise (question JSON).
    """
    group_id = str(group["id"])
    exercises = list(group.get("exercises") or [])
    if len(exercises) <= 1:
        ex = exercises[0] if exercises else {"text": group.get("stem") or ""}
        orig_id = str(ex.get("id") or group_id)
        md5_val = save_question_json_and_get_md5(
            question_payload(ex), orig_id, question_jsons_dir
        )
        return [{"id": group_id, "md5": md5_val}]

    # Set
    stem_p = stem_payload(group)
    stem_md5 = save_question_json_and_get_md5(
        stem_p, f"{group_id}_stem", question_jsons_dir
    )
    taps: list[dict[str, str]] = [{"id": "stem", "md5": stem_md5}]

    for ex in exercises:
        eid = str(ex["id"])
        ex_md5 = save_question_json_and_get_md5(
            question_payload(ex), eid, question_jsons_dir
        )
        taps.append({"id": eid, "md5": ex_md5})

    return taps


def group_record(
    book_id: str,
    group: dict,
    *,
    section: str,
    title: str,
    bboxes: list[dict[str, Any]],
    kept: dict[str, Any],
    question_jsons_dir: Path,
) -> dict[str, Any]:
    """One page entry: the printed group, then the fields a later stage fills."""
    exercises = list(group.get("exercises") or [])
    row: dict[str, Any] = {
        "id": group["id"],
        "objective": group.get("objective") or "",
        "section": section,
        "title": title,
        "kind": "set" if len(exercises) > 1 else "exercise",
        "number_of_parts": len(exercises),
        "taps": compute_taps(group, question_jsons_dir),
        "bboxes": bboxes,
    }
    if group.get("stem"):
        row["stem"] = group["stem"]
    if group.get("figures"):
        row["figures"] = deepcopy(group["figures"])
    row["exercises"] = deepcopy(exercises)
    row.update(kept)
    return row


def book_header(document: dict) -> dict[str, Any]:
    """The fields that describe the book once, above the pages."""
    header = {field: document.get(field, "") for field in BOOK_FIELDS}
    header["page_units"] = "pt"
    header["coordinate_system"] = dict(COORDINATE_SYSTEM)
    header["pages"] = {}
    return header


def put_group(pages: dict[str, list[dict[str, Any]]], key: str, group: dict[str, Any]) -> None:
    """Append `group` to `pages[key]`, creating the page if it is new."""
    pages.setdefault(key, []).append(group)


def write_book(
    book_id: str,
    section: str | None,
    make_pdf: bool = True,
    *,
    papers_dir_override: Path | None = None,
    output_dir: Path | None = None,
    isolated: bool = False,
) -> Path:
    """Rebuild `data/output/<book_id>/<book_id>.json` and `<book_id>_bboxes.pdf`.

    A run of one section replaces that section's groups and leaves the others,
    so a transcript already stored on a different paper is not thrown away.
    Which groups were printed is the selection papers.py wrote. The page and
    the box of each piece are the marks TeX wrote into the paper's .bbox file.
    """
    papers_dir_override = (
        papers_dir_override.resolve()
        if papers_dir_override is not None
        else None
    )
    output_dir = output_dir.resolve() if output_dir is not None else None
    documents = accepted_sections(book_id, None)
    if not documents:
        raise RuntimeError(f"no sections to write groups for {book_id}")
    wanted = {
        stem
        for stem, document in documents
        if section is None or document["number"] == section
    }
    if section and not wanted:
        raise RuntimeError(f"no section {section} in {book_id}")
    papers_dir = (
        papers_dir_override
        or OUTPUT_DIR / book_id / papers.PAPERS_STAGE
    )
    out_path = (
        output_dir / f"{book_id}.json"
        if output_dir is not None
        else working_json(book_id)
    )
    question_jsons_dir = (
        output_dir / QUESTION_JSONS_STAGE
        if output_dir is not None
        else OUTPUT_DIR / book_id / QUESTION_JSONS_STAGE
    )
    question_jsons_dir.mkdir(parents=True, exist_ok=True)
    kept = {} if isolated else existing_transcripts(out_path)
    stored = {} if isolated else previous_by_section(out_path)

    book = book_header(documents[0][1])
    pages: dict[str, list[dict[str, Any]]] = {}

    rebuilding = sum(1 for stem, _ in documents if stem in wanted)
    print(
        f"writing groups for {rebuilding} section(s) of {book_id}",
        flush=True,
    )
    front = frontmatter.front_matter(book_id)
    if front is None:
        raise RuntimeError(
            f"no front matter has been set for {book_id} (run frontmatter.py)"
        )
    offset = front.pages
    for stem, document in documents:
        pdf_path = papers_dir / f"{stem}.pdf"
        if not pdf_path.is_file():
            if stem in wanted:
                raise RuntimeError(
                    f"{document['number']}: no paper PDF; run papers.py first"
                )
            for old in stored.get(document["number"], {}).values():
                rec = deepcopy(old)
                rec["taps"] = compute_taps(rec, question_jsons_dir)
                put_group(pages, page_key(offset + 1), rec)
            continue

        groups, by_qid, reader = printed_from_paper(document, papers_dir, stem)
        for group in groups:
            kept_row = kept.get((document["number"], group["id"]), {})
            for local in pages_of(group_boxes(group, by_qid)):
                put_group(
                    pages,
                    page_key(offset + local + 1),
                    group_record(
                        book_id,
                        group,
                        section=document["number"],
                        title=document["title"],
                        bboxes=nested_bboxes(group, by_qid, reader, page=local),
                        kept=kept_row,
                        question_jsons_dir=question_jsons_dir,
                    ),
                )
        actual_pages = len(reader.pages)
        offset += actual_pages
        if stem in wanted:
            print(
                f"  {document['number']}: {len(groups)} group(s) "
                f"on {actual_pages} page(s)",
                flush=True,
            )

    book["pages"] = {key: pages[key] for key in sorted(pages)}
    total = sum(len(groups) for groups in book["pages"].values())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(book, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"wrote {out_path.relative_to(PROJECT_ROOT)} "
        f"({total} group(s) on {len(book['pages'])} page(s))",
        flush=True,
    )
    if make_pdf:
        write_bboxes_pdf(
            book_id,
            section,
            book,
            papers_dir=papers_dir,
            out_dir=output_dir,
        )
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_id", help="Book ID, e.g. mathematics_ar_sa06_t2")
    parser.add_argument("-s", "--section", help="write only this section, e.g. -s 1.1")
    parser.add_argument(
        "--no-pdf", action="store_true", help="do not generate overlaid bboxes PDF"
    )
    parser.add_argument(
        "--section-only",
        action="store_true",
        help="write an isolated -s artifact without merging production working JSON",
    )
    parser.add_argument(
        "--papers-dir",
        type=Path,
        help="read paper PDFs/selection/bboxes from this directory",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="write isolated JSON/question/bbox artifacts here",
    )
    args = parser.parse_args()
    if args.section_only and not args.section:
        parser.error("--section-only requires --section")
    if args.section_only and args.output_dir is None:
        parser.error("--section-only requires --output-dir")
    try:
        write_book(
            args.book_id,
            args.section,
            make_pdf=not args.no_pdf,
            papers_dir_override=args.papers_dir,
            output_dir=args.output_dir,
            isolated=args.section_only,
        )
    except RuntimeError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
