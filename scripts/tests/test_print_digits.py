from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from src.print_digits import (  # noqa: E402
    bare_print_digits,
    normalize_print_digits,
    normalize_tikz_print_digits,
)


class PrintDigitTests(unittest.TestCase):
    def test_wraps_reader_visible_digits_and_is_idempotent(self) -> None:
        source = (
            r"يبلغ طول العارضة 1 مقدار $(1 + \sqrt{3})$ م، "
            r"ويبلغ طول العارضة 2 مقدار $2.75$ م."
        )
        expected = (
            r"يبلغ طول العارضة $1$ مقدار $(1 + \sqrt{3})$ م، "
            r"ويبلغ طول العارضة $2$ مقدار $2.75$ م."
        )
        self.assertEqual(normalize_print_digits(source), expected)
        self.assertEqual(normalize_print_digits(expected), expected)
        self.assertEqual(bare_print_digits(expected), [])

    def test_signed_decimal_percent_and_figure_token(self) -> None:
        source = r"من -2.5 إلى 4 بنسبة 50%، استخدم [figure 2]."
        expected = (
            r"من $-2.5$ إلى $4$ بنسبة $50\%$، استخدم [figure 2]."
        )
        self.assertEqual(normalize_print_digits(source), expected)
        self.assertEqual(bare_print_digits(source), ["-2.5", "4", "50%"])

    def test_protects_math_and_technical_command_arguments(self) -> None:
        source = r"$12$ ثم \hspace{1.5cm} والعارضة 3."
        self.assertEqual(
            normalize_print_digits(source),
            r"$12$ ثم \hspace{1.5cm} والعارضة $3$.",
        )

    def test_scale_ratio_is_one_island_and_clock_stays_a_clock(self) -> None:
        self.assertEqual(normalize_print_digits("1 : 2,500"), "$1 : 2,500$")
        self.assertEqual(normalize_print_digits("1:2,500"), "$1:2,500$")
        self.assertEqual(normalize_print_digits("3:00"), "$3:00$")
        self.assertEqual(
            normalize_print_digits("At 5:00 p.m. and 1 : 2,500"),
            "At $5:00$ p.m. and $1 : 2,500$",
        )
        self.assertEqual(bare_print_digits("$1 : 2,500$ and $3:00$"), [])

    def test_grade_year_class_numbers_are_not_wrapped(self) -> None:
        self.assertEqual(
            normalize_print_digits("Grade 7 students scored 142"),
            "Grade 7 students scored $142$",
        )
        self.assertEqual(normalize_print_digits("year 8 and class 3"), "year 8 and class 3")
        self.assertEqual(bare_print_digits("Grade 7 students scored $142$"), [])

    def test_tikz_wraps_node_labels_not_geometry(self) -> None:
        source = (
            r"\begin{tikzpicture}[x=0.5cm]"
            r"\draw (0,0)--(2,0);"
            r"\node at (1,0) {2};"
            r"\end{tikzpicture}"
        )
        result = normalize_tikz_print_digits(source)
        self.assertIn("[x=0.5cm]", result)
        self.assertIn(r"\draw (0,0)--(2,0);", result)
        self.assertIn(r"\node at (1,0) {$2$};", result)
        path = r"\fill (0,0) circle (2pt) node [above=3pt] {2};"
        wrapped = normalize_tikz_print_digits(path)
        self.assertIn(r"node [above=3pt] {$2$}", wrapped)
        self.assertIn(r"\fill (0,0) circle (2pt)", wrapped)

    def test_tikz_keeps_nested_braces_inside_math_labels(self) -> None:
        source = (
            r"\node [above=8pt] at (4.712389,0) {$\frac{3\pi}{2}$};"
            r"\node [above] at (0,2.25) {$ص = \text{جا}\,\theta$};"
        )
        result = normalize_tikz_print_digits(source)
        self.assertIn(r"{$\frac{3\pi}{2}$}", result)
        self.assertNotIn(r"\frac{$3$", result)
        self.assertIn(r"{$ص = \text{جا}\,\theta$}", result)
        self.assertEqual(normalize_tikz_print_digits(result), result)

    def test_tikz_westernizes_pgf_digits_not_node_labels(self) -> None:
        source = (
            r"\begin{tikzpicture}"
            r"\pgfmathsetmacro{\a}{٠٫٥*1}"
            r"\draw (٠٫٥,1)--(2,0);"
            r"\node at (1,0) {٢};"
            r"\end{tikzpicture}"
        )
        result = normalize_tikz_print_digits(source)
        self.assertIn(r"{0.5*1}", result)
        self.assertNotIn("٠", result)
        self.assertNotIn("٫", result)
        self.assertIn(r"\draw (0.5,1)--(2,0);", result)
        self.assertIn(r"\node at (1,0) {$٢$};", result)


if __name__ == "__main__":
    unittest.main()
