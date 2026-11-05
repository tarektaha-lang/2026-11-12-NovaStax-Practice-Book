"""Shared Gemini Batch helpers for transcripts.py and audios.py.

Inline Batch jobs stay under 150 requests and 18 MB. Size is the sum of
base64 image bytes, prompt/group/spec text, and the JSON-schema dump of
``response_schema``. ``InlinedRequest.model_dump_json()`` is never used: a
Pydantic class as ``response_schema`` raises ``PydanticSerializationError``.
A chunk that still exceeds 18 MB (one huge crop) is uploaded as JSONL after
converting that schema with the SDK ``t_schema`` helper.

Rows are mapped only by ``metadata["item"]`` (inline) or the JSONL ``key``
field (the same item string). There is no positional fallback.

``run_batch_jobs`` submits every chunk of a round (optional
``--batch-parallel N`` cap; 0 = no cap), polls live jobs in one
single-threaded loop, and applies each job as soon as it finishes.
``429 RESOURCE_EXHAUSTED`` at upload or create re-queues the chunk without
spending an attempt. The state file holds a ``jobs`` list and still reads
the legacy single ``job`` object.
"""

from __future__ import annotations

import base64
import fcntl
import inspect
import json
import os
import re
import tempfile
import time
import uuid
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import httpx
from google.genai import types
from google.genai._transformers import t_contents, t_schema
from google.genai.errors import APIError
from pydantic import BaseModel

BATCH_CHUNK = 150
BATCH_MAX_INLINE_BYTES = 18 * 1024 * 1024
BATCH_POLL_SECONDS = 20.0
BATCH_POLL_BACKOFF = 1.5
BATCH_POLL_CAP = 120.0
BATCH_RATE_WAIT_SECONDS = 60.0
BATCH_RATE_WAIT_BACKOFF = 1.5
BATCH_RATE_WAIT_CAP = 600.0
BATCH_POLL_ERROR_LIMIT = 30
BATCH_DISPLAY_NAME_MAX = 120

# Probe not run in this build; fallback UNKNOWN sends full gets to workers.
STATUS_MASK_ENABLED = False
STATUS_TIMEOUT_MS = 60_000
STATUS_FIELD_MASK = "name,done,metadata.state,metadata.batchStats,error"
FULL_GET_TIMEOUT_MS = 300_000
FULL_GET_SERVER_TIMEOUT = "3600"
FULL_GET_RETRIES = 3
FULL_GET_MASK: str | None = None
_MASK_DISABLED = not STATUS_MASK_ENABLED
_MASK_LOGGED = False

FINISH_DONE = "DONE"
FINISH_FAILED = "FAILED"
FINISH_GONE = "GONE"

BATCH_DONE = frozenset(
    {
        "JOB_STATE_SUCCEEDED",
        "JOB_STATE_PARTIALLY_SUCCEEDED",
        "JOB_STATE_FAILED",
        "JOB_STATE_CANCELLED",
        "JOB_STATE_EXPIRED",
    }
)
BATCH_FAILED = frozenset(
    {
        "JOB_STATE_FAILED",
        "JOB_STATE_CANCELLED",
        "JOB_STATE_EXPIRED",
    }
)

LogFn = Callable[[str], None]
SubmitFn = Callable[["BatchChunk"], None]
FinishResult = tuple[str, list[Any], str | None]
FinishedFn = Callable[[dict[str, Any], "BatchChunk", "BatchStatus", float], FinishResult]
FailedFn = Callable[["BatchChunk", str], None]
AccountFn = Callable[["BatchChunk", list[Any]], None]
AbandonedFn = Callable[["BatchChunk", str], None]
RequeueFn = Callable[["BatchChunk"], list["BatchChunk"]]


@dataclass
class BatchStatus:
    """A status-only or held full Batch job."""

    name: str
    state: str
    error: Any
    light: bool
    job: Any | None


class ApplyError(Exception):
    """Worker apply failed after some rows were already written."""

    def __init__(self, message: str, partial_outcomes: list[Any] | None = None) -> None:
        super().__init__(message)
        self.partial_outcomes = list(partial_outcomes or [])


def new_run_id() -> str:
    """Short unique id kept intact at the end of a Batch display_name."""
    return uuid.uuid4().hex[:8]


def job_state_name(job: Any) -> str:
    """BatchJob.state as JOB_STATE_*, whether it arrived as an enum or a string."""
    state = getattr(job, "state", None)
    name = getattr(state, "name", None)
    if isinstance(name, str) and name:
        return name
    value = getattr(state, "value", None)
    if isinstance(value, str) and value:
        return value
    return str(state or "")


