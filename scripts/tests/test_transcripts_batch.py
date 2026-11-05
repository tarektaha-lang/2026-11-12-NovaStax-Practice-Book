"""Mocked tests for transcripts Batch mode. Never calls the live Gemini API."""

from __future__ import annotations

import hashlib
import io
import itertools
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from google.genai import types
from PIL import Image
from pydantic import BaseModel

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import pipeline  # noqa: E402
import transcripts  # noqa: E402
from cost import Call, load_prices  # noqa: E402
from groups import KEEP_ON_GROUP  # noqa: E402
from src.gemini_batch import (  # noqa: E402
    entries,
    jsonl_request_payload,
    request_payload_size,
)
from src.prompting import Prompt  # noqa: E402

BOOK_ID = "fixture_en_eg_t1"
STEM = "01_02"
SINGLE_JSON = json.dumps(
    {"paragraphs": [{"sentences": ["Hello world."]}]},
    ensure_ascii=False,
)
BAD_JSON = json.dumps({"paragraphs": []})
PNG = b"\x89PNG\r\n" + b"crop"


def tap_md5(group_id: str) -> str:
    return hashlib.md5(group_id.encode("utf-8")).hexdigest()


def make_group(
    group_id: str,
    *,
    section: str = "1.2",
    kind: str = "exercise",
    transcript: dict | None = None,
    generation: dict | None = None,
    answer: str = "1",
) -> dict:
    group: dict = {
        "id": group_id,
        "section": section,
        "kind": kind,
        "objective": "obj_1",
        "exercises": [{"id": f"{group_id}_ex", "answer": answer}],
        "bboxes": [[0.0, 0.0, 10.0, 10.0]],
        "taps": [{"id": group_id, "md5": tap_md5(group_id)}],
    }
    if transcript is not None:
        group["transcript"] = transcript
    if generation is not None:
        group["transcript_generation"] = generation
    return group


def stored_transcript() -> dict:
    return {"paragraphs": [{"sentences": ["Hello world."]}]}


def make_book(groups: list[dict], page: str = "1") -> dict:
    return {
        "id": BOOK_ID,
        "subject": "mathematics",
        "grade": "7",
        "language": "en",
        "country": "eg",
        "language_code": "en",
        "numerals": "western",
        "notation": "latin",
        "country_code": "eg",
        "pages": {page: groups},
    }


def make_response(text: str) -> types.GenerateContentResponse:
    return types.GenerateContentResponse.model_validate(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": text, "thought": False}],
                        "role": "model",
                    }
                }
            ],
            "usage_metadata": {
                "prompt_token_count": 10,
                "cached_content_token_count": 0,
                "thoughts_token_count": 2,
                "candidates_token_count": 5,
                "total_token_count": 17,
            },
        }
    )


def items_of(src) -> list[str]:
    if isinstance(src, str):
        return []
    items = []
    for request in src:
        meta = getattr(request, "metadata", None) or {}
        items.append(meta.get("item"))
    return items


class FakeFiles:
    def __init__(self) -> None:
        self.uploads: list[str] = []
        self.deleted: list[str] = []
        self.downloads: list[str] = []
        self.result_payload = ""

    def upload(self, *, file, config=None):
        self.uploads.append(str(file))
        return SimpleNamespace(name="files/in-jsonl")

    def download(self, *, file):
        self.downloads.append(file)
        return self.result_payload.encode("utf-8")

    def delete(self, *, name):
        self.deleted.append(name)


class FakeModels:
    def __init__(self, parent: FakeClient) -> None:
        self.parent = parent

    def generate_content(self, **kwargs):
        self.parent.unary.append(kwargs)
        text = self.parent.unary_text.pop(0) if self.parent.unary_text else SINGLE_JSON
        if isinstance(text, Exception):
            raise text
        return make_response(text)


