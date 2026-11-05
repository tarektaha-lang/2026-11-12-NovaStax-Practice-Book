#!/usr/bin/env python3
"""Align each stored WAV with the spoken form of its transcript.

WhisperX forced alignment, run locally: no API key and no network once the
wav2vec2 model is cached. One job per audio segment. A standalone exercise is
one alignment. A set is the stem plus each exercise. Math tags are not aligned
as markup: the spoken attribute is what the model hears, and the packaged
``01.json`` replaces that run with ``{"math": "$LaTeX$", "start", "end"}``.

audios.py must have written the WAVs first. A packaged alignment already on
disk is left unless --update is passed, and each success is written back
immediately so a stopped run can resume.

    python3 scripts/align.py mathematics_ar_sa06_t2
    python3 scripts/align.py mathematics_ar_sa06_t2 -s 1.1

Needs whisperx, torch and torchaudio, which are not in requirements.txt:
    pip install whisperx torch torchaudio
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# macOS + conda often ships two OpenMP runtimes. Without this, importing
# torch/whisperx aborts with OMP Error #15. Must be set before those imports.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    from src.api_logger import ApiLogger, safe_log
    from specs import Progress, in_kb, one_line, since
    from sources import OUTPUT_DIR, PROJECT_ROOT, working_json
except ModuleNotFoundError as error:  # pragma: no cover
    sys.exit(f"missing dependency ({error.name}): pip install -r requirements.txt")

try:
    import torch
    import whisperx
except ModuleNotFoundError as error:  # pragma: no cover
    sys.exit(
        f"missing dependency ({error.name}): pip install whisperx torch torchaudio"
    )

ALIGN_STEP = "align"
TAPS_STAGE = "14_taps"

DEFAULT_WORKERS = 4
MAX_ATTEMPTS = 3
MIN_AUDIO_SECONDS = 0.05
WHISPERX_SAMPLE_RATE = 16000

MATH_TAG = re.compile(r"<math(\s[^>]*)?>(.*?)</math>", re.DOTALL | re.IGNORECASE)
SPOKEN_ATTR = re.compile(r'\bspoken="([^"]*)"')


@dataclass(frozen=True)
class Job:
    """One audio segment to force-align against its spoken transcript."""

    page_key: str
    group_id: str
    section: str
    segment_id: str
    tap_md5: str
    spoken: str
    source: str
    wav_path: Path
    align_path: Path
    whisperx_path: Path
    item: str


class BookStore:
    """The working JSON, written after each successful alignment."""

    def __init__(self, path: Path, book: dict[str, Any]) -> None:
        self.path = path
        self.book = book
        self._lock = threading.Lock()

    def save_alignment(
        self, page_key: str, group_id: str, segment_id: str, alignment: dict[str, Any]
    ) -> None:
        """Attach one alignment to its audio row and rewrite the file."""
        with self._lock:
            for group in self.book["pages"][page_key]:
                if group["id"] != group_id:
                    continue
                for row in group.get("audio") or []:
                    if row.get("id") == segment_id:
                        row["alignment"] = alignment
                        break
                break
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(self.book, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)


@dataclass
class Run:
    """What every worker shares: models, log, output folder, the book on disk."""

    logger: ApiLogger
    book_id: str
    language: str
    device: str
    store: BookStore
    progress: Progress
    model_pool: queue.Queue


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


def relative(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def utc_timestamp() -> str:
    """An ISO-8601 UTC timestamp with millisecond precision."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Write `data` to `path` atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def spoken_sentence(text: str) -> str:
    """One sentence with every math tag replaced by its spoken form."""

    def replace(match: re.Match[str]) -> str:
        spoken = SPOKEN_ATTR.search(match.group(1) or "")
        if spoken is None or not spoken.group(1).strip():
            raise ValueError("a <math> tag has no spoken attribute")
        return spoken.group(1)

    return MATH_TAG.sub(replace, text)


def normalize_newlines(text: str) -> str:
    """Collapse runs of 2+ newlines into one.

    WhisperX treats any multi-newline run as a single pause and outputs one
    newline character for it. The same normalisation before alignment keeps
    text positions in exact 1-to-1 correspondence with character output.
    """
    return re.sub(r"\n{2,}", "\n", text)