def finish_token(job: Any | None) -> str:
    """Map a fetched job to a FinishResult state token. BATCH_DONE is never DONE."""
    if job is None:
        return FINISH_GONE
    state = job_state_name(job)
    if state in ("JOB_STATE_SUCCEEDED", "JOB_STATE_PARTIALLY_SUCCEEDED"):
        return FINISH_DONE
    if state in BATCH_FAILED:
        return FINISH_FAILED
    return state


def reset_status_mask(enabled: bool = True) -> None:
    """Reset the once-per-process mask fallback. Tests call this in setUp/tearDown."""
    global _MASK_DISABLED, _MASK_LOGGED
    _MASK_DISABLED = not enabled
    _MASK_LOGGED = False


def _get_rejects_config(client: Any) -> bool:
    """True when batches.get cannot take a config= kwarg (test fakes). Not cached."""
    getter = getattr(getattr(client, "batches", None), "get", None)
    if getter is None:
        return True
    try:
        params = inspect.signature(getter).parameters
    except (TypeError, ValueError):
        return True
    if "config" in params:
        return False
    return all(param.kind != inspect.Parameter.VAR_KEYWORD for param in params.values())


def _disable_mask(reason: str, log: LogFn) -> None:
    global _MASK_DISABLED, _MASK_LOGGED
    _MASK_DISABLED = True
    if not _MASK_LOGGED:
        _MASK_LOGGED = True
        log(f"  status field mask off: {reason}; using full batches.get")


def _dest_has_results(job: Any) -> bool:
    dest = getattr(job, "dest", None)
    if dest is None:
        return False
    if getattr(dest, "inlined_responses", None):
        return True
    return bool(getattr(dest, "file_name", None))


def get_batch_status(
    client: Any, name: str, log: LogFn = print
) -> BatchStatus | None:
    """Cheap status get, or UNKNOWN in fallback. Never a full get on this thread."""
    if _MASK_DISABLED:
        if not _MASK_LOGGED:
            _disable_mask(
                "probe" if not STATUS_MASK_ENABLED else "status field mask off",
                log,
            )
        return BatchStatus(name, "UNKNOWN", None, False, None)
    try:
        job = client.batches.get(
            name=name,
            config={
                "http_options": {
                    "headers": {"X-Goog-FieldMask": STATUS_FIELD_MASK},
                    "timeout": STATUS_TIMEOUT_MS,
                }
            },
        )
    except TypeError:
        if not _get_rejects_config(client):
            raise
        _disable_mask("status call does not accept config", log)
        return BatchStatus(name, "UNKNOWN", None, False, None)
    except APIError as error:
        if getattr(error, "code", None) == 400 or getattr(error, "status", None) == "INVALID_ARGUMENT":
            _disable_mask("field mask rejected (400)", log)
            return BatchStatus(name, "UNKNOWN", None, False, None)
        if is_not_found(error):
            return None
        raise
    except Exception as error:
        if is_not_found(error):
            return None
        raise
    else:
        state = job_state_name(job)
        if _dest_has_results(job):
            _disable_mask("field mask ignored", log)
            return BatchStatus(name, state, None, False, job)
        if not state.startswith("JOB_STATE_"):
            _disable_mask("unparseable masked reply", log)
            return BatchStatus(name, "UNKNOWN", None, False, None)
        return BatchStatus(name, state, None, True, None)


def batch_display_name(*parts: str, run_id: str | None = None) -> str:
    """A Batch display_name the API will accept (at most 120 characters).

    Without ``run_id``, parts are joined, sanitised, and sliced from the
    right — the historical audios.py behaviour, so a refactor stays
    byte-identical on today's calls.

    With ``run_id``, only the prefix (book, stage, round, chunk) is
    truncated. The run id is appended last and is never cut, so a resume
    can still match the name.
    """
    if run_id is None:
        raw = "-".join(str(part) for part in parts)
        return re.sub(r"[^A-Za-z0-9._-]+", "-", raw)[:BATCH_DISPLAY_NAME_MAX]

    def clean(text: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "-", str(text)).strip("-.")

    run = clean(run_id)
    prefix = "-".join(clean(part) for part in parts if str(part).strip())
    if not run:
        return prefix[:BATCH_DISPLAY_NAME_MAX]
    budget = BATCH_DISPLAY_NAME_MAX - len(run) - 1
    if budget < 1:
        return run[:BATCH_DISPLAY_NAME_MAX]
    if len(prefix) > budget:
        prefix = prefix[:budget].rstrip("-.")
    if not prefix:
        return run[:BATCH_DISPLAY_NAME_MAX]
    return f"{prefix}-{run}"


