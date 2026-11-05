from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from src.academic_year import (  # noqa: E402
    REQUIRED_ACADEMIC_YEAR,
    apply_required_year,
    ensure_academic_year,
)


class AcademicYearTests(unittest.TestCase):
    def test_rewrites_stale_year_and_keeps_the_rest_of_the_tag(self) -> None:
        source = (
            '<book subject="Mathematics" academic_year="25-26" country_code="eg">\n'
            "  <module/>\n"
            "</book>\n"
        )
        rewritten, old, changed = apply_required_year(source)
        self.assertTrue(changed)
        self.assertEqual(old, "25-26")
        self.assertIn(f'academic_year="{REQUIRED_ACADEMIC_YEAR}"', rewritten)
        self.assertIn('subject="Mathematics"', rewritten)
        self.assertIn('country_code="eg"', rewritten)
        self.assertNotIn('academic_year="25-26"', rewritten)

    def test_leaves_this_year_untouched(self) -> None:
        source = f'<book academic_year="{REQUIRED_ACADEMIC_YEAR}" grade="8">\n</book>\n'
        rewritten, old, changed = apply_required_year(source)
        self.assertFalse(changed)
        self.assertEqual(old, REQUIRED_ACADEMIC_YEAR)
        self.assertEqual(rewritten, source)

    def test_inserts_the_attribute_when_the_book_has_none(self) -> None:
        source = '<book subject="Mathematics" grade="8">\n</book>\n'
        rewritten, old, changed = apply_required_year(source)
        self.assertTrue(changed)
        self.assertEqual(old, "")
        self.assertIn(f'academic_year="{REQUIRED_ACADEMIC_YEAR}"', rewritten)
        self.assertIn('subject="Mathematics"', rewritten)

    def test_ensure_edits_the_file_and_writes_the_repo_log(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            toc = root / "book.xml"
            log_path = root / "academic_year_edits.txt"
            toc.write_text(
                '<book subject="Mathematics" academic_year="25-26">\n</book>\n',
                encoding="utf-8",
            )
            previous = ensure_academic_year(
                "mathematics_en_eg08_t1", toc=toc, log_path=log_path
            )
            self.assertEqual(previous, "25-26")
            self.assertIn(
                f'academic_year="{REQUIRED_ACADEMIC_YEAR}"',
                toc.read_text(encoding="utf-8"),
            )
            logged = log_path.read_text(encoding="utf-8")
            self.assertIn("The tool modified academic_year for this year.", logged)
            self.assertIn("mathematics_en_eg08_t1", logged)
            self.assertIn("25-26 -> 26-27", logged)
            self.assertIsNone(
                ensure_academic_year(
                    "mathematics_en_eg08_t1", toc=toc, log_path=log_path
                )
            )
            self.assertEqual(log_path.read_text(encoding="utf-8"), logged)


if __name__ == "__main__":
    unittest.main()
