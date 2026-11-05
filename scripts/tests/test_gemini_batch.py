"""Unit tests for src/gemini_batch.py. No live Gemini calls."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from google.genai import types
from pydantic import BaseModel, Field
from pydantic_core import PydanticSerializationError

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from src import gemini_batch  # noqa: E402
from transcripts import SingleReply  # noqa: E402


class TinyReply(BaseModel):
    sentences: list[str] = Field(min_length=1)


def text_request(text: str, schema: type[BaseModel] | None = None) -> types.InlinedRequest:
    config = None
    if schema is not None:
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            thinking_config=types.ThinkingConfig(thinking_level="medium"),
        )
    return types.InlinedRequest(
        contents=[text],
        metadata={"item": "x"},
        config=config,
    )


def image_request(png: bytes, prompt: str = "prompt", schema: type[BaseModel] | None = TinyReply) -> types.InlinedRequest:
    return types.InlinedRequest(
        contents=[
            prompt,
            types.Part.from_bytes(data=png, mime_type="image/png"),
            '{"id": "g"}',
            '{"spec": true}',
        ],
        metadata={"item": "page_g"},
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            thinking_config=types.ThinkingConfig(thinking_level="medium"),
        ),
    )


class JobStateNameTests(unittest.TestCase):
    def test_enum_name(self) -> None:
        job = SimpleNamespace(state=SimpleNamespace(name="JOB_STATE_SUCCEEDED", value=1))
        self.assertEqual(gemini_batch.job_state_name(job), "JOB_STATE_SUCCEEDED")

    def test_string_value(self) -> None:
        job = SimpleNamespace(state=SimpleNamespace(name=None, value="JOB_STATE_FAILED"))
        self.assertEqual(gemini_batch.job_state_name(job), "JOB_STATE_FAILED")


class DisplayNameTests(unittest.TestCase):
    def test_legacy_without_run_id_matches_audios(self) -> None:
        book = "fixture_en_eg_t1"
        label = "a1-b1"
        expected = __import__("re").sub(r"[^A-Za-z0-9._-]+", "-", f"{book}-{label}")[:120]
        self.assertEqual(gemini_batch.batch_display_name(book, label), expected)

    def test_long_book_keeps_run_id(self) -> None:
        run_id = "deadbeef"
        name = gemini_batch.batch_display_name(
            "mathematics_en_eg07_t1_" + ("x" * 200),
            "transcripts",
            "a1-b1",
            run_id=run_id,
        )
        self.assertLessEqual(len(name), 120)
        self.assertTrue(name.endswith(run_id))
        self.assertNotIn("x" * 50, name[-20:])


class WaitForBatchTests(unittest.TestCase):
    def test_backoff_then_success(self) -> None:
        pending = SimpleNamespace(state=SimpleNamespace(name="JOB_STATE_PENDING"))
        done = SimpleNamespace(state=SimpleNamespace(name="JOB_STATE_SUCCEEDED"))
        client = SimpleNamespace(
            batches=SimpleNamespace(get=lambda name: self.jobs.pop(0))
        )
        self.jobs = [pending, pending, done]
        sleeps: list[float] = []
        with patch("src.gemini_batch.time.sleep", side_effect=sleeps.append):
            finished = gemini_batch.wait_for_batch(client, "batches/j", "a1-b1")
        self.assertEqual(gemini_batch.job_state_name(finished), "JOB_STATE_SUCCEEDED")
        self.assertEqual(sleeps, [20.0, 30.0])


class MapRowsTests(unittest.TestCase):
    def test_shuffled_inline_metadata(self) -> None:
        rows = [
            SimpleNamespace(metadata={"item": "b"}, response="B"),
            SimpleNamespace(metadata={"item": "a"}, response="A"),
        ]
        mapped = gemini_batch.map_rows_by_key(rows)
        self.assertEqual(mapped["a"].response, "A")
        self.assertEqual(mapped["b"].response, "B")

    def test_jsonl_key_field(self) -> None:
        rows = [{"key": "item-1", "response": {"text": "ok"}}]
        mapped = gemini_batch.map_rows_by_key(rows)
        self.assertIn("item-1", mapped)

    def test_unkeyed_row_is_not_mapped(self) -> None:
        rows = [
            SimpleNamespace(metadata={}, response="orphan"),
            {"response": "also-orphan"},
        ]
        self.assertEqual(gemini_batch.map_rows_by_key(rows), {})

    def test_duplicate_keeps_first(self) -> None:
        logged: list[str] = []
        rows = [
            SimpleNamespace(metadata={"item": "a"}, response="first"),
            SimpleNamespace(metadata={"item": "a"}, response="second"),
        ]
        mapped = gemini_batch.map_rows_by_key(rows, log=logged.append)
        self.assertEqual(mapped["a"].response, "first")
        self.assertTrue(any("duplicate" in line for line in logged))

    def test_unknown_key_stays_in_dict_for_caller_to_ignore(self) -> None:
        mapped = gemini_batch.map_rows_by_key(
            [SimpleNamespace(metadata={"item": "ghost"}, response="x")]
        )
        self.assertIn("ghost", mapped)


class ChunkAndSizeTests(unittest.TestCase):
    def test_splits_at_150_requests(self) -> None:
        pairs = [(f"i{i}", text_request("x")) for i in range(151)]
        chunks = gemini_batch.chunk_requests(pairs)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(len(chunks[0]), 150)
        self.assertEqual(len(chunks[1]), 1)

    def test_size_never_calls_model_dump_json(self) -> None:
        request = image_request(b"\x89PNG" + b"\x00" * 100, schema=SingleReply)
        with patch.object(
            types.InlinedRequest,
            "model_dump_json",
            side_effect=PydanticSerializationError("class schema"),
        ):
            size = gemini_batch.request_payload_size(request)
        self.assertGreater(size, 0)

    def test_pydantic_class_schema_does_not_raise(self) -> None:
        request = image_request(b"abc", schema=SingleReply)
        try:
            request.model_dump_json()
            dump_raises = False
        except Exception:
            dump_raises = True
        self.assertTrue(dump_raises)
        gemini_batch.request_payload_size(request)

    def test_byte_chunking_splits_under_18mb(self) -> None:
        huge = b"\x00" * (10 * 1024 * 1024)
        pairs = [
            ("a", image_request(huge)),
            ("b", image_request(huge)),
        ]
        chunks = gemini_batch.chunk_requests(pairs)
        self.assertEqual(len(chunks), 2)

    def test_single_oversize_request_is_its_own_chunk(self) -> None:
        huge = b"\x00" * (15 * 1024 * 1024)
        pairs = [("a", image_request(huge))]
        chunks = gemini_batch.chunk_requests(pairs)
        self.assertEqual(len(chunks), 1)
        self.assertTrue(gemini_batch.chunk_over_inline_limit(chunks[0]))


class JsonlTests(unittest.TestCase):
    def test_serializes_real_pydantic_schema_via_t_schema(self) -> None:
        request = image_request(b"png-bytes", schema=SingleReply)
        payload = gemini_batch.jsonl_request_payload(request, api_client=None)
        schema = payload["generationConfig"]["responseSchema"]
        self.assertIsInstance(schema, dict)
        self.assertIn("properties", schema)
        thinking = payload["generationConfig"]["thinkingConfig"]
        self.assertIn(thinking["thinkingLevel"].lower(), ("medium", "MEDIUM".lower()))

    def test_jsonl_src_writes_key_and_uploads(self) -> None:
        uploaded_paths: list[str] = []

        class Files:
            def upload(self, *, file, config):
                uploaded_paths.append(str(file))
                text = Path(file).read_text(encoding="utf-8")
                self.lines = [json.loads(line) for line in text.splitlines() if line.strip()]
                return SimpleNamespace(name="files/in-jsonl")

        client = SimpleNamespace(files=Files(), _api_client=None)
        pairs = [("item-a", image_request(b"png", schema=SingleReply))]
        name = gemini_batch.jsonl_src(client, pairs, "display")
        self.assertEqual(name, "files/in-jsonl")
        self.assertEqual(client.files.lines[0]["key"], "item-a")
        self.assertIn("request", client.files.lines[0])
        self.assertFalse(Path(uploaded_paths[0]).exists())

    def test_src_for_chunk_uses_jsonl_when_over_18mb(self) -> None:
        class Files:
            def upload(self, *, file, config):
                return SimpleNamespace(name="files/big")

        client = SimpleNamespace(files=Files(), _api_client=None)
        huge = b"\x00" * (15 * 1024 * 1024)
        pairs = [("a", image_request(huge))]
        src, uploaded = gemini_batch.src_for_chunk(client, pairs, "disp")
        self.assertEqual(src, "files/big")
        self.assertEqual(uploaded, "files/big")

    def test_delete_files_logs_and_is_not_fatal(self) -> None:
        class Files:
            def delete(self, *, name):
                raise RuntimeError("nope")

        logged: list[str] = []
        gemini_batch.delete_files(
            SimpleNamespace(files=Files()),
            "files/in",
            "files/out",
            log=logged.append,
        )
        self.assertEqual(len(logged), 2)


class FindJobTests(unittest.TestCase):
    def test_list_has_no_filter_and_pages(self) -> None:
        page1 = [SimpleNamespace(display_name="other", name="batches/1")]
        page2 = [SimpleNamespace(display_name="wanted", name="batches/2")]
        calls: list[Any] = []

        class Pager:
            def __init__(self) -> None:
                self._items = page1 + page2
                self.pages_seen = 2

            def __iter__(self):
                return iter(self._items)

        class Batches:
            def list(self, *, config=None):
                calls.append(config)
                if config and (
                    (isinstance(config, dict) and config.get("filter"))
                    or getattr(config, "filter", None)
                ):
                    raise ValueError("filter is not supported on the Developer API")
                return Pager()

        found = gemini_batch.find_job_by_display_name(
            SimpleNamespace(batches=Batches()), "wanted"
        )
        self.assertEqual(found.name, "batches/2")
        self.assertEqual(calls, [None])


class NotFoundTests(unittest.TestCase):
    def test_get_batch_job_returns_none(self) -> None:
        class Batches:
            def get(self, *, name):
                error = RuntimeError("404 NOT_FOUND")
                error.code = 404
                raise error

        self.assertIsNone(
            gemini_batch.get_batch_job(SimpleNamespace(batches=Batches()), "batches/x")
        )


class BatchStateTests(unittest.TestCase):
    def test_atomic_save_load_forget(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "00_transcripts_batch_state.json"
            state = gemini_batch.BatchState(path)
            state.save({"stage": "transcripts", "job": {"name": None, "keys": {"a": "h"}}})
            loaded = state.load()
            self.assertEqual(loaded["job"]["keys"]["a"], "h")
            state.forget()
            self.assertIsNone(state.load())

    def test_entries_and_save_entries(self) -> None:
        self.assertEqual(gemini_batch.entries(None), [])
        self.assertEqual(gemini_batch.entries({}), [])
        self.assertEqual(
            gemini_batch.entries({"job": {"name": "a"}}), [{"name": "a"}]
        )
        self.assertEqual(
            gemini_batch.entries({"jobs": [{"name": "a"}, {"name": "b"}]}),
            [{"name": "a"}, {"name": "b"}],
        )
        self.assertTrue(gemini_batch.is_legacy({"job": {"name": "a"}}))
        self.assertFalse(gemini_batch.is_legacy({"jobs": [{"name": "a"}]}))
        with tempfile.TemporaryDirectory() as folder:
            state = gemini_batch.BatchState(Path(folder) / "state.json")
            state.save_entries(
                {"stage": "transcripts", "job": "stale"},
                [{"display_name": "x", "name": "batches/1"}],
            )
            loaded = state.load()
            self.assertEqual(loaded["jobs"][0]["name"], "batches/1")
            self.assertNotIn("job", loaded)
            state.save_entries({"stage": "transcripts"}, [])
            self.assertIsNone(state.load())
            self.assertFalse(state.path.is_file())


class BatchLockTests(unittest.TestCase):
    def test_second_lock_is_refused_then_released(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "00_transcripts_batch.lock"
            first = gemini_batch.BatchLock(path)
            second = gemini_batch.BatchLock(path)
            first.acquire()
            try:
                with self.assertRaises(RuntimeError):
                    second.acquire()
            finally:
                first.release()
            second.acquire()
            second.release()

    def test_context_manager_releases_on_keyboard_interrupt(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "00_audios_batch.lock"
            try:
                with gemini_batch.BatchLock(path):
                    raise KeyboardInterrupt
            except KeyboardInterrupt:
                pass
            other = gemini_batch.BatchLock(path)
            other.acquire()
            other.release()


class InlinedResponsesTests(unittest.TestCase):
    def test_jsonl_result_keyed_by_key(self) -> None:
        dest = SimpleNamespace(
            inlined_responses=None,
            file_name="files/out",
        )
        job = SimpleNamespace(dest=dest)
        payload = (
            json.dumps({"key": "item-1", "response": {"text": "ok"}})
            + "\n"
            + json.dumps({"key": "item-2", "error": {"message": "nope"}})
            + "\n"
        )

        class Files:
            def download(self, *, file):
                self.downloaded = file
                return payload.encode("utf-8")

        rows = gemini_batch.inlined_responses(
            SimpleNamespace(files=Files()), job
        )
        mapped = gemini_batch.map_rows_by_key(rows)
        self.assertEqual(mapped["item-1"]["response"]["text"], "ok")
        _, error = gemini_batch.response_and_error(mapped["item-2"])
        self.assertEqual(error, "nope")


if __name__ == "__main__":
    unittest.main()
