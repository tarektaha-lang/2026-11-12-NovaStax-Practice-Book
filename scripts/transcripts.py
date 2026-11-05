#!/usr/bin/env python3
"""Write a spoken transcript for every printed question group.

One call per group. The model sees the crop of that group on the section paper,
a JSON of the group id, the objective id, and each exercise id with its answer,
and the section spec. Shape is chosen here from whether the group is a set or a
standalone exercise; those labels are not sent. groups.py must have written the
working JSON first.

By default groups go through the Gemini Batch API: every chunk of a round
is submitted at once (``--batch-parallel N``; 0 = no cap), under 150
requests and 18 MB per job. Finished jobs download in parallel
(``--download-workers N``, default 3). --no-batch is the previous thread
pool of unary generateContent calls (MAX_WORKERS=25). A transcript already stored
is left unless --update is passed, and each success is written back
immediately so a stopped run can resume. Each stored transcript also gets
``14_taps/<md5>/01.txt``, with its ``<math>`` tags kept, including a
transcript already on the book.

In-flight Batch jobs are recorded in
``data/output/<book_id>/00_transcripts_batch_state.json`` (a ``jobs`` list;
a legacy single ``job`` is still read). A second run on the same book is
refused while ``00_transcripts_batch.lock`` is held. --no-batch still
polls and applies every saved job before going unary. --discard-batch-state
deletes only the local JSON; it does not cancel the remote job.

    python3 scripts/transcripts.py mathematics_ar_sa06_t2
    python3 scripts/transcripts.py mathematics_ar_sa06_t2 -s 1.1
    python3 scripts/transcripts.py mathematics_en_eg07_t1 -s 1.2 --no-batch
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
import re
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    import frontmatter
    import pypdfium2 as pdfium
    from google import genai
    from google.genai import types
    from PIL import Image
    from pydantic import BaseModel, Field

    from src.api_logger import (
        ApiLogger,
        genai_answer_text,
        genai_response_files,
        genai_usage,
        safe_log,
    )
    from src.gemini_batch import (
        BATCH_FAILED,
        FINISH_DONE,
        FULL_GET_TIMEOUT_MS,
        ApplyError,
        BatchChunk,
        BatchLock,
        BatchState,
        BatchStatus,
        batch_display_name,
        batch_lock_path,
        batch_state_path,
        chunk_requests,
        delete_files,
        entries,
        find_jobs_by_display_names,
        finish_token,
        generate_content_response,
        get_batch_job,
        get_batch_status,
        inlined_responses,
        is_legacy,
        map_rows_by_key,
        new_run_id,
        pick_duplicate_match,
        response_and_error,
        result_file_name,
        run_batch_jobs,
        src_for_chunk,
    )
    from src.math_tags import clean_math_tags
    from papers import PAPERS_STAGE
    from questions_review import accepted_sections
    from prompts_builders.research_transcripts import assemble_transcript
    from src.prompting import Prompt
    from specs import SPECS_STAGE, Progress, in_kb, one_line, since
    from sources import OUTPUT_DIR, PROJECT_ROOT, working_json
except ModuleNotFoundError as error:  # pragma: no cover
    sys.exit(f"missing dependency ({error.name}): pip install -r requirements.txt")

TRANSCRIPT_STEP = "transcripts"
TAPS_STAGE = "14_taps"
TRANSCRIPT_FILE = "01.txt"

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_THINKING = "medium"
DEFAULT_BATCH = True
DEFAULT_BATCH_PARALLEL = 0
DEFAULT_DOWNLOAD_WORKERS = 3
THINKING_LEVELS = ("minimal", "low", "medium", "high")
API_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
MAX_ATTEMPTS = 4
MAX_WORKERS = 25
BATCH_STAGE = "transcripts"

# Raster cap on the long side of a paper page, in pixels. A group crop is a
# slice of that page; 2048 keeps the figure readable without filling the
# request with a whole A4.
PAGE_MAX_SIDE_PX = 2048
CROP_PAD_PT = 8
STITCH_GAP_PX = 8

MATH_TAG = re.compile(r"<math(\s[^>]*)?>(.*?)</math>", re.DOTALL | re.IGNORECASE)
SPOKEN_ATTR = re.compile(r'\bspoken="([^"]*)"')
ARABIC_INDIC = re.compile(r"[\u0660-\u0669]")
PAGE_CACHE = 16

# What assemble_transcript() and Profile.of read off the working JSON. Copied
# there from the papers, so this stage does not need the table of contents again.
PROMPT_META_KEYS = (
    "subject",
    "grade",
    "language",
    "country",
    "language_code",
    "numerals",
    "notation",
    "country_code",
)


class Paragraph(BaseModel):
    """One spoken phase: short sentences the tutor can pause between."""

    sentences: list[str] = Field(min_length=1)


class StemBlock(BaseModel):
    """Shared context for a set, before the individual exercises."""

    paragraphs: list[Paragraph] = Field(min_length=1)


class MultipartPart(BaseModel):
    """The explanation of one exercise in a set."""

    id: str = Field(description="The exercise id from the JSON, exactly")
    paragraphs: list[Paragraph] = Field(min_length=1)


class SingleReply(BaseModel):
    """Transcript for a standalone exercise."""

    paragraphs: list[Paragraph] = Field(min_length=1)


class MultipartReply(BaseModel):
    """Transcript for a set: shared stem, then one part per exercise."""

    stem: StemBlock
    parts: list[MultipartPart] = Field(min_length=1)


@dataclass(frozen=True)
class PaperRef:
    """Where one section's paper sits in the bound page numbering."""

    number: str
    stem: str
    first: int
    pages: int


@dataclass(frozen=True)
class Job:
    """One group to transcribe."""

    page_key: str
    group: dict[str, Any]
    paper: PaperRef
    pdf_index: int
    shape: str
    item: str
    more_pages: tuple[tuple[int, tuple[tuple[float, ...], ...]], ...] = ()


class PageCache:
    """Rendered paper pages, shared by workers that crop the same sheet.

    pdfium is not safe across threads, so every render and every crop of a
    cached page runs under one lock. Only the last few pages are kept, because
    a whole book of A4 rasters would otherwise sit in memory for the run.
    """

    def __init__(self, limit: int = PAGE_CACHE) -> None:
        self._limit = limit
        self._lock = threading.Lock()
        self._pages: OrderedDict[tuple[str, int], tuple[Image.Image, float, float]] = (
            OrderedDict()
        )

    def crop(self, pdf_path: Path, index: int, bboxes: list[list[float]]) -> bytes:
        """PNG bytes of the union of `bboxes` on that paper page."""
        with self._lock:
            image, width_pt, height_pt = self._page(pdf_path, index)
            return crop_png(image, width_pt, height_pt, bboxes)

    def _page(self, pdf_path: Path, index: int) -> tuple[Image.Image, float, float]:
        """The page as an RGB image, with its PDF-point size. Lock already held."""
        key = (str(pdf_path), index)
        cached = self._pages.get(key)
        if cached is not None:
            self._pages.move_to_end(key)
            return cached
        document = pdfium.PdfDocument(str(pdf_path))
        try:
            page = document[index]
            width_pt, height_pt = page.get_size()
            scale = PAGE_MAX_SIDE_PX / max(width_pt, height_pt)
            image = page.render(scale=scale).to_pil().convert("RGB")
        finally:
            document.close()
        self._pages[key] = (image, width_pt, height_pt)
        if len(self._pages) > self._limit:
            self._pages.popitem(last=False)
        return image, width_pt, height_pt


def tap_ids(group: dict[str, Any]) -> list[str]:
    """Ids of the taps a stored transcript speaks, in stem-then-exercise order.

    Args:
        group: A printed group whose transcript is already attached.

    Returns:
        The standalone group's id, or ``stem`` followed by each part id.
    """
    if group.get("kind") == "exercise":
        return [str(group["id"])]
    transcript = group.get("transcript") or {}
    ids = ["stem"]
    for part in transcript.get("parts") or []:
        if isinstance(part, dict) and part.get("id"):
            ids.append(str(part["id"]))
    return ids