def wait_for_batch(client: Any, name: str, label: str) -> Any:
    """Poll until the Batch job lands in a terminal state.

    Sleeps 20s, then ×1.5 each time, capped at 120s. There is no timeout:
    a Batch job may take up to 24h.
    """
    delay = BATCH_POLL_SECONDS
    while True:
        current = client.batches.get(name=name)
        state = job_state_name(current)
        print(f"  {label}: {state}", flush=True)
        if state in BATCH_DONE:
            return current
        time.sleep(delay)
        delay = min(delay * BATCH_POLL_BACKOFF, BATCH_POLL_CAP)


def inlined_responses(client: Any, batch_job: Any) -> list[Any]:
    """The per-request replies, from dest.inlined_responses or a JSONL result file."""
    dest = getattr(batch_job, "dest", None)
    inlined = getattr(dest, "inlined_responses", None) if dest is not None else None
    if inlined:
        return list(inlined)
    file_name = getattr(dest, "file_name", None) if dest is not None else None
    if not file_name:
        raise RuntimeError("batch job has no inlined responses or result file")
    raw = client.files.download(file=file_name)
    text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    rows: list[Any] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def result_file_name(batch_job: Any) -> str | None:
    """The dest.file_name of a JSONL result, if the job wrote one."""
    dest = getattr(batch_job, "dest", None)
    name = getattr(dest, "file_name", None) if dest is not None else None
    return str(name) if name else None


def metadata_of(row: Any) -> dict[str, str]:
    if isinstance(row, dict):
        meta = row.get("metadata") or {}
    else:
        meta = getattr(row, "metadata", None) or {}
    if not isinstance(meta, dict):
        return {}
    return {str(key): str(value) for key, value in meta.items()}


def row_key(row: Any) -> str | None:
    """The mapping id: JSONL ``key`` or inline ``metadata['item']``."""
    if isinstance(row, dict):
        key = row.get("key")
        if key:
            return str(key)
        item = (row.get("metadata") or {}).get("item") if isinstance(row.get("metadata"), dict) else None
        return str(item) if item else None
    key = getattr(row, "key", None)
    if key:
        return str(key)
    meta = getattr(row, "metadata", None) or {}
    if isinstance(meta, dict):
        item = meta.get("item")
        return str(item) if item else None
    return None


def map_rows_by_key(
    rows: Iterable[Any],
    *,
    log: LogFn = print,
) -> dict[str, Any]:
    """Map Batch rows by item id. No positional fallback.

    A duplicate key keeps the first row and logs the rest. A row with no
    key is skipped (never assigned by position). An unknown key is simply
    present in the dict for the caller to ignore. A missing key is a
    failed item for that round, decided by the caller.
    """
    mapped: dict[str, Any] = {}
    for row in rows:
        key = row_key(row)
        if not key:
            continue
        if key in mapped:
            log(f"  duplicate batch key {key}; keeping the first row")
            continue
        mapped[key] = row
    return mapped


def response_and_error(row: Any) -> tuple[Any | None, str | None]:
    if isinstance(row, dict):
        err = row.get("error")
        if err:
            if isinstance(err, dict):
                return None, str(err.get("message") or err)
            return None, str(err)
        return row.get("response"), None
    err = getattr(row, "error", None)
    if err is not None:
        message = getattr(err, "message", None) or str(err)
        return None, str(message)
    return getattr(row, "response", None), None


def generate_content_response(response: Any) -> Any:
    if isinstance(response, dict):
        return types.GenerateContentResponse.model_validate(response)
    return response


def schema_json_dump(schema: Any) -> Any:
    """JSON-schema dump of a response_schema, including a Pydantic class."""
    if schema is None:
        return None
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema.model_json_schema()
    if isinstance(schema, BaseModel):
        return schema.model_dump(mode="json")
    if hasattr(schema, "model_json_schema"):
        return schema.model_json_schema()
    if hasattr(schema, "model_dump"):
        return schema.model_dump(mode="json")
    return schema


def request_payload_size(request: types.InlinedRequest) -> int:
    """Estimated JSONL/inline size. Never calls InlinedRequest.model_dump_json()."""
    size = 0
    for kind, payload in _iter_request_payloads(request):
        if kind == "text":
            size += len(str(payload).encode("utf-8"))
        elif kind == "image":
            if isinstance(payload, bytes):
                size += len(base64.b64encode(payload))
            elif isinstance(payload, str):
                size += len(payload.encode("utf-8"))
    config = getattr(request, "config", None)
    schema = getattr(config, "response_schema", None) if config is not None else None
    dumped = schema_json_dump(schema)
    if dumped is not None:
        size += len(json.dumps(dumped, default=str).encode("utf-8"))
    return size


