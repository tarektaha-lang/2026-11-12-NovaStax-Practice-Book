from __future__ import annotations

import sys
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
import re
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from src.latex import (  # noqa: E402
    HYPHEN,
    ARABIC_OVER_ARROWS,
    LATIN_VEC_HARPOON,
    arabic_axis_letters,
    arabic_formula,
    arabic_maths,
    arabic_notation,
    arabic_prose,
    arabic_tikz,
    arabic_tikz_nodes,
    canonical_tikz_arrows,
    begin_document,
    body_to_latex,
    drawing_tex,
    clock_array_cells,
    compile_pdf,
    DRAWING_PREAMBLE,
    SET_GRID,
    directional_tables,
    distribute_marked_group_figures,
    escape_prose,
    for_direction,
    grid_lines,
    maths_for_face,
    measure_tex,
    normalize_quotes,
    parallel_slashes,
    paper_tex,
    preamble,
    profile_tikz,
    replace_marker,
    row_breaks_before_rules,
    math_delimiters_as_text,
    rtl_latin_layout,
    rtl_latin_polygon_layout,
    rtl_latin_text_math_space,
    rtl_latin_unit_layout,
    tight_thousands,
    section_tex,
    set_cell,
    standard_lines,
    tally_maths,
    to_latex,
    wrap_arabic_decimal_runs,
    wrap_ipa_in_maths,
    WORD_PIN,
)
from new_questions import ExerciseGroup, repaired  # noqa: E402
from src.prompting import Profile  # noqa: E402


RTL_LATIN = Profile("ar", "western", "latin")
RTL_ARABIC = Profile("ar", "arabic", "arabic")
RTL_ARABIC_WESTERN = Profile("ar", "western", "arabic")