def paragraphs_text(paragraphs: list[Any]) -> str:
    """One tap's transcript: sentences spaced, paragraphs blank-lined.

    ``<math>`` tags stay as they were stored. The spoken attribute is not
    substituted.

    Args:
        paragraphs: Paragraph objects from one stem, part, or standalone transcript.

    Returns:
        The text written to ``01.txt``.
    """
    blocks: list[str] = []
    for paragraph in paragraphs or []:
        if not isinstance(paragraph, dict):
            continue
        sentences = [
            sentence
            for sentence in (paragraph.get("sentences") or [])
            if isinstance(sentence, str) and sentence
        ]
        if sentences:
            blocks.append(" ".join(sentences))
    return "\n\n".join(blocks)


def paragraphs_for_tap(group: dict[str, Any], tap_id: str) -> list[Any]:
    """The paragraph list spoken by one tap id.

    Args:
        group: A group that already holds its transcript.
        tap_id: ``stem``, an exercise id, or the standalone group's id.

    Returns:
        That tap's paragraphs, or an empty list when the transcript has none.
    """
    transcript = group.get("transcript") or {}
    if group.get("kind") == "exercise":
        return list(transcript.get("paragraphs") or [])
    if tap_id == "stem":
        stem = transcript.get("stem") or {}
        return list(stem.get("paragraphs") or []) if isinstance(stem, dict) else []
    for part in transcript.get("parts") or []:
        if isinstance(part, dict) and str(part.get("id")) == tap_id:
            return list(part.get("paragraphs") or [])
    return []


def write_tap_transcripts(taps_dir: Path, group: dict[str, Any]) -> int:
    """Write ``14_taps/<md5>/01.txt`` for each tap this group's transcript covers.

    The folder is named by the tap MD5 already on the group. A tap with no
    MD5 or no text is skipped. An ``01.wav`` already in the folder is left
    as it is, and ``01.txt`` is rewritten from the stored transcript.

    Args:
        taps_dir: ``data/output/<book_id>/14_taps``.
        group: A group that already holds its transcript.

    Returns:
        How many transcript files were written.
    """
    if not group.get("transcript"):
        return 0
    by_id = {
        row["id"]: row["md5"]
        for row in (group.get("taps") or [])
        if isinstance(row, dict) and row.get("id") and row.get("md5")
    }
    written = 0
    for tap_id in tap_ids(group):
        digest = by_id.get(tap_id)
        if not digest:
            continue
        text = paragraphs_text(paragraphs_for_tap(group, tap_id))
        folder = taps_dir / digest
        folder.mkdir(parents=True, exist_ok=True)
        if not text.strip():
            continue
        (folder / TRANSCRIPT_FILE).write_text(text, encoding="utf-8")
        written += 1
    return written


class BookStore:
    """The working JSON, written after each successful transcript."""

    def __init__(self, path: Path, book: dict[str, Any], taps_dir: Path) -> None:
        self.path = path
        self.book = book
        self.taps_dir = taps_dir
        self._lock = threading.Lock()

    def save(self, page_key: str, group_id: str, transcript: dict, generation: dict) -> None:
        """Attach a transcript to its group, write each tap's 01.txt, and rewrite the file."""
        with self._lock:
            for group in self.book["pages"][page_key]:
                if group["id"] == group_id:
                    group["transcript"] = transcript
                    group["transcript_generation"] = generation
                    write_tap_transcripts(self.taps_dir, group)
                    break
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(self.book, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self.book)


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


def page_count(pdf_path: Path) -> int:
    """How many pages a section PDF runs to."""
    if not pdf_path.is_file():
        raise RuntimeError(f"no paper PDF at {pdf_path}")
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        return len(document)
    finally:
        document.close()


def prompt_meta(book: dict[str, Any]) -> dict[str, str]:
    """The book's profile as the working JSON carries it, for assemble_transcript()."""
    return {key: str(book.get(key, "")) for key in PROMPT_META_KEYS}


def shape_of(group: dict[str, Any]) -> str:
    """Which envelope this group returns: a set is multipart, a lone exercise is not."""
    return "single" if group.get("kind") == "exercise" else "multipart"


def model_exercise(exercise: dict[str, Any]) -> dict[str, Any]:
    """The id and answer of one exercise. The crop carries its printed wording."""
    row: dict[str, Any] = {"id": exercise["id"]}
    if "answer" in exercise:
        row["answer"] = exercise["answer"]
    return row


def model_group(group: dict[str, Any]) -> dict[str, Any]:
    """The group id, its objective id, and each exercise id with its answer.

    Printed wording, options, figures, and the stem are on the crop. The section
    spec, sent separately, holds the objective text.
    """
    row: dict[str, Any] = {"id": group["id"]}
    if group.get("objective"):
        row["objective"] = group["objective"]
    row["exercises"] = [model_exercise(exercise) for exercise in group["exercises"]]
    return row


def exercise_ids(group: dict[str, Any]) -> list[str]:
    """The printed exercise ids, in the order the paper numbers them."""
    return [exercise["id"] for exercise in group["exercises"]]


def group_crop_boxes(group: dict[str, Any]) -> list[list[float]]:
    """The union quads transcripts crop, from nested or legacy `bboxes`."""
    raw = group.get("bboxes")
    if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "group" in raw[0]:
        quads = raw[0].get("group") or []
        return [list(quad) for quad in quads]
    quads: list[list[float]] = []
    for item in raw or []:
        if isinstance(item, dict) and "box" in item:
            quads.append(list(item["box"]))
        elif isinstance(item, (list, tuple)) and len(item) == 4:
            quads.append([float(item[0]), float(item[1]), float(item[2]), float(item[3])])
    return quads


def crop_png(
    image: Image.Image,
    width_pt: float,
    height_pt: float,
    bboxes: list[list[float]],
) -> bytes:
    """PNG bytes of the union of `bboxes` on a rendered page.

    Boxes are PDF points, origin top-left, the same ``[x0, y0, x1, y1]`` quads
    stored on the working JSON and painted on the bboxes PDF. A few points of
    padding keep the crop from sitting on the box itself.
    """
    if not bboxes:
        raise ValueError("no bounding boxes to crop")
    pad = CROP_PAD_PT
    left_pt = min(min(box[0], box[2]) for box in bboxes) - pad
    top_pt = min(min(box[1], box[3]) for box in bboxes) - pad
    right_pt = max(max(box[0], box[2]) for box in bboxes) + pad
    bottom_pt = max(max(box[1], box[3]) for box in bboxes) + pad
    scale_x = image.width / width_pt
    scale_y = image.height / height_pt
    left = max(0, int(left_pt * scale_x))
    right = min(image.width, int(right_pt * scale_x))
    top = max(0, int(top_pt * scale_y))
    bottom = min(image.height, int(bottom_pt * scale_y))
    if right <= left or bottom <= top:
        raise ValueError("crop is empty")
    cropped = image.crop((left, top, right, bottom))
    buffer = io.BytesIO()
    cropped.save(buffer, "PNG")
    return buffer.getvalue()


def stitch_pngs(parts: Sequence[bytes]) -> bytes:
    """Stack page crops top to bottom so a split set is one image."""
    if len(parts) == 1:
        return parts[0]
    images = [Image.open(io.BytesIO(part)).convert("RGB") for part in parts]
    width = max(image.width for image in images)
    height = sum(image.height for image in images) + STITCH_GAP_PX * (len(images) - 1)
    stacked = Image.new("RGB", (width, height), "white")
    top = 0
    for image in images:
        stacked.paste(image, (0, top))
        top += image.height + STITCH_GAP_PX
    buffer = io.BytesIO()
    stacked.save(buffer, "PNG")
    return buffer.getvalue()