def segment_texts(paragraphs: list[Any]) -> tuple[str, str]:
    """Spoken prose and tagged source for one paragraph list.

    Sentences are spaced, paragraphs blank-lined, then newlines collapsed.
    The spoken string is what WhisperX aligns; the source string still
    carries ``<math spoken="...">`` tags so packaged output can put LaTeX
    back on those character runs.

    Args:
        paragraphs: Transcript paragraphs, each a dict of sentence strings.

    Returns:
        ``(spoken, source)``, both newline-normalised.

    Raises:
        ValueError: A math tag has no spoken attribute.
    """
    spoken_blocks: list[str] = []
    source_blocks: list[str] = []
    for paragraph in paragraphs:
        if not isinstance(paragraph, dict):
            continue
        spoken_sentences: list[str] = []
        source_sentences: list[str] = []
        for sentence in paragraph.get("sentences") or []:
            if not isinstance(sentence, str) or not sentence.strip():
                continue
            spoken_sentences.append(spoken_sentence(sentence).strip())
            source_sentences.append(sentence.strip())
        if spoken_sentences:
            spoken_blocks.append(" ".join(spoken_sentences))
            source_blocks.append(" ".join(source_sentences))
    spoken = normalize_newlines("\n\n".join(spoken_blocks))
    source = normalize_newlines("\n\n".join(source_blocks))
    return spoken, source


def segment_paragraphs(group: dict[str, Any], segment_id: str) -> list[Any]:
    """The paragraph list for one audio segment of a group."""
    transcript = group.get("transcript") or {}
    if group.get("kind") == "exercise":
        return list(transcript.get("paragraphs") or [])
    if segment_id == "stem":
        return list((transcript.get("stem") or {}).get("paragraphs") or [])
    for part in transcript.get("parts") or []:
        if isinstance(part, dict) and part.get("id") == segment_id:
            return list(part.get("paragraphs") or [])
    return []


def latex_spans_in_spoken(transcript_text: str) -> list[tuple[int, int, str]]:
    """Character ranges in the spoken text that came from a math tag.

    Walks tagged source text and maps each ``<math spoken="...">LaTeX</math>``
    onto the corresponding slice of the spoken string.

    Args:
        transcript_text: Source transcript, already newline-normalised.

    Returns:
        ``(spoken_start, spoken_end_exclusive, dollar_wrapped_latex)`` triples.
    """
    spans: list[tuple[int, int, str]] = []
    spoken_pos = 0
    last_end = 0
    for match in MATH_TAG.finditer(transcript_text):
        spoken_match = SPOKEN_ATTR.search(match.group(1) or "")
        spoken_equiv = spoken_match.group(1) if spoken_match else ""
        latex_inner = match.group(2)
        spoken_pos += match.start() - last_end
        if spoken_equiv:
            spans.append(
                (spoken_pos, spoken_pos + len(spoken_equiv), f"${latex_inner}$")
            )
            spoken_pos += len(spoken_equiv)
        else:
            spoken_pos += len(match.group(0))
        last_end = match.end()
    return spans


def substitute_latex_in_chars(
    characters: list[dict[str, Any]], transcript_text: str
) -> list[dict[str, Any]]:
    """Replace spoken-math character runs with a single math timing entry.

    The replacement shares the timing window of the first and last spoken
    character in the run. The original list is not mutated.

    Args:
        characters: WhisperX character dicts, in spoken-text order.
        transcript_text: Tagged source used to locate the spoken runs.

    Returns:
        A new character list with math runs collapsed.
    """
    spans = latex_spans_in_spoken(transcript_text)
    if not spans:
        return characters

    result = list(characters)
    for spoken_start, spoken_end, latex_str in reversed(spans):
        if spoken_start >= len(result) or spoken_end > len(result):
            print(
                f"  Warning: LaTeX span [{spoken_start}:{spoken_end}] out of range "
                f"(alignment length {len(result)}) — skipping '{latex_str}'",
                flush=True,
            )
            continue
        first_start = result[spoken_start].get("start")
        last_end = result[spoken_end - 1].get("end")
        result[spoken_start:spoken_end] = [
            {"math": latex_str, "start": first_start, "end": last_end}
        ]
    return result