class LatexDirectionTests(unittest.TestCase):
    def test_arabic_unit_layout_splits_suffix_and_reverses_equation(self) -> None:
        self.assertEqual(
            rtl_latin_unit_layout(r"$32\pi\text{ م}^3$"),
            r"$32\pi$ م$^3$",
        )
        self.assertEqual(
            rtl_latin_unit_layout(
                r"$V = \frac{4}{3}\pi r^3\text{ م}^3$"
            ),
            r"$\frac{4}{3}\pi r^3 = V$ م$^3$",
        )

    def test_arabic_unit_layout_reverses_relation_chain(self) -> None:
        self.assertEqual(
            rtl_latin_unit_layout(r"$x \leq 5\text{ م}$"),
            r"$5 \geq x$ م",
        )
        self.assertEqual(
            rtl_latin_unit_layout(r"$0 < x < 5\text{ م}$"),
            r"$5 > x > 0$ م",
        )
        self.assertEqual(
            rtl_latin_unit_layout(r"$= 4\text{ سم}$"),
            r"$4 =$ سم",
        )
        self.assertEqual(
            rtl_latin_unit_layout(r"$= \blank\text{ سم}$"),
            r"$\blank =$ سم",
        )
        self.assertEqual(
            rtl_latin_unit_layout(r"$\leq 5\text{ م}$"),
            r"$5 \leq$ م",
        )

    def test_arabic_unit_layout_is_syntax_driven_and_idempotent(self) -> None:
        source = r"$5\text{ م}$"
        once = rtl_latin_unit_layout(source)
        self.assertEqual(once, "$5$ م")
        self.assertEqual(rtl_latin_unit_layout(once), once)
        self.assertEqual(
            rtl_latin_unit_layout(r"$m+N$"),
            r"$m+N$",
        )

    def test_arabic_polygon_label_moves_after_latin_name(self) -> None:
        source = r"$\text{المضلع } KLMN \equiv \text{المضلع } STUV$"
        want = r"$KLMN\ \text{المضلع} \equiv STUV\ \text{المضلع}$"
        self.assertEqual(rtl_latin_polygon_layout(source), want)
        self.assertEqual(rtl_latin_polygon_layout(want), want)
        self.assertEqual(
            rtl_latin_polygon_layout(r"$ABCD \equiv \text{المضلع } PQRS$"),
            r"$ABCD \equiv PQRS\ \text{المضلع}$",
        )
        self.assertEqual(rtl_latin_polygon_layout(r"$5\text{ م}$"), r"$5\text{ م}$")
        self.assertEqual(
            rtl_latin_polygon_layout(r"$\overline{AC}\text{ و}\overline{BD}$"),
            r"$\overline{AC}\text{ و}\overline{BD}$",
        )

    def assertLaidOut(self, source: str, want: str) -> None:
        self.assertEqual(to_latex(rtl_latin_layout(source), inline=True), want)

    def test_set_phrase_folds_its_number_and_reads_from_the_right(self) -> None:
        self.assertLaidOut(
            r"$C = \text{مجموعة المثلثات التي لها } 4 \text{ أضلاع}$",
            r"$\text{مجموعة المثلثات التي لها $4$ أضلاع} = C$",
        )
        self.assertLaidOut(
            r"$G = \text{مجموعة العوامل الأولية للعدد } 30$",
            r"$\text{مجموعة العوامل الأولية للعدد $30$} = G$",
        )
        self.assertEqual(
            rtl_latin_unit_layout(r"$V = \frac{4}{3}\pi r^3\text{ م}^3$"),
            r"$\frac{4}{3}\pi r^3 = V$ م$^3$",
        )

    def test_arabic_side_of_a_relation_is_read_first(self) -> None:
        self.assertLaidOut(
            r"$\text{المساحة} = 2x + y\text{ سم}^2$",
            r"$2x + y = \text{المساحة}$ سم$^2$",
        )
        self.assertLaidOut(
            r"$\text{عدد الكرات} = 2x + y\text{ كرات}$",
            r"$2x + y = \text{عدد الكرات}$ كرات",
        )
        self.assertLaidOut(
            r"$\text{النسبة} = \frac{3x}{y}$",
            r"$\frac{3x}{y} = \text{النسبة}$",
        )
        self.assertLaidOut(r"$A = 12\text{ سم}^2$", r"$12 = A$ سم$^2$")

    def test_set_builder_condition_is_one_arabic_phrase(self) -> None:
        self.assertLaidOut(
            r"$11 \blank \{x : x \text{ عدد أولي أقل من } 15\}$",
            r"$11 \blank \left\{x : \text{$x$ عدد أولي أقل من $15$}\right\}$",
        )
        self.assertLaidOut(
            r"$2 \blank \{x : x \text{ عدد أولي زوجي}\}$",
            r"$2 \blank \left\{x : \text{$x$ عدد أولي زوجي}\right\}$",
        )
        self.assertLaidOut(
            r"$18 \blank \{x : x \in \mathbb{N} \text{ و } x \text{ مضاعف للعدد } 4\}$",
            r"$18 \blank \left\{x : x \in \mathbb{N}, \text{$x$ مضاعف للعدد $4$}\right\}$",
        )
        self.assertLaidOut(
            r"$S = \{x : x \text{ رقم في العدد } 4,400,331\}$",
            r"$S = \left\{x : \text{$x$ رقم في العدد $4{,}400{,}331$}\right\}$",
        )
        # و before a number is inside the phrase; before a letter it starts a condition.
        self.assertLaidOut(
            r"$P = \{x : x \text{ عدد أولي بين } 10 \text{ و } 20\}$",
            r"$P = \left\{x : \text{$x$ عدد أولي بين $10$ و $20$}\right\}$",
        )
        self.assertLaidOut(
            r"$W = \{x : x > 0, x \text{ مضاعف للعدد } 4 \text{ و } x \le 16\}$",
            r"$W = \left\{x : x > 0, \text{$x$ مضاعف للعدد $4$}, x \le 16\right\}$",
        )
        self.assertLaidOut(
            r"$P = \{x : x \in \mathbb{N} \text{،}\ x \text{ عدد فردي يقبل القسمة على } 2\}$",
            r"$P = \left\{x : x \in \mathbb{N}, \text{$x$ عدد فردي يقبل القسمة على $2$}\right\}$",
        )
        self.assertLaidOut(
            r"$E = \{x : x \text{ حرف في كلمة } \text{EGYPT}\}$",
            r"$E = \left\{x : \text{$x$ حرف في كلمة $\text{EGYPT}$}\right\}$",
        )
        self.assertLaidOut(
            r"$B = \{x : x \in \mathbb{N}\text{، } x \text{ عدد أولي } < 10\}$",
            r"$B = \left\{x : x \in \mathbb{N}, \text{$x$ عدد أولي} < 10\right\}$",
        )
        self.assertLaidOut(
            r"$Q = \{x : x \in \mathbb{N}\text{، } x \text{ مضاعف للعدد } 5\text{، } 0 < x \le 20\}$",
            r"$Q = \left\{x : x \in \mathbb{N}, \text{$x$ مضاعف للعدد $5$}, 0 < x \le 20\right\}$",
        )

    def test_units_inside_the_formula_and_lists_keep_their_order(self) -> None:
        for source in (
            r"$600\text{ جنيه} < 900\text{ جنيه}$",
            r"$2\text{ ساعة} = 120\text{ دقيقة}$",
            r"$\text{أصغر قيمة} = 10, Q_1 = 12, \text{الوسيط} = 16$",
        ):
            self.assertEqual(rtl_latin_layout(source), source)
        self.assertLaidOut(
            r"$30\text{ وحدة مربعة}$",
            r"$30$ وحدة مربعة",
        )
        self.assertLaidOut(
            r"$\text{قيمة الجزء} = \frac{4,500}{9} = 500\text{ جنيه}$",
            r"$500 = \frac{4{,}500}{9} = \text{قيمة الجزء}$ جنيه",
        )

    def test_words_that_are_not_phrases_are_left_in_place(self) -> None:
        for source in (
            r"$5\text{ سم} + 3$",
            r"$\overline{AC}\text{ و}\overline{BD}$",
            r"$x + 2 = 5$",
            r"$\{x : 2x \text{ عدد أولي}\}$",
        ):
            self.assertEqual(rtl_latin_layout(source), source)
        self.assertEqual(
            rtl_latin_layout(r"$\text{المضلع } KLMN \equiv \text{المضلع } STUV$"),
            r"$KLMN\ \text{المضلع} \equiv STUV\ \text{المضلع}$",
        )
        # Folding stops at anything that is not a number or a letter, and the
        # phrase is not peeled as a unit nor turned round.
        self.assertEqual(
            rtl_latin_layout(
                r"$C = \text{مجموعة المثلثات التي لها } \frac{8}{2} \text{ أضلاع}$"
            ),
            r"$C = \text{مجموعة المثلثات التي لها}\ \frac{8}{2} \text{ أضلاع}$",
        )
        table = (
            r"$$\begin{array}{|c|c|} \hline "
            r"\text{عدد الطلاب } 5 & 10 \\ \hline \end{array}$$"
        )
        self.assertNotIn("ensuremath", rtl_latin_layout(table))

    def test_arabic_text_keeps_a_math_skip_before_the_following_number(self) -> None:
        spaced = rtl_latin_text_math_space(r"$\text{المضلع } \frac{1}{2}$")
        self.assertEqual(spaced, r"$\text{المضلع}\ \frac{1}{2}$")
        self.assertEqual(rtl_latin_text_math_space(spaced), spaced)

    def test_western_thousands_are_ordinary_commas_in_math(self) -> None:
        self.assertEqual(tight_thousands("4,400,331"), r"4{,}400{,}331")
        self.assertEqual(tight_thousands(r"4{,}400{,}331"), r"4{,}400{,}331")
        self.assertEqual(tight_thousands(r"\{10, 20, 30\}"), r"\{10, 20, 30\}")
        self.assertEqual(tight_thousands("(2,3)"), "(2,3)")
        self.assertEqual(
            tight_thousands(r"\text{ رقم في العدد } 4,400,331"),
            r"\text{ رقم في العدد } 4{,}400{,}331",
        )
        rendered = to_latex(
            r"$S = \{x : x \text{ رقم في العدد}\ 4,400,331\}$",
            inline=True,
        )
        self.assertIn(r"4{,}400{,}331", rendered)
        self.assertNotIn("4,400,331", rendered)
        self.assertIn(
            r"4{,}400{,}331",
            to_latex(rtl_latin_layout(
                r"$S = \{x : x \text{ رقم في العدد } 4,400,331\}$"
            ), inline=True),
        )

    def test_indian_lakhs_are_ordinary_commas_in_math(self) -> None:
        self.assertEqual(tight_thousands("2,04,300"), r"2{,}04{,}300")
        self.assertEqual(tight_thousands("1,24,42,373"), r"1{,}24{,}42{,}373")
        self.assertEqual(tight_thousands("46,200"), r"46{,}200")
        self.assertEqual(tight_thousands("4,400,331"), r"4{,}400{,}331")
        rendered = to_latex(r"$2,04,300$")
        self.assertIn(r"2{,}04{,}300", rendered)
        self.assertNotIn("2,04,300", rendered)

    def test_unit_only_math_island_is_not_turned_into_display_math(self) -> None:
        self.assertEqual(
            rtl_latin_unit_layout(r"(بوحدة $\text{ كم/س}$)"),
            r"(بوحدة $\text{ كم/س}$)",
        )
        self.assertEqual(
            rtl_latin_unit_layout(r"($\text{ م}^3$)"),
            r"($\text{ م}^3$)",
        )
        table = (
            r"$$\begin{array}{|c|c|} \hline "
            r"\text{الاستهلاك}\ (\text{ م}^3) & 10- \\ \hline "
            r"\end{array}$$"
        )
        self.assertEqual(rtl_latin_unit_layout(table), table)
        self.assertEqual(
            rtl_latin_unit_layout(r"$100\text{ كم/س}$"),
            r"$100$ كم/س",
        )
        camera = (
            r"تسجل كاميرا مراقبة السرعة على طريق سريع المخالفات للمركبات "
            r"التي تسير بسرعات $v$ (بوحدة $\text{ كم/س}$) أكبر من أو تساوي "
            r"$100\text{ كم/س}$."
        )
        laid_out = rtl_latin_unit_layout(camera)
        self.assertIn(r"(بوحدة $\text{ كم/س}$)", laid_out)
        self.assertIn(r"$100$ كم/س", laid_out)
        self.assertNotIn("$$", laid_out)
        rendered = to_latex(laid_out)
        self.assertNotIn("CounterTallyMarks", rendered)
        self.assertNotIn("\\[", rendered)
        table_source = (
            r"بالأمتار المكعبة ($\text{ م}^3$) لـ $50$ أسرة في طنطا: "
            r"$$\begin{array}{|c|c|} \hline "
            r"\text{الاستهلاك}\ (\text{ م}^3) & 10- \\ \hline "
            r"\end{array}$$"
        )
        table_rendered = to_latex(rtl_latin_unit_layout(table_source))
        self.assertIn(r"\begin{array}", table_rendered)
        self.assertNotIn(r"\textbackslash{}", table_rendered)
        self.assertNotIn("CounterTallyMarks", table_rendered)

    def test_compound_unit_slash_is_not_a_tally(self) -> None:
        body = r"\sqrt[3]{20}\text{ جم/سم}^3"
        self.assertEqual(parallel_slashes(body), body)
        self.assertEqual(tally_maths(body), body)
        rendered = to_latex(f"${body}$")
        self.assertIn("جم/سم", rendered)
        self.assertNotIn("CounterTallyMarks", rendered)
        self.assertIn(r"\CounterTallyMarks{4}", tally_maths("////"))
        self.assertEqual(
            parallel_slashes(r"AB // CD"),
            r"AB \parallel  CD",
        )

    def test_per_slash_between_text_units_is_not_a_tally(self) -> None:
        body = r"180\text{ ميلًا}/\text{ساعة}"
        self.assertEqual(tally_maths(body), body)
        rendered = to_latex(f"${body}$")
        self.assertIn(r"\text{ ميلًا}/\text{ساعة}", rendered)
        self.assertNotIn("CounterTallyMarks", rendered)

    def test_table_header_unit_parens_stay_plain_in_text(self) -> None:
        source = (
            r"$$\begin{array}{c|c|c|c|c} "
            r"6 & 4 & 2 & 0 &\text{المسافة}\ x\text{ (كم)} \\ \hline "
            r"40 & 30 & 20 & 10 &\text{الأجرة}\ y\text{ (جنيه)} "
            r"\end{array}$$"
        )
        laid = directional_tables(source, RTL_LATIN)
        rendered = to_latex(laid)
        self.assertIn(r"\hbox{\textdir TRT\relax (كم)}", rendered)
        self.assertIn(r"\hbox{\textdir TRT\relax (جنيه)}", rendered)
        self.assertNotIn(r"\left(كم\right)", rendered)
        self.assertNotIn(r"\left(جنيه\right)", rendered)
        self.assertNotIn(r"\left(بالساعات\right)", to_latex(
            directional_tables(
                r"$$\begin{array}{c}\text{الزمن}\ t\text{ (بالساعات)}\end{array}$$",
                RTL_LATIN,
            )
        ))
        quantity = directional_tables(
            r"\begin{array}{c}(x+1)\text{ سم}\end{array}",
            RTL_LATIN,
        )
        sized = to_latex("$$" + quantity + "$$")
        self.assertIn(r"\left(x+1\right)", sized)
        self.assertIn(r"\hbox{\textdir TRT\relax سم}", sized)
        self.assertEqual(math_delimiters_as_text(r"\left(كم\right)"), "(كم)")
        self.assertEqual(
            rtl_latin_unit_layout(r"$100\text{ \left(كم\right)}$"),
            r"$100$ (كم)",
        )

    def test_table_header_unit_parens_compile(self) -> None:
        source = (
            r"$$\begin{array}{c|c} "
            r"6 &\text{المسافة}\ x\text{ (كم)} \\ \hline "
            r"40 &\text{الأجرة}\ y\text{ (جنيه)} "
            r"\end{array}$$"
        )
        body = to_latex(directional_tables(source, RTL_LATIN))
        self.assertNotIn(r"\left(كم\right)", body)
        self.assertNotIn(r"\left(جنيه\right)", body)
        with tempfile.TemporaryDirectory() as folder:
            tex = Path(folder) / "header-unit.tex"
            tex.write_text(
                preamble(RTL_LATIN)
                + begin_document(RTL_LATIN)
                + body
                + "\n\\end{document}\n",
                encoding="utf-8",
            )
            compile_pdf(tex)
            self.assertTrue(tex.with_suffix(".pdf").is_file())

    def test_trailing_arabic_unit_cells_read_after_the_number(self) -> None:
        quantity = (
            r"\begin{array}{c}"
            r"6\text{ سم}\\"
            r"4.8\text{ سم}"
            r"\end{array}"
        )
        once = directional_tables(quantity, RTL_LATIN)
        twice = directional_tables(once, RTL_LATIN)
        self.assertEqual(once, twice)
        self.assertNotIn(r"6\text{ سم}", once)
        self.assertNotIn(r"4.8\text{ سم}", once)
        self.assertLess(once.index("$6$"), once.index("سم"))
        self.assertLess(once.index("$4.8$"), once.rindex("سم"))
        unit_only = r"\begin{array}{c}\text{ م}^2\end{array}"
        self.assertEqual(directional_tables(unit_only, RTL_LATIN), unit_only)
        self.assertEqual(directional_tables(quantity, RTL_ARABIC), quantity)
        nested = r"\begin{array}{c}\begin{array}{c}6\text{ سم}\end{array}\end{array}"
        self.assertEqual(directional_tables(nested, RTL_LATIN), nested)
        spanned = r"\begin{array}{c|c}\multicolumn{2}{c}{6\text{ سم}}\end{array}"
        self.assertEqual(directional_tables(spanned, RTL_LATIN), spanned)

    def test_ordinary_rtl_table_mirrors_and_groups_mixed_cell(self) -> None:
        source = (
            r"\begin{array}{c|c}"
            r"\text{الحدود العليا}&\text{التكرار}\\"
            r"\text{أقل من } 40&28"
            r"\end{array}"
        )
        result = directional_tables(source, RTL_LATIN)
        self.assertIn(r"\text{التكرار}&\text{الحدود العليا}", result)
        self.assertIn(r"28&\rtlmathcell{أقل من}{40}", result)

    def test_rtl_latin_stem_leaf_table_keeps_stem_left(self) -> None:
        source = (
            r"\begin{array}{c|l}"
            r"\text{الساق}&\text{الأوراق}\\"
            r"1&5\ 8\ 9"
            r"\end{array}"
        )
        self.assertEqual(directional_tables(source, RTL_LATIN), source)

    def test_rtl_arabic_notation_mirrors_stem_leaf_source(self) -> None:
        source = (
            r"\begin{array}{c|l}"
            r"\text{الساق}&\text{الأوراق}\\1&5\ 8"
            r"\end{array}"
        )
        result = directional_tables(source, RTL_ARABIC)
        self.assertIn(r"\text{الأوراق}&\text{الساق}", result)
        self.assertIn(r"5\ 8&1", result)

    def test_tikz_intervals_are_ltr_isolated_and_idempotent(self) -> None:
        source = (
            r"\node [below] at (1,0) {20-};"
            r"\node at (2,0) {80-100};"
            r"\draw (20,0)--(80,0);"
        )
        once = profile_tikz(source, RTL_LATIN)
        twice = profile_tikz(once, RTL_LATIN)
        self.assertEqual(once, twice)
        self.assertIn(r"\foreignlanguage{english}{20" + HYPHEN + "}", once)
        self.assertIn(r"\foreignlanguage{english}{80" + HYPHEN + "100}", once)
        self.assertIn(r"\draw (20,0)--(80,0)", once)

    def test_arabic_notation_tikz_rewrites_printed_digits_not_coordinates(self) -> None:
        source = r"\node at (2,0) {$2س + 4$};\draw (2,0)--(4,0);"
        result = profile_tikz(source, RTL_ARABIC)
        self.assertIn("{$٢\\seen  + ٤$}", result)
        self.assertIn(r"\draw (2,0)--(4,0)", result)

    def test_arabic_prose_leaves_tex_lengths_western(self) -> None:
        source = r"املأ \underline{\hspace{1.5cm}} ثم 35."
        result = arabic_prose(source)
        self.assertIn(r"\hspace{1.5cm}", result)
        self.assertNotIn(r"\hspace{١", result)
        self.assertIn("٣٥", result)
        node = (
            r"\node at (41.5,4.25) {\underline{\hspace{1.5cm}}};"
            r"\node at (8.3,4.25) {35};"
        )
        drawn = profile_tikz(node, RTL_ARABIC)
        self.assertIn(r"\underline{\hspace{1.5cm}}", drawn)
        self.assertIn("{٣٥}", drawn)
        self.assertIn(r"\node at (41.5,4.25)", drawn)

    def test_arabic_tikz_node_text_digits_and_rtl_sequence_order(self) -> None:
        source = (
            r"\begin{tikzpicture} \draw (0,0) rectangle (0.6,0.6); "
            r"\node [below=2pt] at (0.3,0) {الشكل 1}; "
            r"\draw (1.4,0) rectangle (3.2,0.6); "
            r"\node [below=2pt] at (2.3,0) {الشكل 2}; "
            r"\draw (4.0,0) rectangle (7.0,0.6); "
            r"\node [below=2pt] at (5.5,0) {الشكل 3};"
            r"\end{tikzpicture}"
        )
        result = profile_tikz(source, RTL_ARABIC)
        self.assertIn("{الشكل ١}", result)
        self.assertNotIn("{الشكل 1}", result)
        self.assertIn(r"\draw (7,0) rectangle (6.4,0.6);", result)
        first = re.search(
            r"at\s*\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)\s*\{الشكل ١\}", result
        )
        second = re.search(
            r"at\s*\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)\s*\{الشكل ٢\}", result
        )
        third = re.search(
            r"at\s*\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)\s*\{الشكل ٣\}", result
        )
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNotNone(third)
        self.assertGreater(float(first.group(1)), float(second.group(1)))
        self.assertGreater(float(second.group(1)), float(third.group(1)))
        again = profile_tikz(result, RTL_ARABIC)
        self.assertEqual(again, result)

    def test_mixed_node_math_tag_is_isolated_ltr(self) -> None:
        source = (
            r"\fill (-3,5) circle (2pt) node [above=3pt] {Playground ($B$)};"
        )
        result = profile_tikz(source, RTL_LATIN)
        self.assertIn(r"\hbox{\textdir TLT\relax ($B$)}", result)
        self.assertNotIn("{Playground ($B$)}", result)
        arabic = profile_tikz(
            r"\node [above=3pt] at (4,3) {النافورة ($A$)};",
            RTL_LATIN,
        )
        self.assertIn(r"\hbox{\textdir TLT\relax ($A$)}", arabic)
        self.assertIn("النافورة", arabic)

    def test_canonical_tikz_arrows_become_to(self) -> None:
        self.assertIn(r"\arrow{To}", canonical_tikz_arrows(r"\arrow{Stealth}"))
        self.assertIn(r"\arrow{To}", canonical_tikz_arrows(r"\arrow{Latex}"))
        self.assertIn(r"\arrow{To}", canonical_tikz_arrows(r"\arrow{>}"))
        self.assertIn("-{To}", canonical_tikz_arrows("-{Stealth}"))
        self.assertIn("-{To}", canonical_tikz_arrows(r"\draw [-Stealth] (E) -- (Dend);"))
        self.assertIn("{To}-{To}", canonical_tikz_arrows("Latex-Stealth"))
        self.assertIn(">={To}", canonical_tikz_arrows("[>=Stealth]"))
        bent = standard_lines(
            r"\draw [line width=0.4pt, -{Stealth}] (0,0)--(1,0);"
        )
        self.assertIn("-{To}", bent)
        self.assertIn("line width=", bent)
        self.assertEqual(canonical_tikz_arrows(r"\draw [<->] (0,0)--(1,0);"), r"\draw [<->] (0,0)--(1,0);")

    def test_rtl_sequence_does_not_flip_a_coordinate_grid(self) -> None:
        source = (
            r"\begin{tikzpicture}[x=0.5cm, y=0.5cm] "
            r"\draw [gridline] (-6,-6) grid [step=1] (7,7); "
            r"\foreach \x in {-6,-5,-4} { "
            r"\draw [axisline] (\x,0.1) -- (\x,-0.1) node[below=2pt] {$\x$}; } "
            r"\fill (4,2) circle (2.5pt); "
            r"\node [above=2pt] at (4,2) {النافورة}; "
            r"\end{tikzpicture}"
        )
        result = profile_tikz(source, RTL_ARABIC)
        self.assertIn(r"\draw [gridline] (-6,-6) grid [step=1] (7,7);", result)
        self.assertIn(r"\node [above=2pt] at (4,2) {النافورة};", result)

    def test_western_arabic_notation_tikz_keeps_western_digits(self) -> None:
        source = r"\node at (2,0) {$2س + 4$};\draw (2,0)--(4,0);"
        result = profile_tikz(source, RTL_ARABIC_WESTERN)
        self.assertIn("{$2\\seen  + 4$}", result)
        self.assertNotIn("٢", result)
        self.assertNotIn("٤", result)
        self.assertIn(r"\draw (2,0)--(4,0)", result)

    def test_nested_tikz_calc_dollars_keep_geometry_digits_western(self) -> None:
        calc = r"$($(A)!0.5!(B)$)!1.5mm!90:(B)$)"
        source = rf"\draw ({calc}) -- (2,0);\node at (0,0) {{$2س$}};"
        result = profile_tikz(source, RTL_ARABIC)
        self.assertIn(calc, result)
        self.assertIn(r"{$٢\seen $}", result)

    def test_tikz_calc_coefficient_times_point_keeps_western_digits(self) -> None:
        calc = r"$0.5*(P1) + 0.5*(P3)$"
        source = rf"\coordinate (C) at ({calc});\node at (1,0) {{$2$}};"
        result = profile_tikz(source, RTL_ARABIC)
        self.assertIn(calc, result)
        self.assertIn("P1", result)
        self.assertNotIn("٠", result)
        self.assertNotIn("٫", result)
        self.assertIn(r"{$٢$}", result)

    def test_arabic_math_preserves_array_column_preamble(self) -> None:
        result = arabic_maths(
            r"$$\begin{array}{|c|c|}c&س\\1&2\end{array}$$"
        )
        self.assertIn(r"\begin{array}{|c|c|}", result)
        self.assertNotIn(r"\begin{array}{|\jeemi", result)

    def test_arabic_radius_is_arrad_not_noon_qaf(self) -> None:
        result = arabic_maths("$نق$")
        self.assertIn(r"\arrad ", result)
        self.assertNotIn(r"\noon ", result)
        self.assertNotIn(r"\qaf ", result)
        subscript = arabic_maths("$نق_1$")
        self.assertIn(r"\arrad ", subscript)
        self.assertNotIn(r"\noon ", subscript)
        ratio = arabic_maths(r"$\frac{ل}{نق}$")
        self.assertIn(r"\arrad ", ratio)
        self.assertIn(r"\lam ", ratio)
        self.assertNotIn(r"\noon ", ratio)
        separate = arabic_maths("$ن + ق$")
        self.assertIn(r"\noon ", separate)
        self.assertIn(r"\qaf ", separate)
        self.assertNotIn(r"\arrad ", separate)
        already = arabic_maths(r"$\arrad $")
        self.assertEqual(already, arabic_maths(already))
        self.assertIn(r"\arrad", already)
        self.assertIn('\\Umathchardef\\arrad="7 "0 "0608', preamble(RTL_ARABIC))

    def test_prose_list_commas_between_math_islands_are_arabic(self) -> None:
        latin = arabic_notation(r"$2$, $5$, $8$, $11$, $\blank$, $\blank$")
        self.assertIn(r"$٢$، $٥$، $٨$، $١١$، $\blank$، $\blank$", latin)
        self.assertNotIn("$, $", latin)
        already = arabic_notation(r"$4$، $9$، $14$، $19$، $\blank$، $\blank$")
        self.assertIn(r"$٤$، $٩$، $١٤$، $١٩$، $\blank$، $\blank$", already)
        maghreb = arabic_notation(
            r"$2$, $5$, $\blank$", convert_digits=False
        )
        self.assertIn(r"$2$، $5$، $\blank$", maghreb)
        self.assertEqual(arabic_prose("12,500"), "١٢٬٥٠٠")
        self.assertEqual(arabic_prose("12,500", convert_digits=False), "12,500")

    def test_western_arabic_maths_keeps_digits_and_list_commas(self) -> None:
        mashriq = arabic_maths("$7س + ص = 15$")
        maghreb = arabic_maths("$7س + ص = 15$", convert_digits=False)
        self.assertIn("٧", mashriq)
        self.assertIn("١٥", mashriq)
        self.assertIn("7", maghreb)
        self.assertIn("15", maghreb)
        self.assertNotIn("٧", maghreb)
        self.assertNotIn("١٥", maghreb)
        self.assertIn(r"\seen", maghreb)
        listed = arabic_maths("$1, 2, 3$", convert_digits=False)
        self.assertIn("،", listed)
        self.assertNotIn("١", listed)
        self.assertEqual(arabic_formula("12,500", convert_digits=False), "12,500")
        self.assertIn("arabic_maths_western_digits = true", preamble(RTL_ARABIC_WESTERN))
        self.assertIn("arabic_maths_western_digits = false", preamble(RTL_ARABIC))
        self.assertIn(
            "for c = 0x30, 0x39 do tex.setmathcode(c, {0, 0, c}) end",
            preamble(RTL_ARABIC_WESTERN),
        )
        self.assertNotIn(
            "for c = 0x30, 0x39 do tex.setmathcode(c, {0, 0, c}) end",
            preamble(RTL_ARABIC),
        )

    def test_for_direction_western_arabic_keeps_digits_and_section_number(self) -> None:
        document = {
            "number": "2.4",
            "title": "عنوان",
            "book_id": "mathematics_ar_eg07_t1",
            "exercises": {
                "groups": [
                    {"exercises": [{"id": "1", "text": r"$7س + ص = 15$"}]}
                ]
            },
        }
        result = for_direction(document, RTL_ARABIC_WESTERN)
        text = result["exercises"]["groups"][0]["exercises"][0]["text"]
        self.assertIn("7", text)
        self.assertIn("15", text)
        self.assertNotIn("٧", text)
        self.assertNotIn("١٥", text)
        self.assertEqual(result["number"], "2.4")

    def test_set_member_parbox_stays_raggedright(self) -> None:
        self.assertIn(
            r"\parbox[t]{\dimexpr\setcolumn-\numberroom\relax}{\raggedright\setbody{#3}{#4}\par",
            SET_GRID,
        )

    def test_arabic_over_arrows_follow_begin_document(self) -> None:
        english = Profile("en", "western", "latin")
        arabic = begin_document(RTL_ARABIC)
        self.assertTrue(arabic.startswith("\\begin{document}\n"))
        self.assertIn(ARABIC_OVER_ARROWS, arabic)
        self.assertEqual(arabic.count(ARABIC_OVER_ARROWS), 1)
        self.assertIn(r"\let\overrightarrow\overleftarrow", arabic)
        self.assertIn(r"\let\rightarrow\leftarrow", arabic)
        self.assertIn(r"\let\to\leftarrow", arabic)
        self.assertIn(r"\NagwaOverLeftRight", arabic)
        self.assertIn(r"\NagwaOverLeftHarp", arabic)
        self.assertIn(r"\renewcommand{\vec}{\overleftharpoon}", arabic)
        self.assertNotIn(r"\renewcommand{\vec}{\NagwaOverLeft}", arabic)
        self.assertLess(
            arabic.index(r"\renewcommand{\overleftarrow}{\NagwaOverLeft}"),
            arabic.index(r"\let\overrightarrow\overleftarrow"),
        )
        latin_opening = begin_document(english)
        self.assertNotIn("NagwaOverLeft", latin_opening)
        self.assertNotIn(r"\let\rightarrow\leftarrow", latin_opening)
        self.assertNotIn(r"\let\to\leftarrow", latin_opening)
        self.assertIn(LATIN_VEC_HARPOON, latin_opening)
        self.assertNotIn("NagwaOverLeft", begin_document(RTL_LATIN))
        self.assertIn(LATIN_VEC_HARPOON, begin_document(RTL_LATIN))
        self.assertIn(r"\plannedtrue", begin_document(RTL_ARABIC, planned=True))
        self.assertNotIn(r"\plannedtrue", begin_document(RTL_ARABIC))

        document = {
            "number": "1.4",
            "title": "t",
            "exercises": {"groups": []},
            "book_id": "mathematics_ar_eg10_t1",
        }
        paper = paper_tex(document, [[]], {}, RTL_ARABIC)
        measured = measure_tex([], {}, RTL_ARABIC)
        section = section_tex(document, profile=RTL_ARABIC)
        opening = "\\begin{document}\n" + ARABIC_OVER_ARROWS
        self.assertIn(opening, paper)
        self.assertIn(opening, measured)
        self.assertIn(opening, section)
        self.assertEqual(paper.count(ARABIC_OVER_ARROWS), 1)
        self.assertEqual(measured.count(ARABIC_OVER_ARROWS), 1)
        self.assertEqual(section.count(ARABIC_OVER_ARROWS), 1)
        self.assertNotIn("NagwaOverLeft", section_tex(document, profile=english))
        self.assertNotIn("NagwaOverLeft", section_tex(document, profile=RTL_LATIN))

    def test_arabic_over_arrows_interrupt_an_outer_picture(self) -> None:
        self.assertEqual(ARABIC_OVER_ARROWS.count(r"\pgfinterruptpicture"), 4)
        self.assertEqual(ARABIC_OVER_ARROWS.count(r"\endpgfinterruptpicture"), 4)
        self.assertEqual(ARABIC_OVER_ARROWS.count(r"\hbox{%"), 4)
        self.assertIn(
            r"-{Straight Barb[right, length=2pt, width=2pt]}]",
            ARABIC_OVER_ARROWS,
        )
        self.assertIn(r"([yshift=0.1em]n.north east)", ARABIC_OVER_ARROWS)
        self.assertIn(r"-- ([yshift=0.1em]n.north west);", ARABIC_OVER_ARROWS)
        figure = drawing_tex(
            r"\begin{tikzpicture}\node {$\vec{ق}$};\end{tikzpicture}",
            profile=RTL_ARABIC,
        )
        self.assertIn(ARABIC_OVER_ARROWS, figure)
        self.assertIn(r"\pgfinterruptpicture", figure)

    def test_open_range_hyphen_is_normalized(self) -> None:
        self.assertIn("{20" + HYPHEN + "}", standard_lines(r"\node at (0,0) {20-};"))

    def test_nested_inline_math_delimiters_are_removed(self) -> None:
        self.assertEqual(
            maths_for_face(r"$\(2\)^2 - 4$"),
            r"$2^2 - 4$",
        )
        self.assertEqual(
            to_latex(r"$\(2\)^2 - 4$"),
            r"$2^2 - 4$",
        )

    def test_latin_diacritic_word_in_maths_uses_text_face(self) -> None:
        self.assertEqual(
            to_latex(r"($archéologie$)"),
            r"($\text{archéologie}$)",
        )
        self.assertEqual(
            arabic_maths(r"$archéologie$"),
            r"$\text{archéologie}$",
        )
        self.assertEqual(
            maths_for_face(r"$archéologie$"),
            r"$\text{archéologie}$",
        )
        self.assertEqual(
            maths_for_face(r"$\text{archéologie}$"),
            r"$\text{archéologie}$",
        )
        self.assertEqual(to_latex(r"$logos$"), r"$logos$")

    def test_maths_slash_and_prime_stay_maths(self) -> None:
        self.assertEqual(maths_for_face(r"$f'(0)$"), r"$f'(0)$")
        self.assertEqual(
            maths_for_face(r"$f^{-1}(y) = (y - 5)^{1/3}$"),
            r"$f^{-1}(y) = (y - 5)^{1/3}$",
        )
        self.assertEqual(maths_for_face(r"$e^{x/2}$"), r"$e^{x/2}$")
        self.assertEqual(
            maths_for_face(r"$(2 - x)^{-1/2}$"),
            r"$(2 - x)^{-1/2}$",
        )
        self.assertEqual(wrap_ipa_in_maths("f'(0)"), "f'(0)")
        self.assertEqual(wrap_ipa_in_maths("e^{x/2}"), "e^{x/2}")
        self.assertEqual(
            maths_for_face("$५$"), r"$\text{\devanagarisnippet{५}}$"
        )
        self.assertIn(r"\CounterTallyMarks{5}", tally_maths(r"\cancel{||||}"))
        self.assertEqual(wrap_ipa_in_maths("/eə/"), r"\text{/eə/}")

    def test_grid_number_steps_mirror_for_rtl(self) -> None:
        ltr = grid_lines(Profile("en", "western", "latin"))
        rtl = grid_lines(RTL_LATIN)
        pattern = re.compile(r"\\draw\[red,[^\]]+\] \(([0-9.]+)pt,")
        ltr_steps = [float(value) for value in pattern.findall(ltr)]
        rtl_steps = [float(value) for value in pattern.findall(rtl)]
        self.assertEqual(len(ltr_steps), 4)
        self.assertEqual(len(rtl_steps), 4)
        self.assertNotEqual(ltr_steps, rtl_steps)
        for left, right in zip(ltr_steps, rtl_steps):
            self.assertGreater(right, left)

    def test_shared_figures_with_member_markers_move_to_each_member(self) -> None:
        group = {
            "figures": [
                {"description": "first"},
                {"description": "second"},
            ],
            "exercises": [
                {"text": "Use [figure 1]."},
                {"text": "Use [figure 2]."},
            ],
        }
        distribute_marked_group_figures(group)
        self.assertEqual(group["figures"], [])
        self.assertEqual(group["exercises"][0]["text"], "Use [figure 1].")
        self.assertEqual(group["exercises"][1]["text"], "Use [figure 1].")
        self.assertEqual(
            group["exercises"][1]["figures"][0]["description"], "second"
        )

    @unittest.skipUnless(
        shutil.which("lualatex") and shutil.which("pdftotext"),
        "PDF bidi golden needs lualatex and pdftotext",
    )
    def test_arabic_unit_examples_compile_in_rtl_latin_profile(self) -> None:
        first = rtl_latin_unit_layout(r"$32\pi\text{ م}^3$")
        second = rtl_latin_unit_layout(
            r"$V = \frac{4}{3}\pi r^3\text{ م}^3$"
        )
        with tempfile.TemporaryDirectory() as folder:
            tex = Path(folder) / "mapped-units.tex"
            tex.write_text(
                preamble(RTL_LATIN)
                + "\n\\begin{document}\n"
                + "الحجم "
                + first
                + ".\\par\nالعلاقة "
                + second
                + ".\n\\end{document}\n",
                encoding="utf-8",
            )
            compile_pdf(tex)
            self.assertTrue(tex.with_suffix(".pdf").is_file())

    @unittest.skipUnless(
        shutil.which("lualatex"),
        "ray/vector glyph compile needs lualatex",
    )
    def test_ray_and_vector_marks_compile_in_all_profiles(self) -> None:
        body = r"$\overrightarrow{AB}$ and $\vec{a}$ and $\vec{AB}$."
        english = Profile("en", "western", "latin")
        for profile, name in (
            (english, "latin-en"),
            (RTL_LATIN, "latin-ar"),
            (RTL_ARABIC, "arabic-ar"),
            (RTL_ARABIC_WESTERN, "arabic-ar-western"),
        ):
            with tempfile.TemporaryDirectory() as folder:
                tex = Path(folder) / f"{name}.tex"
                tex.write_text(
                    preamble(profile) + begin_document(profile) + body + "\n\\end{document}\n",
                    encoding="utf-8",
                )
                compile_pdf(tex)
                self.assertTrue(tex.with_suffix(".pdf").is_file())

    @unittest.skipUnless(
        shutil.which("lualatex"),
        "western arabic-notation compile needs lualatex",
    )
    def test_western_arabic_notation_digits_compile(self) -> None:
        body = arabic_maths(
            r"$7س + ص = 15$ and $3.14$ and $50\%$ and $1، 2، 3$.",
            convert_digits=False,
        )
        with tempfile.TemporaryDirectory() as folder:
            tex = Path(folder) / "western-arabic.tex"
            tex.write_text(
                preamble(RTL_ARABIC_WESTERN)
                + begin_document(RTL_ARABIC_WESTERN)
                + body
                + "\n\\end{document}\n",
                encoding="utf-8",
            )
            compile_pdf(tex)
            self.assertTrue(tex.with_suffix(".pdf").is_file())

    @unittest.skipUnless(
        shutil.which("lualatex") and shutil.which("pdftotext"),
        "PDF bidi golden needs lualatex and pdftotext",
    )
    def test_pdf_golden_places_arabic_right_of_number_and_keeps_range_ltr(self) -> None:
        table = directional_tables(
            r"\begin{array}{c|c}\text{الحدود}&\text{التكرار}\\"
            r"\text{أقل من } 40&28\end{array}",
            RTL_LATIN,
        )
        tikz = standard_lines(
            profile_tikz(
                r"\begin{tikzpicture}\node at (0,0) {20-};\end{tikzpicture}",
                RTL_LATIN,
            )
        )
        with tempfile.TemporaryDirectory() as folder:
            tex = Path(folder) / "bidi.tex"
            tex.write_text(
                preamble(RTL_LATIN)
                + "\n\\begin{document}\n\\["
                + table
                + "\\]\n"
                + tikz
                + "\n\\end{document}\n",
                encoding="utf-8",
            )
            compile_pdf(tex)
            xml = subprocess.run(
                ["pdftotext", "-bbox", str(tex.with_suffix(".pdf")), "-"],
                capture_output=True,
                check=True,
                text=True,
            ).stdout
        root = ET.fromstring(xml)
        words = [
            (
                node.text or "",
                float(node.attrib["xMin"]),
                float(node.attrib["xMax"]),
                float(node.attrib["yMin"]),
            )
            for node in root.iter()
            if node.tag.endswith("word")
        ]
        number = next(row for row in words if row[0] == "40")
        arabic_same_line = [
            row
            for row in words
            if abs(row[3] - number[3]) < 4
            and any("\u0600" <= char <= "\uFEFF" for char in row[0])
        ]
        self.assertTrue(arabic_same_line)
        self.assertGreater(max(row[2] for row in arabic_same_line), number[2])
        self.assertTrue(any(row[0] == "20" + HYPHEN for row in words))

    def test_math_slashes_are_parallel_not_two_tally_strokes(self) -> None:
        parallel = to_latex(r"$\overline{AB} // \overline{CD}$")
        self.assertIn(r"\parallel", parallel)
        self.assertNotIn("CounterTallyMarks", parallel)
        self.assertNotIn("//", parallel)
        lines = to_latex(
            r"$\overleftrightarrow{د هـ} // \overleftrightarrow{أ ج}$"
        )
        self.assertIn(r"\parallel", lines)
        self.assertNotIn("CounterTallyMarks", lines)
        already = to_latex(r"$\overline{AB} \parallel \overline{CD}$")
        self.assertIn(r"\parallel", already)
        self.assertNotIn("CounterTallyMarks", already)
        half = to_latex(r"$1/2$")
        self.assertIn("1/2", half)
        self.assertNotIn(r"\parallel", half)
        tally = to_latex(r"$////\backslash\ ///$")
        self.assertIn(r"\scalebox{0.8}{\CounterTallyMarks{8}}", tally)
        self.assertNotIn(r"\parallel", tally)

    def test_inline_tally_is_scaled_table_tally_is_not(self) -> None:
        inline = tally_maths(r"\cancel{||||}")
        self.assertIn(r"\scalebox{0.8}{\CounterTallyMarks{5}}", inline)
        table = tally_maths(r"\begin{array}{c} \cancel{||||} \end{array}")
        self.assertIn(r"\CounterTallyMarks{5}", table)
        self.assertNotIn(r"\scalebox", table)
        paper = to_latex(r"bundles of five (${\cancel{||||}}\;$)")
        self.assertIn(r"\scalebox{0.8}{\CounterTallyMarks{5}}", paper)

    def test_doubled_command_is_one_backslash(self) -> None:
        result = to_latex("$\\\\frac{1 + \\\\sqrt{10}}{2}$")
        self.assertIn(r"\frac{1 + \sqrt{10}}{2}", result)
        self.assertNotIn("\\\\", result)

    def test_array_row_break_stays_a_line_break(self) -> None:
        result = to_latex("$$\\begin{array}{c}\na \\\\\nb\n\\end{array}$$")
        self.assertIn("\\\\", result)
        self.assertNotIn("\\\\b", result)
        self.assertIn(r"\begin{array}", result)

    def test_two_column_text_table_wraps_instead_of_scaling(self) -> None:
        proof = (
            r"$$\begin{array}{l|l} \text{Statements} & \text{Reasons} \\ "
            r"\hline AM = MB & \text{Corresponding parts of congruent triangles (CPCT)}"
            r" \end{array}$$"
        )
        rendered = to_latex(proof)
        self.assertNotIn(r"\tablefit", rendered)
        self.assertIn(r"\begin{tabularx}{\linewidth}", rendered)
        self.assertIn(r"{l|>{\raggedright\arraybackslash}X}", rendered)
        self.assertIn("Corresponding parts of congruent triangles (CPCT)", rendered)
        self.assertNotIn(r"\text{Corresponding parts", rendered)
        numeric = to_latex(r"$$\begin{array}{c|c} 1 & 2 \\ 3 & 4 \end{array}$$")
        self.assertIn(r"\tablefit", numeric)
        self.assertNotIn(r"\begin{tabularx}", numeric)

    def test_proof_table_keeps_step_narrow_and_splits_long_math(self) -> None:
        proof = (
            r"$$\begin{array}{c|l|l} \text{Step} & \text{Statement} & \text{Reason} \\ "
            r"\hline 1 & a^2=\left(2m+1\right)^2=4m^2+4m+1 & \text{Algebraic expansion}"
            r" \end{array}$$"
        )
        rendered = to_latex(proof)
        self.assertIn(r"\begin{tabularx}{\linewidth}", rendered)
        self.assertIn("{c|", rendered)
        self.assertEqual(rendered.count(r"arraybackslash}X"), 1)
        self.assertIn("|l}", rendered)
        self.assertIn("$a^2$ $=$ $\\left(2m+1\\right)^2$ $=$ $4m^2+4m+1$", rendered)
        self.assertIn("Algebraic expansion", rendered)
        self.assertNotIn(r"\tablefit", rendered)

    def test_short_text_table_stays_natural_width(self) -> None:
        table = (
            r"$$\begin{array}{c|l} I & \text{Mean} \\ "
            r"\hline II & \text{Standard deviation} \end{array}$$"
        )
        rendered = to_latex(table)
        self.assertIn(r"\tablefit", rendered)
        self.assertNotIn(r"\begin{tabularx}", rendered)
        self.assertIn(r"\begin{array}{c|l}", rendered)

    def test_step_number_control_space_does_not_eat_math_dollar(self) -> None:
        proof = (
            r"$$\begin{array}{l|l} \text{Statements} & \text{Comment} \\ "
            r"\hline 1.\ \text{Assume the archer scored }10\text{ points} "
            r"& \text{Negation} \end{array}$$"
        )
        rendered = to_latex(proof)
        self.assertIn("$1.$ Assume the archer scored $10$ points", rendered)
        self.assertNotIn(r"$1.\$", rendered)

    def test_table_beside_figure_uses_full_measure(self) -> None:
        drawings = {
            "lines": {
                "width": 80,
                "tikz": r"\begin{tikzpicture}\draw (0,0)--(1,0);\end{tikzpicture}",
            }
        }
        stem = "Complete the table by identifying all the angle pairs:"
        table = r"$$\begin{array}{l|l} A & B \\ C & D \end{array}$$"
        rendered = body_to_latex(f"{stem} {table}", ["lines"], drawings)
        first, _, rest = rendered.split(r"\besidefigure{", 1)[1].partition("}{")
        self.assertEqual(first, stem)
        self.assertNotIn(r"\tablefit", first)
        self.assertIn(r"\tablefit", rest)
        self.assertIn(r"\begin{array}", rest)
        plain = body_to_latex("Which angle is opposite?", ["lines"], drawings)
        self.assertIn(r"\besidefigure{Which angle is opposite?}", plain)
        self.assertNotIn(r"\tablefit", plain)

    def test_punctuation_after_figure_marker_is_dropped(self) -> None:
        text = "As shown in [figure 1], an L-shaped piece"
        self.assertEqual(
            replace_marker(text, 1, r"\widefigure{X}"),
            r"As shown in \widefigure{X} an L-shaped piece",
        )
        rendered = body_to_latex(text, ["L shape"], {})
        self.assertNotIn(", an L-shaped", rendered)
        self.assertIn("an L-shaped", rendered)

    def test_punctuation_after_display_math_is_dropped(self) -> None:
        table = r"$$\begin{array}{c} a \end{array}$$، حيث $\bar{X} = 2.6$"
        rendered = to_latex(table)
        self.assertRegex(rendered, r"\\]\s*حيث")
        self.assertNotIn(r"\]،", rendered)
        english = to_latex(r"$$\begin{array}{c} a \end{array}$$, where $x=1$")
        self.assertRegex(english, r"\\]\s*where")
        self.assertNotIn(r"\],", english)
        inline = to_latex(r"$x=1$.")
        self.assertTrue(inline.endswith("."))
        self.assertNotIn(r"\[", inline)
        single = to_latex(r"$\begin{array}{c} a \end{array}$، ثم")
        self.assertRegex(single, r"\\]\s*ثم")
        self.assertNotIn(r"\]،", single)
        self.assertNotIn(
            r"\[",
            to_latex(r"$$\begin{array}{c} a \end{array}$$, where", inline=True),
        )

    def test_set_member_is_measured_without_its_figure_marker(self) -> None:
        wording = set_cell(
            {"id": "exercise_83", "text": "قبل [figure 1] بعد", "figures": ["مربع"]},
            83,
            {},
            200,
        )
        alone = set_cell(
            {"id": "exercise_1", "text": "[figure 1]", "figures": ["مثلث"]},
            1,
            {},
            200,
        )
        self.assertIn(r"\setitem{\labelnumber{83}}{قبل بعد}", wording)
        self.assertIn(r"\setitem{\labelnumber{1}}{}", alone)
        self.assertNotRegex(wording + alone, r"\[figure\s+[0-9]")

    def test_rule_after_a_row_break_is_left_alone(self) -> None:
        source = (
            r"\begin{array}{|c|c|}\hline a & b \\ \hline c & d \\ \hline\end{array}"
        )
        self.assertEqual(row_breaks_before_rules(source), source)
        cline = (
            r"\begin{array}{|c|c|c|c|} \hline a & "
            r"\multicolumn{3}{c|}{b} \\ \cline{2-4} & c & d & e \end{array}"
        )
        self.assertEqual(row_breaks_before_rules(cline), cline)


