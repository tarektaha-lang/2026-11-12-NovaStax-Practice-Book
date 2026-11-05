#!/usr/bin/env python3
"""Write spoken audio for every stored transcript.

By default pending segments go through the Gemini Batch API: every chunk of
a round is submitted at once (``--batch-parallel N``; 0 = no cap), under
150 requests per job. Finished jobs download in parallel
(``--download-workers N``, default 3). --no-batch is the previous thread
pool of unary generateContent calls (MAX_WORKERS=25). A standalone exercise is one WAV.
A set is a WAV for the shared stem and one WAV for each exercise. Math
tags are not sent: only the spoken attribute is read aloud.
transcripts.py must have written the working JSON, and the
``14_taps/<md5>/`` folder for each tap, first.

Segments already on disk are left unless --update is passed, and each success
is written back immediately so a stopped run can resume. Batch logs set
``batch: true`` so cost.py can use Batch rates.

In-flight Batch jobs are recorded in
``data/output/<book_id>/00_audios_batch_state.json`` (a ``jobs`` list; a
legacy single ``job`` is still read). A second run on the same book is
refused while ``00_audios_batch.lock`` is held. --no-batch still polls and
applies every saved job before going unary. --discard-batch-state deletes
only the local JSON; it does not cancel the remote job.

    python3 scripts/audios.py mathematics_ar_sa06_t2
    python3 scripts/audios.py mathematics_ar_sa06_t2 -s 1.1
    python3 scripts/audios.py mathematics_en_eg08_t1 -s 1.1 --update --no-batch
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
import re
import shutil
import sys
import threading
import time
import wave
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    from google import genai
    from google.genai import types

    from src.api_logger import ApiLogger, genai_usage, safe_log
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
    )
    from src.prompting import PROMPTS_DIR
    from specs import Progress, in_kb, one_line, since
    from sources import OUTPUT_DIR, PROJECT_ROOT, working_json
except ModuleNotFoundError as error:  # pragma: no cover
    sys.exit(f"missing dependency ({error.name}): pip install -r requirements.txt")

PROMPT_STAGE = "audios"
AUDIO_STEP = "audios"
BATCH_STAGE = "audios"
TAPS_STAGE = "14_taps"
AUDIOS_LEGACY_STAGE = "15_audios"

DEFAULT_MODEL = "gemini-3.8-flash-tts"
DEFAULT_BATCH = True
DEFAULT_BATCH_PARALLEL = 0
DEFAULT_DOWNLOAD_WORKERS = 3
VOICE_NAME = "Sulafat"
API_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
MAX_ATTEMPTS = 4
MAX_WORKERS = 25
WAV_RATE = 24000
WAV_SAMPLE_WIDTH = 2

MATH_TAG = re.compile(r"<math(\s[^>]*)?>(.*?)</math>", re.DOTALL | re.IGNORECASE)
SPOKEN_ATTR = re.compile(r'\bspoken="([^"]*)"')
VARIETY_SLOT = "{spoken_variety}"


@dataclass(frozen=True)
class Job:
    """One transcript segment to voice."""

    page_key: str
    group_id: str
    section: str
    segment_id: str
    tap_md5: str
    spoken: str
    wav_path: Path
    txt_path: Path
    item: str


class BookStore:
    """The working JSON, written after each successful segment."""

    def __init__(self, path: Path, book: dict[str, Any]) -> None:
        self.path = path
        self.book = book
        self._lock = threading.Lock()

    def save_segment(self, page_key: str, group_id: str, segment: dict[str, Any]) -> None:
        """Attach one audio segment to its group and rewrite the file."""
        with self._lock:
            for group in self.book["pages"][page_key]:
                if group["id"] == group_id:
                    audio = [
                        row
                        for row in (group.get("audio") or [])
                        if row.get("id") != segment["id"]
                    ]
                    audio.append(segment)
                    group["audio"] = ordered_audio(group, audio)
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


@dataclass(frozen=True)
class Run:
    """What every worker shares: client, prompt, output folder, the book on disk."""

    client: genai.Client
    logger: ApiLogger
    book_id: str
    model: str
    prompt: str
    prompt_files: list[str]
    taps_dir: Path
    store: BookStore
    progress: Progress
    update: bool = False
    section: str | None = None


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


def audio_prompt(book: dict[str, Any]) -> tuple[str, list[str]]:
    """The delivery style for this book, and the files it was filled from.

    Gemini 3.8 reads the transcript verbatim, so this string is attached as
    speech metadata and is not spoken. Spoken variety is a fact about the
    country, not about the book's numerals or notation, so the country file
    is `{country}.md`.
    """
    country = str(book.get("country_code", "")).strip()
    if not country:
        raise RuntimeError("working JSON is missing country_code")
    core = PROMPTS_DIR / PROMPT_STAGE / "core.md"
    country_path = PROMPTS_DIR / PROMPT_STAGE / "country" / f"{country}.md"
    if not core.is_file():
        raise RuntimeError(f"no audio prompt at {relative(core)}")
    if not country_path.is_file():
        raise RuntimeError(f"no audio country file {relative(country_path)}")
    text = core.read_text(encoding="utf-8")
    if VARIETY_SLOT not in text:
        raise RuntimeError(f"{relative(core)} has no {VARIETY_SLOT} placeholder")
    variety = country_path.read_text(encoding="utf-8").strip()
    if not variety:
        raise RuntimeError(f"{relative(country_path)} is empty")
    filled = text.replace(VARIETY_SLOT, variety).strip()
    if VARIETY_SLOT in filled:
        raise RuntimeError(f"{relative(core)} still carries {VARIETY_SLOT}")
    if not filled:
        raise RuntimeError(f"{relative(core)} is empty once {VARIETY_SLOT} is filled")
    return filled, [relative(core), relative(country_path)]


def spoken_sentence(text: str) -> str:
    """One sentence with every math tag replaced by its spoken form."""

    def replace(match: re.Match[str]) -> str:
        spoken = SPOKEN_ATTR.search(match.group(1))
        if spoken is None or not spoken.group(1).strip():
            raise ValueError("a <math> tag has no spoken attribute")
        return spoken.group(1)

    return MATH_TAG.sub(replace, text)


def paragraphs_text(paragraphs: list[Any]) -> str:
    """Spoken prose for one paragraph list: sentences spaced, paragraphs blank-lined."""
    blocks: list[str] = []
    for paragraph in paragraphs:
        if not isinstance(paragraph, dict):
            continue
        sentences = [
            spoken_sentence(sentence).strip()
            for sentence in (paragraph.get("sentences") or [])
            if isinstance(sentence, str) and sentence.strip()
        ]
        if sentences:
            blocks.append(" ".join(sentences))
    return "\n\n".join(blocks)


def segments_of(page_key: str, group: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    """(segment id, spoken text, wav filename, log item) for one group."""
    transcript = group["transcript"]
    group_id = group["id"]
    if group.get("kind") == "exercise":
        spoken = paragraphs_text(transcript.get("paragraphs") or [])
        if not spoken.strip():
            raise RuntimeError(f"{page_key} {group_id}: transcript has no spoken text")
        name = f"{page_key}_{group_id}.wav"
        return [(group_id, spoken, name, f"{page_key}_{group_id}")]

    rows: list[tuple[str, str, str, str]] = []
    stem = paragraphs_text((transcript.get("stem") or {}).get("paragraphs") or [])
    if not stem.strip():
        raise RuntimeError(f"{page_key} {group_id}: stem has no spoken text")
    rows.append(
        ("stem", stem, f"{page_key}_{group_id}_stem.wav", f"{page_key}_{group_id}_stem")
    )
    parts = transcript.get("parts") or []
    ids = [exercise["id"] for exercise in group.get("exercises") or []]
    named = [part.get("id") for part in parts]
    if named != ids:
        raise RuntimeError(
            f"{page_key} {group_id}: parts are {named}, not the exercises {ids} in order"
        )
    for part in parts:
        spoken = paragraphs_text(part.get("paragraphs") or [])
        part_id = part["id"]
        if not spoken.strip():
            raise RuntimeError(f"{page_key} {group_id} {part_id}: no spoken text")
        rows.append(
            (
                part_id,
                spoken,
                f"{page_key}_{group_id}_{part_id}.wav",
                f"{page_key}_{group_id}_{part_id}",
            )
        )
    return rows


def ordered_audio(group: dict[str, Any], audio: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Audio rows in stem-then-exercise order, so the file always reads the same way."""
    by_id = {row["id"]: row for row in audio}
    order = (
        [group["id"]]
        if group.get("kind") == "exercise"
        else ["stem", *[exercise["id"] for exercise in group.get("exercises") or []]]
    )
    return [by_id[key] for key in order if key in by_id]