def choose_device() -> str:
    """CUDA if present, otherwise CPU. MPS is not used: WhisperX alignment is unreliable there."""
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def load_model_pool(language: str, device: str, workers: int) -> tuple[queue.Queue, str]:
    """One WhisperX alignment model per worker.

    Args:
        language: ISO 639-1 code WhisperX uses to pick the wav2vec2 checkpoint.
        device: ``cuda`` or ``cpu``. Falls back to CPU if the first load fails.
        workers: How many model instances to load.

    Returns:
        The pool and the device actually used.

    Raises:
        RuntimeError: No instance could be loaded.
    """
    pool: queue.Queue = queue.Queue()
    used = device
    print(
        f"loading {workers} WhisperX model instance(s) (language={language!r}, {used})",
        flush=True,
    )
    for index in range(workers):
        try:
            model, metadata = whisperx.load_align_model(
                language_code=language, device=used
            )
            pool.put((model, metadata))
            print(f"  model {index + 1}/{workers} loaded", flush=True)
        except Exception as error:
            if used != "cpu":
                print(f"  {used} failed ({error}), retrying on CPU", flush=True)
                used = "cpu"
                try:
                    model, metadata = whisperx.load_align_model(
                        language_code=language, device="cpu"
                    )
                    pool.put((model, metadata))
                    print(f"  model {index + 1}/{workers} loaded (CPU)", flush=True)
                except Exception as retry_error:
                    raise RuntimeError(
                        f"could not load WhisperX alignment model: {retry_error}"
                    ) from retry_error
            else:
                raise RuntimeError(
                    f"could not load WhisperX alignment model: {error}"
                ) from error
    return pool, used


