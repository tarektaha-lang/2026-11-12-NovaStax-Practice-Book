"""Pair an English practice book with its Arabic sibling.

Translation mode swaps `_en_` for `_ar_` in the book id and refuses to run
unless that sibling already has a table of contents, with the same leaf
section numbers. The Arabic XML is the term authority; this module does not
invent a book.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sources import (
    INPUT_DIR,
    OUTPUT_DIR,
    book_root,
    book_spans,
    matching_sections,
    number_pad_width,
    number_stem,
    section_matches,
    section_spans,
    working_json,
)

EN_MARK = "_en_"
AR_MARK = "_ar_"

SPECS_STAGE = "03_section_specs"
REVIEW_STAGE = "04_book_review"
LABELS_STAGE = "05_section_labels"
BUDGETS_STAGE = "06_section_budgets"
EXERCISES_STAGE = "08_section_new_questions"
ACCEPTED_STAGE = "09_section_questions_review"
FIGURE_DATA_STAGE = "10_book_figure_data"
FIGURES_STAGE = "11_book_figures"
FRONTMATTER_STAGE = "01_frontmatter"
SOURCES_STAGE = "02_section_sources"
PAPERS_STAGE = "12_section_papers"
TAPS_STAGE = "14_taps"
PACKAGE_STAGE = "15_package"
APPLIED_BOOK_FILE = "book_after_review.json"
COST_REPORT = "cost_report.html"
TRANSLATION_STATE_STAGE = "00_translation_state"
TRANSLATION_MANIFEST_VERSION = 3
BOOK_MAP_FILE = "book_map.json"

# Stages that always re-run when invoked; the orchestrator skips them under
# --translate unless --updateboth, because they have no skip of their own.
ALWAYS_RERUN = frozenset(
    {"extract", "frontmatter", "papers", "groups", "package", "cost"}
)


def sibling_id(book_id: str) -> str:
    """The other language's book id, swapping `_en_` and `_ar_` once."""
    if EN_MARK in book_id:
        return book_id.replace(EN_MARK, AR_MARK, 1)
    if AR_MARK in book_id:
        return book_id.replace(AR_MARK, EN_MARK, 1)
    raise ValueError(
        f"{book_id!r} has neither {EN_MARK!r} nor {AR_MARK!r}, so it has no sibling"
    )


def require_english_id(book_id: str) -> None:
    """Refuse --translate on anything but an English book id."""
    if EN_MARK not in book_id:
        raise RuntimeError(
            f"--translate is only valid on a book id that contains {EN_MARK!r}, not {book_id}"
        )


def sibling_xml(book_id: str) -> Path:
    """The Arabic table of contents, or a RuntimeError naming the missing file."""
    require_english_id(book_id)
    ar_id = sibling_id(book_id)
    path = INPUT_DIR / ar_id / f"{ar_id}.xml"
    if not path.is_file():
        raise RuntimeError(
            f"no Arabic sibling input at {path}; --translate does not invent an AR book"
        )
    return path


def leaf_numbers(book_id: str) -> list[str]:
    """Innermost module number paths, in table-of-contents order."""
    leaves: list[str] = []

    def walk(parent, numbers: list[str]) -> None:
        for module in parent:
            if module.tag != "module":
                continue
            path = numbers + [module.get("number", "?")]
            children = [child for child in module if child.tag == "module"]
            if children:
                walk(module, path)
            else:
                leaves.append(".".join(path))

    walk(book_root(book_id), [])
    return leaves


def require_matching_leaves(en_id: str, ar_id: str) -> None:
    """Fail if the two tables of contents do not share the same leaf numbers."""
    english = leaf_numbers(en_id)
    arabic = leaf_numbers(ar_id)
    if english == arabic:
        return
    missing = [number for number in english if number not in arabic]
    extra = [number for number in arabic if number not in english]
    parts = []
    if missing:
        parts.append("missing from AR: " + ", ".join(missing))
    if extra:
        parts.append("extra in AR: " + ", ".join(extra))
    if not parts:
        parts.append(
            f"same leaves in a different order (EN {' '.join(english)}; "
            f"AR {' '.join(arabic)})"
        )
    raise RuntimeError(
        f"{en_id} and {ar_id} do not have the same leaf sections ({'; '.join(parts)})"
    )


def pair_books(en_id: str) -> str:
    """The Arabic sibling id, after checking the input XML and the leaf numbers."""
    path = sibling_xml(en_id)
    ar_id = path.parent.name
    require_matching_leaves(en_id, ar_id)
    return ar_id


