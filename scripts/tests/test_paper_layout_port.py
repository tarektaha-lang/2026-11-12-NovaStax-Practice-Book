"""Paper-layout port: short division, TOC, STIX digits, vertices, Lua merge."""

from __future__ import annotations

import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from frontmatter import (  # noqa: E402
    CONTENTS_PREAMBLE,
    contents_body,
    number_tex,
    prose_tex,
)
from src.latex import (  # noqa: E402
    DRAWING_PREAMBLE,
    _WESTERN_STIX,
    _reset_quoting,
    arabic_notation,
    autosize_brackets,
    foot_tex,
    for_direction,
    heading_tex,
    house_wording_square_problems,
    maybe_stix_digits,
    quoting_for,
    rewrite_short_division,
    section_blocks,
    section_tex,
    to_latex,
    type_preamble,
    unframed,
    unwrap_arabic_math,
    widen_array_spec,
    without_marker,
    wrap_stix_digits,
)
from src.prompting import Profile  # noqa: E402


ENGLISH = Profile("en", "western", "latin")
RTL_LATIN = Profile("ar", "western", "latin")
RTL_ARABIC = Profile("ar", "arabic", "arabic")

HOUSE_MISSING_DIGIT = (
    r"\begin{array}{r}12\\4\overline{ )4\square}\end{array}"
)
HOUSE_MISSING_DIVISOR = (
    r"\begin{array}{r}12\\\square\overline{ )48}\end{array}"
)
HOUSE_MISSING_CARRY = (
    r"\begin{array}{r}12^{(\square)}\\4\overline{ )48}\end{array}"
)
HOUSE_BLANK = (
    r"\begin{array}{r}12\\4\overline{ )4\blank}\end{array}"
)
HOUSE_BLANK_BREAK = (
    r"\begin{array}{r}12\blank \\4\overline{ )48}\end{array}"
)
HOUSE_COMPLETE = (
    r"\begin{array}{r}12\\4\overline{ )48}\end{array}"
)
PLAIN_R_ARRAY = r"\begin{array}{r}12\\34\end{array}"


def _field_document(text: str) -> dict:
    return {
        "number": "1.1",
        "title": "عنوان",
        "book_id": "mathematics_ar_eg09_t1",
        "exercises": {"groups": [{"exercises": [{"id": "1", "text": text}]}]},
    }


def _toc_root(*pairs: tuple[str, str, int]) -> ET.Element:
    root = ET.Element("book")
    chapters: dict[str, ET.Element] = {}
    for number, title, depth in pairs:
        if depth == 0:
            module = ET.SubElement(root, "module", number=number, title=title)
            chapters[number] = module
            continue
        parent_number = number.rsplit(".", 1)[0]
        parent = chapters.get(parent_number, root)
        local = number.rsplit(".", 1)[-1]
        ET.SubElement(parent, "module", number=local, title=title)
    return root