def flatten_words(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Word-level timing from a WhisperX align result."""
    return [
        {
            "text": word.get("word", ""),
            "start": word.get("start"),
            "end": word.get("end"),
            "score": word.get("score"),
        }
        for word in result.get("word_segments", [])
    ]


def flatten_characters(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Character-level timing from a WhisperX align result."""
    characters: list[dict[str, Any]] = []
    for segment in result.get("segments", []):
        for char in segment.get("chars", []):
            characters.append(
                {
                    "text": char.get("char", ""),
                    "start": char.get("start"),
                    "end": char.get("end"),
                    "score": char.get("score"),
                }
            )
    return characters


def package_characters(characters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop WhisperX scores, keep text/math plus start/end."""
    packaged: list[dict[str, Any]] = []
    for char in characters:
        if "math" in char:
            packaged.append(
                {"math": char["math"], "start": char.get("start"), "end": char.get("end")}
            )
        else:
            packaged.append(
                {"text": char["text"], "start": char.get("start"), "end": char.get("end")}
            )
    return packaged


def logged_align(
    run: Run, job: Job, attempt: int
) -> tuple[dict[str, Any] | None, str | None]:
    """Align one segment once, logging the call however it turns out."""
    align_model, align_metadata = run.model_pool.get()
    spoken = job.spoken
    error_message = None
    payload: dict[str, Any] | None = None
    started = time.perf_counter()
    try:
        audio = whisperx.load_audio(str(job.wav_path))
        duration = len(audio) / WHISPERX_SAMPLE_RATE
        if duration < MIN_AUDIO_SECONDS:
            raise ValueError(f"audio too short ({duration:.3f}s) to align")

        result = whisperx.align(
            [{"text": spoken, "start": 0.0, "end": duration}],
            align_model,
            align_metadata,
            audio,
            run.device,
            return_char_alignments=True,
            print_progress=False,
        )
        words = flatten_words(result)
        characters = flatten_characters(result)
        write_json(
            job.whisperx_path,
            {"alignment_source": "whisperx", "words": words, "characters": characters},
        )
        substituted = (
            substitute_latex_in_chars(characters, job.source) if job.source else characters
        )
        write_json(
            job.align_path,
            {
                "alignment_source": "whisperx",
                "characters": package_characters(substituted),
            },
        )
        payload = {
            "file": f"{job.tap_md5}/01.json",
            "whisperx_file": f"{job.tap_md5}/{job.whisperx_path.name}",
            "api": "whisperx",
            "model": "wav2vec2",
            "device": run.device,
            "datetime": utc_timestamp(),
            "characters_count": len(characters),
            "words_count": len(words),
        }
    except Exception as error:  # noqa: BLE001 - retry load and align failures alike
        error_message = f"{type(error).__name__}: {error}"
        payload = None
    finally:
        run.model_pool.put((align_model, align_metadata))

    elapsed = time.perf_counter() - started
    safe_log(
        run.logger,
        files_builder=lambda: {"spoken.txt": spoken},
        page_number=job.item,
        step_name=ALIGN_STEP,
        attempt=attempt,
        api="whisperx",
        model="wav2vec2",
        usage={},
        duration_seconds=elapsed,
        status="success" if payload is not None else "error",
        error_message=error_message,
        extra={
            "book_id": run.book_id,
            "section": job.section,
            "group_id": job.group_id,
            "page": job.page_key,
            "segment_id": job.segment_id,
            "tap_md5": job.tap_md5,
            "device": run.device,
            "language": run.language,
            "audio_path": relative(job.wav_path),
            "audio_size_bytes": job.wav_path.stat().st_size if job.wav_path.is_file() else 0,
            "spoken_text_length": len(spoken),
            "words_count": payload["words_count"] if payload else None,
            "characters_count": payload["characters_count"] if payload else None,
        },
    )
    return payload, error_message


def request_alignment(
    run: Run,
    job: Job,
    report: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Align until a packaged JSON arrives, retrying every failure."""
    last_error: str | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        payload, last_error = logged_align(run, job, attempt)
        if payload is not None:
            return payload
        if attempt < MAX_ATTEMPTS:
            if report is not None:
                report(
                    f"attempt {attempt} of {MAX_ATTEMPTS} failed, "
                    f"{one_line(last_error or 'no reason given')}; "
                    f"trying again in {2 ** attempt}s"
                )
            time.sleep(2**attempt)
    raise RuntimeError(f"failed after {MAX_ATTEMPTS} attempts: {last_error}")


def align_segment(run: Run, job: Job) -> tuple[str, str]:
    """Align one segment, returning what became of it and the line to print."""
    started = time.perf_counter()
    run.progress.step(
        job.item,
        "aligning",
        f"{in_kb(len(job.spoken.encode('utf-8')))} spoken, aligning",
    )
    payload = request_alignment(
        run,
        job,
        report=lambda message: run.progress.say(f"  {job.item}: {message}"),
    )
    run.store.save_alignment(job.page_key, job.group_id, job.segment_id, payload)
    elapsed = since(time.perf_counter() - started)
    return (
        "written",
        f"  {job.item}: {payload['words_count']} words, "
        f"{payload['characters_count']} chars ({elapsed})",
    )


def collect_jobs(
    book: dict[str, Any],
    taps_dir: Path,
    section: str | None,
    update: bool,
) -> tuple[list[Job], int, int]:
    """Jobs still to run, how many alignments already exist, how many groups are silent.

    Raises:
        RuntimeError: Audio is listed on a group but the WAV is missing, or a
            listed segment has no spoken transcript.
    """
    jobs: list[Job] = []
    skipped = 0
    silent = 0
    missing_wavs: list[str] = []

    for page_key, groups in book["pages"].items():
        for group in groups:
            if section and group.get("section") != section:
                continue
            audio_rows = [
                row
                for row in (group.get("audio") or [])
                if isinstance(row, dict) and row.get("id") and row.get("tap_md5")
            ]
            if not group.get("transcript"):
                silent += 1
                continue
            if not audio_rows:
                silent += 1
                continue

            group_id = group["id"]
            for row in audio_rows:
                segment_id = row["id"]
                tap_md5 = row["tap_md5"]
                item = f"{page_key}_{group_id}" + (
                    "" if segment_id == group_id else f"_{segment_id}"
                )
                tap_folder = taps_dir / tap_md5
                wav_path = tap_folder / "01.wav"
                align_path = tap_folder / "01.json"
                if not update and align_path.is_file():
                    skipped += 1
                    continue
                if not wav_path.is_file():
                    missing_wavs.append(item)
                    continue
                spoken, source = segment_texts(segment_paragraphs(group, segment_id))
                if not spoken.strip():
                    raise RuntimeError(f"{item}: transcript has no spoken text")
                jobs.append(
                    Job(
                        page_key=page_key,
                        group_id=group_id,
                        section=group.get("section") or "",
                        segment_id=segment_id,
                        tap_md5=tap_md5,
                        spoken=spoken,
                        source=source,
                        wav_path=wav_path,
                        align_path=align_path,
                        whisperx_path=tap_folder / "01_whisperx.json",
                        item=item,
                    )
                )

    if missing_wavs:
        listed = ", ".join(missing_wavs[:8])
        extra = f" (+{len(missing_wavs) - 8} more)" if len(missing_wavs) > 8 else ""
        raise RuntimeError(
            f"audio listed but WAV missing for {len(missing_wavs)} segment(s): "
            f"{listed}{extra}"
        )
    return jobs, skipped, silent


def build_alignments(
    book_id: str, section: str | None, workers: int, update: bool
) -> tuple[Path, int]:
    """Align every pending audio segment of a book, or of just one section."""
    groups_path = working_json(book_id)
    if not groups_path.is_file():
        raise RuntimeError(
            f"working JSON not found: {groups_path.relative_to(PROJECT_ROOT)}\n"
            f"run groups.py first: python3 scripts/groups.py {book_id}"
        )
    print(f"reading groups of {book_id}", flush=True)
    book = load_json(groups_path)
    if section and not any(
        group.get("section") == section
        for groups in book.get("pages", {}).values()
        for group in groups
    ):
        raise RuntimeError(f"no section {section} in {book_id}")

    language = str(book.get("language_code") or "").strip()
    if not language:
        raise RuntimeError("working JSON is missing language_code")

    taps_dir = OUTPUT_DIR / book_id / TAPS_STAGE
    jobs, skipped, silent = collect_jobs(book, taps_dir, section, update)

    written = 0
    failures: list[str] = []
    if not jobs:
        extra = []
        if skipped:
            extra.append(f"{skipped} already aligned")
        if silent:
            extra.append(f"{silent} with no transcript or audio")
        suffix = f", {', '.join(extra)}" if extra else ""
        print(f"OK: nothing to align{suffix} under {taps_dir.relative_to(PROJECT_ROOT)}")
        return taps_dir, 0

    workers = min(workers, len(jobs))
    opening = f"aligning {len(jobs)} segment(s) with {workers} worker(s), language={language}"
    if skipped:
        opening += f"; {skipped} already aligned"
    if silent:
        opening += f"; {silent} with no transcript or audio"
    print(opening, flush=True)

    device = choose_device()
    model_pool, device = load_model_pool(language, device, workers)
    logger = ApiLogger(OUTPUT_DIR / book_id)

    with Progress(len(jobs), phases=("aligning",)) as progress:
        run = Run(
            logger=logger,
            book_id=book_id,
            language=language,
            device=device,
            store=BookStore(groups_path, book),
            progress=progress,
            model_pool=model_pool,
        )
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(align_segment, run, job): job.item for job in jobs}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    outcome, line = future.result()
                except Exception as error:  # noqa: BLE001 - the others still stand
                    failures.append(f"{item}: {type(error).__name__}: {error}")
                    progress.done(
                        item,
                        f"  {item}: FAILED {type(error).__name__}: {one_line(str(error))}",
                    )
                    continue
                if outcome == "written":
                    written += 1
                progress.done(item, line)

    summary = f"{written} segment(s) aligned"
    if skipped:
        summary += f", {skipped} already aligned"
    if silent:
        summary += f", {silent} with no transcript or audio"
    if failures:
        summary += f", {len(failures)} failed"
        print("\n".join(f"  FAILED {line}" for line in failures), flush=True)
    print(f"OK: {summary} under {taps_dir.relative_to(PROJECT_ROOT)}")
    return taps_dir, len(failures)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_id", help="Book ID, e.g. mathematics_ar_sa06_t2")
    parser.add_argument("-s", "--section", help="align only this section, e.g. -s 1.1")
    parser.add_argument(
        "--update", action="store_true", help="rewrite alignments that already exist"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=(
            f"concurrent workers / model instances (default: {DEFAULT_WORKERS}; "
            "each loads its own wav2vec2)"
        ),
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    try:
        _, failed = build_alignments(
            args.book_id, args.section, args.workers, args.update
        )
    except RuntimeError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
