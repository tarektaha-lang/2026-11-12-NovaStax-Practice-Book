"""Golden regression for audios.py Batch and unary paths.

Records batches.create arguments, WAV bytes and names, working-JSON audio
rows, call.json (batch flag and batch_name) and the cost.py tier. Polling is
not asserted: time.sleep is a no-op. Fixtures live under
scripts/tests/fixtures/audios_batch/. They were captured against today's
audios.py and must stay byte-identical through the later refactor.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import wave
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from google.genai import types  # noqa: E402

import audios  # noqa: E402
from cost import load_prices, read_call  # noqa: E402
from src import api_logger  # noqa: E402


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "audios_batch"
BOOK_ID = "fixture_en_eg_t1"
FROZEN = datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc)
JOB_NAME = "batches/golden-audios-job"


def tiny_wav(seed: int) -> bytes:
    """A short valid WAV whose bytes depend on `seed` (0.1s so duration is not zero)."""
    pcm = bytes([(seed + index) % 256 for index in range(4800)])
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(pcm)
    return buffer.getvalue()


WAV_BY_ITEM = {
    "0001_group_1": tiny_wav(1),
    "0001_group_2_stem": tiny_wav(2),
    "0001_group_2_ex_1": tiny_wav(3),
    "0001_group_2_ex_2": tiny_wav(4),
}


def fixture_book() -> dict:
    return {
        "country_code": "eg",
        "pages": {
            "0001": [
                {
                    "id": "group_1",
                    "kind": "exercise",
                    "section": "1.2",
                    "exercises": [{"id": "group_1"}],
                    "taps": [{"id": "group_1", "md5": "tap111aaaaaaaaaaaaaaaaaaaaaaaaaa"}],
                    "transcript": {
                        "paragraphs": [{"sentences": ["Hello world."]}]
                    },
                },
                {
                    "id": "group_2",
                    "kind": "set",
                    "section": "1.2",
                    "exercises": [{"id": "ex_1"}, {"id": "ex_2"}],
                    "taps": [
                        {"id": "stem", "md5": "tap222bbbbbbbbbbbbbbbbbbbbbbbbbb"},
                        {"id": "ex_1", "md5": "tap333cccccccccccccccccccccccccc"},
                        {"id": "ex_2", "md5": "tap444dddddddddddddddddddddddddd"},
                    ],
                    "transcript": {
                        "stem": {"paragraphs": [{"sentences": ["Shared stem."]}]},
                        "parts": [
                            {
                                "id": "ex_1",
                                "paragraphs": [{"sentences": ["Part one."]}],
                            },
                            {
                                "id": "ex_2",
                                "paragraphs": [{"sentences": ["Part two."]}],
                            },
                        ],
                    },
                },
            ]
        },
    }


def tts_response(wav: bytes, input_tokens: int, output_tokens: int) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    parts=[
                        types.Part(
                            inline_data=types.Blob(data=wav, mime_type="audio/wav")
                        )
                    ]
                )
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=input_tokens,
            candidates_token_count=output_tokens,
            total_token_count=input_tokens + output_tokens,
        ),
    )


class FakeBatches:
    def __init__(self) -> None:
        self.create_calls: list[dict] = []
        self.jobs: dict[str, object] = {}

    def create(self, *, model, src, config=None):
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
        job = SimpleNamespace(
            name=JOB_NAME,
            state=SimpleNamespace(name="JOB_STATE_SUCCEEDED"),
            dest=SimpleNamespace(inlined_responses=rows, file_name=None),
            error=None,
        )
        self.jobs[JOB_NAME] = job
        return SimpleNamespace(name=JOB_NAME)

    def get(self, name):
        return self.jobs[name]


class FakeModels:
    def generate_content(self, *, model, contents, config):
        spoken = contents[0].parts[0].text
        by_spoken = {
            "Hello world.": "0001_group_1",
            "Shared stem.": "0001_group_2_stem",
            "Part one.": "0001_group_2_ex_1",
            "Part two.": "0001_group_2_ex_2",
        }
        item = by_spoken[spoken]
        return tts_response(WAV_BY_ITEM[item], 20, 200)


class FakeClient:
    def __init__(self) -> None:
        self.batches = FakeBatches()
        self.models = FakeModels()


def dump_request(request: types.InlinedRequest) -> dict:
    return request.model_dump(mode="json", exclude_none=True)


def dump_create(call: dict) -> dict:
    return {
        "model": call["model"],
        "src": [dump_request(row) for row in call["src"]],
        "config": call["config"],
    }


def strip_call(document: dict) -> dict:
    """call.json without wall-clock duration, which is not part of the golden contract."""
    cleaned = dict(document)
    cleaned.pop("duration_seconds", None)
    return cleaned


def audio_rows(book: dict) -> list[dict]:
    rows = []
    for page_key, groups in sorted(book["pages"].items()):
        for group in groups:
            for segment in group.get("audio") or []:
                generation = dict(segment.get("generation") or {})
                generation.pop("duration_seconds", None)
                generation.pop("batch_name", None)
                rows.append(
                    {
                        "page_key": page_key,
                        "group_id": group["id"],
                        "id": segment["id"],
                        "tap_md5": segment["tap_md5"],
                        "file": segment["file"],
                        "duration_seconds": segment["duration_seconds"],
                        "generation": generation,
                    }
                )
    return rows


def wav_inventory(taps: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for wav in sorted(taps.glob("*/01.wav")):
        found[f"{wav.parent.name}/01.wav"] = wav.read_bytes().hex()
    return found


def calls_by_item(logs: Path) -> dict[str, dict]:
    by_item: dict[str, dict] = {}
    for folder in sorted(logs.glob("*_audios")):
        path = folder / "call.json"
        if not path.is_file():
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        by_item[str(document.get("page_number"))] = strip_call(document)
    return by_item


def capture(batch: bool) -> dict:
    fake = FakeClient()
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
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(audios, "OUTPUT_DIR", root),
            patch.object(audios, "working_json", lambda book_id: root / book_id / f"{book_id}.json"),
            patch.object(audios, "MAX_WORKERS", 1),
            patch.object(audios.genai, "Client", return_value=fake),
            patch.object(api_logger.ApiLogger, "_compute_ntp_offset", staticmethod(lambda: 0.0)),
            patch.object(api_logger.ApiLogger, "utc_now", lambda self: FROZEN),
            patch.object(audios.time, "sleep", lambda _seconds: None),
            patch.object(audios.time, "perf_counter", lambda: 1000.0),
            patch.object(audios, "new_run_id", lambda: "golden00"),
        ):
            audios.build_audios(BOOK_ID, "1.2", audios.DEFAULT_MODEL, False, batch=batch)

        book = json.loads(book_path.read_text(encoding="utf-8"))
        taps = book_dir / "14_taps"
        logs = book_dir / "00_api_call_logs"
        prices = load_prices()
        costs = []
        problems: list[str] = []
        for folder_path in sorted(logs.glob("*_audios")):
            call = read_call(folder_path / "call.json", BOOK_ID, problems)
            if call is not None:
                costs.append(
                    {
                        "item": call.item,
                        "batch": call.batch,
                        "model_label": call.model_label,
                        "cost": call.cost(prices),
                    }
                )
        payload = {
            "audio_rows": audio_rows(book),
            "wavs": wav_inventory(taps),
            "calls": calls_by_item(logs),
            "costs": costs,
            "problems": problems,
        }
        if batch:
            payload["create"] = [dump_create(call) for call in fake.batches.create_calls]
        return payload


def write_golden(name: str, payload: dict) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_golden(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class AudiosBatchRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.batch = capture(True)
        cls.unary = capture(False)
        if os.environ.get("UPDATE_AUDIOS_GOLDEN"):
            write_golden("batch.json", cls.batch)
            write_golden("unary.json", cls.unary)

    def test_golden_fixtures_exist(self) -> None:
        self.assertTrue((FIXTURES / "batch.json").is_file())
        self.assertTrue((FIXTURES / "unary.json").is_file())

    def test_batch_matches_golden(self) -> None:
        self.assertEqual(self.batch, load_golden("batch.json"))

    def test_unary_matches_golden(self) -> None:
        self.assertEqual(self.unary, load_golden("unary.json"))

    def test_batch_logs_set_batch_true_and_batch_name(self) -> None:
        for item, document in self.batch["calls"].items():
            self.assertTrue(document.get("batch"), item)
            self.assertEqual(document.get("batch_name"), JOB_NAME, item)

    def test_batch_cost_uses_batch_periods(self) -> None:
        self.assertTrue(self.batch["costs"])
        for row in self.batch["costs"]:
            self.assertTrue(row["batch"])
            self.assertEqual(row["model_label"], "gemini-3.8-flash-tts (batch)")
            self.assertGreater(row["cost"], 0)

    def test_unary_cost_uses_standard_periods(self) -> None:
        self.assertTrue(self.unary["costs"])
        for row in self.unary["costs"]:
            self.assertFalse(row["batch"])
            self.assertEqual(row["model_label"], "gemini-3.8-flash-tts")

    def test_wav_names_are_tap_md5_slash_01_wav(self) -> None:
        for name in self.batch["wavs"]:
            self.assertTrue(name.endswith("/01.wav"))
            self.assertEqual(len(name.split("/")[0]), 32)


if __name__ == "__main__":
    unittest.main()
