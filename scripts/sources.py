#!/usr/bin/env python3
"""Validate a practice book's table of contents, then split its source PDFs into
one image-only PDF per section.

Every page a section claims is rasterised, cropped to the fractional boundaries
the table of contents gives it, and embedded as a JPEG in a fresh PDF. Nothing
survives but pixels: a page shared between two sections cannot leak the
neighbour's wording into the model, because the neighbour's half of the page is
never written. The rendered images are not kept — the section PDFs are the only
artefact anything downstream reads.

Validation exits non-zero and reports every problem found if any check fails.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    import pypdfium2 as pdfium
    from PIL import Image
    Image  # pdfium's to_pil() imports this name at runtime
    from pypdf import PdfReader

    from src.prompting import profile_problems
except ModuleNotFoundError as error:  # pragma: no cover
    sys.exit(f"missing dependency ({error.name}): pip install -r requirements.txt")

PROJECT_ROOT = SCRIPT_DIR.parent
INPUT_DIR = PROJECT_ROOT / "data" / "input"
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"


def working_json(book_id: str) -> Path:
    """The book's working JSON: the file groups.py writes and the later stages read.

    One file per book, beside the bound PDF, named for the book. Transcripts,
    alignment, audio and packaging all open a book through this, so the path
    cannot drift between them.
    """
    return OUTPUT_DIR / book_id / f"{book_id}.json"

SECTIONS_STAGE = "02_section_sources"

# The model scales a document page to at most 3072 px on a side and gains
# nothing from more, so that is what pages are rendered to. Textbook pages are
# portrait, making height the side that binds; a landscape page is clamped so
# its own long side lands on the cap instead.
TARGET_HEIGHT_PX = 3072
MAX_SIDE_PX = 3072
JPEG_QUALITY = 85
# Documents past 50 MB are refused, and that only surfaces at upload time.
SIZE_WARN_BYTES = 45 * 1024**2

PAGE_ATTRS = ("start_page", "end_page")


@dataclass(frozen=True)
class Span:
    """A page range within one source PDF, as claimed by one module."""

    md5: str
    start_raw: str
    end_raw: str
    start: float
    end: float
    module_path: str
    number_path: str
    title: str

    def describe(self) -> str:
        return f"pages {self.start_raw}-{self.end_raw} ({self.module_path})"

    @property
    def first_page(self) -> int:
        return math.floor(self.start)

    @property
    def last_page(self) -> int:
        return math.ceil(self.end) - 1

    def page_fractions(self, page: int) -> tuple[float, float]:
        """The vertical slice of `page` belonging to this span, as 0-1 fractions.

        A span only ever crops its own first and last page; pages in between
        belong to it whole.
        """
        top = self.start - page if page == self.first_page else 0.0
        bottom = min(self.end - page, 1.0) if page == self.last_page else 1.0
        return top, bottom


def page_position(raw: str, *, is_end: bool) -> float:
    """Map a page value onto a single linear axis so ranges can be compared.

    A fractional value is an explicit position part-way down a page, which is
    how consecutive modules split a shared page. A whole number in end_page
    means the end of that page, so page N alone covers the interval [N, N+1).
    """
    value = float(raw)
    if value % 1:
        return value
    return value + 1.0 if is_end else value


def module_label(module: ET.Element) -> str:
    return f"{module.get('number', '?')}. {module.get('title', '<untitled>')}"


def collect_spans(root: ET.Element, errors: list[str]) -> list[Span]:
    """Read every <source> into a Span, reporting malformed attributes."""
    spans: list[Span] = []
    paths = {root: ""}
    numbers: dict[ET.Element, list[str]] = {root: []}

    for parent in root.iter():
        for child in parent:
            if child.tag == "module":
                crumb = module_label(child)
                paths[child] = f"{paths[parent]} > {crumb}" if paths[parent] else crumb
                numbers[child] = numbers[parent] + [child.get("number", "?")]

    for module in root.iter("module"):
        for source in module:
            if source.tag != "source":
                continue
            path = paths[module]
            md5 = source.get("md5")
            if not md5:
                errors.append(f"[attrs] <source> without md5 in {path}")
                continue
            if any(source.get(attr) is None for attr in PAGE_ATTRS):
                errors.append(f"[attrs] <source> md5={md5[:8]} missing start_page/end_page in {path}")
                continue
            try:
                start = page_position(source.get("start_page"), is_end=False)
                end = page_position(source.get("end_page"), is_end=True)
            except ValueError:
                errors.append(
                    f"[attrs] non-numeric page value in {path}: "
                    f"start_page={source.get('start_page')!r} end_page={source.get('end_page')!r}"
                )
                continue
            spans.append(
                Span(
                    md5,
                    source.get("start_page"),
                    source.get("end_page"),
                    start,
                    end,
                    path,
                    ".".join(numbers[module]),
                    module.get("title", ""),
                )
            )
    return spans


def check_level_progression(root: ET.Element, errors: list[str]) -> None:
    """Levels start at 1 and increase by exactly 1 with each nesting step."""
    def walk(parent: ET.Element, expected: int) -> None:
        for module in parent:
            if module.tag != "module":
                continue
            actual = module.get("level")
            if actual != str(expected):
                errors.append(
                    f"[levels] {module_label(module)}: level={actual!r}, expected {expected} "
                    f"for its nesting depth"
                )
            walk(module, expected + 1)

    walk(root, 1)


def check_sources_on_leaves(root: ET.Element, errors: list[str]) -> None:
    """Only innermost modules carry <source>; nothing else does."""
    for source in root:
        if source.tag == "source":
            errors.append(
                f"[placement] <source> md5={source.get('md5', '?')[:8]} is a direct child of "
                f"<{root.tag}>, outside any module"
            )

    for module in root.iter("module"):
        has_source = any(child.tag == "source" for child in module)
        nested = [child for child in module if child.tag == "module"]
        if has_source and nested:
            errors.append(
                f"[placement] {module_label(module)} holds <source> elements but is not innermost: "
                f"it also nests {len(nested)} module(s)"
            )


def check_page_order(spans: list[Span], errors: list[str]) -> None:
    """start_page must not come after end_page.

    Mapped positions alone cannot see every backwards range: a whole number in
    end_page means the end of that page, so start_page="20" end_page="19" maps
    to 20.0 and 20.0, exactly as a repeated fraction does. The raw values are
    compared as well to tell the two apart. start_page="19.4" end_page="19"
    stays legal, being the last 60% of page 19.
    """
    for span in spans:
        if span.start >= span.end and float(span.start_raw) > float(span.end_raw):
            errors.append(
                f"[order] md5={span.md5[:8]} start_page={span.start_raw} is after "
                f"end_page={span.end_raw} in {span.module_path}"
            )


def check_no_empty_spans(spans: list[Span], errors: list[str]) -> None:
    """A span must cover some of a page; start and end cannot be the same point.

    A whole number in end_page means the end of that page, so start_page and
    end_page both reading 19 is page 19 entire, not an empty span. It takes a
    fraction repeated on both sides to name one point twice. A descending range
    lands on one point too, so the raw values must agree before a span is called
    empty; check_page_order has already reported it otherwise.
    """
    for span in spans:
        if span.start == span.end and float(span.start_raw) == float(span.end_raw):
            errors.append(
                f"[empty] md5={span.md5[:8]} start_page={span.start_raw} and "
                f"end_page={span.end_raw} refer to the same point in {span.module_path}: "
                f"the span is empty, so there is nothing to render"
            )


def check_no_overlaps(spans: list[Span], errors: list[str]) -> None:
    """Page ranges for one PDF must not overlap between modules.

    Ranges that merely touch at a shared boundary are fine: one module ending
    at 14.27 and the next starting at 14.27 split page 14 between them.
    """
    by_md5: dict[str, list[Span]] = {}
    for span in spans:
        by_md5.setdefault(span.md5, []).append(span)

    for md5, group in sorted(by_md5.items()):
        ordered = sorted(group, key=lambda s: (s.start, s.end))
        for first, second in zip(ordered, ordered[1:]):
            if second.start < first.end:
                errors.append(
                    f"[overlap] md5={md5[:8]}: {first.describe()} overlaps {second.describe()}"
                )


def check_pdfs(book_dir: Path, spans: list[Span], errors: list[str]) -> None:
    """Each referenced PDF exists and is long enough for its page ranges."""
    required: dict[str, tuple[int, Span]] = {}
    for span in spans:
        last_page = span.last_page
        if span.md5 not in required or last_page > required[span.md5][0]:
            required[span.md5] = (last_page, span)

    for md5, (last_page, span) in sorted(required.items()):
        pdf_path = book_dir / f"{md5}.pdf"
        if not pdf_path.is_file():
            errors.append(f"[pdf] missing file: {pdf_path}")
            continue
        try:
            page_count = len(PdfReader(pdf_path).pages)
        except Exception as error:
            errors.append(f"[pdf] cannot read {pdf_path.name}: {error}")
            continue
        if page_count < last_page:
            errors.append(
                f"[pdf] {pdf_path.name} has {page_count} pages but {span.module_path} "
                f"references up to page {last_page}"
            )


def validate(book_id: str) -> list[str]:
    xml_path = INPUT_DIR / book_id / f"{book_id}.xml"
    if not xml_path.is_file():
        return [f"[xml] file not found: {xml_path}"]
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError as error:
        return [f"[xml] {xml_path} is not well-formed: {error}"]

    errors: list[str] = []
    # The language, numerals and notation decide which prompts every later stage
    # sends, so a book they do not describe is stopped here, before a page is rendered.
    errors += profile_problems(dict(root.attrib))
    spans = collect_spans(root, errors)
    check_level_progression(root, errors)
    check_sources_on_leaves(root, errors)
    check_page_order(spans, errors)
    check_no_empty_spans(spans, errors)
    check_no_overlaps(spans, errors)
    check_pdfs(xml_path.parent, spans, errors)
    return errors


def book_root(book_id: str) -> ET.Element:
    return ET.parse(INPUT_DIR / book_id / f"{book_id}.xml").getroot()


def book_spans(book_id: str) -> list[Span]:
    return collect_spans(book_root(book_id), [])


def book_metadata(book_id: str) -> dict[str, str]:
    """The book's attributes as the <book> element carries them.

    Subject, grade, term, country, and the three that choose its prompts: language,
    numerals and notation. Every stage that builds a prompt reads them from here rather
    than off an earlier stage's files, so that the table of contents is the one place
    a book says what it is.

    A book that runs the whole year has no term, and a table of contents says so either
    by leaving the attribute out or by writing term="0", as the Indian books do; both
    come out of here as no term, so that the cover, the foot and the prompts all leave
    it unsaid rather than naming a "Term 0".

    This year's academic_year is 26-27. A book that still names another year is
    rewritten here, and the edit is logged at the repo root.
    """
    from src.academic_year import ensure_academic_year

    ensure_academic_year(book_id)
    meta = dict(book_root(book_id).attrib)
    if meta.get("term", "").strip() == "0":
        meta["term"] = ""
    grade = meta.get("grade", "").strip()
    if grade.isdigit():
        meta["grade"] = str(int(grade))
    wording_path = PROJECT_ROOT / "frontmatter" / "wording.json"
    try:
        wording = json.loads(wording_path.read_text(encoding="utf-8"))
        named = (
            wording.get("country_names", {})
            .get(meta.get("country_code", "").strip().lower(), {})
            .get(meta.get("language_code", "").strip().lower())
        )
    except (OSError, ValueError, AttributeError):
        named = None
    if isinstance(named, str) and named.strip():
        meta["country"] = named.strip()
    return meta


def section_matches(wanted: str | None, number_path: str, stem: str = "") -> bool:
    """True if this section is the one named, or sits under it.

    `-s 1.3` still names one section, and any 1.3.x under it. `-s 1` or `-s 01`
    names every section whose first number is 1, which is how a unit is asked for.
    Leading zeros and underscores are the filename spelling of the same numbers.
    """
    if wanted is None:
        return True
    wanted = wanted.strip()
    if not wanted:
        return True
    if number_path == wanted or number_path.startswith(f"{wanted}."):
        return True
    if stem and (stem == wanted or stem.startswith(f"{wanted}_")):
        return True
    wanted_parts = [part for part in wanted.replace("_", ".").split(".") if part]
    path_parts = [part for part in number_path.replace("_", ".").split(".") if part]
    if not wanted_parts or len(wanted_parts) > len(path_parts):
        return False

    def key(part: str) -> str:
        return str(int(part)) if part.isdigit() else part

    return [key(part) for part in wanted_parts] == [
        key(part) for part in path_parts[: len(wanted_parts)]
    ]


def matching_sections(rows: list[dict], section: str | None) -> list[dict]:
    """The rows `-s` keeps: one section, or every section under that prefix."""
    return [
        row
        for row in rows
        if section_matches(section, row["number"], row.get("stem", ""))
    ]


def section_spans(book_id: str, section: str | None) -> dict[str, list[Span]]:
    """Each section mapped to its sources, in the order the table of contents lists them.

    One md5 is kept once per section: a section that names the same PDF twice is
    still one source, and so one section PDF and one extraction.
    """
    sections: dict[str, list[Span]] = {}
    for span in book_spans(book_id):
        if not section_matches(section, span.number_path):
            continue
        spans = sections.setdefault(span.number_path, [])
        if not any(seen.md5 == span.md5 for seen in spans):
            spans.append(span)
    return sections


@dataclass(frozen=True)
class PageImage:
    """One rendered, cropped page on its way into a section PDF."""

    jpeg: bytes
    width_px: int
    height_px: int
    width_pt: float
    height_pt: float
    cropped: bool


def number_pad_width(spans: list[Span]) -> int:
    """Zero-pad width that keeps filename order matching table-of-contents order."""
    numbers = [
        int(part)
        for span in spans
        for part in span.number_path.split(".")
        if part.isdigit()
    ]
    return max(2, len(str(max(numbers, default=0))))


def number_stem(number_path: str, pad_width: int) -> str:
    """A section's padded hierarchy, e.g. `01_02_01` for section 1.2.1."""
    return "_".join(
        f"{int(part):0{pad_width}d}" if part.isdigit() else part
        for part in number_path.split(".")
    )


