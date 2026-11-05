#!/usr/bin/env python3
"""
Package Script (Practice Book)

Creates the Foliostax practice package from the working JSON
(data/output/<book_id>/<book_id>.json) and the bound PDF
(data/output/<book_id>/<book_id>.pdf).

Two artifacts, for the whole book or for one section with -s:

  data/output/<book_id>/<book_id>_package.json
  data/output/<book_id>/<book_id>_<section>_package.json
  data/output/<book_id>/15_package/<md5>.zip

    python3 scripts/package.py mathematics_ar_sa06_t2
    python3 scripts/package.py mathematics_ar_sa06_t2 -s 1.1

-s keeps only the groups of that section. Page keys stay the bound-PDF
page numbers, so a tap still sits on the page where it was printed.

The zip is the served package. Its md5 is the MD5 of the practice book id
in data/book_database.json, and its layout matches the Nagwa Books foliostax
zip, for a practice book only:

  <md5>.pdf
  <md5>.json
  <md5>_thumbnail.png
  taps/<tap_md5>/01.mp3
  taps/<tap_md5>/01.txt
  taps/<tap_md5>/01.json

The zip is not written unless every tap in that package has a transcript
(01.txt), audio (01.wav or 01.mp3), and alignment (01.json). A section run
checks only that section's taps.

After the served zip is written, the script also archives everything under
``data/output/<book_id>/`` except the ``15_package`` folder into:

  data/output/<book_id>/15_package/<practice_book_id>_<md5>_<yyyy-mm-dd>.zip

The practice book id and its md5 come from ``data/book_database.json``.
``--no-output-backup`` skips that dated archive and still writes the served zip.

Adheres to NAGWA_BOOK_SCHEMA:
  - document metadata with type="practice"
  - pages dictionary indexed by bound-PDF page ("0005", etc.)
  - taps keyed by 32-hex MD5 digest with top-left bounding boxes
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

from pypdf import PdfReader

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    import pypdfium2 as pdfium
    from PIL import Image

    from sources import OUTPUT_DIR, PROJECT_ROOT, working_json
except ModuleNotFoundError as error:  # pragma: no cover
    sys.exit(f"missing dependency ({error.name}): pip install -r requirements.txt")

CLASSIFICATION_CODE = "N4"

# Cover thumbnail, the same A4 frame the Nagwa Books packager writes.
THUMBNAIL_WIDTH = 400
THUMBNAIL_HEIGHT = round(THUMBNAIL_WIDTH * 297 / 210)
# Render above the served size, then Lanczos-down. Direct render at 400px
# fatten hairline strokes (0.5 pt title/waves) to ~1 px; supersample keeps
# relative weight closer to the PDF.
THUMBNAIL_SUPERSAMPLE = 4

COORDINATE_SYSTEM = {
    "type": "absolute",
    "origin": "top_left",
    "format": "x0,y0,x1,y1",
}


def load_json(path: Path) -> dict[str, Any]:
    """Read a JSON object from path, or raise RuntimeError."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise RuntimeError(f"cannot read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid JSON in {path}: {error}") from error
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} is not a JSON object")
    return data


def book_md5(book_id: str) -> str:
    """MD5 of a book id, the name the served zip and its document share.

    The id is the practice book id from the catalog, not the folder the
    pipeline reads the book from.
    """
    return hashlib.md5(book_id.encode("utf-8")).hexdigest()


# Catalog keys name grade, term, language, and country; the row must agree.
# Optional _PartN is a split of the same regular book, not a different key shape.
CATALOG_KEY = re.compile(
    r"^.+_G(?P<grade>\d+)_T(?P<term>\d+)_Regular_"
    r"(?P<lang>[a-z]+)_(?P<country>[a-z]+)(?:_Part\d+)?$"
)
CATALOG_ID_FIELDS = ("book_id", "study_book_id", "practice_book_id", "exam_book_id")
TOC_IDENTITY_FIELDS = (
    "subject",
    "grade",
    "term",
    "language_code",
    "country_code",
)
TOC_FIELDS = TOC_IDENTITY_FIELDS + ("cover_illustration", "academic_year")

_catalog_validated = False


def catalog_number(value: str) -> str:
    """'07' and '7' are the same grade or term."""
    text = value.strip()
    return str(int(text)) if text.isdigit() else text


def catalog_identity(
    key: str, row: dict[str, Any]
) -> tuple[str, str, str, str, str] | None:
    """Subject, grade, term, language, country for a catalog key, if the key is shaped."""
    match = CATALOG_KEY.match(key)
    if match is None:
        return None
    return (
        str(row.get("subject", "")).strip(),
        catalog_number(str(row.get("grade", ""))),
        catalog_number(match.group("term")),
        str(row.get("language_code", "")).strip(),
        str(row.get("country_code", "")).strip(),
    )


def package_ready(row: dict[str, Any]) -> bool:
    """True when the row can be served: subject_id is a Nagwa digit id."""
    return str(row.get("subject_id", "")).strip().isdigit()


def wanted_identity(
    identity: tuple[str, str, str, str, str],
) -> tuple[str, str, str, str, str]:
    """Normalize grade/term digits in a TOC identity tuple."""
    return (
        identity[0],
        catalog_number(identity[1]),
        catalog_number(identity[2]),
        identity[3],
        identity[4],
    )


def row_matches_identity(
    key: str,
    row: dict[str, Any],
    identity: tuple[str, str, str, str, str],
) -> bool:
    """True when the row is this book by catalog subject or by book_title.

    Applied/Pure share catalog ``subject`` (Mathematics) and put the XML
    subject on ``book_title``. Matching title+grade+term+lang+country finds
    the row when the TOC subject is the title, not the catalog subject.
    """
    cat = catalog_identity(key, row)
    if cat is None:
        return False
    wanted = wanted_identity(identity)
    if cat == wanted:
        return True
    title = str(row.get("book_title", "")).strip()
    return bool(title) and title == wanted[0] and cat[1:] == wanted[1:]