def group_png(run: Run, job: Job) -> bytes:
    """The first-page crop, plus later pages of the same printed set."""
    pdf_path = run.papers_dir / f"{job.paper.stem}.pdf"
    parts = [run.cache.crop(pdf_path, job.pdf_index, group_crop_boxes(job.group))]
    for pdf_index, bboxes in job.more_pages:
        parts.append(
            run.cache.crop(pdf_path, pdf_index, [list(box) for box in bboxes])
        )
    return stitch_pngs(parts)


def clean_reply(reply: SingleReply | MultipartReply) -> None:
    """Rewrite every sentence's math tags in place.

    Args:
        reply: A parsed transcript. Each sentence is replaced with the cleaned
            form before validation stores it.
    """
    blocks = (
        reply.paragraphs
        if isinstance(reply, SingleReply)
        else [
            *reply.stem.paragraphs,
            *(paragraph for part in reply.parts for paragraph in part.paragraphs),
        ]
    )
    for paragraph in blocks:
        paragraph.sentences = [clean_math_tags(sentence) for sentence in paragraph.sentences]


def sentences_of(reply: SingleReply | MultipartReply) -> Iterator[str]:
    """Every spoken sentence in a transcript, in order."""
    blocks = (
        reply.paragraphs
        if isinstance(reply, SingleReply)
        else [
            *reply.stem.paragraphs,
            *(paragraph for part in reply.parts for paragraph in part.paragraphs),
        ]
    )
    for paragraph in blocks:
        yield from paragraph.sentences


def math_problems(text: str) -> list[str]:
    """Reasons a sentence's math tags cannot be used."""
    problems: list[str] = []
    lower = text.lower()
    if lower.count("<math") != lower.count("</math>"):
        problems.append("unbalanced <math> tags")
        return problems
    for attrs, body in MATH_TAG.findall(text):
        if "<math" in body.lower():
            problems.append("nested <math> tags")
        spoken = SPOKEN_ATTR.search(attrs)
        if spoken is None or not spoken.group(1).strip():
            problems.append("a <math> tag has no spoken attribute")
    return problems


def transcript_problems(
    reply: SingleReply | MultipartReply,
    ids: list[str],
) -> list[str]:
    """Every reason a reply cannot be stored."""
    problems: list[str] = []
    if isinstance(reply, MultipartReply):
        named = [part.id for part in reply.parts]
        if named != ids:
            problems.append(
                f"parts are {named}, not the exercises {ids} in order"
            )
    for index, sentence in enumerate(sentences_of(reply), start=1):
        if not sentence.strip():
            problems.append(f"sentence {index} is empty")
            continue
        where = f"sentence {index}"
        problems.extend(f"{where} {problem}" for problem in math_problems(sentence))
    return problems


def check_transcript(
    reply: SingleReply | MultipartReply,
    ids: list[str],
) -> None:
    """Reject a transcript that cannot be used, reporting every reason at once."""
    problems = transcript_problems(reply, ids)
    if problems:
        raise ValueError("; ".join(problems))


def parse_reply(raw: str, shape: str) -> SingleReply | MultipartReply:
    """Parse the model's JSON as the envelope this group asked for."""
    if shape == "single":
        return SingleReply.model_validate_json(raw)
    return MultipartReply.model_validate_json(raw)


def schema_for(shape: str) -> type[BaseModel]:
    """The Pydantic envelope the call asks the model to fill."""
    return SingleReply if shape == "single" else MultipartReply


@dataclass(frozen=True)
class Run:
    """What every worker shares: client, prompts, papers, the book on disk."""

    client: genai.Client
    logger: ApiLogger
    book_id: str
    model: str
    thinking: str
    prompts: dict[str, Prompt]
    specs: dict[str, str]
    papers_dir: Path
    cache: PageCache
    store: BookStore
    progress: Progress
    pdf_hashes: dict[str, str] = field(default_factory=dict)
    update: bool = False
    section: str | None = None


@dataclass(frozen=True)
class Bundle:
    """One group's crop and source texts, ready to send."""

    job: Job
    png: bytes
    group_json: str
    spec_text: str
    digest: str


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def transcript_input_digest(
    run: Run, job: Job, prompt: str, group_json: str, spec_text: str
) -> str:
    """sha256 of the source inputs that determine the request, never of PNG bytes."""
    pdf_path = run.papers_dir / f"{job.paper.stem}.pdf"
    key = str(pdf_path)
    if key not in run.pdf_hashes:
        run.pdf_hashes[key] = file_sha256(pdf_path)
    payload = {
        "prompt": prompt,
        "group": group_json,
        "spec": spec_text,
        "pdf_sha256": run.pdf_hashes[key],
        "pdf_index": job.pdf_index,
        "bboxes": group_crop_boxes(job.group),
        "page_max_side_px": PAGE_MAX_SIDE_PX,
        "crop_pad_pt": CROP_PAD_PT,
    }
    if job.more_pages:
        payload["more_pages"] = [
            {"pdf_index": pdf_index, "bboxes": [list(box) for box in bboxes]}
            for pdf_index, bboxes in job.more_pages
        ]
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def bundle_of(run: Run, job: Job) -> Bundle:
    png = group_png(run, job)
    group_json = json.dumps(model_group(job.group), ensure_ascii=False, indent=2)
    spec_text = run.specs[job.paper.stem]
    prompt = run.prompts[job.shape].text
    digest = transcript_input_digest(run, job, prompt, group_json, spec_text)
    return Bundle(job, png, group_json, spec_text, digest)


def transcript_request(
    run: Run,
    job: Job,
    png: bytes,
    group_json: str,
    spec_text: str,
) -> tuple[list[Any], types.GenerateContentConfig]:
    """Contents and config shared by unary logged_call and the Batch InlinedRequest."""
    prompt = run.prompts[job.shape].text
    image_part = types.Part.from_bytes(data=png, mime_type="image/png")
    contents = [prompt, image_part, group_json, spec_text]
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema_for(job.shape),
        thinking_config=types.ThinkingConfig(thinking_level=run.thinking),
    )
    return contents, config


def log_key_for(run: Run, job: Job, attempt: int) -> str:
    return (
        f"{run.book_id}|{job.group['section']}|{job.group['id']}|"
        f"{job.page_key}|a{attempt}"
    )


def batch_request(
    run: Run,
    bundle: Bundle,
    attempt: int,
) -> types.InlinedRequest:
    contents, config = transcript_request(
        run, bundle.job, bundle.png, bundle.group_json, bundle.spec_text
    )
    return types.InlinedRequest(
        contents=contents,
        metadata={
            "item": bundle.job.item,
            "shape": bundle.job.shape,
            "log_key": log_key_for(run, bundle.job, attempt),
        },
        config=config,
    )


def transcript_log_extra(
    run: Run,
    job: Job,
    attempt: int,
    *,
    batch: bool,
    batch_name: str | None = None,
) -> dict[str, Any]:
    extra: dict[str, Any] = {
        "book_id": run.book_id,
        "section": job.group["section"],
        "group_id": job.group["id"],
        "page": job.page_key,
        "shape": job.shape,
        "thinking_level": run.thinking,
        "prompt_files": list(run.prompts[job.shape].files),
    }
    if batch:
        extra["batch"] = True
        extra["batch_key"] = log_key_for(run, job, attempt)
        if batch_name:
            extra["batch_name"] = batch_name
    return extra


def generation_record(
    run: Run,
    job: Job,
    attempt: int,
    duration: float,
    usage: dict[str, Any],
    *,
    batch: bool,
    batch_name: str | None = None,
) -> dict[str, Any]:
    generation: dict[str, Any] = {
        "model": run.model,
        "thinking_level": run.thinking,
        "attempt": attempt,
        "duration_seconds": round(duration, 3),
        "usage": usage,
        "prompt_files": list(run.prompts[job.shape].files),
    }
    if batch:
        generation["batch"] = True
        if batch_name:
            generation["batch_name"] = batch_name
    return generation