def section_stem(md5: str, number_path: str, pad_width: int) -> str:
    """Build `<padded hierarchy>_<md5>`, e.g. `01_02_01_8d2361ba...`.

    The hierarchy leads so that listing the folder walks the book in order, and
    the md5 tails it so the sources of one section sort together.
    """
    return f"{number_stem(number_path, pad_width)}_{md5}"


def render_page(page, top: float, bottom: float) -> PageImage:
    """Rasterise one page and keep only the [top, bottom] slice of it."""
    width_pt, height_pt = page.get_size()
    scale = TARGET_HEIGHT_PX / height_pt
    if width_pt * scale > MAX_SIDE_PX:
        scale = MAX_SIDE_PX / width_pt

    image = page.render(scale=scale).to_pil().convert("RGB")
    cropped = top > 0 or bottom < 1
    if cropped:
        box = (0, round(top * image.height), image.width, round(bottom * image.height))
        image = image.crop(box)

    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return PageImage(
        jpeg=buffer.getvalue(),
        width_px=image.width,
        height_px=image.height,
        width_pt=width_pt,
        height_pt=height_pt * (bottom - top),
        cropped=cropped,
    )


def write_image_pdf(path: Path, pages: list[PageImage]) -> None:
    """Write a PDF whose pages hold nothing but the JPEGs handed in.

    The JPEG bytes are embedded as they are (/DCTDecode) rather than handed to a
    PDF library that would decode and re-encode them, so the single encode in
    render_page is the only lossy step in the pipeline.
    """
    objects: list[bytes] = []
    # Object 1 is the catalog and object 2 the page tree; each page then takes
    # three objects (page, content stream, image) in that order.
    page_numbers = [3 + index * 3 for index in range(len(pages))]
    kids = " ".join(f"{number} 0 R" for number in page_numbers)

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("latin-1")
    )

    for index, page in enumerate(pages):
        content_number = 4 + index * 3
        image_number = 5 + index * 3
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 {page.width_pt:.2f} {page.height_pt:.2f}] "
                f"/Resources << /XObject << /Im0 {image_number} 0 R >> >> "
                f"/Contents {content_number} 0 R >>"
            ).encode("latin-1")
        )
        content = (
            f"q {page.width_pt:.2f} 0 0 {page.height_pt:.2f} 0 0 cm /Im0 Do Q"
        ).encode("latin-1")
        objects.append(
            b"<< /Length "
            + str(len(content)).encode("latin-1")
            + b" >>\nstream\n"
            + content
            + b"\nendstream"
        )
        objects.append(
            (
                f"<< /Type /XObject /Subtype /Image /Width {page.width_px} "
                f"/Height {page.height_px} /ColorSpace /DeviceRGB "
                f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(page.jpeg)} >>"
            ).encode("latin-1")
            + b"\nstream\n"
            + page.jpeg
            + b"\nendstream"
        )

    body = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n".encode("latin-1") + obj + b"\nendobj\n"

    xref_offset = len(body)
    size = len(objects) + 1
    body += f"xref\n0 {size}\n".encode("latin-1")
    body += b"0000000000 65535 f \n"
    for offset in offsets:
        body += f"{offset:010d} 00000 n \n".encode("latin-1")
    body += (
        f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF"
    ).encode("latin-1")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(body))