def match_catalog_row(
    catalog: dict[str, Any],
    identity: tuple[str, str, str, str, str],
    cover: str,
    *,
    catalog_name: str = "book_database.json",
) -> tuple[str, dict[str, Any]]:
    """The one package-ready row for this book's identity.

    Draft rows with no subject_id are ignored, so a Biology stub cannot block
    a mathematics package. Cover is used only when several ready rows share
    subject, grade, term, language and country (Applied vs Pure). A unique
    ready row still matches when the XML cover filename has drifted.
    """
    ready: list[tuple[str, dict[str, Any]]] = []
    for key, row in catalog.items():
        if not isinstance(row, dict) or not package_ready(row):
            continue
        if row_matches_identity(key, row, identity):
            ready.append((key, row))
    found = ready
    if len(found) > 1:
        covered = [
            pair
            for pair in found
            if str(pair[1].get("cover_illustration", "")).strip() == cover
        ]
        if covered:
            found = covered
    if len(found) != 1:
        subject, grade, term, language, country = identity
        raise RuntimeError(
            f"{catalog_name} has {len(found)} rows for {subject} "
            f"grade {grade} term {term} {language}_{country}"
        )
    return found[0]


def validate_catalog(
    catalog: dict[str, Any], *, path_name: str | None = None
) -> None:
    """Refuse a catalog whose keys, rows, or ids disagree with each other.

    Completeness of subject_id and titles is required of the row being
    packaged, not of every stub in the file.
    """
    name = path_name or catalog_path().name
    seen_ids: dict[str, str] = {}
    seen_practice_md5: dict[str, str] = {}

    for key, row in catalog.items():
        if not isinstance(row, dict):
            raise RuntimeError(f"{name} key {key!r} is not an object")
        match = CATALOG_KEY.match(key)
        if match is None:
            raise RuntimeError(f"{name} key {key!r} is not a catalog key")

        grade = str(row.get("grade", "")).strip()
        language = str(row.get("language_code", "")).strip()
        country = str(row.get("country_code", "")).strip()
        ready = package_ready(row)
        if ready or grade:
            if grade != match.group("grade"):
                raise RuntimeError(
                    f"{name} row {key!r} has grade {grade!r} but the key says "
                    f"G{match.group('grade')}"
                )
        if ready or language:
            if language != match.group("lang"):
                raise RuntimeError(
                    f"{name} row {key!r} has language_code {language!r} but the "
                    f"key says {match.group('lang')!r}"
                )
        if ready or country:
            if country != match.group("country"):
                raise RuntimeError(
                    f"{name} row {key!r} has country_code {country!r} but the "
                    f"key says {match.group('country')!r}"
                )

        row_ids: list[str] = []
        for field in CATALOG_ID_FIELDS:
            value = str(row.get(field, "")).strip()
            if len(value) != 12 or not value.isdigit():
                raise RuntimeError(
                    f"{name} row {key!r} has invalid {field}: {value!r}"
                )
            row_ids.append(value)
            owner = seen_ids.get(value)
            if owner is not None and owner != key:
                raise RuntimeError(
                    f"{name} reuses id {value} on {key!r} and {owner!r}"
                )
            seen_ids[value] = key
        if len(set(row_ids)) != len(row_ids):
            raise RuntimeError(f"{name} row {key!r} repeats an id within the row")

        practice_id = row_ids[2]
        digest = book_md5(practice_id)
        owner = seen_practice_md5.get(digest)
        if owner is not None and owner != key:
            raise RuntimeError(
                f"{name} rows {key!r} and {owner!r} share practice md5 {digest}"
            )
        seen_practice_md5[digest] = key


def ensure_catalog_validated(catalog: dict[str, Any]) -> None:
    """Validate the catalog once per process."""
    global _catalog_validated
    if _catalog_validated:
        return
    validate_catalog(catalog)
    _catalog_validated = True


CATALOG_ROW_FIELDS = (
    "book_id",
    "study_book_id",
    "practice_book_id",
    "exam_book_id",
    "academic_year",
    "country",
    "country_code",
    "language",
    "language_code",
    "grade",
    "subject",
    "subject_id",
    "book_title",
    "cover_illustration",
)
PLACEHOLDER_CATALOG_ID = "<new>"
ID_SERVICE_URL = "https://12digit.nagwa.com/get.bulk.codes/{count}/cps/cps.system/"


def catalog_path() -> Path:
    """The book catalog the served id and the subject id are taken from."""
    return PROJECT_ROOT / "data" / "book_database.json"


def toc_path(book_id: str) -> Path:
    """The table of contents whose attributes identify a book in the catalog."""
    return PROJECT_ROOT / "data" / "input" / book_id / f"{book_id}.xml"


def wording_path() -> Path:
    """Country names and other front-matter wording, including English short names."""
    return PROJECT_ROOT / "frontmatter" / "wording.json"


def subjects_csv_path() -> Path:
    """Nagwa subject ids keyed by grade title and subject URL text."""
    return PROJECT_ROOT / "data" / "subjects.csv"


def english_subject_from_book_id(book_id: str) -> str:
    """The catalog key prefix: the first ``_`` token of the pipeline book id, capitalized."""
    token = book_id.split("_", 1)[0]
    if not token or not token.isascii() or not token.isalpha():
        raise RuntimeError(
            f"book id {book_id!r} has no ASCII-letter subject token"
        )
    return token.capitalize()


def catalog_key_for(book_id: str, root: ET.Element) -> str:
    """The catalog key for this book's XML identity and pipeline subject token."""
    subject_key = english_subject_from_book_id(book_id)
    try:
        grade = str(int(str(root.attrib.get("grade", "")).strip()))
        term = str(int(str(root.attrib.get("term", "")).strip()))
    except ValueError as error:
        raise RuntimeError(
            f"table of contents has an invalid grade or term: {error}"
        ) from error
    language_code = str(root.attrib.get("language_code", "")).strip()
    country_code = str(root.attrib.get("country_code", "")).strip()
    key = f"{subject_key}_G{grade}_T{term}_Regular_{language_code}_{country_code}"
    if CATALOG_KEY.match(key) is None:
        raise RuntimeError(f"{key!r} is not a catalog key")
    return key


def reject_part_book(book_id: str, root: ET.Element, key: str) -> None:
    """A split ``_PartN`` book is out of scope for autocreate."""
    if "part" in book_id.lower() or str(root.attrib.get("part", "")).strip() or re.search(
        r"_Part\d+$", key
    ):
        raise RuntimeError(
            f"{book_id} is a part book; add the row to book_database.json by hand"
        )


