from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from translation_metrics import summarize  # noqa: E402


class FirstPassRejectionTests(unittest.TestCase):
    def test_correction_is_not_a_first_pass_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / "00_translation_state"
            state.mkdir()
            (state / "01_01.json").write_text(
                json.dumps(
                    {
                        "version": 4,
                        "section": "1.1",
                        "groups": {
                            "group_1": {"path": "batch"},
                            "group_2": {"path": "salvage1"},
                            "group_3": {"path": "correction"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            (root / "00_api_call_logs").mkdir()
            summary = summarize(root)
        rejection = summary["first_pass_rejection"]
        self.assertEqual(rejection["groups"], 3)
        self.assertEqual(rejection["not_batch"], 1)
        self.assertAlmostEqual(rejection["rate"], 1 / 3)

    def test_batch_then_correction_is_accepted_on_the_first_pass(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / "00_translation_state"
            state.mkdir()
            (state / "03_02.json").write_text(
                json.dumps(
                    {
                        "version": 4,
                        "section": "3.2",
                        "groups": {
                            "group_109": {
                                "path": "correction",
                                "first_path": "batch",
                            },
                            "group_2": {
                                "path": "correction",
                                "first_path": "salvage1",
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            (root / "00_api_call_logs").mkdir()
            summary = summarize(root)
        rejection = summary["first_pass_rejection"]
        self.assertEqual(rejection["groups"], 2)
        self.assertEqual(rejection["not_batch"], 1)
        self.assertAlmostEqual(rejection["rate"], 0.5)

    def test_cache_storage_is_priced_from_token_hours(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            logs = root / "00_api_call_logs" / "2026-10-21T12-00-00.000Z_cache_1_cache_storage"
            logs.mkdir(parents=True)
            (logs / "call.json").write_text(
                json.dumps(
                    {
                        "timestamp": "2026-10-21T12:00:00+00:00",
                        "model": "gemini-3.8-flash",
                        "step": "cache_storage",
                        "status": "success",
                        "usage": {
                            "cache_storage_tokens": 1_000_000,
                            "cache_storage_seconds": 3600,
                        },
                    }
                ),
                encoding="utf-8",
            )
            summary = summarize(root)
        self.assertAlmostEqual(summary["steps"]["cache_storage"]["usd"], 0.5)
        self.assertAlmostEqual(summary["cost_usd"], 0.5)