def _iter_request_payloads(request: types.InlinedRequest) -> Iterable[tuple[str, Any]]:
    contents = getattr(request, "contents", None) or []
    for content in contents:
        yield from _iter_content_payloads(content)


def _iter_content_payloads(content: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(content, str):
        yield "text", content
        return
    parts = getattr(content, "parts", None)
    if parts is None:
        text = getattr(content, "text", None)
        if text:
            yield "text", text
        inline = getattr(content, "inline_data", None)
        data = getattr(inline, "data", None) if inline is not None else None
        if data:
            yield "image", data
        return
    for part in parts:
        if isinstance(part, str):
            yield "text", part
            continue
        text = getattr(part, "text", None)
        if text:
            yield "text", text
        inline = getattr(part, "inline_data", None)
        data = getattr(inline, "data", None) if inline is not None else None
        if data:
            yield "image", data


def chunk_requests(
    pairs: list[tuple[str, types.InlinedRequest]],
) -> list[list[tuple[str, types.InlinedRequest]]]:
    """Split into chunks of at most 150 requests and 18 MB estimated size.

    A single request larger than 18 MB is still its own chunk and is
    submitted as JSONL.
    """
    chunks: list[list[tuple[str, types.InlinedRequest]]] = []
    current: list[tuple[str, types.InlinedRequest]] = []
    current_bytes = 0
    for pair in pairs:
        size = request_payload_size(pair[1])
        if current and (
            len(current) >= BATCH_CHUNK
            or current_bytes + size > BATCH_MAX_INLINE_BYTES
        ):
            chunks.append(current)
            current = []
            current_bytes = 0
        current.append(pair)
        current_bytes += size
    if current:
        chunks.append(current)
    return chunks


def chunk_over_inline_limit(pairs: list[tuple[str, types.InlinedRequest]]) -> bool:
    return sum(request_payload_size(request) for _, request in pairs) > BATCH_MAX_INLINE_BYTES


def jsonl_request_payload(request: types.InlinedRequest, api_client: Any = None) -> dict[str, Any]:
    """One JSONL ``request`` object, with ``response_schema`` run through t_schema."""
    contents = t_contents(request.contents)
    payload: dict[str, Any] = {
        "contents": [
            content.model_dump(mode="json", by_alias=True, exclude_none=True)
            for content in contents
        ]
    }
    if request.model:
        payload["model"] = request.model
    config = request.config
    if config is None:
        return payload
    generation = config.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
        exclude={"response_schema", "http_options", "should_return_http_response"},
    )
    schema = config.response_schema
    if schema is not None:
        converted = t_schema(api_client, schema)
        if converted is not None:
            generation["responseSchema"] = converted.model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
    if generation:
        payload["generationConfig"] = generation
    return payload


def jsonl_src(
    client: Any,
    pairs: list[tuple[str, types.InlinedRequest]],
    display_name: str,
) -> str:
    """Upload ``{"key": item, "request": ...}`` JSONL. Returns the files/ name."""
    api_client = getattr(client, "_api_client", None)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".jsonl",
        delete=False,
        encoding="utf-8",
    )
    path = handle.name
    try:
        with handle:
            for item, request in pairs:
                line = {
                    "key": item,
                    "request": jsonl_request_payload(request, api_client),
                }
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")
        uploaded_name = None
        try:
            uploaded = client.files.upload(
                file=path,
                config={"mime_type": "jsonl", "display_name": display_name[:40]},
            )
            uploaded_name = getattr(uploaded, "name", None)
            if not uploaded_name:
                raise RuntimeError("files.upload returned no name")
            return str(uploaded_name)
        except Exception:
            if uploaded_name:
                delete_files(client, uploaded_name)
            raise
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def src_for_chunk(
    client: Any,
    pairs: list[tuple[str, types.InlinedRequest]],
    display_name: str,
) -> tuple[Any, str | None]:
    """Inline list of InlinedRequest, or a JSONL file name when over 18 MB.

    Returns ``(src, uploaded_file_name)``. ``uploaded_file_name`` is None
    for an inline src.
    """
    if not chunk_over_inline_limit(pairs):
        return [request for _, request in pairs], None
    uploaded = jsonl_src(client, pairs, display_name)
    return uploaded, uploaded


def delete_files(client: Any, *names: str | None, log: LogFn = print) -> None:
    """Delete uploaded input / result files. A delete error is logged, never fatal."""
    for name in names:
        if not name:
            continue
        try:
            client.files.delete(name=name)
        except Exception as error:  # noqa: BLE001 - cleanup must not fail the run
            log(f"  could not delete {name}: {error}")