def load_book_root(book_id: str, *, stamp_year: bool) -> ET.Element:
    """Parse the table of contents, optionally stamping this year's academic_year."""
    source = toc_path(book_id)
    if stamp_year:
        from src.academic_year import ensure_academic_year

        ensure_academic_year(book_id, toc=source)
    if not source.is_file():
        raise RuntimeError(f"table of contents not found: {display_path(source)}")
    try:
        root = ET.parse(source).getroot()
    except ET.ParseError as error:
        raise RuntimeError(
            f"invalid table of contents {display_path(source)}: {error}"
        ) from error
    missing = [name for name in TOC_FIELDS if not str(root.attrib.get(name, "")).strip()]
    if missing:
        raise RuntimeError(
            f"{display_path(source)} has no {', '.join(missing)} on <book>"
        )
    return root


def book_cover_identity(
    root: ET.Element,
) -> tuple[tuple[str, str, str, str, str], str]:
    """Subject, grade, term, language, country, and the cover filename."""
    identity = tuple(str(root.attrib[name]).strip() for name in TOC_IDENTITY_FIELDS)
    cover = str(root.attrib.get("cover_illustration", "")).strip()
    return identity, cover


def identity_row_flags(
    catalog: dict[str, Any], identity: tuple[str, str, str, str, str]
) -> tuple[bool, bool]:
    """Whether any ready row and any draft row share this book's identity."""
    has_ready = False
    has_draft = False
    for key, row in catalog.items():
        if not isinstance(row, dict):
            continue
        if not row_matches_identity(key, row, identity):
            continue
        if package_ready(row):
            has_ready = True
        else:
            has_draft = True
    return has_ready, has_draft


def country_short_name_en(country_code: str, wording: dict[str, Any]) -> str:
    """The catalog's English country name from wording.json."""
    names = wording.get("country_short_names_en")
    if not isinstance(names, dict):
        raise RuntimeError("frontmatter/wording.json has no country_short_names_en")
    value = str(names.get(country_code, "")).strip()
    if not value:
        raise RuntimeError(
            f"frontmatter/wording.json has no country_short_names_en for {country_code!r}"
        )
    return value


def subject_id_from_csv(
    country_code: str,
    grade: str,
    language_code: str,
    english_subject: str,
) -> str:
    """The one Nagwa subject id for this grade title and subject URL text."""
    grade_title = f"{country_code.strip().upper()}{int(str(grade).strip()):02d}"
    url_text = english_subject.lower().replace("_", "-") + "-" + language_code.strip()
    path = subjects_csv_path()
    found: list[str] = []
    seen: set[str] = set()
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for record in reader:
                if (
                    str(record.get("grade_title", "")).strip() == grade_title
                    and str(record.get("subject_url_text", "")).strip() == url_text
                ):
                    subject_id = str(record.get("subject_id", "")).strip()
                    if subject_id and subject_id not in seen:
                        seen.add(subject_id)
                        found.append(subject_id)
    except OSError as error:
        raise RuntimeError(f"cannot read {display_path(path)}: {error}") from error
    if len(found) != 1:
        listing = ", ".join(found) if found else "none"
        raise RuntimeError(
            f"subjects.csv has {len(found)} subject_id(s) for {grade_title} "
            f"{url_text} ({listing}); add the row to book_database.json by hand"
        )
    return found[0]


def catalog_id_set(catalog: dict[str, Any]) -> set[str]:
    """Every 12-digit id already stored on a catalog row."""
    found: set[str] = set()
    for row in catalog.values():
        if not isinstance(row, dict):
            continue
        for field in CATALOG_ID_FIELDS:
            value = str(row.get(field, "")).strip()
            if value:
                found.add(value)
    return found


def practice_md5_set(catalog: dict[str, Any]) -> set[str]:
    """MD5 of every practice book id already in the catalog."""
    found: set[str] = set()
    for row in catalog.values():
        if not isinstance(row, dict):
            continue
        practice = str(row.get("practice_book_id", "")).strip()
        if practice:
            found.add(book_md5(practice))
    return found


def _fetch_bulk_codes(count: int) -> Any:
    """One GET to the Nagwa 12-digit service. Tests patch this; it is not called otherwise."""
    import warnings

    try:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.exceptions import InsecureRequestWarning
        from urllib3.util.retry import Retry
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "12-digit ID service: missing dependency (requests): "
            "pip install -r requirements.txt"
        ) from error

    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.5,
        status_forcelist=(500, 502, 503, 504),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    url = ID_SERVICE_URL.format(count=count)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", InsecureRequestWarning)
            response = session.get(url, timeout=10, verify=False)
        response.raise_for_status()
        return response.json()
    except Exception as error:
        raise RuntimeError(f"12-digit ID service: {error}") from error


def mint_catalog_ids(
    existing: set[str],
    count: int = 4,
    *,
    practice_md5s: set[str] | frozenset[str] = frozenset(),
) -> list[str]:
    """Four new 12-digit ids, unused in the catalog and unique among themselves."""
    try:
        payload = _fetch_bulk_codes(count)
    except RuntimeError:
        raise
    except Exception as error:
        raise RuntimeError(f"12-digit ID service: {error}") from error
    if not isinstance(payload, list):
        raise RuntimeError("12-digit ID service: response is not a list")
    ids = [str(item) for item in payload]
    if len(ids) != count:
        raise RuntimeError(
            f"12-digit ID service: expected {count} ids, got {len(ids)}"
        )
    for value in ids:
        if len(value) != 12 or not value.isdigit():
            raise RuntimeError(f"12-digit ID service: not a 12-digit id: {value!r}")
    if len(set(ids)) != len(ids):
        raise RuntimeError("12-digit ID service: duplicate ids in the response")
    for value in ids:
        if value in existing:
            raise RuntimeError(
                f"12-digit ID service: id {value} is already in the catalog"
            )
    if count >= 3:
        digest = book_md5(ids[2])
        if digest in practice_md5s:
            raise RuntimeError(
                f"12-digit ID service: practice md5 {digest} is already in the catalog"
            )
    return ids