class FakeBatches:
    def __init__(self, parent: FakeClient) -> None:
        self.parent = parent

    def create(self, **kwargs):
        if self.parent.create_interrupt:
            self.parent.creates.append(kwargs)
            job = self._job_from_src(kwargs, name="batches/adopt-me")
            job.display_name = kwargs.get("config", {}).get("display_name")
            self.parent.remote[job.name] = job
            self.parent.listed.append(job)
            raise KeyboardInterrupt
        self.parent.creates.append(kwargs)
        name = f"batches/job-{len(self.parent.creates)}"
        job = self._job_from_src(kwargs, name=name)
        self.parent.remote[name] = job
        self.parent.listed.append(job)
        return job

    def get(self, *, name):
        self.parent.gets.append(name)
        if self.parent.get_interrupt and self.parent.gets.count(name) == 1:
            raise KeyboardInterrupt
        if name not in self.parent.remote:
            error = RuntimeError("404 NOT_FOUND")
            error.code = 404
            raise error
        job = self.parent.remote[name]
        if self.parent.force_state:
            job.state = SimpleNamespace(name=self.parent.force_state.pop(0))
        return job

    def list(self, *, config=None):
        self.parent.list_calls.append(config)
        if config and (
            (isinstance(config, dict) and config.get("filter"))
            or getattr(config, "filter", None)
        ):
            raise ValueError("filter is not supported on the Developer API")
        return iter(list(self.parent.listed))

    def _job_from_src(self, kwargs, name: str):
        src = kwargs.get("src")
        display = (kwargs.get("config") or {}).get("display_name")
        if isinstance(src, str):
            rows = self.parent.jsonl_rows or []
            dest = SimpleNamespace(inlined_responses=None, file_name="files/out-jsonl")
            self.parent.files.result_payload = "\n".join(
                json.dumps(row) for row in rows
            )
        else:
            items = items_of(src)
            rows = []
            for item in items:
                if item in self.parent.omit_items:
                    continue
                if item in self.parent.item_text:
                    text = self.parent.item_text[item]
                else:
                    queue = self.parent.reply_queue.setdefault(item, [])
                    text = queue.pop(0) if queue else SINGLE_JSON
                if text is None:
                    continue
                extra = dict(src[items.index(item)].metadata or {})
                rows.append(
                    types.InlinedResponse(
                        metadata=extra,
                        response=make_response(text),
                    )
                )
            if self.parent.shuffle:
                rows = list(reversed(rows))
            if self.parent.extra_rows:
                rows.extend(self.parent.extra_rows)
            dest = SimpleNamespace(inlined_responses=rows, file_name=None)
        state = self.parent.create_state
        return SimpleNamespace(
            name=name,
            state=SimpleNamespace(name=state),
            display_name=display,
            dest=dest,
            error=None,
        )


class FakeClient:
    def __init__(self) -> None:
        self.creates: list[dict] = []
        self.gets: list[str] = []
        self.list_calls: list = []
        self.unary: list[dict] = []
        self.unary_text: list = []
        self.remote: dict = {}
        self.listed: list = []
        self.item_text: dict[str, str] = {}
        self.reply_queue: dict[str, list] = {}
        self.shuffle = False
        self.extra_rows: list = []
        self.create_state = "JOB_STATE_SUCCEEDED"
        self.force_state: list[str] = []
        self.create_interrupt = False
        self.get_interrupt = False
        self.jsonl_rows: list | None = None
        self.omit_items: set[str] = set()
        self.files = FakeFiles()
        self.models = FakeModels(self)
        self.batches = FakeBatches(self)
        self._api_client = None


def paper_for(*numbers: str) -> list[transcripts.PaperRef]:
    refs = []
    first = 1
    for index, number in enumerate(numbers, start=1):
        stem = f"01_{index:02d}" if number != "1.2" else STEM
        if number == "1.3":
            stem = "01_03"
        refs.append(
            transcripts.PaperRef(
                number=number, stem=stem, first=first, pages=20
            )
        )
        first += 20
    return refs