def is_rate_limited(error: BaseException) -> bool:
    """True for 429 / RESOURCE_EXHAUSTED, including APIError subclasses."""
    if "RESOURCE_EXHAUSTED" in str(error):
        return True
    if isinstance(error, APIError) and (
        getattr(error, "code", None) == 429
        or getattr(error, "status", None) == "RESOURCE_EXHAUSTED"
    ):
        return True
    return False


def find_job_by_display_name(client: Any, display_name: str) -> Any | None:
    """Page through ``client.batches.list()`` with no filter= and match in Python.

    ``ListBatchJobsConfig.filter`` raises on the Gemini Developer API.
    """
    pager = client.batches.list()
    for job in pager:
        if getattr(job, "display_name", None) == display_name:
            return job
    return None


def find_jobs_by_display_names(
    client: Any, names: Iterable[str]
) -> dict[str, list[Any]]:
    """One pass over ``client.batches.list()`` (no filter=). Values are lists."""
    wanted = {str(name) for name in names if name}
    found: dict[str, list[Any]] = {name: [] for name in wanted}
    if not wanted:
        return found
    pager = client.batches.list()
    for job in pager:
        display = getattr(job, "display_name", None)
        if display in wanted:
            found[str(display)].append(job)
    return found


def _create_time_sort_key(job: Any) -> tuple[int, datetime]:
    """Missing create_time counts as oldest so a timestamped match wins."""
    value = getattr(job, "create_time", None)
    if value is None:
        return (0, datetime.min.replace(tzinfo=timezone.utc))
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return (1, value)
    return (1, datetime.min.replace(tzinfo=timezone.utc))


def pick_duplicate_match(matches: list[Any]) -> tuple[Any | None, str]:
    """Choose one remote job when several share a display_name.

    One non-terminal match is adopted. Several non-terminal, or all
    SUCCEEDED/PARTIALLY_SUCCEEDED, take the newest ``create_time``.
    Resubmit only when every match is FAILED/CANCELLED/EXPIRED.
    """
    names = [str(getattr(job, "name", None) or "?") for job in matches]
    warning = (
        "WARNING: multiple batch jobs share a display_name: " + ", ".join(names)
    )
    non_terminal: list[Any] = []
    succeeded: list[Any] = []
    failed: list[Any] = []
    for job in matches:
        state = job_state_name(job)
        if state in BATCH_FAILED:
            failed.append(job)
        elif state in BATCH_DONE:
            succeeded.append(job)
        else:
            non_terminal.append(job)
    if len(matches) == 1:
        return matches[0], ""
    if len(non_terminal) == 1 and not succeeded:
        return non_terminal[0], warning
    if len(failed) == len(matches):
        return None, warning
    candidates = non_terminal + succeeded
    if not candidates:
        return None, warning
    return max(candidates, key=_create_time_sort_key), warning


@dataclass
class BatchChunk:
    """One Batch job's worth of requests, ready to submit or already adopted."""

    label: str
    display_name: str
    attempt: int
    keys: dict[str, str]
    items: list[Any]
    build_src: Callable[[], tuple[Any, str | None]] | None = None


def _short_state(name: str) -> str:
    return name.removeprefix("JOB_STATE_") if name else "?"


_SUCCEEDED_STATES = frozenset(
    {"JOB_STATE_SUCCEEDED", "JOB_STATE_PARTIALLY_SUCCEEDED"}
)