def new_catalog_row(
    book_id: str,
    root: ET.Element,
    wording: dict[str, Any],
    ids: list[str],
    subject_id: str,
) -> tuple[str, dict[str, Any]]:
    """The 14-field catalog row and its key, in the usual field order."""
    if len(ids) != 4:
        raise RuntimeError(f"catalog row needs 4 ids, got {len(ids)}")
    country_code = str(root.attrib.get("country_code", "")).strip()
    language_code = str(root.attrib.get("language_code", "")).strip()
    subject = str(root.attrib.get("subject", "")).strip()
    try:
        grade = str(int(str(root.attrib.get("grade", "")).strip()))
    except ValueError as error:
        raise RuntimeError(
            f"table of contents has an invalid grade: {error}"
        ) from error
    row = {
        "book_id": ids[0],
        "study_book_id": ids[1],
        "practice_book_id": ids[2],
        "exam_book_id": ids[3],
        "academic_year": str(root.attrib.get("academic_year", "")).strip(),
        "country": country_short_name_en(country_code, wording),
        "country_code": country_code,
        "language": str(root.attrib.get("language", "")).strip(),
        "language_code": language_code,
        "grade": grade,
        "subject": subject,
        "subject_id": subject_id,
        "book_title": subject,
        "cover_illustration": str(root.attrib.get("cover_illustration", "")).strip(),
    }
    if tuple(row) != CATALOG_ROW_FIELDS:
        raise RuntimeError("catalog row fields are out of order")
    return catalog_key_for(book_id, root), row


def _catalog_row_json(key: str, row: dict[str, Any]) -> str:
    """One catalog entry at the 4-space field indent of rows 2..280."""
    dumped = json.dumps(row, ensure_ascii=False, indent=4)
    if not dumped.endswith("\n}"):
        raise RuntimeError("catalog row is not an indented JSON object")
    dumped = dumped[: -len("\n}")] + "\n  }"
    return f"  {json.dumps(key, ensure_ascii=False)}: {dumped}"


def splice_catalog_text(text: str, key: str, row: dict[str, Any]) -> str:
    """Append ``key`` as the last object without rewriting earlier rows."""
    stripped = text.rstrip("\n")
    if not stripped.endswith("}"):
        raise RuntimeError("book_database.json does not end with }")
    body = stripped[:-1].rstrip()
    if not body.endswith("}"):
        raise RuntimeError("book_database.json has no last row object")
    return body + ",\n" + _catalog_row_json(key, row) + "\n}\n"