def section_stems(book_id: str, section: str | None) -> list[tuple[str, str]]:
    """`(number, stem)` for every leaf `-s` keeps, in table-of-contents order."""
    spans = book_spans(book_id)
    pad_width = number_pad_width(spans)
    seen: list[str] = []
    for span in spans:
        if not section_matches(section, span.number_path):
            continue
        if span.number_path not in seen:
            seen.append(span.number_path)
    return [(number, number_stem(number, pad_width)) for number in seen]


def _json_ok(path: Path) -> bool:
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return path.is_file()


def _all_stems(book_id: str, section: str | None, stage: str) -> bool:
    rows = section_stems(book_id, section)
    if not rows:
        return False
    folder = OUTPUT_DIR / book_id / stage
    return all(_json_ok(folder / f"{stem}.json") for _, stem in rows)


def _sources_done(book_id: str, section: str | None) -> bool:
    groups = section_spans(book_id, section)
    if not groups:
        return False
    pad_width = number_pad_width(book_spans(book_id))
    folder = OUTPUT_DIR / book_id / SOURCES_STAGE
    from sources import section_stem

    for number_path, spans in groups.items():
        for span in spans:
            path = folder / f"{section_stem(span.md5, number_path, pad_width)}.pdf"
            if not path.is_file():
                return False
    return True


def _draw_done(book_id: str, section: str | None) -> bool:
    try:
        import src.latex as latex
        from draw import library
        from questions_review import accepted_sections
    except ModuleNotFoundError:
        folder = OUTPUT_DIR / book_id / FIGURES_STAGE
        return folder.is_dir() and any(folder.glob("*.json"))
    try:
        documents = accepted_sections(book_id, section)
    except RuntimeError:
        return False
    drawings = library(book_id)
    for _, document in documents:
        for description in latex.figures_in(document):
            row = drawings.get(description) or {}
            if not str(row.get("tikz", "")).strip() or not row.get("settled"):
                return False
    return True


def _extract_done(book_id: str, section: str | None) -> bool:
    try:
        import extract as extract_stage
        from questions_review import accepted_sections
    except (ModuleNotFoundError, RuntimeError):
        return False
    try:
        # extract.py is a whole-book stage even when the pipeline was given -s.
        documents = accepted_sections(book_id, None)
    except RuntimeError:
        return False

    root = OUTPUT_DIR / book_id / FIGURE_DATA_STAGE
    expected: set[Path] = set()
    for _, document in documents:
        for group in document.get("exercises", {}).get("groups") or []:
            for key, row in extract_stage.records(group):
                expected.add(
                    Path(extract_stage.folder(row.get("figure_type", "")))
                    / f"{key}.json"
                )
    actual = (
        {
            path.relative_to(root)
            for path in root.rglob("*.json")
            if _json_ok(path)
        }
        if root.is_dir()
        else set()
    )
    return actual == expected


def _papers_done(book_id: str, section: str | None) -> bool:
    rows = section_stems(book_id, section)
    if not rows:
        return False
    papers_dir = OUTPUT_DIR / book_id / PAPERS_STAGE
    if not all((papers_dir / f"{stem}.pdf").is_file() for _, stem in rows):
        return False
    if section is None:
        return (OUTPUT_DIR / book_id / f"{book_id}.pdf").is_file()
    return True


def _groups_done(book_id: str, section: str | None) -> bool:
    """Whether working JSON exists and includes the requested section."""
    path = working_json(book_id)
    if not path.is_file():
        return False
    if section is None:
        return True
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return any(
        isinstance(group, dict)
        and section_matches(section, str(group.get("section") or ""))
        for groups in (document.get("pages") or {}).values()
        for group in (groups or [])
    )


def _working_book(book_id: str, output_dir: Path) -> dict | None:
    path = output_dir / book_id / f"{book_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _taps_dir(book_id: str, output_dir: Path) -> Path:
    return output_dir / book_id / TAPS_STAGE


def audio_wav_counts(
    book_id: str, section: str | None, output_dir: Path | None = None
) -> tuple[int, int]:
    """Collectable ``01.wav`` files on disk, and how many collect_jobs would queue."""
    import audios

    root = output_dir or OUTPUT_DIR
    book = _working_book(book_id, root)
    if book is None:
        return 0, 0
    try:
        return audios.audio_wav_counts(book, _taps_dir(book_id, root), section)
    except (OSError, RuntimeError, TypeError, ValueError, KeyError):
        return 0, 0


