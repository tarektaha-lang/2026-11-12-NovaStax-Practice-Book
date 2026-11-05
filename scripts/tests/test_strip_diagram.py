from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from figures.strip_diagram import (  # noqa: E402
    Cell,
    math_of,
    part_shares,
    tikz_of,
)


def _bare_percent_in_math(text: str) -> bool:
    """True when a % inside `$...$` is not preceded by a backslash."""
    i = 0
    while True:
        start = text.find("$", i)
        if start < 0:
            return False
        end = text.find("$", start + 1)
        if end < 0:
            return False
        body = text[start + 1 : end]
        if any(
            body[j] == "%" and (j == 0 or body[j - 1] != "\\")
            for j in range(len(body))
        ):
            return True
        i = end + 1


class MathOfTests(unittest.TestCase):
    def test_percent_is_escaped_maths(self) -> None:
        for source in ("40%", "40 %", r"40\%", "40٪"):
            with self.subTest(source=source):
                self.assertEqual(math_of(source), r"$40\%$")

    def test_whole_percent(self) -> None:
        self.assertEqual(math_of("100%"), r"$100\%$")

    def test_known_cells_unchanged(self) -> None:
        self.assertEqual(math_of("?"), "$?$")
        self.assertEqual(math_of("8 L"), r"$8\text{ L}$")
        self.assertEqual(math_of("x"), "$x$")

    def test_arabic_quantity_wraps_unit_not_product(self) -> None:
        self.assertEqual(math_of("19 لترًا"), r"$19\text{ لترًا}$")
        self.assertEqual(math_of("50 كجم"), r"$50\text{ كجم}$")
        self.assertEqual(math_of("? كجم"), r"$?\text{ كجم}$")
        self.assertEqual(math_of("ل سم"), r"$ل\text{ سم}$")
        self.assertEqual(math_of("م ح"), r"$م ح$")
        self.assertEqual(math_of("ح"), r"$ح$")

    def test_fallback_escapes_percent_in_prose(self) -> None:
        out = math_of("Food 40%")
        self.assertIn(r"\%", out)
        self.assertFalse(_bare_percent_in_math(out))
        self.assertTrue(out.startswith("$") and out.endswith("$"))


class TikzOfTests(unittest.TestCase):
    def test_eg07_expenditure_strip_escapes_percents(self) -> None:
        tikz = tikz_of(
            {
                "max_narrow_width": "84mm",
                "strip": {
                    "layout": "part_whole",
                    "whole": {"text": "100%"},
                    "parts": [
                        {"text": "40%"},
                        {"text": "30%"},
                        {"text": "20%"},
                        {"text": "10%"},
                    ],
                },
            }
        )
        self.assertIn(r"$40\%$", tikz)
        self.assertIn(r"$100\%$", tikz)
        self.assertNotIn("$40%$", tikz)
        self.assertNotIn("$100%$", tikz)
        self.assertFalse(_bare_percent_in_math(tikz))
        self.assertEqual(tikz.count(r"\draw ("), 5)

    def test_eg07_energy_strip_keeps_unknown(self) -> None:
        tikz = tikz_of(
            {
                "max_narrow_width": "84mm",
                "strip": {
                    "layout": "part_whole",
                    "whole": {"text": "100%"},
                    "parts": [
                        {"text": "35%"},
                        {"text": "25%"},
                        {"text": "?"},
                        {"text": "15%"},
                    ],
                },
            }
        )
        self.assertIn("$?$", tikz)
        self.assertIn(r"$35\%$", tikz)
        self.assertNotIn("$35%$", tikz)


class PartShareTests(unittest.TestCase):
    def test_known_percents_are_proportional(self) -> None:
        shares = part_shares(
            [
                Cell(kind="text", text="40%"),
                Cell(kind="text", text="30%"),
                Cell(kind="text", text="20%"),
                Cell(kind="text", text="10%"),
            ]
        )
        self.assertEqual(len(shares), 4)
        self.assertAlmostEqual(sum(shares), 1.0)
        self.assertGreater(shares[0], shares[1])
        self.assertGreater(shares[1], shares[2])
        self.assertGreater(shares[2], shares[3])

    def test_unknown_beside_percents_equal_splits(self) -> None:
        shares = part_shares(
            [
                Cell(kind="text", text="35%"),
                Cell(kind="text", text="25%"),
                Cell(kind="text", text="?"),
                Cell(kind="text", text="15%"),
            ]
        )
        self.assertEqual(shares, [0.25, 0.25, 0.25, 0.25])


class LocalDrawerTypeFilesTests(unittest.TestCase):
    def test_every_local_drawer_has_type_rules(self) -> None:
        from draw import FIGURE_TYPES_DIR, LOCAL_DRAWERS

        missing = [
            name
            for name in LOCAL_DRAWERS
            if not (FIGURE_TYPES_DIR / f"{name}.md").is_file()
        ]
        self.assertEqual(
            missing,
            [],
            f"local drawers need rules at {FIGURE_TYPES_DIR}: {missing}",
        )


if __name__ == "__main__":
    unittest.main()
