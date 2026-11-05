from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from src.arabic_labels import (  # noqa: E402
    rewrite_arabic_curve_label,
    rewrite_graph,
)


class ArabicCurveLabelTests(unittest.TestCase):
    def test_matches_seen_sad_from_axis_parens(self) -> None:
        wording = (
            "تمثيل بياني يوضح الكتلة بالرطل على المحور الأفقي (س) "
            "والكتلة بالأوقية على المحور الرأسي (ص)."
        )
        self.assertEqual(rewrite_arabic_curve_label("y = 16x", wording), "ص = 16س")

    def test_matches_feh_noon_from_axis_words(self) -> None:
        wording = (
            "الزمن بالساعات (ن) على المحور الأفقي والمسافة بالكيلومترات (ف) "
            "على المحور الرأسي."
        )
        self.assertEqual(rewrite_arabic_curve_label("y = 80x", wording), "ف = 80ن")

    def test_arabic_label_is_left_alone(self) -> None:
        self.assertEqual(
            rewrite_arabic_curve_label("ف = 80 ن", "المحور الأفقي (س)"),
            "ف = 80 ن",
        )

    def test_defaults_to_seen_sad_without_wording(self) -> None:
        self.assertEqual(rewrite_arabic_curve_label("y = 16x", ""), "ص = 16س")

    def test_math_dollar_letters_on_axes(self) -> None:
        wording = "المحور الأفقي $ن$ والمحور الرأسي $ف$."
        self.assertEqual(rewrite_arabic_curve_label("Y=12X", wording), "ف=12ن")

    def test_rewrite_graph_copies_only_when_needed(self) -> None:
        graph = {
            "curves": [
                {"kind": "line", "label": "y = 12x", "points": [[0, 0], [4, 48]]}
            ]
        }
        wording = "المحور الأفقي (س) والمحور الرأسي (ص)."
        rewritten = rewrite_graph(graph, wording)
        self.assertEqual(rewritten["curves"][0]["label"], "ص = 12س")
        self.assertEqual(graph["curves"][0]["label"], "y = 12x")
        same = rewrite_graph({"curves": [{"kind": "line", "label": "ص = 4 س"}]}, wording)
        self.assertEqual(same["curves"][0]["label"], "ص = 4 س")


if __name__ == "__main__":
    unittest.main()
