"""eg10 notation and locked-paper layout: radian mark, radius, angle qaf, headings."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from src.arabic_math import letter_macros, letters_for_tex, macros_tex  # noqa: E402
from src.latex import (  # noqa: E402
    TEXT_HEIGHT,
    _SHORT_DIV_LETTER_MACRO,
    Block,
    arabic_maths,
    arabic_maths_letters,
    arabic_notation,
    best_fill,
    foot_tex,
    for_direction,
    lay_out,
    next_in_order,
    tallest_first,
    to_latex,
    wrap_stix_digits,
)
from src.protect import restored_problems, skeleton  # noqa: E402
from src.prompting import Profile  # noqa: E402


RTL_LATIN = Profile("ar", "western", "latin")
RTL_ARABIC = Profile("ar", "arabic", "arabic")

MCQ_42_30 = r"\mcq{$٠٫٥^{\text{د}}$}{$١^{\text{د}}$}{$٢^{\text{د}}$}{$\pi^{\text{د}}$}"
FUNCTION_LEFT = r"$\text{د}\left(\seen \right) = \alef \seen ^٢$"
FUNCTION_INVERSE = r"$\text{د}^{-1}\left(س\right)$"


def _field_document(text: str) -> dict:
    return {
        "number": "1.1",
        "title": "عنوان",
        "book_id": "mathematics_ar_eg10_t1",
        "exercises": {"groups": [{"exercises": [{"id": "1", "text": text}]}]},
    }


def _field_text(document: dict) -> str:
    return document["exercises"]["groups"][0]["exercises"][0]["text"]


def _block(
    key: str,
    kind: str,
    height: float,
    *,
    objective: str = "",
    pieces: list[Block] | None = None,
) -> Block:
    return Block(
        key=key,
        kind=kind,
        height=height,
        depth=0.0,
        objective=objective,
        pieces=list(pieces or []),
    )


def _page_keys(pages: list[list[Block]]) -> list[list[str]]:
    return [[block.key for block in page] for page in pages]


class StixFooterTests(unittest.TestCase):
    def test_latin_digit_tokens_and_year_range_and_page_number(self) -> None:
        self.assertEqual(wrap_stix_digits("G7"), "G7")
        self.assertEqual(wrap_stix_digits("T1"), "T1")
        self.assertEqual(wrap_stix_digits("A1B"), "A1B")
        year = wrap_stix_digits("سنة 2026-2027")
        self.assertEqual(year.count(r"\hbox{\foreignlanguage{english}{2026}}"), 1)
        self.assertEqual(year.count(r"\hbox{\foreignlanguage{english}{2027}}"), 1)
        self.assertIn(r"\hbox{\foreignlanguage{english}{-}}", year)
        self.assertIn(
            r"\hbox{\foreignlanguage{english}{12}}",
            wrap_stix_digits("صفحة 12"),
        )

    def test_eg07_footer_keeps_g7_t1(self) -> None:
        footer = foot_tex(
            {
                "grade": "7",
                "term": "1",
                "subject": "الرياضيات",
                "country": "جمهورية مصر العربية",
                "academic_year": "26-27",
            },
            profile=RTL_LATIN,
        )
        self.assertIn("G7", footer)
        self.assertIn("T1", footer)
        self.assertNotIn(r"G\hbox", footer)
        self.assertNotIn(r"T\hbox", footer)


class RadianMarkTests(unittest.TestCase):
    def test_unit_superscripts_unwrap_on_both_arabic_maths_paths(self) -> None:
        cases = (
            (r"$\pi^{\text{د}}$", r"\pi^{\dal"),
            (r"$0.5^{\text{د}}$", r"^{\dal"),
            (r"$-\frac{4\pi}{5}^{\text{د}}$", r"^{\dal"),
            (r"$\theta^\text{د}$", r"^{\dal"),
            (r"$ص^{\text{جـ}}$", r"^{\jeemi"),
            (r"$^{ \text{جـ} }$", r"^{\jeemi"),
        )
        for source, needle in cases:
            for rewrite in (arabic_maths, arabic_maths_letters, arabic_notation):
                out = rewrite(source)
                self.assertIn(needle, out, f"{rewrite.__name__}: {source}")
                self.assertNotIn(r"\text{د}", out, f"{rewrite.__name__}: {source}")
                self.assertNotIn(r"\text{جـ}", out, f"{rewrite.__name__}: {source}")

    def test_mcq_exercise_30_all_four_options_use_dal(self) -> None:
        for rewrite in (arabic_maths, arabic_maths_letters, arabic_notation):
            out = rewrite(MCQ_42_30)
            self.assertEqual(out.count(r"^{\dal"), 4, rewrite.__name__)
            self.assertNotIn(r"\text{د}", out, rewrite.__name__)

    def test_function_names_multi_letter_units_and_english_stay(self) -> None:
        for rewrite in (arabic_maths, arabic_maths_letters, arabic_notation):
            left = rewrite(FUNCTION_LEFT)
            self.assertIn(r"\text{د}", left, rewrite.__name__)
            self.assertNotIn(r"\dal", left, rewrite.__name__)
            inverse = rewrite(FUNCTION_INVERSE)
            self.assertIn(r"\text{د}", inverse, rewrite.__name__)
            self.assertNotIn(r"\dal", inverse, rewrite.__name__)
            self.assertIn(r"\text{سم}", rewrite(r"$5^{\text{سم}}$"))
        self.assertIn(r"^{\text{د}}", to_latex(r"$\pi^{\text{د}}$"))
        latin = _field_text(for_direction(_field_document(r"$\pi^{\text{د}}$"), RTL_LATIN))
        self.assertIn(r"^{\text{د}}", latin)
        self.assertNotIn(r"\dal", latin)
        arabic = _field_text(for_direction(_field_document(r"$\pi^{\text{د}}$"), RTL_ARABIC))
        self.assertIn(r"^{\dal", arabic)


class RadiusSymbolTests(unittest.TestCase):
    def test_symbol_text_becomes_the_symbols_macro_on_both_paths(self) -> None:
        cases = (
            r"$\text{ن ق}_١$",
            r"$2\pi \text{ن ق}$",
            r"$2 \text{نق}$",
            r"$\frac{ل}{\text{ن ق}}$",
        )
        for source in cases:
            for rewrite in (arabic_maths, arabic_maths_letters, arabic_notation):
                out = rewrite(source)
                self.assertIn(r"\arrad", out, f"{rewrite.__name__}: {source}")
                self.assertNotIn(r"\text{ن", out, f"{rewrite.__name__}: {source}")
        subscript = arabic_maths(r"$\text{ن ق}_١$")
        self.assertRegex(subscript, r"\\arrad\s*_١")

    def test_non_symbol_text_and_prose_and_short_div_macro_set(self) -> None:
        for rewrite in (arabic_maths, arabic_maths_letters, arabic_notation):
            self.assertIn(r"\text{نقطة}", rewrite(r"$\text{نقطة}$"))
            self.assertNotIn(r"\arrad", rewrite(r"$\text{نقطة}$"))
        self.assertIn(r"\text{ن ق}", arabic_notation(r"نصف القطر \text{ن ق}"))
        english = to_latex(r"radius $\text{ن ق}_1$")
        self.assertIn(r"\text{ن ق}", english)
        latin = _field_text(for_direction(_field_document(r"$\text{ن ق}_1$"), RTL_LATIN))
        self.assertIn(r"\text{ن ق}", latin)
        self.assertIn("arrad", _SHORT_DIV_LETTER_MACRO)


class AngleQafTests(unittest.TestCase):
    def test_dotlessqaft_is_defined_and_in_the_font(self) -> None:
        self.assertIn(("dotlessqaft", 0x1EE5F), letter_macros())
        self.assertIn(r"\Umathchardef\dotlessqaft=" + '"7 "0 "1EE5F', macros_tex())
        from fontTools.ttLib import TTFont

        cmap = set(TTFont(ROOT / "fonts" / "NagwaArabicMath-12-25.otf").getBestCmap())
        self.assertIn(0x1EE5F, cmap)
        paper = ROOT / "data" / "output" / "mathematics_ar_eg10_t1" / "12_section_papers" / "04_02.tex"
        if paper.is_file():
            self.assertIn(r"\Umathchardef\dotlessqaft", paper.read_text(encoding="utf-8"))

    def test_qaf_before_angle_prints_dotless_tailed_form(self) -> None:
        angled = (
            r"$ق(\angle ا و ب) = \theta$",
            r"$ق\angle ا ب جـ = 60^\circ$",
            r"$ق \left( \angle ا \right)$",
        )
        for source in angled:
            for rewrite in (arabic_maths, arabic_maths_letters, letters_for_tex):
                body = source if rewrite is not letters_for_tex else source.strip("$")
                out = rewrite(body)
                self.assertIn(r"\dotlessqaft", out, f"{rewrite.__name__}: {source}")

    def test_text_qaf_before_angle_prints_dotless_tailed_form(self) -> None:
        angled = (
            r"$\text{ق}\left(\angle ا\right)$",
            r"$\text{ق}(\angle ا)$",
            r"$\text{ق}\angle ا$",
            r"$\text{ق}\left[\angle ا\right]$",
            r"$\text{ ق }(\angle ا)$",
            r"$\text{قـ}\left(\angle ا\right)$",
        )
        for source in angled:
            for rewrite in (arabic_maths, arabic_maths_letters, arabic_notation):
                out = rewrite(source)
                self.assertIn(r"\dotlessqaft", out, f"{rewrite.__name__}: {source}")
                self.assertNotIn(r"\text{ق}", out, f"{rewrite.__name__}: {source}")

    def test_other_qaf_and_tarek_rules_stay(self) -> None:
        kept = (
            r"$ق = 5$",
            r"$\seen ق$",
            r"$ق(س)$",
        )
        for source in kept:
            out = arabic_maths(source)
            self.assertIn(r"\qaf", out, source)
            self.assertNotIn(r"\dotlessqaft", out, source)
        for source in (r"$\text{ق}$", r"$\text{ق}(س)$", r"$\text{قياس}(\angle ا)$"):
            for rewrite in (arabic_maths, arabic_maths_letters, arabic_notation):
                out = rewrite(source)
                self.assertNotIn(r"\dotlessqaft", out, f"{rewrite.__name__}: {source}")
                if "قياس" in source:
                    self.assertIn(r"\text{قياس}", out, f"{rewrite.__name__}: {source}")
                else:
                    self.assertIn(r"\text{ق}", out, f"{rewrite.__name__}: {source}")
        latin = _field_text(for_direction(_field_document(r"$ق\angle ا$"), RTL_LATIN))
        self.assertIn("ق", latin)
        self.assertNotIn(r"\dotlessqaft", latin)
        self.assertIn(r"\qaf '", letters_for_tex("قَ"))
        spaced = arabic_maths(r"$ق ل م$")
        self.assertIn(r"\qaf", spaced)
        self.assertIn(r"\lam", spaced)
        self.assertIn(r"\meem", spaced)
        self.assertNotIn(r"\dotlessqaft", spaced)


class SkeletonScriptTests(unittest.TestCase):
    def test_script_braces_fold_and_structure_still_fails(self) -> None:
        self.assertEqual(
            skeleton(r"$\theta^{}=\frac{}{}$", notation="arabic"),
            skeleton(r"$\theta^=\frac{}{}$", notation="arabic"),
        )
        self.assertEqual(
            restored_problems(r"$\theta^{}=\frac{}{}$", r"$\theta^=\frac{}{}$", "x", notation="arabic"),
            [],
        )
        self.assertEqual(
            restored_problems("$1^{}$", "$1^$", "x", notation="arabic"),
            [],
        )
        self.assertEqual(
            restored_problems(r"$\pi^{}$", r"$\pi^$", "x", notation="arabic"),
            [],
        )
        self.assertTrue(
            restored_problems(r"$\theta^{}=\frac{1}{2}$", r"$\theta^=$", "x", notation="arabic")
        )
        self.assertTrue(restored_problems("$1^{2}$", "$12$", "x", notation="arabic"))


class HeadingWidowTests(unittest.TestCase):
    def test_strict_order_reserves_the_next_block_not_the_shortest(self) -> None:
        capacity = 250.0
        holdback = TEXT_HEIGHT - capacity
        filler = _block("g0", "exercise", 100, objective="o0")
        head = _block("head-o1", "objective", 20, objective="o1")
        tall = _block("g1", "exercise", 180, objective="o1")
        short = _block("g2", "exercise", 30, objective="o1")
        pages = lay_out(
            [filler, head, tall, short],
            holdback,
            next_in_order,
            strict_order=True,
        )
        keys = _page_keys(pages)
        self.assertEqual(keys[0], ["g0"])
        self.assertIn("head-o1", keys[1])
        self.assertIn("g1", keys[1])
        self._assert_no_stranded_heading(pages)

    def test_free_order_still_keeps_the_heading_with_a_short_question(self) -> None:
        capacity = 250.0
        holdback = TEXT_HEIGHT - capacity
        filler = _block("g0", "exercise", 100, objective="o0")
        head = _block("head-o1", "objective", 20, objective="o1")
        tall = _block("g1", "exercise", 180, objective="o1")
        short = _block("g2", "exercise", 30, objective="o1")
        pages = lay_out(
            [filler, head, tall, short],
            holdback,
            tallest_first,
            strict_order=False,
        )
        keys = _page_keys(pages)
        self.assertIn("head-o1", keys[0])
        self.assertIn("g2", keys[0])
        self.assertIn("g1", keys[1])
        self._assert_no_stranded_heading(pages)

    def test_strict_set_splits_when_the_first_piece_fits(self) -> None:
        capacity = 200.0
        holdback = TEXT_HEIGHT - capacity
        head = _block("head-o1", "objective", 20, objective="o1")
        piece0 = _block("g1p0", "member", 80, objective="o1")
        piece1 = _block("g1p1", "member", 80, objective="o1")
        aset = _block("g1", "set", 180, objective="o1", pieces=[piece0, piece1])
        pages = lay_out([head, aset], holdback, next_in_order, strict_order=True)
        keys = _page_keys(pages)
        self.assertEqual(keys[0][:2], ["head-o1", "g1p0"])
        self.assertIn("g1p1", keys[0] + (keys[1] if len(keys) > 1 else []))
        self._assert_no_stranded_heading(pages)

    def test_section_and_objective_never_end_a_page_alone(self) -> None:
        capacity = 200.0
        holdback = TEXT_HEIGHT - capacity
        section = _block("section", "section", 30)
        head = _block("head-o1", "objective", 20, objective="o1")
        tall = _block("g1", "exercise", 190, objective="o1")
        pages = lay_out(
            [section, head, tall],
            holdback,
            next_in_order,
            strict_order=True,
        )
        self._assert_no_stranded_heading(pages)
        self.assertTrue(any(block.key == "section" for page in pages for block in page))
        self.assertTrue(any(block.key == "head-o1" for page in pages for block in page))

    def test_best_fill_still_returns_pages(self) -> None:
        holdback = TEXT_HEIGHT - 300.0
        blocks = [
            _block("head-o1", "objective", 20, objective="o1"),
            _block("g1", "exercise", 80, objective="o1"),
            _block("g2", "exercise", 70, objective="o1"),
            _block("g3", "exercise", 60, objective="o1"),
        ]
        pages = lay_out(blocks, holdback, best_fill, strict_order=False)
        self.assertGreaterEqual(len(pages), 1)
        self._assert_no_stranded_heading(pages)

    def _assert_no_stranded_heading(self, pages: list[list[Block]]) -> None:
        for index, page in enumerate(pages):
            if not page:
                continue
            if index == len(pages) - 1:
                continue
            self.assertNotIn(
                page[-1].kind,
                {"objective", "section"},
                f"page {index} ends with {page[-1].key}",
            )


if __name__ == "__main__":
    unittest.main()
