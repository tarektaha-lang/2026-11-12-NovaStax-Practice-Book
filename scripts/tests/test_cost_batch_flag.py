from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from cost import load_prices, read_call  # noqa: E402


def _log(**overrides) -> dict:
    document = {
        "model": "gemini-3.8-flash",
        "step": "translation_review",
        "attempt": 1,
        "duration_seconds": 1,
        "timestamp": "2026-10-22T11:20:31+00:00",
        "usage": {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
        "section": "1.4",
        "page_number": "1.4_review_1",
        "status": "success",
        "book_id": "book_x",
    }
    document.update(overrides)
    return document


def _read(document: dict):
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "call" / "call.json"
        path.parent.mkdir()
        path.write_text(json.dumps(document), encoding="utf-8")
        call = read_call(path, "book_x", problems)
    return call, problems


class BatchFlagTests(unittest.TestCase):
    def test_review_chunk_number_is_standard(self) -> None:
        call, problems = _read(_log(batch=1))
        self.assertEqual(problems, [])
        self.assertFalse(call.batch)
        self.assertAlmostEqual(call.cost(load_prices()), 0.75 + 3.75)

    def test_true_batch_is_batch_rate(self) -> None:
        call, problems = _read(_log(batch=True, batch_name="batches/job-1"))
        self.assertEqual(problems, [])
        self.assertTrue(call.batch)
        self.assertAlmostEqual(call.cost(load_prices()), 0.375 + 1.875)

    def test_translate_review_context_uses_review_batch(self) -> None:
        source = (SCRIPTS / "translate.py").read_text(encoding="utf-8")
        self.assertIn('"review_batch": batch_number', source)
        self.assertNotIn('"batch": batch_number', source)

    def test_flat_rate_model_refuses_cached_tokens(self) -> None:
        call, problems = _read(
            _log(
                model="gemini-2.5-flash-preview-tts",
                usage={"input_tokens": 100, "cached_input_tokens": 50},
            )
        )
        self.assertEqual(problems, [])
        with self.assertRaises(RuntimeError):
            call.cost(load_prices())

    def test_flat_rate_model_without_cache_still_prices(self) -> None:
        call, _ = _read(_log(model="gemini-2.5-flash-preview-tts"))
        self.assertAlmostEqual(call.cost(load_prices()), 0.5 + 10.0)


if __name__ == "__main__":
    unittest.main()