def log_transcript_call(
    run: Run,
    job: Job,
    prompt: str,
    png: bytes,
    group_json: str,
    spec_text: str,
    attempt: int,
    response: Any,
    result: SingleReply | MultipartReply | None,
    error_message: str | None,
    duration: float,
    *,
    batch: bool,
    batch_name: str | None = None,
) -> None:
    usage = genai_usage(response) if response is not None else {}
    safe_log(
        run.logger,
        files_builder=lambda captured=response: {
            "prompt.txt": prompt,
            "group.json": json.loads(group_json),
            "spec.json": json.loads(spec_text),
            "crop.png": png,
            **(genai_response_files(captured) if captured is not None else {}),
        },
        page_number=job.item,
        step_name=TRANSCRIPT_STEP,
        attempt=attempt,
        api="gemini",
        model=run.model,
        usage=usage,
        duration_seconds=duration,
        status="success" if result is not None else "error",
        error_message=error_message,
        extra=transcript_log_extra(
            run, job, attempt, batch=batch, batch_name=batch_name
        ),
    )


def logged_call(
    run: Run,
    job: Job,
    prompt: str,
    png: bytes,
    group_json: str,
    spec_text: str,
    attempt: int,
) -> tuple[SingleReply | MultipartReply | None, dict[str, Any], str | None]:
    """Ask for one group's transcript once, logging the call however it turns out."""
    contents, config = transcript_request(run, job, png, group_json, spec_text)
    response = None
    result = None
    error_message = None
    started = time.perf_counter()
    try:
        response = run.client.models.generate_content(
            model=run.model,
            contents=contents,
            config=config,
        )
        result = parse_reply(genai_answer_text(response), job.shape)
        clean_reply(result)
        check_transcript(result, exercise_ids(job.group))
    except Exception as error:  # noqa: BLE001 - retry transport, parse and rule failures alike
        error_message = f"{type(error).__name__}: {error}"
        result = None
    duration = time.perf_counter() - started
    usage = genai_usage(response) if response is not None else {}
    generation = generation_record(
        run, job, attempt, duration, usage, batch=False
    )
    log_transcript_call(
        run,
        job,
        prompt,
        png,
        group_json,
        spec_text,
        attempt,
        response,
        result,
        error_message,
        duration,
        batch=False,
    )
    return result, generation, error_message


def request_transcript(
    run: Run,
    job: Job,
    png: bytes,
    group_json: str,
    spec_text: str,
    report: Callable[[str], None] | None = None,
) -> tuple[SingleReply | MultipartReply, dict[str, Any]]:
    """Call until a usable transcript arrives, retrying every failure."""
    prompt = run.prompts[job.shape].text
    last_error: str | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        result, generation, last_error = logged_call(
            run, job, prompt, png, group_json, spec_text, attempt
        )
        if result is not None:
            return result, generation
        if attempt < MAX_ATTEMPTS:
            if report is not None:
                report(
                    f"attempt {attempt} of {MAX_ATTEMPTS} failed, "
                    f"{one_line(last_error or 'no reason given')}; "
                    f"asking again in {2**attempt}s"
                )
            time.sleep(2**attempt)
    raise RuntimeError(f"failed after {MAX_ATTEMPTS} attempts: {last_error}")


def transcribe_group(run: Run, job: Job) -> tuple[str, str]:
    """Transcribe one group, returning what became of it and the line to print."""
    started = time.perf_counter()
    png = group_png(run, job)
    payload = model_group(job.group)
    group_json = json.dumps(payload, ensure_ascii=False, indent=2)
    spec_text = run.specs[job.paper.stem]
    run.progress.asking(
        job.item,
        f"{job.shape}, {in_kb(len(group_json.encode('utf-8')) + len(spec_text.encode('utf-8')) + len(png))} "
        f"sent, asking the model",
    )
    reply, generation = request_transcript(
        run,
        job,
        png,
        group_json,
        spec_text,
        report=lambda message: run.progress.say(f"  {job.item}: {message}"),
    )
    run.store.save(job.page_key, job.group["id"], reply.model_dump(), generation)
    elapsed = since(time.perf_counter() - started)
    return "written", f"  {job.item}: {job.shape} ({elapsed})"


def parse_transcript_response(
    run: Run, job: Job, response: Any
) -> SingleReply | MultipartReply:
    result = parse_reply(genai_answer_text(response), job.shape)
    clean_reply(result)
    check_transcript(result, exercise_ids(job.group))
    return result


def apply_transcript_row(
    run: Run,
    bundle: Bundle,
    row: Any,
    attempt: int,
    elapsed: float,
    batch_name: str,
) -> tuple[bool, str | None]:
    """Turn one Batch row into a stored transcript + log. Never maps by position."""
    job = bundle.job
    prompt = run.prompts[job.shape].text
    response, error_message = response_and_error(row)
    parsed = None
    result = None
    if error_message is None:
        try:
            if response is None:
                raise ValueError("no batch response for this request")
            parsed = generate_content_response(response)
            result = parse_transcript_response(run, job, parsed)
        except Exception as error:  # noqa: BLE001 - same retry story as unary
            error_message = f"{type(error).__name__}: {error}"
            result = None
    usage = genai_usage(parsed) if parsed is not None else {}
    generation = generation_record(
        run, job, attempt, elapsed, usage, batch=True, batch_name=batch_name
    )
    log_transcript_call(
        run,
        job,
        prompt,
        bundle.png,
        bundle.group_json,
        bundle.spec_text,
        attempt,
        parsed,
        result,
        error_message,
        elapsed,
        batch=True,
        batch_name=batch_name,
    )
    if result is None:
        return False, error_message
    run.store.save(job.page_key, job.group["id"], result.model_dump(), generation)
    return True, None


def apply_batch_rows(
    run: Run,
    bundles: list[Bundle],
    rows: list[Any],
    keys: dict[str, str],
    attempt: int,
    batch_name: str,
    elapsed: float,
) -> list[tuple[Job, bool, str | None]]:
    """Apply mapped rows for bundles still pending. Unknown keys are ignored."""
    by_item = map_rows_by_key(rows)
    results: list[tuple[Job, bool, str | None]] = []
    pending_items = {bundle.job.item for bundle in bundles}
    for bundle in bundles:
        job = bundle.job
        if job.item not in pending_items:
            continue
        saved_hash = keys.get(job.item)
        if saved_hash and saved_hash != bundle.digest:
            print(
                f"  {job.item}: inputs changed since submit; will resubmit",
                flush=True,
            )
            results.append((job, False, "stale batch row"))
            continue
        row = by_item.get(job.item)
        if row is None:
            log_transcript_call(
                run,
                job,
                run.prompts[job.shape].text,
                bundle.png,
                bundle.group_json,
                bundle.spec_text,
                attempt,
                None,
                None,
                "no batch response for this request",
                elapsed,
                batch=True,
                batch_name=batch_name,
            )
            results.append((job, False, "no batch response for this request"))
            continue
        ok, error_message = apply_transcript_row(
            run, bundle, row, attempt, elapsed, batch_name
        )
        results.append((job, ok, error_message))
    return results


def state_envelope(
    run: Run,
    *,
    update: bool,
) -> dict[str, Any]:
    return {
        "stage": BATCH_STAGE,
        "book_id": run.book_id,
        "model": run.model,
        "thinking": run.thinking,
        "section": run.section,
        "update": update,
    }


def build_transcript_chunk(
    run: Run, bundles: list[Bundle], attempt: int, label: str
) -> BatchChunk:
    """One inline or JSONL Batch job for these bundles."""
    display = batch_display_name(
        run.book_id, BATCH_STAGE, label, run_id=new_run_id()
    )
    keys = {bundle.job.item: bundle.digest for bundle in bundles}

    def build_src() -> tuple[Any, str | None]:
        pairs = [
            (bundle.job.item, batch_request(run, bundle, attempt))
            for bundle in bundles
        ]
        return src_for_chunk(run.client, pairs, display)

    return BatchChunk(
        label=label,
        display_name=display,
        attempt=attempt,
        keys=keys,
        items=list(bundles),
        build_src=build_src,
    )


