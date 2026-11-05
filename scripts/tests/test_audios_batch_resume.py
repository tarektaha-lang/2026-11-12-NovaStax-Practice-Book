"""Resume, stale-state, lock and edited-transcript tests for audios Batch."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import audios  # noqa: E402
from tests.test_audios_batch_regression import (  # noqa: E402
    BOOK_ID,
    FROZEN,
    WAV_BY_ITEM,
    FakeClient,
    audio_rows,
    capture,
    fixture_book,
    load_golden,
)
from src import api_logger  # noqa: E402


class ResumeGoldenTests(unittest.TestCase):
    def test_resume_matches_golden_without_second_create(self) -> None:
        fake = FakeClient()
        original_get = fake.batches.get
        calls = {"n": 0}

        def interrupting_get(name):
            calls["n"] += 1
            if calls["n"] == 1:
                raise KeyboardInterrupt
            return original_get(name)

        fake.batches.get = interrupting_get
        with tempfile.TemporaryDirectory(dir=str(audios.PROJECT_ROOT)) as folder:
            root = Path(folder)
            book_dir = root / BOOK_ID
            book_dir.mkdir()
            book_path = book_dir / f"{BOOK_ID}.json"
            book_path.write_text(
                json.dumps(fixture_book(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            env = {"GEMINI_API_KEY": "test-key"}
            patches = (
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
                patch.object(audios.time, "sleep", lambda _seconds: None),
                patch.object(audios.time, "perf_counter", lambda: 1000.0),
                patch.object(audios.os, "_exit", side_effect=SystemExit(130)),
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9]:
                try:
                    audios.build_audios(
                        BOOK_ID, "1.2", audios.DEFAULT_MODEL, False, batch=True
                    )
                except SystemExit:
                    pass
                self.assertEqual(len(fake.batches.create_calls), 1)
                self.assertTrue(
                    (book_dir / "00_audios_batch_state.json").is_file()
                )
                audios.build_audios(
                    BOOK_ID, "1.2", audios.DEFAULT_MODEL, False, batch=True
                )
            book = json.loads(book_path.read_text(encoding="utf-8"))
            payload = {"audio_rows": audio_rows(book)}
        self.assertEqual(len(fake.batches.create_calls), 1)
        golden = load_golden("batch.json")
        self.assertEqual(payload["audio_rows"], golden["audio_rows"])

    def test_golden_fixtures_still_match_live_capture(self) -> None:
        live = capture(True)
        self.assertEqual(live, load_golden("batch.json"))


class StaleAndMismatchTests(unittest.TestCase):
    def test_stale_fully_applied_state_is_dropped(self) -> None:
        fake = FakeClient()
        book = fixture_book()
        book["pages"]["0001"][0]["audio"] = [{"id": "group_1", "file": "01.wav"}]
        book["pages"]["0001"][1]["audio"] = [
            {"id": "stem", "file": "01.wav"},
            {"id": "ex_1", "file": "01.wav"},
            {"id": "ex_2", "file": "01.wav"},
        ]
        book["pages"]["0002"] = [
            {
                "id": "later",
                "kind": "exercise",
                "section": "1.3",
                "exercises": [{"id": "later"}],
                "taps": [{"id": "later", "md5": "tap555eeeeeeeeeeeeeeeeeeeeeeeeee"}],
                "transcript": {"paragraphs": [{"sentences": ["Hello world."]}]},
            }
        ]
        WAV_BY_ITEM["0002_later"] = WAV_BY_ITEM["0001_group_1"]
        with tempfile.TemporaryDirectory(dir=str(audios.PROJECT_ROOT)) as folder:
            root = Path(folder)
            book_dir = root / BOOK_ID
            taps = book_dir / "14_taps"
            book_dir.mkdir()
            (taps / "tap111aaaaaaaaaaaaaaaaaaaaaaaaaa").mkdir(parents=True)
            (taps / "tap111aaaaaaaaaaaaaaaaaaaaaaaaaa" / "01.wav").write_bytes(
                WAV_BY_ITEM["0001_group_1"]
            )
            (taps / "tap222bbbbbbbbbbbbbbbbbbbbbbbbbb").mkdir(parents=True)
            (taps / "tap222bbbbbbbbbbbbbbbbbbbbbbbbbb" / "01.wav").write_bytes(
                WAV_BY_ITEM["0001_group_2_stem"]
            )
            (taps / "tap333cccccccccccccccccccccccccc").mkdir(parents=True)
            (taps / "tap333cccccccccccccccccccccccccc" / "01.wav").write_bytes(
                WAV_BY_ITEM["0001_group_2_ex_1"]
            )
            (taps / "tap444dddddddddddddddddddddddddd").mkdir(parents=True)
            (taps / "tap444dddddddddddddddddddddddddd" / "01.wav").write_bytes(
                WAV_BY_ITEM["0001_group_2_ex_2"]
            )
            book_path = book_dir / f"{BOOK_ID}.json"
            book_path.write_text(
                json.dumps(book, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            state = {
                "stage": "audios",
                "book_id": BOOK_ID,
                "model": audios.DEFAULT_MODEL,
                "section": "1.2",
                "update": False,
                "job": {
                    "name": "batches/old",
                    "display_name": "old",
                    "attempt": 1,
                    "keys": {
                        "0001_group_1": "x",
                        "0001_group_2_stem": "x",
                        "0001_group_2_ex_1": "x",
                        "0001_group_2_ex_2": "x",
                    },
                },
            }
            (book_dir / "00_audios_batch_state.json").write_text(
                json.dumps(state) + "\n", encoding="utf-8"
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
                patch.object(audios.genai, "Client", return_value=fake),
                patch.object(
                    api_logger.ApiLogger,
                    "_compute_ntp_offset",
                    staticmethod(lambda: 0.0),
                ),
                patch.object(api_logger.ApiLogger, "utc_now", lambda self: FROZEN),
                patch.object(audios.time, "sleep", lambda _seconds: None),
            ):
                audios.build_audios(
                    BOOK_ID, None, audios.DEFAULT_MODEL, False, batch=True
                )
            self.assertFalse((book_dir / "00_audios_batch_state.json").is_file())
            stored = json.loads(book_path.read_text(encoding="utf-8"))
        self.assertTrue(stored["pages"]["0002"][0].get("audio"))

    def test_pending_keys_different_section_stops(self) -> None:
        fake = FakeClient()
        book = fixture_book()
        book["pages"]["0002"] = [
            {
                "id": "other",
                "kind": "exercise",
                "section": "1.3",
                "exercises": [{"id": "other"}],
                "taps": [{"id": "other", "md5": "tap666ffffffffffffffffffffffffff"}],
                "transcript": {"paragraphs": [{"sentences": ["Hello world."]}]},
            }
        ]
        with tempfile.TemporaryDirectory(dir=str(audios.PROJECT_ROOT)) as folder:
            root = Path(folder)
            book_dir = root / BOOK_ID
            book_dir.mkdir()
            (book_dir / f"{BOOK_ID}.json").write_text(
                json.dumps(book, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            state = {
                "stage": "audios",
                "book_id": BOOK_ID,
                "model": audios.DEFAULT_MODEL,
                "section": "1.2",
                "update": False,
                "job": {
                    "name": "batches/saved",
                    "display_name": "saved",
                    "attempt": 1,
                    "keys": {"0001_group_1": "abc"},
                },
            }
            (book_dir / "00_audios_batch_state.json").write_text(
                json.dumps(state) + "\n", encoding="utf-8"
            )
            with (
                patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False),
                patch.object(audios, "OUTPUT_DIR", root),
                patch.object(
                    audios,
                    "working_json",
                    lambda book_id: root / book_id / f"{book_id}.json",
                ),
            ):
                with self.assertRaises(RuntimeError) as caught:
                    audios.build_audios(
                        BOOK_ID, "1.3", audios.DEFAULT_MODEL, False, batch=True
                    )
            self.assertIn("1.2", str(caught.exception))
        self.assertEqual(fake.batches.create_calls, [])


class EditedTranscriptTests(unittest.TestCase):
    def test_changed_spoken_text_is_resubmitted(self) -> None:
        fake = FakeClient()
        original_get = fake.batches.get
        calls = {"n": 0}

        def interrupting_get(name):
            calls["n"] += 1
            if calls["n"] == 1:
                raise KeyboardInterrupt
            return original_get(name)

        fake.batches.get = interrupting_get
        book = fixture_book()
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
                patch.object(audios.genai, "Client", return_value=fake),
                patch.object(
                    api_logger.ApiLogger,
                    "_compute_ntp_offset",
                    staticmethod(lambda: 0.0),
                ),
                patch.object(api_logger.ApiLogger, "utc_now", lambda self: FROZEN),
                patch.object(audios.time, "sleep", lambda _seconds: None),
                patch.object(audios.os, "_exit", side_effect=SystemExit(130)),
            ):
                try:
                    audios.build_audios(
                        BOOK_ID, "1.2", audios.DEFAULT_MODEL, False, batch=True
                    )
                except SystemExit:
                    pass
                stored = json.loads(book_path.read_text(encoding="utf-8"))
                stored["pages"]["0001"][0]["transcript"]["paragraphs"][0][
                    "sentences"
                ] = ["Edited later."]
                book_path.write_text(
                    json.dumps(stored, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                WAV_BY_ITEM["0001_group_1"] = WAV_BY_ITEM["0001_group_1"]
                with patch.dict(
                    WAV_BY_ITEM, {"0001_group_1": WAV_BY_ITEM["0001_group_1"]}
                ):
                    # The edited spoken text still maps through metadata item,
                    # not the spoken-text unary table.
                    audios.build_audios(
                        BOOK_ID, "1.2", audios.DEFAULT_MODEL, False, batch=True
                    )
        self.assertEqual(len(fake.batches.create_calls), 2)


class LockTests(unittest.TestCase):
    def test_second_run_is_refused(self) -> None:
        fake = FakeClient()
        started = threading.Event()
        release = threading.Event()
        original_get = fake.batches.get

        def blocking_get(name):
            started.set()
            release.wait(timeout=5)
            return original_get(name)

        fake.batches.get = blocking_get
        book = fixture_book()
        with tempfile.TemporaryDirectory(dir=str(audios.PROJECT_ROOT)) as folder:
            root = Path(folder)
            book_dir = root / BOOK_ID
            book_dir.mkdir()
            (book_dir / f"{BOOK_ID}.json").write_text(
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
                patch.object(audios.genai, "Client", return_value=fake),
                patch.object(
                    api_logger.ApiLogger,
                    "_compute_ntp_offset",
                    staticmethod(lambda: 0.0),
                ),
                patch.object(api_logger.ApiLogger, "utc_now", lambda self: FROZEN),
                patch.object(audios.time, "sleep", lambda _seconds: None),
            ):
                errors: list[BaseException] = []

                def first():
                    try:
                        audios.build_audios(
                            BOOK_ID, "1.2", audios.DEFAULT_MODEL, False, batch=True
                        )
                    except Exception as error:  # noqa: BLE001
                        errors.append(error)

                worker = threading.Thread(target=first)
                worker.start()
                self.assertTrue(started.wait(timeout=5))
                with self.assertRaises(RuntimeError) as caught:
                    audios.build_audios(
                        BOOK_ID, "1.2", audios.DEFAULT_MODEL, False, batch=True
                    )
                self.assertIn("lock", str(caught.exception).lower())
                release.set()
                worker.join(timeout=5)
        self.assertEqual(errors, [])


class ExpiredJobTests(unittest.TestCase):
    def test_expired_saved_job_is_resubmitted(self) -> None:
        fake = FakeClient()
        original_get = fake.batches.get
        calls = {"n": 0}

        def first_interrupt_then_expired(name):
            calls["n"] += 1
            if calls["n"] == 1:
                raise KeyboardInterrupt
            job = original_get(name)
            if calls["n"] == 2:
                job.state = SimpleNamespace(name="JOB_STATE_EXPIRED")
            return job

        fake.batches.get = first_interrupt_then_expired
        with tempfile.TemporaryDirectory(dir=str(audios.PROJECT_ROOT)) as folder:
            root = Path(folder)
            book_dir = root / BOOK_ID
            book_dir.mkdir()
            (book_dir / f"{BOOK_ID}.json").write_text(
                json.dumps(fixture_book(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
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
                patch.object(audios.genai, "Client", return_value=fake),
                patch.object(
                    api_logger.ApiLogger,
                    "_compute_ntp_offset",
                    staticmethod(lambda: 0.0),
                ),
                patch.object(api_logger.ApiLogger, "utc_now", lambda self: FROZEN),
                patch.object(audios.time, "sleep", lambda _seconds: None),
                patch.object(audios.os, "_exit", side_effect=SystemExit(130)),
            ):
                try:
                    audios.build_audios(
                        BOOK_ID, "1.2", audios.DEFAULT_MODEL, False, batch=True
                    )
                except SystemExit:
                    pass
                audios.build_audios(
                    BOOK_ID, "1.2", audios.DEFAULT_MODEL, False, batch=True
                )
        self.assertEqual(len(fake.batches.create_calls), 2)


if __name__ == "__main__":
    unittest.main()
