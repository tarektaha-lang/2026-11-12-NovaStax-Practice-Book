"""Egyptian papers: a unit vector ``\\hat`` is the same mark as a vector."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from src.latex import (  # noqa: E402
    _reset_quoting,
    quoting_for,
    section_tex,
    to_latex,
)
from src.prompting import Profile  # noqa: E402


ENGLISH = Profile("en", "western", "latin")
RTL_ARABIC = Profile("ar", "arabic", "arabic")
MAGHREB = Profile("ar", "western", "latin")

EG_EN = "applied_mathematics_en_eg12_t0"
EG_AR = "mathematics_ar_eg10_t1"
NON_EG = "mathematics_ar_ma07_t1"


def render(text: str, book_id: str, profile: Profile = ENGLISH) -> str:
    quotes, western, snippets = quoting_for(profile, book_id)
    try:
        return to_latex(text)
    finally:
        _reset_quoting(quotes, western, snippets)


class EgyptUnitVectorArrowTests(unittest.TestCase):
    def test_eg_braced_i_and_j(self) -> None:
        self.assertEqual(
            render(r"$3\hat{i} + 4\hat{j}$", EG_EN),
            r"$3\overrightarrow{i} + 4\overrightarrow{j}$",
        )

    def test_eg_unbraced_k_and_braced_c(self) -> None:
        self.assertEqual(render(r"$\hat k$", EG_EN), r"$\overrightarrow{k}$")
        self.assertEqual(render(r"$\hat{c}$", EG_EN), r"$\overrightarrow{c}$")

    def test_eg_widehat_is_unchanged(self) -> None:
        source = r"$\widehat{ABC}$"
        self.assertEqual(render(source, EG_EN), to_latex(source))
        self.assertEqual(render(source, EG_EN), source)

    def test_eg_prose_hat_is_unchanged(self) -> None:
        source = "The word hat names a covering."
        self.assertEqual(render(source, EG_EN), to_latex(source))
        self.assertIn("hat", render(source, EG_EN))

    def test_non_eg_hat_is_unchanged(self) -> None:
        source = r"$3\hat{i}$"
        self.assertEqual(render(source, NON_EG), to_latex(source))
        self.assertEqual(render(source, NON_EG, MAGHREB), to_latex(source))
        self.assertEqual(render(source, NON_EG), source)

    def test_ar_eg_same_rewrite(self) -> None:
        self.assertEqual(
            render(r"$3\hat{i} + 4\hat{j}$", EG_AR, RTL_ARABIC),
            r"$3\overrightarrow{i} + 4\overrightarrow{j}$",
        )

    def test_eg_imath_and_spaced_braces(self) -> None:
        self.assertEqual(
            render(r"$\hat{\imath}$", EG_EN),
            r"$\overrightarrow{\imath}$",
        )
        self.assertEqual(render(r"$\hat {i}$", EG_EN), r"$\overrightarrow{i}$")

    def test_ar_eg_section_tex_rewrites_from_book_id(self) -> None:
        document = {
            "number": "1.1",
            "title": "t",
            "book_id": EG_AR,
            "exercises": {
                "groups": [{"exercises": [{"id": "1", "text": r"$3\hat{i}$"}]}]
            },
        }
        tex = section_tex(document, profile=RTL_ARABIC)
        self.assertIn(r"\overrightarrow{i}", tex)
        self.assertNotIn(r"\hat{i}", tex)
        self.assertNotIn(r"\hat i", tex)


if __name__ == "__main__":
    unittest.main()