def section_groups(
    spans: list[Span], section: str | None
) -> dict[tuple[str, str], list[Span]]:
    """Group spans into the sections they build, one PDF per (source, section).

    A section naming the same PDF more than once contributes all of its ranges
    to that one PDF, in the order the table of contents lists them.
    """
    groups: dict[tuple[str, str], list[Span]] = {}
    for span in spans:
        if not section_matches(section, span.number_path):
            continue
        groups.setdefault((span.md5, span.number_path), []).append(span)
    return groups


def group_pages(spans: list[Span]) -> list[tuple[int, float, float]]:
    """Every page one section takes from one PDF, with the slice it keeps."""
    pages: list[tuple[int, float, float]] = []
    for span in spans:
        for page in range(span.first_page, span.last_page + 1):
            top, bottom = span.page_fractions(page)
            pages.append((page, top, bottom))
    return pages


def pdf_page_count(path: Path) -> int | None:
    """Pages in an existing PDF, or None when it is missing or unreadable."""
    if not path.is_file():
        return None
    try:
        document = pdfium.PdfDocument(path)
    except (pdfium.PdfiumError, OSError):
        return None
    try:
        return len(document)
    finally:
        document.close()


def sort_key(key: tuple[str, str]) -> tuple:
    md5, number_path = key
    numbers = tuple(
        int(part) if part.isdigit() else 0 for part in number_path.split(".")
    )
    return (numbers, md5)