def transcript_txt_counts(
    book_id: str, section: str | None, output_dir: Path | None = None
) -> tuple[int, int]:
    """Expected ``01.txt`` files on disk, and how many groups' taps should have them."""
    import transcripts

    root = output_dir or OUTPUT_DIR
    book = _working_book(book_id, root)
    if book is None:
        return 0, 0
    try:
        return transcripts.transcript_txt_counts(
            book, _taps_dir(book_id, root), section
        )
    except (OSError, RuntimeError, TypeError, ValueError, KeyError):
        return 0, 0


def align_json_counts(
    book_id: str, section: str | None, output_dir: Path | None = None
) -> tuple[int, int]:
    """``01.json`` beside each real ``01.wav``. A ``*.wav.tmp`` is not a WAV."""
    root = output_dir or OUTPUT_DIR
    taps_dir = _taps_dir(book_id, root)
    wavs: list[Path] = []
    if section:
        book = _working_book(book_id, root)
        if book is None:
            return 0, 0
        seen: set[str] = set()
        for groups in (book.get("pages") or {}).values():
            if not isinstance(groups, list):
                continue
            for group in groups:
                if not isinstance(group, dict):
                    continue
                if group.get("section") != section:
                    continue
                for row in group.get("taps") or []:
                    if not isinstance(row, dict) or not row.get("md5"):
                        continue
                    digest = str(row["md5"])
                    if digest in seen:
                        continue
                    seen.add(digest)
                    wav = taps_dir / digest / "01.wav"
                    if wav.is_file():
                        wavs.append(wav)
    elif taps_dir.is_dir():
        wavs = [
            path
            for path in taps_dir.glob("*/01.wav")
            if path.is_file() and path.name == "01.wav"
        ]
    have = sum(1 for wav in wavs if wav.with_name("01.json").is_file())
    return have, len(wavs)


def tap_stage_counts(
    stage: str, book_id: str, section: str | None, output_dir: Path | None = None
) -> tuple[int, int] | None:
    """``(have, expected)`` for transcripts, audios or align; else None."""
    name = stage.removeprefix("ar_")
    if name == "audios":
        return audio_wav_counts(book_id, section, output_dir)
    if name == "transcripts":
        return transcript_txt_counts(book_id, section, output_dir)
    if name == "align":
        return align_json_counts(book_id, section, output_dir)
    return None


def tap_progress_line(label: str, canonical: str, have: int, expected: int) -> str:
    """``EN audios: 798/2146 on disk, 1348 to generate``."""
    missing = max(0, expected - have)
    return f"{label} {canonical}: {have}/{expected} on disk, {missing} to generate"


def _tap_stage_complete(
    stage: str, book_id: str, section: str | None, output_dir: Path | None = None
) -> bool:
    counts = tap_stage_counts(stage, book_id, section, output_dir)
    if counts is None:
        return False
    have, expected = counts
    return expected > 0 and have == expected