class ShortDivisionTests(unittest.TestCase):
    def test_house_rewrites_missing_digit_divisor_and_carry(self) -> None:
        digit = rewrite_short_division(HOUSE_MISSING_DIGIT)
        self.assertIn(r"\divform", digit)
        self.assertIn(r"\square", digit)
        self.assertNotIn(r"\overline{ )", digit)
        divisor = rewrite_short_division(HOUSE_MISSING_DIVISOR)
        self.assertIn(r"\divform{$\square$}", divisor)
        carry = rewrite_short_division(HOUSE_MISSING_CARRY)
        self.assertIn(r"\cir", carry)
        self.assertIn(r"\divform", carry)

    def test_non_house_r_array_is_unchanged(self) -> None:
        self.assertEqual(rewrite_short_division(PLAIN_R_ARRAY), PLAIN_R_ARRAY)

    def test_wording_square_follows_the_house_mark(self) -> None:
        boxed = to_latex(
            rf"The digit at $\square$ is $\blank$. $${HOUSE_MISSING_DIGIT}$$"
        )
        self.assertIn(r"$\square$", boxed)
        self.assertIn(r"$\blank$", boxed)
        self.assertIn(r"\divform", boxed)
        circled = to_latex(
            rf"The carry at $\square$ is $\blank$. $${HOUSE_MISSING_CARRY}$$"
        )
        self.assertIn(r"$\cirmark{}$", circled)
        self.assertNotIn(r"$^{(\square)}$", circled)
        self.assertIn(r"\blank{}", to_latex(r"The digit is \blank."))

    def test_carry_unwrap_and_blank_stay(self) -> None:
        rendered = to_latex(
            r"The mark is $^{(\square)}$. $$" + HOUSE_MISSING_CARRY + "$$"
        )
        self.assertNotIn(r"$^{(\square)}$", rendered)
        self.assertIn(r"\cirmark{}", rendered)
        blank = to_latex(rf"The digit is $\blank$. $${HOUSE_BLANK}$$")
        self.assertIn(r"$\blank$", blank)
        self.assertIn(r"\divform", blank)

    def test_blank_before_row_break_keeps_the_break(self) -> None:
        rewritten = rewrite_short_division(HOUSE_BLANK_BREAK)
        self.assertIn(r"\divform", rewritten)
        self.assertNotIn(r"\blank \\", rewritten)

    def test_arabic_indic_cells_use_divdec_and_letter_macros(self) -> None:
        house = r"\begin{array}{r}١٫٢\\٤\overline{ )٤٫٨}\end{array}"
        rewritten = rewrite_short_division(house)
        self.assertIn(r"\divdec", rewritten)
        self.assertIn("١", rewritten)
        letters = rewrite_short_division(
            r"\begin{array}{r}\seen\\\ain\overline{ )٤٨}\end{array}"
        )
        self.assertIn(r"\seen", letters)

    def test_autosize_leaves_divform_intact(self) -> None:
        rewritten = rewrite_short_division(HOUSE_MISSING_DIGIT)
        self.assertEqual(autosize_brackets(rewritten), rewritten)
        rendered = to_latex(rf"$${HOUSE_MISSING_DIGIT}$$")
        self.assertIn(r"\divform", rendered)
        self.assertEqual(rendered.count("$") % 2, 0)

    def test_rtl_layout_does_not_reverse_a_house(self) -> None:
        house = rf"$${HOUSE_MISSING_DIGIT}$$"
        latin = for_direction(_field_document(house), RTL_LATIN)
        latin_text = latin["exercises"]["groups"][0]["exercises"][0]["text"]
        self.assertIn(r"\overline{", latin_text)
        self.assertIn(r"\divform", to_latex(latin_text))
        arabic = for_direction(_field_document(house), RTL_ARABIC)
        arabic_text = arabic["exercises"]["groups"][0]["exercises"][0]["text"]
        self.assertIn(r"\overline{", arabic_text)
        self.assertIn(r"\divform", to_latex(arabic_text))

    def test_house_wording_square_problems(self) -> None:
        self.assertEqual(
            house_wording_square_problems(r"The digit at $\square$.", "stem"),
            [
                "stem writes \\square in the wording but has no short-division "
                "house to point at"
            ],
        )
        self.assertEqual(
            house_wording_square_problems(
                rf"The digit at $\square$. $${HOUSE_COMPLETE}$$",
                "stem",
            ),
            [
                "stem writes \\square in the wording but the short-division "
                "house has no missing digit or carry"
            ],
        )
        self.assertEqual(
            house_wording_square_problems(
                rf"The digit at $\square$. $${HOUSE_MISSING_DIGIT}$$",
                "stem",
            ),
            [],
        )


class TableFrameTests(unittest.TestCase):
    def test_five_cells_on_two_columns_do_not_make_a_double_rule(self) -> None:
        body = r"\begin{array}{|c|c|}a & b & c & d & e\end{array}"
        opened = unframed(body)
        self.assertNotIn("||", opened)
        self.assertEqual(widen_array_spec("|c|c|", 5), "|c|c|c|c|c")


class SpacedVertexTests(unittest.TestCase):
    def test_spaced_vertices_stay_maths_with_primes(self) -> None:
        self.assertIsNone(unwrap_arabic_math("سَ صَ عـَ لَ"))
        self.assertIsNone(unwrap_arabic_math("سَ"))
        self.assertIsNone(unwrap_arabic_math("عـَ"))
        self.assertEqual(unwrap_arabic_math("فَعَلَ"), "فَعَلَ")
        self.assertEqual(unwrap_arabic_math("فَعَلَ يَفْعَلُ"), "فَعَلَ يَفْعَلُ")
        noted = arabic_notation("$سَ صَ عـَ لَ$")
        self.assertIn(r"\seen '", noted)
        self.assertIn(r"\ain '", noted)
        self.assertNotIn(r"\aini", noted)
        self.assertIn(r"\seen '", arabic_notation("$سَ$"))
        self.assertEqual(unwrap_arabic_math("سَ", language_book=True), "سَ")
        self.assertIsNone(unwrap_arabic_math("س"))