def append_catalog_row(path: Path, key: str, row: dict[str, Any]) -> None:
    """Atomically append one row at the end of book_database.json."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"cannot read {path}: {error}") from error
    try:
        catalog = json.loads(text)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid JSON in {path}: {error}") from error
    if not isinstance(catalog, dict):
        raise RuntimeError(f"{path} is not a JSON object")
    if key in catalog:
        raise RuntimeError(f"{path.name} already has {key!r}")
    candidate = dict(catalog)
    candidate[key] = row
    validate_catalog(candidate, path_name=path.name)
    if not package_ready(row):
        raise RuntimeError(f"{path.name} row {key} is not package-ready")
    new_text = splice_catalog_text(text, key, row)
    loaded = json.loads(new_text)
    if loaded != candidate:
        raise RuntimeError("spliced catalog JSON does not match the candidate")
    if list(loaded) != list(catalog) + [key]:
        raise RuntimeError("spliced catalog key order is wrong")
    for old_key, old_row in catalog.items():
        if loaded[old_key] != old_row:
            raise RuntimeError(f"spliced catalog changed {old_key!r}")
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(new_text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    reread = load_json(path)
    validate_catalog(reread, path_name=path.name)


def resolve_new_catalog_fields(
    book_id: str,
    root: ET.Element,
    catalog: dict[str, Any],
    path: Path,
) -> tuple[str, str, dict[str, Any]]:
    """Key, subject_id, and wording. Raises before the id service is called."""
    key = catalog_key_for(book_id, root)
    if key in catalog:
        raise RuntimeError(
            f"{path.name} already has {key!r}; add the row to book_database.json by hand"
        )
    reject_part_book(book_id, root, key)
    wording = load_json(wording_path())
    country_code = str(root.attrib.get("country_code", "")).strip()
    grade = str(root.attrib.get("grade", "")).strip()
    language_code = str(root.attrib.get("language_code", "")).strip()
    country_short_name_en(country_code, wording)
    subject_id = subject_id_from_csv(
        country_code,
        grade,
        language_code,
        english_subject_from_book_id(book_id),
    )
    return key, subject_id, wording


def create_catalog_row(
    book_id: str,
    root: ET.Element,
    catalog: dict[str, Any],
    path: Path,
) -> None:
    """Mint ids, append the row, and log it. Existing rows are not modified."""
    key, subject_id, wording = resolve_new_catalog_fields(book_id, root, catalog, path)
    ids = mint_catalog_ids(
        catalog_id_set(catalog),
        practice_md5s=practice_md5_set(catalog),
    )
    built_key, row = new_catalog_row(book_id, root, wording, ids, subject_id)
    if built_key != key:
        raise RuntimeError(f"catalog key changed from {key!r} to {built_key!r}")
    candidate = dict(catalog)
    candidate[key] = row
    validate_catalog(candidate, path_name=path.name)
    if not package_ready(row):
        raise RuntimeError(f"{path.name} row {key} is not package-ready")
    append_catalog_row(path, key, row)
    identity, _cover = book_cover_identity(root)
    subject, grade, term, language, country = identity
    print(
        f"package: book_database.json has no row for {subject} "
        f"grade {grade} term {term} {language}_{country}; "
        f"appended {key}: {json.dumps(row, ensure_ascii=False)}",
        flush=True,
    )


def dry_run_catalog(book_id: str) -> None:
    """Print the existing row or the row that would be appended. No writes, no HTTP."""
    root = load_book_root(book_id, stamp_year=False)
    identity, cover = book_cover_identity(root)
    path = catalog_path()
    catalog = load_json(path)
    ensure_catalog_validated(catalog)
    has_ready, has_draft = identity_row_flags(catalog, identity)
    if has_ready or has_draft:
        key, row = match_catalog_row(
            catalog, identity, cover, catalog_name=path.name
        )
        print(
            f"package: book_database.json already has {key}: "
            f"{json.dumps(row, ensure_ascii=False)}",
            flush=True,
        )
        return
    key, subject_id, wording = resolve_new_catalog_fields(book_id, root, catalog, path)
    built_key, row = new_catalog_row(
        book_id,
        root,
        wording,
        [PLACEHOLDER_CATALOG_ID] * 4,
        subject_id,
    )
    if built_key != key:
        raise RuntimeError(f"catalog key changed from {key!r} to {built_key!r}")
    print(
        f"package: would append {key}: {json.dumps(row, ensure_ascii=False)}",
        flush=True,
    )


def catalog_record(book_id: str) -> dict[str, Any]:
    """The catalog row for this book.

    The served package is named from ``practice_book_id``, and ``subject_id``
    is written on its document. Both come from the one package-ready row whose
    subject, grade, term, language and country are the table of contents'.
    Cover disambiguates only when several ready rows share that identity.
    The term is the ``T`` segment of the catalog key.

    When no row (ready or draft) shares that identity and the computed key is
    unused, a new row is appended: four ids from the 12-digit service, subject_id
    from subjects.csv, country from wording.json.

    Raises:
        RuntimeError: The table of contents or the catalog is missing a field,
            or the book is not exactly one row.
    """
    root = load_book_root(book_id, stamp_year=True)
    identity, cover = book_cover_identity(root)
    path = catalog_path()
    catalog = load_json(path)
    ensure_catalog_validated(catalog)
    has_ready, has_draft = identity_row_flags(catalog, identity)
    if has_ready or has_draft:
        key, row = match_catalog_row(
            catalog, identity, cover, catalog_name=path.name
        )
    else:
        create_catalog_row(book_id, root, catalog, path)
        global _catalog_validated
        _catalog_validated = False
        catalog = load_json(path)
        ensure_catalog_validated(catalog)
        key, row = match_catalog_row(
            catalog, identity, cover, catalog_name=path.name
        )
    xml_year = str(root.attrib.get("academic_year", "")).strip()
    row_year = str(row.get("academic_year", "")).strip()
    if xml_year and row_year and xml_year != row_year:
        raise RuntimeError(
            f"{path.name} row {key!r} has academic_year {row_year!r} but "
            f"{display_path(toc_path(book_id))} has {xml_year!r}"
        )
    practice_id = str(row.get("practice_book_id", "")).strip()
    subject_id = str(row.get("subject_id", "")).strip()
    if not practice_id or not subject_id.isdigit():
        raise RuntimeError(f"{path.name} row {key} has no practice_book_id and subject_id")
    return row


def build_document_meta(
    book_id: str,
    working: dict[str, Any],
    *,
    page_count: int,
    taps_count: int,
) -> dict[str, Any]:
    """Build the document object matching NAGWA_BOOK_SCHEMA."""
    doc_md5 = book_md5(book_id)
    subject = str(working.get("subject", "") or "")
    title = str(working.get("title", "") or subject)

    return {
        "classification_code": CLASSIFICATION_CODE,
        "md5": doc_md5,
        "source": "nagwa",
        "type": "practice",
        "title": title,
        "sub_title": str(working.get("sub_title", "") or ""),
        "country": str(working.get("country", "") or ""),
        "country_code": str(working.get("country_code", "") or ""),
        "language": str(working.get("language", "") or ""),
        "language_code": str(working.get("language_code", "") or ""),
        "additional_languages": list(working.get("additional_languages") or []),
        "grade": str(working.get("grade", "") or ""),
        "academic_year": str(working.get("academic_year", "") or ""),
        "term": str(working.get("term", "") or "0"),
        "subject": subject,
        "subject_id": str(working.get("subject_id", "") or ""),
        "page_count": page_count,
        "taps_count": taps_count,
        "page_units": "pt",
        "coordinate_system": dict(COORDINATE_SYSTEM),
        "keywords": list(working.get("keywords") or []),
    }


def extract_tap_bboxes(group: dict[str, Any], tap_id: str) -> list[list[float]]:
    """Extract top-left quads for a specific tap (standalone, stem, or exercise)."""
    raw_bboxes = group.get("bboxes") or []
    if not raw_bboxes or not isinstance(raw_bboxes, list):
        return []

    first_entry = raw_bboxes[0] if isinstance(raw_bboxes[0], dict) else {}

    if tap_id == "stem":
        return list(first_entry.get("stem") or [])

    if group.get("kind") == "set":
        exercises_list = first_entry.get("exercises") or []
        for ex in exercises_list:
            if isinstance(ex, dict) and tap_id in ex:
                return list(ex[tap_id] or [])
        return []

    # Standalone exercise: tap_id == group["id"]
    return list(first_entry.get("group") or [])


def pages_for_section(
    working_pages: dict[str, list[dict[str, Any]]],
    section: str | None,
    book_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Keep every group, or only the groups of one section.

    Time is O(groups). A page left with no groups of that section is dropped.

    Args:
        working_pages: The working JSON pages, keyed by bound-PDF page.
        section: Section number such as ``1.1``, or None for the whole book.
        book_id: Named in the error when the section is not in the book.

    Returns:
        The pages to package, still keyed by their bound-PDF page.

    Raises:
        RuntimeError: ``section`` matches no stored group.
    """
    if section is None:
        return working_pages
    filtered: dict[str, list[dict[str, Any]]] = {}
    for page_key, groups in working_pages.items():
        kept = [
            group
            for group in groups
            if isinstance(group, dict) and group.get("section") == section
        ]
        if kept:
            filtered[page_key] = kept
    if not filtered:
        raise RuntimeError(f"no section {section} in {book_id}")
    return filtered


def package_json_path(book_id: str, section: str | None = None) -> Path:
    """The package JSON for the whole book, or for one section."""
    name = (
        f"{book_id}_package.json"
        if section is None
        else f"{book_id}_{section}_package.json"
    )
    return OUTPUT_DIR / book_id / name