def pcm_params(mime_type: str | None) -> tuple[int, int]:
    """Bits per sample and frame rate from a Gemini audio MIME type."""
    bits = 16
    rate = WAV_RATE
    if not mime_type:
        return bits, rate
    for param in mime_type.split(";"):
        piece = param.strip()
        lower = piece.lower()
        if lower.startswith("rate="):
            try:
                rate = int(piece.split("=", 1)[1])
            except ValueError:
                pass
        elif "audio/l" in lower:
            try:
                bits = int(re.search(r"l(\d+)", lower, re.IGNORECASE).group(1))
            except (AttributeError, ValueError):
                pass
    return bits, rate


def wav_bytes(pcm: bytes, mime_type: str | None) -> bytes:
    """A WAV file wrapping Gemini's raw PCM."""
    bits, rate = pcm_params(mime_type)
    sample_width = max(1, bits // 8)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(sample_width)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


def wav_duration(pcm: bytes, mime_type: str | None) -> float:
    """Length of the PCM in seconds."""
    bits, rate = pcm_params(mime_type)
    width = max(1, bits // 8)
    if rate <= 0 or width <= 0:
        return 0.0
    return round(len(pcm) / (rate * width), 2)


def write_wav(path: Path, data: bytes) -> None:
    """Write `data` to `path` atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".wav.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def is_wav_file(data: bytes, mime_type: str | None) -> bool:
    """Whether `data` is a RIFF WAV rather than headerless PCM.

    A unary Gemini 3.8 reply is audio/wav. Earlier speech models returned
    audio/l16, which this script still wraps.
    """
    mime = (mime_type or "").lower()
    if "wav" in mime:
        return True
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def container_duration(data: bytes) -> float:
    """Length of a WAV file in seconds, read from its header."""
    with wave.open(io.BytesIO(data), "rb") as handle:
        rate = handle.getframerate()
        if rate <= 0:
            return 0.0
        return round(handle.getnframes() / rate, 2)


def prepared_wav(audio: bytes, mime_type: str | None) -> tuple[bytes, float]:
    """WAV bytes and their duration.

    A container is stored as the model sent it. Raw PCM is wrapped first,
    because writing a WAV header around a WAV file makes a file no player
    can open and a duration that counts the header as samples.
    """
    if is_wav_file(audio, mime_type):
        return audio, container_duration(audio)
    return wav_bytes(audio, mime_type), wav_duration(audio, mime_type)


def audio_from_response(response: Any) -> tuple[bytes, str | None]:
    """The audio bytes and MIME type of a TTS reply, or a ValueError."""
    candidates = getattr(response, "candidates", None) or []
    candidate = candidates[0] if candidates else None
    content = getattr(candidate, "content", None) if candidate is not None else None
    parts = getattr(content, "parts", None) if content is not None else None
    finish = getattr(candidate, "finish_reason", None) if candidate is not None else None
    if not parts:
        reason = f"finish_reason={finish}" if finish else "no content"
        raise ValueError(f"empty audio response ({reason})")
    inline = getattr(parts[0], "inline_data", None)
    pcm = getattr(inline, "data", None) if inline is not None else None
    if not pcm:
        reason = f"finish_reason={finish}" if finish else "no inline audio"
        raise ValueError(f"empty audio response ({reason})")
    mime = getattr(inline, "mime_type", None)
    return pcm, mime


def tts_log_files(
    prompt: str,
    spoken: str,
    wav: bytes | None,
    response: Any,
) -> dict[str, Any]:
    """What a TTS call stores in its log folder, with audio bytes kept once."""
    files: dict[str, Any] = {"prompt.txt": prompt, "spoken.txt": spoken}
    if wav is not None:
        files["audio.wav"] = wav
    if response is None:
        return files
    if hasattr(response, "model_dump"):
        dump = response.model_dump(mode="json", exclude_none=True)
    elif isinstance(response, dict):
        dump = response
    else:
        return files
    for candidate in dump.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            inline = part.get("inline_data")
            if inline and inline.get("data"):
                inline["data"] = "<audio bytes stripped; saved as audio.wav>"
    files["response.json"] = dump
    return files


def is_gemini_25_tts(model: str) -> bool:
    """True for gemini-2.5-*-tts, which use the older GenerateContent TTS shape."""
    name = model.lower()
    return "2.5" in name and "tts" in name


def speech_contents(spoken: str, style: str, model: str) -> list[types.Content]:
    """The transcript plus delivery style, shaped for the TTS model in use.

    Gemini 3.8 reads text verbatim and takes style in speech_metadata. Gemini 2.5
    has no speech_metadata, so the style is a text prefix and is not spoken as a
    separate turn.
    """
    if is_gemini_25_tts(model):
        text = f"{style.strip()}\n\n{spoken}" if style.strip() else spoken
        return [types.Content(role="user", parts=[types.Part(text=text)])]
    return [
        types.Content(
            role="user",
            parts=[
                types.Part(
                    text=spoken,
                    speech_metadata=types.SpeechMetadata(style=style),
                )
            ],
        )
    ]


def speech_config(model: str) -> types.GenerateContentConfig:
    """Audio out, Sulafat, no function calling."""
    if is_gemini_25_tts(model):
        voice = types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE_NAME),
        )
    else:
        voice = types.VoiceConfig(voice=VOICE_NAME)
    return types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        speech_config=types.SpeechConfig(voice_config=voice),
    )


def inlined_tts_request(run: Run, job: Job) -> types.InlinedRequest:
    """One segment as an inline Batch generateContent request."""
    return types.InlinedRequest(
        contents=speech_contents(job.spoken, run.prompt, run.model),
        metadata={
            "item": job.item,
            "tap_md5": job.tap_md5,
            "segment_id": job.segment_id,
        },
        config=speech_config(run.model),
    )


def log_tts_call(
    run: Run,
    job: Job,
    *,
    attempt: int,
    response: Any,
    wav: bytes | None,
    segment: dict[str, Any] | None,
    error_message: str | None,
    elapsed: float,
    batch: bool,
    batch_name: str | None = None,
) -> None:
    extra: dict[str, Any] = {
        "book_id": run.book_id,
        "section": job.section,
        "group_id": job.group_id,
        "page": job.page_key,
        "segment_id": job.segment_id,
        "tap_md5": job.tap_md5,
        "voice_name": VOICE_NAME,
        "audio_file": f"{job.tap_md5}/01.wav",
        "audio_duration_seconds": segment["duration_seconds"] if segment else None,
        "prompt_files": list(run.prompt_files),
    }
    if batch:
        extra["batch"] = True
        if batch_name:
            extra["batch_name"] = batch_name
    usage = genai_usage(response) if response is not None else {}
    safe_log(
        run.logger,
        files_builder=lambda captured=response, payload=wav: tts_log_files(
            run.prompt, job.spoken, payload, captured
        ),
        page_number=job.item,
        step_name=AUDIO_STEP,
        attempt=attempt,
        api="gemini",
        model=run.model,
        usage=usage,
        duration_seconds=elapsed,
        status="success" if segment is not None else "error",
        error_message=error_message,
        extra=extra,
    )


def segment_from_audio(
    run: Run,
    job: Job,
    audio: bytes,
    mime: str | None,
    attempt: int,
    elapsed: float,
    response: Any,
    *,
    batch: bool,
    batch_name: str | None = None,
) -> tuple[dict[str, Any], bytes]:
    wav, duration = prepared_wav(audio, mime)
    if duration <= 0:
        raise ValueError("audio duration is zero")
    write_wav(job.wav_path, wav)
    generation: dict[str, Any] = {
        "model": run.model,
        "voice": VOICE_NAME,
        "attempt": attempt,
        "duration_seconds": round(elapsed, 3),
        "usage": genai_usage(response),
        "prompt_files": list(run.prompt_files),
    }
    if batch:
        generation["batch"] = True
        if batch_name:
            generation["batch_name"] = batch_name
    return {
        "id": job.segment_id,
        "tap_md5": job.tap_md5,
        "file": f"{job.tap_md5}/01.wav",
        "duration_seconds": duration,
        "generation": generation,
    }, wav


def logged_call(
    run: Run,
    job: Job,
    attempt: int,
) -> tuple[dict[str, Any] | None, str | None]:
    """Ask for one segment's audio once, logging the call however it turns out."""
    response = None
    segment = None
    wav = None
    error_message = None
    started = time.perf_counter()
    try:
        response = run.client.models.generate_content(
            model=run.model,
            contents=speech_contents(job.spoken, run.prompt, run.model),
            config=speech_config(run.model),
        )
        audio, mime = audio_from_response(response)
        segment, wav = segment_from_audio(
            run,
            job,
            audio,
            mime,
            attempt,
            time.perf_counter() - started,
            response,
            batch=False,
        )
    except Exception as error:  # noqa: BLE001 - retry transport and empty replies alike
        error_message = f"{type(error).__name__}: {error}"
        segment = None
        wav = None
    elapsed = time.perf_counter() - started
    log_tts_call(
        run,
        job,
        attempt=attempt,
        response=response,
        wav=wav,
        segment=segment,
        error_message=error_message,
        elapsed=elapsed,
        batch=False,
    )
    return segment, error_message


def apply_batch_row(
    run: Run,
    job: Job,
    row: Any,
    attempt: int,
    elapsed: float,
    batch_name: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Turn one Batch inlined response into a WAV + log."""
    response, error_message = response_and_error(row)
    parsed = None
    segment = None
    wav = None
    if error_message is None:
        try:
            if response is None:
                raise ValueError("no batch response for this request")
            parsed = generate_content_response(response)
            audio, mime = audio_from_response(parsed)
            segment, wav = segment_from_audio(
                run,
                job,
                audio,
                mime,
                attempt,
                elapsed,
                parsed,
                batch=True,
                batch_name=batch_name,
            )
        except Exception as error:  # noqa: BLE001 - same retry story as unary
            error_message = f"{type(error).__name__}: {error}"
            segment = None
            wav = None
    log_tts_call(
        run,
        job,
        attempt=attempt,
        response=parsed,
        wav=wav,
        segment=segment,
        error_message=error_message,
        elapsed=elapsed,
        batch=True,
        batch_name=batch_name,
    )
    return segment, error_message


def audio_input_digest(job: Job, style: str) -> str:
    """sha256 of spoken text, style and tap_md5 — all strings, byte-stable."""
    payload = {
        "spoken": str(job.spoken),
        "style": str(style),
        "tap_md5": str(job.tap_md5),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def audio_json_row(
    book: dict[str, Any], page_key: str, group_id: str, segment_id: str
) -> dict[str, Any] | None:
    for group in book.get("pages", {}).get(page_key) or []:
        if group.get("id") != group_id:
            continue
        for entry in group.get("audio") or []:
            if isinstance(entry, dict) and entry.get("id") == segment_id:
                return entry
        return None
    return None


def audio_is_applied(job: Job, book: dict[str, Any], update: bool, job_name: str | None) -> bool:
    if not job.wav_path.is_file():
        return False
    row = audio_json_row(book, job.page_key, job.group_id, job.segment_id)
    if row is None:
        return False
    if not update:
        return True
    generation = row.get("generation") or {}
    return bool(job_name) and generation.get("batch_name") == job_name


def apply_audio_rows(
    run: Run,
    jobs: list[Job],
    rows: list[Any],
    keys: dict[str, str],
    attempt: int,
    batch_name: str,
    elapsed: float,
) -> list[tuple[Job, dict[str, Any] | None, str | None]]:
    by_item = map_rows_by_key(rows)
    results: list[tuple[Job, dict[str, Any] | None, str | None]] = []
    for job in jobs:
        saved_hash = keys.get(job.item)
        current = audio_input_digest(job, run.prompt)
        if saved_hash and saved_hash != current:
            print(
                f"  {job.item}: spoken text changed since submit; will resubmit",
                flush=True,
            )
            results.append((job, None, "stale batch row"))
            continue
        row = by_item.get(job.item)
        if row is None:
            log_tts_call(
                run,
                job,
                attempt=attempt,
                response=None,
                wav=None,
                segment=None,
                error_message="no batch response for this request",
                elapsed=elapsed,
                batch=True,
                batch_name=batch_name,
            )
            results.append((job, None, "no batch response for this request"))
            continue
        segment, error_message = apply_batch_row(
            run, job, row, attempt, elapsed, batch_name
        )
        results.append((job, segment, error_message))
    return results


def state_envelope(run: Run, update: bool) -> dict[str, Any]:
    return {
        "stage": BATCH_STAGE,
        "book_id": run.book_id,
        "model": run.model,
        "section": run.section,
        "update": update,
    }


def build_audio_chunk(
    run: Run, jobs: list[Job], attempt: int, label: str
) -> BatchChunk:
    display = batch_display_name(run.book_id, label, run_id=new_run_id())
    keys = {job.item: audio_input_digest(job, run.prompt) for job in jobs}
    captured = list(jobs)

    def build_src() -> tuple[Any, str | None]:
        return [inlined_tts_request(run, job) for job in captured], None

    return BatchChunk(
        label=label,
        display_name=display,
        attempt=attempt,
        keys=keys,
        items=list(captured),
        build_src=build_src,
    )


def adopted_audio_chunk(
    entry: dict[str, Any], by_item: dict[str, Job]
) -> BatchChunk:
    keys = dict(entry.get("keys") or {})
    items = [by_item[key] for key in keys if key in by_item]
    return BatchChunk(
        label=str(entry.get("display_name") or "resume"),
        display_name=str(entry.get("display_name") or ""),
        attempt=int(entry.get("attempt") or 1),
        keys=keys,
        items=items,
        build_src=None,
    )


def _audio_chunks_for(
    run: Run, leftover: list[Job], attempt: int
) -> list[BatchChunk]:
    if not leftover:
        return []
    by_item = {job.item: job for job in leftover}
    pairs = [(job.item, inlined_tts_request(run, job)) for job in leftover]
    queue: list[BatchChunk] = []
    for index, chunk in enumerate(chunk_requests(pairs), start=1):
        group = [by_item[item] for item, _request in chunk]
        queue.append(
            build_audio_chunk(run, group, attempt, f"a{attempt}-b{index}")
        )
    return queue


def voice_segments_batch(
    run: Run,
    jobs: list[Job],
    state: BatchState,
    update: bool,
    *,
    adopted: list[dict[str, Any]] | None = None,
    parallel: int = 0,
    submit_leftovers: bool = True,
    prefetched: dict[str, BatchStatus] | None = None,
    download_workers: int = DEFAULT_DOWNLOAD_WORKERS,
) -> tuple[int, list[str], set[str]]:
    """Voice pending segments through Batch, retrying failed ones up to MAX_ATTEMPTS.

    Attempt 1 is the single owner of overlap: adopted jobs plus leftover chunks.
    """
    written = 0
    failures: list[str] = []
    abandoned_keys: set[str] = set()
    pending = list(jobs)
    adopted_entries = list(adopted or [])
    envelope = state_envelope(run, update)
    finalized: set[str] = set()

    def account(
        job: Job,
        ok: bool,
        error_message: str | None,
        round_attempt: int,
        still: list[Job],
        job_name: str | None,
        line: str | None = None,
    ) -> None:
        nonlocal written
        if job in still or job.item in finalized:
            return
        if ok or audio_is_applied(job, run.store.book, run.update, job_name):
            written += 1
            finalized.add(job.item)
            run.progress.done(job.item, line or f"  {job.item}: audio")
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
        still: list[Job] = []
        by_item = {job.item: job for job in pending}
        adopted_keys: set[str] = set()
        adopted_pairs: list[tuple[dict[str, Any], BatchChunk]] = []
        if attempt == 1:
            for entry in adopted_entries:
                keys = entry.get("keys") or {}
                adopted_keys.update(str(key) for key in keys)
                chunk = adopted_audio_chunk(entry, by_item)
                for key in keys:
                    key = str(key)
                    if key in by_item:
                        continue
                    if audio_item_is_applied(
                        run.store.book,
                        run.taps_dir,
                        key,
                        run.update,
                        entry.get("name"),
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
        queue = _audio_chunks_for(run, leftover_jobs, attempt)

        def on_submit(chunk: BatchChunk) -> None:
            for job in chunk.items:
                run.progress.asking(
                    job.item,
                    f"{in_kb(len(job.spoken.encode('utf-8')))} spoken, "
                    f"in batch {chunk.label}",
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
            to_apply: list[Job] = []
            for job in chunk.items:
                if audio_is_applied(job, book, run.update, name):
                    outcomes.append((job, True, None, "already on disk"))
                    continue
                to_apply.append(job)
            if not to_apply:
                return FINISH_DONE, outcomes, result_file
            try:
                rows = inlined_responses(run.client, remote)
                results = apply_audio_rows(
                    run, to_apply, rows, chunk.keys, log_attempt, name, elapsed
                )
                for job, segment, error_message in results:
                    if segment is not None:
                        run.store.save_segment(job.page_key, job.group_id, segment)
                        outcomes.append(
                            (
                                job,
                                True,
                                None,
                                f"  {job.item}: {segment['duration_seconds']:.1f}s audio",
                            )
                        )
                    else:
                        outcomes.append((job, False, error_message, None))
            except ApplyError:
                raise
            except Exception as error:
                book = run.store.snapshot() if run.update else run.store.book
                accounted = {job.item for job, *_rest in outcomes}
                for job in to_apply:
                    if job.item in accounted:
                        continue
                    if audio_is_applied(job, book, run.update, name):
                        outcomes.append((job, True, None, None))
                raise ApplyError(
                    f"{type(error).__name__}: {error}", outcomes
                ) from error
            return FINISH_DONE, outcomes, result_file

        def account_outcomes(chunk: BatchChunk, outcomes: list[Any]) -> None:
            for job, ok, error_message, note in outcomes:
                if note == "already on disk":
                    print(f"  {job.item}: already on disk", flush=True)
                    account(job, True, None, attempt, still, None)
                    continue
                line = note if ok and note else None
                account(job, ok, error_message, attempt, still, None, line)

        def on_failed(chunk: BatchChunk, message: str) -> None:
            if attempt < MAX_ATTEMPTS:
                print(
                    f"  {chunk.label} failed: {one_line(message)}; will retry",
                    flush=True,
                )
            for job in chunk.items:
                if audio_is_applied(job, run.store.book, run.update, None):
                    account(job, True, None, attempt, still, None)
                    continue
                account(job, False, message, attempt, still, None)

        def on_abandoned(chunk: BatchChunk, message: str) -> None:
            for job in chunk.items:
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
            for job in chunk.items:
                if audio_is_applied(job, run.store.book, run.update, None):
                    account(job, True, None, attempt, still, None)
                    continue
                leftover.append(job)
            if not submit_leftovers:
                return []
            return _audio_chunks_for(run, leftover, attempt)

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


def audio_item_is_applied(
    book: dict[str, Any],
    taps_dir: Path,
    key: str,
    update: bool,
    job_name: str | None,
) -> bool:
    for page_key, groups in book.get("pages", {}).items():
        for group in groups:
            if not group.get("transcript"):
                # A group split across pages is transcribed once; its copy has none.
                continue
            tap_by_id = {
                row["id"]: row["md5"]
                for row in (group.get("taps") or [])
                if isinstance(row, dict) and row.get("id") and row.get("md5")
            }
            for segment_id, _spoken, _wav_name, item in segments_of(page_key, group):
                if item != key:
                    continue
                digest = tap_by_id.get(segment_id)
                wav_path = taps_dir / digest / "01.wav" if digest else None
                if wav_path is None or not wav_path.is_file():
                    return False
                row = None
                for audio in group.get("audio") or []:
                    if isinstance(audio, dict) and audio.get("id") == segment_id:
                        row = audio
                        break
                if row is None:
                    return False
                if not update:
                    return True
                generation = row.get("generation") or {}
                return bool(job_name) and generation.get("batch_name") == job_name
    return False


def saved_audio_keys_pending(
    book: dict[str, Any], taps_dir: Path, data: dict[str, Any]
) -> list[str]:
    update = bool(data.get("update"))
    pending: list[str] = []
    for entry in entries(data):
        name = entry.get("name")
        for key in entry.get("keys") or {}:
            if not audio_item_is_applied(book, taps_dir, str(key), update, name):
                pending.append(str(key))
    return pending


def audio_state_mismatch(
    data: dict[str, Any], book_id: str, model: str, section: str | None
) -> str | None:
    if data.get("stage") and data.get("stage") != BATCH_STAGE:
        return f"saved batch state is for stage {data.get('stage')}, not {BATCH_STAGE}"
    if data.get("book_id") and data.get("book_id") != book_id:
        return f"saved batch state is for {data.get('book_id')}, not {book_id}"
    if data.get("model") and data.get("model") != model:
        return f"saved batch state used model {data.get('model')}, not {model}"
    if data.get("section") != section:
        saved = data.get("section") or "the whole book"
        now = section or "the whole book"
        return f"saved batch state is for {saved}, not {now}"
    return None


def resume_saved_audio_jobs(
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


def request_audio(
    run: Run,
    job: Job,
    report: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Call until a WAV arrives, retrying every failure."""
    last_error: str | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        segment, last_error = logged_call(run, job, attempt)
        if segment is not None:
            return segment
        if attempt < MAX_ATTEMPTS:
            if report is not None:
                report(
                    f"attempt {attempt} of {MAX_ATTEMPTS} failed, "
                    f"{one_line(last_error or 'no reason given')}; "
                    f"asking again in {2**attempt}s"
                )
            time.sleep(2**attempt)
    raise RuntimeError(f"failed after {MAX_ATTEMPTS} attempts: {last_error}")


def voice_segment(run: Run, job: Job) -> tuple[str, str]:
    """Voice one segment, returning what became of it and the line to print."""
    started = time.perf_counter()
    run.progress.asking(
        job.item,
        f"{in_kb(len(job.spoken.encode('utf-8')))} spoken, asking the model",
    )
    segment = request_audio(
        run,
        job,
        report=lambda message: run.progress.say(f"  {job.item}: {message}"),
    )
    run.store.save_segment(job.page_key, job.group_id, segment)
    elapsed = since(time.perf_counter() - started)
    return "written", f"  {job.item}: {segment['duration_seconds']:.1f}s audio ({elapsed})"


@dataclass(frozen=True)
class AudioTarget:
    """One segment collect_jobs would consider, with paths and no writes."""

    page_key: str
    group_id: str
    section: str
    segment_id: str
    tap_md5: str
    spoken: str
    wav_name: str
    item: str
    wav_path: Path
    txt_path: Path
    stored_file: str | None


def iter_audio_targets(
    book: dict[str, Any],
    taps_dir: Path,
    section: str | None,
) -> list[AudioTarget]:
    """Segments collect_jobs would queue, read-only: no tap writes and no legacy copy."""
    found: list[AudioTarget] = []
    for page_key, groups in (book.get("pages") or {}).items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            if section and group.get("section") != section:
                continue
            if not group.get("transcript"):
                continue
            tap_md5_by_id = {
                t["id"]: t["md5"]
                for t in (group.get("taps") or [])
                if isinstance(t, dict) and "id" in t and "md5" in t
            }
            stored_audio_files = {
                entry.get("id"): entry.get("file")
                for entry in (group.get("audio") or [])
                if isinstance(entry, dict) and entry.get("id") and entry.get("file")
            }
            for segment_id, spoken, wav_name, item in segments_of(page_key, group):
                tap_md5 = tap_md5_by_id.get(segment_id)
                if not tap_md5:
                    continue
                tap_folder = taps_dir / tap_md5
                found.append(
                    AudioTarget(
                        page_key=page_key,
                        group_id=group["id"],
                        section=group.get("section") or "",
                        segment_id=segment_id,
                        tap_md5=tap_md5,
                        spoken=spoken,
                        wav_name=wav_name,
                        item=item,
                        wav_path=tap_folder / "01.wav",
                        txt_path=tap_folder / "01.txt",
                        stored_file=stored_audio_files.get(segment_id),
                    )
                )
    return found


def audio_wav_is_ready(path: Path) -> bool:
    """True when ``01.wav`` is on disk. A ``*.wav.tmp`` beside it does not count."""
    return path.name == "01.wav" and path.is_file()


def audio_wav_counts(
    book: dict[str, Any], taps_dir: Path, section: str | None
) -> tuple[int, int]:
    """How many collectable segments have ``01.wav``, and how many there are."""
    paths = [row.wav_path for row in iter_audio_targets(book, taps_dir, section)]
    have = sum(1 for path in paths if audio_wav_is_ready(path))
    return have, len(paths)


def collect_jobs(
    book_id: str,
    book: dict[str, Any],
    taps_dir: Path,
    section: str | None,
    update: bool,
) -> tuple[list[Job], int, int]:
    """Jobs still to run, how many WAVs already exist, how many groups have no transcript."""
    from transcripts import write_tap_transcripts

    jobs: list[Job] = []
    skipped = 0
    silent = 0
    legacy_audios_dir = OUTPUT_DIR / book_id / AUDIOS_LEGACY_STAGE
    for page_key, groups in book["pages"].items():
        for group in groups:
            if section and group.get("section") != section:
                continue
            if not group.get("transcript"):
                silent += 1
                continue
            write_tap_transcripts(taps_dir, group)
    for row in iter_audio_targets(book, taps_dir, section):
        wav_path = row.wav_path
        stored_file = row.stored_file
        legacy_candidates = [
            legacy_audios_dir / row.wav_name,
            OUTPUT_DIR / book_id / stored_file if stored_file else None,
        ]
        for cand in legacy_candidates:
            if cand and cand.is_file() and not wav_path.is_file():
                wav_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cand, wav_path)
                break

        if (
            not update
            and audio_wav_is_ready(wav_path)
            and audio_json_row(book, row.page_key, row.group_id, row.segment_id)
            is not None
        ):
            skipped += 1
            continue

        jobs.append(
            Job(
                page_key=row.page_key,
                group_id=row.group_id,
                section=row.section,
                segment_id=row.segment_id,
                tap_md5=row.tap_md5,
                spoken=row.spoken,
                wav_path=row.wav_path,
                txt_path=row.txt_path,
                item=row.item,
            )
        )
    return jobs, skipped, silent


def voice_segments_unary(run: Run, jobs: list[Job]) -> tuple[int, list[str]]:
    written = 0
    failures: list[str] = []
    workers = min(len(jobs), MAX_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(voice_segment, run, job): job.item for job in jobs}
        for future in as_completed(futures):
            item = futures[future]
            try:
                outcome, line = future.result()
            except Exception as error:  # noqa: BLE001 - the others still stand
                failures.append(f"{item}: {type(error).__name__}: {error}")
                run.progress.done(
                    item,
                    f"  {item}: FAILED {type(error).__name__}: "
                    f"{one_line(str(error))}",
                )
                continue
            if outcome == "written":
                written += 1
            run.progress.done(item, line)
    return written, failures


def build_audios(
    book_id: str,
    section: str | None,
    model: str,
    update: bool,
    *,
    batch: bool = DEFAULT_BATCH,
    discard_batch_state: bool = False,
    batch_parallel: int = DEFAULT_BATCH_PARALLEL,
    download_workers: int = DEFAULT_DOWNLOAD_WORKERS,
) -> tuple[Path, int]:
    """Voice every pending transcript segment of a book, or of just one section."""
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
    book_dir = OUTPUT_DIR / book_id
    taps_dir = book_dir / TAPS_STAGE
    jobs, skipped, silent = collect_jobs(book_id, book, taps_dir, section, update)
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
        pending_saved = saved_audio_keys_pending(book, taps_dir, data)
        if not pending_saved:
            print("  dropping fully-applied batch state", flush=True)
            state.forget()
            data = None
        else:
            reason = audio_state_mismatch(data, book_id, model, section)
            if reason:
                raise RuntimeError(
                    f"{reason}. Pass --discard-batch-state to drop the local "
                    "JSON only (the remote job is not cancelled)."
                )

    if not jobs:
        extra = []
        if skipped:
            extra.append(f"{skipped} already written")
        if silent:
            extra.append(f"{silent} with no transcript")
        suffix = f", {', '.join(extra)}" if extra else ""
        print(
            f"OK: nothing to voice{suffix} "
            f"under {taps_dir.relative_to(PROJECT_ROOT)}"
        )
        return taps_dir, 0

    if not any(os.environ.get(name) for name in API_KEY_VARS):
        raise RuntimeError(f"set one of {' / '.join(API_KEY_VARS)} to call the model")

    lock: BatchLock | None = None
    if batch or state.path.is_file():
        lock = BatchLock(batch_lock_path(book_dir, BATCH_STAGE))
        lock.acquire()
    try:
        return _build_audios_locked(
            book_id,
            section,
            model,
            update,
            batch=batch,
            batch_parallel=batch_parallel,
            download_workers=download_workers,
            groups_path=groups_path,
            book=book,
            jobs=jobs,
            skipped=skipped,
            silent=silent,
            taps_dir=taps_dir,
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


def _build_audios_locked(
    book_id: str,
    section: str | None,
    model: str,
    update: bool,
    *,
    batch: bool,
    batch_parallel: int,
    download_workers: int,
    groups_path: Path,
    book: dict[str, Any],
    jobs: list[Job],
    skipped: int,
    silent: int,
    taps_dir: Path,
    state: BatchState,
) -> tuple[Path, int]:
    prompt, prompt_files = audio_prompt(book)
    if batch:
        opening = f"voicing {len(jobs)} segment(s) through the Batch API"
    else:
        workers = min(len(jobs), MAX_WORKERS)
        opening = f"voicing {len(jobs)} segment(s) with {workers} worker(s)"
    if skipped:
        opening += f"; {skipped} already written"
    if silent:
        opening += f"; {silent} with no transcript"
    print(opening, flush=True)
    print("  opening the model client", flush=True)
    client = genai.Client()
    print("  setting the log clock", flush=True)
    logger = ApiLogger(OUTPUT_DIR / book_id)
    taps_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    failures: list[str] = []
    with Progress(len(jobs), phases=("still with the model",)) as progress:
        run = Run(
            client=client,
            logger=logger,
            book_id=book_id,
            model=model,
            prompt=prompt,
            prompt_files=prompt_files,
            taps_dir=taps_dir,
            store=BookStore(groups_path, book),
            progress=progress,
            update=update,
            section=section,
        )
        adopted: list[dict[str, Any]] = []
        prefetched: dict[str, BatchStatus] = {}
        if entries(state.load()):
            adopted, prefetched = resume_saved_audio_jobs(run, state)
        jobs, skipped, silent = collect_jobs(
            book_id, book, taps_dir, section, update
        )
        abandoned_keys: set[str] = set()
        if not jobs and not adopted:
            pass
        elif batch:
            got, failures, abandoned_keys = voice_segments_batch(
                run,
                jobs,
                state,
                update,
                adopted=adopted,
                parallel=batch_parallel,
                prefetched=prefetched,
                download_workers=download_workers,
            )
            written += got
        else:
            if adopted:
                got, failures, abandoned_keys = voice_segments_batch(
                    run,
                    jobs,
                    state,
                    update,
                    adopted=adopted,
                    parallel=batch_parallel,
                    submit_leftovers=False,
                    prefetched=prefetched,
                    download_workers=download_workers,
                )
                written += got
                jobs, skipped, silent = collect_jobs(
                    book_id, book, taps_dir, section, update
                )
            jobs = [job for job in jobs if job.item not in abandoned_keys]
            if jobs:
                got, unary_failures = voice_segments_unary(run, jobs)
                written += got
                failures.extend(unary_failures)

    summary = f"{written} segment(s) voiced"
    if skipped:
        summary += f", {skipped} already written"
    if silent:
        summary += f", {silent} with no transcript"
    if failures:
        summary += f", {len(failures)} failed"
        print("\n".join(f"  FAILED {line}" for line in failures), flush=True)
    print(f"OK: {summary} under {taps_dir.relative_to(PROJECT_ROOT)}")
    return taps_dir, len(failures)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_id", help="Book ID, e.g. mathematics_ar_sa06_t2")
    parser.add_argument("-s", "--section", help="voice only this section, e.g. -s 1.1")
    parser.add_argument(
        "--update", action="store_true", help="rewrite audio that already exists"
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"model to call (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--batch",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_BATCH,
        help="voice gemini-3.8-flash-tts through the Gemini Batch API (default); "
        "--no-batch calls unary generateContent",
    )
    parser.add_argument(
        "--discard-batch-state",
        action="store_true",
        help="delete data/output/<book>/00_audios_batch_state.json only; "
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
    if args.batch_parallel < 0:
        parser.error("--batch-parallel must be >= 0")
    if args.download_workers < 1:
        parser.error("--download-workers must be at least 1")
    try:
        _, failed = build_audios(
            args.book_id,
            args.section,
            args.model,
            args.update,
            batch=args.batch,
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