class LatinListTests(unittest.TestCase):
    def test_arabic_prose_boxes_a_latin_list_once(self) -> None:
        rendered = to_latex("الرسم H، M، A، T في الشكل.")
        self.assertEqual(rendered.count(r"\hbox{\textdir TLT\relax"), 1)
        self.assertIn("H، M، A، T", rendered)
        again = to_latex(rendered)
        self.assertEqual(again.count(r"\hbox{\textdir TLT\relax"), 1)

    def test_english_letter_list_is_not_boxed(self) -> None:
        rendered = to_latex("Points A, B, C are collinear.")
        self.assertNotIn(r"\textdir TLT", rendered)


class TocTests(unittest.TestCase):
    def test_contents_needspace_for_zero_one_two_and_many(self) -> None:
        wording = {"table_of_contents": {"en": "Contents", "ar": "المحتويات"}}
        empty, count = contents_body(ET.Element("book"), ENGLISH, wording)
        self.assertEqual(count, 0)
        self.assertNotIn(r"\Needspace{2\baselineskip}", empty)
        self.assertNotIn(r"\nopagebreak", empty.replace(r"\par\nopagebreak", ""))
        one, count = contents_body(
            _toc_root(("1", "One", 0)), ENGLISH, wording
        )
        self.assertEqual(count, 1)
        self.assertIn(r"\nopagebreak", one)
        self.assertNotIn(r"\Needspace{2\baselineskip}", one)
        two, count = contents_body(
            _toc_root(("1", "One", 0), ("2", "Two", 0)), ENGLISH, wording
        )
        self.assertEqual(count, 2)
        self.assertIn(r"\Needspace{2\baselineskip}", two)
        many, count = contents_body(
            _toc_root(("1", "One", 0), ("1.1", "First", 1), ("1.2", "Second", 1)),
            ENGLISH,
            wording,
        )
        self.assertEqual(count, 3)
        self.assertIn(r"\Needspace{2\baselineskip}", many)
        self.assertIn(r"\nopagebreak", many)

    def test_contents_use_labelnumber_and_drop_the_number_point(self) -> None:
        self.assertIn(r"\newcommand{\contentsentry}[4]", CONTENTS_PREAMBLE)
        self.assertIn(r"\labelnumber{#2}", CONTENTS_PREAMBLE)
        self.assertEqual(number_tex("1", ENGLISH), "1")
        self.assertFalse(number_tex("1", ENGLISH).endswith("."))
        self.assertFalse(number_tex("1", RTL_ARABIC).endswith("."))
        self.assertFalse(number_tex("1.1", RTL_ARABIC).endswith("."))
        wording = {"table_of_contents": {"en": "Contents", "ar": "المحتويات"}}
        body, _count = contents_body(
            _toc_root(("1", "One", 0), ("1.1", "First", 1)), ENGLISH, wording
        )
        self.assertIn(r"\contentschapter{1}{", body)
        self.assertIn(r"\contentssection{1}{1.1}{", body)

    def test_prose_tex_wraps_digits_only_for_rtl_western(self) -> None:
        self.assertIn(r"\foreignlanguage{english}{1919}", prose_tex("Year 1919", RTL_LATIN))
        self.assertEqual(prose_tex("Year 1919", ENGLISH), "Year 1919")
        self.assertNotIn(r"\foreignlanguage{english}", prose_tex("سنة ١٩١٩", RTL_ARABIC))

    def test_heading_tex_wraps_bare_exponents_and_uppercases_prose(self) -> None:
        title = "Finding the term containing x^k in the expansion of Binomial"
        toc = heading_tex(title, ENGLISH, uppercase=False)
        head = heading_tex(title, ENGLISH, uppercase=True)
        self.assertIn("$x^k$", toc)
        self.assertNotIn(r"\textasciicircum", toc)
        self.assertIn("FINDING THE TERM CONTAINING", head)
        self.assertIn("$x^k$", head)
        self.assertNotIn("X^K", head)

    def test_contents_body_typesets_x_caret_k_from_xml_title(self) -> None:
        wording = {"table_of_contents": {"en": "Contents", "ar": "المحتويات"}}
        body, _count = contents_body(
            _toc_root(
                (
                    "1.1.2",
                    "Finding the term containing x^k in the expansion of Binomial",
                    1,
                ),
            ),
            ENGLISH,
            wording,
        )
        self.assertIn("$x^k$", body)
        self.assertNotIn(r"\textasciicircum", body)