def run_batch_jobs(
    client: Any,
    model: str,
    state: BatchState,
    envelope: dict[str, Any],
    queue: list[BatchChunk],
    adopted: list[tuple[dict[str, Any], BatchChunk]],
    parallel: int,
    attempt: int,
    on_finished: FinishedFn,
    on_failed: FailedFn,
    log: LogFn = print,
    on_submit: SubmitFn | None = None,
    prefetched: dict[str, BatchStatus] | None = None,
    download_workers: int = 3,
    on_account: AccountFn | None = None,
    on_abandoned: AbandonedFn | None = None,
    on_requeue: RequeueFn | None = None,
) -> None:
    """Submit queued chunks and download finished jobs in worker threads.

    ``attempt`` is this run's round number for the status line. Adopted
    jobs keep ``started=None`` so elapsed is 0.0. ``429`` re-queues the
    chunk without popping it. Known-DONE downloads do not count toward
    ``parallel``. KeyboardInterrupt is re-raised after executor shutdown.
    """
    live: dict[str, tuple[dict[str, Any], BatchChunk, float | None]] = {}
    adopted_names: set[str] = set()
    for entry, chunk in adopted:
        name = entry.get("name")
        if not name:
            continue
        live[str(name)] = (entry, chunk, None)
        adopted_names.add(str(name))
    last_states: dict[str, str] = {}
    errors: dict[str, int] = defaultdict(int)
    timeouts: dict[str, int] = defaultdict(int)
    terminal_seen: set[str] = set()
    blocked = False
    poll = BATCH_POLL_SECONDS
    rate_wait = BATCH_RATE_WAIT_SECONDS
    done_count = 0
    fail_count = 0
    pending = list(queue)
    stage = str(envelope.get("stage") or "batch")
    prefetch = dict(prefetched or {})
    workers = max(1, int(download_workers or 1))
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="batch-dl")
    futures: dict[Any, tuple[str, dict[str, Any], BatchChunk, float, BatchStatus, bool]] = {}

    def downloading_count() -> int:
        return sum(1 for meta in futures.values() if meta[5])

    def in_flight_names() -> set[str]:
        return {meta[0] for meta in futures.values()}

    def cleanup(
        name: str,
        entry: dict[str, Any],
        chunk: BatchChunk,
        result_name: str | None,
    ) -> None:
        state.remove_entry(chunk.display_name)
        delete_files(client, entry.get("src_file_name"), result_name, log=log)
        live.pop(name, None)
        last_states.pop(name, None)

    def requeue_adopted(name: str, chunk: BatchChunk) -> bool:
        if (
            on_requeue is None
            or attempt != 1
            or name not in adopted_names
            or name in terminal_seen
        ):
            return False
        leftovers = on_requeue(chunk)
        pending.extend(leftovers)
        return True

    def handle_failed(
        name: str,
        entry: dict[str, Any],
        chunk: BatchChunk,
        detail: str,
        result_name: str | None,
    ) -> None:
        nonlocal fail_count
        if requeue_adopted(name, chunk):
            cleanup(name, entry, chunk, result_name)
            return
        fail_count += 1
        on_failed(chunk, detail)
        cleanup(name, entry, chunk, result_name)

    def harvest(done_futs: object | None = None) -> bool:
        nonlocal done_count, fail_count
        finished_any = False
        targets = list(done_futs) if done_futs is not None else [
            fut for fut in list(futures) if fut.done()
        ]
        for fut in targets:
            if fut not in futures:
                continue
            name, entry, chunk, _elapsed, _status, _known = futures.pop(fut)
            try:
                result = fut.result()
            except Exception as error:  # noqa: BLE001 - timeout vs apply vs other
                finished_any = True
                if _is_fetch_timeout(error):
                    timeouts[name] += 1
                    if timeouts[name] >= FULL_GET_RETRIES:
                        live.pop(name, None)
                        last_states.pop(name, None)
                        fail_count += 1
                        if on_abandoned is not None:
                            on_abandoned(
                                chunk,
                                f"full get timed out {FULL_GET_RETRIES} times for {name}",
                            )
                    continue
                partial = getattr(error, "partial_outcomes", None) or []
                if partial and on_account is not None:
                    on_account(chunk, partial)
                fail_count += 1
                on_failed(chunk, f"{type(error).__name__}: {error}")
                state.remove_entry(chunk.display_name)
                delete_files(client, entry.get("src_file_name"), log=log)
                live.pop(name, None)
                last_states.pop(name, None)
                continue
            token, outcomes, result_file = result
            if token == FINISH_DONE:
                finished_any = True
                terminal_seen.add(name)
                if on_account is not None:
                    on_account(chunk, outcomes)
                done_count += 1
                cleanup(name, entry, chunk, result_file)
                continue
            if token == FINISH_FAILED:
                finished_any = True
                handle_failed(name, entry, chunk, token, result_file)
                terminal_seen.add(name)
                continue
            if token == FINISH_GONE:
                finished_any = True
                handle_failed(name, entry, chunk, "gone", None)
                terminal_seen.add(name)
                continue
            if name in last_states and last_states[name] != token:
                finished_any = True
            last_states[name] = token
        return finished_any

    def submit_pending() -> None:
        nonlocal blocked, fail_count
        while (
            pending
            and (
                parallel == 0
                or (len(live) - downloading_count()) < parallel
            )
            and not blocked
        ):
            chunk = pending[0]
            entry = {
                "name": None,
                "display_name": chunk.display_name,
                "attempt": chunk.attempt,
                "src_file_name": None,
                "keys": dict(chunk.keys),
            }
            state.add_entry(envelope, entry)
            uploaded: str | None = None
            try:
                if chunk.build_src is None:
                    raise RuntimeError(f"{chunk.label}: no build_src")
                src, uploaded = chunk.build_src()
                state.set_entry_field(chunk.display_name, "src_file_name", uploaded)
                entry["src_file_name"] = uploaded
                created = client.batches.create(
                    model=model,
                    src=src,
                    config={"display_name": chunk.display_name},
                )
            except Exception as error:  # noqa: BLE001 - 429 vs other create/upload
                delete_files(client, uploaded, log=log)
                state.remove_entry(chunk.display_name)
                if is_rate_limited(error):
                    blocked = True
                    log(
                        f"  429 RESOURCE_EXHAUSTED on {chunk.label}: re-queued, "
                        f"{len(pending)} queued, {len(live)} running"
                    )
                    break
                pending.pop(0)
                fail_count += 1
                on_failed(chunk, f"{type(error).__name__}: {error}")
                continue
            name = str(getattr(created, "name", "") or "")
            state.set_entry_field(chunk.display_name, "name", name)
            entry["name"] = name
            pending.pop(0)
            live[name] = (entry, chunk, time.perf_counter())
            if on_submit is not None:
                on_submit(chunk)
            log(
                f"  submitted {chunk.label}: {name} ({len(chunk.keys)} request(s))"
            )

    try:
        while True:
            submit_pending()

            if not live and not pending and not futures:
                executor.shutdown(wait=True)
                return
            if not live and not futures:
                log(
                    f"  no job running, {len(pending)} chunk(s) waiting on 429; "
                    f"retrying create in {int(rate_wait)}s"
                )
                time.sleep(rate_wait)
                rate_wait = min(rate_wait * BATCH_RATE_WAIT_BACKOFF, BATCH_RATE_WAIT_CAP)
                blocked = False
                continue

            changed = False
            finished_any = False
            busy = in_flight_names()
            for name in list(live):
                if name in busy:
                    continue
                entry, chunk, started = live[name]
                try:
                    if name in prefetch:
                        status = prefetch.pop(name)
                    else:
                        status = get_batch_status(client, name, log=log)
                except Exception as error:  # noqa: BLE001 - keep polling otherwise
                    log(f"  {chunk.label}: poll error: {type(error).__name__}: {error}")
                    errors[name] += 1
                    if errors[name] >= BATCH_POLL_ERROR_LIMIT:
                        message = (
                            f"poll failed {BATCH_POLL_ERROR_LIMIT} times in a row "
                            f"for {name}; stopping, state kept, rerun to resume"
                        )
                        log(f"  {message}")
                        raise RuntimeError(message) from error
                    continue
                errors[name] = 0
                if status is None:
                    state.remove_entry(chunk.display_name)
                    delete_files(client, entry.get("src_file_name"), log=log)
                    del live[name]
                    last_states.pop(name, None)
                    fail_count += 1
                    on_failed(chunk, "gone")
                    finished_any = True
                    continue
                job_state = status.state
                if name in last_states and last_states[name] != job_state:
                    changed = True
                last_states[name] = job_state
                if job_state in BATCH_FAILED:
                    error = status.error
                    if error is None and status.job is not None:
                        error = getattr(status.job, "error", None)
                    message = getattr(error, "message", None) if error is not None else None
                    detail = f"{job_state}: {message or error or 'no error detail'}"
                    result_name = result_file_name(status.job) if status.job is not None else None
                    fail_count += 1
                    on_failed(chunk, detail)
                    state.remove_entry(chunk.display_name)
                    delete_files(
                        client,
                        entry.get("src_file_name"),
                        result_name,
                        log=log,
                    )
                    del live[name]
                    last_states.pop(name, None)
                    finished_any = True
                    continue
                if job_state in _SUCCEEDED_STATES or job_state == "UNKNOWN":
                    if started is None:
                        elapsed = 0.0
                    else:
                        elapsed = time.perf_counter() - started
                    known_done = job_state in _SUCCEEDED_STATES
                    fut = executor.submit(on_finished, entry, chunk, status, elapsed)
                    futures[fut] = (name, entry, chunk, elapsed, status, known_done)
                    if known_done:
                        in_flight = downloading_count()
                        log(f"  {chunk.label}: downloading ({in_flight} in flight)")
                    continue

            finished_any = harvest() or finished_any
            submit_pending()

            counts: dict[str, int] = defaultdict(int)
            for live_name in live:
                counts[_short_state(last_states.get(live_name, ""))] += 1
            state_text = ", ".join(
                f"{label} {count}" for label, count in sorted(counts.items()) if label
            ) or "none"
            log(
                f"  {stage} a{attempt}: {len(live)} running, {len(pending)} queued, "
                f"{done_count} done, {fail_count} failed; {state_text}"
            )
            if finished_any:
                blocked = False
                rate_wait = BATCH_RATE_WAIT_SECONDS
            if changed or finished_any:
                poll = BATCH_POLL_SECONDS
            if futures:
                done, _ = wait(list(futures), timeout=poll, return_when=FIRST_COMPLETED)
                harvest(done)
            elif not (changed or finished_any):
                time.sleep(poll)
                poll = min(poll * BATCH_POLL_BACKOFF, BATCH_POLL_CAP)
    except BaseException:
        for fut in list(futures):
            fut.cancel()
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            executor.shutdown(wait=False)
        raise