def _accepted_groups_match(en_path: Path, ar_path: Path) -> bool:
    """True when the Arabic accepted file has the same groups as the English one."""
    if not _json_ok(en_path) or not _json_ok(ar_path):
        return False
    try:
        english = json.loads(en_path.read_text(encoding="utf-8"))
        arabic = json.loads(ar_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    en_groups = list(english.get("exercises", {}).get("groups") or [])
    ar_groups = list(arabic.get("exercises", {}).get("groups") or [])
    if len(en_groups) != len(ar_groups):
        return False
    by_id = {str(group.get("id") or ""): group for group in ar_groups}
    for en_group in en_groups:
        ar_group = by_id.get(str(en_group.get("id") or ""))
        if ar_group is None:
            return False
        if len(en_group.get("exercises") or []) != len(ar_group.get("exercises") or []):
            return False
    return True


def _fingerprint(value) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _translation_manifest_ok(
    root: Path, stem: str, book_map_fingerprint: str
) -> bool:
    manifest_path = root / TRANSLATION_STATE_STAGE / f"{stem}.json"
    candidate_path = root / EXERCISES_STAGE / f"{stem}.json"
    accepted_path = root / ACCEPTED_STAGE / f"{stem}.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        accepted = json.loads(accepted_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if manifest.get("version") != TRANSLATION_MANIFEST_VERSION:
        return False
    if not manifest.get("accepted"):
        return False
    if (manifest.get("review") or {}).get("status") != "passed":
        return False
    if manifest.get("book_map_full_fingerprint") != book_map_fingerprint:
        return False
    expected = str(manifest.get("candidate_fingerprint") or "")
    return bool(expected) and expected == _fingerprint(
        candidate.get("exercises")
    ) == _fingerprint(accepted.get("exercises"))


def _translate_output_complete(ar_id: str, section: str | None) -> bool:
    """Every translated artifact is present, reviewed, fingerprinted and current."""
    if not all(
        _all_stems(ar_id, section, stage)
        for stage in (
            SPECS_STAGE,
            LABELS_STAGE,
            BUDGETS_STAGE,
            EXERCISES_STAGE,
            ACCEPTED_STAGE,
        )
    ):
        return False
    try:
        en_id = sibling_id(ar_id)
    except ValueError:
        return False
    rows = section_stems(ar_id, section)
    if not rows:
        return False
    root = OUTPUT_DIR / ar_id
    try:
        book_map = json.loads(
            (root / TRANSLATION_STATE_STAGE / BOOK_MAP_FILE).read_text(
                encoding="utf-8"
            )
        )
        book_map_fingerprint = str(book_map["semantic_fingerprint"])
        if book_map.get("status") != "ready" or not book_map_fingerprint:
            return False
    except (KeyError, OSError, ValueError):
        return False
    for _, stem in rows:
        if not _accepted_groups_match(
            OUTPUT_DIR / en_id / ACCEPTED_STAGE / f"{stem}.json",
            OUTPUT_DIR / ar_id / ACCEPTED_STAGE / f"{stem}.json",
        ):
            return False
        if not _translation_manifest_ok(root, stem, book_map_fingerprint):
            return False
    return _json_ok(root / REVIEW_STAGE / APPLIED_BOOK_FILE) and _draw_done(
        ar_id, section
    )


def stage_output_exists(
    stage: str,
    book_id: str,
    section: str | None,
    output_dir: Path | None = None,
) -> bool:
    """True when this stage's artefacts for `-s` (or the whole book) are on disk."""
    name = stage.removeprefix("ar_")
    root = (output_dir or OUTPUT_DIR) / book_id
    if name == "sources":
        return _sources_done(book_id, section)
    if name == "specs":
        return _all_stems(book_id, section, SPECS_STAGE)
    if name == "review":
        return _json_ok(root / REVIEW_STAGE / APPLIED_BOOK_FILE)
    if name == "labels":
        return _all_stems(book_id, section, LABELS_STAGE)
    if name == "budgets":
        return _all_stems(book_id, section, BUDGETS_STAGE)
    if name == "source_questions":
        return _all_stems(book_id, section, "07_section_source_questions")
    if name == "new_questions":
        return _all_stems(book_id, section, EXERCISES_STAGE)
    if name == "questions_review":
        return _all_stems(book_id, section, ACCEPTED_STAGE)
    if name == "extract":
        return _extract_done(book_id, section)
    if name == "draw":
        return _draw_done(book_id, section)
    if name == "frontmatter":
        return (root / FRONTMATTER_STAGE / f"{book_id}_cover.pdf").is_file()
    if name == "papers":
        return _papers_done(book_id, section)
    if name == "groups":
        return _groups_done(book_id, section)
    if name == "translate":
        return _translate_output_complete(book_id, section)
    if name in {"transcripts", "audios", "align"}:
        return _tap_stage_complete(name, book_id, section, output_dir)
    if name == "package":
        folder = root / PACKAGE_STAGE
        return folder.is_dir() and any(folder.glob("*.zip"))
    if name == "cost":
        return (root / COST_REPORT).is_file()
    return False


def applied_book_path(book_id: str) -> Path:
    return OUTPUT_DIR / book_id / REVIEW_STAGE / APPLIED_BOOK_FILE


def require_english_review(en_id: str) -> dict:
    """The applied English book, which translation reads instead of `03_`.

    Lost LaTeX backslashes are restored in memory. The file on disk is not
    rewritten. A control that is not a known command fails before any model call.
    """
    from src.english_json import format_english_hits, read_english_json

    path = applied_book_path(en_id)
    try:
        document, hits = read_english_json(path)
    except FileNotFoundError:
        raise RuntimeError(
            f"no reviewed English book at {path}; finish review.py on {en_id} first"
        ) from None
    except (OSError, ValueError) as error:
        raise RuntimeError(f"cannot read {path}: {error}") from error
    if not document.get("sections"):
        raise RuntimeError(f"no sections in {path}")
    for line in format_english_hits(en_id, path, hits):
        print(line)
    return document


def matching_applied_sections(document: dict, section: str | None) -> list[dict]:
    """The applied-book rows `-s` keeps."""
    rows = matching_sections(document["sections"], section)
    if section and not rows:
        raise RuntimeError(
            f"no section matching {section} in the reviewed {document.get('book_id', 'book')}"
        )
    return rows