class StixDigitTests(unittest.TestCase):
    def test_wrap_stix_digits_ranges_clocks_and_tex_syntax(self) -> None:
        wrapped = wrap_stix_digits("سنة 1870-1953")
        self.assertIn(r"\hbox{\foreignlanguage{english}{1870}}", wrapped)
        self.assertIn(r"\hbox{\foreignlanguage{english}{-}}", wrapped)
        self.assertIn(r"\hbox{\foreignlanguage{english}{1953}}", wrapped)
        self.assertEqual(wrap_stix_digits(wrapped), wrapped)
        self.assertEqual(
            wrap_stix_digits("3:00"),
            r"\hbox{\foreignlanguage{english}{3:00}}",
        )
        self.assertEqual(
            wrap_stix_digits("2:3"),
            r"\hbox{\foreignlanguage{english}{2:3}}",
        )
        self.assertEqual(
            wrap_stix_digits("1/2"),
            r"\hbox{\foreignlanguage{english}{1/2}}",
        )
        self.assertEqual(
            wrap_stix_digits("2.5"),
            r"\hbox{\foreignlanguage{english}{2.5}}",
        )
        self.assertEqual(
            wrap_stix_digits("1,000"),
            r"\hbox{\foreignlanguage{english}{1,000}}",
        )
        self.assertEqual(wrap_stix_digits("١٢"), "١٢")
        self.assertEqual(wrap_stix_digits(r"\\[4pt]"), r"\\[4pt]")
        self.assertEqual(wrap_stix_digits(r"\hskip.333em"), r"\hskip.333em")
        self.assertEqual(
            wrap_stix_digits(r"\Needspace{2\baselineskip}"),
            r"\Needspace{2\baselineskip}",
        )
        self.assertEqual(wrap_stix_digits("[figure 1]"), "[figure 1]")
        self.assertEqual(wrap_stix_digits("[poem 3]"), "[poem 3]")
        mixed = wrap_stix_digits("سنة 1919 [figure 1]")
        self.assertIn("[figure 1]", mixed)
        self.assertNotIn(r"[figure \hbox", mixed)
        self.assertIn(r"\hbox{\foreignlanguage{english}{1919}}", mixed)
        self.assertEqual(wrap_stix_digits("G7"), "G7")
        self.assertEqual(wrap_stix_digits("T1"), "T1")
        self.assertEqual(wrap_stix_digits("A1B"), "A1B")
        year = wrap_stix_digits("سنة 2026-2027")
        self.assertEqual(year.count(r"\hbox{\foreignlanguage{english}{2026}}"), 1)
        self.assertEqual(year.count(r"\hbox{\foreignlanguage{english}{2027}}"), 1)
        self.assertIn(r"\hbox{\foreignlanguage{english}{-}}", year)
        self.assertEqual(
            wrap_stix_digits("صفحة 12"),
            r"صفحة \hbox{\foreignlanguage{english}{12}}",
        )
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

    def test_maybe_stix_digits_follows_the_profile(self) -> None:
        self.assertEqual(maybe_stix_digits("1919", ENGLISH), "1919")
        self.assertEqual(maybe_stix_digits("1919", RTL_ARABIC), "1919")
        self.assertIn(
            r"\foreignlanguage{english}{1919}",
            maybe_stix_digits("1919", RTL_LATIN),
        )
        self.assertFalse(_WESTERN_STIX.get())
        quotes, western, snippets = quoting_for(RTL_LATIN)
        try:
            self.assertTrue(_WESTERN_STIX.get())
            self.assertIn(r"\foreignlanguage{english}{1919}", maybe_stix_digits("1919"))
            raise RuntimeError("boom")
        except RuntimeError:
            _reset_quoting(quotes, western, snippets)
        self.assertFalse(_WESTERN_STIX.get())
        document = {
            "number": "1.1",
            "title": "Year 1919",
            "book_id": "mathematics_ar_eg09_t1",
            "exercises": {
                "groups": [
                    {
                        "objective": "obj",
                        "exercises": [{"id": "1", "text": "x"}],
                    }
                ]
            },
        }
        blocks = section_blocks(document, {"obj": "Aim 1919"}, RTL_LATIN)
        self.assertFalse(_WESTERN_STIX.get())
        self.assertIn(r"\foreignlanguage{english}{1919}", blocks[0].name)
        self.assertIn(r"\foreignlanguage{english}{1919}", blocks[1].name)
        tex = section_tex(document, {}, {"obj": "Aim 1919"}, RTL_LATIN)
        self.assertFalse(_WESTERN_STIX.get())
        self.assertIn(r"\foreignlanguage{english}{1919}", tex)

    def test_text_years_survive_autosize_and_math_stays_bare(self) -> None:
        quotes, western, snippets = quoting_for(RTL_LATIN)
        try:
            rendered = to_latex(r"$$\begin{array}{c}\text{1919}\end{array}$$")
            self.assertIn(r"\hbox{\foreignlanguage{english}{1919}}", rendered)
            self.assertEqual(
                autosize_brackets(r"\text{\hbox{\foreignlanguage{english}{1919}}}"),
                r"\text{\hbox{\foreignlanguage{english}{1919}}}",
            )
            self.assertEqual(to_latex("$x=12$"), "$x=12$")
            self.assertIn(r"$\blank$", to_latex(r"سنة 1919 $\blank$"))
            self.assertIn(r"\blank{}", to_latex(r"سنة 1919 \blank"))
            listed = to_latex("الرسم H، M، A، T سنة 1919.")
            self.assertEqual(listed.count(r"\hbox{\textdir TLT\relax"), 1)
            self.assertIn(r"\foreignlanguage{english}{1919}", listed)
            marked = to_latex("قبل [figure 1] بعد سنة 1919")
            self.assertIn("[figure 1]", marked)
            self.assertNotIn(r"[figure \hbox", marked)
            self.assertNotIn("[figure 1]", without_marker(marked, 1))
        finally:
            _reset_quoting(quotes, western, snippets)


