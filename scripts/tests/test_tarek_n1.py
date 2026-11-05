"""N1: a thin space ``\\,`` is not a list comma, so math_commas leaves it alone."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from src.latex import to_latex  # noqa: E402


class ThinSpaceCommaTests(unittest.TestCase):
    def test_thousands_thin_space_is_not_a_break(self) -> None:
        self.assertEqual(to_latex(r"$25\,000$"), r"$25\,000$")
        self.assertNotIn(r"\allowbreak", to_latex(r"$25\,000$"))

    def test_differential_thin_space_is_not_a_break(self) -> None:
        rendered = to_latex(r"$\int f(x)\,dx$")
        self.assertIn(r"\,dx", rendered)
        self.assertNotIn(r"\allowbreak", rendered)

    def test_list_comma_still_gets_a_break(self) -> None:
        self.assertEqual(to_latex(r"$10, 20$"), r"$10,\allowbreak 20$")

    def test_braced_thousands_comma_is_unchanged(self) -> None:
        self.assertEqual(to_latex(r"$4{,}400$"), r"$4{,}400$")

    def test_thin_space_inside_braces_or_brackets_is_unchanged(self) -> None:
        braced = to_latex(r"${a\,b}$")
        self.assertEqual(braced, r"${a\,b}$")
        self.assertNotIn(r"\allowbreak", braced)
        bracketed = to_latex(r"$[a\,b]$")
        self.assertIn(r"a\,b", bracketed)
        self.assertNotIn(r"\allowbreak", bracketed)

    def test_arabic_comma_still_gets_a_break(self) -> None:
        self.assertEqual(to_latex("$١، ٢$"), r"$١،\allowbreak ٢$")

    def test_row_break_then_comma_also_skips_allowbreak(self) -> None:
        # Tarek looks only at the character before the comma, so \\, is treated
        # as a thin space. Recorded here; 0 cases in the data.
        rendered = to_latex(r"$a\\,b$")
        self.assertNotIn(r"\allowbreak", rendered)


if __name__ == "__main__":
    unittest.main()
