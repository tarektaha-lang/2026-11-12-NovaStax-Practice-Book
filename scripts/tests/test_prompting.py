from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from src.prompting import (  # noqa: E402
    Profile,
    assemble,
    profile_problems,
    prompt_subject,
    stages_written,
)
from prompts_builders.research_transcripts import glyph_rule  # noqa: E402


class ProfileTests(unittest.TestCase):
    def test_western_arabic_notation_is_allowed(self) -> None:
        meta = {
            "language_code": "ar",
            "numerals": "western",
            "notation": "arabic",
        }
        self.assertEqual(profile_problems(meta), [])
        profile = Profile.of(meta)
        self.assertFalse(profile.indic_digits)
        self.assertTrue(profile.rtl)
        self.assertEqual(profile.notation, "arabic")

    def test_indic_digits_with_latin_notation_are_refused(self) -> None:
        problems = profile_problems(
            {
                "language_code": "ar",
                "numerals": "arabic",
                "notation": "latin",
            }
        )
        self.assertTrue(problems)
        self.assertTrue(any("not a supported book" in problem for problem in problems))

    def test_every_stage_assembles_for_the_fourth_profile(self) -> None:
        meta = {
            "language_code": "ar",
            "language": "Arabic",
            "numerals": "western",
            "notation": "arabic",
            "subject": "الرياضيات",
        }
        for stage in stages_written():
            prompt = assemble(stage, meta)
            self.assertTrue(prompt.text)
            joined = " ".join(prompt.parts)
            if "{numerals_rules}" in (Path(SCRIPTS).parent / "prompts" / stage / "core.md").read_text(
                encoding="utf-8"
            ):
                self.assertIn("numerals/western.md", joined)
            if "{notation_rules}" in (Path(SCRIPTS).parent / "prompts" / stage / "core.md").read_text(
                encoding="utf-8"
            ):
                self.assertIn("notation/arabic.md", joined)

    def test_transcript_glyph_rule_keeps_western_digits_with_arabic_letters(self) -> None:
        text, parts = glyph_rule(Profile("ar", "western", "arabic"))
        self.assertIn("س", text)
        self.assertIn("0–9", text)
        self.assertNotIn("Never rewrite them as Western digits", text)
        self.assertTrue(any(path.name == "arabic.txt" and "notation" in path.parts for path in parts))
        self.assertTrue(any(path.name == "western.txt" and "numerals" in path.parts for path in parts))


class SubjectPromptTests(unittest.TestCase):
    def test_mathematics_uses_stage_root_core(self) -> None:
        meta = {
            "language_code": "en",
            "language": "English",
            "numerals": "western",
            "notation": "latin",
        }
        prompt = assemble("new_questions", meta, "mathematics_en_eg08_t1")
        self.assertIn("prompts/new_questions/core.md", prompt.parts[0])
        self.assertNotIn("subject/core.md", " ".join(prompt.parts))

    def test_arabic_overlay_includes_subject_blocks(self) -> None:
        meta = {
            "language_code": "ar",
            "language": "Arabic",
            "numerals": "western",
            "notation": "latin",
        }
        prompt = assemble("specs", meta, "arabic_ar_eg08_t1")
        joined = " ".join(prompt.parts)
        self.assertIn("subject/core.md", joined)
        self.assertIn("subject/arabic/glossary.md", joined)
        self.assertIn("language_lesson_practice_architecture.md", joined)

    def test_unsupported_subject_token_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            prompt_subject("biology_en_eg08_t1")


if __name__ == "__main__":
    unittest.main()