class PreambleAndLuaTests(unittest.TestCase):
    def test_divform_names_are_in_the_drawing_preamble(self) -> None:
        for name in (r"\divdec", r"\cirmark", r"\cir", r"\divform", r"\novastax_divform"):
            self.assertIn(name, DRAWING_PREAMBLE)

    def test_integral_is_display_size_in_shared_maths_preamble(self) -> None:
        pre = type_preamble(ENGLISH)
        self.assertIn(r"\everymath{\displaystyle}", pre)
        self.assertIn(r"\let\Big\bigg", pre)
        self.assertIn(r"\let\latexint\int", pre)
        self.assertIn(r"{\displaystyle\latexint}", pre)
        pre_ar = type_preamble(RTL_ARABIC)
        self.assertIn(r"\everymath{\displaystyle}", pre_ar)

    def test_lua_keeps_the_clock_colon_and_adds_the_bar(self) -> None:
        lua = (SCRIPTS / "arabic_maths.lua").read_text(encoding="utf-8")
        self.assertIn("is_colon_char", lua)
        self.assertIn("is_colon_atom", lua)
        self.assertIn("mark ~= 0x3A", lua)
        self.assertIn("is_repeating_bar", lua)
        self.assertIn("trailing_ellipsis", lua)
        self.assertIn("is_digit_atom", lua)
        self.assertIn("continues_number", lua)
        self.assertIn("continues_number(tail)", lua)
        self.assertIn("last.subtype ~= over_sub", lua)

    def test_times_alef_and_function_text_still_hold(self) -> None:
        self.assertIn(r"\times", to_latex(r"$3 \times 4$"))
        self.assertIn(r"\alef", arabic_notation("$أ$"))
        self.assertNotIn("أ", arabic_notation("$أ$"))
        self.assertIn(r"\text{د}", arabic_notation(r"$\text{د}(\theta)$"))


if __name__ == "__main__":
    unittest.main()