def build_pages_dict(
    working_pages: dict[str, list[dict[str, Any]]],
    reader: PdfReader,
) -> tuple[dict[str, Any], int]:
    """Build the pages dictionary for package JSON, calculating tap counts."""
    pages: dict[str, Any] = {}
    total_taps = 0

    for page_key, groups in sorted(working_pages.items()):
        try:
            page_index = int(page_key) - 1
        except ValueError:
            continue

        if page_index < 0 or page_index >= len(reader.pages):
            continue

        pdf_page = reader.pages[page_index]
        page_width = round(float(pdf_page.mediabox.width), 2)
        page_height = round(float(pdf_page.mediabox.height), 2)

        page_taps: dict[str, Any] = {}

        for group in groups:
            group_title = group.get("title", "") or ""
            is_set = group.get("kind") == "set"
            exercises = list(group.get("exercises") or [])
            taps_list = group.get("taps") or []

            # Match tap records by id
            tap_md5_by_id = {
                t["id"]: t["md5"]
                for t in taps_list
                if isinstance(t, dict) and "id" in t and "md5" in t
            }

            if is_set and len(exercises) > 1:
                # 1. Stem tap. A piece with no box on this page is on another
                # page of a set the paper divided; it is not a tap here.
                stem_md5 = tap_md5_by_id.get("stem")
                if stem_md5:
                    stem_bboxes = extract_tap_bboxes(group, "stem")
                    if stem_bboxes:
                        if stem_md5 in page_taps:
                            for box in stem_bboxes:
                                if box not in page_taps[stem_md5]["bboxes"]:
                                    page_taps[stem_md5]["bboxes"].append(box)
                        else:
                            page_taps[stem_md5] = {
                                "label": f"{group_title} - Statement".strip(),
                                "variants": 1,
                                "bboxes": stem_bboxes,
                                "item": "Question",
                                "status": "current",
                            }

                # 2. Part taps
                for idx, ex in enumerate(exercises, start=1):
                    eid = ex.get("id")
                    ex_md5 = tap_md5_by_id.get(eid)
                    if ex_md5:
                        ex_bboxes = extract_tap_bboxes(group, eid)
                        if not ex_bboxes:
                            continue
                        if ex_md5 in page_taps:
                            for box in ex_bboxes:
                                if box not in page_taps[ex_md5]["bboxes"]:
                                    page_taps[ex_md5]["bboxes"].append(box)
                        else:
                            page_taps[ex_md5] = {
                                "label": f"{group_title} - Part {idx}".strip(),
                                "variants": 1,
                                "bboxes": ex_bboxes,
                                "item": "Question",
                                "status": "current",
                            }
            else:
                gid = group.get("id")
                group_md5 = tap_md5_by_id.get(gid)
                if group_md5:
                    group_bboxes = extract_tap_bboxes(group, gid)
                    if group_bboxes:
                        if group_md5 in page_taps:
                            for box in group_bboxes:
                                if box not in page_taps[group_md5]["bboxes"]:
                                    page_taps[group_md5]["bboxes"].append(box)
                        else:
                            page_taps[group_md5] = {
                                "label": group_title,
                                "variants": 1,
                                "bboxes": group_bboxes,
                                "item": "Question",
                                "status": "current",
                            }

        if page_taps:
            pages[page_key] = {
                "width": page_width,
                "height": page_height,
                "taps": page_taps,
            }
            total_taps += len(page_taps)

    return pages, total_taps


TAPS_STAGE = "14_taps"
# The front-matter stage folder cover.py writes. The served thumbnail is the
# cover page, not a separate drawing.
FRONTMATTER_STAGE = "01_frontmatter"
PACKAGE_STAGE = "15_package"


def export_tap_transcripts(book_id: str, working_pages: dict[str, list[dict[str, Any]]]) -> int:
    """Export 01.txt for each tap with a transcript into data/output/<book_id>/14_taps/<md5>/01.txt.

    The file is the stored transcript with its <math> tags kept, the same text
    transcripts.py writes when the transcript is saved.
    """
    from transcripts import write_tap_transcripts

    taps_dir = OUTPUT_DIR / book_id / TAPS_STAGE
    exported = 0
    for groups in working_pages.values():
        for group in groups:
            exported += write_tap_transcripts(taps_dir, group)
    return exported


def display_path(path: Path) -> str:
    """A path relative to the project when it lives there."""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def cover_pdf_path(book_id: str) -> Path:
    """The drawn cover PDF the served thumbnail is rendered from."""
    return OUTPUT_DIR / book_id / FRONTMATTER_STAGE / f"{book_id}_cover.pdf"


def served_zip_path(
    book_id: str,
    section: str | None = None,
    *,
    named_id: str | None = None,
) -> Path:
    """Where the served zip is written.

    Always ``<md5>.zip``, whether the whole book or one section is packaged.
    ``named_id`` is the practice book id the md5 is taken from; without it the
    folder name is used. ``section`` is ignored for the file name only.
    """
    digest_id = named_id or book_id
    return OUTPUT_DIR / book_id / PACKAGE_STAGE / f"{book_md5(digest_id)}.zip"


def output_backup_zip_path(
    book_id: str,
    practice_id: str,
    *,
    stamp: date | None = None,
) -> Path:
    """Path for the book output backup written beside the served package."""
    if len(practice_id) != 12 or not practice_id.isdigit():
        raise RuntimeError(
            f"practice_book_id must be a 12-digit id, not {practice_id!r}"
        )
    day = stamp or date.today()
    digest = book_md5(practice_id)
    name = f"{practice_id}_{digest}_{day.isoformat()}.zip"
    return OUTPUT_DIR / book_id / PACKAGE_STAGE / name


