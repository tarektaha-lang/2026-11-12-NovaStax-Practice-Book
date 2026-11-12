"""Cover year line: `_pN` is a part only when term is empty."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from cover.cover import YEAR_BULLET, YEAR_DASH, cover_lines, read_wording  # noqa: E402
from src.prompting import Profile  # noqa: E402


def _meta(**overrides: str) -> dict[str, str]:
    base = {
        "subject": "Mathematics",
        "country": "India",
        "country_code": "in",
        "grade": "9",
        "term": "0",
        "academic_year": "26-27",
        "language_code": "en",
        "numerals": "western",
        "notation": "latin",
    }
    base.update(overrides)
    return base


def _year(meta: dict[str, str], book_id: str) -> str:
    return cover_lines(meta, Profile.of(meta), read_wording(), book_id)["year"].reading


class CoverPartTests(unittest.TestCase):
    def test_india_p1_with_no_term_is_roman(self) -> None:
        self.assertEqual(
            _year(_meta(), "mathematics_en_in09_p1"),
            f"2026{YEAR_DASH}2027{YEAR_BULLET}Part I",
        )

    def test_english_non_india_p1_is_arabic_numerals(self) -> None:
        self.assertEqual(
            _year(_meta(country_code="eg", country="Egypt", grade="8"), "mathematics_en_eg08_p1"),
            f"2026{YEAR_DASH}2027{YEAR_BULLET}Part 1",
        )

    def test_arabic_p1_is_al_juz(self) -> None:
        self.assertEqual(
            _year(
                _meta(language_code="ar", country_code="eg", country="Egypt", grade="8"),
                "mathematics_ar_eg08_p1",
            ),
            f"2026{YEAR_DASH}2027{YEAR_BULLET}الجزء الأول",
        )

    def test_term_wins_over_p_suffix(self) -> None:
        self.assertEqual(
            _year(_meta(term="1"), "mathematics_en_in09_p1"),
            f"2026{YEAR_DASH}2027{YEAR_BULLET}Term 1",
        )

    def test_no_term_no_suffix_is_years_only(self) -> None:
        self.assertEqual(
            _year(_meta(grade="10"), "mathematics_en_in10"),
            f"2026{YEAR_DASH}2027",
        )


if __name__ == "__main__":
    unittest.main()
