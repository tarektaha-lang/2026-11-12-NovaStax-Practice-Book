"""Mocked tests for status-only polling and parallel Batch downloads."""

from __future__ import annotations

import io
import json
import os
import time
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from google.genai.errors import APIError

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import audios  # noqa: E402
import pipeline  # noqa: E402
import transcripts  # noqa: E402
from src import api_logger, gemini_batch  # noqa: E402
from src.gemini_batch import (  # noqa: E402
    BATCH_POLL_ERROR_LIMIT,
    FINISH_DONE,
    FINISH_FAILED,
    FINISH_GONE,
    FULL_GET_SERVER_TIMEOUT,
    FULL_GET_TIMEOUT_MS,
    STATUS_FIELD_MASK,
    STATUS_TIMEOUT_MS,
    ApplyError,
    BatchChunk,
    BatchState,
    BatchStatus,
    finish_token,
    get_batch_job,
    get_batch_status,
    reset_status_mask,
    run_batch_jobs,
)
from tests.test_audios_batch_regression import (  # noqa: E402
    BOOK_ID,
    WAV_BY_ITEM,
    FakeClient as AudioFakeClient,
    fixture_book,
)
from tests.test_transcripts_batch import (  # noqa: E402
    FakeClient as TranscriptFakeClient,
    make_book,
    make_group,
    transcript_env,
)


def _job(name: str, state: str, *, dest: object | None = None, error: object | None = None):
    return SimpleNamespace(
        name=name,
        state=SimpleNamespace(name=state),
        dest=dest if dest is not None else SimpleNamespace(inlined_responses=None, file_name=None),
        error=error,
    )


def _chunk(label: str, items: list[str] | None = None, *, build_src=None) -> BatchChunk:
    keys = {item: "h" for item in (items or ["a"])}
    return BatchChunk(
        label=label,
        display_name=label,
        attempt=1,
        keys=keys,
        items=list(keys),
        build_src=build_src or (lambda: (["src"], None)),
    )


def _entry(name: str, display: str, keys: dict[str, str] | None = None) -> dict:
    return {
        "name": name,
        "display_name": display,
        "attempt": 1,
        "src_file_name": None,
        "keys": keys or {"a": "h"},
    }


class RecordingBatches:
    def __init__(self, parent: "RecordingClient") -> None:
        self.parent = parent

    def create(self, *, model, src, config=None):
        name = f"batches/c{len(self.parent.creates) + 1}"
        self.parent.creates.append(name)
        job = self.parent.jobs.get(name) or _job(name, self.parent.create_state)
        job.name = name
        self.parent.jobs[name] = job
        return SimpleNamespace(name=name)

    def get(self, *, name, config=None):
        thread = threading.current_thread().name
        options = (config or {}).get("http_options") or {}
        headers = options.get("headers") or {}
        record = {
            "thread": thread,
            "name": name,
            "masked": "X-Goog-FieldMask" in headers,
            "timeout": options.get("timeout"),
            "headers": dict(headers),
            "config": config,
        }
        self.parent.calls.append(record)
        if self.parent.raise_on_get is not None:
            error = self.parent.raise_on_get
            self.parent.raise_on_get = None if self.parent.raise_once else error
            if callable(error):
                raise error(record)
            raise error
        if name not in self.parent.jobs:
            err = RuntimeError("404 NOT_FOUND")
            err.code = 404
            raise err
        return self.parent.jobs[name]


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.creates: list[str] = []
        self.jobs: dict[str, object] = {}
        self.create_state = "JOB_STATE_SUCCEEDED"
        self.raise_on_get: object | None = None
        self.raise_once = True
        self.batches = RecordingBatches(self)
        self.files = SimpleNamespace(
            delete=lambda **kwargs: None,
            upload=lambda **kwargs: None,
            download=lambda **kwargs: b"",
        )


def _run(
    client: RecordingClient,
    state: BatchState,
    *,
    queue: list[BatchChunk] | None = None,
    adopted: list | None = None,
    prefetched: dict | None = None,
    parallel: int = 0,
    download_workers: int = 3,
    on_finished=None,
    on_failed=None,
    on_account=None,
    on_abandoned=None,
    on_requeue=None,
    attempt: int = 1,
) -> dict:
    accounted: list = []
    failed: list = []
    abandoned: list = []
    tokens: list[str] = []

    def default_finished(entry, chunk, status, elapsed):
        remote = status.job
        if remote is None:
            remote = get_batch_job(
                client, str(entry.get("name") or status.name), timeout_ms=FULL_GET_TIMEOUT_MS
            )
        token = finish_token(remote)
        tokens.append(token)
        result = gemini_batch.result_file_name(remote) if remote is not None else None
        if token != FINISH_DONE:
            return token, [], result
        return token, [(chunk.items[0], True, None, None)], result

    def default_account(chunk, outcomes):
        accounted.extend(outcomes)

    def default_failed(chunk, message):
        failed.append(message)

    def default_abandoned(chunk, message):
        abandoned.append(message)

    run_batch_jobs(
        client,
        "model",
        state,
        {"stage": "audios", "book_id": "b", "model": "model", "section": None, "update": False},
        list(queue or []),
        list(adopted or []),
        parallel,
        attempt,
        on_finished or default_finished,
        on_failed or default_failed,
        log=lambda _line: None,
        prefetched=prefetched,
        download_workers=download_workers,
        on_account=on_account or default_account,
        on_abandoned=on_abandoned or default_abandoned,
        on_requeue=on_requeue,
    )
    return {
        "accounted": accounted,
        "failed": failed,
        "abandoned": abandoned,
        "tokens": tokens,
    }


class DownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_status_mask(enabled=True)
        self._sleep = patch.object(gemini_batch.time, "sleep", lambda _seconds: None)
        self._sleep.start()
        self._folder = tempfile.TemporaryDirectory()
        self.state = BatchState(Path(self._folder.name) / "state.json")

    def tearDown(self) -> None:
        self._sleep.stop()
        self._folder.cleanup()
        reset_status_mask(enabled=gemini_batch.STATUS_MASK_ENABLED)

    def test_01_prefetched_succeeded_one_full_get_failed_zero_gets(self) -> None:
        client = RecordingClient()
        name = "batches/done"
        client.jobs[name] = _job(name, "JOB_STATE_SUCCEEDED")
        chunk = _chunk("done")
        entry = _entry(name, "done")
        self.state.save_entries({"stage": "audios"}, [entry])
        prefetched = {
            name: BatchStatus(name, "JOB_STATE_SUCCEEDED", None, True, None)
        }
        _run(
            client,
            self.state,
            adopted=[(entry, chunk)],
            prefetched=prefetched,
        )
        self.assertEqual(len(client.calls), 1)
        self.assertFalse(client.calls[0]["masked"])
        self.assertTrue(client.calls[0]["thread"].startswith("batch-dl"))

        client2 = RecordingClient()
        failed_name = "batches/fail"
        chunk2 = _chunk("fail")
        entry2 = _entry(failed_name, "fail")
        self.state.save_entries({"stage": "audios"}, [entry2])
        failed = []
        _run(
            client2,
            self.state,
            adopted=[(entry2, chunk2)],
            prefetched={
                failed_name: BatchStatus(
                    failed_name, "JOB_STATE_FAILED", None, True, None
                )
            },
            on_failed=lambda chunk, message: failed.append(message),
        )
        self.assertEqual(client2.calls, [])
        self.assertEqual(len(failed), 1)
        self.assertIsNone(self.state.load())

    def test_02_prefetched_running_then_status_get(self) -> None:
        client = RecordingClient()
        name = "batches/run"
        client.jobs[name] = _job(name, "JOB_STATE_SUCCEEDED")
        chunk = _chunk("run")
        entry = _entry(name, "run")
        self.state.save_entries({"stage": "audios"}, [entry])
        logs: list[str] = []
        run_batch_jobs(
            client,
            "model",
            self.state,
            {"stage": "audios"},
            [],
            [(entry, chunk)],
            0,
            1,
            lambda entry, chunk, status, elapsed: (
                finish_token(
                    status.job
                    or get_batch_job(client, name, timeout_ms=FULL_GET_TIMEOUT_MS)
                ),
                [],
                None,
            ),
            lambda chunk, message: None,
            log=logs.append,
            prefetched={name: BatchStatus(name, "JOB_STATE_RUNNING", None, True, None)},
            download_workers=1,
            on_account=lambda chunk, outcomes: None,
        )
        self.assertTrue(any("RUNNING" in line or "running" in line.lower() for line in logs) or client.calls)
        self.assertTrue(any(call["masked"] for call in client.calls))

    def test_03_failed_never_reaches_worker(self) -> None:
        client = RecordingClient()
        name = "batches/fail"
        worker_calls: list[str] = []

        def finished(entry, chunk, status, elapsed):
            worker_calls.append("hit")
            return FINISH_DONE, [], None

        chunk = _chunk("fail")
        entry = _entry(name, "fail")
        self.state.save_entries({"stage": "audios"}, [entry])
        _run(
            client,
            self.state,
            adopted=[(entry, chunk)],
            prefetched={name: BatchStatus(name, "JOB_STATE_FAILED", None, True, None)},
            on_finished=finished,
        )
        self.assertEqual(worker_calls, [])
        self.assertEqual(client.calls, [])

    def test_04_mask_fallback_once(self) -> None:
        logs: list[str] = []

        class NoConfig:
            def get(self, *, name):
                raise TypeError("unexpected keyword argument 'config'")

        reset_status_mask(enabled=True)
        status = get_batch_status(SimpleNamespace(batches=NoConfig()), "batches/x", log=logs.append)
        self.assertEqual(status.state, "UNKNOWN")
        self.assertEqual(
            logs,
            ["  status field mask off: status call does not accept config; using full batches.get"],
        )

        logs.clear()
        reset_status_mask(enabled=True)

        class BadMask:
            def get(self, *, name, config=None):
                raise APIError(
                    400,
                    {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "bad"}},
                )

        status = get_batch_status(SimpleNamespace(batches=BadMask()), "batches/x", log=logs.append)
        self.assertEqual(status.state, "UNKNOWN")
        self.assertEqual(len(logs), 1)
        self.assertIn("field mask rejected (400)", logs[0])

        logs.clear()
        reset_status_mask(enabled=True)

        class Unparseable:
            def get(self, *, name, config=None):
                return _job("batches/x", "weird")

        status = get_batch_status(
            SimpleNamespace(batches=Unparseable()), "batches/x", log=logs.append
        )
        self.assertEqual(status.state, "UNKNOWN")
        self.assertEqual(len(logs), 1)
        self.assertIn("unparseable masked reply", logs[0])

    def test_05_parse_typeerror_is_poll_error(self) -> None:
        reset_status_mask(enabled=True)

        class ParseBoom:
            def get(self, *, name, config=None):
                raise TypeError("while parsing JSON")

        with self.assertRaises(TypeError):
            get_batch_status(SimpleNamespace(batches=ParseBoom()), "batches/x")
        self.assertFalse(gemini_batch._MASK_DISABLED)

    def test_06_status_timeout_poll_cap(self) -> None:
        reset_status_mask(enabled=True)
        client = RecordingClient()
        name = "batches/t"
        client.jobs[name] = _job(name, "JOB_STATE_RUNNING")
        client.raise_on_get = httpx.TimeoutException("status timeout")
        client.raise_once = False
        chunk = _chunk("t")
        entry = _entry(name, "t")
        self.state.save_entries({"stage": "audios"}, [entry])
        with self.assertRaises(RuntimeError) as caught:
            _run(client, self.state, adopted=[(entry, chunk)])
        self.assertIn("poll failed 30 times", str(caught.exception))
        self.assertFalse(gemini_batch._MASK_DISABLED)
        self.assertGreaterEqual(len(client.calls), BATCH_POLL_ERROR_LIMIT)

    def test_07_http_options(self) -> None:
        client = RecordingClient()
        name = "batches/opt"
        client.jobs[name] = _job(name, "JOB_STATE_SUCCEEDED")
        get_batch_status(client, name)
        status_call = client.calls[-1]
        self.assertTrue(status_call["masked"])
        self.assertEqual(status_call["timeout"], STATUS_TIMEOUT_MS)
        self.assertEqual(status_call["headers"].get("X-Goog-FieldMask"), STATUS_FIELD_MASK)
        self.assertNotIn("X-Server-Timeout", status_call["headers"])

        get_batch_job(client, name, timeout_ms=FULL_GET_TIMEOUT_MS)
        full = client.calls[-1]
        self.assertFalse(full["masked"])
        self.assertEqual(full["timeout"], FULL_GET_TIMEOUT_MS)
        self.assertEqual(full["headers"].get("X-Server-Timeout"), FULL_GET_SERVER_TIMEOUT)

    def test_08_parallel_release_order(self) -> None:
        client = RecordingClient()
        names = ["batches/1", "batches/2", "batches/3"]
        gates = {name: threading.Event() for name in names}
        started = {name: threading.Event() for name in names}
        accounted: list[str] = []
        order: list[str] = []
        real_remove = BatchState.remove_entry
        real_delete = gemini_batch.delete_files

        def finished(entry, chunk, status, elapsed):
            name = str(entry["name"])
            started[name].set()
            gates[name].wait(timeout=2)
            return FINISH_DONE, [(name, True, None, None)], None

        def account(chunk, outcomes):
            accounted.append(str(chunk.label))

        def wrapping_remove(self, display_name):
            order.append(f"remove:{display_name}")
            return real_remove(self, display_name)

        def wrapping_delete(client_arg, *names_arg, log=print):
            order.append("delete")
            return real_delete(client_arg, *names_arg, log=log)

        adopted = []
        for name in names:
            client.jobs[name] = _job(name, "JOB_STATE_SUCCEEDED")
            chunk = _chunk(name)
            entry = _entry(name, name, {name: "h"})
            adopted.append((entry, chunk))
        self.state.save_entries({"stage": "audios"}, [row[0] for row in adopted])
        prefetched = {
            name: BatchStatus(name, "JOB_STATE_SUCCEEDED", None, True, None)
            for name in names
        }
        with (
            patch.object(BatchState, "remove_entry", wrapping_remove),
            patch.object(gemini_batch, "delete_files", wrapping_delete),
        ):
            thread = threading.Thread(
                target=lambda: _run(
                    client,
                    self.state,
                    adopted=adopted,
                    prefetched=prefetched,
                    download_workers=3,
                    on_finished=finished,
                    on_account=account,
                )
            )
            thread.start()
            for name in names:
                self.assertTrue(started[name].wait(timeout=2))
            for name in ("batches/3", "batches/1", "batches/2"):
                before = len(accounted)
                gates[name].set()
                deadline = time.time() + 2
                while len(accounted) == before and time.time() < deadline:
                    time.sleep(0.01)
            thread.join(timeout=5)
        self.assertEqual(accounted, ["batches/3", "batches/1", "batches/2"])
        removes = [item for item in order if item.startswith("remove:")]
        self.assertEqual(len(removes), 3)
        for index, name in enumerate(("batches/3", "batches/1", "batches/2")):
            remove_at = order.index(f"remove:{name}")
            delete_at = min(
                i for i, item in enumerate(order) if item == "delete" and i > remove_at
            )
            self.assertLess(remove_at, delete_at)

    def test_09_thread_rules(self) -> None:
        client = RecordingClient()
        name = "batches/th"
        client.jobs[name] = _job(name, "JOB_STATE_SUCCEEDED")
        main = threading.current_thread().name
        seen = {"account": None, "remove": None, "get": None}
        real_remove = BatchState.remove_entry

        def finished(entry, chunk, status, elapsed):
            seen["get"] = threading.current_thread().name
            remote = get_batch_job(client, name, timeout_ms=FULL_GET_TIMEOUT_MS)
            return finish_token(remote), [("a", True, None, None)], None

        def account(chunk, outcomes):
            seen["account"] = threading.current_thread().name

        def wrapping_remove(self, display_name):
            seen["remove"] = threading.current_thread().name
            return real_remove(self, display_name)

        chunk = _chunk("th")
        entry = _entry(name, "th")
        self.state.save_entries({"stage": "audios"}, [entry])
        with patch.object(BatchState, "remove_entry", wrapping_remove):
            _run(
                client,
                self.state,
                adopted=[(entry, chunk)],
                prefetched={name: BatchStatus(name, "JOB_STATE_SUCCEEDED", None, True, None)},
                on_finished=finished,
                on_account=account,
            )
        self.assertEqual(seen["account"], main)
        self.assertEqual(seen["remove"], main)
        self.assertTrue(str(seen["get"]).startswith("batch-dl"))

    def test_10_partial_exception_accounts_then_retries_missing(self) -> None:
        client = RecordingClient()
        name = "batches/p"
        client.jobs[name] = _job(name, "JOB_STATE_SUCCEEDED")
        accounted: list = []
        failed: list = []

        def finished(entry, chunk, status, elapsed):
            raise ApplyError("boom", [("a", True, None, None)])

        chunk = _chunk("p", ["a", "b", "c"])
        entry = _entry(name, "p", {"a": "h", "b": "h", "c": "h"})
        self.state.save_entries({"stage": "audios"}, [entry])
        _run(
            client,
            self.state,
            adopted=[(entry, chunk)],
            prefetched={name: BatchStatus(name, "JOB_STATE_SUCCEEDED", None, True, None)},
            on_finished=finished,
            on_account=lambda chunk, outcomes: accounted.extend(outcomes),
            on_failed=lambda chunk, message: failed.append(chunk.items),
        )
        self.assertEqual(accounted, [("a", True, None, None)])
        self.assertEqual(failed, [["a", "b", "c"]])
        self.assertIsNone(self.state.load())

    def test_12_first_timeout_keeps_entry(self) -> None:
        client = RecordingClient()
        name = "batches/to"
        client.jobs[name] = _job(name, "JOB_STATE_SUCCEEDED")
        hits = {"n": 0}
        failed: list = []

        def finished(entry, chunk, status, elapsed):
            hits["n"] += 1
            if hits["n"] == 1:
                raise TimeoutError("full get timeout")
            return FINISH_DONE, [("a", True, None, None)], None

        chunk = _chunk("to")
        entry = _entry(name, "to")
        self.state.save_entries({"stage": "audios"}, [entry])
        _run(
            client,
            self.state,
            adopted=[(entry, chunk)],
            prefetched={name: BatchStatus(name, "JOB_STATE_SUCCEEDED", None, True, None)},
            on_finished=finished,
            on_failed=lambda chunk, message: failed.append(message),
        )
        self.assertEqual(hits["n"], 2)
        self.assertEqual(failed, [])

    def test_13_third_timeout_abandons(self) -> None:
        client = RecordingClient()
        name = "batches/to3"
        client.jobs[name] = _job(name, "JOB_STATE_SUCCEEDED")
        abandoned: list = []
        failed: list = []
        created: list = []

        def finished(entry, chunk, status, elapsed):
            raise TimeoutError("full get timeout")

        chunk = _chunk("to3")
        entry = _entry(name, "to3")
        self.state.save_entries({"stage": "audios"}, [entry])
        _run(
            client,
            self.state,
            adopted=[(entry, chunk)],
            prefetched={name: BatchStatus(name, "JOB_STATE_SUCCEEDED", None, True, None)},
            on_finished=finished,
            on_failed=lambda chunk, message: failed.append(message),
            on_abandoned=lambda chunk, message: abandoned.append(message),
            on_requeue=lambda chunk: created.extend(["no"]) or [],
        )
        self.assertEqual(len(abandoned), 1)
        self.assertEqual(failed, [])
        self.assertEqual(created, [])
        data = self.state.load()
        self.assertEqual(len(gemini_batch.entries(data)), 1)

    def test_15_known_done_does_not_block_capacity(self) -> None:
        client = RecordingClient()
        gate = threading.Event()
        started = threading.Event()
        created_during: list[str] = []

        def finished(entry, chunk, status, elapsed):
            name = str(entry.get("name") or "")
            if name == "batches/a":
                started.set()
                gate.wait(timeout=2)
            remote = status.job or get_batch_job(
                client, name, timeout_ms=FULL_GET_TIMEOUT_MS
            )
            return finish_token(remote), [], None

        name_a = "batches/a"
        client.jobs[name_a] = _job(name_a, "JOB_STATE_SUCCEEDED")
        client.create_state = "JOB_STATE_SUCCEEDED"
        chunk_a = _chunk("a")
        entry_a = _entry(name_a, "a")
        leftover = _chunk("b")
        self.state.save_entries({"stage": "audios"}, [entry_a])

        original_create = client.batches.create
        leftover_created = threading.Event()

        def wrapped_create(*, model, src, config=None):
            created_during.append("create")
            leftover_created.set()
            return original_create(model=model, src=src, config=config)

        client.batches.create = wrapped_create  # type: ignore[method-assign]
        thread = threading.Thread(
            target=lambda: _run(
                client,
                self.state,
                queue=[leftover],
                adopted=[(entry_a, chunk_a)],
                prefetched={
                    name_a: BatchStatus(name_a, "JOB_STATE_SUCCEEDED", None, True, None)
                },
                parallel=1,
                download_workers=1,
                on_finished=finished,
            )
        )
        thread.start()
        self.assertTrue(started.wait(timeout=2))
        self.assertTrue(leftover_created.wait(timeout=2))
        self.assertTrue(created_during)
        gate.set()
        thread.join(timeout=5)

        reset_status_mask(enabled=False)
        client2 = RecordingClient()
        gate2 = threading.Event()
        started2 = threading.Event()
        created2: list[str] = []
        name_u = "batches/u"

        def finished2(entry, chunk, status, elapsed):
            started2.set()
            gate2.wait(timeout=2)
            remote = get_batch_job(
                client2, str(entry.get("name") or name_u), timeout_ms=FULL_GET_TIMEOUT_MS
            )
            return finish_token(remote), [], None

        leftover2 = threading.Event()

        def create2(*, model, src, config=None):
            created2.append("create")
            leftover2.set()
            return RecordingBatches.create(client2.batches, model=model, src=src, config=config)

        client2.jobs[name_u] = _job(name_u, "JOB_STATE_SUCCEEDED")
        client2.batches.create = create2  # type: ignore[method-assign]
        entry_u = _entry(name_u, "u")
        self.state.save_entries({"stage": "audios"}, [entry_u])
        thread2 = threading.Thread(
            target=lambda: _run(
                client2,
                self.state,
                queue=[_chunk("v")],
                adopted=[(entry_u, _chunk("u"))],
                parallel=1,
                download_workers=1,
                on_finished=finished2,
            )
        )
        thread2.start()
        self.assertTrue(started2.wait(timeout=2))
        self.assertFalse(leftover2.wait(timeout=0.2))
        self.assertEqual(created2, [])
        gate2.set()
        thread2.join(timeout=5)

    def test_17a_run_batch_jobs_keyboardinterrupt(self) -> None:
        client = RecordingClient()
        name = "batches/ki"
        client.raise_on_get = KeyboardInterrupt()
        client.raise_once = False
        chunk = _chunk("ki")
        entry = _entry(name, "ki")
        self.state.save_entries({"stage": "audios"}, [entry])
        exits: list[int] = []
        with patch.object(os, "_exit", lambda code: exits.append(code)):
            with self.assertRaises(KeyboardInterrupt):
                _run(
                    client,
                    self.state,
                    adopted=[(entry, chunk)],
                    prefetched={
                        name: BatchStatus(name, "JOB_STATE_SUCCEEDED", None, True, None)
                    },
                )
        self.assertEqual(exits, [])
        self.assertEqual(len(gemini_batch.entries(self.state.load())), 1)

    def test_23_non_adopted_gone(self) -> None:
        reset_status_mask(enabled=False)
        client = RecordingClient()
        failed: list = []
        original_create = client.batches.create

        def create(*, model, src, config=None):
            name = f"batches/c{len(client.creates) + 1}"
            client.creates.append(name)
            return SimpleNamespace(name=name)

        client.batches.create = create  # type: ignore[method-assign]

        def finished(entry, chunk, status, elapsed):
            remote = get_batch_job(
                client, str(entry.get("name")), timeout_ms=FULL_GET_TIMEOUT_MS
            )
            if remote is None:
                return FINISH_GONE, [], None
            return finish_token(remote), [], None

        chunk = _chunk("gone")
        _run(
            client,
            self.state,
            queue=[chunk],
            on_finished=finished,
            on_failed=lambda chunk, message: failed.append(message),
        )
        client.batches.create = original_create  # type: ignore[method-assign]
        self.assertEqual(failed, ["gone"])
        self.assertIsNone(self.state.load())
        full_gets = [call for call in client.calls if not call["masked"]]
        self.assertEqual(len(full_gets), 1)

    def test_24_worker_running_stays_live(self) -> None:
        client = RecordingClient()
        name = "batches/run2"
        states = iter(["JOB_STATE_RUNNING", "JOB_STATE_SUCCEEDED"])

        def finished(entry, chunk, status, elapsed):
            state = next(states)
            job = _job(name, state)
            return finish_token(job), [] if state != "JOB_STATE_SUCCEEDED" else [("a", True, None, None)], None

        chunk = _chunk("run2")
        entry = _entry(name, "run2")
        self.state.save_entries({"stage": "audios"}, [entry])
        removed: list[str] = []
        real_remove = BatchState.remove_entry

        def wrapping_remove(self, display_name):
            removed.append(display_name)
            return real_remove(self, display_name)

        with patch.object(BatchState, "remove_entry", wrapping_remove):
            _run(
                client,
                self.state,
                adopted=[(entry, chunk)],
                prefetched={
                    name: BatchStatus(name, "JOB_STATE_SUCCEEDED", None, True, None)
                },
                on_finished=finished,
            )
        self.assertEqual(removed, ["run2"])

    def test_25_d5_mask_ignored_reused(self) -> None:
        dest = SimpleNamespace(inlined_responses=[SimpleNamespace()], file_name=None)
        held = _job("batches/full", "JOB_STATE_SUCCEEDED", dest=dest)
        client = RecordingClient()
        client.jobs["batches/full"] = held
        status = get_batch_status(client, "batches/full")
        self.assertFalse(status.light)
        self.assertIs(status.job, held)
        before = len(client.calls)
        chunk = _chunk("full")
        entry = _entry("batches/full", "full")
        self.state.save_entries({"stage": "audios"}, [entry])

        def finished(entry, chunk, status, elapsed):
            self.assertIs(status.job, held)
            return FINISH_DONE, [], None

        _run(
            client,
            self.state,
            adopted=[(entry, chunk)],
            prefetched={"batches/full": status},
            on_finished=finished,
        )
        self.assertEqual(len(client.calls), before)

    def test_26_probe_false_skips_mask(self) -> None:
        reset_status_mask(enabled=False)
        logs: list[str] = []
        client = RecordingClient()
        client.jobs["batches/x"] = _job("batches/x", "JOB_STATE_SUCCEEDED")
        status = get_batch_status(client, "batches/x", log=logs.append)
        self.assertEqual(status.state, "UNKNOWN")
        self.assertEqual(client.calls, [])
        self.assertEqual(
            logs, ["  status field mask off: probe; using full batches.get"]
        )

    def test_28_worker_keyboardinterrupt_keeps_entry(self) -> None:
        client = RecordingClient()
        name = "batches/wki"
        failed: list = []

        def finished(entry, chunk, status, elapsed):
            raise KeyboardInterrupt

        chunk = _chunk("wki")
        entry = _entry(name, "wki")
        self.state.save_entries({"stage": "audios"}, [entry])
        with self.assertRaises(KeyboardInterrupt):
            _run(
                client,
                self.state,
                adopted=[(entry, chunk)],
                prefetched={
                    name: BatchStatus(name, "JOB_STATE_SUCCEEDED", None, True, None)
                },
                on_finished=finished,
                on_failed=lambda chunk, message: failed.append(message),
            )
        self.assertEqual(failed, [])
        self.assertEqual(len(gemini_batch.entries(self.state.load())), 1)

    def test_29_config_reject_not_cached(self) -> None:
        class Accepts:
            def get(self, *, name, config=None):
                return _job(name, "JOB_STATE_SUCCEEDED")

        class Rejects:
            def get(self, *, name):
                return _job(name, "JOB_STATE_SUCCEEDED")

        get_batch_job(SimpleNamespace(batches=Accepts()), "batches/a", timeout_ms=1)
        job = get_batch_job(SimpleNamespace(batches=Rejects()), "batches/b", timeout_ms=1)
        self.assertEqual(job.name, "batches/b")

    def test_32_finish_tokens(self) -> None:
        self.assertEqual(finish_token(_job("n", "JOB_STATE_SUCCEEDED")), FINISH_DONE)
        self.assertEqual(
            finish_token(_job("n", "JOB_STATE_PARTIALLY_SUCCEEDED")), FINISH_DONE
        )
        self.assertEqual(finish_token(_job("n", "JOB_STATE_FAILED")), FINISH_FAILED)
        self.assertEqual(finish_token(_job("n", "JOB_STATE_CANCELLED")), FINISH_FAILED)
        self.assertEqual(finish_token(_job("n", "JOB_STATE_EXPIRED")), FINISH_FAILED)
        self.assertEqual(finish_token(None), FINISH_GONE)
        self.assertEqual(finish_token(_job("n", "JOB_STATE_RUNNING")), "JOB_STATE_RUNNING")
        inlined: list[str] = []

        def finished(entry, chunk, status, elapsed):
            remote = _job("n", "JOB_STATE_FAILED")
            token = finish_token(remote)
            if token == FINISH_DONE:
                inlined.append("called")
            return token, [], None

        client = RecordingClient()
        name = "batches/tok"
        chunk = _chunk("tok")
        entry = _entry(name, "tok")
        self.state.save_entries({"stage": "audios"}, [entry])
        _run(
            client,
            self.state,
            adopted=[(entry, chunk)],
            prefetched={name: BatchStatus(name, "JOB_STATE_SUCCEEDED", None, True, None)},
            on_finished=finished,
        )
        self.assertEqual(inlined, [])


class IntegrationDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_status_mask(enabled=True)
        self._sleep = patch.object(gemini_batch.time, "sleep", lambda _seconds: None)
        self._sleep.start()

    def tearDown(self) -> None:
        self._sleep.stop()
        reset_status_mask(enabled=gemini_batch.STATUS_MASK_ENABLED)

    def test_11_partial_under_update(self) -> None:
        fake = TranscriptFakeClient()
        book = make_book([make_group("g0"), make_group("g1"), make_group("g2")])
        saved = {"n": 0}
        real_save = transcripts.BookStore.save

        def wrapping_save(self, page_key, group_id, result, generation):
            real_save(self, page_key, group_id, result, generation)
            saved["n"] += 1
            if saved["n"] == 1:
                raise RuntimeError("boom after first row")

        with transcript_env(book, fake) as env, patch.object(
            transcripts.BookStore, "save", wrapping_save
        ):
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", True, batch=True
            )
            stored = json.loads(env.book_path.read_text(encoding="utf-8"))
        written = [g["id"] for g in stored["pages"]["1"] if g.get("transcript")]
        self.assertIn("g0", written)
        retried = []
        for call in fake.creates[1:]:
            src = call["src"]
            for request in src:
                retried.append((request.metadata or {}).get("item"))
        self.assertNotIn("1_g0", retried)

    def test_14_fallback_two_jobs_one_get_each(self) -> None:
        reset_status_mask(enabled=False)
        fake = TranscriptFakeClient()
        book = make_book([make_group("g0"), make_group("g1")])
        with transcript_env(book, fake), patch("src.gemini_batch.BATCH_CHUNK", 1):
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
        counts = {name: fake.gets.count(name) for name in set(fake.gets)}
        self.assertTrue(counts)
        self.assertTrue(all(count == 1 for count in counts.values()))

    def test_16_two_workers_same_page(self) -> None:
        fake = AudioFakeClient()
        original_create = fake.batches.create

        def create(*, model, src, config=None):
            original_create(model=model, src=src, config=config)
            name = f"batches/audio-{len(fake.batches.create_calls)}"
            job = fake.batches.jobs.pop("batches/golden-audios-job", None)
            if job is not None:
                job.name = name
                fake.batches.jobs[name] = job
            return SimpleNamespace(name=name)

        fake.batches.create = create
        book = fixture_book()
        with tempfile.TemporaryDirectory(dir=str(audios.PROJECT_ROOT)) as folder:
            root = Path(folder)
            book_dir = root / BOOK_ID
            book_dir.mkdir()
            book_path = book_dir / f"{BOOK_ID}.json"
            book_path.write_text(
                json.dumps(book, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            with (
                patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False),
                patch.object(audios, "OUTPUT_DIR", root),
                patch.object(
                    audios, "working_json", lambda book_id: root / book_id / f"{book_id}.json"
                ),
                patch.object(audios.genai, "Client", return_value=fake),
                patch.object(audios.os, "_exit", side_effect=SystemExit(130)),
                patch.object(
                    api_logger.ApiLogger,
                    "_compute_ntp_offset",
                    staticmethod(lambda: 0.0),
                ),
                patch.object(audios.time, "sleep", lambda _seconds: None),
                patch("src.gemini_batch.BATCH_CHUNK", 1),
            ):
                audios.build_audios(
                    BOOK_ID,
                    "1.2",
                    audios.DEFAULT_MODEL,
                    False,
                    batch=True,
                    download_workers=2,
                )
            stored = json.loads(book_path.read_text(encoding="utf-8"))
        ids = [row["id"] for row in stored["pages"]["0001"][0].get("audio") or []]
        ids += [row["id"] for row in stored["pages"]["0001"][1].get("audio") or []]
        self.assertGreaterEqual(len(ids), 2)

    def test_17b_lock_held_at_os_exit(self) -> None:
        fake = TranscriptFakeClient()
        fake.get_interrupt = True
        book = make_book([make_group("g0"), make_group("g1")])
        held = {"ok": False}
        locks: list = []
        real_acquire = gemini_batch.BatchLock.acquire

        def tracking_acquire(self):
            locks.append(self)
            return real_acquire(self)

        def fake_exit(code):
            lock = locks[-1]
            held["ok"] = lock._handle is not None
            raise SystemExit(code)

        with (
            transcript_env(book, fake) as env,
            patch.object(gemini_batch.BatchLock, "acquire", tracking_acquire),
            patch.object(transcripts.os, "_exit", fake_exit),
            patch("src.gemini_batch.BATCH_CHUNK", 1),
        ):
            with self.assertRaises(SystemExit) as caught:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            self.assertEqual(caught.exception.code, 130)
            self.assertTrue(held["ok"])
            self.assertEqual(len(gemini_batch.entries(json.loads(
                (env.book_dir / "00_transcripts_batch_state.json").read_text()
            ))), 2)
            fake.get_interrupt = False
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )

    def test_18_ctrl_c_during_resume_keeps_named_jobs(self) -> None:
        fake = TranscriptFakeClient()
        book = make_book([make_group("g0"), make_group("g1")])
        with transcript_env(book, fake) as env, patch("src.gemini_batch.BATCH_CHUNK", 1):
            fake.get_interrupt = True
            try:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            except SystemExit:
                pass
            data = json.loads((env.book_dir / "00_transcripts_batch_state.json").read_text())
            rows = gemini_batch.entries(data)
            self.assertTrue(all(row.get("name") for row in rows))
            reset_status_mask(enabled=True)

            def boom(*, name, config=None):
                raise KeyboardInterrupt

            fake.batches.get = boom
            try:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            except SystemExit:
                pass
            again = json.loads((env.book_dir / "00_transcripts_batch_state.json").read_text())
            self.assertEqual(len(gemini_batch.entries(again)), len(rows))

    def test_19_resume_prints_status_only(self) -> None:
        fake = TranscriptFakeClient()
        book = make_book([make_group("g0")])
        buf = io.StringIO()
        with transcript_env(book, fake), redirect_stdout(buf):
            fake.get_interrupt = True
            try:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            except SystemExit:
                pass
            fake.get_interrupt = False
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
        self.assertIn("adopted job ", buf.getvalue())
        self.assertIn("(status only)", buf.getvalue())

    def test_20_download_workers_one_is_serial(self) -> None:
        fake = TranscriptFakeClient()
        book = make_book([make_group("g0"), make_group("g1")])
        live = {"n": 0, "max": 0}
        original = fake.batches.get
        lock = threading.Lock()

        def get(*, name, config=None):
            with lock:
                live["n"] += 1
                live["max"] = max(live["max"], live["n"])
            try:
                try:
                    return original(name=name, config=config)
                except TypeError:
                    return original(name=name)
            finally:
                with lock:
                    live["n"] -= 1

        fake.batches.get = get
        with transcript_env(book, fake), patch("src.gemini_batch.BATCH_CHUNK", 1):
            transcripts.build_transcripts(
                BOOK_ID,
                "1.2",
                transcripts.DEFAULT_MODEL,
                "medium",
                False,
                batch=True,
                download_workers=1,
            )
        self.assertLessEqual(live["max"], 1)

    def test_21_cli_and_stage_command(self) -> None:
        with patch.object(sys, "argv", ["audios.py", BOOK_ID, "--download-workers", "0"]):
            with self.assertRaises(SystemExit):
                audios.main()
        with patch.object(sys, "argv", ["transcripts.py", BOOK_ID, "--download-workers", "0"]):
            with self.assertRaises(SystemExit):
                transcripts.main()
        with patch.object(
            sys, "argv", ["pipeline.py", BOOK_ID, "--download-workers", "0"]
        ):
            with self.assertRaises(SystemExit):
                pipeline.main()
        kwargs = dict(
            section=None,
            update=False,
            model=None,
            thinking_level=None,
            workers=None,
            tts_model=None,
        )
        for name in ("transcripts", "audios"):
            command = pipeline.stage_command(
                pipeline.STAGES_BY_NAME[name], BOOK_ID, **kwargs, download_workers=2
            )
            self.assertIn("--download-workers", command)
            self.assertIn("2", command)
        for name in ("ar_transcripts", "ar_audios"):
            stage = next(row for row in pipeline.TRANSLATE_STAGES if row.name == name)
            command = pipeline.stage_command(
                stage, "mathematics_ar_eg07_t1", **kwargs, download_workers=2
            )
            self.assertIn("--download-workers", command)
        groups = pipeline.stage_command(
            pipeline.STAGES_BY_NAME["groups"], BOOK_ID, **kwargs, download_workers=2
        )
        self.assertNotIn("--download-workers", groups)
        omitted = pipeline.stage_command(
            pipeline.STAGES_BY_NAME["transcripts"], BOOK_ID, **kwargs
        )
        self.assertNotIn("--download-workers", omitted)

    def test_22_d2_orphan_wav_is_pending(self) -> None:
        book = fixture_book()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            taps = root / "taps"
            md5 = book["pages"]["0001"][0]["taps"][0]["md5"]
            wav_path = taps / md5 / "01.wav"
            wav_path.parent.mkdir(parents=True)
            wav_path.write_bytes(WAV_BY_ITEM["0001_group_1"])
            jobs, skipped, _silent = audios.collect_jobs(
                BOOK_ID, book, taps, "1.2", False
            )
            self.assertTrue(any(job.item == "0001_group_1" for job in jobs))
            self.assertEqual(skipped, 0)

        fake = AudioFakeClient()
        with tempfile.TemporaryDirectory(dir=str(audios.PROJECT_ROOT)) as folder:
            root = Path(folder)
            book_dir = root / BOOK_ID
            book_dir.mkdir()
            book_path = book_dir / f"{BOOK_ID}.json"
            book_path.write_text(
                json.dumps(book, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            md5 = book["pages"]["0001"][0]["taps"][0]["md5"]
            wav_path = book_dir / "14_taps" / md5 / "01.wav"
            wav_path.parent.mkdir(parents=True)
            wav_path.write_bytes(WAV_BY_ITEM["0001_group_1"])
            buf = io.StringIO()
            with (
                patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False),
                patch.object(audios, "OUTPUT_DIR", root),
                patch.object(
                    audios, "working_json", lambda book_id: root / book_id / f"{book_id}.json"
                ),
                patch.object(audios.genai, "Client", return_value=fake),
                patch.object(
                    api_logger.ApiLogger,
                    "_compute_ntp_offset",
                    staticmethod(lambda: 0.0),
                ),
                patch.object(audios.time, "sleep", lambda _seconds: None),
                redirect_stdout(buf),
            ):
                audios.build_audios(
                    BOOK_ID, "1.2", audios.DEFAULT_MODEL, False, batch=True
                )
            stored = json.loads(book_path.read_text(encoding="utf-8"))
        self.assertTrue(stored["pages"]["0001"][0].get("audio"))
        self.assertNotIn("FAILED adopted key missing", buf.getvalue())

    def test_27_prefetched_attempt_one_only(self) -> None:
        seen: list = []
        real = gemini_batch.run_batch_jobs

        def spy(*args, **kwargs):
            seen.append(
                {
                    "attempt": args[7] if len(args) > 7 else kwargs.get("attempt"),
                    "prefetched": kwargs.get("prefetched"),
                }
            )
            return real(*args, **kwargs)

        fake = TranscriptFakeClient()
        fake.create_state = "JOB_STATE_FAILED"
        book = make_book([make_group("g0")])
        with (
            transcript_env(book, fake),
            patch.object(gemini_batch, "run_batch_jobs", spy),
            patch.object(transcripts, "run_batch_jobs", spy),
        ):
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
        self.assertGreaterEqual(len(seen), 2)
        self.assertIsNotNone(seen[0]["prefetched"])
        later = [row for row in seen if row["attempt"] and row["attempt"] > 1]
        self.assertTrue(later)
        self.assertEqual(later[0]["prefetched"], {})

        seen.clear()
        fake2 = TranscriptFakeClient()
        book2 = make_book([make_group("g0")])
        with transcript_env(book2, fake2):
            fake2.get_interrupt = True
            try:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            except SystemExit:
                pass
            fake2.get_interrupt = False
            with (
                patch.object(gemini_batch, "run_batch_jobs", spy),
                patch.object(transcripts, "run_batch_jobs", spy),
            ):
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=False
                )
        self.assertEqual(seen[0]["attempt"], 1)
        self.assertIsNotNone(seen[0]["prefetched"])

    def test_30_no_batch_abandoned_skips_unary(self) -> None:
        fake = TranscriptFakeClient()
        book = make_book([make_group("g0")])

        def always_timeout(name, config=None):
            raise httpx.TimeoutException("full get timeout")

        with transcript_env(book, fake):
            fake.get_interrupt = True
            try:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            except SystemExit:
                pass
            fake.get_interrupt = False
            fake.batches.get = always_timeout
            _, failed = transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=False
            )
        self.assertEqual(len(fake.unary), 0)
        self.assertGreaterEqual(failed, 1)

    def test_31_adopted_failed_requeues_attempt_one(self) -> None:
        fake = TranscriptFakeClient()
        book = make_book([make_group("g0")])
        with transcript_env(book, fake) as env:
            fake.get_interrupt = True
            try:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            except SystemExit:
                pass
            saved = json.loads(
                (env.book_dir / "00_transcripts_batch_state.json").read_text()
            )
            name = gemini_batch.entries(saved)[0]["name"]
            fake.get_interrupt = False
            fake.remote[name].state = SimpleNamespace(name="JOB_STATE_EXPIRED")
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
        self.assertEqual(len(fake.creates), 2)
        self.assertNotIn("-a2-", fake.creates[1]["config"]["display_name"])

        fake2 = TranscriptFakeClient()
        book2 = make_book([make_group("g0")])
        with transcript_env(book2, fake2) as env:
            fake2.get_interrupt = True
            try:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            except SystemExit:
                pass
            saved = json.loads(
                (env.book_dir / "00_transcripts_batch_state.json").read_text()
            )
            name = gemini_batch.entries(saved)[0]["name"]
            fake2.get_interrupt = False
            fake2.remote[name].state = SimpleNamespace(name="JOB_STATE_EXPIRED")
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=False
            )
        self.assertEqual(len(fake2.creates), 1)
        self.assertEqual(len(fake2.unary), 1)


if __name__ == "__main__":
    unittest.main()