def build_output_backup_zip(
    book_id: str,
    practice_id: str,
    *,
    stamp: date | None = None,
) -> Path:
    """Zip ``data/output/<book_id>/`` except ``15_package`` into that stage folder.

    The archive keeps paths relative to the book output directory. The served
    foliostax zip and any prior backup zips live under ``15_package`` and are
    not included.
    """
    book_dir = OUTPUT_DIR / book_id
    if not book_dir.is_dir():
        raise RuntimeError(f"book output not found: {display_path(book_dir)}")

    zip_path = output_backup_zip_path(book_id, practice_id, stamp=stamp)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    partial = zip_path.with_name(f".{zip_path.name}.partial")
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(book_dir.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(book_dir)
                if relative.parts and relative.parts[0] == PACKAGE_STAGE:
                    continue
                if relative.name.startswith("."):
                    continue
                archive.write(path, relative.as_posix())
        partial.replace(zip_path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return zip_path


def tap_md5s(pages: dict[str, Any]) -> list[str]:
    """Tap digests in page order, each once.

    Time is O(taps). A tap merged onto an earlier page is not listed again.
    """
    ordered: list[str] = []
    seen: set[str] = set()
    for page_key in sorted(pages):
        taps = (pages[page_key] or {}).get("taps") or {}
        for digest in taps:
            if digest in seen:
                continue
            seen.add(digest)
            ordered.append(digest)
    return ordered


def render_cover_thumbnail(cover_path: Path, dest: Path) -> None:
    """Write the A4 cover thumbnail the served zip carries.

    Args:
        cover_path: The book's cover PDF.
        dest: PNG path to write.

    Raises:
        RuntimeError: The cover cannot be rendered.
    """
    try:
        document = pdfium.PdfDocument(str(cover_path))
    except (pdfium.PdfiumError, OSError) as error:
        raise RuntimeError(f"cannot read cover {cover_path}: {error}") from error
    try:
        page = document[0]
        width_pt, _height_pt = page.get_size()
        if width_pt <= 0:
            raise RuntimeError(f"cover has no width: {cover_path}")
        scale = (THUMBNAIL_WIDTH * THUMBNAIL_SUPERSAMPLE) / width_pt
        image = page.render(scale=scale).to_pil().convert("RGB")
    except (pdfium.PdfiumError, OSError, IndexError) as error:
        raise RuntimeError(f"cannot render cover {cover_path}: {error}") from error
    finally:
        document.close()
    image = image.resize(
        (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), Image.Resampling.LANCZOS
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, "PNG")


def encode_mp3(wav_path: Path, mp3_path: Path) -> None:
    """Encode one tap WAV to an mp3 file.

    The encode lands in a sibling file and replaces the destination only after
    ffmpeg exits cleanly, so a killed encode is not reused next time.

    Args:
        wav_path: Source 01.wav.
        mp3_path: Destination mp3.

    Raises:
        RuntimeError: ffmpeg is missing or the encode fails.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is required to encode tap audio as 01.mp3")
    partial = mp3_path.with_name(f"{mp3_path.stem}.partial.mp3")
    completed = subprocess.run(
        [ffmpeg, "-y", "-i", str(wav_path), "-q:a", "2", str(partial)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0 or not partial.is_file():
        partial.unlink(missing_ok=True)
        detail = completed.stderr.decode("utf-8", errors="replace").strip().splitlines()
        tail = detail[-1] if detail else f"exit {completed.returncode}"
        raise RuntimeError(f"ffmpeg failed for {wav_path.parent.name}: {tail}")
    partial.replace(mp3_path)


# What every tap must have on disk before a book is served. Audio may already
# be the wav the pipeline recorded or an mp3 left from an earlier encode.
TAP_ASSETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("transcript", ("01.txt",)),
    ("audio", ("01.wav", "01.mp3")),
    ("alignment", ("01.json",)),
)
_MISSING_SAMPLE = 8


def missing_tap_assets(
    taps_dir: Path, digests: list[str]
) -> dict[str, list[str]]:
    """Taps that lack a transcript, audio, or alignment file.

    Time is O(taps). A tap missing more than one file is listed under each.

    Args:
        taps_dir: The book's 14_taps directory.
        digests: Tap MD5s named by the package JSON, in page order.

    Returns:
        Kind to the digests missing that file. Empty when every tap is complete.
    """
    missing: dict[str, list[str]] = {kind: [] for kind, _names in TAP_ASSETS}
    for digest in digests:
        folder = taps_dir / digest
        for kind, names in TAP_ASSETS:
            if not any((folder / name).is_file() for name in names):
                missing[kind].append(digest)
    return {kind: items for kind, items in missing.items() if items}


def require_tap_assets(taps_dir: Path, digests: list[str]) -> None:
    """Refuse to serve a book while any tap is short a required file.

    Args:
        taps_dir: The book's 14_taps directory.
        digests: Tap MD5s named by the package JSON.

    Raises:
        RuntimeError: At least one tap has no transcript, audio, or alignment.
    """
    missing = missing_tap_assets(taps_dir, digests)
    if not missing:
        return
    incomplete = {digest for items in missing.values() for digest in items}
    lines = [
        f"{len(incomplete)} tap(s) are missing a transcript, audio, or alignment file; "
        "served package not written",
    ]
    for kind, _names in TAP_ASSETS:
        items = missing.get(kind)
        if not items:
            continue
        shown = ", ".join(items[:_MISSING_SAMPLE])
        rest = len(items) - _MISSING_SAMPLE
        extra = f" (+{rest} more)" if rest > 0 else ""
        lines.append(f"  {kind}: {len(items)} ({shown}{extra})")
    raise RuntimeError("\n".join(lines))


def build_served_zip(
    book_id: str,
    *,
    package_json: Path,
    book_pdf: Path,
    cover_pdf: Path,
    taps_dir: Path,
    pages: dict[str, Any],
    zip_path: Path,
    named_id: str | None = None,
) -> tuple[Path, int]:
    """Write the served practice zip in the Nagwa Books foliostax layout.

    Practice only: one zip, named by the practice book id's MD5. Every tap is
    stored with 01.mp3, 01.txt, and 01.json. The zip is not written when any
    tap is missing one of those, and an older zip at the same path is removed
    so a short book is not left served.

    Args:
        book_id: Folder the book is built from, named in errors.
        named_id: Practice book id whose MD5 names the archive entries.
            The folder name is used when this is omitted.
        package_json: The <book_id>_package.json just written.
        book_pdf: The bound book PDF.
        cover_pdf: The cover page rendered to the thumbnail.
        taps_dir: data/output/<book_id>/14_taps.
        pages: The package pages dict, which names every tap.
        zip_path: Destination, replaced only after the archive is complete.

    Returns:
        The zip path and how many mp3s were encoded this call.

    Raises:
        RuntimeError: A tap is missing a required file, the cover is missing,
            or an audio encode fails.
    """
    digests = tap_md5s(pages)
    try:
        require_tap_assets(taps_dir, digests)
    except RuntimeError:
        zip_path.unlink(missing_ok=True)
        raise
    if not cover_pdf.is_file():
        raise RuntimeError(
            f"cover PDF not found: {display_path(cover_pdf)}\n"
            f"run frontmatter.py first: python3 scripts/frontmatter.py {book_id}"
        )
    digest = book_md5(named_id or book_id)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    partial = zip_path.with_name(f".{zip_path.name}.partial")
    thumb_path = partial.with_name(f".{digest}_thumbnail.png")
    # One encode at a time. The mp3 lives in the zip, not beside the wav, so a
    # book does not keep a second copy of every clip.
    temp_mp3 = partial.with_name(".tap.mp3")
    encoded = 0
    try:
        render_cover_thumbnail(cover_pdf, thumb_path)
        archive = zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED)
        try:
            archive.write(book_pdf, f"{digest}.pdf")
            archive.write(package_json, f"{digest}.json")
            archive.write(thumb_path, f"{digest}_thumbnail.png")
            for tap_digest in digests:
                folder = taps_dir / tap_digest
                for name in ("01.txt", "01.json"):
                    asset = folder / name
                    if asset.is_file():
                        archive.write(asset, f"taps/{tap_digest}/{name}")
                stored_mp3 = folder / "01.mp3"
                wav_path = folder / "01.wav"
                arc_mp3 = f"taps/{tap_digest}/01.mp3"
                if stored_mp3.is_file():
                    archive.write(stored_mp3, arc_mp3)
                    stored_mp3.unlink()
                elif wav_path.is_file():
                    encode_mp3(wav_path, temp_mp3)
                    archive.write(temp_mp3, arc_mp3)
                    temp_mp3.unlink()
                    encoded += 1
            archive.close()
        except Exception:
            try:
                archive.close()
            except OSError:
                pass
            raise
        partial.replace(zip_path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    finally:
        thumb_path.unlink(missing_ok=True)
        temp_mp3.unlink(missing_ok=True)
    return zip_path, encoded


def package_book(
    book_id: str,
    section: str | None = None,
    *,
    output_backup: bool = True,
) -> Path:
    """Build the package JSON and the served practice zip.

    Args:
        book_id: Book to package.
        section: When set, the JSON and the zip contain only this section.
        output_backup: When True, also zip the book output tree beside the
            served package. ``--no-output-backup`` sets this False.
    """
    working_path = working_json(book_id)
    if not working_path.is_file():
        raise RuntimeError(
            f"working JSON not found: {working_path.relative_to(PROJECT_ROOT)}\n"
            f"run groups.py first: python3 scripts/groups.py {book_id}"
        )

    working = load_json(working_path)
    book_pdf_path = OUTPUT_DIR / book_id / f"{book_id}.pdf"
    if not book_pdf_path.is_file():
        raise RuntimeError(
            f"bound PDF not found: {book_pdf_path.relative_to(PROJECT_ROOT)}\n"
            f"run papers.py first: python3 scripts/papers.py {book_id}"
        )

    reader = PdfReader(book_pdf_path)
    page_count = len(reader.pages)

    working_pages = working.get("pages") or {}
    if not isinstance(working_pages, dict):
        raise RuntimeError(f"{working_path.name} has no valid 'pages' dictionary")

    selected_pages = pages_for_section(working_pages, section, book_id)
    pages, taps_count = build_pages_dict(selected_pages, reader)

    # Export transcript text files to 14_taps/<md5>/01.txt
    exported_transcripts = export_tap_transcripts(book_id, selected_pages)

    record = catalog_record(book_id)
    practice_id = str(record["practice_book_id"]).strip()
    working = dict(working)
    working["subject_id"] = str(record["subject_id"]).strip()
    # The table of contents names the country in the book's language. The
    # served package uses the catalog's English name.
    working["country"] = str(record["country"]).strip()
    working["academic_year"] = str(record["academic_year"]).strip()
    # When catalog subject and book_title differ (Applied/Pure), the app
    # subject is catalog subject; the document title is book_title.
    catalog_subject = str(record.get("subject", "")).strip()
    catalog_title = str(record.get("book_title", "")).strip()
    if catalog_subject and catalog_title and catalog_subject != catalog_title:
        working["subject"] = catalog_subject
        working["title"] = catalog_title

    package_data = {
        "document": build_document_meta(
            practice_id,
            working,
            page_count=page_count,
            taps_count=taps_count,
        ),
        "pages": pages,
    }

    out_path = package_json_path(book_id, section)
    out_path.write_text(
        json.dumps(package_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    zip_path, encoded = build_served_zip(
        book_id,
        package_json=out_path,
        book_pdf=book_pdf_path,
        cover_pdf=cover_pdf_path(book_id),
        taps_dir=OUTPUT_DIR / book_id / TAPS_STAGE,
        pages=pages,
        zip_path=served_zip_path(book_id, section, named_id=practice_id),
        named_id=practice_id,
    )
    zip_mb = zip_path.stat().st_size / (1024 * 1024)

    print(
        f"wrote {out_path.relative_to(PROJECT_ROOT)} "
        f"({taps_count} tap(s) across {len(pages)} content page(s), total {page_count} page(s)"
        f"{'' if section is None else f', section {section}'}, "
        f"{exported_transcripts} transcript 01.txt exported to {TAPS_STAGE})",
        flush=True,
    )
    print(
        f"wrote {zip_path.relative_to(PROJECT_ROOT)} ({zip_mb:.1f} MB, "
        f"{encoded} mp3(s) encoded)",
        flush=True,
    )

    if output_backup:
        backup_path = build_output_backup_zip(book_id, practice_id)
        backup_mb = backup_path.stat().st_size / (1024 * 1024)
        print(
            f"wrote {backup_path.relative_to(PROJECT_ROOT)} ({backup_mb:.1f} MB, "
            f"book output except {PACKAGE_STAGE})",
            flush=True,
        )
    return zip_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_id", help="Book ID, e.g. mathematics_ar_sa06_t2")
    parser.add_argument("-s", "--section", help="package only this section, e.g. -s 1.1")
    parser.add_argument(
        "--dry-run-catalog",
        action="store_true",
        help="print the catalog row this book would use and exit (no service call, no writes)",
    )
    parser.add_argument(
        "--no-output-backup",
        action="store_true",
        help="write the served zip only; skip the dated archive of data/output/<book_id>/",
    )
    args = parser.parse_args()
    try:
        if args.dry_run_catalog:
            dry_run_catalog(args.book_id)
        else:
            package_book(
                args.book_id,
                args.section,
                output_backup=not args.no_output_backup,
            )
    except RuntimeError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
