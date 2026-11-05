"""Selecting sections never limits terminology or the glossary merge."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))


def _sections() -> list[dict]:
    return [
        {"number": "1.1", "stem": "01_01", "title": "One"},
        {"number": "4.5", "stem": "04_05", "title": "Four"},
        {"number": "4.6", "stem": "04_06", "title": "Five"},
    ]


class GlossaryScopeTests(unittest.TestCase):
    def _run(self, stored: set[str], *, fail: str | None = None) -> dict:
        import translate

        seen: list[str] = []
        extracted: list[str] = []
        merged: list[str] = []
        translated: list[str] = []

        def fake_term(run, section, drawings, force=False, allow_translate=True):
            del run, drawings, force, allow_translate
            seen.append(section["number"])
            if section["number"] == fail:
                raise RuntimeError("terminology boom")
            if section["number"] not in stored:
                extracted.append(section["number"])
                line = f"  {section['number']}: extracted terminology"
            else:
                line = f"  {section['number']}: terminology already on disk"
            return {
                "fragment": {"section": section["number"], "observations": []},
                "line": line,
            }

        def fake_merge(run, paths, fragments, force=False):
            del run, paths, force
            merged.extend(str(row.get("section")) for row in fragments)
            return {"entries": [], "status": "ready", "merge_key": "k"}, "ran"

        def fake_translate(run, row, drawings, prepared):
            del run, drawings, prepared
            translated.append(row["number"])
            spec = Path(translate.OUTPUT_DIR) / "mathematics_ar_scope_t1" / "03_section_specs"
            spec.mkdir(parents=True, exist_ok=True)
            (spec / f"{row['stem']}.json").write_text(
                json.dumps({"number": row["number"], "title": "t", "module_path": "m", "spec": {}}),
                encoding="utf-8",
            )
            return [f"  {row['number']}: translated"]

        document = {"book_id": "mathematics_en_scope_t1", "sections": _sections()}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            accepted = root / "mathematics_en_scope_t1" / "09_section_questions_review"
            accepted.mkdir(parents=True)
            (accepted / "04_05.json").write_text("{}", encoding="utf-8")
            with (
                patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}),
                patch("translate.OUTPUT_DIR", root),
                patch("translate.pair_books", return_value="mathematics_ar_scope_t1"),
                patch("translate.require_english_review", return_value=document),
                patch("translate.book_metadata", return_value={
                    "language_code": "ar", "numerals": "western", "notation": "latin",
                }),
                patch("translate.split_book"),
                patch("translate.library", return_value={}),
                patch("translate.book_spans", return_value=[]),
                patch("translate.number_pad_width", return_value=2),
                patch("translate.genai.Client"),
                patch("translate.assemble") as assemble,
                patch("translate.bind_profile", side_effect=lambda prompt, meta: prompt),
                patch("translate.ensure_terminology", side_effect=fake_term),
                patch("translate.ensure_book_glossary", side_effect=fake_merge),
                patch("translate.translate_section", side_effect=fake_translate),
                patch("translate.write_applied_book"),
                patch("translate.exercises_complete", return_value=True),
            ):
                assemble.return_value = type("Prompt", (), {"text": "prompt", "parts": []})()
                code = translate.translate_book(
                    "mathematics_en_scope_t1",
                    "4.5",
                    "gemini-3.8-flash",
                    "medium",
                    False,
                    1,
                )
            report_path = root / "mathematics_ar_scope_t1" / "00_translation_state" / "translation_report.json"
            report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
        return {
            "code": code,
            "seen": seen,
            "extracted": extracted,
            "merged": merged,
            "translated": translated,
            "report": report,
        }

    def test_a_section_selection_still_maps_the_whole_book(self) -> None:
        result = self._run(stored={"1.1"})
        self.assertEqual(result["seen"], ["1.1", "4.5", "4.6"])
        self.assertEqual(result["extracted"], ["4.5", "4.6"])
        self.assertEqual(result["merged"], ["1.1", "4.5", "4.6"])
        self.assertEqual(result["translated"], ["4.5"])
        self.assertEqual(result["report"]["terminology"]["extracted"], ["4.5", "4.6"])
        reused = result["report"]["terminology"]["reused"]
        self.assertEqual(len(reused), 1)
        self.assertTrue(reused[0].startswith("1.1"))
        self.assertEqual(result["report"]["glossary"]["action"], "ran")

    def test_stored_terminology_makes_no_extraction(self) -> None:
        result = self._run(stored={"1.1", "4.5", "4.6"})
        self.assertEqual(result["seen"], ["1.1", "4.5", "4.6"])
        self.assertEqual(result["extracted"], [])
        self.assertEqual(result["report"]["terminology"]["extracted"], [])
        self.assertEqual(result["report"]["terminology"]["calls"], 0)
        self.assertEqual(result["merged"], ["1.1", "4.5", "4.6"])

    def test_a_failed_section_stops_before_translation(self) -> None:
        result = self._run(stored=set(), fail="1.1")
        self.assertEqual(result["code"], 1)
        self.assertEqual(result["merged"], [])
        self.assertEqual(result["translated"], [])
        self.assertIn("1.1", result["report"]["terminology"]["failed"])
        self.assertEqual(result["report"]["glossary"]["action"], "not_run")


if __name__ == "__main__":
    unittest.main()