def is_not_found(error: BaseException) -> bool:
    code = getattr(error, "code", None)
    if code in (404, "404", "NOT_FOUND"):
        return True
    status = getattr(error, "status", None)
    if status in (404, "NOT_FOUND"):
        return True
    if "notfound" in type(error).__name__.lower():
        return True
    text = str(error)
    return "NOT_FOUND" in text or " 404" in f" {text}" or text.startswith("404")


def _is_fetch_timeout(error: BaseException) -> bool:
    if isinstance(error, (httpx.TimeoutException, TimeoutError)):
        return True
    name = type(error).__name__.lower()
    if "timeout" in name:
        return True
    return "timeout" in str(error).lower() or "timed out" in str(error).lower()


def get_batch_job(
    client: Any, name: str, timeout_ms: int | None = None
) -> Any | None:
    """``batches.get``, or None when the job is gone (NOT_FOUND)."""
    config: dict[str, Any] | None = None
    if timeout_ms is not None:
        headers: dict[str, str] = {"X-Server-Timeout": FULL_GET_SERVER_TIMEOUT}
        if FULL_GET_MASK:
            headers["X-Goog-FieldMask"] = FULL_GET_MASK
        config = {
            "http_options": {
                "timeout": timeout_ms,
                "headers": headers,
            }
        }
    try:
        if config is None:
            return client.batches.get(name=name)
        return client.batches.get(name=name, config=config)
    except TypeError:
        if config is None or not _get_rejects_config(client):
            raise
        return client.batches.get(name=name)
    except Exception as error:
        if is_not_found(error):
            return None
        raise