def adopted_transcript_chunk(
    entry: dict[str, Any], by_item: dict[str, Bundle]
) -> BatchChunk:
    keys = {
        str(key): str(digest)
        for key, digest in (entry.get("keys") or {}).items()
    }
    items = [by_item[key] for key in keys if key in by_item]
    return BatchChunk(
        label=str(entry.get("display_name") or "resume"),
        display_name=str(entry.get("display_name") or ""),
        attempt=int(entry.get("attempt") or 1),
        keys=keys,
        items=items,
        build_src=None,
    )


def transcribe_unary_round(
    run: Run, jobs: list[Job], attempt: int
) -> tuple[int, list[Job], list[str]]:
    """Send each remaining item through logged_call at this attempt number."""
    written = 0
    still: list[Job] = []
    failures: list[str] = []
    for job in jobs:
        bundle = bundle_of(run, job)
        prompt = run.prompts[job.shape].text
        run.progress.asking(
            job.item,
            f"{job.shape}, {in_kb(len(bundle.group_json.encode('utf-8')) + len(bundle.spec_text.encode('utf-8')) + len(bundle.png))} "
            f"sent, unary fallback a{attempt}",
        )
        result, generation, error_message = logged_call(
            run,
            job,
            prompt,
            bundle.png,
            bundle.group_json,
            bundle.spec_text,
            attempt,
        )
        if result is not None:
            run.store.save(
                job.page_key, job.group["id"], result.model_dump(), generation
            )
            written += 1
            run.progress.done(job.item, f"  {job.item}: {job.shape}")
        elif attempt < MAX_ATTEMPTS:
            still.append(job)
            run.progress.say(
                f"  {job.item}: attempt {attempt} of {MAX_ATTEMPTS} failed, "
                f"{one_line(error_message or 'no reason given')}; asking again"
            )
        else:
            failures.append(
                f"{job.item}: failed after {MAX_ATTEMPTS} attempts: {error_message}"
            )
            run.progress.done(
                job.item,
                f"  {job.item}: FAILED {one_line(error_message or 'no reason given')}",
            )
    return written, still, failures


def _transcript_chunks_for(
    run: Run,
    leftover_jobs: list[Job],
    by_item: dict[str, Bundle],
    attempt: int,
) -> list[BatchChunk]:
    leftover_bundles = [
        by_item[job.item] if job.item in by_item else bundle_of(run, job)
        for job in leftover_jobs
    ]
    bundle_by_item = {bundle.job.item: bundle for bundle in leftover_bundles}
    pairs = [
        (bundle.job.item, batch_request(run, bundle, attempt))
        for bundle in leftover_bundles
    ]
    return [
        build_transcript_chunk(
            run,
            [bundle_by_item[item] for item, _ in request_chunk],
            attempt,
            f"a{attempt}-b{index}",
        )
        for index, request_chunk in enumerate(chunk_requests(pairs), start=1)
    ]


def transcribe_groups_batch(
    run: Run,
    jobs: list[Job],
    state: BatchState,
    update: bool,
    unary_fallback: int,
    *,
    adopted: list[dict[str, Any]] | None = None,
    parallel: int = 0,
    submit_leftovers: bool = True,
    prefetched: dict[str, BatchStatus] | None = None,
    download_workers: int = DEFAULT_DOWNLOAD_WORKERS,
) -> tuple[int, list[str], set[str]]:
    """Transcribe pending groups through Batch, retrying failed ones up to MAX_ATTEMPTS.

    Attempt 1 is the single owner of overlap: adopted jobs plus leftover chunks.
    """
    written = 0
    failures: list[str] = []
    abandoned_keys: set[str] = set()
    pending = list(jobs)
    adopted_entries = list(adopted or [])
    envelope = state_envelope(run, update=update)
    finalized: set[str] = set()

    def account(
        job: Job,
        ok: bool,
        error_message: str | None,
        round_attempt: int,
        still: list[Job],
        job_name: str | None,
    ) -> None:
        nonlocal written
        if job in still or job.item in finalized:
            return
        if ok or transcript_is_applied(job.group, run.update, job_name):
            written += 1
            finalized.add(job.item)
            run.progress.done(job.item, f"  {job.item}: {job.shape}")
            return
        if round_attempt < MAX_ATTEMPTS:
            still.append(job)
            run.progress.say(
                f"  {job.item}: attempt {round_attempt} of {MAX_ATTEMPTS} failed, "
                f"{one_line(error_message or 'no reason given')}; asking again"
            )
            return
        failures.append(
            f"{job.item}: failed after {MAX_ATTEMPTS} attempts: {error_message}"
        )
        finalized.add(job.item)
        run.progress.done(
            job.item,
            f"  {job.item}: FAILED {one_line(error_message or 'no reason given')}",
        )

    for attempt in range(1, MAX_ATTEMPTS + 1):
        if not pending and not (attempt == 1 and adopted_entries):
            break
        leftover_for_unary = [
            job
            for job in pending
            if attempt > 1
            or job.item not in {
                key
                for entry in adopted_entries
                for key in (entry.get("keys") or {})
            }
        ]
        if (
            unary_fallback > 0
            and attempt == MAX_ATTEMPTS
            and not adopted_entries
            and 0 < len(leftover_for_unary) <= unary_fallback
        ):
            got, pending, round_failures = transcribe_unary_round(
                run, leftover_for_unary, attempt
            )
            written += got
            failures.extend(round_failures)
            continue
        still: list[Job] = []
        by_item = {bundle.job.item: bundle for bundle in (bundle_of(run, job) for job in pending)}
        adopted_keys: set[str] = set()
        adopted_pairs: list[tuple[dict[str, Any], BatchChunk]] = []
        if attempt == 1:
            for entry in adopted_entries:
                keys = entry.get("keys") or {}
                adopted_keys.update(str(key) for key in keys)
                chunk = adopted_transcript_chunk(entry, by_item)
                for key in keys:
                    key = str(key)
                    if key in by_item:
                        continue
                    found = group_for_item(run.store.book, key)
                    if found and transcript_is_applied(
                        found[1], run.update, entry.get("name")
                    ):
                        print(f"  {key}: already on disk", flush=True)
                        continue
                    failures.append(f"{key}: adopted key missing from pending work")
                    run.progress.done(
                        key, f"  {key}: FAILED adopted key missing"
                    )
                adopted_pairs.append((entry, chunk))
        leftover_jobs = [
            job
            for job in pending
            if job.item not in adopted_keys and submit_leftovers
        ]
        queue = _transcript_chunks_for(run, leftover_jobs, by_item, attempt)

        def on_submit(chunk: BatchChunk) -> None:
            for bundle in chunk.items:
                job = bundle.job
                run.progress.asking(
                    job.item,
                    f"{job.shape}, {in_kb(len(bundle.group_json.encode('utf-8')) + len(bundle.spec_text.encode('utf-8')) + len(bundle.png))} "
                    f"sent, in batch {chunk.label}",
                )

        def fetch_apply(
            entry: dict[str, Any],
            chunk: BatchChunk,
            status: BatchStatus,
            elapsed: float,
        ) -> tuple[str, list[Any], str | None]:
            name = str(entry.get("name") or status.name or "")
            log_attempt = int(entry.get("attempt") or chunk.attempt)
            remote = status.job
            if remote is None:
                remote = get_batch_job(
                    run.client, name, timeout_ms=FULL_GET_TIMEOUT_MS
                )
            token = finish_token(remote)
            result_file = result_file_name(remote) if remote is not None else None
            if token != FINISH_DONE:
                return token, [], result_file
            book = run.store.snapshot() if run.update else run.store.book
            outcomes: list[Any] = []
            to_apply: list[Bundle] = []
            for bundle in chunk.items:
                found = group_for_item(book, bundle.job.item)
                group = found[1] if found else bundle.job.group
                if transcript_is_applied(group, run.update, name):
                    outcomes.append((bundle.job, True, None, "already on disk"))
                    continue
                to_apply.append(bundle)
            if not to_apply:
                return FINISH_DONE, outcomes, result_file
            try:
                rows = inlined_responses(run.client, remote)
                for bundle in to_apply:
                    results = apply_batch_rows(
                        run, [bundle], rows, chunk.keys, log_attempt, name, elapsed
                    )
                    for item_job, ok, error_message in results:
                        outcomes.append((item_job, ok, error_message, None))
            except ApplyError:
                raise
            except Exception as error:
                accounted = {item_job.item for item_job, *_rest in outcomes}
                for bundle in to_apply:
                    if bundle.job.item in accounted:
                        continue
                    if transcript_is_applied(bundle.job.group, run.update, name):
                        outcomes.append((bundle.job, True, None, None))
                raise ApplyError(
                    f"{type(error).__name__}: {error}", outcomes
                ) from error
            return FINISH_DONE, outcomes, result_file

        def account_outcomes(chunk: BatchChunk, outcomes: list[Any]) -> None:
            for item_job, ok, error_message, note in outcomes:
                if note == "already on disk":
                    print(f"  {item_job.item}: already on disk", flush=True)
                account(item_job, ok, error_message, attempt, still, None)

        def on_failed(chunk: BatchChunk, message: str) -> None:
            if attempt < MAX_ATTEMPTS:
                print(
                    f"  {chunk.label} failed: {one_line(message)}; will retry",
                    flush=True,
                )
            for bundle in chunk.items:
                job = bundle.job
                if transcript_is_applied(job.group, run.update, None):
                    account(job, True, None, attempt, still, None)
                    continue
                account(job, False, message, attempt, still, None)

        def on_abandoned(chunk: BatchChunk, message: str) -> None:
            for bundle in chunk.items:
                job = bundle.job
                abandoned_keys.add(job.item)
                if job.item in finalized:
                    continue
                finalized.add(job.item)
                failures.append(f"{job.item}: {message}")
                run.progress.done(
                    job.item,
                    f"  {job.item}: FAILED {one_line(message)}",
                )

        def on_requeue(chunk: BatchChunk) -> list[BatchChunk]:
            leftover: list[Job] = []
            for bundle in chunk.items:
                job = bundle.job
                if transcript_is_applied(job.group, run.update, None):
                    account(job, True, None, attempt, still, None)
                    continue
                leftover.append(job)
            if not submit_leftovers:
                return []
            return _transcript_chunks_for(run, leftover, by_item, attempt)

        run_batch_jobs(
            run.client,
            run.model,
            state,
            envelope,
            queue,
            adopted_pairs if attempt == 1 else [],
            parallel,
            attempt,
            fetch_apply,
            on_failed,
            log=print,
            on_submit=on_submit,
            prefetched=prefetched if attempt == 1 else {},
            download_workers=download_workers,
            on_account=account_outcomes,
            on_abandoned=on_abandoned,
            on_requeue=on_requeue,
        )
        adopted_entries = []
        pending = still
        if pending and attempt < MAX_ATTEMPTS:
            time.sleep(2**attempt)
    for job in pending:
        failures.append(f"{job.item}: still pending after {MAX_ATTEMPTS} attempts")
        run.progress.done(job.item, f"  {job.item}: FAILED still pending")
    return written, failures, abandoned_keys