class ProseMarkupTests(unittest.TestCase):
    def test_smart_quotes_stay_typographic(self) -> None:
        self.assertIn("“word”", escape_prose("“word”"))

    def test_guillemets_stay_guillemets_in_arabic_prose(self) -> None:
        self.assertIn("«word»", normalize_quotes("«word»", guillemets=True))

    def test_arabic_quotes_become_guillemets(self) -> None:
        self.assertIn("«hi»", normalize_quotes('say "hi"', guillemets=True))
        self.assertIn("«hi»", normalize_quotes("say “hi”", guillemets=True))
        self.assertIn("«hi»", normalize_quotes("say ‘hi’", guillemets=True))
        self.assertEqual(normalize_quotes("don't", guillemets=True), "don’t")
        self.assertNotIn("«", normalize_quotes("don't", guillemets=True))
        rendered = to_latex(
            "في المسألة الآتية: “قطع فهد مسافة $240$ كم.”",
            guillemets=True,
        )
        self.assertIn("«", rendered)
        self.assertIn("»", rendered)
        self.assertNotIn("“", rendered)
        self.assertNotIn("”", rendered)

    def test_math_primes_are_not_guillemets(self) -> None:
        rendered = to_latex(r"$أ' ب'$", guillemets=True)
        self.assertIn("أ'", rendered)
        self.assertNotIn("«", rendered)
        self.assertIn("«قال»", normalize_quotes('"قال"', guillemets=True))
        node = arabic_tikz_nodes(r"\node [below left] at (A') {$أ'$};")
        self.assertIn(r"{$أ'$}", node)
        self.assertNotIn("«", node)

    def test_ascii_double_quotes_become_typographic(self) -> None:
        self.assertIn("“hi”", escape_prose('say "hi"'))

    def test_wrong_single_quotes_get_a_closing_glyph(self) -> None:
        self.assertIn("‘street wear’", escape_prose("‘street wear‘"))

    def test_guillemets_become_curly_doubles_in_english_prose(self) -> None:
        self.assertIn("“word”", escape_prose("«word»"))

    def test_ascii_arrow_becomes_rightarrow(self) -> None:
        self.assertIn(r"$\rightarrow$", escape_prose("هدى -> هديان"))

    def test_markdown_bold_becomes_textbf(self) -> None:
        self.assertEqual(escape_prose("** Bold **"), r"\textbf{Bold}")

    def test_markdown_italic_becomes_textit(self) -> None:
        self.assertEqual(escape_prose("*Amalasiddhi*"), r"\textit{Amalasiddhi}")
        self.assertEqual(
            escape_prose("**Bold** and *Amalasiddhi*"),
            r"\textbf{Bold} and \textit{Amalasiddhi}",
        )

    def test_blank_control_space_is_not_printed(self) -> None:
        self.assertEqual(
            to_latex(r"زاويتين \blank\."),
            r"زاويتين \blank{}.",
        )
        self.assertEqual(to_latex(r"\blank\."), r"\blank{}.")
        self.assertEqual(
            to_latex(r"always \blank\ angles."),
            r"always \blank{} angles.",
        )
        self.assertEqual(to_latex(r"the same \blank."), r"the same \blank{}.")
        self.assertNotIn(r"\textbackslash", to_latex(r"زاوية الرأس \blank\."))
        self.assertIn(r"\textbackslash{}", to_latex(r"\blank\angle"))

    def test_to_latex_leaves_maths_arrow_alone(self) -> None:
        self.assertIn(r"\rightarrow", to_latex(r"$a \rightarrow b$"))

    def test_wordpin_is_in_the_drawing_preamble(self) -> None:
        self.assertIn(r"\wordpin", WORD_PIN)
        self.assertIn(r"\wordpin", DRAWING_PREAMBLE)
        self.assertIn("(wordpinlab)", WORD_PIN)
        self.assertIn(r"O{2.2cm}", WORD_PIN)
        self.assertNotIn("node [inner sep=1pt, anchor=center] {#5} --", WORD_PIN)

    @unittest.skipUnless(
        shutil.which("lualatex"),
        "wordpin compile needs lualatex",
    )
    def test_wordpin_picture_compiles(self) -> None:
        body = (
            r"\begin{tikzpicture}"
            r"\fill (0,0) circle (2.5pt);"
            r"\wordpin{135}{0}{0}{المدخل الرئيسي}"
            r"\end{tikzpicture}"
        )
        with tempfile.TemporaryDirectory() as folder:
            tex = Path(folder) / "wordpin.tex"
            tex.write_text(
                preamble(RTL_ARABIC)
                + begin_document(RTL_ARABIC)
                + body
                + "\n\\end{document}\n",
                encoding="utf-8",
            )
            compile_pdf(tex)
            self.assertTrue(tex.with_suffix(".pdf").is_file())

    def test_draw_pin_prompt_asks_for_wordpin(self) -> None:
        prompts = Path(__file__).resolve().parents[2] / "prompts" / "draw"
        pin = (prompts / "pin.md").read_text(encoding="utf-8")
        relabel = (prompts / "relabel.md").read_text(encoding="utf-8")
        core = (prompts / "core.md").read_text(encoding="utf-8")
        self.assertIn(r"\wordpin", pin)
        self.assertIn(r"\wordpin{angle}{x}{y}{text}", pin)
        self.assertIn("Change only the colliding word nodes", pin)
        self.assertIn(r"\wordpin", relabel)
        self.assertIn(r"\wordpin", core)

    def test_array_clock_cells_become_ltr_hbox_and_ratios_stay(self) -> None:
        table = (
            r"$$\begin{array}{c|c}"
            r"\text{وقت} & ٨:١٥\text{ صباحًا} \\"
            r"٦ : ١٠ & ١:٣٠\text{ مساءً}"
            r"\end{array}$$"
        )
        cells = clock_array_cells(table)
        # Only H:MM is TLT; period word stays outside so RTL can place it.
        self.assertIn(r"\hbox{\textdir TLT\relax ٨:١٥}\text{ صباحًا}", cells)
        self.assertIn(r"\hbox{\textdir TLT\relax ١:٣٠}\text{ مساءً}", cells)
        self.assertNotIn(r"\hbox{\textdir TLT\relax ٨:١٥\text{ صباحًا}}", cells)
        self.assertIn("٦ : ١٠", cells)
        noted = arabic_notation(table)
        self.assertIn(r"\hbox{\textdir TLT\relax ٨:١٥}\text{ صباحًا}", noted)
        self.assertIn("صباحًا", noted)
        self.assertNotIn(r"\sad \beh \alef \hah", noted)
        self.assertIn("٦ : ١٠", noted)

    def test_nested_text_header_stays_joining_arabic(self) -> None:
        header = r"\text{درجة الحرارة العظمى ($^\circ\text{س}$)}"
        result = arabic_maths(f"$${header}$$")
        self.assertIn(r"\text{درجة الحرارة العظمى ($^\circ\text{س}$)}", result)
        self.assertNotIn(r"\dal ", result)
        self.assertNotIn(r"\reh ", result)
        self.assertNotIn(r"\jeemi ", result)
        self.assertEqual(result.count(r"\text{س}"), 1)
        self.assertNotIn(r"\text{س}$)}", result[result.find(header) + len(header) :])
        from src.latex import maths_for_face

        faced = maths_for_face(f"$${header}$$")
        self.assertEqual(faced.count(r"\text{س}"), 1)
        self.assertIn(header, faced)

    def test_bare_arabic_decimal_is_wrapped_for_math_face(self) -> None:
        self.assertEqual(wrap_arabic_decimal_runs("١٢٫٥٢ ريالًا"), "$١٢٫٥٢$ ريالًا")
        self.assertEqual(arabic_prose("12.52 ريالًا"), "$١٢٫٥٢$ ريالًا")
        self.assertEqual(arabic_prose("٣٥ فقط"), "٣٥ فقط")

    def test_arabic_tikz_maps_axis_x_y_to_seen_sad(self) -> None:
        source = (
            r"\node [right] at (5.9,0) {$x$};"
            r"\node [above] at (0,23.6) {$y$};"
            r"\node at (1,1) {$2x+y$};"
        )
        mapped = arabic_axis_letters(source)
        self.assertIn(r"{$\seen $}", mapped)
        self.assertIn(r"{$\sad $}", mapped)
        self.assertIn(r"{$2x+y$}", mapped)
        result = arabic_tikz(source)
        self.assertIn(r"{$\seen $}", result)
        self.assertIn(r"{$\sad $}", result)
        again = arabic_tikz(result)
        self.assertEqual(again, result)


class RepairTests(unittest.TestCase):
    def test_doubled_command_is_stored_with_one_backslash(self) -> None:
        group = ExerciseGroup.model_validate({
            "exercises": [{
                "text": "$\\\\frac{1 + \\\\sqrt{10}}{2}$",
                "objectives": ["objective_1"],
                "answer": "$$\\begin{array}{c}\na \\\\\nb\n\\end{array}$$",
            }]
        })
        groups, info = repaired([group], 4)
        exercise = groups[0].exercises[0]
        self.assertEqual(exercise.text, r"$\frac{1 + \sqrt{10}}{2}$")
        self.assertIn("\\\\\nb", exercise.answer)
        self.assertEqual(info["doubled_commands"], 2)


if __name__ == "__main__":
    unittest.main()
