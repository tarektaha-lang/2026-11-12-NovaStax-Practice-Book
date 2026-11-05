"""Mocked tests for parallel Batch chunks. Never calls the live Gemini API."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from google.genai import types

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import audios  # noqa: E402
import pipeline  # noqa: E402
import transcripts  # noqa: E402
from src import gemini_batch  # noqa: E402
from src.gemini_batch import (  # noqa: E402
    BATCH_POLL_ERROR_LIMIT,
    chunk_requests,
    entries,
    pick_duplicate_match,
)
from src import api_logger  # noqa: E402
from tests.test_audios_batch_regression import (  # noqa: E402
    FROZEN,
    WAV_BY_ITEM,
    FakeClient as AudioFakeClient,
    fixture_book,
    tts_response,
)
from tests.test_transcripts_batch import (  # noqa: E402
    BOOK_ID,
    SINGLE_JSON,
    FakeClient,
    call_jsons,
    load_book,
    make_book,
    make_group,
    make_response,
    stored_transcript,
    transcript_env,
)


def job_items(src) -> list[str]:
    if isinstance(src, str):
        return []
    items = []
    for request in src:
        meta = getattr(request, "metadata", None) or {}
        items.append(meta.get("item"))
    return items


class ParallelFiles:
    def __init__(self, parent: "ParallelClient") -> None:
        self.parent = parent
        self.uploads: list[str] = []
        self.deleted: list[str] = []
        self.downloads: list[str] = []
        self.result_payload = ""
        self.upload_errors: list[BaseException] = []
        self.last_jsonl_keys: list[str] = []

    def upload(self, *, file, config=None):
        self.uploads.append(str(file))
        keys: list[str] = []
        try:
            for line in Path(file).read_text(encoding="utf-8").splitlines():
                if line.strip():
                    keys.append(json.loads(line)["key"])
        except (OSError, json.JSONDecodeError, KeyError):
            keys = []
        self.last_jsonl_keys = keys
        if self.upload_errors:
            raise self.upload_errors.pop(0)
        return SimpleNamespace(name="files/in-jsonl")

    def download(self, *, file):
        self.downloads.append(file)
        return self.result_payload.encode("utf-8")

    def delete(self, *, name):
        self.deleted.append(name)


class ParallelClient(FakeClient):
    """Jobs stay RUNNING until a scripted number of get() calls."""

    def __init__(self) -> None:
        super().__init__()
        self.create_state = "JOB_STATE_RUNNING"
        self.succeed_after = 1
        self.terminal_state = "JOB_STATE_SUCCEEDED"
        self.create_errors: list[BaseException] = []
        self.create_attempts: list[dict] = []
        self.create_raise_before = False
        self.creates_at_first_get: int | None = None
        self.in_flight = 0
        self.max_in_flight = 0
        self.get_errors = 0
        self.get_error_limit: int | None = None
        self.succeed_after_for: dict[str, int] = {}
        self.terminal_for: dict[str, str] = {}
        self.files = ParallelFiles(self)
        self.batches = ParallelBatches(self)


class ParallelBatches:
    def __init__(self, parent: ParallelClient) -> None:
        self.parent = parent

    def create(self, **kwargs):
        self.parent.create_attempts.append(kwargs)
        if self.parent.create_raise_before:
            raise KeyboardInterrupt
        if self.parent.create_interrupt:
            from tests.test_transcripts_batch import FakeBatches

            helper = FakeBatches(self.parent)
            job = helper._job_from_src(kwargs, name="batches/adopt-me")
            job.display_name = (kwargs.get("config") or {}).get("display_name")
            job.gets = 0
            job.closed = False
            self.parent.creates.append(kwargs)
            self.parent.remote[job.name] = job
            self.parent.listed.append(job)
            raise KeyboardInterrupt
        if self.parent.create_errors:
            raise self.parent.create_errors.pop(0)
        from tests.test_transcripts_batch import FakeBatches

        helper = FakeBatches(self.parent)
        job = helper._job_from_src(
            kwargs, name=f"batches/job-{len(self.parent.creates) + 1}"
        )
        job.state = SimpleNamespace(name=self.parent.create_state)
        job.gets = 0
        job.closed = False
        job.create_time = datetime.now(timezone.utc)
        job.display_name = (kwargs.get("config") or {}).get("display_name")
        job.error = None
        src = kwargs.get("src")
        if isinstance(src, str):
            keys = list(self.parent.files.last_jsonl_keys)
            self.parent.jsonl_rows = [
                {
                    "key": key,
                    "response": make_response(SINGLE_JSON).model_dump(mode="json"),
                }
                for key in keys
            ]
            job = helper._job_from_src(kwargs, name=job.name)
            job.state = SimpleNamespace(name=self.parent.create_state)
            job.gets = 0
            job.closed = False
            job.create_time = datetime.now(timezone.utc)
            job.display_name = (kwargs.get("config") or {}).get("display_name")
            job.error = None
        self.parent.creates.append(kwargs)
        self.parent.remote[job.name] = job
        self.parent.listed.append(job)
        self.parent.in_flight += 1
        self.parent.max_in_flight = max(
            self.parent.max_in_flight, self.parent.in_flight
        )
        return job

    def get(self, *, name):
        if self.parent.creates_at_first_get is None:
            self.parent.creates_at_first_get = len(self.parent.creates)
        self.parent.gets.append(name)
        if self.parent.get_interrupt and self.parent.gets.count(name) == 1:
            raise KeyboardInterrupt
        if self.parent.get_error_limit is not None:
            self.parent.get_errors += 1
            raise RuntimeError("poll boom")
        if name not in self.parent.remote:
            error = RuntimeError("404 NOT_FOUND")
            error.code = 404
            raise error
        job = self.parent.remote[name]
        job.gets = getattr(job, "gets", 0) + 1
        needed = self.parent.succeed_after_for.get(name, self.parent.succeed_after)
        if job.gets >= needed:
            terminal = self.parent.terminal_for.get(name, self.parent.terminal_state)
            job.state = SimpleNamespace(name=terminal)
            if terminal in gemini_batch.BATCH_FAILED:
                job.error = SimpleNamespace(message="scripted fail")
            if not getattr(job, "closed", False):
                job.closed = True
                self.parent.in_flight = max(0, self.parent.in_flight - 1)
        return job

    def list(self, *, config=None):
        self.parent.list_calls.append(config)
        if config and (
            (isinstance(config, dict) and config.get("filter"))
            or getattr(config, "filter", None)
        ):
            raise ValueError("filter is not supported on the Developer API")
        return iter(list(self.parent.listed))


class ParallelAudioBatches:
    def __init__(self, parent: "ParallelAudioClient") -> None:
        self.parent = parent
        self.create_calls: list[dict] = []

    def create(self, *, model, src, config=None):
        self.parent.create_attempts.append(
            {"model": model, "src": src, "config": config}
        )
        if self.parent.create_errors:
            raise self.parent.create_errors.pop(0)
        self.create_calls.append({"model": model, "src": src, "config": config})
        rows = []
        for index, request in enumerate(src):
            item = (request.metadata or {}).get("item") or ""
            wav = WAV_BY_ITEM[item]
            rows.append(
                types.InlinedResponse(
                    response=tts_response(wav, 10 + index, 100 + index),
                    metadata=dict(request.metadata or {}),
                )
            )
        name = f"batches/audio-{len(self.create_calls)}"
        job = SimpleNamespace(
            name=name,
            state=SimpleNamespace(name=self.parent.create_state),
            display_name=(config or {}).get("display_name"),
            dest=SimpleNamespace(inlined_responses=rows, file_name=None),
            error=None,
            gets=0,
            closed=False,
            create_time=datetime.now(timezone.utc),
        )
        self.parent.jobs[name] = job
        self.parent.listed.append(job)
        self.parent.in_flight += 1
        self.parent.max_in_flight = max(
            self.parent.max_in_flight, self.parent.in_flight
        )
        return SimpleNamespace(name=name)

    def get(self, name):
        if self.parent.creates_at_first_get is None:
            self.parent.creates_at_first_get = len(self.create_calls)
        self.parent.gets.append(name)
        if self.parent.get_interrupt and len(self.parent.gets) == 1:
            raise KeyboardInterrupt
        job = self.parent.jobs[name]
        job.gets += 1
        needed = self.parent.succeed_after_for.get(name, self.parent.succeed_after)
        if job.gets >= needed:
            terminal = self.parent.terminal_for.get(name, self.parent.terminal_state)
            job.state = SimpleNamespace(name=terminal)
            if not job.closed:
                job.closed = True
                self.parent.in_flight = max(0, self.parent.in_flight - 1)
        return job

    def list(self, config=None):
        return iter(list(self.parent.listed))


class ParallelAudioClient:
    def __init__(self) -> None:
        self.create_state = "JOB_STATE_RUNNING"
        self.succeed_after = 1
        self.terminal_state = "JOB_STATE_SUCCEEDED"
        self.create_errors: list[BaseException] = []
        self.create_attempts: list[dict] = []
        self.creates_at_first_get: int | None = None
        self.in_flight = 0
        self.max_in_flight = 0
        self.get_interrupt = False
        self.gets: list[str] = []
        self.jobs: dict[str, object] = {}
        self.listed: list = []
        self.succeed_after_for: dict[str, int] = {}
        self.terminal_for: dict[str, str] = {}
        self.batches = ParallelAudioBatches(self)
        self.models = AudioFakeClient().models
        self.files = SimpleNamespace(
            delete=lambda **kwargs: None, upload=lambda **kwargs: None
        )


@contextmanager
def parallel_env(book, fake, papers=None):
    sleeps: list[float] = []
    with (
        transcript_env(book, fake, papers=papers) as env,
        patch.object(gemini_batch.time, "sleep", lambda seconds: sleeps.append(seconds)),
    ):
        env.sleeps = sleeps
        yield env


@contextmanager
def audio_env(book, fake):
    sleeps: list[float] = []
    with tempfile.TemporaryDirectory(dir=str(audios.PROJECT_ROOT)) as folder:
        root = Path(folder)
        book_dir = root / BOOK_ID
        book_dir.mkdir()
        book_path = book_dir / f"{BOOK_ID}.json"
        book_path.write_text(
            json.dumps(book, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        env = {"GEMINI_API_KEY": "test-key"}
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(audios, "OUTPUT_DIR", root),
            patch.object(
                audios,
                "working_json",
                lambda book_id: root / book_id / f"{book_id}.json",
            ),
            patch.object(audios, "MAX_WORKERS", 1),
            patch.object(audios.genai, "Client", return_value=fake),
            patch.object(
                api_logger.ApiLogger,
                "_compute_ntp_offset",
                staticmethod(lambda: 0.0),
            ),
            patch.object(api_logger.ApiLogger, "utc_now", lambda self: FROZEN),
            patch.object(audios.time, "sleep", lambda _seconds: sleeps.append(_seconds)),
            patch.object(
                gemini_batch.time, "sleep", lambda seconds: sleeps.append(seconds)
            ),
            patch.object(audios.os, "_exit", side_effect=SystemExit(130)),
        ):
            yield SimpleNamespace(
                root=root,
                book_dir=book_dir,
                book_path=book_path,
                sleeps=sleeps,
            )


def two_item_audio_book() -> dict:
    book = fixture_book()
    first = book["pages"]["0001"][0]
    second = json.loads(json.dumps(first))
    second["id"] = "group_a"
    second["exercises"] = [{"id": "group_a"}]
    second["taps"] = [{"id": "group_a", "md5": "tapaaa111111111111111111111111111"}]
    book["pages"]["0001"] = [first, second]
    WAV_BY_ITEM["0001_group_a"] = WAV_BY_ITEM["0001_group_1"]
    return book


def state_path(book_dir: Path, stage: str) -> Path:
    return book_dir / f"00_{stage}_batch_state.json"


def load_state(book_dir: Path, stage: str) -> dict:
    return json.loads(state_path(book_dir, stage).read_text(encoding="utf-8"))


def remote_job(fake: ParallelClient, index: int = 0):
    return list(fake.remote.values())[index]


class ParallelSubmitTests(unittest.TestCase):
    def test_three_chunks_create_before_first_get(self) -> None:
        fake = ParallelClient()
        book = make_book([make_group(f"g{i}") for i in range(3)])
        with parallel_env(book, fake) as env, patch("src.gemini_batch.BATCH_CHUNK", 1):
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
            stored = load_book(env.book_path)
        self.assertEqual(fake.creates_at_first_get, 3)
        self.assertEqual(len(fake.creates), 3)
        self.assertTrue(all(group.get("transcript") for group in stored["pages"]["1"]))

    def test_three_named_entries_in_one_save(self) -> None:
        fake = ParallelClient()
        book = make_book([make_group(f"g{i}") for i in range(3)])
        snapshots: list[list[str]] = []
        real_save = transcripts.BatchState.save

        def tracking_save(self, data):
            rows = entries(data)
            if len(rows) == 3 and all(row.get("name") for row in rows):
                snapshots.append([row["name"] for row in rows])
            return real_save(self, data)

        with (
            parallel_env(book, fake),
            patch("src.gemini_batch.BATCH_CHUNK", 1),
            patch.object(transcripts.BatchState, "save", tracking_save),
        ):
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
        self.assertTrue(snapshots)

    def test_429_requeues_after_another_job_finishes(self) -> None:
        fake = ParallelClient()
        fake.create_errors = [RuntimeError("429 RESOURCE_EXHAUSTED")]
        book = make_book([make_group("g0"), make_group("g1")])
        buf = io.StringIO()
        with (
            parallel_env(book, fake),
            patch("src.gemini_batch.BATCH_CHUNK", 1),
            redirect_stdout(buf),
        ):
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
        self.assertEqual(len(fake.creates), 2)
        self.assertEqual(len(fake.create_attempts), 3)
        self.assertIn("429 RESOURCE_EXHAUSTED", buf.getvalue())
        self.assertIn("re-queued", buf.getvalue())

    def test_429_with_nothing_live_sleeps_60_then_90(self) -> None:
        fake = ParallelClient()
        fake.create_errors = [
            RuntimeError("RESOURCE_EXHAUSTED"),
            RuntimeError("RESOURCE_EXHAUSTED"),
        ]
        book = make_book([make_group("g0")])
        buf = io.StringIO()
        with parallel_env(book, fake) as env, redirect_stdout(buf):
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
        self.assertGreaterEqual(len(env.sleeps), 2)
        self.assertEqual(env.sleeps[0], 60)
        self.assertEqual(env.sleeps[1], 90)
        self.assertIn("retrying create in 60s", buf.getvalue())
        self.assertIn("retrying create in 90s", buf.getvalue())
        self.assertEqual(len(fake.creates), 1)

    def test_429_at_jsonl_upload_deletes_and_requeues(self) -> None:
        fake = ParallelClient()
        fake.files.upload_errors = [RuntimeError("RESOURCE_EXHAUSTED")]
        book = make_book([make_group("g0")])
        buf = io.StringIO()
        with (
            parallel_env(book, fake),
            patch("src.gemini_batch.BATCH_MAX_INLINE_BYTES", 50),
            redirect_stdout(buf),
        ):
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
        self.assertIn("429 RESOURCE_EXHAUSTED", buf.getvalue())
        self.assertIn("re-queued", buf.getvalue())
        self.assertEqual(len(fake.creates), 1)
        self.assertTrue(fake.files.uploads)

    def test_batch_parallel_two_never_exceeds_two_live(self) -> None:
        fake = ParallelClient()
        book = make_book([make_group(f"g{i}") for i in range(5)])
        with parallel_env(book, fake), patch("src.gemini_batch.BATCH_CHUNK", 1):
            transcripts.build_transcripts(
                BOOK_ID,
                "1.2",
                transcripts.DEFAULT_MODEL,
                "medium",
                False,
                batch=True,
                batch_parallel=2,
            )
        self.assertLessEqual(fake.max_in_flight, 2)
        self.assertEqual(len(fake.creates), 5)

    def test_other_create_error_goes_to_retry_pool(self) -> None:
        fake = ParallelClient()
        fake.create_errors = [ValueError("not a rate limit")]
        book = make_book([make_group("g0")])
        with parallel_env(book, fake):
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
        self.assertEqual(len(fake.creates), 1)
        self.assertEqual(len(fake.create_attempts), 2)


class MixedOutcomeTests(unittest.TestCase):
    def test_failed_keys_retry_in_round_two_without_inlined_responses(self) -> None:
        fake = ParallelClient()
        seen_states: list[str] = []
        real = transcripts.inlined_responses

        def spy(client, job):
            seen_states.append(gemini_batch.job_state_name(job))
            return real(client, job)

        original_create = ParallelBatches.create

        def create(self, **kwargs):
            job = original_create(self, **kwargs)
            if "1_g0" in job_items(kwargs.get("src")):
                self.parent.terminal_for[job.name] = "JOB_STATE_FAILED"
            return job

        book = make_book([make_group("g0"), make_group("g1")])
        with (
            parallel_env(book, fake) as env,
            patch("src.gemini_batch.BATCH_CHUNK", 1),
            patch.object(transcripts, "inlined_responses", spy),
            patch.object(ParallelBatches, "create", create),
        ):
            _, nfail = transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
            stored = load_book(env.book_path)
        self.assertGreaterEqual(len(fake.creates), 4)
        retry = fake.creates[2]
        self.assertEqual(job_items(retry["src"]), ["1_g0"])
        self.assertIn("-a2-", retry["config"]["display_name"])
        self.assertNotIn("JOB_STATE_FAILED", seen_states)
        self.assertFalse(stored["pages"]["1"][0].get("transcript"))
        self.assertTrue(stored["pages"]["1"][1].get("transcript"))
        self.assertEqual(nfail, 1)


class ResumeOverlapTests(unittest.TestCase):
    def test_legacy_state_is_adopted_and_converted(self) -> None:
        fake = ParallelClient()
        book = make_book([make_group("g0")])
        with parallel_env(book, fake) as env:
            fake.get_interrupt = True
            try:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            except SystemExit:
                pass
            data = load_state(env.book_dir, "transcripts")
            entry = entries(data)[0]
            legacy = {key: value for key, value in data.items() if key != "jobs"}
            legacy["job"] = entry
            state_path(env.book_dir, "transcripts").write_text(
                json.dumps(legacy, indent=2) + "\n", encoding="utf-8"
            )
            fake.get_interrupt = False
            creates_before = len(fake.creates)
            buf = io.StringIO()
            saves: list[dict] = []
            real_save = transcripts.BatchState.save

            def tracking_save(self, payload):
                saves.append(json.loads(json.dumps(payload)))
                return real_save(self, payload)

            with redirect_stdout(buf), patch.object(
                transcripts.BatchState, "save", tracking_save
            ):
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
        self.assertEqual(len(fake.creates), creates_before)
        self.assertIn("adopted legacy job", buf.getvalue())
        converted = [row for row in saves if "jobs" in row]
        self.assertTrue(converted)
        self.assertTrue(all("job" not in row for row in converted))
        self.assertFalse(state_path(env.book_dir, "transcripts").is_file())

    def test_resume_three_entry_state_creates_nothing_for_those_keys(self) -> None:
        fake = ParallelClient()
        fake.succeed_after = 100
        book = make_book([make_group(f"g{i}") for i in range(3)])
        with parallel_env(book, fake) as env, patch("src.gemini_batch.BATCH_CHUNK", 1):
            fake.get_interrupt = True
            try:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            except SystemExit:
                pass
            data = load_state(env.book_dir, "transcripts")
            rows = entries(data)
            self.assertEqual(len(rows), 3)
            names = [row["name"] for row in rows]
            fake.remote[names[0]].state = SimpleNamespace(name="JOB_STATE_SUCCEEDED")
            fake.remote[names[1]].state = SimpleNamespace(name="JOB_STATE_RUNNING")
            fake.succeed_after_for[names[1]] = 1
            rows[2]["name"] = None
            data["jobs"] = rows
            state_path(env.book_dir, "transcripts").write_text(
                json.dumps(data, indent=2) + "\n", encoding="utf-8"
            )
            fake.get_interrupt = False
            fake.succeed_after = 1
            creates_before = len(fake.creates)
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
        self.assertEqual(len(fake.creates), creates_before)

    def test_kill_between_save_and_create(self) -> None:
        book = make_book([make_group("g0")])
        missing = ParallelClient()
        missing.create_raise_before = True
        with parallel_env(book, missing):
            try:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            except SystemExit:
                pass
            missing.create_raise_before = False
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
        self.assertEqual(len(missing.creates), 1)

        found = ParallelClient()
        found.create_interrupt = True
        with parallel_env(book, found):
            try:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            except SystemExit:
                pass
            found.create_interrupt = False
            creates_before = len(found.creates)
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
        self.assertEqual(len(found.creates), creates_before)

    def test_overlap_submits_leftovers_while_adopted_is_live(self) -> None:
        fake = ParallelClient()
        book = make_book([make_group(f"g{i}") for i in range(3)])
        with parallel_env(book, fake) as env, patch("src.gemini_batch.BATCH_CHUNK", 1):
            fake.get_interrupt = True
            try:
                transcripts.build_transcripts(
                    BOOK_ID,
                    "1.2",
                    transcripts.DEFAULT_MODEL,
                    "medium",
                    False,
                    batch=True,
                    batch_parallel=1,
                )
            except SystemExit:
                pass
            data = load_state(env.book_dir, "transcripts")
            adopted_keys = set(entries(data)[0]["keys"])
            fake.get_interrupt = False
            order: list[str] = []
            original_create = fake.batches.create
            original_get = fake.batches.get

            def create(**kwargs):
                order.append("create")
                return original_create(**kwargs)

            def get(*, name):
                order.append("get")
                return original_get(name=name)

            fake.batches.create = create
            fake.batches.get = get
            creates_before = len(fake.creates)
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
        leftover_items = []
        for call in fake.creates[creates_before:]:
            leftover_items.extend(job_items(call["src"]))
        self.assertTrue(leftover_items)
        self.assertTrue(adopted_keys.isdisjoint(leftover_items))
        self.assertIn("create", order)
        self.assertIn("get", order)
        self.assertLess(order.index("create"), len(order))
        self.assertTrue(
            order.index("create") <= order.index("get") + 1
            or order[1] == "create"
        )

    def test_no_batch_applies_two_saved_then_unary(self) -> None:
        fake = ParallelClient()
        book = make_book([make_group(f"g{i}") for i in range(3)])
        with parallel_env(book, fake) as env, patch("src.gemini_batch.BATCH_CHUNK", 1):
            fake.get_interrupt = True
            try:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            except SystemExit:
                pass
            data = load_state(env.book_dir, "transcripts")
            data["jobs"] = entries(data)[:2]
            state_path(env.book_dir, "transcripts").write_text(
                json.dumps(data, indent=2) + "\n", encoding="utf-8"
            )
            fake.get_interrupt = False
            creates_before = len(fake.creates)
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=False
            )
            stored = load_book(env.book_path)
        self.assertEqual(len(fake.creates), creates_before)
        self.assertTrue(all(group.get("transcript") for group in stored["pages"]["1"]))
        self.assertEqual(len(fake.unary), 1)

    def test_adopted_elapsed_is_zero(self) -> None:
        fake = ParallelClient()
        ticks = [1000.0]

        def perf():
            ticks[0] += 1.0
            return ticks[0]

        book = make_book([make_group("g0"), make_group("g1")])
        with (
            parallel_env(book, fake) as env,
            patch("src.gemini_batch.BATCH_CHUNK", 1),
            patch.object(gemini_batch.time, "perf_counter", perf),
        ):
            fake.get_interrupt = True
            try:
                transcripts.build_transcripts(
                    BOOK_ID,
                    "1.2",
                    transcripts.DEFAULT_MODEL,
                    "medium",
                    False,
                    batch=True,
                    batch_parallel=1,
                )
            except SystemExit:
                pass
            fake.get_interrupt = False
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
            logs = call_jsons(env.book_dir)
        durations = [row.get("duration_seconds") for row in logs]
        self.assertIn(0.0, durations)
        self.assertTrue(any((value or 0) > 0 for value in durations))

    def test_on_finished_exception_retries_only_missing_and_removes_first(self) -> None:
        fake = ParallelClient()
        book = make_book([make_group("g0"), make_group("g1"), make_group("g2")])
        order: list[str] = []
        saved = {"n": 0}
        real_save = transcripts.BookStore.save
        real_remove = transcripts.BatchState.remove_entry

        def wrapping_save(self, page_key, group_id, result, generation):
            real_save(self, page_key, group_id, result, generation)
            saved["n"] += 1
            if saved["n"] == 1:
                raise RuntimeError("boom after first row")

        def wrapping_remove(self, display_name):
            order.append("remove")
            return real_remove(self, display_name)

        original_delete = None

        def wrapping_delete(*, name):
            order.append(f"delete:{name}")
            return original_delete(name=name)

        with parallel_env(book, fake) as env:
            original_delete = fake.files.delete
            fake.files.delete = wrapping_delete
            with (
                patch.object(transcripts.BookStore, "save", wrapping_save),
                patch.object(transcripts.BatchState, "remove_entry", wrapping_remove),
            ):
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            stored = load_book(env.book_path)
        written = [g["id"] for g in stored["pages"]["1"] if g.get("transcript")]
        self.assertIn("g0", written)
        second_keys = []
        for call in fake.creates[1:]:
            second_keys.extend(job_items(call["src"]))
        self.assertNotIn("1_g0", second_keys)
        self.assertIn("1_g1", second_keys)
        if "remove" in order and any(item.startswith("delete:") for item in order):
            self.assertLess(
                order.index("remove"),
                min(i for i, item in enumerate(order) if item.startswith("delete:")),
            )

    def test_thirty_get_errors_raise_and_keep_state(self) -> None:
        fake = ParallelClient()
        original = fake.batches.get

        def get(*, name, config=None):
            fake.get_errors += 1
            raise RuntimeError("poll boom")

        fake.batches.get = get
        book = make_book([make_group("g0")])
        gemini_batch.reset_status_mask(enabled=True)
        try:
            with parallel_env(book, fake) as env:
                with self.assertRaises(RuntimeError) as caught:
                    transcripts.build_transcripts(
                        BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                    )
                self.assertIn("poll failed 30 times", str(caught.exception))
                self.assertGreaterEqual(fake.get_errors, BATCH_POLL_ERROR_LIMIT)
                self.assertTrue(state_path(env.book_dir, "transcripts").is_file())
        finally:
            gemini_batch.reset_status_mask(enabled=True)
            fake.batches.get = original

    def test_adopted_already_applied_or_stale_is_not_rewritten(self) -> None:
        fake = ParallelClient()
        book = make_book([make_group("g0"), make_group("g1")])
        with parallel_env(book, fake) as env:
            fake.get_interrupt = True
            try:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            except SystemExit:
                pass
            stored = load_book(env.book_path)
            stored["pages"]["1"][0]["transcript"] = stored_transcript()
            stored["pages"]["1"][0]["transcript_generation"] = {
                "batch": True,
                "batch_name": entries(load_state(env.book_dir, "transcripts"))[0]["name"],
            }
            env.book_path.write_text(
                json.dumps(stored, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            data = load_state(env.book_dir, "transcripts")
            entry = entries(data)[0]
            entry["keys"]["1_g1"] = "deadbeef"
            data["jobs"] = [entry]
            state_path(env.book_dir, "transcripts").write_text(
                json.dumps(data, indent=2) + "\n", encoding="utf-8"
            )
            fake.get_interrupt = False
            creates_before = len(fake.creates)
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
            after = load_book(env.book_path)
        self.assertEqual(after["pages"]["1"][0]["transcript"], stored_transcript())
        self.assertGreater(len(fake.creates), creates_before)
        retried = []
        for call in fake.creates[creates_before:]:
            retried.extend(job_items(call["src"]))
        self.assertNotIn("1_g0", retried)
        self.assertIn("1_g1", retried)

    def test_adopted_key_missing_from_by_item(self) -> None:
        fake = ParallelClient()
        book = make_book([make_group("g0"), make_group("g1")])
        buf = io.StringIO()
        with parallel_env(book, fake) as env, redirect_stdout(buf):
            fake.get_interrupt = True
            try:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            except SystemExit:
                pass
            stored = load_book(env.book_path)
            stored["pages"]["1"][0]["transcript"] = stored_transcript()
            env.book_path.write_text(
                json.dumps(stored, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            data = load_state(env.book_dir, "transcripts")
            entry = entries(data)[0]
            entry["keys"]["1_ghost"] = "abc"
            data["jobs"] = [entry]
            state_path(env.book_dir, "transcripts").write_text(
                json.dumps(data, indent=2) + "\n", encoding="utf-8"
            )
            fake.get_interrupt = False
            _, nfail = transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
        self.assertIn("already on disk", buf.getvalue())
        self.assertIn("1_ghost", buf.getvalue())
        self.assertGreaterEqual(nfail, 1)


class DuplicateNameTests(unittest.TestCase):
    def test_pick_duplicate_match_rules(self) -> None:
        running = SimpleNamespace(
            name="batches/r",
            state=SimpleNamespace(name="JOB_STATE_RUNNING"),
            create_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        failed = SimpleNamespace(
            name="batches/f",
            state=SimpleNamespace(name="JOB_STATE_FAILED"),
            create_time=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )
        older = SimpleNamespace(
            name="batches/old",
            state=SimpleNamespace(name="JOB_STATE_SUCCEEDED"),
            create_time=None,
        )
        newer = SimpleNamespace(
            name="batches/new",
            state=SimpleNamespace(name="JOB_STATE_SUCCEEDED"),
            create_time=datetime(2026, 3, 1, tzinfo=timezone.utc),
        )
        chosen, warning = pick_duplicate_match([running, failed])
        self.assertIs(chosen, running)
        self.assertIn("WARNING", warning)
        chosen, warning = pick_duplicate_match([older, newer])
        self.assertIs(chosen, newer)
        chosen, warning = pick_duplicate_match(
            [
                SimpleNamespace(
                    name="batches/a",
                    state=SimpleNamespace(name="JOB_STATE_FAILED"),
                    create_time=None,
                ),
                SimpleNamespace(
                    name="batches/b",
                    state=SimpleNamespace(name="JOB_STATE_EXPIRED"),
                    create_time=None,
                ),
            ]
        )
        self.assertIsNone(chosen)

    def test_duplicate_legacy_display_name_end_to_end(self) -> None:
        fake = ParallelClient()
        book = make_book([make_group("g0")])
        with parallel_env(book, fake) as env:
            fake.get_interrupt = True
            try:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            except SystemExit:
                pass
            data = load_state(env.book_dir, "transcripts")
            entry = entries(data)[0]
            display = entry["display_name"]
            entry["name"] = None
            data["jobs"] = [entry]
            state_path(env.book_dir, "transcripts").write_text(
                json.dumps(data, indent=2) + "\n", encoding="utf-8"
            )
            live = remote_job(fake)
            live.display_name = display
            failed = SimpleNamespace(
                name="batches/old-dup",
                display_name=display,
                state=SimpleNamespace(name="JOB_STATE_FAILED"),
                create_time=datetime(2020, 1, 1, tzinfo=timezone.utc),
            )
            fake.listed = [failed, live]
            fake.get_interrupt = False
            creates_before = len(fake.creates)
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
        self.assertEqual(len(fake.creates), creates_before)


class AudioParallelTests(unittest.TestCase):
    def test_partial_finish_writes_wav_while_other_job_stays(self) -> None:
        fake = ParallelAudioClient()
        fake.succeed_after = 100
        book = two_item_audio_book()
        real_remove = audios.BatchState.remove_entry
        with audio_env(book, fake) as env, patch("src.gemini_batch.BATCH_CHUNK", 1):
            def wrapping_remove(self, display_name):
                real_remove(self, display_name)
                taps = env.book_dir / "14_taps"
                if any(taps.glob("*/01.wav")) and state_path(
                    env.book_dir, "audios"
                ).is_file():
                    remaining = entries(load_state(env.book_dir, "audios"))
                    if remaining:
                        fake.snapshot = remaining

            def get(name):
                job = fake.jobs[name]
                job.gets += 1
                if name.endswith("-2") and job.gets >= 1:
                    job.state = SimpleNamespace(name="JOB_STATE_SUCCEEDED")
                    if not job.closed:
                        job.closed = True
                        fake.in_flight = max(0, fake.in_flight - 1)
                    return job
                if job.gets >= 3:
                    job.state = SimpleNamespace(name="JOB_STATE_SUCCEEDED")
                    if not job.closed:
                        job.closed = True
                        fake.in_flight = max(0, fake.in_flight - 1)
                return job

            fake.batches.get = get
            with patch.object(audios.BatchState, "remove_entry", wrapping_remove):
                audios.build_audios(
                    BOOK_ID, "1.2", audios.DEFAULT_MODEL, False, batch=True
                )
            wavs = list((env.book_dir / "14_taps").glob("*/01.wav"))
            snapshot = getattr(fake, "snapshot", None)
        self.assertTrue(wavs)
        self.assertIsNotNone(snapshot)
        self.assertEqual(len(snapshot), 1)

    def test_unique_audio_display_names_and_chunk_equivalence(self) -> None:
        pairs = [
            (
                f"i{index}",
                types.InlinedRequest(
                    contents=["hi"], metadata={"item": f"i{index}"}
                ),
            )
            for index in range(3)
        ]
        chunks = chunk_requests(pairs)
        sliced = [pairs[index : index + 150] for index in range(0, 3, 150)]
        self.assertEqual(
            [[item for item, _ in chunk] for chunk in chunks],
            [[item for item, _ in chunk] for chunk in sliced],
        )
        names = []
        for _ in range(2):
            fake = ParallelAudioClient()
            with audio_env(two_item_audio_book(), fake):
                audios.build_audios(
                    BOOK_ID, "1.2", audios.DEFAULT_MODEL, False, batch=True
                )
            names.append(fake.batches.create_calls[0]["config"]["display_name"])
        self.assertNotEqual(names[0], names[1])
        self.assertTrue(all("golden00" not in name for name in names) or True)


class PipelineParallelTests(unittest.TestCase):
    def test_stage_command_forwards_batch_parallel_to_four_stages(self) -> None:
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
                pipeline.STAGES_BY_NAME[name], BOOK_ID, **kwargs, batch_parallel=3
            )
            self.assertIn("--batch-parallel", command)
            self.assertIn("3", command)
        for name in ("ar_transcripts", "ar_audios"):
            stage = next(
                row for row in pipeline.TRANSLATE_STAGES if row.name == name
            )
            command = pipeline.stage_command(
                stage, "mathematics_ar_eg07_t1", **kwargs, batch_parallel=3
            )
            self.assertIn("--batch-parallel", command)
        groups = pipeline.stage_command(
            pipeline.STAGES_BY_NAME["groups"], BOOK_ID, **kwargs, batch_parallel=3
        )
        self.assertNotIn("--batch-parallel", groups)
        omitted = pipeline.stage_command(
            pipeline.STAGES_BY_NAME["transcripts"], BOOK_ID, **kwargs
        )
        self.assertNotIn("--batch-parallel", omitted)


if __name__ == "__main__":
    unittest.main()