@contextmanager
def transcript_env(book: dict, fake: FakeClient, *, papers: list[str] | None = None):
    papers = papers or ["1.2"]
    refs = paper_for(*papers)
    prompt = Prompt("TRANSCRIBE THE GROUP", ("prompts/transcripts/core.md",))
    with tempfile.TemporaryDirectory(dir=str(transcripts.PROJECT_ROOT)) as folder:
        root = Path(folder)
        book_dir = root / BOOK_ID
        papers_dir = book_dir / transcripts.PAPERS_STAGE
        specs_dir = book_dir / transcripts.SPECS_STAGE
        papers_dir.mkdir(parents=True)
        specs_dir.mkdir(parents=True)
        book_path = book_dir / f"{BOOK_ID}.json"
        book_path.write_text(
            json.dumps(book, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        for ref in refs:
            (papers_dir / f"{ref.stem}.pdf").write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
            (specs_dir / f"{ref.stem}.json").write_text(
                '{"objective": "fractions"}', encoding="utf-8"
            )
        env = {"GEMINI_API_KEY": "test-key"}
        clock = itertools.count(1)
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(transcripts, "OUTPUT_DIR", root),
            patch.object(
                transcripts, "working_json", lambda book_id: root / book_id / f"{book_id}.json"
            ),
            patch.object(transcripts, "paper_index", lambda book_id: refs),
            patch.object(transcripts, "assemble_transcript", lambda meta: prompt),
            patch.object(transcripts.PageCache, "crop", lambda self, pdf, index, boxes: PNG),
            patch.object(transcripts.genai, "Client", lambda: fake),
            patch.object(transcripts.ApiLogger, "_compute_ntp_offset", return_value=0),
            patch.object(
                transcripts.ApiLogger,
                "utc_now",
                side_effect=lambda *args, **kwargs: datetime(
                    2026, 10, 25, 12, 0, 0, next(clock) * 1000, tzinfo=timezone.utc
                ),
            ),
            patch.object(transcripts.time, "sleep", lambda seconds: None),
            patch.object(transcripts, "new_run_id", lambda: "runid001"),
            patch.object(transcripts.os, "_exit", side_effect=SystemExit(130)),
        ):
            yield SimpleNamespace(root=root, book_path=book_path, book_dir=book_dir, refs=refs)


def load_book(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def call_jsons(book_dir: Path) -> list[dict]:
    logs = book_dir / "00_api_call_logs"
    if not logs.is_dir():
        return []
    rows = []
    for folder in sorted(logs.iterdir()):
        path = folder / "call.json"
        if path.is_file():
            rows.append(json.loads(path.read_text(encoding="utf-8")))
    return rows


class RequestParityTests(unittest.TestCase):
    def test_batch_request_matches_unary(self) -> None:
        fake = FakeClient()
        book = make_book([make_group("group_1")])
        with transcript_env(book, fake):
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
            unary_fake = FakeClient()
        with transcript_env(book, unary_fake):
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=False
            )
        batch_req = fake.creates[0]["src"][0]
        unary = unary_fake.unary[0]
        self.assertEqual(len(batch_req.contents), len(unary["contents"]))
        self.assertEqual(batch_req.contents[0], unary["contents"][0])
        self.assertEqual(batch_req.contents[2], unary["contents"][2])
        self.assertEqual(batch_req.contents[3], unary["contents"][3])
        self.assertEqual(batch_req.config.response_mime_type, unary["config"].response_mime_type)
        self.assertIs(batch_req.config.response_schema, unary["config"].response_schema)
        self.assertEqual(
            batch_req.config.thinking_config.thinking_level,
            unary["config"].thinking_config.thinking_level,
        )
        self.assertEqual(batch_req.metadata["item"], "1_group_1")
        self.assertIn("|a1", batch_req.metadata["log_key"])


class MappingTests(unittest.TestCase):
    def test_shuffled_rows_map_by_item(self) -> None:
        fake = FakeClient()
        fake.shuffle = True
        book = make_book([make_group("group_1"), make_group("group_2")])
        with transcript_env(book, fake) as env:
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
            stored = load_book(env.book_path)
        groups = stored["pages"]["1"]
        self.assertEqual(groups[0]["transcript"]["paragraphs"][0]["sentences"][0], "Hello world.")
        self.assertEqual(groups[1]["transcript"]["paragraphs"][0]["sentences"][0], "Hello world.")

    def test_unkeyed_row_is_not_mapped_by_position(self) -> None:
        fake = FakeClient()
        fake.extra_rows = [
            types.InlinedResponse(
                metadata={},
                response=make_response(
                    json.dumps({"paragraphs": [{"sentences": ["ORPHAN"]}]})
                ),
            )
        ]
        fake.omit_items.add("1_group_1")
        book = make_book([make_group("group_1")])
        with transcript_env(book, fake) as env:
            _, failed = transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
            stored = load_book(env.book_path)
        self.assertIsNone(stored["pages"]["1"][0].get("transcript"))
        self.assertGreaterEqual(failed, 1)

    def test_duplicate_and_unknown_keys(self) -> None:
        fake = FakeClient()
        fake.extra_rows = [
            types.InlinedResponse(
                metadata={"item": "1_group_1"},
                response=make_response(
                    json.dumps({"paragraphs": [{"sentences": ["SECOND"]}]})
                ),
            ),
            types.InlinedResponse(
                metadata={"item": "ghost"},
                response=make_response(SINGLE_JSON),
            ),
        ]
        book = make_book([make_group("group_1")])
        with transcript_env(book, fake) as env:
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
            stored = load_book(env.book_path)
        sentence = stored["pages"]["1"][0]["transcript"]["paragraphs"][0]["sentences"][0]
        self.assertEqual(sentence, "Hello world.")


class OutputParityTests(unittest.TestCase):
    def test_txt_and_working_json_match_unary(self) -> None:
        book = make_book([make_group("group_1")])
        batch_fake = FakeClient()
        unary_fake = FakeClient()
        with transcript_env(book, batch_fake) as batch_env:
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
            batch_book = load_book(batch_env.book_path)
            batch_txt = (
                batch_env.book_dir / "14_taps" / tap_md5("group_1") / "01.txt"
            ).read_text(encoding="utf-8")
        with transcript_env(book, unary_fake) as unary_env:
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=False
            )
            unary_book = load_book(unary_env.book_path)
            unary_txt = (
                unary_env.book_dir / "14_taps" / tap_md5("group_1") / "01.txt"
            ).read_text(encoding="utf-8")
        self.assertEqual(batch_txt, unary_txt)
        self.assertEqual(
            batch_book["pages"]["1"][0]["transcript"],
            unary_book["pages"]["1"][0]["transcript"],
        )