def group_for_item(
    book: dict[str, Any], item: str
) -> tuple[str, dict[str, Any]] | None:
    for page_key, groups in book.get("pages", {}).items():
        for group in groups:
            if f"{page_key}_{group.get('id')}" == item:
                return str(page_key), group
    return None


def transcript_is_applied(
    group: dict[str, Any], update: bool, job_name: str | None
) -> bool:
    """A key is applied when the group holds a transcript, and on --update the batch_name matches."""
    if not group.get("transcript"):
        return False
    if not update:
        return True
    generation = group.get("transcript_generation") or {}
    return bool(job_name) and generation.get("batch_name") == job_name


def saved_keys_pending(book: dict[str, Any], data: dict[str, Any]) -> list[str]:
    update = bool(data.get("update"))
    pending: list[str] = []
    for entry in entries(data):
        name = entry.get("name")
        for key in entry.get("keys") or {}:
            found = group_for_item(book, key)
            if found is None:
                pending.append(str(key))
                continue
            _, group = found
            if not transcript_is_applied(group, update, name):
                pending.append(str(key))
    return pending


def state_mismatch(
    data: dict[str, Any],
    book_id: str,
    model: str,
    thinking: str,
    section: str | None,
) -> str | None:
    if data.get("stage") and data.get("stage") != BATCH_STAGE:
        return (
            f"saved batch state is for stage {data.get('stage')}, not {BATCH_STAGE}"
        )
    if data.get("book_id") and data.get("book_id") != book_id:
        return f"saved batch state is for {data.get('book_id')}, not {book_id}"
    if data.get("model") and data.get("model") != model:
        return f"saved batch state used model {data.get('model')}, not {model}"
    if data.get("thinking") and data.get("thinking") != thinking:
        return (
            f"saved batch state used thinking {data.get('thinking')}, not {thinking}"
        )
    if data.get("section") != section:
        saved = data.get("section") or "the whole book"
        now = section or "the whole book"
        return f"saved batch state is for {saved}, not {now}"
    return None


def resume_saved_jobs(
    run: Run, state: BatchState
) -> tuple[list[dict[str, Any]], dict[str, BatchStatus]]:
    """Adopt saved entries (status only). Never start a full get and never create."""
    data = state.load()
    if not data:
        return [], {}
    current = entries(data)
    if not current:
        return [], {}
    legacy = is_legacy(data)
    prefix = "adopted legacy job" if legacy else "adopted job"
    if legacy:
        state.save_entries(
            {key: value for key, value in data.items() if key != "job"},
            current,
        )
    null_names = [
        str(entry.get("display_name") or "")
        for entry in current
        if not entry.get("name") and entry.get("display_name")
    ]
    found_map = (
        find_jobs_by_display_names(run.client, null_names) if null_names else {}
    )
    adopted: list[dict[str, Any]] = []
    prefetched: dict[str, BatchStatus] = {}
    for entry in list(current):
        display = str(entry.get("display_name") or "")
        name = entry.get("name")
        if not name:
            matches = found_map.get(display) or []
            if not matches:
                print(
                    "  no remote batch job for saved display_name; will resubmit",
                    flush=True,
                )
                state.remove_entry(display)
                continue
            if len(matches) == 1:
                remote, warning = matches[0], ""
            else:
                remote, warning = pick_duplicate_match(matches)
            if warning:
                print(f"  {warning}", flush=True)
            if remote is None:
                print(
                    "  all matching remote jobs terminal-failed; will resubmit",
                    flush=True,
                )
                state.remove_entry(display)
                continue
            name = remote.name
            state.set_entry_field(display, "name", name)
            entry["name"] = name
        print(f"  {prefix} {name} (status only)", flush=True)
        status = get_batch_status(run.client, str(name))
        if status is None:
            print(f"  saved batch job {name} is gone; will resubmit", flush=True)
            state.remove_entry(display)
            continue
        if status.state in BATCH_FAILED:
            print(f"  saved job ended {status.state}; will resubmit", flush=True)
            if status.job is not None:
                delete_files(
                    run.client,
                    entry.get("src_file_name"),
                    result_file_name(status.job),
                )
            else:
                delete_files(run.client, entry.get("src_file_name"))
            state.remove_entry(display)
            continue
        adopted.append(entry)
        if status.state != "UNKNOWN":
            prefetched[str(name)] = status
    return adopted, prefetched