def batch_state_path(book_dir: Path, stage: str) -> Path:
    return book_dir / f"00_{stage}_batch_state.json"


def batch_lock_path(book_dir: Path, stage: str) -> Path:
    return book_dir / f"00_{stage}_batch.lock"


def entries(data: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The in-flight jobs: ``jobs`` list, else a legacy ``job`` object as one entry."""
    if not data:
        return []
    jobs = data.get("jobs")
    if isinstance(jobs, list):
        return [row for row in jobs if isinstance(row, dict)]
    job = data.get("job")
    if isinstance(job, dict):
        return [job]
    return []


def is_legacy(data: dict[str, Any] | None) -> bool:
    return bool(data) and "job" in data and "jobs" not in data


def _envelope_of(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if key not in ("job", "jobs")}


class BatchState:
    """Atomic load/save/forget of in-flight Batch jobs."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(data)
        job = payload.get("job")
        if job is not None and not isinstance(job, dict):
            raise ValueError("batch state job must be an object")
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def save_entries(
        self, envelope: dict[str, Any], job_entries: list[dict[str, Any]]
    ) -> None:
        """Write ``jobs`` only (never ``job``). An empty list deletes the file."""
        if not job_entries:
            self.forget()
            return
        payload = dict(envelope)
        payload.pop("job", None)
        payload["jobs"] = [dict(row) for row in job_entries]
        self.save(payload)

    def forget(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def job(self) -> dict[str, Any] | None:
        items = entries(self.load())
        return items[0] if items else None

    def add_entry(self, envelope: dict[str, Any], entry: dict[str, Any]) -> None:
        data = self.load() or {}
        current = entries(data)
        merged = dict(envelope)
        if data:
            for key, value in _envelope_of(data).items():
                merged.setdefault(key, value)
        current.append(dict(entry))
        self.save_entries(merged, current)

    def set_entry_field(self, display_name: str, field: str, value: Any) -> None:
        data = self.load()
        if not data:
            return
        current = entries(data)
        for row in current:
            if row.get("display_name") == display_name:
                row[field] = value
                break
        self.save_entries(_envelope_of(data), current)

    def remove_entry(self, display_name: str) -> None:
        data = self.load()
        if not data:
            return
        current = [row for row in entries(data) if row.get("display_name") != display_name]
        self.save_entries(_envelope_of(data), current)


class BatchLock:
    """Exclusive non-blocking flock. Released in ``finally`` so Ctrl-C frees it."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: Any | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            handle.close()
            raise RuntimeError(
                f"another run holds {self.path.name} for this book"
            ) from error
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self) -> BatchLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