def split_book(book_id: str, section: str | None, update: bool) -> Path:
    """Build one image-only PDF for every (source PDF, section) pair in the book."""
    spans = book_spans(book_id)
    groups = section_groups(spans, section)
    if section and not groups:
        raise RuntimeError(f"no section matching {section} in {book_id}")

    pad_width = number_pad_width(spans)
    book_dir = INPUT_DIR / book_id
    stage_dir = OUTPUT_DIR / book_id / SECTIONS_STAGE
    documents: dict[str, pdfium.PdfDocument] = {}
    built = skipped = pages_total = cropped_total = 0

    try:
        for key in sorted(groups, key=sort_key):
            md5, number_path = key
            group = groups[key]
            out_path = stage_dir / f"{section_stem(md5, number_path, pad_width)}.pdf"
            source_path = book_dir / f"{md5}.pdf"
            pages = group_pages(group)

            # An existing PDF holding exactly the pages the table of contents
            # asks for is taken as done. --update rebuilds it anyway.
            if not update and pdf_page_count(out_path) == len(pages):
                skipped += 1
                continue

            if md5 not in documents:
                # Validation catches this first, unless it was skipped.
                if not source_path.is_file():
                    raise RuntimeError(f"missing source PDF: {source_path}")
                documents[md5] = pdfium.PdfDocument(source_path)
            document = documents[md5]

            images = [
                render_page(document[page - 1], top, bottom)
                for page, top, bottom in pages
            ]
            write_image_pdf(out_path, images)

            size = out_path.stat().st_size
            cropped = sum(1 for image in images if image.cropped)
            built += 1
            pages_total += len(images)
            cropped_total += cropped

            note = f", {cropped} cropped" if cropped else ""
            warning = (
                "  WARNING: close to the 50 MB the model accepts"
                if size > SIZE_WARN_BYTES
                else ""
            )
            print(
                f"  {number_path} [{md5[:8]}]: {len(images)} pages{note} -> "
                f"{size / 1024**2:.1f} MB{warning}"
            )
    finally:
        for document in documents.values():
            document.close()

    summary = f"{built} section PDF(s) built from {pages_total} pages ({cropped_total} cropped)"
    if skipped:
        summary += f", {skipped} already up to date"
    print(f"OK: {summary} under {stage_dir.relative_to(PROJECT_ROOT)}")
    return stage_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_id", help="Book ID, e.g. eg_mathematics_en_g7_t1")
    parser.add_argument(
        "-s", "--section", help="build this section, or every section under it, e.g. -s 1 or -s 01"
    )
    parser.add_argument(
        "--update", action="store_true", help="rebuild section PDFs that already exist"
    )
    parser.add_argument(
        "--skip-validation", action="store_true", help="split without validating first"
    )
    args = parser.parse_args()

    from src.academic_year import ensure_academic_year

    ensure_academic_year(args.book_id)

    if not args.skip_validation:
        errors = validate(args.book_id)
        if errors:
            print(f"FAILED: {args.book_id} has {len(errors)} problem(s)", file=sys.stderr)
            for error in errors:
                print(f"  {error}", file=sys.stderr)
            return 1
        print(f"OK: {args.book_id} passed all checks")

    try:
        split_book(args.book_id, args.section, args.update)
    except RuntimeError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