class LoggingAndCostTests(unittest.TestCase):
    def test_call_json_batch_true_priced_at_batch_rate(self) -> None:
        fake = FakeClient()
        book = make_book([make_group("group_1")])
        with transcript_env(book, fake) as env:
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
            logs = call_jsons(env.book_dir)
            generation = load_book(env.book_path)["pages"]["1"][0]["transcript_generation"]
        self.assertTrue(logs)
        document = logs[0]
        self.assertTrue(document["batch"])
        self.assertTrue(document["batch_name"].startswith("batches/"))
        self.assertIn("batch_key", document)
        prices = load_prices()
        usage = document.get("usage") or {}
        call = Call(
            step=document.get("step_name") or "transcripts",
            model=document["model"],
            item=str(document.get("page_number") or ""),
            sections=("1.2",),
            attempt=int(document.get("attempt") or 1),
            status=document.get("status") or "success",
            timestamp=document["timestamp"],
            input_tokens=int(usage.get("input_tokens") or 0),
            cached_input_tokens=int(usage.get("cached_input_tokens") or 0),
            thinking_tokens=int(usage.get("thinking_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            cache_storage_tokens=0,
            cache_storage_seconds=0.0,
            duration_seconds=float(document.get("duration_seconds") or 0),
            record="call.json",
            batch=True,
        )
        batch_cost = call.cost(prices)
        unary = Call(**{**call.__dict__, "batch": False})
        self.assertLess(batch_cost, unary.cost(prices))
        self.assertIn("batch", generation)
        self.assertEqual(generation["batch_name"], document["batch_name"])


class RetryAndFallbackTests(unittest.TestCase):
    def test_invalid_reply_retries_then_stops(self) -> None:
        fake = FakeClient()
        fake.reply_queue["1_group_1"] = [BAD_JSON, BAD_JSON, BAD_JSON, BAD_JSON]
        book = make_book([make_group("group_1")])
        with transcript_env(book, fake) as env:
            _, failed = transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
            stored = load_book(env.book_path)
        self.assertEqual(len(fake.creates), 4)
        self.assertEqual(failed, 1)
        self.assertIsNone(stored["pages"]["1"][0].get("transcript"))

    def test_unary_fallback_uses_current_attempt(self) -> None:
        fake = FakeClient()
        fake.reply_queue["1_group_1"] = [BAD_JSON, BAD_JSON, BAD_JSON]
        fake.unary_text = [SINGLE_JSON]
        book = make_book([make_group("group_1")])
        with transcript_env(book, fake) as env:
            transcripts.build_transcripts(
                BOOK_ID,
                "1.2",
                transcripts.DEFAULT_MODEL,
                "medium",
                False,
                batch=True,
                unary_fallback=1,
            )
            logs = call_jsons(env.book_dir)
        self.assertEqual(len(fake.creates), 3)
        self.assertEqual(len(fake.unary), 1)
        unary_logs = [row for row in logs if not row.get("batch")]
        self.assertEqual(unary_logs[0]["attempt"], 4)

    def test_partially_succeeded_retries_missing(self) -> None:
        fake = FakeClient()
        fake.create_state = "JOB_STATE_PARTIALLY_SUCCEEDED"
        fake.reply_queue["1_group_2"] = [None]
        book = make_book([make_group("group_1"), make_group("group_2")])
        with transcript_env(book, fake) as env:
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
            stored = load_book(env.book_path)
        self.assertGreaterEqual(len(fake.creates), 2)
        self.assertTrue(stored["pages"]["1"][0].get("transcript"))


class ResumeTests(unittest.TestCase):
    def _write_state(self, book_dir: Path, keys: dict[str, str], **overrides) -> None:
        payload = {
            "stage": "transcripts",
            "book_id": BOOK_ID,
            "model": transcripts.DEFAULT_MODEL,
            "thinking": "medium",
            "section": "1.2",
            "update": False,
            "job": {
                "name": "batches/saved",
                "display_name": "saved-display",
                "attempt": 1,
                "src_file_name": None,
                "keys": keys,
            },
        }
        payload.update(overrides)
        if "job" in overrides:
            payload["job"] = overrides["job"]
        (book_dir / "00_transcripts_batch_state.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )

    def test_resume_reuses_saved_name(self) -> None:
        fake = FakeClient()
        book = make_book([make_group("group_1")])
        with transcript_env(book, fake):
            fake.get_interrupt = True
            try:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            except SystemExit:
                pass
            fake.get_interrupt = False
            creates_before = len(fake.creates)
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
        self.assertEqual(creates_before, 1)
        self.assertEqual(len(fake.creates), creates_before)

    def test_ctrl_c_during_poll_resume_same_hashes(self) -> None:
        fake = FakeClient()
        book = make_book([make_group("group_1")])
        with transcript_env(book, fake) as env:
            fake.get_interrupt = True
            try:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            except SystemExit:
                pass
            state = json.loads(
                (env.book_dir / "00_transcripts_batch_state.json").read_text()
            )
            saved_hash = entries(state)[0]["keys"]["1_group_1"]
            fake.get_interrupt = False
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
            self.assertFalse((env.book_dir / "00_transcripts_batch_state.json").is_file())
        self.assertEqual(len(fake.creates), 1)
        self.assertTrue(saved_hash)

    def test_ctrl_c_before_create_adopts_via_list(self) -> None:
        fake = FakeClient()
        fake.create_interrupt = True
        book = make_book([make_group("group_1")])
        with transcript_env(book, fake) as env:
            try:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            except SystemExit:
                pass
            fake.create_interrupt = False
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
            stored = load_book(env.book_path)
        self.assertEqual(len(fake.creates), 1)
        self.assertTrue(fake.list_calls)
        self.assertIsNone(fake.list_calls[0])
        self.assertTrue(stored["pages"]["1"][0].get("transcript"))

    def test_expired_saved_job_resubmitted(self) -> None:
        fake = FakeClient()
        book = make_book([make_group("group_1")])
        with transcript_env(book, fake) as env:
            fake.get_interrupt = True
            try:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            except SystemExit:
                pass
            fake.get_interrupt = False
            saved = json.loads(
                (env.book_dir / "00_transcripts_batch_state.json").read_text()
            )
            name = entries(saved)[0]["name"]
            fake.remote[name].state = SimpleNamespace(name="JOB_STATE_EXPIRED")
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
        self.assertEqual(len(fake.creates), 2)

    def test_not_found_saved_job_resubmitted(self) -> None:
        fake = FakeClient()
        book = make_book([make_group("group_1")])
        with transcript_env(book, fake) as env:
            fake.get_interrupt = True
            try:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            except SystemExit:
                pass
            fake.get_interrupt = False
            saved = json.loads(
                (env.book_dir / "00_transcripts_batch_state.json").read_text()
            )
            del fake.remote[entries(saved)[0]["name"]]
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
        self.assertEqual(len(fake.creates), 2)

    def test_mismatch_with_pending_keys_stops(self) -> None:
        fake = FakeClient()
        book = make_book([make_group("group_1")])
        with transcript_env(book, fake) as env:
            self._write_state(
                env.book_dir,
                {"1_group_1": "abc"},
                thinking="high",
            )
            with self.assertRaises(RuntimeError) as caught:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            self.assertIn("thinking", str(caught.exception))
            self.assertTrue((env.book_dir / "00_transcripts_batch_state.json").is_file())
        self.assertEqual(fake.creates, [])

    def test_pending_keys_different_section_stops(self) -> None:
        fake = FakeClient()
        groups = [make_group("group_1", section="1.2"), make_group("g3", section="1.3")]
        book = {"id": BOOK_ID, "pages": {"1": [groups[0]], "21": [groups[1]]}}
        book.update(make_book([]) )
        book["pages"] = {"1": [groups[0]], "21": [groups[1]]}
        with transcript_env(book, fake, papers=["1.2", "1.3"]) as env:
            self._write_state(env.book_dir, {"1_group_1": "abc"}, section="1.2")
            with self.assertRaises(RuntimeError) as caught:
                transcripts.build_transcripts(
                    BOOK_ID, "1.3", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            self.assertIn("1.2", str(caught.exception))
        self.assertEqual(fake.creates, [])

    def test_stale_fully_applied_state_is_dropped(self) -> None:
        fake = FakeClient()
        done = make_group(
            "group_1",
            transcript=stored_transcript(),
            generation={"batch": True, "batch_name": "batches/old"},
        )
        pending = make_group("g3", section="1.3")
        book = make_book([])
        book["pages"] = {"1": [done], "21": [pending]}
        with transcript_env(book, fake, papers=["1.2", "1.3"]) as env:
            self._write_state(env.book_dir, {"1_group_1": "abc"}, section="1.2")
            transcripts.build_transcripts(
                BOOK_ID, None, transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
            self.assertFalse((env.book_dir / "00_transcripts_batch_state.json").is_file())
            stored = load_book(env.book_path)
        self.assertTrue(stored["pages"]["21"][0].get("transcript"))

    def _digest(self, env, group: dict, page_key: str = "1") -> str:
        paper = next(ref for ref in env.refs if ref.number == group["section"])
        pdf_index = int(page_key) - paper.first
        job = transcripts.Job(
            page_key,
            group,
            paper,
            pdf_index,
            transcripts.shape_of(group),
            f"{page_key}_{group['id']}",
        )
        group_json = json.dumps(
            transcripts.model_group(group), ensure_ascii=False, indent=2
        )
        payload = {
            "prompt": "TRANSCRIBE THE GROUP",
            "group": group_json,
            "spec": '{"objective": "fractions"}',
            "pdf_sha256": transcripts.file_sha256(
                env.book_dir / transcripts.PAPERS_STAGE / f"{paper.stem}.pdf"
            ),
            "pdf_index": job.pdf_index,
            "bboxes": transcripts.group_crop_boxes(group),
            "page_max_side_px": transcripts.PAGE_MAX_SIDE_PX,
            "crop_pad_pt": transcripts.CROP_PAD_PT,
        }
        blob = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def test_resume_intersection_applies_only_pending(self) -> None:
        fake = FakeClient()
        groups = []
        for index in range(1, 66):
            gid = f"g{index}"
            if index <= 40:
                groups.append(
                    make_group(
                        gid,
                        transcript=stored_transcript(),
                        generation={"model": "old"},
                    )
                )
            else:
                groups.append(make_group(gid))
        book = make_book(groups)
        with transcript_env(book, fake) as env:
            keys = {f"1_{group['id']}": self._digest(env, group) for group in groups}
            rows = [
                types.InlinedResponse(
                    metadata={"item": f"1_{group['id']}"},
                    response=make_response(SINGLE_JSON),
                )
                for group in groups
            ]
            fake.remote["batches/saved"] = SimpleNamespace(
                name="batches/saved",
                state=SimpleNamespace(name="JOB_STATE_SUCCEEDED"),
                display_name="saved-display",
                dest=SimpleNamespace(inlined_responses=rows, file_name=None),
                error=None,
            )
            self._write_state(env.book_dir, keys)
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
            stored = load_book(env.book_path)
        self.assertEqual(fake.creates, [])
        pending_done = sum(
            1
            for group in stored["pages"]["1"]
            if int(group["id"][1:]) > 40 and group.get("transcript")
        )
        self.assertEqual(pending_done, 25)

    def test_keyboard_interrupt_keeps_state(self) -> None:
        fake = FakeClient()
        fake.get_interrupt = True
        book = make_book([make_group("group_1")])
        with transcript_env(book, fake) as env:
            with self.assertRaises(SystemExit) as caught:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            self.assertEqual(caught.exception.code, 130)
            self.assertTrue((env.book_dir / "00_transcripts_batch_state.json").is_file())

    def test_no_batch_applies_saved_job(self) -> None:
        fake = FakeClient()
        book = make_book([make_group("group_1"), make_group("group_2")])
        with transcript_env(book, fake) as env:
            fake.get_interrupt = True
            try:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            except SystemExit:
                pass
            fake.get_interrupt = False
            creates_before = len(fake.creates)
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=False
            )
            stored = load_book(env.book_path)
        self.assertEqual(len(fake.creates), creates_before)
        self.assertTrue(all(group.get("transcript") for group in stored["pages"]["1"]))
        self.assertEqual(len(fake.unary), 0)

    def test_update_does_not_count_old_transcripts(self) -> None:
        fake = FakeClient()
        old = make_group(
            "group_1",
            transcript=stored_transcript(),
            generation={"model": "old", "batch_name": "batches/old"},
        )
        book = make_book([old])
        with transcript_env(book, fake) as env:
            fake.get_interrupt = True
            try:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", True, batch=True
                )
            except SystemExit:
                pass
            fake.get_interrupt = False
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", True, batch=True
            )
            stored = load_book(env.book_path)
        gen = stored["pages"]["1"][0]["transcript_generation"]
        self.assertNotEqual(gen.get("batch_name"), "batches/old")
        self.assertTrue(gen.get("batch"))

    def test_changed_group_json_discards_row(self) -> None:
        fake = FakeClient()
        book = make_book([make_group("group_1", answer="1")])
        with transcript_env(book, fake) as env:
            fake.get_interrupt = True
            try:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            except SystemExit:
                pass
            fake.get_interrupt = False
            stored = load_book(env.book_path)
            stored["pages"]["1"][0]["exercises"][0]["answer"] = "999"
            env.book_path.write_text(
                json.dumps(stored, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
        self.assertEqual(len(fake.creates), 2)

    def test_state_never_holds_two_jobs(self) -> None:
        fake = FakeClient()
        book = make_book([make_group(f"g{i}") for i in range(3)])
        named_triples: list[list[str]] = []
        real_save = transcripts.BatchState.save

        def tracking_save(self, data):
            rows = entries(data)
            if len(rows) == 3 and all(row.get("name") for row in rows):
                named_triples.append([row["name"] for row in rows])
            return real_save(self, data)

        with (
            transcript_env(book, fake),
            patch("src.gemini_batch.BATCH_CHUNK", 1),
            patch.object(transcripts.BatchState, "save", tracking_save),
        ):
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
        self.assertTrue(named_triples)
        self.assertEqual(len(fake.creates), 3)


class JsonlAndSizingTests(unittest.TestCase):
    def test_oversize_chunk_uses_jsonl_and_deletes_files(self) -> None:
        fake = FakeClient()
        fake.jsonl_rows = [
            {"key": "1_group_1", "response": make_response(SINGLE_JSON).model_dump(mode="json")}
        ]
        book = make_book([make_group("group_1")])
        with (
            patch("src.gemini_batch.BATCH_MAX_INLINE_BYTES", 50),
            transcript_env(book, fake) as env,
        ):
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
            stored = load_book(env.book_path)
        self.assertEqual(fake.creates[0]["src"], "files/in-jsonl")
        self.assertIn("files/in-jsonl", fake.files.deleted)
        self.assertIn("files/out-jsonl", fake.files.deleted)
        self.assertTrue(stored["pages"]["1"][0].get("transcript"))

    def test_real_schema_sizes_and_jsonl_dumps(self) -> None:
        request = types.InlinedRequest(
            contents=["prompt", types.Part.from_bytes(data=PNG, mime_type="image/png"), "{}", "{}"],
            metadata={"item": "x"},
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=transcripts.SingleReply,
                thinking_config=types.ThinkingConfig(thinking_level="medium"),
            ),
        )
        size = request_payload_size(request)
        self.assertGreater(size, 0)
        payload = jsonl_request_payload(request, api_client=None)
        self.assertIsInstance(payload["generationConfig"]["responseSchema"], dict)
        self.assertTrue(issubclass(request.config.response_schema, BaseModel))


class LockTests(unittest.TestCase):
    def test_second_run_stops_on_lock(self) -> None:
        fake = FakeClient()
        book = make_book([make_group("group_1")])
        started = threading.Event()
        release = threading.Event()

        def blocking_get(*, name):
            started.set()
            release.wait(timeout=5)
            return fake.remote[name]

        with transcript_env(book, fake) as env:
            fake.batches.get = blocking_get
            errors: list[BaseException] = []

            def first():
                try:
                    transcripts.build_transcripts(
                        BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                    )
                except Exception as error:  # noqa: BLE001
                    errors.append(error)

            worker = threading.Thread(target=first)
            worker.start()
            self.assertTrue(started.wait(timeout=5))
            with self.assertRaises(RuntimeError) as caught:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            self.assertIn("lock", str(caught.exception).lower())
            release.set()
            worker.join(timeout=5)
            self.assertFalse(
                (env.book_dir / "00_transcripts_batch_state.json").is_file()
            )
        self.assertEqual(errors, [])

    def test_lock_released_after_keyboard_interrupt(self) -> None:
        fake = FakeClient()
        fake.get_interrupt = True
        book = make_book([make_group("group_1")])
        with transcript_env(book, fake) as env:
            with self.assertRaises(SystemExit) as caught:
                transcripts.build_transcripts(
                    BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
                )
            self.assertEqual(caught.exception.code, 130)
            fake.get_interrupt = False
            transcripts.build_transcripts(
                BOOK_ID, "1.2", transcripts.DEFAULT_MODEL, "medium", False, batch=True
            )
            self.assertTrue(load_book(env.book_path)["pages"]["1"][0].get("transcript"))


class KeepOnGroupTests(unittest.TestCase):
    def test_batch_fields_survive_keep_on_group(self) -> None:
        group = make_group(
            "group_1",
            transcript=stored_transcript(),
            generation={
                "model": transcripts.DEFAULT_MODEL,
                "batch": True,
                "batch_name": "batches/job-1",
            },
        )
        payload = {key: group[key] for key in KEEP_ON_GROUP if key in group}
        self.assertIn("transcript_generation", payload)
        self.assertTrue(payload["transcript_generation"]["batch"])
        self.assertEqual(payload["transcript_generation"]["batch_name"], "batches/job-1")
        self.assertNotIn("batch", payload)


class SplitSetCropTests(unittest.TestCase):
    def test_stitch_pngs_stacks_two_crops(self) -> None:
        top = Image.new("RGB", (4, 2), "red")
        bottom = Image.new("RGB", (3, 3), "blue")
        parts = []
        for image in (top, bottom):
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            parts.append(buffer.getvalue())
        stacked = Image.open(io.BytesIO(transcripts.stitch_pngs(parts)))
        self.assertEqual(stacked.size, (4, 2 + 3 + transcripts.STITCH_GAP_PX))

    def test_collect_jobs_keeps_continuation_page_crop(self) -> None:
        first = make_group("g1", kind="set")
        first["bboxes"] = [{"group": [[0.0, 0.0, 10.0, 10.0]]}]
        later = make_group("g1", kind="set")
        later["bboxes"] = [{"group": [[0.0, 50.0, 10.0, 90.0]]}]
        book = {"pages": {"5": [first], "6": [later]}}
        refs = [transcripts.PaperRef("1.2", STEM, 5, 3)]
        jobs, skipped = transcripts.collect_jobs(
            book, refs, None, False, Path("/tmp")
        )
        self.assertEqual(skipped, 0)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].pdf_index, 0)
        self.assertEqual(jobs[0].more_pages, ((1, ((0.0, 50.0, 10.0, 90.0),)),))


class PipelineFlagTests(unittest.TestCase):
    def test_forwards_only_to_transcript_stages(self) -> None:
        transcripts_stage = pipeline.STAGES_BY_NAME["transcripts"]
        audios_stage = pipeline.STAGES_BY_NAME["audios"]
        ar_transcripts = next(
            stage for stage in pipeline.TRANSLATE_STAGES if stage.name == "ar_transcripts"
        )
        kwargs = dict(
            section=None,
            update=False,
            model=None,
            thinking_level=None,
            workers=None,
            tts_model=None,
        )
        forwarded = pipeline.stage_command(
            transcripts_stage, BOOK_ID, **kwargs, transcripts_batch=False
        )
        omitted = pipeline.stage_command(
            transcripts_stage, BOOK_ID, **kwargs, transcripts_batch=None
        )
        ar_cmd = pipeline.stage_command(
            ar_transcripts, "mathematics_ar_eg07_t1", **kwargs, transcripts_batch=True
        )
        audios_cmd = pipeline.stage_command(
            audios_stage, BOOK_ID, **kwargs, transcripts_batch=False, tts_batch=None
        )
        self.assertIn("--no-batch", forwarded)
        self.assertNotIn("--no-batch", omitted)
        self.assertNotIn("--batch", omitted)
        self.assertIn("--batch", ar_cmd)
        self.assertNotIn("--no-batch", audios_cmd)
        self.assertNotIn("--batch", audios_cmd)
        membership = pipeline.stage_command(
            next(s for s in pipeline.TRANSLATE_STAGES if s.name == "ar_papers"),
            "mathematics_ar_eg08_t1",
            section="4",
            update=False,
            model=None,
            thinking_level=None,
            workers=None,
            tts_model=None,
            selection_from=Path("/tmp/en-papers"),
        )
        self.assertIn("--membership-from", membership)


if __name__ == "__main__":
    unittest.main()