def paper_index(book_id: str) -> list[PaperRef]:
    """Section papers in book order, with the page they start on."""
    documents = accepted_sections(book_id, None)
    if not documents:
        raise RuntimeError(f"no sections to transcribe for {book_id}")
    front = frontmatter.front_matter(book_id)
    if front is None:
        raise RuntimeError(
            f"no front matter has been set for {book_id} (run frontmatter.py)"
        )
    papers_dir = OUTPUT_DIR / book_id / PAPERS_STAGE
    refs: list[PaperRef] = []
    first = front.pages + 1
    for stem, document in documents:
        pdf_path = papers_dir / f"{stem}.pdf"
        pages = page_count(pdf_path)
        refs.append(
            PaperRef(
                number=document["number"],
                stem=stem,
                first=first,
                pages=pages,
            )
        )
        first += pages
    return refs


def paper_for(refs: list[PaperRef], number: str) -> PaperRef:
    """The paper of one section number, or a hard failure."""
    for paper in refs:
        if paper.number == number:
            return paper
    raise RuntimeError(f"no paper for section {number}")


def load_specs(book_id: str, refs: list[PaperRef]) -> dict[str, str]:
    """Each section spec as the text the call sends, keyed by stem."""
    folder = OUTPUT_DIR / book_id / SPECS_STAGE
    specs: dict[str, str] = {}
    missing: list[str] = []
    for paper in refs:
        path = folder / f"{paper.stem}.json"
        if not path.is_file():
            missing.append(paper.number)
            continue
        specs[paper.stem] = path.read_text(encoding="utf-8")
    if missing:
        raise RuntimeError(
            f"{len(missing)} section(s) have no spec ({', '.join(missing)}); "
            "run specs.py first"
        )
    return specs


def iter_unique_groups(
    book: dict[str, Any], section: str | None
) -> list[dict[str, Any]]:
    """Printed groups once each, in page order, matching collect_jobs."""
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for _page_key, groups in sorted((book.get("pages") or {}).items()):
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            if section and group.get("section") != section:
                continue
            identity = (str(group.get("section") or ""), str(group.get("id") or ""))
            if identity in seen:
                continue
            seen.add(identity)
            found.append(group)
    return found


def expected_transcript_txt_paths(
    book: dict[str, Any], taps_dir: Path, section: str | None
) -> list[Path]:
    """``01.txt`` paths every in-scope group should have, with no writes.

    A group that already holds a transcript uses the same taps
    ``write_tap_transcripts`` would write. A group still waiting for a
    transcript still counts each tap with an md5, so the stage is not
    treated as done.
    """
    paths: list[Path] = []
    for group in iter_unique_groups(book, section):
        by_id = {
            row["id"]: row["md5"]
            for row in (group.get("taps") or [])
            if isinstance(row, dict) and row.get("id") and row.get("md5")
        }
        if group.get("transcript"):
            tap_keys = tap_ids(group)
        else:
            tap_keys = [
                str(row.get("id") or "")
                for row in (group.get("taps") or [])
                if isinstance(row, dict) and row.get("id")
            ]
        for tap_id in tap_keys:
            digest = by_id.get(tap_id)
            if not digest:
                continue
            if group.get("transcript"):
                text = paragraphs_text(paragraphs_for_tap(group, tap_id))
                if not text.strip():
                    continue
            paths.append(taps_dir / digest / TRANSCRIPT_FILE)
    return paths


def transcript_txt_counts(
    book: dict[str, Any], taps_dir: Path, section: str | None
) -> tuple[int, int]:
    """How many expected tap transcripts exist, and how many there should be."""
    paths = expected_transcript_txt_paths(book, taps_dir, section)
    have = sum(1 for path in paths if path.is_file())
    return have, len(paths)


def collect_jobs(
    book: dict[str, Any],
    refs: list[PaperRef],
    section: str | None,
    update: bool,
    taps_dir: Path,
) -> tuple[list[Job], int]:
    """Jobs still to run, and how many groups already hold a transcript.

    A group that already holds a transcript keeps it, and its ``01.txt`` files
    are written here so a resume does not wait for a later step to make them.
    """
    jobs: list[Job] = []
    skipped = 0
    # A set the paper divided is stored once on each page it occupies. The
    # transcript belongs to the set, and the earliest page is the one that
    # carries it; later pages are extra crops of the same set.
    seen: dict[tuple[str, str], int] = {}
    extra_pages: dict[
        tuple[str, str], list[tuple[int, tuple[tuple[float, ...], ...]]]
    ] = {}
    for page_key, groups in sorted(book["pages"].items()):
        book_page = int(page_key)
        for group in groups:
            if section and group["section"] != section:
                continue
            identity = (str(group.get("section") or ""), str(group.get("id") or ""))
            paper = paper_for(refs, group["section"])
            pdf_index = book_page - paper.first
            if pdf_index < 0 or pdf_index >= paper.pages:
                raise RuntimeError(
                    f"{page_key} {group['id']}: page {book_page} is not in "
                    f"section {paper.number} (pages {paper.first}-"
                    f"{paper.first + paper.pages - 1})"
                )
            if identity in seen:
                boxes = group_crop_boxes(group)
                if boxes and seen[identity] >= 0:
                    extra_pages.setdefault(identity, []).append(
                        (
                            pdf_index,
                            tuple(tuple(float(x) for x in box) for box in boxes),
                        )
                    )
                continue
            if not update and group.get("transcript"):
                seen[identity] = -1
                write_tap_transcripts(taps_dir, group)
                skipped += 1
                continue
            seen[identity] = len(jobs)
            jobs.append(
                Job(
                    page_key=page_key,
                    group=group,
                    paper=paper,
                    pdf_index=pdf_index,
                    shape=shape_of(group),
                    item=f"{page_key}_{group['id']}",
                )
            )
    filled: list[Job] = []
    for job in jobs:
        identity = (
            str(job.group.get("section") or ""),
            str(job.group.get("id") or ""),
        )
        extra = extra_pages.get(identity)
        filled.append(replace(job, more_pages=tuple(extra)) if extra else job)
    return filled, skipped


def transcribe_groups_unary(run: Run, jobs: list[Job]) -> tuple[int, list[str]]:
    """The thread pool of unary generateContent calls (--no-batch)."""
    written = 0
    failures: list[str] = []
    workers = min(len(jobs), MAX_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(transcribe_group, run, job): job.item for job in jobs}
        for future in as_completed(futures):
            item = futures[future]
            try:
                outcome, line = future.result()
            except Exception as error:  # noqa: BLE001 - the others still stand
                failures.append(f"{item}: {type(error).__name__}: {error}")
                run.progress.done(
                    item,
                    f"  {item}: FAILED {type(error).__name__}: {one_line(str(error))}",
                )
                continue
            if outcome == "written":
                written += 1
            run.progress.done(item, line)
    return written, failures


def build_transcripts(
    book_id: str,
    section: str | None,
    model: str,
    thinking: str,
    update: bool,
    *,
    batch: bool = DEFAULT_BATCH,
    unary_fallback: int = 0,
    discard_batch_state: bool = False,
    batch_parallel: int = DEFAULT_BATCH_PARALLEL,
    download_workers: int = DEFAULT_DOWNLOAD_WORKERS,
) -> tuple[Path, int]:
    """Transcribe every pending group of a book, or of just one section."""
    groups_path = working_json(book_id)
    if not groups_path.is_file():
        raise RuntimeError(
            f"working JSON not found: {groups_path.relative_to(PROJECT_ROOT)}\n"
            f"run groups.py first: python3 scripts/groups.py {book_id}"
        )
    print(f"reading groups of {book_id}", flush=True)
    book = load_json(groups_path)
    refs = paper_index(book_id)
    if section and not any(paper.number == section for paper in refs):
        raise RuntimeError(f"no section {section} in {book_id}")
    taps_dir = OUTPUT_DIR / book_id / TAPS_STAGE
    book_dir = OUTPUT_DIR / book_id
    jobs, skipped = collect_jobs(book, refs, section, update, taps_dir)
    papers_dir = book_dir / PAPERS_STAGE
    state = BatchState(batch_state_path(book_dir, BATCH_STAGE))

    if discard_batch_state:
        if state.path.is_file():
            print(
                "  discarding local batch state "
                "(the remote job is not cancelled)",
                flush=True,
            )
        state.forget()

    data = state.load()
    if data:
        pending_saved = saved_keys_pending(book, data)
        if not pending_saved:
            print("  dropping fully-applied batch state", flush=True)
            state.forget()
            data = None
        else:
            reason = state_mismatch(data, book_id, model, thinking, section)
            if reason:
                raise RuntimeError(
                    f"{reason}. Pass --discard-batch-state to drop the local "
                    "JSON only (the remote job is not cancelled)."
                )

    if not jobs:
        already = f", {skipped} already written" if skipped else ""
        print(
            f"OK: nothing to transcribe{already} "
            f"in {groups_path.relative_to(PROJECT_ROOT)}; "
            f"tap folders under {taps_dir.relative_to(PROJECT_ROOT)}"
        )
        return groups_path, 0

    if not any(os.environ.get(name) for name in API_KEY_VARS):
        raise RuntimeError(f"set one of {' / '.join(API_KEY_VARS)} to call the model")

    lock: BatchLock | None = None
    if batch or state.path.is_file():
        lock = BatchLock(batch_lock_path(book_dir, BATCH_STAGE))
        lock.acquire()
    try:
        return _build_transcripts_locked(
            book_id,
            section,
            model,
            thinking,
            update,
            batch=batch,
            unary_fallback=unary_fallback,
            batch_parallel=batch_parallel,
            download_workers=download_workers,
            groups_path=groups_path,
            book=book,
            refs=refs,
            jobs=jobs,
            skipped=skipped,
            taps_dir=taps_dir,
            papers_dir=papers_dir,
            state=state,
        )
    except KeyboardInterrupt:
        if lock is not None:
            print("interrupted; batch state kept, rerun to resume", flush=True)
            sys.stderr.flush()
            os._exit(130)
        raise
    finally:
        if lock is not None:
            lock.release()


def _build_transcripts_locked(
    book_id: str,
    section: str | None,
    model: str,
    thinking: str,
    update: bool,
    *,
    batch: bool,
    unary_fallback: int,
    batch_parallel: int,
    download_workers: int,
    groups_path: Path,
    book: dict[str, Any],
    refs: list[PaperRef],
    jobs: list[Job],
    skipped: int,
    taps_dir: Path,
    papers_dir: Path,
    state: BatchState,
) -> tuple[Path, int]:
    meta = prompt_meta(book)
    prompt = assemble_transcript(meta)
    prompts = {"single": prompt, "multipart": prompt}
    specs = load_specs(
        book_id, list({job.paper.stem: job.paper for job in jobs}.values())
    )
    workers = min(len(jobs), MAX_WORKERS)
    if batch:
        opening = f"transcribing {len(jobs)} group(s) through the Batch API"
    else:
        opening = f"transcribing {len(jobs)} group(s) with {workers} worker(s)"
    if skipped:
        opening += f"; {skipped} already written"
    print(opening, flush=True)
    print("  opening the model client", flush=True)
    client = genai.Client()
    print("  setting the log clock", flush=True)
    logger = ApiLogger(OUTPUT_DIR / book_id)

    written = 0
    failures: list[str] = []
    with Progress(len(jobs), phases=("still with the model",)) as progress:
        run = Run(
            client=client,
            logger=logger,
            book_id=book_id,
            model=model,
            thinking=thinking,
            prompts=prompts,
            specs=specs,
            papers_dir=papers_dir,
            cache=PageCache(),
            store=BookStore(groups_path, book, taps_dir),
            progress=progress,
            pdf_hashes={},
            update=update,
            section=section,
        )
        adopted: list[dict[str, Any]] = []
        prefetched: dict[str, BatchStatus] = {}
        if entries(state.load()):
            adopted, prefetched = resume_saved_jobs(run, state)
        jobs, skipped = collect_jobs(book, refs, section, update, taps_dir)
        abandoned_keys: set[str] = set()
        if not jobs and not adopted:
            pass
        elif batch:
            got, failures, abandoned_keys = transcribe_groups_batch(
                run,
                jobs,
                state,
                update,
                unary_fallback,
                adopted=adopted,
                parallel=batch_parallel,
                prefetched=prefetched,
                download_workers=download_workers,
            )
            written += got
        else:
            if adopted:
                got, failures, abandoned_keys = transcribe_groups_batch(
                    run,
                    jobs,
                    state,
                    update,
                    0,
                    adopted=adopted,
                    parallel=batch_parallel,
                    submit_leftovers=False,
                    prefetched=prefetched,
                    download_workers=download_workers,
                )
                written += got
                jobs, skipped = collect_jobs(
                    book, refs, section, update, taps_dir
                )
            jobs = [job for job in jobs if job.item not in abandoned_keys]
            if jobs:
                got, unary_failures = transcribe_groups_unary(run, jobs)
                written += got
                failures.extend(unary_failures)

    summary = f"{written} group(s) transcribed"
    if skipped:
        summary += f", {skipped} already written"
    if failures:
        summary += f", {len(failures)} failed"
        print(
            "\n".join(f"  FAILED {line}" for line in failures),
            flush=True,
        )
    print(
        f"OK: {summary} in {groups_path.relative_to(PROJECT_ROOT)}; "
        f"tap folders under {taps_dir.relative_to(PROJECT_ROOT)}"
    )
    return groups_path, len(failures)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_id", help="Book ID, e.g. mathematics_ar_sa06_t2")
    parser.add_argument("-s", "--section", help="transcribe only this section, e.g. -s 1.1")
    parser.add_argument(
        "--update", action="store_true", help="rewrite transcripts that already exist"
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"model to call (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--thinking-level",
        default=DEFAULT_THINKING,
        choices=THINKING_LEVELS,
        help=f"how hard the model thinks (default: {DEFAULT_THINKING})",
    )
    parser.add_argument(
        "--batch",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_BATCH,
        help="transcribe through the Gemini Batch API (default); "
        "--no-batch calls unary generateContent with "
        f"{MAX_WORKERS} workers",
    )
    parser.add_argument(
        "--unary-fallback",
        type=int,
        default=0,
        metavar="N",
        help="before the last Batch round, send at most N remaining items "
        "through unary generateContent at the current attempt (default: 0)",
    )
    parser.add_argument(
        "--discard-batch-state",
        action="store_true",
        help="delete data/output/<book>/00_transcripts_batch_state.json only; "
        "does not cancel the remote Batch job",
    )
    parser.add_argument(
        "--batch-parallel",
        type=int,
        default=DEFAULT_BATCH_PARALLEL,
        metavar="N",
        help="0 = no cap (default); N = at most N jobs in flight",
    )
    parser.add_argument(
        "--download-workers",
        type=int,
        default=DEFAULT_DOWNLOAD_WORKERS,
        metavar="N",
        help="parallel downloads of finished batch jobs; 1 = serial; "
        "each worker can peak at ~1-1.5 GB for an audio job; "
        "use 2 or 1 when several books run",
    )
    args = parser.parse_args()
    if args.unary_fallback < 0:
        parser.error("--unary-fallback must be >= 0")
    if args.batch_parallel < 0:
        parser.error("--batch-parallel must be >= 0")
    if args.download_workers < 1:
        parser.error("--download-workers must be at least 1")
    try:
        _, failed = build_transcripts(
            args.book_id,
            args.section,
            args.model,
            args.thinking_level,
            args.update,
            batch=args.batch,
            unary_fallback=args.unary_fallback,
            discard_batch_state=args.discard_batch_state,
            batch_parallel=args.batch_parallel,
            download_workers=args.download_workers,
        )
    except RuntimeError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
